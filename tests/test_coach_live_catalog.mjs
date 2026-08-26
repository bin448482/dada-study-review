import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const repositoryRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const coachSkillPath = path.join(repositoryRoot, "skill", "dada-english-recite-coach", "SKILL.md");

test("Coach frontmatter carries the fixed actions required by a no-read production session", async () => {
  const source = await readFile(coachSkillPath, "utf8");
  const description = source.match(/^description:\s*"([^\n]*)"$/m)?.[1] ?? "";

  assert.match(description, /dada_repetition_archive/);
  assert.match(description, /request_entry_start/);
  assert.match(description, /request_review_start/);
  assert.match(description, /payload:\{\}/);
  assert.match(description, /不得声称已经开始/);
  assert.match(description, /我要开始复习英语/);
  for (const phrase of ["继续复习", "接着复习", "恢复复习", "继续刚才的复习", "再复习一下", "复习之前学过的内容"]) {
    assert.match(description, new RegExp(phrase));
  }
});

test("Coach only offers an idle review reminder from trusted injected context", async () => {
  const source = await readFile(coachSkillPath, "utf8");
  assert.match(source, /可信系统上下文明确给出.*无活动学习状态.*正数到期题量/);
  assert.match(source, /回复末尾追加系统指定的复习邀请/);
  assert.match(source, /录入与复习活动消息不由本 Skill 处理，绝不在其中添加提醒/);
});
