import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync, lstatSync, realpathSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const MAX_OUTPUT_BYTES = 64 * 1024;
const TIMEOUT_MS = 140_000;
const MEDIA_SEND_TIMEOUT_MS = 45_000;
const UNAVAILABLE_REPLY = "学习功能暂时不可用，请稍后再试。";

function validModel(value, review = false) {
  return value && typeof value.definitionDirectory === "string" && /^[a-f0-9]{64}$/.test(value.definitionDigest)
    && typeof value.provider === "string" && value.provider && typeof value.model === "string" && value.model
    && typeof value.endpoint === "string" && value.endpoint.startsWith("https://") && ["responses", "chat-completions"].includes(value.apiStyle)
    && typeof value.userAgent === "string" && value.userAgent.length <= 256 && /^[^\r\n]+$/.test(value.userAgent)
    && (!review || typeof value.schedulePath === "string" && value.schedulePath);
}
function validDialogue(value) {
  return validModel(value) && value.dialogueUnitId === "grade6-english-unit-1-school-life"
    && typeof value.unitPath === "string" && value.unitPath
    && typeof value.policyPath === "string" && value.policyPath
    && typeof value.reviewSchedulePath === "string" && value.reviewSchedulePath;
}
function validInboundBinding(value) {
  return value && typeof value.sessionKey === "string" && value.sessionKey
    && typeof value.channelId === "string" && value.channelId
    && typeof value.accountId === "string" && value.accountId
    && typeof value.conversationId === "string" && value.conversationId;
}
function validTtsCommon(value) {
  return value && !Array.isArray(value) && typeof value.enabled === "boolean"
    && typeof value.endpoint === "string" && value.endpoint.startsWith("https://")
    && Number.isInteger(value.timeoutSeconds) && value.timeoutSeconds > 0
    && Number.isInteger(value.maxTextChars) && value.maxTextChars > 0
    && Number.isInteger(value.maxAudioBytes) && value.maxAudioBytes > 0
    && Number.isInteger(value.retentionSeconds) && value.retentionSeconds > 0;
}
function validVolcengineSeedTts(value) {
  return validTtsCommon(value) && value.resourceId === "seed-tts-2.0" && typeof value.speaker === "string" && value.speaker;
}
function validMiniMaxTts(value) {
  return validTtsCommon(value)
    && value.endpoint === "https://api.minimaxi.com/v1/t2a_v2"
    && value.model === "speech-2.8-turbo"
    && typeof value.voiceId === "string" && value.voiceId;
}
export function normalizeReviewTts(value) {
  if (value === undefined) return undefined;
  const keys = Object.keys(value).sort();
  const legacyKeys = ["enabled", "endpoint", "maxAudioBytes", "maxTextChars", "resourceId", "retentionSeconds", "speaker", "timeoutSeconds"];
  const canonicalKeys = ["enabled", "endpoint", "maxAudioBytes", "maxTextChars", "provider", "resourceId", "retentionSeconds", "speaker", "timeoutSeconds"];
  const minimaxKeys = ["enabled", "endpoint", "maxAudioBytes", "maxTextChars", "model", "provider", "retentionSeconds", "timeoutSeconds", "voiceId"];
  if (keys.join(",") === legacyKeys.join(",") && validVolcengineSeedTts(value)) return { ...value, provider: "volcengine_seed" };
  if (keys.join(",") === canonicalKeys.join(",") && value.provider === "volcengine_seed" && validVolcengineSeedTts(value)) return value;
  if (keys.join(",") === minimaxKeys.join(",") && value.provider === "minimax" && validMiniMaxTts(value)) return value;
  return null;
}
function validReviewTts(value) { return normalizeReviewTts(value) !== null; }
function diagnosticsEnabled(config) { return config?.diagnostics?.inboundClaim === true; }
function diagnostic(config, logger, fields) {
  if (!diagnosticsEnabled(config)) return;
  logger?.info?.(`[dada-v3-workflow] ${JSON.stringify(fields)}`);
}
function validConfig(config) {
  return config && config.agentId === "dada" && typeof config.childScopeToken === "string" && /^child_[a-f0-9]{32}$/.test(config.childScopeToken)
    && typeof config.pythonBin === "string" && typeof config.runtimeRoot === "string" && typeof config.archiveRoot === "string"
    && validInboundBinding(config.inboundBinding) && validModel(config.entry) && validModel(config.review, true)
    && validDialogue(config.dialogue) && validReviewTts(config.reviewTts);
}
export function isStaticDadaChildSession(config, ctx) {
  return validConfig(config) && ctx?.agentId === config.agentId && ctx?.sessionKey === config.inboundBinding.sessionKey;
}
export function isStaticDadaInboundConversation(config, ctx) {
  return validConfig(config) && ctx?.channelId === config.inboundBinding.channelId
    && ctx?.accountId === config.inboundBinding.accountId && ctx?.conversationId === config.inboundBinding.conversationId;
}
function staticBindingTarget(config) {
  const binding = config.inboundBinding;
  const digest = createHash("sha256").update(JSON.stringify({ pluginId: "dada-v3-workflow-inbound", channel: binding.channelId, accountId: binding.accountId, conversationId: binding.conversationId })).digest("hex").slice(0, 24);
  return `plugin-binding:dada-v3-workflow-inbound:${digest}`;
}
export async function ensureStaticInboundBinding(config, plugin, bindingApi) {
  const pluginRoot = plugin?.rootDir || (typeof plugin?.source === "string" ? dirname(plugin.source) : "");
  if (!validConfig(config) || !pluginRoot || !bindingApi?.createConversationBindingRecord) return false;
  try {
    await bindingApi.createConversationBindingRecord({
      targetSessionKey: staticBindingTarget(config), targetKind: "session", placement: "current",
      conversation: { channel: config.inboundBinding.channelId, accountId: config.inboundBinding.accountId, conversationId: config.inboundBinding.conversationId },
      metadata: { pluginBindingOwner: "plugin", pluginId: plugin.id, pluginName: plugin.name, pluginRoot, summary: "Dada v3 active learning workflow" },
    });
    return true;
  } catch { return false; }
}
export async function releaseStaticInboundBinding(config, bindingApi) {
  if (!validConfig(config) || !bindingApi?.unbindConversationBindingRecord) return false;
  try { await bindingApi.unbindConversationBindingRecord({ targetSessionKey: staticBindingTarget(config), reason: "dada_v3_workflow_idle" }); return true; } catch { return false; }
}
async function loadGatewayBindingApi() {
  const gatewayEntrypoint = typeof process.argv[1] === "string" ? process.argv[1] : "";
  if (!gatewayEntrypoint) throw new Error("gateway entrypoint is unavailable");
  return await import(pathToFileURL(resolve(dirname(gatewayEntrypoint), "plugin-sdk/conversation-runtime.js")).href);
}
function openReadonlyDatabase(path) { const { DatabaseSync } = require("node:sqlite"); return new DatabaseSync(path, { readOnly: true, enableForeignKeyConstraints: true }); }
export function readV3WorkflowActivity(config, scopeToken = config?.childScopeToken, openDatabase = openReadonlyDatabase, pathExists = existsSync) {
  if (!validConfig(config) || scopeToken !== config.childScopeToken) return { ok: true, mode: null };
  const path = `${config.archiveRoot}/workflow-v3.sqlite3`; if (!pathExists(path)) return { ok: true, mode: null };
  try { const db = openDatabase(path); try { const row = db.prepare("SELECT workflow_type FROM workflows WHERE external_session_id = ? AND phase = 'active' LIMIT 1").get(scopeToken); return { ok: true, mode: ["entry", "review", "dialogue"].includes(row?.workflow_type) ? row.workflow_type : null }; } finally { db.close(); } } catch { return { ok: false, mode: null }; }
}
export function readV3IdleDueCount(config, scopeToken = config?.childScopeToken, at = new Date().toISOString(), openDatabase = openReadonlyDatabase, pathExists = existsSync) {
  if (!validConfig(config) || scopeToken !== config.childScopeToken || typeof at !== "string" || !at) return { ok: false, due_count: 0 };
  const path = `${config.archiveRoot}/workflow-v3.sqlite3`; if (!pathExists(path)) return { ok: true, due_count: 0 };
  try {
    const db = openDatabase(path);
    try {
      const paused = db.prepare("SELECT workflow_id FROM workflows WHERE external_session_id = ? AND workflow_type = 'review' AND phase = 'paused_for_entry' LIMIT 1").get(scopeToken);
      if (paused?.workflow_id) {
        const row = db.prepare("SELECT COUNT(*) AS count FROM review_queue_items WHERE workflow_id = ? AND status <> 'completed'").get(paused.workflow_id);
        return { ok: true, due_count: Number.isSafeInteger(row?.count) && row.count > 0 ? row.count : 0 };
      }
      const row = db.prepare("SELECT COUNT(*) AS count FROM learning_items AS item JOIN learning_materials AS material ON material.material_id = item.material_id WHERE material.source_workflow_id IN (SELECT workflow_id FROM workflows WHERE external_session_id = ? AND workflow_type = 'entry') AND material.status = 'active' AND material.needs_parent_review = 0 AND item.review_stage IS NOT NULL AND item.completed_at IS NULL AND item.next_review_at <= ? AND NOT EXISTS (SELECT 1 FROM workflows AS existing WHERE existing.workflow_type = 'review' AND existing.learning_item_id = item.learning_item_id AND existing.phase <> 'closed')").get(scopeToken, at);
      return { ok: true, due_count: Number.isSafeInteger(row?.count) && row.count > 0 ? row.count : 0 };
    } finally { db.close(); }
  } catch { return { ok: false, due_count: 0 }; }
}
export function idleDueReminderPrompt(config, ctx, readActivity = readV3WorkflowActivity, readDueCount = readV3IdleDueCount, at = new Date().toISOString()) {
  if (!isStaticDadaChildSession(config, ctx)) return null;
  const activity = readActivity(config, config.childScopeToken);
  if (!activity.ok || activity.mode) return null;
  const due = readDueCount(config, config.childScopeToken, at);
  if (!due.ok || due.due_count <= 0) return null;
  return `当前是无活动学习状态，已有 ${due.due_count} 道到期复习内容。仅当孩子本轮是普通聊天且你正常文字回复时，在回复末尾追加：“你还有 ${due.due_count} 题可以复习，要现在开始吗？”。孩子明确说开始复习时，仍必须且仅调用 dada_repetition_archive(request_review_start)；不得把提醒当作已经开始，也不得在录入或复习活动状态显示提醒。`;
}
function envFor(config, mode) {
  const model = config[mode]; const prefix = mode === "entry" ? "DADA_ENTRY" : mode === "review" ? "DADA_REVIEW" : "DADA_DIALOGUE";
  const env = { ...process.env, PYTHONPATH: config.runtimeRoot, [`${prefix}_ARCHIVE_ROOT`]: config.archiveRoot, [`${prefix}_DEFINITION_DIR`]: model.definitionDirectory, [`${prefix}_DEFINITION_DIGEST`]: model.definitionDigest, [`${prefix}_MODEL_PROVIDER`]: model.provider, [`${prefix}_MODEL`]: model.model, [`${prefix}_MODEL_ENDPOINT`]: model.endpoint, [`${prefix}_MODEL_API_STYLE`]: model.apiStyle, [`${prefix}_MODEL_USER_AGENT`]: model.userAgent };
  if (mode === "review") {
    env.DADA_REVIEW_SCHEDULE_PATH = model.schedulePath; env.DADA_REVIEW_ENTRY_DEFINITION_DIR = config.entry.definitionDirectory; env.DADA_REVIEW_ENTRY_DEFINITION_DIGEST = config.entry.definitionDigest;
    const tts = normalizeReviewTts(config.reviewTts);
    if (tts) {
      env.DADA_REVIEW_TTS_ENABLED = tts.enabled ? "1" : "0";
      env.DADA_REVIEW_TTS_PROVIDER = tts.provider;
      env.DADA_REVIEW_TTS_ENDPOINT = tts.endpoint;
      env.DADA_REVIEW_TTS_TIMEOUT_SECONDS = String(tts.timeoutSeconds);
      env.DADA_REVIEW_TTS_MAX_TEXT_CHARS = String(tts.maxTextChars);
      env.DADA_REVIEW_TTS_MAX_AUDIO_BYTES = String(tts.maxAudioBytes);
      env.DADA_REVIEW_TTS_RETENTION_SECONDS = String(tts.retentionSeconds);
      if (tts.provider === "volcengine_seed") {
        env.DADA_REVIEW_TTS_RESOURCE_ID = tts.resourceId;
        env.DADA_REVIEW_TTS_SPEAKER = tts.speaker;
        if (tts.enabled && typeof process.env.DADA_REVIEW_TTS_API_KEY !== "string" && typeof process.env.DADA_ENTRY_MODEL_API_KEY === "string") env.DADA_REVIEW_TTS_API_KEY = process.env.DADA_ENTRY_MODEL_API_KEY;
      } else if (tts.provider === "minimax") {
        env.DADA_REVIEW_TTS_MODEL = tts.model;
        env.DADA_REVIEW_TTS_VOICE_ID = tts.voiceId;
      }
    }
  }
  if (mode === "entry") {
    const review = config.review;
    env.DADA_ENTRY_REVIEW_DEFINITION_DIR = review.definitionDirectory;
    env.DADA_ENTRY_REVIEW_DEFINITION_DIGEST = review.definitionDigest;
    env.DADA_ENTRY_REVIEW_MODEL_PROVIDER = review.provider;
    env.DADA_ENTRY_REVIEW_MODEL = review.model;
    env.DADA_ENTRY_REVIEW_MODEL_ENDPOINT = review.endpoint;
    env.DADA_ENTRY_REVIEW_MODEL_API_STYLE = review.apiStyle;
    env.DADA_ENTRY_REVIEW_MODEL_USER_AGENT = review.userAgent;
    env.DADA_ENTRY_REVIEW_SCHEDULE_PATH = review.schedulePath;
    const tts = normalizeReviewTts(config.reviewTts);
    if (tts) {
      env.DADA_REVIEW_TTS_ENABLED = tts.enabled ? "1" : "0";
      env.DADA_REVIEW_TTS_PROVIDER = tts.provider;
      env.DADA_REVIEW_TTS_ENDPOINT = tts.endpoint;
      env.DADA_REVIEW_TTS_TIMEOUT_SECONDS = String(tts.timeoutSeconds);
      env.DADA_REVIEW_TTS_MAX_TEXT_CHARS = String(tts.maxTextChars);
      env.DADA_REVIEW_TTS_MAX_AUDIO_BYTES = String(tts.maxAudioBytes);
      env.DADA_REVIEW_TTS_RETENTION_SECONDS = String(tts.retentionSeconds);
      if (tts.provider === "volcengine_seed") {
        env.DADA_REVIEW_TTS_RESOURCE_ID = tts.resourceId;
        env.DADA_REVIEW_TTS_SPEAKER = tts.speaker;
        if (tts.enabled && typeof process.env.DADA_REVIEW_TTS_API_KEY !== "string" && typeof process.env.DADA_ENTRY_MODEL_API_KEY === "string") env.DADA_REVIEW_TTS_API_KEY = process.env.DADA_ENTRY_MODEL_API_KEY;
      } else if (tts.provider === "minimax") {
        env.DADA_REVIEW_TTS_MODEL = tts.model;
        env.DADA_REVIEW_TTS_VOICE_ID = tts.voiceId;
      }
    }
  }
  if (mode === "dialogue") {
    env.DADA_DIALOGUE_UNIT_PATH = model.unitPath;
    env.DADA_DIALOGUE_POLICY_PATH = model.policyPath;
    env.DADA_DIALOGUE_REVIEW_SCHEDULE_PATH = model.reviewSchedulePath;
    const tts = normalizeReviewTts(config.reviewTts);
    if (tts) {
      env.DADA_REVIEW_TTS_ENABLED = tts.enabled ? "1" : "0";
      env.DADA_REVIEW_TTS_PROVIDER = tts.provider;
      env.DADA_REVIEW_TTS_ENDPOINT = tts.endpoint;
      env.DADA_REVIEW_TTS_TIMEOUT_SECONDS = String(tts.timeoutSeconds);
      env.DADA_REVIEW_TTS_MAX_TEXT_CHARS = String(tts.maxTextChars);
      env.DADA_REVIEW_TTS_MAX_AUDIO_BYTES = String(tts.maxAudioBytes);
      env.DADA_REVIEW_TTS_RETENTION_SECONDS = String(tts.retentionSeconds);
      if (tts.provider === "volcengine_seed") {
        env.DADA_REVIEW_TTS_RESOURCE_ID = tts.resourceId;
        env.DADA_REVIEW_TTS_SPEAKER = tts.speaker;
        if (tts.enabled && typeof process.env.DADA_REVIEW_TTS_API_KEY !== "string" && typeof process.env.DADA_ENTRY_MODEL_API_KEY === "string") env.DADA_REVIEW_TTS_API_KEY = process.env.DADA_ENTRY_MODEL_API_KEY;
      } else if (tts.provider === "minimax") {
        env.DADA_REVIEW_TTS_MODEL = tts.model;
        env.DADA_REVIEW_TTS_VOICE_ID = tts.voiceId;
      }
    }
  }
  return env;
}
function runJsonProcess(command, args, input, env) { return new Promise((resolve, reject) => { const child = spawn(command, args, { env, stdio: ["pipe", "pipe", "ignore"] }); let output = ""; const timeout = setTimeout(() => child.kill("SIGTERM"), TIMEOUT_MS); child.stdout.on("data", (chunk) => { output += chunk; if (Buffer.byteLength(output, "utf8") > MAX_OUTPUT_BYTES) child.kill("SIGTERM"); }); child.on("error", reject); child.on("close", (code) => { clearTimeout(timeout); if (code !== 0) return reject(new Error("workflow process failed")); try { resolve(JSON.parse(output)); } catch { reject(new Error("workflow process returned invalid JSON")); } }); child.stdin.end(JSON.stringify(input)); }); }
export async function runV3Workflow(config, mode, event, runProcess = runJsonProcess, startRequested = false) {
  if (!validConfig(config) || !["entry", "review", "dialogue"].includes(mode) || typeof event?.content !== "string" || !event.content) return { ok: false, handled: false, reply_text: null, progress_text: null, no_due_item: false, media_path: null };
  const module = mode === "entry" ? "v3_entry_production.cli" : mode === "review" ? "v3_review_production.cli" : "v3_dialogue_production.cli";
  try { const value = await runProcess(config.pythonBin, ["-m", module], { message_text: event.content, received_at: new Date(event.timestamp ?? Date.now()).toISOString(), external_session_ref: config.childScopeToken, start_requested: startRequested }, envFor(config, mode)); return value && value.ok === true && typeof value.handled === "boolean" && (value.reply_text === null || typeof value.reply_text === "string") && (value.state_text === null || value.state_text === undefined || typeof value.state_text === "string") && (value.progress_text === null || value.progress_text === undefined || typeof value.progress_text === "string") && (value.media_path === null || typeof value.media_path === "string" || value.media_path === undefined) && (mode === "entry" || mode === "dialogue" || typeof value.no_due_item === "boolean") ? { ok: true, handled: value.handled, reply_text: value.reply_text, state_text: typeof value.state_text === "string" ? value.state_text : null, progress_text: typeof value.progress_text === "string" ? value.progress_text : null, no_due_item: Boolean(value.no_due_item), media_path: typeof value.media_path === "string" ? value.media_path : null } : { ok: false, handled: false, reply_text: null, state_text: null, progress_text: null, no_due_item: false, media_path: null }; } catch { return { ok: false, handled: false, reply_text: null, state_text: null, progress_text: null, no_due_item: false, media_path: null }; }
}
function ttsConfigFor(config, _mode) { return normalizeReviewTts(config?.reviewTts); }
export function safeTtsMediaPath(config, value, mode = "review") {
  const tts = ttsConfigFor(config, mode);
  if (!tts?.enabled || typeof value !== "string" || !value) return null;
  try {
    const outbox = realpathSync(resolve(config.archiveRoot, "outbound-media"));
    const path = resolve(value);
    if (dirname(path) !== outbox || !path.endsWith(".mp3") || realpathSync(path) !== path) return null;
    const stat = lstatSync(path);
    return stat.isFile() && !stat.isSymbolicLink() && stat.size > 0 && stat.size <= tts.maxAudioBytes ? path : null;
  } catch { return null; }
}
export function safeReviewMediaPath(config, value) { return safeTtsMediaPath(config, value, "review"); }
export function reviewMediaDeliveryArgs(config, mediaPath, gatewayEntrypoint = process.argv[1]) {
  const path = safeReviewMediaPath(config, mediaPath);
  const binding = config?.inboundBinding;
  if (!path || binding?.channelId !== "openclaw-weixin" || typeof gatewayEntrypoint !== "string" || !gatewayEntrypoint) return null;
  return [gatewayEntrypoint, "message", "send", "--channel", binding.channelId, "--account", binding.accountId, "--target", binding.conversationId, "--media", path, "--json"];
}
function runReviewMediaDelivery(config, mediaPath) {
  const args = reviewMediaDeliveryArgs(config, mediaPath);
  if (!args) return Promise.resolve(false);
  return new Promise((resolve) => {
    const child = spawn(process.execPath, args, { env: process.env, stdio: ["ignore", "ignore", "ignore"] });
    const timeout = setTimeout(() => child.kill("SIGTERM"), MEDIA_SEND_TIMEOUT_MS);
    child.on("error", () => { clearTimeout(timeout); resolve(false); });
    child.on("close", (code) => { clearTimeout(timeout); resolve(code === 0); });
  });
}
export function dialogueTextDeliveryArgs(config, text, gatewayEntrypoint = process.argv[1]) {
  const binding = config?.inboundBinding;
  if (binding?.channelId !== "openclaw-weixin" || typeof text !== "string" || !text || typeof gatewayEntrypoint !== "string" || !gatewayEntrypoint) return null;
  return [gatewayEntrypoint, "message", "send", "--channel", binding.channelId, "--account", binding.accountId, "--target", binding.conversationId, "--message", text, "--json"];
}
function runDialogueTextDelivery(config, text) {
  const args = dialogueTextDeliveryArgs(config, text);
  if (!args) return Promise.resolve(false);
  return new Promise((resolve) => {
    const child = spawn(process.execPath, args, { env: process.env, stdio: ["ignore", "ignore", "ignore"] });
    const timeout = setTimeout(() => child.kill("SIGTERM"), MEDIA_SEND_TIMEOUT_MS);
    child.on("error", () => { clearTimeout(timeout); resolve(false); });
    child.on("close", (code) => { clearTimeout(timeout); resolve(code === 0); });
  });
}
export async function deliverDialogueText(config, text, send = runDialogueTextDelivery) {
  const value = typeof text === "string" && text ? text : null;
  return value ? Boolean(await send(config, value)) : false;
}
export async function deliverReviewMedia(config, mediaPath, send = runReviewMediaDelivery) {
  const path = safeReviewMediaPath(config, mediaPath);
  return path ? Boolean(await send(config, path)) : false;
}
export async function deliverTtsMedia(config, mediaPath, mode = "review", send = runReviewMediaDelivery) {
  const path = safeTtsMediaPath(config, mediaPath, mode);
  return path ? Boolean(await send(config, path)) : false;
}
async function deliverReviewMediaOrFallback(config, mediaPath, sendReviewMedia) {
  const path = safeReviewMediaPath(config, mediaPath);
  if (!path) return false;
  try { return Boolean(await sendReviewMedia(config, path)); } catch { return false; }
}
async function deliverTtsMediaOrFallback(config, mediaPath, mode, sendMedia) {
  const path = safeTtsMediaPath(config, mediaPath, mode);
  if (!path) return false;
  try { return Boolean(await sendMedia(config, path)); } catch { return false; }
}
function visibleReviewReply(result) {
  if (typeof result?.reply_text !== "string" || !result.reply_text) return null;
  return typeof result.progress_text === "string" && result.progress_text ? `${result.progress_text}\n\n${result.reply_text}` : result.reply_text;
}
export async function requestReviewStart(config, ctx, runWorkflow = runV3Workflow, readActivity = readV3WorkflowActivity, now = Date.now) {
  if (!isStaticDadaChildSession(config, ctx)) return { ok: false, error: { code: "not_authorized" } };
  const activity = readActivity(config, config.childScopeToken); if (!activity.ok) return { ok: false, error: { code: "unavailable" } }; if (activity.mode) return { ok: false, error: { code: "already_active" } };
  const result = await runWorkflow(config, "review", { content: "开始复习", timestamp: now(), sessionKey: ctx.sessionKey }, undefined, true);
  if (!result.ok) return { ok: false, error: { code: "unavailable" } };
  return result.no_due_item ? { started: false, reason: "no_due_item" } : result.handled ? { started: true, mode: "review_active", reply_text: visibleReviewReply(result), progress_text: result.progress_text, media_path: result.media_path } : { ok: false, error: { code: "unavailable" } };
}
export async function requestEntryStart(config, ctx, runWorkflow = runV3Workflow, readActivity = readV3WorkflowActivity, now = Date.now) {
  if (!isStaticDadaChildSession(config, ctx)) return { ok: false, error: { code: "not_authorized" } };
  const activity = readActivity(config, config.childScopeToken); if (!activity.ok) return { ok: false, error: { code: "unavailable" } }; if (activity.mode) return { ok: false, error: { code: "already_active" } };
  const result = await runWorkflow(config, "entry", { content: "开始录入", timestamp: now(), sessionKey: ctx.sessionKey }, undefined, true);
  return result.ok && result.handled ? { started: true, mode: "entry_collecting", reply_text: result.reply_text } : { ok: false, error: { code: "unavailable" } };
}
export async function requestDialogueStart(config, ctx, runWorkflow = runV3Workflow, readActivity = readV3WorkflowActivity, now = Date.now) {
  if (!isStaticDadaChildSession(config, ctx)) return { ok: false, error: { code: "not_authorized" } };
  const activity = readActivity(config, config.childScopeToken); if (!activity.ok) return { ok: false, error: { code: "unavailable" } }; if (activity.mode) return { ok: false, error: { code: "already_active" } };
  const result = await runWorkflow(config, "dialogue", { content: "开始对话", timestamp: now(), sessionKey: ctx.sessionKey }, undefined, true);
  if (!result.ok || !result.handled) return { ok: false, error: { code: "unavailable" } };
  const response = { started: true, mode: "dialogue_active", reply_text: result.reply_text };
  if (typeof result.state_text === "string") response.state_text = result.state_text;
  if (typeof result.progress_text === "string") response.progress_text = result.progress_text;
  if (typeof result.media_path === "string") response.media_path = result.media_path;
  return response;
}
export function createV3WorkflowArchiveTool({ config, sessionKey, agentId = config?.agentId, runEntry = requestEntryStart, runReview = requestReviewStart, runDialogue = requestDialogueStart, sendReviewMedia = deliverReviewMedia, sendDialogueText = deliverDialogueText, ensureBinding = async () => true, releaseBinding = async () => true, now = Date.now }) {
  if (!isStaticDadaChildSession(config, { agentId, sessionKey })) return null;
  return {
    name: "dada_repetition_archive",
    label: "Dada Recitation Archive",
    description: "Use only to request the current static child session's v3 entry, review or Dialogue mode.",
    parameters: {
      type: "object", additionalProperties: false, required: ["action"],
      properties: {
        action: { type: "string", enum: ["request_entry_start", "request_review_start", "request_dialogue_start"] },
        payload: { type: "object", additionalProperties: false },
      },
    },
    async execute(_id, params) {
      if (!params || !["request_entry_start", "request_review_start", "request_dialogue_start"].includes(params.action) || (params.payload && (typeof params.payload !== "object" || Array.isArray(params.payload) || Object.keys(params.payload).length))) {
        return { content: [{ type: "text", text: JSON.stringify({ ok: false, error: { code: "invalid_request" } }) }] };
      }
      if (!(await ensureBinding())) return { content: [{ type: "text", text: JSON.stringify({ ok: false, error: { code: "unavailable" } }) }] };
      const result = params.action === "request_entry_start"
        ? await runEntry(config, { agentId: config.agentId, sessionKey }, runV3Workflow, readV3WorkflowActivity, now)
        : params.action === "request_review_start"
          ? await runReview(config, { agentId: config.agentId, sessionKey }, runV3Workflow, readV3WorkflowActivity, now)
          : await runDialogue(config, { agentId: config.agentId, sessionKey }, runV3Workflow, readV3WorkflowActivity, now);
      if (!result?.started) await releaseBinding();
      const mediaMode = params.action === "request_review_start" ? "review" : params.action === "request_dialogue_start" ? "dialogue" : null;
      const publicResult = result && typeof result === "object"
        ? Object.fromEntries(Object.entries(result).filter(([key]) => key !== "media_path" && key !== "progress_text" && key !== "state_text"))
        : result;
      const stateProgressText = mediaMode === "dialogue"
        ? [result?.state_text, result?.progress_text].filter(Boolean).join("\n\n") || null
        : null;
      const dialogueStatusPreflight = mediaMode === "dialogue" && result?.started && stateProgressText
        && safeTtsMediaPath(config, result?.media_path, "dialogue");
      const dialogueStatusSent = dialogueStatusPreflight
        ? await deliverDialogueText(config, stateProgressText, sendDialogueText)
        : false;
      const audioDelivered = mediaMode ? await deliverTtsMediaOrFallback(config, result?.media_path, mediaMode, sendReviewMedia) : false;
      if (mediaMode && result?.started && audioDelivered && publicResult && typeof publicResult === "object") {
        if (mediaMode === "review") {
          publicResult.reply_text = result.progress_text || null;
          publicResult.audio_delivered = true;
        } else {
          publicResult.reply_text = dialogueStatusSent ? null : stateProgressText;
          publicResult.audio_delivered = dialogueStatusSent;
        }
      } else if (mediaMode === "dialogue" && result?.started && dialogueStatusSent && publicResult && typeof publicResult === "object") {
        const modelReply = typeof result.reply_text === "string" && stateProgressText && result.reply_text.startsWith(`${stateProgressText}\n\n`)
          ? result.reply_text.slice(stateProgressText.length + 2)
          : result.reply_text;
        const modelTextSent = await deliverDialogueText(config, modelReply, sendDialogueText);
        publicResult.reply_text = modelTextSent ? null : result.reply_text;
        publicResult.audio_delivered = false;
      }
      return { content: [{ type: "text", text: JSON.stringify(publicResult) }] };
    },
  };
}
export async function handleV3WorkflowInbound(config, event, ctx, { runWorkflow = runV3Workflow, readActivity = readV3WorkflowActivity, sendReviewMedia = deliverReviewMedia, releaseBinding = async () => true } = {}) {
  if (!isStaticDadaInboundConversation(config, ctx)) return undefined;
  const activity = readActivity(config);
  if (!activity.ok) return { handled: true, reply: { text: UNAVAILABLE_REPLY } };
  if (!activity.mode) { await releaseBinding(); return undefined; }
  const result = await runWorkflow(config, activity.mode, event);
  if (result.ok && result.handled) {
    const after = readActivity(config);
    if (after.ok && !after.mode) await releaseBinding();
    const isReviewDelivery = after.ok ? after.mode === "review" : activity.mode === "review";
    const isDialogueDelivery = activity.mode === "dialogue";
    const mediaMode = isReviewDelivery ? "review" : isDialogueDelivery ? "dialogue" : null;
    const audioDelivered = mediaMode ? await deliverTtsMediaOrFallback(config, result.media_path, mediaMode, sendReviewMedia) : false;
    if (audioDelivered) {
      const text = mediaMode === "review" ? result.progress_text : [result.state_text, result.progress_text].filter(Boolean).join("\n\n");
      return text ? { handled: true, reply: { text } } : { handled: true };
    }
    const visibleReply = isReviewDelivery ? visibleReviewReply(result) : result.reply_text;
    return visibleReply ? { handled: true, reply: { text: visibleReply } } : { handled: true, reply: { text: UNAVAILABLE_REPLY } };
  }
  return { handled: true, reply: { text: UNAVAILABLE_REPLY } };
}
export function registerV3WorkflowInbound(api) {
  const config = api.pluginConfig ?? {};
  const bindingApi = {
    createConversationBindingRecord: async (input) => (await loadGatewayBindingApi()).createConversationBindingRecord(input),
    unbindConversationBindingRecord: async (input) => (await loadGatewayBindingApi()).unbindConversationBindingRecord(input),
  };
  const ensureBinding = () => ensureStaticInboundBinding(config, api, bindingApi);
  const releaseBinding = () => releaseStaticInboundBinding(config, bindingApi);
  api.registerTool?.((ctx) => createV3WorkflowArchiveTool({ config, sessionKey: ctx.sessionKey, agentId: ctx.agentId, ensureBinding, releaseBinding }), { name: "dada_repetition_archive" });
  api.on("gateway_start", async () => {
    const activity = readV3WorkflowActivity(config);
    const bound = activity.ok && activity.mode ? await ensureBinding() : false;
    diagnostic(config, api.logger, { hook: "gateway_start", active_mode: activity.mode, activity_read_ok: activity.ok, binding_created: bound });
  });
  api.on("inbound_claim", async (event, ctx) => {
    const authorized = isStaticDadaInboundConversation(config, ctx);
    const activity = authorized ? readV3WorkflowActivity(config) : null;
    const result = await handleV3WorkflowInbound(config, event, ctx, { releaseBinding });
    diagnostic(config, api.logger, { hook: "inbound_claim", channel_id_present: Boolean(ctx?.channelId), account_id_present: Boolean(ctx?.accountId), conversation_id_present: Boolean(ctx?.conversationId), authorized, activity_read_ok: activity?.ok ?? null, activity_mode: activity?.mode ?? null, claimed: result?.handled === true });
    return result;
  });
  api.on("before_prompt_build", async (_event, ctx) => {
    const prompt = idleDueReminderPrompt(config, ctx);
    return prompt ? { appendSystemContext: prompt } : undefined;
  });
}
export default { id: "dada-v3-workflow-inbound", name: "Dada v3 Workflow Inbound", register: registerV3WorkflowInbound };
