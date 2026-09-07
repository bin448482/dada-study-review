# Fixed Turn Contract

Input must be:

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

`pending_reentry_requests` is always an array; each item contains only `event_id`, `guidance`, and nullable `unit_type`. It is neither full history nor a freely accessible archive.

The only output is `dada.entry_state_machine_result` v3. Ordinary `reply_only`:

```json
{
  "contract_name": "dada.entry_state_machine_result",
  "contract_version": 3,
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
  "contract_version": 3,
  "data": {
    "assistant_response": "这一句已经收好啦。",
    "next_operation": "record_entry_audit",
    "entry_audit": {
      "contract_name": "dada.entry_audit",
      "contract_version": 3,
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

Fields must match exactly; unknown fields are forbidden. `record_entry_audit` may contain only the example's `assistant_response`, `next_operation`, and `entry_audit`; do not add `guidance_kind` or `requested_transition` (the runtime accepts both as `null` only for compatibility with an already published legacy output). v3 phrase learning items must add `review_context` with the structure required by the Review context contract; word/sentence learning items retain the original fields. `start_entry` cannot return `record_entry_audit`, and both `guidance_kind` and `requested_transition` must be `null`.
For `collect_message`, non-audit guidance may return only `reply_only` + `guidance_kind:"entry_guidance"`; a natural completion intent may return only `reply_only` + `requested_transition`, which may be only `"finish_entry"` or `"start_review"`, never both. These are controlled LLM requests, not completed state changes; the Graph must revalidate and commit them. Each `reentry_requests` item allows only `guidance` and nullable `unit_type`; at least one of `materials` or `reentry_requests` must be non-empty. `resolved_reentry_request_ids` may reference only unresolved IDs from the input and may not contain duplicates. Authoritative program validation is in `runtime/v3_entry/contracts/validation.py`; this reference does not expand the contract.

Unrelated input such as dates, weather, or ordinary small talk is also `entry_guidance`: the reply must explicitly say “现在正在录入”, ask the child to continue sending the English to study, and explain that the unrelated question can be asked after entry ends; do not answer the unrelated question directly.

When an ordinary spelling or grammar issue requires re-entry, `record_entry_audit` is required: `materials` may be empty, but `reentry_requests` must contain at least one specific Chinese instruction. Do not substitute `reply_only`, or the re-entry task will not become a recoverable business fact.

`word` and `phrase` can independently be learning material; do not misclassify a phrase such as `go to school` as a sentence missing a subject. Create a sentence re-entry task only when the input clearly attempts a complete sentence and a sentence component is missing.
