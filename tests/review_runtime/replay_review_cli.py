"""Deterministic stdin/stdout Review Graph fixture for the Node adapter replay.

It deliberately uses a temporary database and FakeReviewModelGateway.  The
fixture is not a runtime CLI and accepts no provider, archive-root, or channel
configuration.
"""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
import tempfile


PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "runtime"))

from v3_review import AuthorizedReviewIngress, ReviewTurnService
from v3_review.gateway import FakeReviewModelGateway, question_result
from v3_workflow.persistence.repository import WorkflowRepository
from v3_workflow.policy.review_schedule import ReviewSchedulePolicy


NOW = "2026-08-23T00:00:00Z"


def _seed_due_item(database_path: Path, external_session_ref: str) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(
            """INSERT INTO workflows(workflow_id, external_session_id, workflow_type, phase, learning_item_id,
               question_sequence, started_at, closed_at) VALUES ('replay-entry', ?, 'entry', 'closed', NULL, 0, ?, ?)""",
            (external_session_ref, NOW, NOW),
        )
        connection.execute(
            """INSERT INTO learning_materials(material_id, source_workflow_id, status, title, language, unit_type,
               reference_text, needs_parent_review, audit_result_json, created_at, updated_at)
               VALUES ('replay-material', 'replay-entry', 'active', 'Replay material', 'en', 'sentence',
               'I go to school.', 0, '{}', ?, ?)""",
            (NOW, NOW),
        )
        connection.execute(
            """INSERT INTO learning_items(learning_item_id, material_id, item_order, reference_text, meaning_zh,
               review_stage, next_review_at, completed_at, revision, created_at, updated_at)
               VALUES ('replay-item', 'replay-material', 1, 'I go to school.', '我去上学。', 0, ?, NULL, 1, ?, ?)""",
            ("2026-08-22T23:59:00Z", NOW, NOW),
        )


def run(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != {"message_text", "received_at", "external_session_ref", "start_requested"}:
        raise ValueError("replay ingress is invalid")
    if not isinstance(value["message_text"], str) or not value["message_text"] or not isinstance(value["received_at"], str) or not isinstance(value["external_session_ref"], str) or not value["external_session_ref"].startswith("child_") or value["start_requested"] is not True:
        raise ValueError("replay ingress fields are invalid")
    with tempfile.TemporaryDirectory() as temporary_directory:
        database_path = Path(temporary_directory) / "workflow-v3.sqlite3"
        repository = WorkflowRepository(database_path)
        repository.initialize()
        _seed_due_item(database_path, value["external_session_ref"])
        gateway = FakeReviewModelGateway([
            question_result("en_to_zh", {"prompt": "I go to school.", "instruction": "请说出这句话的中文意思。"}),
        ])
        service = ReviewTurnService(
            database_path,
            gateway,
            "fixed replay review prompt",
            ReviewSchedulePolicy.from_file(PROJECT / "config" / "review-schedule.test.json"),
            question_mode_selector=lambda _unit_type, _previous: "en_to_zh",
        )
        try:
            delivery = service.handle(AuthorizedReviewIngress(value["message_text"], value["received_at"], value["external_session_ref"], True))
            workflow = repository.get_active_workflow(value["external_session_ref"], "review")
            event_types = [] if workflow is None else [event["event_type"] for event in repository.list_events(workflow["workflow_id"])]
            return {
                "ok": True,
                "handled": delivery.handled,
                "reply_text": delivery.reply_text,
                "progress_text": delivery.progress_text,
                "no_due_item": delivery.no_due_item,
                "replay": {"question_locked": event_types.count("question_locked"), "assistant_response": event_types.count("assistant_response")},
            }
        finally:
            service.close()


def main() -> int:
    try:
        result = run(json.loads(sys.stdin.read()))
    except (ValueError, OSError, json.JSONDecodeError):
        result = {"ok": False, "handled": False, "reply_text": None, "progress_text": None, "no_due_item": False}
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
