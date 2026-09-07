# Unit candidate 合同

candidate 是严格的 `dada.dialogue_unit_definition v3` JSON 对象。v3 是当前页面语义内容形状；Unit 自身仍通过 `version` 冻结课程内容。活动 Unit 1 v1 包继续按既有合同加载；新 candidate 不得使用已移除的旧内容形状。

```json
{
  "definition_schema_version": 3,
  "unit_id": "调用者提供的稳定 ID",
  "version": 1,
  "title": "调用者提供的标题",
  "source_pages": [14],
  "targets": [],
  "question_intents": [],
  "scenarios": [],
  "completion": {}
}
```

## Target 字段

每个 target 保留 Unit 的基础字段，并增加以下 v3 字段：

| 字段 | 要求 |
| --- | --- |
| `source_refs` | 非空来源列表；每项包含 `page`、`raw_heading`、`source_locator`。标题只用于定位，不是策略或类型限制 |
| `semantic_focus` | 简短说明孩子需要理解的语义或交际能力 |
| `dialogue_evidence` | `understand_and_respond`、`semantic_expression`、`initiate_question` 或 `interaction` |
| `child_output_requirement` | 根据 evidence 使用 `respond`、`describe`、`ask` 或 `interact` |

v3 的 `phrase` target 还必须包含 `review_design`。它是受限的版本化复习设计，包含 `schema_version`、复习目的、`context_kind`、信息槽位、
题面约束、至少一个 `target_form` 的 `accepted_expressions`，以及可空的 `semantic_alternatives`。它只能描述当前页面和 Unit 主题支持的语义，
不能包含完整教材转写、档案、凭据或隐藏推理。`word`、`sentence` 和 `function` 不得携带该字段。

`source_locator` 是不超过 240 个字符的区块定位说明，例如“Look and say / question 2”或“timetable table / Monday”。它不能复制整页 Markdown、截图、完整答案或模型推理。

target 可以是 `word`、`phrase`、`sentence` 或 `function`。词、短语和句子是教学焦点，不要求孩子逐字复述；`function` 描述可观察的交际能力。`reviewable` 仍由 target 类型决定：前三类为 `true`，`function` 为 `false`。

## Question intent 字段

每个 target 至少有一个 question intent。除基础的 `intent_key`、`target_id`、`scenario_ids` 和 `purpose` 外，还必须包含 `semantic_focus`、`dialogue_evidence` 和 `prompt_constraint`。intent 的 evidence 必须与 target 一致。

`initiate_question` 的 `prompt_constraint` 必须包含角色、目的和“信息缺口”；`understand_and_respond` 必须要求“完整回答”。其他 evidence 不得把教材原句当作孩子必须背诵的答案。

## 来源与多解规则

- `source_refs.page` 必须属于 Unit 的 `source_pages`，`raw_heading` 必须是对应 Markdown 中实际存在的二级或更深标题；
- `source_locator` 只能定位区块，不能把标题当作 target 资格证明；
- target 必须由指定页面的主题、任务或语言材料直接支撑，允许语义等价的自然表达；
- 页面中每个可观察的学习者任务都必须有 target、场景内容槽位或覆盖检查项；合并任务时必须保留全部信息维度，不能只保留标题或一个宽泛主题词；
- 页面中明确列出的示范句、练习句、学习笔记表达和可替换句型也属于语言材料；可复用表达必须单独成为 target，或在覆盖审计中记录其作为支架/示例而不建点的理由，不能被宽泛 function 静默吞掉；
- 每个指定页面及其二级或更深区块都必须完成分类：词汇、短语/搭配、句子/句型、交际任务、信息关系或明确排除；不得静默跳过整页或练习区块；
- 页面的明确教学材料优先于偶然正文词，一次性事实答案、人物姓名、版式说明和空白必须有排除理由；
- `initiate_question` 必须记录角色、目的、信息缺口和问题所覆盖的信息维度；`Who / What / How long / How many` 等提示不能在语义抽取时被省略；
- 同一教材内容可以生成多套合法 candidate，不要求与固定 JSON 唯一相同；同一个 Unit 内相同学习结果不能重复建点；
- `question_intents`、`scenarios`、`completion` 和 targets 的引用必须双向一致；
- 不得写入完整页面 Markdown、截图路径、档案、凭据、provider 配置或模型隐藏推理。

## 固化边界

candidate 必须同时提供两个 sidecar：

- `unit.v<version>.candidate.task-audit.json`：逐页任务/语言材料覆盖；必须覆盖每个允许页和每个 candidate target；
- `unit.v<version>.candidate.semantic-review.json`：target 合并/拆分审计；必须绑定 candidate content hash、覆盖每个 target，且所有 group 的 decision 为 `retain_distinct`。

`validate-dialogue-unit-candidate.py` 可以单独校验 candidate；但 `finalize-dialogue-unit-candidate.py` 必须同时接收并校验两个 sidecar。只有三者都通过，才可原子写入同版本 `unit.v<version>.json`。正式文件已存在时必须失败；任何失败都不得留下部分正式包。固化后的版本不可覆盖，另一套合理设计必须创建更高版本。
