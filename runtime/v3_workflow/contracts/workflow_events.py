"""Names and envelopes for the unified v3 workflow event log."""

from __future__ import annotations

EVENT_CONTRACT_NAME = "dada.workflow_log_event"
EVENT_CONTRACT_VERSION = 1

COMMON_EVENT_TYPES = frozenset(
    {
        "system_prompt",
        "child_message",
        "llm_request",
        "internal_reasoning",
        "internal_reasoning_unavailable",
        "tool_call",
        "tool_result",
        "llm_response",
        "model_turn_attempt_failed",
        "assistant_response",
        "state_transition",
    }
)
ENTRY_EVENT_TYPES = frozenset({"reentry_requested", "reentry_resolved"})
REVIEW_EVENT_TYPES = frozenset({"question_locked", "question_released", "schedule_applied", "material_archived"})
DIALOGUE_EVENT_TYPES = frozenset(
    {
        "dialogue_turn_evaluated",
        "dialogue_capture_committed",
        "dialogue_wrapping_started",
        "dialogue_batch_ready",
        "unit_course_passed",
        "dialogue_round_planned",
        "dialogue_question_intent_used",
        "dialogue_progress_checkpoint",
        "dialogue_round_completed",
        "dialogue_target_reopened",
    }
)
EVENT_TYPES = COMMON_EVENT_TYPES | ENTRY_EVENT_TYPES | REVIEW_EVENT_TYPES | DIALOGUE_EVENT_TYPES
