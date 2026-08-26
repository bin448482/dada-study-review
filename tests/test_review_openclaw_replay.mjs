import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { dirname, resolve } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import {
  createV3WorkflowArchiveTool,
  handleV3WorkflowInbound,
  requestReviewStart,
  runV3Workflow,
} from "../runtime/inbound_v3_workflow_hook/index.mjs";

const projectRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const replayPython = resolve(projectRoot, ".venv", "bin", "python");
const replayFixture = resolve(projectRoot, "tests", "review_runtime", "replay_review_cli.py");

const config = {
  agentId: "dada",
  childScopeToken: "child_0123456789abcdef0123456789abcdef",
  inboundBinding: {
    sessionKey: "agent:dada:openclaw-weixin:account:direct:child",
    channelId: "openclaw-weixin",
    accountId: "account",
    conversationId: "child",
  },
  pythonBin: replayPython,
  runtimeRoot: resolve(projectRoot, "runtime"),
  archiveRoot: "/project/data/learning-archives",
  entry: { definitionDirectory: "/definitions/entry", definitionDigest: "a".repeat(64), provider: "entry", model: "entry-model", endpoint: "https://provider.invalid/entry", apiStyle: "responses" },
  review: { definitionDirectory: "/definitions/review", definitionDigest: "b".repeat(64), provider: "review", model: "review-model", endpoint: "https://provider.invalid/review", apiStyle: "responses", schedulePath: "/project/config/review-schedule.test.json" },
};
const toolContext = { agentId: config.agentId, sessionKey: config.inboundBinding.sessionKey };
const inboundContext = { channelId: config.inboundBinding.channelId, accountId: config.inboundBinding.accountId, conversationId: config.inboundBinding.conversationId };
const lockedReply = "这轮有 1 题需要复习，现在还剩 1 题（含当前题）。\n\nI go to school.\n\n请说出这句话的中文意思。";

async function toolResult(tool, action) {
  return JSON.parse((await tool.execute("replay-call", { action, payload: {} })).content[0].text);
}

async function runPythonReplay(_command, args, input) {
  assert.deepEqual(args, ["-m", "v3_review_production.cli"]);
  assert.equal(existsSync(replayPython), true);
  return await new Promise((resolvePromise, reject) => {
    const child = spawn(replayPython, [replayFixture], { stdio: ["pipe", "pipe", "ignore"] });
    let output = "";
    child.stdout.on("data", (chunk) => { output += chunk; });
    child.on("error", reject);
    child.on("close", (code) => {
      if (code !== 0) return reject(new Error("replay fixture failed"));
      try { resolvePromise(JSON.parse(output)); } catch { reject(new Error("replay fixture returned invalid JSON")); }
    });
    child.stdin.end(JSON.stringify(input));
  });
}

test("RE-01 replay starts review and captures the full locked question", async () => {
  const captured = [];
  const tool = createV3WorkflowArchiveTool({
    config, ...toolContext,
    runReview: async (configured, context) => requestReviewStart(
      configured,
      context,
      (settings, mode, event, _process, startRequested) => runV3Workflow(settings, mode, event, runPythonReplay, startRequested),
      () => ({ ok: true, mode: null }),
    ),
    ensureBinding: async () => true,
  });
  const result = await toolResult(tool, "request_review_start");
  captured.push(result.reply_text);
  assert.deepEqual(result, { started: true, mode: "review_active", reply_text: lockedReply });
  assert.deepEqual(captured, [lockedReply]);
  assert.match(captured[0], /^这轮有 1 题需要复习，现在还剩 1 题（含当前题）。\n\nI go to school\.\n\n请说出/);
});

test("RE-02 replay leaves no workflow delivery when no due item exists", async () => {
  let released = 0;
  const tool = createV3WorkflowArchiveTool({
    config, ...toolContext,
    runReview: async () => ({ started: false, reason: "no_due_item" }),
    ensureBinding: async () => true,
    releaseBinding: async () => { released += 1; },
  });
  assert.deepEqual(await toolResult(tool, "request_review_start"), { started: false, reason: "no_due_item" });
  assert.equal(released, 1);
});

test("RE-03 replay claims an active answer and sends only the Review reply", async () => {
  let invoked = 0;
  const result = await handleV3WorkflowInbound(config, { content: "我去上学。", timestamp: Date.UTC(2026, 7, 23) }, inboundContext, {
    readActivity: () => ({ ok: true, mode: "review" }),
    runWorkflow: async (_config, mode, event) => {
      invoked += 1;
      assert.equal(mode, "review");
      assert.equal(event.content, "我去上学。");
      return { ok: true, handled: true, reply_text: "答得很好。" };
    },
  });
  assert.deepEqual(result, { handled: true, reply: { text: "答得很好。" } });
  assert.equal(invoked, 1);
});

test("RE-04 replay keeps review ownership while it requests an entry switch", async () => {
  const modes = ["review", "entry"];
  const result = await handleV3WorkflowInbound(config, { content: "我要录入英语" }, inboundContext, {
    readActivity: () => ({ ok: true, mode: modes.shift() ?? null }),
    runWorkflow: async (_config, mode) => {
      assert.equal(mode, "review");
      return { ok: true, handled: true, reply_text: "已经切换到录入状态。" };
    },
    releaseBinding: async () => { throw new Error("entry is still active and must remain bound"); },
  });
  assert.deepEqual(result, { handled: true, reply: { text: "已经切换到录入状态。" } });
});

test("RE-05 replay never exposes a malformed question as a child question", async () => {
  const result = await handleV3WorkflowInbound(config, { content: "开始复习" }, inboundContext, {
    readActivity: () => ({ ok: true, mode: "review" }),
    runWorkflow: async () => ({ ok: false, handled: false, reply_text: "只有提示、没有题面" }),
  });
  assert.equal(result.handled, true);
  assert.equal(result.reply.text, "学习功能暂时不可用，请稍后再试。");
  assert.doesNotMatch(result.reply.text, /只有提示|题面/);
});

test("RE-06 replay re-delivers an already committed question unchanged after a capture failure", async () => {
  const attempts = [];
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const result = await handleV3WorkflowInbound(config, { content: "重试" }, inboundContext, {
      readActivity: () => ({ ok: true, mode: "review" }),
      runWorkflow: async () => ({ ok: true, handled: true, reply_text: lockedReply }),
    });
    attempts.push(result.reply.text);
  }
  assert.deepEqual(attempts, [lockedReply, lockedReply]);
});

test("RE-07 replay releases completed review traffic to the idle Dada route", async () => {
  let released = 0;
  let invoked = 0;
  const result = await handleV3WorkflowInbound(config, { content: "我们聊聊恐龙吧" }, inboundContext, {
    readActivity: () => ({ ok: true, mode: null }),
    runWorkflow: async () => { invoked += 1; return { ok: true, handled: true, reply_text: "不应调用" }; },
    releaseBinding: async () => { released += 1; },
  });
  assert.equal(result, undefined);
  assert.equal(released, 1);
  assert.equal(invoked, 0);
});
