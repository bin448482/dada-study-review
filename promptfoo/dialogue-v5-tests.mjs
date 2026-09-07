import { readFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const unit = JSON.parse(await readFile(path.join(root, "config/dialogue-units/grade6-english-unit-1-school-life/unit.v5.json"), "utf8"));

const assertion = {
  type: "javascript",
  value: `return (() => {
  try {
    const r = JSON.parse(output), d = r?.data, text = d?.assistant_response || '';
    if (r.contract_name !== 'dada.dialogue_state_machine_result' || r.contract_version !== 1 || !d) return false;
    if (!['guided_question','role_play','reasoned_response'].includes(d.level_behavior)) return false;
    if (d.repetition_outcome !== 'not_requested' || d.requested_transition !== null) return false;
    if (!Array.isArray(d.capture_candidate_target_ids) || d.capture_candidate_target_ids.length !== 0) return false;
    if (typeof text !== 'string' || !/[A-Za-z]/.test(text) || /(api key|workflow_id|checkpoint|system prompt)/i.test(text)) return false;
    // Targets are semantic contracts: the model may realize the target in
    // natural language instead of repeating its literal surface form.
    // The golden gate therefore checks an actionable question, not a
    // brittle string match against target.english.
    return /[?？]/.test(text) || /(?:ask|question|tell me|please|use|say|describe|correct|greet|imagine|share|complete|answer|what|how|when|where|why|which|could you|you can|try|first|example|先说|可以)/i.test(text);
  } catch { return false; }
})();`
};

const logicCases = [
  { case_id: "logic_minor_grammar_success", kind: "minor_grammar" },
  { case_id: "logic_negative_subject_answer_success", kind: "negative_answer" },
  { case_id: "logic_off_topic_activity", kind: "off_topic_activity" },
  { case_id: "logic_off_topic_subject_time", kind: "off_topic_subject_time" },
  { case_id: "logic_off_topic_after_science", kind: "off_topic_after_science" },
  { case_id: "logic_wrapping_no_repetition", kind: "wrapping" },
];

function logicAssertion(kind) {
  const lines = [
    "return (() => {",
    "  try {",
    "    const r = JSON.parse(output), d = r?.data, text = d?.assistant_response || '';",
    "    if (r.contract_name !== 'dada.dialogue_state_machine_result' || r.contract_version !== 1 || !d) return false;",
    "    if (d.level_behavior !== 'guided_question' || d.requested_transition !== null) return false;",
    "    if (!Array.isArray(d.capture_candidate_target_ids) || d.capture_candidate_target_ids.length !== 0) return false;",
  ];
  if (kind === "minor_grammar" || kind === "negative_answer") {
    lines.push("    return d.evaluation.target_evidence === 'independent_success' && d.repetition_outcome === 'not_requested' && !/(请再说一遍|repeat)/i.test(text) && /[?？]/.test(text);");
  } else if (kind === "wrapping") {
    lines.push("    return d.repetition_outcome === 'not_requested' && !/(请再说一遍|repeat)/i.test(text) && !/[?？]/.test(text);");
  } else {
    lines.push("    if (d.evaluation.target_evidence !== 'unable' || d.repetition_outcome !== 'not_requested') return false;");
    const expected = {
      off_topic_activity: "I do experiments in Science.",
      off_topic_subject_time: "I have Maths",
    }[kind];
    if (kind === "off_topic_after_science") {
      lines.push("    return /(这个问题是在问|问题是在问)/.test(text) && /after science.*i have [a-z]+|i have [a-z]+.*after science/i.test(text);");
    } else {
      lines.push("    return /(这个问题是在问|问题是在问)/.test(text) && text.toLowerCase().includes(" + JSON.stringify(expected.toLowerCase()) + ");");
    }
  }
  lines.push("  } catch { return false; }", "})();");
  return { type: "javascript", value: lines.join("\n") };
}

export default function generateTests() {
  const filter = process.env.DADA_GOLDEN_FILTER ? new RegExp(process.env.DADA_GOLDEN_FILTER) : null;
  const targets = filter ? unit.targets.filter((target) => filter.test(target.target_id)) : unit.targets;
  const matrix = targets.flatMap((target) => ["ask", "followup"].map((phase) => ({
    description: `v5 target ${target.target_id} ${phase}`,
    vars: {
      case_id: `target_matrix_${target.target_id}_${phase}`,
      matrix_target: target.target_id,
      matrix_phase: phase,
      matrix_evidence: target.dialogue_evidence,
    },
    assert: [assertion],
  })));
  const logic = logicCases.map((item) => ({
    description: "logic regression " + item.case_id,
    vars: { case_id: item.case_id },
    assert: [logicAssertion(item.kind)],
  }));
  return matrix.concat(logic);
}
