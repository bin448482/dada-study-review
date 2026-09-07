# Fixed Turn Contract

Input must be:

```json
{
  "task_contract_name": "dada.review_state_machine_turn",
  "task_contract_version": 5,
  "mode": "start_review | answer_question | next_question",
  "active_mode": "review",
  "graph_node": "review_starting | review_process_answer",
  "workflow_id": "...",
  "learning_item": {
    "learning_item_id": "...",
    "reference_text": "...",
    "meaning_zh": "...",
    "revision": 1,
    "unit_type": "word | phrase | sentence",
    "review_context": null
  },
  "selected_question_mode": "...",
  "current_child_message": {"event_id": "...", "text": "..."},
  "locked_question": {
    "question_sequence": 1,
    "question_mode": "...",
    "question_json": {},
    "item_revision": 1
  },
  "history": [],
  "history_truncated": false
}
```

`start_review` and `next_question` have no `locked_question`; the latter means the program advanced by assessment to the next item in the frozen queue and still uses the child event that triggered the current review as the audit source. `answer_question` always has the current locked question. `history` contains only committed events from the same workflow and includes event IDs; it is not a freely accessible full archive.

`selected_question_mode` is randomly selected by the program from the fixed pool for `learning_item.unit_type`: words use `spelling`, `zh_to_en`, or `en_to_zh`; phrases use `zh_to_en`; sentences use `sentence_recall`, `zh_to_en`, or `en_to_zh`. The program samples among candidates used least often in this workflow and avoids immediate repetition when alternatives exist; it does not generate prompts or scores. `ask_question.question_mode` must exactly equal this field.

When `locked_question.question_mode` is word `spelling`, a complete correct word and letter-by-letter spelling separated by spaces, commas, hyphens, or separate messages are all assessable answers; a correct answer must return `complete_assessment`. The prompt may invite spelling, but letter-by-letter input must not be the only accepted format. Use `continue_locked_question` with minimal guidance only for an incomplete, indeterminate, or misspelled answer.

The only output is `dada.review_state_machine_result` v4. Initial question or follow-up:

```json
{
  "contract_name": "dada.review_state_machine_result",
  "contract_version": 4,
  "data": {
    "next_operation": "ask_question",
    "question_mode": "en_to_zh",
    "question_json": {
      "prompt": "I go to school.",
      "instruction": "请说说这句话是什么意思。"
    }
  }
}
```

Completed assessment:

```json
{
  "contract_name": "dada.review_state_machine_result",
  "contract_version": 4,
  "data": {
    "next_operation": "complete_assessment",
    "assistant_response": "答得很认真，我们下次再巩固一下。",
    "assessment": {
      "contract_name": "dada.review_assessment",
      "contract_version": 1,
      "data": {
        "question_sequence": 1,
        "accuracy": 0.8,
        "explanation": "意思基本正确。",
        "incorrect_words": [],
        "feedback_basis": "根据当前锁题和孩子本次回答判断。"
      }
    }
  }
}
```

Fields must match exactly; unknown fields are forbidden. `ask_question` may contain only `next_operation`, `question_mode`, and `question_json`; `question_json` may contain only a non-empty `prompt`, optional non-empty `instruction`, and optional non-empty `speech_text`. The Graph places `prompt` unchanged at the start of the visible message and then appends `instruction`; therefore `ask_question` must not include `assistant_response`. Translation questions must not include `speech_text`; word `spelling` and sentence `sentence_recall` must include it, for speech synthesis only and never for display to the child. `complete_assessment` cannot include `question_mode` or `question_json`. For unrelated input or an unanswered question, keep the same locked question:

```json
{
  "contract_name": "dada.review_state_machine_result",
  "contract_version": 4,
  "data": {
    "next_operation": "continue_locked_question",
    "assistant_response": "我们先完成这一题吧。"
  }
}
```

`continue_locked_question` may contain only these two fields. The Graph persists the guidance and resends the committed locked question; it does not create an assessment, schedule, new question, or queue change. An active turn may also request a transition:

For unrelated input such as dates, weather, or ordinary small talk, the guidance must explicitly say “现在正在复习”, ask the child to answer the current question first, and explain that other questions can be asked after review ends; do not answer the unrelated question itself.

```json
{
  "contract_name": "dada.review_state_machine_result",
  "contract_version": 4,
  "data": {
    "next_operation": "request_transition",
    "requested_transition": "stop_review | start_entry",
    "assistant_response": "好的，我先帮你停在这里。"
  }
}
```

`request_transition` cannot include a prompt or assessment; it is only a controlled LLM intent result, and the Graph must validate the current state before closing or handing off. Authoritative program validation is in `runtime/v3_review/contracts/validation.py`; this reference does not expand the contract.

`learning_item.review_context` is an optional bounded object for phrases: a new phrase must provide `schema_version`, `purpose_zh`, `context_kind`, `information_slots`, `prompt_constraint_zh`, non-empty `accepted_expressions`, and nullable `semantic_alternatives`. Target-form examples are only for generating and judging contextualized `zh_to_en`; they must not leak the answer directly in the prompt. A historical phrase without backfill may temporarily be `null` for legacy prompt compatibility; a new phrase must not be `null`. A phrase `zh_to_en` prompt must provide enough concrete facts to fill the information slots; the child passes by using a registered target form with consistent slot values and need not repeat the template verbatim. If the child gives only a semantic alternative, explain that this question still practices the target phrase; an answer that conflicts with the prompt facts or omits a key slot cannot complete assessment.
