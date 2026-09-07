---
name: dada-textbook-markdown-to-unit-candidate
description: "根据显式课程工作区中的 Unit 主题和按页拆分的英文教材 Markdown 设计、校验并固化 Dialogue Unit candidate。课程维护需要生成 targets、scenes 和 question intents 时使用；不要用于截图、档案、运行时交付或生产启用。"
---

# 教材 Markdown 到 Dialogue Unit Candidate

输入是显式课程工作区中一个已完成的 Unit `english-text/` 目录，以及调用者提供的 `unit_id`、`title`、`version` 和允许的 source pages。输出是 v3 Unit candidate、结构化 task-audit、结构化 semantic-review，以及仅在三者校验通过后生成的同版本不可变 Unit JSON。这是课程维护 Skill，不是面向孩子的 Dada Skill：不得读取或写入学习档案、截图、OpenClaw 配置、凭据、Gateway 状态或生产 allowlist。

target 生成遵循有约束的多解原则：同一教材内容可以产生多套合法的 target 划分、英文锚点或问题意图，不要求与某个参考 JSON 唯一相同；但每套结果都必须有可追溯教材来源、独立且可复用的语义、自然的 Dialogue 验证，并通过去重、引用、合同及版本校验。固化后不得覆盖既有版本，另一套合理设计必须作为新版本 candidate。

## 必须遵循的流程

1. 读取 Unit README、仅允许的页面 Markdown 文件。如果选定页面缺失、为空、含有未解决的转写标记，或与请求的页码冲突，则停止。
2. 完整阅读每页的主题、标题、说明、图片/表格、示范语言和问题。标题只是定位和理解教学任务的上下文，不是固定策略名、target 类型白名单或 evidence 决定器；新标题和标题变体不因字面陌生而失败。
3. 先建立“页面教学任务清单”，再设计 target。每个任务至少记录：页面/区块定位、教材要求孩子执行的动作、信息对象或语义维度、孩子输出方式，以及它是必做、邀请练习、示例还是背景说明。不能只记录标题或抽出的英文词。
4. 做任务覆盖审计：每个有学习者动作的任务必须映射到至少一个 target、场景内容槽位或明确的覆盖检查项；如果被已有 target 合并，必须说明覆盖的语义维度，不能因为换了标题或问题措辞而丢失任务。未被解释的任务清单视为抽取失败。
5. 再根据任务语义选择 `word`、`phrase`、`sentence` 或 `function`。允许语义等价的自然英语，但必须能用 `semantic_focus` 和 `source_refs` 说明从页面任务到该 target 的语义桥接；不能引入页面任务、Unit 主题或场景不支持的内容。
6. 单独盘点页面中的语言材料：示范对话句、练习题句、`My learning notes`/词组表中明确列出的表达、可替换句型骨架和成组疑问词。可复用的句型或表达不能被 function 覆盖后静默丢弃；必须单独成为 `phrase`/`sentence` target，或在任务覆盖审计中说明为什么只作为场景支架/示例。一次性人名、事实答案、寒暄和纯操作说明可以排除，但必须有理由。
7. 为每个 target 写入 `source_refs`、`semantic_focus`、`dialogue_evidence` 和 `child_output_requirement`。每个 `phrase` 还要写入受限的 `review_design`：
   复习目的、上下文类型、信息槽位、题面约束、至少一个目标形式示例和可选语义替代表达。教材问句默认检验理解后的完整回答；只有任务要求或明确邀请孩子发起提问时，才使用 `initiate_question`。此时必须保留角色、目的和信息缺口，并列出问题所覆盖的信息维度；不能只写“主动提问”。
8. 先确定 targets，再生成同主题场景和 question intents。每个 target 至少有一个 intent；场景、target、intent 和 completion 的引用必须双向一致。一个 intent 不得代替另一个未覆盖的页面任务。
9. 做跨页语义去重。相同学习结果只保留一个稳定 target；不同但合理的拆分可以保留为候选版本或评审备选，不能在同一个 Unit 中重复建点。合并 target 必须覆盖被合并任务的全部信息维度和可复用句型。
10. 在 `config/dialogue-units/<unit-id>/` 中生成 `unit.v<version>.candidate.json`、`unit.v<version>.candidate.task-audit.json` 和 `unit.v<version>.candidate.semantic-review.json`。候选只保存最小 target 合同和页面来源，不生成标题策略报告；两个 sidecar 是固化前的结构化审计输出。
11. 运行 `scripts/validate-dialogue-unit-candidate.py` 并传入两个审计文件，再运行 `scripts/finalize-dialogue-unit-candidate.py`。任一步失败时保留 candidate 和审计输出、返回精确原因，不写入正式 Unit 包。

永远不要覆盖已存在的最终 Unit 版本。固化后不得修改冻结 Unit、发布 Skill 或启用 Unit。

生成 candidate 前阅读 [references/unit-candidate-contract.md](references/unit-candidate-contract.md)。该合同不依赖标题策略文件。

## 页面教学任务清单的最小格式

在模型内部先为每个页面建立清单，再输出最终 JSON。清单不写入 Unit 包，但必须能回答“这一页要求孩子做什么，以及每个要求由哪个 target 或场景覆盖”：

```text
page: 16
locator: B1 Look and say / question prompts
learner_action: 主动询问社团信息
information_dimensions: 人物、活动、时长、人数等具体信息
output: ask
modality: invited_practice
coverage: ask_for_specific_club_information (initiate_question)
```

`learner_action` 不能写成“学习 Look and say”；必须写成可观察动作，例如识别、回答、比较、说明理由、主动提问、澄清或互动。`information_dimensions` 不能省略：`Who / What / How long / How many` 这类提示即使共享一个 function target，也必须在语义焦点和 question intent 中保留。

### 精度门槛

- 页面有多个独立学习动作时，不得只生成一个无边界的“谈论本页主题” target；只有在一个 target 明确覆盖所有动作和信息维度时才能合并。
- 页面出现“ask questions”“compare”“give reasons”等动作词时，必须在任务清单中登记；不能只抽旁边的名词。
- `may / can / try` 表示邀请练习，不等于强制背诵，但仍要决定它是 function target、场景机会还是内容槽位，并在候选中留下可解释的覆盖关系。
- 每个 `initiate_question` intent 必须说明角色、目的、信息缺口和允许的问题维度；不能只给一个完整教材问句作为支架。
- `My learning notes`、练习题和示范对话中明确列出的可替换表达，必须在任务清单中逐项登记；例如 `Can/May I ask some questions about ...?` 与 `How can I join the ... club?` 不能只被一个“询问信息” function 隐含覆盖。
- 生成结束前逐项检查“页面任务 → 覆盖 target/场景 → Dialogue evidence → question intent”。任一任务没有去向，candidate 不得固化。

## 两个结构化审计输出

`task-audit` 必须是 JSON 对象，包含 `audit_schema_version: 1`、Unit/version/source_pages 和 `pages[]`。每页至少一个 section；section 必须记录 `locator`、`section_kind`、`learner_action`、`information_dimensions`、`output`、`modality`、`coverage_target_ids`、`coverage_scenario_ids` 和 `exclusion_reason`。所有允许页面都必须出现，所有 candidate target 都必须被覆盖；没有 target/场景覆盖的 section 必须写排除理由。

`semantic-review` 必须是 JSON 对象，包含 `review_schema_version: 1`、Unit/version、candidate 的 `candidate_content_hash`、`review_status: complete` 和 `groups[]`。每个 group 记录 `group_id`、完整的 `target_ids`、`decision` 和 `rationale`；每个 target 必须且只能出现在一个 group 中。只有所有 group 都为 `retain_distinct` 时才可固化；存在 `merge` 或 `remove` 时必须先修改 candidate 并重新生成审计。

## 逐页、逐区块的抽取清单

对调用者指定的每一页都执行一次完整扫描；不能因为某页标题熟悉、内容较短或看起来只是练习就跳过。每个二级及更深层区块至少归入以下一种类别，并在内部清单中留下定位和处理结果：

| 区块类别 | 必须检查的材料 | 典型结果 |
| --- | --- | --- |
| 主题词汇 | 图片/表格标签、加粗词、词汇表、反复聚焦的名词或动词 | `word` target，或说明它只是一次性背景词 |
| 短语与搭配 | 词组表、动词搭配、固定表达、可替换成分 | `phrase` target；不能只保留单个动词 |
| 句子与句型 | 示范对话、练习句、问题提示、`My learning notes`、句型骨架 | `sentence` target，或记录明确的支架/示例排除理由 |
| 交际任务 | ask、answer、compare、describe、give reasons、role-play 等学习者动作 | `function` target 或场景任务；必须有 Dialogue evidence |
| 信息关系 | 时间、地点、人物、数量、顺序、相同/不同、原因和结果 | 写入 semantic focus、question intent 或内容槽位；不能被宽泛主题词替代 |
| 非目标材料 | 人名、一次性事实答案、版式说明、空白、重复复习提示 | 明确记录排除原因，不静默丢弃 |

完成清单后，还要确认页级完整性：每页至少有一个 target、场景内容槽位或明确的“本页不产生 target”结论；后者必须说明该页所有区块为何只是背景、格式或覆盖检查。任何未分类区块、未解释的可复用句型或未覆盖的学习者动作，都视为抽取失败。

### 证据强度与 target 粒度

按以下证据强度决定是否建点，而不是按词出现次数决定：

1. 明确教学材料：标题下列出的词组、加粗/列出的表达、示范句、练习句、问题提示和学习笔记，优先进入候选；
2. 任务直接支撑的语义：页面问题、表格或图片共同要求孩子完成的比较、说明、提问或推断，可以泛化为 function 或 sentence target；
3. 偶然出现的正文词、一次性答案和纯版式文字，不自动成为 target。

先决定孩子要掌握的语义，再决定用 `word`、`phrase`、`sentence` 还是 `function` 表示。同一语义可以有不同合法拆分，但不得用合并来掩盖任何一条明确教学材料。
