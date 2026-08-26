# 固定回合合同

输入必须是：

```json
{
  "task_contract_name": "dada.review_state_machine_turn",
  "task_contract_version": 3,
  "mode": "start_review | answer_question | next_question",
  "active_mode": "review",
  "graph_node": "review_starting | review_process_answer",
  "workflow_id": "...",
  "learning_item": {
    "learning_item_id": "...",
    "reference_text": "...",
    "meaning_zh": "...",
    "revision": 1,
    "unit_type": "word | phrase | sentence"
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

`start_review` 与 `next_question` 没有 `locked_question`；后者代表程序已按 assessment 推进到本次冻结队列的下一项，
仍使用触发当前复习的孩子事件作为审计来源。`answer_question` 必有当前锁题。`history` 仅来自同一 workflow 的已提交事件，并带 event ID；它不是可访问的完整档案。

`selected_question_mode` 由程序按 `learning_item.unit_type` 的固定允许池随机选择：单词为 `word_mask`、`spelling`、`zh_to_en`、`en_to_zh`；短语为
`mask`、`zh_to_en`、`en_to_zh`；句子为 `mask`、`sentence_recall`、`zh_to_en`、`en_to_zh`。程序在本 workflow 已使用最少的候选中随机抽取，
并在有其他候选时避免紧邻重复；它不生成题面或评分。`ask_question.question_mode` 必须与该字段完全相等。

当 `locked_question.question_mode` 为单词 `spelling` 时，完整且正确的单词与空格、逗号、连字符或逐条消息分隔的逐字母拼写，都是可评估作答；正确时必须返回 `complete_assessment`。题面可邀请孩子拼写，但不得将逐字母输入作为唯一接受格式。未完成、无法判断或拼写错误时，才可用 `continue_locked_question` 保留锁题并作最小引导。

唯一输出为 `dada.review_state_machine_result` v3。首次或追问：

```json
{
  "contract_name": "dada.review_state_machine_result",
  "contract_version": 3,
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

完成 assessment：

```json
{
  "contract_name": "dada.review_state_machine_result",
  "contract_version": 3,
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

字段必须精确匹配；禁止 unknown fields。`ask_question` 只能包含 `next_operation`、`question_mode` 和
`question_json`；其中 question_json 只能有非空 `prompt` 与可选非空 `instruction`。Graph 将 prompt 原样放在可见消息
最前，再附 instruction；因此 ask_question 不得带 `assistant_response`。`complete_assessment` 不能带 `question_mode` 或
`question_json`。无关输入或尚未作答时，保持同一锁题：

```json
{
  "contract_name": "dada.review_state_machine_result",
  "contract_version": 3,
  "data": {
    "next_operation": "continue_locked_question",
    "assistant_response": "我们先完成这一题吧。"
  }
}
```

`continue_locked_question` 只能含这两个字段。Graph 将持久化该引导并重发已提交的锁题；它不创建 assessment、排程、换题或修改队列。活动回合也可请求转换：

对日期、天气或普通闲聊等无关输入，引导必须明确“现在正在复习”，请孩子先回答当前题，并说明其他问题可在复习结束后再问；不得回答该无关问题本身。

```json
{
  "contract_name": "dada.review_state_machine_result",
  "contract_version": 3,
  "data": {
    "next_operation": "request_transition",
    "requested_transition": "stop_review | start_entry",
    "assistant_response": "好的，我先帮你停在这里。"
  }
}
```

`request_transition` 不能带题面或 assessment；它只是 LLM 的受控意图结果，Graph 必须验证当前状态后才可关闭或交接。权威程序校验位于 `runtime/v3_review/contracts/validation.py`；本参考不扩大合同。
