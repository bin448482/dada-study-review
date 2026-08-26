import assert from "node:assert/strict";
import test from "node:test";
import {
  createV3WorkflowArchiveTool,
  ensureStaticInboundBinding,
  handleV3WorkflowInbound,
  isStaticDadaChildSession,
  isStaticDadaInboundConversation,
  idleDueReminderPrompt,
  readV3IdleDueCount,
  readV3WorkflowActivity,
  registerV3WorkflowInbound,
  requestReviewStart,
  runV3Workflow,
} from "../runtime/inbound_v3_workflow_hook/index.mjs";

const config = {
  agentId: "dada", childScopeToken: "child_0123456789abcdef0123456789abcdef",
  inboundBinding: { sessionKey: "agent:dada:openclaw-weixin:account:direct:child", channelId: "openclaw-weixin", accountId: "account", conversationId: "child" },
  pythonBin: "/project/.venv/bin/python", runtimeRoot: "/project/runtime", archiveRoot: "/project/data/learning-archives",
  entry: { definitionDirectory: "/definitions/entry", definitionDigest: "a".repeat(64), provider: "entry", model: "entry-model", endpoint: "https://provider.invalid/entry", apiStyle: "responses" },
  review: { definitionDirectory: "/definitions/review", definitionDigest: "b".repeat(64), provider: "review", model: "review-model", endpoint: "https://provider.invalid/review", apiStyle: "chat-completions", schedulePath: "/project/config/review-schedule.json" },
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
  assert.deepEqual(result, { ok: true, handled: true, reply_text: "继续。", no_due_item: false }); assert.deepEqual(seen.args, ["-m", "v3_review_production.cli"]); assert.equal(seen.input.external_session_ref, config.childScopeToken); assert.equal(seen.env.DADA_REVIEW_SCHEDULE_PATH, "/project/config/review-schedule.json"); assert.equal(seen.env.DADA_REVIEW_ENTRY_DEFINITION_DIR, "/definitions/entry"); assert.equal("DADA_REVIEW_MODEL_API_KEY" in seen.env, false);
  const entry = await runV3Workflow(config, "entry", { content: "开始复习", timestamp: Date.UTC(2026, 7, 23) }, async (command, args, input, env) => { seen = { command, args, input, env }; return { ok: true, handled: true, reply_text: "继续。" }; });
  assert.equal(entry.ok, true); assert.equal(seen.env.DADA_ENTRY_REVIEW_DEFINITION_DIR, "/definitions/review");
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
  assert.deepEqual(tool.parameters.properties.action.enum, ["request_entry_start", "request_review_start"]);
  assert.equal(JSON.parse((await tool.execute("call", { action: "request_entry_start", payload: {} })).content[0].text).started, true);
  assert.equal(JSON.parse((await tool.execute("call", { action: "request_review_start", payload: {} })).content[0].text).reason, "no_due_item");
  assert.equal(ensured, 2); assert.equal(released, 1);
  assert.equal(createV3WorkflowArchiveTool({ config, sessionKey: "other", agentId: "dada" }), null);
});
test("plugin registers an idle-only prompt hook alongside active inbound routing", () => {
  const handlers = new Map();
  registerV3WorkflowInbound({ pluginConfig: config, registerTool() {}, on(name, handler) { handlers.set(name, handler); } });
  assert.equal(typeof handlers.get("inbound_claim"), "function"); assert.equal(typeof handlers.get("before_prompt_build"), "function");
});
