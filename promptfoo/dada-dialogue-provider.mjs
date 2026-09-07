import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import path from "node:path";

const directory = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(directory, "..");
const unitPath = path.join(projectRoot, "config", "dialogue-units", "grade6-english-unit-1-school-life", "unit.v5.json");
const skillPath = path.join(projectRoot, "skill", "dada-dialogue-state-machine", "SKILL.md");
const contractPath = path.join(projectRoot, "skill", "dada-dialogue-state-machine", "references", "dialogue-turn-contract.md");
const REQUEST_TIMEOUT_MS = 60_000;
const responseSchema = { type: "object", additionalProperties: false, required: ["contract_name", "contract_version", "data"], properties: { contract_name: { const: "dada.dialogue_state_machine_result" }, contract_version: { const: 1 }, data: { type: "object" } } };
const unit = JSON.parse(await readFile(unitPath, "utf8"));
const targetById = new Map(unit.targets.map((target) => [target.target_id, target]));
const intentByTarget = new Map(unit.question_intents.map((intent) => [intent.target_id, intent]));
const scenarioById = new Map(unit.scenarios.map((scenario) => [scenario.scenario_id, scenario]));

function requiredEnvironment(name) { const value = process.env[name]; if (!value) throw new Error(`${name} must be set for a real Promptfoo evaluation`); return value; }
function outputText(response, apiStyle) {
  if (apiStyle === "chat-completions") {
    const content = response?.choices?.[0]?.message?.content;
    if (typeof content === "string") return content;
    if (Array.isArray(content)) return content.map((part) => typeof part === "string" ? part : part?.text ?? "").join("") || null;
    return null;
  }
  if (typeof response?.output_text === "string") return response.output_text;
  if (typeof response?.output === "string") return response.output;
  const texts = [];
  for (const item of response?.output ?? []) {
    for (const content of item?.content ?? []) {
      if ((content?.type === "output_text" || content?.type === "text") && typeof content.text === "string") texts.push(content.text);
    }
  }
  if (texts.length) return texts.join("");
  // A few OpenAI-compatible gateways wrap Responses payloads one level
  // deeper or expose content as a plain string. Walk only text-bearing keys
  // so a refusal/reasoning object is not mistaken for the model answer.
  const walk = (value, key = "") => {
    if (typeof value === "string" && ["output_text", "text", "content"].includes(key)) return value;
    if (!value || typeof value !== "object") return null;
    if (Array.isArray(value)) {
      for (const item of value) { const found = walk(item, key); if (found) return found; }
      return null;
    }
    for (const [childKey, childValue] of Object.entries(value)) {
      const found = walk(childValue, childKey); if (found) return found;
    }
    return null;
  };
  return walk(response);
}

function targetMatrixTurn(caseId) {
  const match = /^target_matrix_(.+)_(ask|followup)$/.exec(caseId);
  if (!match) return null;
  const [, targetId, phase] = match;
  const target = targetById.get(targetId);
  if (!target) return null;
  const scenarioId = target.scenario_ids[0];
  const scenario = scenarioById.get(scenarioId);
  const intent = intentByTarget.get(targetId);
  const question = `Tell me about ${target.english}.`;
  const childAnswer = target.dialogue_evidence === "initiate_question" ? "Could you tell me more about it?" : `I can talk about ${target.english}.`;
  const ready = "I am ready to talk about this school topic.";
  const history = phase === "ask"
    ? [{ event_id: "matrix-history-1", event_type: "assistant_response", text: "Let's talk about this school topic.", created_at: "2026-09-01T00:00:00Z" }, { event_id: "matrix-history-2", event_type: "child_message", text: ready, created_at: "2026-09-01T00:01:00Z" }]
    : [{ event_id: "matrix-history-1", event_type: "assistant_response", text: question, created_at: "2026-09-01T00:00:00Z" }, { event_id: "matrix-history-2", event_type: "child_message", text: childAnswer, created_at: "2026-09-01T00:01:00Z" }];
  return {
    task_contract_name: "dada.dialogue_state_machine_turn", task_contract_version: 1, mode: "continue_dialogue", active_mode: "dialogue",
    graph_node: "dialogue_continuing", workflow_id: `synthetic-${caseId}`,
    unit: { unit_id: unit.unit_id, version: unit.version, title: unit.title, targets: [target] }, scenario,
    focus_target: target, difficulty_level: 2, pending_repetition_target_ids: [], subphase: "normal",
    mastery_snapshot: { target_progress: {}, scenario_progress: {}, unit_course_passed: false },
    current_child_message: { event_id: `event-${caseId}`, text: phase === "ask" ? ready : childAnswer }, history, history_truncated: false,
    active_step: { step_id: "step-1", scenario_id: scenarioId, target_id: targetId, question_intent_key: intent.intent_key, entry_difficulty: 2, question_intent: intent, content_slots: [{ slot_id: "target_expression", target_id: targetId, purpose: "target" }, { slot_id: "supporting_detail", target_id: "supporting_detail", purpose: "supporting detail" }] }, max_capture_candidates: 3,
  };
}

const logicSpecs = {
  logic_minor_grammar_success: { targetId: "subject_art", question: "What day do you have Art in your timetable?", answer: "I have Art at Friday.", subphase: "normal" },
  logic_negative_subject_answer_success: { targetId: "subject_history", question: "When do you have History in your timetable?", answer: "I don't have History.", subphase: "normal" },
  logic_off_topic_activity: { targetId: "subject_science", question: "What do you do in Science?", answer: "I have Science on Tuesday and Wednesday.", subphase: "normal" },
  logic_off_topic_subject_time: { targetId: "subject_maths", question: "When do you have Maths in your timetable?", answer: "Math is my subject.", subphase: "normal" },
  logic_off_topic_after_science: { targetId: "subject_science", question: "What subject do you have after Science?", answer: "I have Science on Tuesday and Wednesday.", subphase: "normal" },
  logic_wrapping_no_repetition: { targetId: "subject_music", question: "What do you do in Music?", answer: "I sing a song in Music.", subphase: "wrapping_up" },
};

function logicTurn(caseId) {
  const spec = logicSpecs[caseId];
  if (!spec) return null;
  const target = targetById.get(spec.targetId);
  const intent = intentByTarget.get(spec.targetId);
  const scenarioId = "school-subjects";
  const scenario = scenarioById.get(scenarioId);
  return {
    task_contract_name: "dada.dialogue_state_machine_turn", task_contract_version: 1, mode: "continue_dialogue", active_mode: "dialogue",
    graph_node: "dialogue_continuing", workflow_id: "synthetic-" + caseId,
    unit: { unit_id: unit.unit_id, version: unit.version, title: unit.title, targets: spec.targetId === "subject_science" && caseId === "logic_off_topic_after_science" ? [target, targetById.get("subject_geography")] : [target] }, scenario,
    focus_target: target, difficulty_level: 2, pending_repetition_target_ids: [], subphase: spec.subphase,
    mastery_snapshot: { target_progress: {}, scenario_progress: {}, unit_course_passed: false },
    current_child_message: { event_id: "event-" + caseId, text: spec.answer },
    history: [
      { event_id: "history-question-" + caseId, event_type: "assistant_response", text: spec.question, created_at: "2026-09-01T00:00:00Z" },
      { event_id: "history-answer-" + caseId, event_type: "child_message", text: spec.answer, created_at: "2026-09-01T00:01:00Z" },
    ],
    history_truncated: false,
    active_step: { step_id: "step-1", scenario_id: scenarioId, target_id: spec.targetId, question_intent_key: intent.intent_key, entry_difficulty: 2, question_intent: intent, content_slots: [{ slot_id: "target_expression", target_id: spec.targetId, purpose: "target" }, { slot_id: "supporting_detail", target_id: "supporting_detail", purpose: "supporting detail" }] },
    max_capture_candidates: 3,
  };
}

export default class DadaDialogueStateMachineProvider {
  constructor(options = {}) { this.providerId = options.id ?? "dada-dialogue-state-machine-v5-live"; }
  id() { return this.providerId; }
  async callApi(prompt) {
    const [skill, contract] = await Promise.all([readFile(skillPath, "utf8"), readFile(contractPath, "utf8")]);
    const endpoint = requiredEnvironment("DADA_EVAL_BASE_URL");
    const apiKey = requiredEnvironment("DADA_EVAL_API_KEY");
    const model = requiredEnvironment("DADA_EVAL_MODEL");
    const apiStyle = process.env.DADA_EVAL_API_STYLE ?? "responses";
    if (!endpoint.startsWith("https://") || !["responses", "chat-completions"].includes(apiStyle)) throw new Error("Promptfoo provider configuration is invalid");
    let requestPrompt = prompt;
    try {
      const parsed = JSON.parse(prompt);
      if (typeof parsed?.case_id === "string") {
        const turn = targetMatrixTurn(parsed.case_id) || logicTurn(parsed.case_id);
        if (turn) requestPrompt = JSON.stringify(turn);
      }
    } catch { /* fixed raw turn remains supported */ }
    const system = `${skill}\n\n${contract}\n\nThe supplied Unit is the current version 5 package. Keep the frozen target and question intent; do not invent another target.`;
    const body = apiStyle === "responses"
      ? { model, input: [{ role: "system", content: [{ type: "input_text", text: system }] }, { role: "user", content: [{ type: "input_text", text: requestPrompt }] }], text: { format: { type: "json_schema", name: "dada_dialogue_state_machine_result", strict: true, schema: responseSchema } }, tools: [] }
      : { model, messages: [{ role: "system", content: system }, { role: "user", content: requestPrompt }], response_format: { type: "json_object" }, temperature: 0 };
    const response = await fetch(endpoint, { method: "POST", headers: { Authorization: `Bearer ${apiKey}`, "Content-Type": "application/json" }, body: JSON.stringify(body), signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS) });
    const payload = await response.json();
    if (!response.ok) return { error: `provider request failed with HTTP ${response.status}` };
    const output = outputText(payload, apiStyle);
    return output ? { output } : { error: "provider response did not contain output_text" };
  }
}
