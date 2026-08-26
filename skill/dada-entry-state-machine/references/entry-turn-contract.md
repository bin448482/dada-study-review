# 固定回合合同

输入必须是：

```json
{
  "task_contract_name": "dada.entry_state_machine_turn",
  "task_contract_version": 1,
  "mode": "start_entry | collect_message",
  "active_mode": "entry",
  "graph_node": "entry_process_turn",
  "current_message": {"event_id": "...", "text": "..."},
  "pending_reentry_requests": [{"event_id": "...", "guidance": "...", "unit_type": "sentence"}]
}
```

`pending_reentry_requests` 始终是数组；每项仅含 `event_id`、`guidance` 与可空 `unit_type`。它不是完整历史，也不是可自由访问的档案。

唯一输出为 `dada.entry_state_machine_result` v2。普通 `reply_only`：

```json
{
  "contract_name": "dada.entry_state_machine_result",
  "contract_version": 2,
  "data": {
    "assistant_response": "现在开始录入啦，请一行一条发给我。",
    "next_operation": "reply_only",
    "guidance_kind": null,
    "requested_transition": null
  }
}
```

`record_entry_audit`：

```json
{
  "contract_name": "dada.entry_state_machine_result",
  "contract_version": 2,
  "data": {
    "assistant_response": "这一句已经收好啦。",
    "next_operation": "record_entry_audit",
    "entry_audit": {
      "contract_name": "dada.entry_audit",
      "contract_version": 2,
      "data": {
        "materials": [{
          "title": "学校句子",
          "language": "en",
          "unit_type": "sentence",
          "reference_text": "I go to school.",
          "needs_parent_review": false,
          "audit_result": {
            "contract_name": "dada.material_audit_result",
            "contract_version": 1,
            "data": {
              "audit_confidence": "high",
              "needs_parent_review": false,
              "audit_changes": [],
              "semantic_duplicate_review": {
                "source": "llm_semantic_review",
                "decision": "no_semantic_duplicate",
                "reason": "含义独立"
              }
            }
          },
          "items": [{"reference_text": "I go to school.", "meaning_zh": "我去上学。"}]
        }],
        "reentry_requests": [],
        "resolved_reentry_request_ids": []
      }
    }
  }
}
```

字段必须精确匹配；禁止 unknown fields。`record_entry_audit` 只能包含示例中的 `assistant_response`、`next_operation` 和 `entry_audit`，不得附带 `guidance_kind` 或 `requested_transition`（运行时仅为已发布旧输出兼容两者都为 `null` 的情况）。`start_entry` 不能返回 `record_entry_audit`，且 `guidance_kind` 与 `requested_transition` 必须都是 `null`。
`collect_message` 的非审核引导只能返回 `reply_only` + `guidance_kind:"entry_guidance"`；自然完成意图只能返回 `reply_only` +
`requested_transition` 仅可为 `"finish_entry"` 或 `"start_review"`，二者不能同时出现。它们都是 LLM 的受控请求，不是已发生的状态改变；Graph 必须二次验证并提交。`reentry_requests` 中每项只允许 `guidance` 和可空的 `unit_type`；
`materials` 或 `reentry_requests` 至少一个非空。`resolved_reentry_request_ids` 只能引用输入中尚未解决的 ID，且不得重复。权威程序校验位于
`runtime/v3_entry/contracts/validation.py`；本参考不扩大它的合同。

日期、天气或普通闲聊等无关输入也属于 `entry_guidance`：回复必须明确“现在正在录入”，请孩子先继续发要学习的英文，并说明无关问题可在录入结束后再问；不得直接回答无关问题。

普通拼写或语法问题需要孩子重录时，必须使用 `record_entry_audit`：`materials` 可以为空，但
`reentry_requests` 至少含一条具体中文引导。不可用 `reply_only` 代替，否则补录任务不会成为可恢复的业务事实。

`word` 和 `phrase` 可独立成为学习材料；不能把 `go to school` 这类短语误判为缺主语的句子。只有输入明确在尝试完整句且
句子成分缺失时，才创建句子补录任务。
