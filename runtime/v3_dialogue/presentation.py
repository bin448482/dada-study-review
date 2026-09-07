"""Program-owned child-visible Dialogue status and progress text."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DialoguePresentation:
    """Committed program text and the complete child-visible reply."""

    state_text: str | None
    progress_text: str | None
    reply_text: str
    speech_text: str


def compose_dialogue_presentation(
    model_reply: str,
    *,
    mode: str,
    current_difficulty: int,
    next_difficulty: int,
    captured_before: int,
    captured_after: int,
    closing: bool,
    round_completion_reason: str | None = None,
    unit_completed_targets: int = 0,
    unit_total_targets: int = 0,
    round_plan: dict[str, Any] | None = None,
    unit: Any | None = None,
    speech_text: str | None = None,
) -> DialoguePresentation:
    """Add only program-owned state/progress facts to a committed reply.

    Normal turns are quiet; state appears at start, captures,
    recovery/error paths, and close boundaries.
    """

    _require_difficulty(current_difficulty)
    _require_difficulty(next_difficulty)
    if not isinstance(model_reply, str) or not model_reply:
        raise ValueError("model reply must be non-empty")
    if type(captured_before) is not int or type(captured_after) is not int or captured_before < 0 or captured_after < captured_before:
        raise ValueError("captured counts are invalid")
    if type(unit_completed_targets) is not int or type(unit_total_targets) is not int or unit_completed_targets < 0 or unit_total_targets < 0 or unit_completed_targets > unit_total_targets:
        raise ValueError("Unit target progress is invalid")
    if speech_text is not None and (not isinstance(speech_text, str) or not speech_text.strip()):
        raise ValueError("speech text is invalid")

    state_text: str | None = None
    progress_text: str | None = None
    if round_completion_reason == "completed":
        state_text = "【本轮对话已完成】完成得很棒！这一轮对话内容已经完成。需要继续时，请再次说“开始对话”。"
        progress_text = f"本轮已完成 {captured_after} 个复习点；已创建专用复习批次。" if captured_after > 0 else "本轮没有生成复习点。"
    elif round_completion_reason == "closed_without_completion":
        state_text = "【本轮对话已结束】这一轮先到这里，未完成的内容会保留到下一轮。需要继续时，请再次说“开始对话”。"
        progress_text = f"本轮已完成 {captured_after} 个复习点；已创建专用复习批次。" if captured_after > 0 else "本轮没有生成复习点。"
    elif closing:
        if captured_after > 0:
            state_text = "【英语对话已结束】本轮已创建专用复习批次。之后说“开始复习”即可开始这批复习。"
            progress_text = f"本轮复习点：{captured_after} 个。"
        else:
            state_text = "【英语对话已结束】本轮没有生成复习点。需要继续时，请再次说“开始对话”。"
            progress_text = "本轮复习点：0 个。"
    elif mode == "start_dialogue":
        state_text = "【英语对话中】现在开始英语对话。想结束时，请说“对话结束”。"
        progress_text = f"本单元进度：已完成 {unit_completed_targets} / {unit_total_targets} 个目标。"
        plan_text = format_round_plan(round_plan, unit)
        if plan_text:
            progress_text = f"{progress_text}\n\n{plan_text}"
    elif next_difficulty != current_difficulty:
        state_text = "【英语对话中】"
        progress_text = None
    elif captured_after > captured_before:
        state_text = "【英语对话中】"
        progress_text = f"本轮已整理：{captured_after} 个以后会复习的小点。"

    parts = [part for part in (state_text, progress_text, model_reply) if part]
    return DialoguePresentation(state_text, progress_text, "\n\n".join(parts), speech_text or model_reply)


def active_dialogue_failure_text() -> str:
    """Fixed state-aware fallback when a model turn cannot be committed."""

    return "【英语对话中】刚才没有处理成功，请原样再发一次。想结束时，请说“对话结束”。"


_SLOT_LABELS = {
    "target_expression": "目标表达",
    "school_detail": "学校细节",
    "club_detail": "社团细节",
    "school_day_detail": "学校日程细节",
    "rule_reason_or_action": "规则做法或理由",
    "choice_reason": "选择理由",
    "similarity_or_difference": "相同点或不同点",
    "contrast_or_similarity": "对比或相同点",
    "supporting_detail": "相关细节",
}

_SCENARIO_LABELS = {
    "school-introduction": "介绍学校",
    "club-enquiry": "社团交流",
    "school-day": "学校日程",
    "school-safety-etiquette": "学校安全与礼仪",
    "dream-school": "理想学校",
    "compare-schools": "比较学校",
}


def format_round_plan(round_plan: dict[str, Any] | None, unit: Any | None) -> str | None:
    """Render the frozen program plan as a concise child-visible start notice."""

    if not isinstance(round_plan, dict) or not isinstance(round_plan.get("steps"), list) or not round_plan["steps"]:
        return None
    scenarios_by_id = getattr(unit, "scenarios_by_id", {}) if unit is not None else {}
    targets_by_id = getattr(unit, "targets_by_id", {}) if unit is not None else {}
    intents = getattr(unit, "question_intents", ()) if unit is not None else ()
    intents_by_key = {str(item.get("intent_key")): item for item in intents if isinstance(item, dict)}
    scenario_ids = [str(value) for value in round_plan.get("scenario_ids", [])]
    scenario_labels = []
    for scenario_id in scenario_ids:
        scenario_labels.append(_SCENARIO_LABELS.get(scenario_id, scenario_id))

    lines = ["本轮计划："]
    if scenario_labels:
        lines.append("场景：" + "；".join(scenario_labels) + "。")
    lines.append("交流步骤：")
    for index, raw_step in enumerate(round_plan["steps"], 1):
        if not isinstance(raw_step, dict):
            continue
        target_id = str(raw_step.get("target_id", ""))
        target = targets_by_id.get(target_id, {})
        english = str(target.get("english", target_id)).strip()
        meaning = str(target.get("meaning_zh", "")).strip()
        target_label = f"{english}（{meaning}）" if meaning else english
        scenario_label = _SCENARIO_LABELS.get(str(raw_step.get("scenario_id", "")), str(raw_step.get("scenario_id", "")))
        intent_key = str(raw_step.get("question_intent_key", ""))
        intent = intents_by_key.get(intent_key, {})
        purpose = str(intent.get("purpose", "")).strip() or f"围绕 {english} 交流"
        slots = [
            _SLOT_LABELS.get(str(slot.get("purpose", "")), str(slot.get("purpose", "")).strip())
            for slot in raw_step.get("content_slots", [])
            if isinstance(slot, dict)
            and str(slot.get("purpose", "")).strip()
            and str(slot.get("purpose", "")) != "target_expression"
        ]
        line = f"{index}. [{scenario_label}] {target_label}：{purpose}"
        if slots:
            line += "；完整表达时还要说出：" + "、".join(dict.fromkeys(slots))
        lines.append(line + "。")
    lines.append("我会根据你的回答逐步调整问题；计划中的较难问题要把多个信息说完整。")
    return "\n".join(lines)


def _require_difficulty(value: int) -> None:
    if type(value) is not int or not 2 <= value <= 4:
        raise ValueError("dialogue difficulty is invalid")
