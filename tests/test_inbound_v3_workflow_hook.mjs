import assert from "node:assert/strict";
import { mkdir, mkdtemp, rm, symlink, writeFile } from "node:fs/promises";
import { join } from "node:path";
import test from "node:test";
import {
  createV3WorkflowArchiveTool,
  deliverReviewMedia,
  ensureStaticInboundBinding,
  handleV3WorkflowInbound,
  isStaticDadaChildSession,
  isStaticDadaInboundConversation,
  idleDueReminderPrompt,
  normalizeReviewTts,
  readV3IdleDueCount,
  readV3WorkflowActivity,
  registerV3WorkflowInbound,
  requestReviewStart,
  requestDialogueStart,
  reviewMediaDeliveryArgs,
  runV3Workflow,
  safeTtsMediaPath,
  safeReviewMediaPath,
} from "../runtime/inbound_v3_workflow_hook/index.mjs";

const config = {
  agentId: "dada", childScopeToken: "child_0123456789abcdef0123456789abcdef",
  inboundBinding: { sessionKey: "agent:dada:openclaw-weixin:account:direct:child", channelId: "openclaw-weixin", accountId: "account", conversationId: "child" },
  pythonBin: "/project/.venv/bin/python", runtimeRoot: "/project/runtime", archiveRoot: "/project/data/learning-archives",
  entry: { definitionDirectory: "/definitions/entry", definitionDigest: "a".repeat(64), provider: "entry", model: "entry-model", endpoint: "https://provider.invalid/entry", apiStyle: "responses", userAgent: "Mozilla/5.0" },
  review: { definitionDirectory: "/definitions/review", definitionDigest: "b".repeat(64), provider: "review", model: "review-model", endpoint: "https://provider.invalid/review", apiStyle: "chat-completions", schedulePath: "/project/config/review-schedule.json", userAgent: "Mozilla/5.0" },
  dialogue: { definitionDirectory: "/definitions/dialogue", definitionDigest: "c".repeat(64), provider: "review", model: "review-model", endpoint: "https://provider.invalid/review", apiStyle: "chat-completions", userAgent: "Mozilla/5.0", dialogueUnitId: "grade6-english-unit-1-school-life", unitPath: "/project/config/dialogue-units/grade6-english-unit-1-school-life/unit.v1.json", policyPath: "/project/config/dialogue-policy.json", reviewSchedulePath: "/project/config/review-schedule.json" },
};
const toolContext = { agentId: "dada", sessionKey: config.inboundBinding.sessionKey };
const inboundContext = { channelId: "openclaw-weixin", accountId: "account", conversationId: "child" };

test("tool and inbound authorization use separate exact static identities", () => {
  assert.equal(isStaticDadaChildSession(config, toolContext), true);
  assert.equal(isStaticDadaChildSession(config, { agentId: "dada", sessionKey: "agent:dada:other" }), false);
  assert.equal(isStaticDadaInboundConversation(config, inboundContext), true);
  assert.equal(isStaticDadaInboundConversation(config, { ...inboundContext, conversationId: "other" }), false);
});
test("unified activity query returns exact entry/review type", () => {
  let sql; let scope; const activity = readV3WorkflowActivity(config, config.childScopeToken, () => ({ prepare(value) { sql = value; return { get(value) { scope = value; return { workflow_type: "review" }; } }; }, close() {} }), () => true);
  assert.deepEqual(activity, { ok: true, mode: "review" }); assert.match(sql, /external_session_id = \?/); assert.match(sql, /phase = 'active'/); assert.equal(scope, config.childScopeToken);
});
test("idle due count uses only fixed queue facts", () => {
  const seen = [];
  const result = readV3IdleDueCount(config, config.childScopeToken, "2026-08-24T10:00:00Z", () => ({
    prepare(sql) { seen.push(sql); return { get(...args) { return seen.length === 1 ? undefined : (args.length === 2 ? { count: 3 } : { count: 0 }); } }; }, close() {},
  }), () => true);
  assert.deepEqual(result, { ok: true, due_count: 3 });
  assert.equal(seen.length, 2); assert.match(seen[0], /paused_for_entry/); assert.match(seen[1], /next_review_at <= \?/); assert.match(seen[1], /external_session_id = \?/);
});
test("idle reminder is exact-session and no-active-workflow only", () => {
  const due = () => ({ ok: true, due_count: 4 });
  const idle = idleDueReminderPrompt(config, toolContext, () => ({ ok: true, mode: null }), due, "2026-08-24T10:00:00Z");
  assert.match(idle, /4 道到期复习内容/); assert.match(idle, /4 题可以复习/);
  assert.equal(idleDueReminderPrompt(config, toolContext, () => ({ ok: true, mode: "entry" }), due), null);
  assert.equal(idleDueReminderPrompt(config, toolContext, () => ({ ok: true, mode: "review" }), due), null);
  assert.equal(idleDueReminderPrompt(config, { agentId: "dada", sessionKey: "other" }, () => ({ ok: true, mode: null }), due), null);
  assert.equal(idleDueReminderPrompt(config, toolContext, () => ({ ok: true, mode: null }), () => ({ ok: true, due_count: 0 })), null);
});
test("workflow CLIs have only fixed module and deployment values", async () => {
  let seen; const result = await runV3Workflow(config, "review", { content: "回答", timestamp: Date.UTC(2026, 7, 23) }, async (command, args, input, env) => { seen = { command, args, input, env }; return { ok: true, handled: true, reply_text: "继续。", no_due_item: false }; });
  assert.deepEqual(result, { ok: true, handled: true, reply_text: "继续。", state_text: null, progress_text: null, no_due_item: false, media_path: null }); assert.deepEqual(seen.args, ["-m", "v3_review_production.cli"]); assert.equal(seen.input.external_session_ref, config.childScopeToken); assert.equal(seen.env.DADA_REVIEW_SCHEDULE_PATH, "/project/config/review-schedule.json"); assert.equal(seen.env.DADA_REVIEW_MODEL_USER_AGENT, "Mozilla/5.0"); assert.equal(seen.env.DADA_REVIEW_ENTRY_DEFINITION_DIR, "/definitions/entry"); assert.equal("DADA_REVIEW_MODEL_API_KEY" in seen.env, false);
  const entry = await runV3Workflow(config, "entry", { content: "开始复习", timestamp: Date.UTC(2026, 7, 23) }, async (command, args, input, env) => { seen = { command, args, input, env }; return { ok: true, handled: true, reply_text: "继续。" }; });
  assert.equal(entry.ok, true); assert.equal(seen.env.DADA_ENTRY_MODEL_USER_AGENT, "Mozilla/5.0"); assert.equal(seen.env.DADA_ENTRY_REVIEW_DEFINITION_DIR, "/definitions/review"); assert.equal(seen.env.DADA_ENTRY_REVIEW_MODEL_USER_AGENT, "Mozilla/5.0");
});
test("review TTS normalizes only the legacy Seed shape into its explicit provider", () => {
  const legacy = { enabled: true, endpoint: "https://tts.invalid/unidirectional", resourceId: "seed-tts-2.0", speaker: "speaker", timeoutSeconds: 5, maxTextChars: 100, maxAudioBytes: 1024, retentionSeconds: 60 };
  const canonical = { ...legacy, provider: "volcengine_seed" };
  assert.deepEqual(normalizeReviewTts(legacy), canonical);
  assert.deepEqual(normalizeReviewTts(canonical), canonical);
  assert.equal(normalizeReviewTts({ ...legacy, provider: "qwen_bailian" }), null);
  assert.equal(isStaticDadaChildSession({ ...config, reviewTts: { ...legacy, provider: "qwen_bailian" } }, toolContext), false);
});
test("review TTS accepts only the fixed MiniMax provider shape and injects no fallback model key", async () => {
  const settings = {
    ...config,
    reviewTts: {
      provider: "minimax", enabled: true, endpoint: "https://api.minimaxi.com/v1/t2a_v2",
      model: "speech-2.8-turbo", voiceId: "male-qn-qingse",
      timeoutSeconds: 5, maxTextChars: 100, maxAudioBytes: 1024, retentionSeconds: 60,
    },
  };
  assert.deepEqual(normalizeReviewTts(settings.reviewTts), settings.reviewTts);
  assert.equal(normalizeReviewTts({ ...settings.reviewTts, model: "speech-2.8-hd" }), null);
  const previousReviewTtsKey = process.env.DADA_REVIEW_TTS_API_KEY;
  const previousEntryKey = process.env.DADA_ENTRY_MODEL_API_KEY;
  delete process.env.DADA_REVIEW_TTS_API_KEY;
  process.env.DADA_ENTRY_MODEL_API_KEY = "entry-only-key";
  try {
    let seen;
    await runV3Workflow(settings, "review", { content: "答案" }, async (_command, _args, _input, env) => {
      seen = env; return { ok: true, handled: true, reply_text: "继续。", no_due_item: false };
    });
    assert.equal(seen.DADA_REVIEW_TTS_PROVIDER, "minimax");
    assert.equal(seen.DADA_REVIEW_TTS_MODEL, "speech-2.8-turbo");
    assert.equal(seen.DADA_REVIEW_TTS_VOICE_ID, "male-qn-qingse");
    assert.equal("DADA_REVIEW_TTS_API_KEY" in seen, false);
    assert.equal("DADA_REVIEW_TTS_RESOURCE_ID" in seen, false);
  } finally {
    if (previousReviewTtsKey === undefined) delete process.env.DADA_REVIEW_TTS_API_KEY; else process.env.DADA_REVIEW_TTS_API_KEY = previousReviewTtsKey;
    if (previousEntryKey === undefined) delete process.env.DADA_ENTRY_MODEL_API_KEY; else process.env.DADA_ENTRY_MODEL_API_KEY = previousEntryKey;
  }
});
test("review TTS maps the protected Agent Plan key only into a TTS-enabled child CLI", async () => {
  const prior = process.env.DADA_ENTRY_MODEL_API_KEY; process.env.DADA_ENTRY_MODEL_API_KEY = "protected-agent-plan-key";
  try {
    const settings = { ...config, reviewTts: { enabled: true, endpoint: "https://openspeech.bytedance.com/api/v3/plan/tts/unidirectional", resourceId: "seed-tts-2.0", speaker: "speaker", timeoutSeconds: 5, maxTextChars: 100, maxAudioBytes: 1024, retentionSeconds: 60 } };
    let reviewEnv; await runV3Workflow(settings, "review", { content: "答案" }, async (_command, _args, _input, env) => { reviewEnv = env; return { ok: true, handled: true, reply_text: "继续。", no_due_item: false }; });
    let entryEnv; await runV3Workflow(settings, "entry", { content: "录入" }, async (_command, _args, _input, env) => { entryEnv = env; return { ok: true, handled: true, reply_text: "继续。" }; });
    assert.equal(reviewEnv.DADA_REVIEW_TTS_API_KEY, "protected-agent-plan-key"); assert.equal(entryEnv.DADA_REVIEW_TTS_API_KEY, "protected-agent-plan-key");
    assert.equal(reviewEnv.DADA_REVIEW_TTS_PROVIDER, "volcengine_seed"); assert.equal(entryEnv.DADA_REVIEW_TTS_PROVIDER, "volcengine_seed");
  } finally { if (prior === undefined) delete process.env.DADA_ENTRY_MODEL_API_KEY; else process.env.DADA_ENTRY_MODEL_API_KEY = prior; }
});
test("Dialogue TTS reuses the shared Review TTS configuration and key", async () => {
  const settings = {
    ...config,
    reviewTts: { enabled: true, endpoint: "https://tts.invalid/unidirectional", resourceId: "seed-tts-2.0", speaker: "dialogue-speaker", timeoutSeconds: 5, maxTextChars: 100, maxAudioBytes: 1024, retentionSeconds: 60 },
  };
  const previous = process.env.DADA_REVIEW_TTS_API_KEY;
  process.env.DADA_REVIEW_TTS_API_KEY = "shared-tts-key";
  try {
    let seen;
    const result = await runV3Workflow(settings, "dialogue", { content: "继续", timestamp: Date.UTC(2026, 7, 23) }, async (_command, _args, _input, env) => {
      seen = env; return { ok: true, handled: true, reply_text: "Let's continue.", state_text: "【英语对话中】", progress_text: null, media_path: null };
    });
    assert.equal(result.ok, true);
    assert.equal(seen.DADA_REVIEW_TTS_ENABLED, "1");
    assert.equal(seen.DADA_REVIEW_TTS_API_KEY, "shared-tts-key");
  } finally { if (previous === undefined) delete process.env.DADA_REVIEW_TTS_API_KEY; else process.env.DADA_REVIEW_TTS_API_KEY = previous; }
});
test("fixed review start returns no_due without model-controlled input", async () => {
  let observed; const result = await requestReviewStart(config, toolContext, async (_config, mode, event, _process, start) => { observed = { mode, event, start }; return { ok: true, handled: true, reply_text: null, no_due_item: true }; }, () => ({ ok: true, mode: null }));
  assert.deepEqual(result, { started: false, reason: "no_due_item" }); assert.equal(observed.mode, "review"); assert.equal(observed.event.content, "开始复习"); assert.equal(observed.start, true);
});
test("fixed start accepts an injected clock without changing the tool contract", async () => {
  let entryTimestamp; let reviewTimestamp;
  const entry = await createV3WorkflowArchiveTool({ config, ...toolContext, now: () => 1234, runEntry: async (_config, _ctx, _run, _activity, now) => { entryTimestamp = now(); return { started: true, mode: "entry_collecting", reply_text: "开始。" }; }, runReview: async (_config, _ctx, _run, _activity, now) => { reviewTimestamp = now(); return { started: true, mode: "review_active", reply_text: "开始复习。" }; } });
  await entry.execute("clock-entry", { action: "request_entry_start", payload: {} });
  await entry.execute("clock-review", { action: "request_review_start", payload: {} });
  assert.equal(entryTimestamp, 1234); assert.equal(reviewTimestamp, 1234);
});
test("active or failed reads cannot start review", async () => {
  const active = await requestReviewStart(config, toolContext, undefined, () => ({ ok: true, mode: "entry" })); assert.equal(active.error.code, "already_active");
  const failed = await requestReviewStart(config, toolContext, undefined, () => ({ ok: false, mode: null })); assert.equal(failed.error.code, "unavailable");
});
test("binding is exact, plugin-owned, and contains no child content", async () => {
  let input; const ok = await ensureStaticInboundBinding(config, { id: "dada-v3-workflow-inbound", name: "Dada v3", rootDir: "/plugin" }, { async createConversationBindingRecord(value) { input = value; } });
  assert.equal(ok, true); assert.equal(input.conversation.conversationId, "child"); assert.equal(input.metadata.pluginBindingOwner, "plugin"); assert.match(input.targetSessionKey, /^plugin-binding:dada-v3-workflow-inbound:[a-f0-9]{24}$/); assert.equal(JSON.stringify(input).includes("回答"), false);
});
test("bound active inbound claims exact review and releases only after close", async () => {
  let released = 0; let calls = 0; const modes = ["review", null];
  const result = await handleV3WorkflowInbound(config, { content: "答案", timestamp: Date.UTC(2026, 7, 23) }, inboundContext, {
    readActivity: () => ({ ok: true, mode: modes.shift() ?? null }),
    runWorkflow: async (_config, mode, event) => { calls += 1; assert.equal(mode, "review"); assert.equal(event.content, "答案"); return { ok: true, handled: true, reply_text: "很好。" }; },
    releaseBinding: async () => { released += 1; },
  });
  assert.deepEqual(result, { handled: true, reply: { text: "很好。" } }); assert.equal(calls, 1); assert.equal(released, 1);
});
test("review media is accepted only from the fixed bounded outbox", async () => {
  const root = await mkdtemp("/tmp/dada-review-media-");
  try {
    const outbox = join(root, "outbound-media"); await mkdir(outbox);
    const media = join(outbox, "question.mp3"); await writeFile(media, "audio");
    const settings = { ...config, archiveRoot: root, reviewTts: { enabled: true, endpoint: "https://tts.invalid/unidirectional", resourceId: "seed-tts-2.0", speaker: "speaker", timeoutSeconds: 5, maxTextChars: 100, maxAudioBytes: 1024, retentionSeconds: 60 } };
    assert.equal(safeReviewMediaPath(settings, media), media);
    assert.equal(safeReviewMediaPath(settings, join(root, "outside.mp3")), null);
    const linked = join(outbox, "linked.mp3"); await symlink(join(root, "outside.mp3"), linked);
    assert.equal(safeReviewMediaPath(settings, linked), null);
    assert.deepEqual(reviewMediaDeliveryArgs(settings, media, "/gateway/index.js"), ["/gateway/index.js", "message", "send", "--channel", "openclaw-weixin", "--account", "account", "--target", "child", "--media", media, "--json"]);
    const sent = []; assert.equal(await deliverReviewMedia(settings, media, async (input, path) => { sent.push([input, path]); return true; }), true);
    assert.equal(await deliverReviewMedia(settings, join(root, "outside.mp3"), async () => { throw new Error("must not send"); }), false);
    const result = await handleV3WorkflowInbound(settings, { content: "答案" }, inboundContext, { readActivity: () => ({ ok: true, mode: "review" }), runWorkflow: async () => ({ ok: true, handled: true, reply_text: "题目", progress_text: "第 2 / 3 题，还剩 2 题。", media_path: media }), sendReviewMedia: async (input, path) => { sent.push([input, path]); return true; } });
    assert.deepEqual(result, { handled: true, reply: { text: "第 2 / 3 题，还剩 2 题。" } }); assert.equal(sent.length, 2); assert.equal(sent[1][1], media);
    const fallback = await handleV3WorkflowInbound(settings, { content: "答案" }, inboundContext, { readActivity: () => ({ ok: true, mode: "review" }), runWorkflow: async () => ({ ok: true, handled: true, reply_text: "题目", media_path: media }), sendReviewMedia: async () => false });
    assert.deepEqual(fallback, { handled: true, reply: { text: "题目" } });
    const modes = ["review", "entry"];
    const handoff = await handleV3WorkflowInbound(settings, { content: "开始录入" }, inboundContext, { readActivity: () => ({ ok: true, mode: modes.shift() ?? null }), runWorkflow: async () => ({ ok: true, handled: true, reply_text: "已经切换到录入状态。", media_path: media }), sendReviewMedia: async () => { throw new Error("entry handoff must not send review audio"); } });
    assert.deepEqual(handoff, { handled: true, reply: { text: "已经切换到录入状态。" } });
  } finally { await rm(root, { recursive: true, force: true }); }
});
test("active Dialogue sends state/progress text and media through the shared TTS path", async () => {
  const root = await mkdtemp("/tmp/dada-dialogue-media-");
  try {
    const outbox = join(root, "outbound-media"); await mkdir(outbox);
    const media = join(outbox, "dialogue.mp3"); await writeFile(media, "audio");
    const settings = { ...config, archiveRoot: root, reviewTts: { enabled: true, endpoint: "https://tts.invalid/unidirectional", resourceId: "seed-tts-2.0", speaker: "speaker", timeoutSeconds: 5, maxTextChars: 100, maxAudioBytes: 1024, retentionSeconds: 60 } };
    const sent = [];
    const result = await handleV3WorkflowInbound(settings, { content: "回答", timestamp: Date.UTC(2026, 7, 23) }, inboundContext, {
      readActivity: () => ({ ok: true, mode: "dialogue" }),
      runWorkflow: async () => ({ ok: true, handled: true, reply_text: "Great!", state_text: "【英语对话中】", progress_text: "下一步练习：句子支架。", media_path: media }),
      sendReviewMedia: async (input, path) => { sent.push([input, path]); return true; },
    });
    assert.deepEqual(result, { handled: true, reply: { text: "【英语对话中】\n\n下一步练习：句子支架。" } });
    assert.equal(sent.length, 1); assert.equal(sent[0][1], media); assert.equal(safeTtsMediaPath(settings, media, "dialogue"), media);
  } finally { await rm(root, { recursive: true, force: true }); }
});
test("idle review start attaches only a validated first-question MP3", async () => {
  const root = await mkdtemp("/tmp/dada-review-start-media-");
  try {
    const outbox = join(root, "outbound-media"); await mkdir(outbox);
    const media = join(outbox, "first.mp3"); await writeFile(media, "audio");
    const settings = { ...config, archiveRoot: root, reviewTts: { enabled: true, endpoint: "https://tts.invalid/unidirectional", resourceId: "seed-tts-2.0", speaker: "speaker", timeoutSeconds: 5, maxTextChars: 100, maxAudioBytes: 1024, retentionSeconds: 60 } };
    const sent = []; const tool = createV3WorkflowArchiveTool({ config: settings, ...toolContext, runReview: async () => ({ started: true, mode: "review_active", reply_text: "首题", progress_text: "这轮共 3 题，现在从第 1 题开始。", media_path: media }), sendReviewMedia: async (input, path) => { sent.push([input, path]); return true; }, ensureBinding: async () => true });
    const result = await tool.execute("start", { action: "request_review_start", payload: {} });
    assert.deepEqual(JSON.parse(result.content[0].text), { started: true, mode: "review_active", reply_text: "这轮共 3 题，现在从第 1 题开始。", audio_delivered: true });
    assert.equal("details" in result, false); assert.equal(sent.length, 1); assert.equal(sent[0][1], media);
  } finally { await rm(root, { recursive: true, force: true }); }
});
test("Dialogue audio start retains state and progress text for the child", async () => {
  const root = await mkdtemp("/tmp/dada-dialogue-start-media-");
  try {
    const outbox = join(root, "outbound-media"); await mkdir(outbox);
    const media = join(outbox, "first.mp3"); await writeFile(media, "audio");
    const settings = { ...config, archiveRoot: root, reviewTts: { enabled: true, endpoint: "https://tts.invalid/unidirectional", resourceId: "seed-tts-2.0", speaker: "speaker", timeoutSeconds: 5, maxTextChars: 100, maxAudioBytes: 1024, retentionSeconds: 60 } };
    const sent = []; const sentText = [];
    const tool = createV3WorkflowArchiveTool({
      config: settings, ...toolContext,
      runDialogue: async () => ({ started: true, mode: "dialogue_active", reply_text: "【英语对话中】\n\n本单元进度：已完成 5 / 28 个目标。\n\nWhat subject do you like?", state_text: "【英语对话中】", progress_text: "本单元进度：已完成 5 / 28 个目标。", media_path: media }),
      sendReviewMedia: async (input, path) => { sent.push([input, path]); return true; },
      sendDialogueText: async (input, text) => { sentText.push([input, text]); return true; }, ensureBinding: async () => true,
    });
    const result = await tool.execute("start", { action: "request_dialogue_start", payload: {} });
    assert.deepEqual(JSON.parse(result.content[0].text), { started: true, mode: "dialogue_active", reply_text: null, audio_delivered: true });
    assert.equal(sent.length, 1); assert.equal(sent[0][1], media);
    assert.deepEqual(sentText, [[settings, "【英语对话中】\n\n本单元进度：已完成 5 / 28 个目标。"]]);
  } finally { await rm(root, { recursive: true, force: true }); }
});
test("active workflow never claims success without a visible reply", async () => {
  const result = await handleV3WorkflowInbound(config, { content: "答案" }, inboundContext, {
    readActivity: () => ({ ok: true, mode: "entry" }),
    runWorkflow: async () => ({ ok: true, handled: true, reply_text: null }),
  });
  assert.deepEqual(result, { handled: true, reply: { text: "学习功能暂时不可用，请稍后再试。" } });
});
test("only idle bound inbound falls through to free chat after releasing its binding", async () => {
  let released = 0;
  assert.equal(await handleV3WorkflowInbound(config, { content: "hi" }, { ...inboundContext, conversationId: "other" }), undefined);
  const idle = await handleV3WorkflowInbound(config, { content: "hi" }, inboundContext, { readActivity: () => ({ ok: true, mode: null }), releaseBinding: async () => { released += 1; } });
  assert.equal(idle, undefined); assert.equal(released, 1);
  const failed = await handleV3WorkflowInbound(config, { content: "hi" }, inboundContext, { readActivity: () => ({ ok: true, mode: "entry" }), runWorkflow: async () => ({ ok: false }) });
  assert.equal(failed.handled, true); assert.equal(failed.reply.text, "学习功能暂时不可用，请稍后再试。");
});
test("archive tool binds before start and releases a no_due request", async () => {
  let ensured = 0; let released = 0;
  const tool = createV3WorkflowArchiveTool({ config, ...toolContext, runEntry: async () => ({ started: true, mode: "entry_collecting", reply_text: "开始。" }), runReview: async () => ({ started: false, reason: "no_due_item" }), ensureBinding: async () => { ensured += 1; return true; }, releaseBinding: async () => { released += 1; } });
  assert.deepEqual(tool.parameters.properties.action.enum, ["request_entry_start", "request_review_start", "request_dialogue_start"]);
  assert.equal(JSON.parse((await tool.execute("call", { action: "request_entry_start", payload: {} })).content[0].text).started, true);
  assert.equal(JSON.parse((await tool.execute("call", { action: "request_review_start", payload: {} })).content[0].text).reason, "no_due_item");
  assert.equal(ensured, 2); assert.equal(released, 1);
  assert.equal(createV3WorkflowArchiveTool({ config, sessionKey: "other", agentId: "dada" }), null);
});
test("fixed Dialogue start uses the independent Dialogue CLI configuration", async () => {
  let seen;
  const result = await requestDialogueStart(config, toolContext, async (_config, mode, event, _process, start) => {
    seen = { mode, event, start };
    return { ok: true, handled: true, reply_text: "Let's talk about school." };
  }, () => ({ ok: true, mode: null }), () => 1234);
  assert.deepEqual(result, { started: true, mode: "dialogue_active", reply_text: "Let's talk about school." });
  assert.equal(seen.mode, "dialogue");
  assert.equal(seen.event.content, "开始对话");
  assert.equal(seen.start, true);
});
test("active Dialogue inbound is claimed only by the Dialogue workflow and releases after close", async () => {
  let released = 0;
  const modes = ["dialogue", null];
  const result = await handleV3WorkflowInbound(config, { content: "I like Maths.", timestamp: Date.UTC(2026, 8, 1) }, inboundContext, {
    readActivity: () => ({ ok: true, mode: modes.shift() ?? null }),
    runWorkflow: async (_config, mode, event) => {
      assert.equal(mode, "dialogue");
      assert.equal(event.content, "I like Maths.");
      return { ok: true, handled: true, reply_text: "Great!" };
    },
    releaseBinding: async () => { released += 1; },
  });
  assert.deepEqual(result, { handled: true, reply: { text: "Great!" } });
  assert.equal(released, 1);
});
test("plugin registers an idle-only prompt hook alongside active inbound routing", () => {
  const handlers = new Map();
  registerV3WorkflowInbound({ pluginConfig: config, registerTool() {}, on(name, handler) { handlers.set(name, handler); } });
  assert.equal(typeof handlers.get("inbound_claim"), "function"); assert.equal(typeof handlers.get("before_prompt_build"), "function");
});
