import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import { existsSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const MAX_OUTPUT_BYTES = 64 * 1024;
const TIMEOUT_MS = 140_000;
const UNAVAILABLE_REPLY = "学习功能暂时不可用，请稍后再试。";

function validModel(value, review = false) {
  return value && typeof value.definitionDirectory === "string" && /^[a-f0-9]{64}$/.test(value.definitionDigest)
    && typeof value.provider === "string" && value.provider && typeof value.model === "string" && value.model
    && typeof value.endpoint === "string" && value.endpoint.startsWith("https://") && ["responses", "chat-completions"].includes(value.apiStyle)
    && (!review || typeof value.schedulePath === "string" && value.schedulePath);
}
function validInboundBinding(value) {
  return value && typeof value.sessionKey === "string" && value.sessionKey
    && typeof value.channelId === "string" && value.channelId
    && typeof value.accountId === "string" && value.accountId
    && typeof value.conversationId === "string" && value.conversationId;
}
function diagnosticsEnabled(config) { return config?.diagnostics?.inboundClaim === true; }
function diagnostic(config, logger, fields) {
  if (!diagnosticsEnabled(config)) return;
  logger?.info?.(`[dada-v3-workflow] ${JSON.stringify(fields)}`);
}
function validConfig(config) {
  return config && config.agentId === "dada" && typeof config.childScopeToken === "string" && /^child_[a-f0-9]{32}$/.test(config.childScopeToken)
    && typeof config.pythonBin === "string" && typeof config.runtimeRoot === "string" && typeof config.archiveRoot === "string"
    && validInboundBinding(config.inboundBinding) && validModel(config.entry) && validModel(config.review, true);
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
  try { const db = openDatabase(path); try { const row = db.prepare("SELECT workflow_type FROM workflows WHERE external_session_id = ? AND phase = 'active' LIMIT 1").get(scopeToken); return { ok: true, mode: row?.workflow_type === "entry" || row?.workflow_type === "review" ? row.workflow_type : null }; } finally { db.close(); } } catch { return { ok: false, mode: null }; }
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
  const model = config[mode]; const prefix = mode === "entry" ? "DADA_ENTRY" : "DADA_REVIEW";
  const env = { ...process.env, PYTHONPATH: config.runtimeRoot, [`${prefix}_ARCHIVE_ROOT`]: config.archiveRoot, [`${prefix}_DEFINITION_DIR`]: model.definitionDirectory, [`${prefix}_DEFINITION_DIGEST`]: model.definitionDigest, [`${prefix}_MODEL_PROVIDER`]: model.provider, [`${prefix}_MODEL`]: model.model, [`${prefix}_MODEL_ENDPOINT`]: model.endpoint, [`${prefix}_MODEL_API_STYLE`]: model.apiStyle };
  if (mode === "review") { env.DADA_REVIEW_SCHEDULE_PATH = model.schedulePath; env.DADA_REVIEW_ENTRY_DEFINITION_DIR = config.entry.definitionDirectory; env.DADA_REVIEW_ENTRY_DEFINITION_DIGEST = config.entry.definitionDigest; }
  if (mode === "entry") {
    const review = config.review;
    env.DADA_ENTRY_REVIEW_DEFINITION_DIR = review.definitionDirectory;
    env.DADA_ENTRY_REVIEW_DEFINITION_DIGEST = review.definitionDigest;
    env.DADA_ENTRY_REVIEW_MODEL_PROVIDER = review.provider;
    env.DADA_ENTRY_REVIEW_MODEL = review.model;
    env.DADA_ENTRY_REVIEW_MODEL_ENDPOINT = review.endpoint;
    env.DADA_ENTRY_REVIEW_MODEL_API_STYLE = review.apiStyle;
    env.DADA_ENTRY_REVIEW_SCHEDULE_PATH = review.schedulePath;
  }
  return env;
}
function runJsonProcess(command, args, input, env) { return new Promise((resolve, reject) => { const child = spawn(command, args, { env, stdio: ["pipe", "pipe", "ignore"] }); let output = ""; const timeout = setTimeout(() => child.kill("SIGTERM"), TIMEOUT_MS); child.stdout.on("data", (chunk) => { output += chunk; if (Buffer.byteLength(output, "utf8") > MAX_OUTPUT_BYTES) child.kill("SIGTERM"); }); child.on("error", reject); child.on("close", (code) => { clearTimeout(timeout); if (code !== 0) return reject(new Error("workflow process failed")); try { resolve(JSON.parse(output)); } catch { reject(new Error("workflow process returned invalid JSON")); } }); child.stdin.end(JSON.stringify(input)); }); }
export async function runV3Workflow(config, mode, event, runProcess = runJsonProcess, startRequested = false) {
  if (!validConfig(config) || !["entry", "review"].includes(mode) || typeof event?.content !== "string" || !event.content) return { ok: false, handled: false, reply_text: null, no_due_item: false };
  const module = mode === "entry" ? "v3_entry_production.cli" : "v3_review_production.cli";
  try { const value = await runProcess(config.pythonBin, ["-m", module], { message_text: event.content, received_at: new Date(event.timestamp ?? Date.now()).toISOString(), external_session_ref: config.childScopeToken, start_requested: startRequested }, envFor(config, mode)); return value && value.ok === true && typeof value.handled === "boolean" && (value.reply_text === null || typeof value.reply_text === "string") && (mode === "entry" || typeof value.no_due_item === "boolean") ? { ok: true, handled: value.handled, reply_text: value.reply_text, no_due_item: Boolean(value.no_due_item) } : { ok: false, handled: false, reply_text: null, no_due_item: false }; } catch { return { ok: false, handled: false, reply_text: null, no_due_item: false }; }
}
export async function requestReviewStart(config, ctx, runWorkflow = runV3Workflow, readActivity = readV3WorkflowActivity, now = Date.now) {
  if (!isStaticDadaChildSession(config, ctx)) return { ok: false, error: { code: "not_authorized" } };
  const activity = readActivity(config, config.childScopeToken); if (!activity.ok) return { ok: false, error: { code: "unavailable" } }; if (activity.mode) return { ok: false, error: { code: "already_active" } };
  const result = await runWorkflow(config, "review", { content: "开始复习", timestamp: now(), sessionKey: ctx.sessionKey }, undefined, true);
  if (!result.ok) return { ok: false, error: { code: "unavailable" } };
  return result.no_due_item ? { started: false, reason: "no_due_item" } : result.handled ? { started: true, mode: "review_active", reply_text: result.reply_text } : { ok: false, error: { code: "unavailable" } };
}
export async function requestEntryStart(config, ctx, runWorkflow = runV3Workflow, readActivity = readV3WorkflowActivity, now = Date.now) {
  if (!isStaticDadaChildSession(config, ctx)) return { ok: false, error: { code: "not_authorized" } };
  const activity = readActivity(config, config.childScopeToken); if (!activity.ok) return { ok: false, error: { code: "unavailable" } }; if (activity.mode) return { ok: false, error: { code: "already_active" } };
  const result = await runWorkflow(config, "entry", { content: "开始录入", timestamp: now(), sessionKey: ctx.sessionKey }, undefined, true);
  return result.ok && result.handled ? { started: true, mode: "entry_collecting", reply_text: result.reply_text } : { ok: false, error: { code: "unavailable" } };
}
export function createV3WorkflowArchiveTool({ config, sessionKey, agentId = config?.agentId, runEntry = requestEntryStart, runReview = requestReviewStart, ensureBinding = async () => true, releaseBinding = async () => true, now = Date.now }) {
  if (!isStaticDadaChildSession(config, { agentId, sessionKey })) return null;
  return { name: "dada_repetition_archive", label: "Dada Recitation Archive", description: "Use only to request the current static child session's v3 entry or review mode.", parameters: { type: "object", additionalProperties: false, required: ["action"], properties: { action: { type: "string", enum: ["request_entry_start", "request_review_start"] }, payload: { type: "object", additionalProperties: false } } }, async execute(_id, params) { if (!params || !["request_entry_start", "request_review_start"].includes(params.action) || (params.payload && (typeof params.payload !== "object" || Array.isArray(params.payload) || Object.keys(params.payload).length))) return { content: [{ type: "text", text: JSON.stringify({ ok: false, error: { code: "invalid_request" } }) }] }; if (!(await ensureBinding())) return { content: [{ type: "text", text: JSON.stringify({ ok: false, error: { code: "unavailable" } }) }] }; const result = params.action === "request_entry_start" ? await runEntry(config, { agentId: config.agentId, sessionKey }, runV3Workflow, readV3WorkflowActivity, now) : await runReview(config, { agentId: config.agentId, sessionKey }, runV3Workflow, readV3WorkflowActivity, now); if (!result?.started) await releaseBinding(); return { content: [{ type: "text", text: JSON.stringify(result) }] }; } };
}
export async function handleV3WorkflowInbound(config, event, ctx, { runWorkflow = runV3Workflow, readActivity = readV3WorkflowActivity, releaseBinding = async () => true } = {}) {
  if (!isStaticDadaInboundConversation(config, ctx)) return undefined;
  const activity = readActivity(config);
  if (!activity.ok) return { handled: true, reply: { text: UNAVAILABLE_REPLY } };
  if (!activity.mode) { await releaseBinding(); return undefined; }
  const result = await runWorkflow(config, activity.mode, event);
  if (result.ok && result.handled) {
    const after = readActivity(config);
    if (after.ok && !after.mode) await releaseBinding();
    return result.reply_text ? { handled: true, reply: { text: result.reply_text } } : { handled: true, reply: { text: UNAVAILABLE_REPLY } };
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
