"""Entry-facing compatibility facade over shared v3 workflow facts."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from v3_workflow.persistence.repository import RepositoryError, WorkflowRepository, canonical_json, canonical_time
from v3_workflow.policy.review_schedule import ReviewSchedulePolicy

from ..contracts.entry_turn import EntryAudit, EntryStateMachineResult


class EntryRepository(WorkflowRepository):
    """Retains the EntryTurnService-facing API while using unified tables."""

    def __init__(self, database_path: Path, policy: ReviewSchedulePolicy | None = None) -> None:
        super().__init__(database_path)
        self._policy = policy or ReviewSchedulePolicy.from_file(Path(__file__).resolve().parents[3] / "config" / "review-schedule.json")

    def get_collecting_entry(self, external_session_id: str) -> dict[str, Any] | None:
        return self._entry_alias(self.get_active_workflow(external_session_id, "entry"))

    def get_workflow(self, entry_workflow_id: str) -> dict[str, Any] | None:
        return self._entry_alias(super().get_workflow(entry_workflow_id))

    @staticmethod
    def _entry_alias(workflow: dict[str, Any] | None) -> dict[str, Any] | None:
        if workflow is None:
            return None
        # Preserve the public EntryRepository phase vocabulary while the
        # shared business fact uses generic active/closed workflow phases.
        legacy_phase = "collecting" if workflow["phase"] == "active" else workflow["phase"]
        return {**workflow, "entry_workflow_id": workflow["workflow_id"], "phase": legacy_phase}

    def get_pending_reentry_requests(self, entry_workflow_id: str) -> tuple[dict[str, str | None], ...]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT requested.event_id, requested.payload_json FROM workflow_log_events AS requested
                   WHERE requested.workflow_id = ? AND requested.event_type = 'reentry_requested'
                     AND NOT EXISTS (SELECT 1 FROM workflow_log_events AS resolved
                       WHERE resolved.workflow_id = requested.workflow_id AND resolved.event_type = 'reentry_resolved'
                         AND resolved.related_event_id = requested.event_id) ORDER BY requested.sequence_no""",
                (entry_workflow_id,),
            ).fetchall()
        return tuple(
            {"event_id": row["event_id"], "guidance": json.loads(row["payload_json"])["data"]["guidance"], "unit_type": json.loads(row["payload_json"])["data"]["unit_type"]}
            for row in rows
        )

    def get_unprocessed_entry_child_event(self, entry_workflow_id: str) -> dict[str, str] | None:
        """Return the oldest child input without an assistant terminal outcome.

        This is the Entry equivalent of Review's pending-answer recovery. It
        makes an older persisted input the only candidate for model replay and
        never manufactures a second child event.
        """

        with self._connection() as connection:
            row = connection.execute(
                """SELECT child.event_id, child.payload_json,
                          CASE WHEN NOT EXISTS (
                            SELECT 1 FROM workflow_log_events AS earlier
                            WHERE earlier.workflow_id = child.workflow_id AND earlier.event_type = 'child_message'
                              AND earlier.sequence_no < child.sequence_no
                          ) THEN 'start_entry' ELSE 'collect_message' END AS turn_mode
                   FROM workflow_log_events AS child
                   WHERE child.workflow_id = ? AND child.event_type = 'child_message'
                     AND NOT EXISTS (
                       SELECT 1 FROM workflow_log_events AS outcome
                       WHERE outcome.workflow_id = child.workflow_id AND outcome.sequence_no > child.sequence_no
                         AND outcome.event_type = 'assistant_response'
                     )
                   ORDER BY child.sequence_no LIMIT 1""",
                (entry_workflow_id,),
            ).fetchone()
        if row is None:
            return None
        text = json.loads(row["payload_json"]).get("data", {}).get("text")
        if not isinstance(text, str) or not text:
            raise RepositoryError("unprocessed entry child text is invalid")
        return {"event_id": str(row["event_id"]), "text": text, "turn_mode": str(row["turn_mode"])}

    def list_materials(self, entry_workflow_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute("SELECT * FROM learning_materials WHERE source_workflow_id = ? ORDER BY created_at, material_id", (entry_workflow_id,)).fetchall()
        return [dict(row) for row in rows]

    def start_entry(self, entry_workflow_id: str, external_session_id: str | None, system_prompt: str, trigger_message: str, created_at: str) -> str:
        if not isinstance(external_session_id, str):
            raise RepositoryError("entry start requires an external session")
        return self.start_entry_workflow(entry_workflow_id, external_session_id, system_prompt, trigger_message, created_at)

    def append_child_message(self, entry_workflow_id: str, message_text: str, created_at: str) -> str:
        return super().append_child_message(entry_workflow_id, message_text, created_at, "entry")

    def append_log_event(
        self,
        entry_workflow_id: str,
        event_type: str,
        payload: dict[str, Any],
        created_at: str,
        related_event_id: str | None = None,
        *,
        require_active_type: str | None = None,
    ) -> str:
        if require_active_type not in (None, "entry"):
            raise RepositoryError("entry facade only writes entry workflows")
        return super().append_log_event(entry_workflow_id, event_type, payload, created_at, related_event_id, require_active_type="entry")

    def commit_state_machine_result(self, entry_workflow_id: str, child_message_event_id: str, result: EntryStateMachineResult, created_at: str) -> tuple[str, str]:
        timestamp = canonical_time(created_at)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_active(connection, entry_workflow_id, "entry")
            self._require_child_event(connection, entry_workflow_id, child_message_event_id)
            if result.next_operation == "record_entry_audit":
                if result.entry_audit is None:
                    raise RepositoryError("audit operation requires a validated entry audit")
                self._commit_audit(connection, entry_workflow_id, result.entry_audit, timestamp)
                material_count = int(connection.execute(
                    "SELECT COUNT(*) FROM learning_materials WHERE source_workflow_id = ?", (entry_workflow_id,)
                ).fetchone()[0])
                response_text = (
                    f"{result.assistant_response}\n\n"
                    f"本次已录入 {material_count} 条学习内容。"
                ) if result.entry_audit.materials else result.assistant_response
            elif result.next_operation != "reply_only" or result.entry_audit is not None:
                raise RepositoryError("state machine operation is invalid")
            else:
                response_text = result.assistant_response
            event_id = self._append_event(connection, entry_workflow_id, "assistant_response", {"text": response_text}, timestamp)
            return event_id, response_text

    def commit_control_response(self, entry_workflow_id: str, response_text: str, created_at: str) -> str:
        if not isinstance(response_text, str) or not response_text:
            raise RepositoryError("control response must be non-empty")
        return self.append_log_event(entry_workflow_id, "assistant_response", {"text": response_text}, created_at)

    def finish_entry_if_ready(self, entry_workflow_id: str, created_at: str) -> tuple[bool, str, str]:
        timestamp = canonical_time(created_at)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_active(connection, entry_workflow_id, "entry")
            pending = self._pending_reentry_count(connection, entry_workflow_id)
            if pending:
                response = f"还有 {pending} 条需要补充。补完后再说“结束录入”吧。"
                event_id = self._append_event(connection, entry_workflow_id, "assistant_response", {"text": response}, timestamp)
                return False, event_id, response
            event_id = self._append_event(connection, entry_workflow_id, "state_transition", {"from_state": "entry_collecting", "to_state": None, "cause": "close"}, timestamp)
            connection.execute("UPDATE workflows SET phase = 'closed', closed_at = ? WHERE workflow_id = ?", (timestamp, entry_workflow_id))
            response = "这次录入已经保存好了，做得很认真！"
            self._append_event(connection, entry_workflow_id, "assistant_response", {"text": response}, timestamp)
            return True, event_id, response

    def record_recovery(self, entry_workflow_id: str, created_at: str) -> str:
        return self.append_log_event(entry_workflow_id, "state_transition", {"from_state": "entry_collecting", "to_state": "entry_collecting", "cause": "recover"}, created_at)

    def _commit_audit(self, connection: sqlite3.Connection, entry_workflow_id: str, audit: EntryAudit, timestamp: str) -> None:
        material_ids: list[str] = []
        for material in audit.materials:
            material_id = str(uuid4())
            material_ids.append(material_id)
            is_parent_review = material.needs_parent_review
            connection.execute(
                """INSERT INTO learning_materials(material_id, source_workflow_id, status, title, language, unit_type,
                   reference_text, needs_parent_review, audit_result_json, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'en', ?, ?, ?, ?, ?, ?)""",
                (material_id, entry_workflow_id, "draft" if is_parent_review else "active", material.title, material.unit_type,
                 material.reference_text, 1 if is_parent_review else 0, canonical_json(material.audit_result.as_envelope()), timestamp, timestamp),
            )
            for item_order, item in enumerate(material.items, start=1):
                connection.execute(
                    """INSERT INTO learning_items(learning_item_id, material_id, item_order, reference_text, meaning_zh,
                       review_stage, next_review_at, completed_at, revision, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 1, ?, ?)""",
                    (str(uuid4()), material_id, item_order, item["reference_text"], item["meaning_zh"],
                     None if is_parent_review else self._policy.initial_stage,
                     None if is_parent_review else self._policy.initial_due_at(timestamp), timestamp, timestamp),
                )
        for request in audit.reentry_requests:
            self._append_event(connection, entry_workflow_id, "reentry_requested", {"guidance": request.guidance, "unit_type": request.unit_type}, timestamp)
        for request_id in audit.resolved_reentry_request_ids:
            self._append_event(connection, entry_workflow_id, "reentry_resolved", {"material_id": material_ids[0]}, timestamp, request_id)

    @staticmethod
    def _pending_reentry_count(connection: sqlite3.Connection, entry_workflow_id: str) -> int:
        return int(connection.execute(
            """SELECT COUNT(*) AS count FROM workflow_log_events AS requested WHERE requested.workflow_id = ?
               AND requested.event_type = 'reentry_requested' AND NOT EXISTS (
                 SELECT 1 FROM workflow_log_events AS resolved WHERE resolved.workflow_id = requested.workflow_id
                   AND resolved.event_type = 'reentry_resolved' AND resolved.related_event_id = requested.event_id)""",
            (entry_workflow_id,),
        ).fetchone()["count"])

    @staticmethod
    def _require_child_event(connection: sqlite3.Connection, entry_workflow_id: str, event_id: str) -> None:
        row = connection.execute("SELECT 1 FROM workflow_log_events WHERE workflow_id = ? AND event_id = ? AND event_type = 'child_message'", (entry_workflow_id, event_id)).fetchone()
        if row is None:
            raise RepositoryError("state machine result must be attached to its child message")
