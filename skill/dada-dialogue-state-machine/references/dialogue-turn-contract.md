# Fixed Dialogue Turn Contract

The Chinese fragments in the examples below are intentional child-visible output requirements, not documentation prose. Preserve exact behavior such as `角色、目的和信息缺口`, `缺少 `in the club``, `这个问题是在问`, `答非所问`, `必须评估为 `independent_success``, and `修改原因`.

Input is `dada.dialogue_state_machine_turn` v1. It contains the current Unit/scenario/target, L2--L4 `difficulty_level`, current round plan/step, optional L4 `content_slots[]`, used question intents, optional `pending_repetition_target_ids`, `subphase`, bounded history, and one current child message. The targets in the input are the only targets that may be referenced.

The only output is:

```json
{
  "contract_name": "dada.dialogue_state_machine_result",
  "contract_version": 1,
  "data": {
    "assistant_response": "What subjects do you have at school?",
    "speech_text": "What subjects do you have at school?",
    "level_behavior": "guided_question",
    "evaluation": {
      "target_evidence": "exposed",
      "scenario_achievement": "basic",
      "grammar_observations": [],
      "content_slots_covered": []
    },
    "repetition_outcome": "not_requested",
    "capture_candidate_target_ids": [],
    "difficulty_suggestion": "stay",
    "requested_transition": null
  }
}
```

`speech_text` is optional; when present it contains only the English to read with TTS and no Chinese explanation. When absent, the program uses `assistant_response`. `target_evidence` may be only `exposed`, `supported_success`, `independent_success`, or `unable`; `scenario_achievement` may be only `none`, `basic`, `role_play`, or `reasoned_response`. Each grammar observation must exactly be `{"category":"subject_verb_agreement|verb_tense|article|preposition|word_order","correction":"minimum correct form"}`.

When this turn must correct a grammar, collocation, or expression error that affects the meaning or target expression, `assistant_response` must first briefly explain in Chinese what was wrong and why, then give the correct English and explicitly ask for repetition; do not only say “可以说……” or repeat the correct sentence. For example, if the child says `every day in school day`, explain that the intended meaning is “每个上学日”, use `every school day`, and ask the child to repeat. `speech_text` still contains only the English to read aloud. A minor grammar, spelling, case, or preposition error that does not affect the current meaning does not require downgrading or repetition; keep `independent_success` and continue the dialogue.

`capture_candidate_target_ids` is an array of zero or more target IDs. When pending targets exist, return the exact reviewable target set in `pending_repetition_target_ids` and `repetition_outcome:"succeeded"` only after the child successfully repeats all pending content; then `target_evidence:"supported_success"` is required, and the repetition must not be labeled `independent_success`. When L4 is partial and no pending set exists, a candidate set within the textbook scope may be returned as the pending repetition set, but it must use `supported_success`/`unable` and `repetition_outcome:"not_requested"`; these candidates are not yet archived. With no pending set and no candidates, outcome must be `not_requested`; with pending targets, outcome must be `succeeded` or `not_succeeded`.
`candidate` must be an empty array and outcome must be `not_requested`.

Successful repetition must satisfy semantic completeness, not merely correct sentence form: the child must cover all key information needed to answer the current question in the previous English model. If the place, time, activity object, or other required content is missing, return `repetition_outcome:"not_succeeded"` even when grammar is broadly correct, identify the missing part in Chinese, give the complete English again, and ask for repetition; do not return “说得很好”, clear pending, or immediately ask the same unfinished question as a new independent question. For example, if the model is `I would like to do an experiment in the club next.` and the child says `I would like to do an experiment next.`, identify the missing `in the club`.

`difficulty_suggestion` may be only `stay`, `raise`, or `lower`. `requested_transition` may be only `null` or `stop_dialogue`; it does not directly close the workflow.

The target word, phrase, or pattern is the teaching focus of this turn, not an answer the child must repeat verbatim. A complete, semantically correct sentence answering the current question without a sentence scaffold must be assessed as `independent_success`; alternative wording is allowed. For example, answering `If you could do experiments in the club, what would you make?` with `I would make a robot in the science club.` completes the target even without repeating `do experiments`; answering `What is your school day like?` with complete sentences about school, classes, or dismissal also completes the target. L2/L3 require at least a complete natural sentence; an isolated `Yes`, `No`, or target word is not complete. L4 must also cover all `active_step.content_slots` with multiple related clauses or complete sentences. If meaning is broadly correct but a key fact or expression remains clearly wrong, such as `play a project`, assess `supported_success`, explain the error in Chinese, give and read the correct English, and request repetition; the target remains incomplete, the intent is `retryable`, and the next round continues the same question.

Judge whether the child answered the meaning of the current question before judging grammatical perfection. A complete, semantically clear answer with minor grammar, spelling, case, or preposition errors that do not affect meaning must be `independent_success`; optional grammar observations must not downgrade it to `supported_success`, require repetition, or block the next same-scenario question. Only an error that changes/obscures key meaning or an answer to another question prevents independent completion.

For factual questions such as whether a class exists or when it appears on a timetable, an explicit negative answer is also semantically complete. For example, `When do you have History in your timetable?` → `I don't have History.`, or `I don't have History, but I have Geography.`, must be assessed as `independent_success` and may advance; do not require a nonexistent time, assess `unable`, or treat it as off-topic.

For an off-topic answer, stay on the current question: do not first say “回答正确” or advance to another target/question. Off-topic includes answering another fact, repeating the topic word without answering, or giving a complete sentence that lacks the information required by the current question. For example, the question `What do you do in Science?` receives `I have Science on Tuesday and Wednesday.`, or `When do you have Maths in your timetable?` receives `Math is my subject.` Return `target_evidence:"unable"` and `repetition_outcome:"not_requested"`; use Chinese in `assistant_response` to explain “这个问题是在问……” and its meaning, give a short English sentence that truly answers the current question, and ask the child to repeat. Keep the intent `retryable`; do not misclassify an off-topic answer as ordinary `exposed` or grammar correction.

Special case: `I do the math` in response to `What project do you do in the club?` is an understandable complete English expression and must not be assessed as `unable`. It expresses a maths activity but does not naturally and explicitly cover `project`, so assess `supported_success` and use this feedback: “我明白你的意思，你是在说数学项目。为了完整回答这个问题，可以说：” then give and read `I do a Maths project in the club.`, and finish with “请再说一遍。” Keep the target incomplete and the intent `retryable`.

`level_behavior` is a verifiable echo of program-selected difficulty and must exactly match input `difficulty_level`: 2=`guided_question`, 3=`role_play`, 4=`reasoned_response`. Each new Dialogue round starts at L2; L0/L1 are not current Dialogue difficulty levels. `content_slots_covered` must be the set of slot IDs in `active_step.content_slots` covered by the child's answer; L4 independent success requires every slot.

`assistant_response` must also maintain a continuous dialogue: acknowledge the current child message, advance the current round-plan step's intent, ask at most one main question in a turn, and do not repeat a completed intent. Do not chain two question-mark questions in one response. The communication-function target of politely requesting information may advance with an instruction such as “请提出一个礼貌问题”, but the target must not change. The current `focus_target` must be explicitly elicited in the main question: use the target word/phrase/pattern directly or ask a fact directly related to it; do not substitute a neighboring topic. If the current intent is `retryable`, repeat the original question across rounds until the child answers independently; successful repetition scaffolding only creates or reuses review points and completes the current step for the program to advance to the next frozen step, but does not mark the target complete. This applies to every Unit 1 target and scenario: clubs may advance through activities/time/place/how to join, school schedules through classes/class content/break/lunch, safety etiquette through actions/reasons, ideal schools through features/reasons, and school comparison through similarities/differences.

The generated v2 Unit supplies `semantic_focus`, `dialogue_evidence`, and `prompt_constraint` in `active_step.question_intent`. These are fixed teaching constraints, not optional model suggestions. For `understand_and_respond`, require a complete answer using the textbook question or a semantic equivalent; for `semantic_expression`, require natural expression around the target meaning; for `interaction`, complete the specified action in role interaction. For `initiate_question`, provide role, purpose, and information gap, for example “你想加入社团，但不知道第一步该做什么。请向社团老师问一个问题。”, so the child organizes a natural question. Do not show the complete textbook question first or treat its repetition as independent completion; a semantically correct natural reformulation is independently successful. Only `initiate_question` requires the child to ask; other evidence types are judged by a complete, semantically correct answer or description.
When the child says “我不知道怎么回答/怎么说”, first explain the latest question in Chinese, then give one short English template that semantically answers it, read the English aloud, and ask for repetition. For example, `What do you learn in History?` → “这个问题是在问你在历史课学什么。你可以说：`I learn about the past in History.`”, then read it aloud and ask the child to repeat. Do not only repeat the question, give only `History`, or switch to another subject/question; output remains `target_evidence:"unable"`, `repetition_outcome:"not_requested"`, and the intent is `retryable` for independent judgment in the next turn/round. For partial L4 completion, likewise identify missing slots and request the complete answer; only after success may multiple corresponding review points be created.
