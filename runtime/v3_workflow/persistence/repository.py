"""Fixed SQLite operations for unified v3 workflow facts and audit events."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Iterator
from uuid import uuid4

from ..contracts.validation import WorkflowContractError, validate_workflow_log_event
from .schema import initialize_schema


class RepositoryError(RuntimeError):
    """A workflow persistence precondition or invariant was not satisfied."""


@dataclass(frozen=True)
class ReviewAssessmentOutcome:
    last_event_id: str
    has_next_item: bool


_FORBIDDEN_KEYS = frozenset({"authorization", "api_key", "apikey", "token", "cookie", "password", "secret"})


def canonical_time(value: str) -> str:
    if not isinstance(value, str):
        raise RepositoryError("timestamp must be RFC3339 UTC text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RepositoryError("timestamp must be RFC3339 UTC text") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise RepositoryError("timestamp must be UTC")
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    if _contains_forbidden_key(value):
        raise RepositoryError("credential-shaped value cannot be stored in an audit event")
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise RepositoryError("audit payload is not JSON serializable") from exc


def _contains_forbidden_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(str(key).lower() in _FORBIDDEN_KEYS or _contains_forbidden_key(item) for key, item in value.items())
    if isinstance(value, list):
        return any(_contains_forbidden_key(item) for item in value)
    return False


class WorkflowRepository:
    """The only shared v3 workflow business-table read/write boundary."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)

    def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            initialize_schema(connection)

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        return dict(row) if row is not None else None

    def get_active_workflow(self, external_session_id: str, workflow_type: str | None = None) -> dict[str, Any] | None:
        _require_text(external_session_id, "external session id")
        if workflow_type is not None and workflow_type not in {"entry", "review"}:
            raise RepositoryError("workflow type is unsupported")
        with self._connection() as connection:
            if workflow_type is None:
                row = connection.execute(
                    "SELECT * FROM workflows WHERE external_session_id = ? AND phase = 'active'", (external_session_id,)
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM workflows WHERE external_session_id = ? AND workflow_type = ? AND phase = 'active'",
                    (external_session_id, workflow_type),
                ).fetchone()
            return self._row(row)

    def get_paused_review(self, external_session_id: str) -> dict[str, Any] | None:
        _require_text(external_session_id, "external session id")
        with self._connection() as connection:
            row = connection.execute(
                "SELECT * FROM workflows WHERE external_session_id = ? AND workflow_type = 'review' AND phase = 'paused_for_entry'",
                (external_session_id,),
            ).fetchone()
            return self._row(row)

    def get_workflow(self, workflow_id: str) -> dict[str, Any] | None:
        with self._connection() as connection:
            return self._row(connection.execute("SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,)).fetchone())

    def list_events(self, workflow_id: str) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM workflow_log_events WHERE workflow_id = ? ORDER BY sequence_no", (workflow_id,)
            ).fetchall()
        return [{**dict(row), "payload": json.loads(row["payload_json"])["data"]} for row in rows]

    def append_log_event(
        self,
        workflow_id: str,
        event_type: str,
        payload: dict[str, Any],
        created_at: str,
        related_event_id: str | None = None,
        *,
        require_active_type: str | None = None,
    ) -> str:
        timestamp = canonical_time(created_at)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if require_active_type is not None:
                self._require_active(connection, workflow_id, require_active_type)
            return self._append_event(connection, workflow_id, event_type, payload, timestamp, related_event_id)

    def append_child_message(self, workflow_id: str, message_text: str, created_at: str, workflow_type: str) -> str:
        _require_text(message_text, "child message")
        return self.append_log_event(
            workflow_id, "child_message", {"text": message_text}, created_at, require_active_type=workflow_type
        )

    def get_child_message(self, workflow_id: str, event_id: str) -> str:
        with self._connection() as connection:
            row = connection.execute(
                """SELECT payload_json FROM workflow_log_events
                   WHERE workflow_id = ? AND event_id = ? AND event_type = 'child_message'""",
                (workflow_id, event_id),
            ).fetchone()
        if row is None:
            raise RepositoryError("child message event was not found")
        text = json.loads(row["payload_json"]).get("data", {}).get("text")
        _require_text(text, "child message event text")
        return text

    def get_latest_child_event_id(self, workflow_id: str) -> str:
        """Return the newest persisted child event for a fixed workflow only."""

        with self._connection() as connection:
            row = connection.execute(
                """SELECT event_id FROM workflow_log_events WHERE workflow_id = ? AND event_type = 'child_message'
                   ORDER BY sequence_no DESC LIMIT 1""",
                (workflow_id,),
            ).fetchone()
        if row is None:
            raise RepositoryError("workflow has no child message event")
        return str(row["event_id"])

    def get_latest_assistant_delivery(self, workflow_id: str) -> tuple[str, str] | None:
        """Read the last committed child-visible response without mutation."""

        with self._connection() as connection:
            row = connection.execute(
                """SELECT event_id, payload_json FROM workflow_log_events
                   WHERE workflow_id = ? AND event_type = 'assistant_response'
                   ORDER BY sequence_no DESC LIMIT 1""",
                (workflow_id,),
            ).fetchone()
        if row is None:
            return None
        text = json.loads(row["payload_json"]).get("data", {}).get("text")
        return str(row["event_id"]), _require_text(text, "assistant delivery text")

    def start_entry_workflow(
        self, workflow_id: str, external_session_id: str, system_prompt: str, trigger_message: str, created_at: str
    ) -> str:
        timestamp = canonical_time(created_at)
        for value, label in ((workflow_id, "workflow id"), (external_session_id, "external session id"), (system_prompt, "system prompt"), (trigger_message, "trigger message")):
            _require_text(value, label)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM workflows WHERE external_session_id = ? AND phase = 'active'", (external_session_id,)).fetchone() is not None:
                raise RepositoryError("an active workflow already exists")
            connection.execute(
                """INSERT INTO workflows(workflow_id, external_session_id, workflow_type, phase, learning_item_id,
                   question_sequence, locked_question_mode, locked_question_json, locked_item_revision, locked_at,
                   started_at, paused_at, closed_at)
                   VALUES (?, ?, 'entry', 'active', NULL, 0, NULL, NULL, NULL, NULL, ?, NULL, NULL)""",
                (workflow_id, external_session_id, timestamp),
            )
            self._append_event(connection, workflow_id, "system_prompt", {"text": system_prompt}, timestamp)
            self._append_event(connection, workflow_id, "state_transition", {"from_state": None, "to_state": "entry_collecting", "cause": "start"}, timestamp)
            return self._append_event(connection, workflow_id, "child_message", {"text": trigger_message}, timestamp)

    def request_review_start(self, workflow_id: str, external_session_id: str, created_at: str) -> dict[str, Any] | None:
        """Resume a paused review or freeze all currently due active items.

        The caller owns question generation. This fixed action performs no LLM
        work and leaves no workflow when no item is eligible.
        """

        timestamp = canonical_time(created_at)
        _require_text(workflow_id, "workflow id")
        _require_text(external_session_id, "external session id")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM workflows WHERE external_session_id = ? AND phase = 'active'", (external_session_id,)).fetchone() is not None:
                raise RepositoryError("an active workflow already exists")
            paused = connection.execute(
                "SELECT * FROM workflows WHERE external_session_id = ? AND workflow_type = 'review' AND phase = 'paused_for_entry'",
                (external_session_id,),
            ).fetchone()
            if paused is not None:
                connection.execute("UPDATE workflows SET phase = 'active', paused_at = NULL WHERE workflow_id = ?", (paused["workflow_id"],))
                self._append_event(connection, paused["workflow_id"], "state_transition", {"from_state": None, "to_state": "review_active", "cause": "resume"}, timestamp)
                return self._row(connection.execute("SELECT * FROM workflows WHERE workflow_id = ?", (paused["workflow_id"],)).fetchone())
            items = connection.execute(
                """SELECT item.learning_item_id, item.revision FROM learning_items AS item
                   JOIN learning_materials AS material ON material.material_id = item.material_id
                   WHERE material.source_workflow_id IN (
                     SELECT workflow_id FROM workflows WHERE external_session_id = ? AND workflow_type = 'entry'
                   ) AND material.status = 'active' AND material.needs_parent_review = 0
                     AND item.review_stage IS NOT NULL AND item.completed_at IS NULL AND item.next_review_at <= ?
                   ORDER BY item.next_review_at, item.learning_item_id""",
                (external_session_id, timestamp),
            ).fetchall()
            if not items:
                return None
            first = items[0]
            connection.execute(
                """INSERT INTO workflows(workflow_id, external_session_id, workflow_type, phase, learning_item_id,
                   question_sequence, locked_question_mode, locked_question_json, locked_item_revision, locked_at,
                   started_at, paused_at, closed_at)
                   VALUES (?, ?, 'review', 'active', ?, 0, NULL, NULL, NULL, NULL, ?, NULL, NULL)""",
                (workflow_id, external_session_id, first["learning_item_id"], timestamp),
            )
            connection.executemany(
                """INSERT INTO review_queue_items(workflow_id, queue_position, learning_item_id, item_revision_at_start, status, completed_at)
                   VALUES (?, ?, ?, ?, 'pending', NULL)""",
                [(workflow_id, position, item["learning_item_id"], item["revision"]) for position, item in enumerate(items, start=1)],
            )
            self._append_event(connection, workflow_id, "state_transition", {"from_state": None, "to_state": "review_active", "cause": "start"}, timestamp)
            return self._row(connection.execute("SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,)).fetchone())

    def preview_entry_review_start(self, entry_workflow_id: str, at: str) -> dict[str, Any] | None:
        """Read the one eligible item for an entry→review handoff without mutation."""

        timestamp = canonical_time(at)
        with self._connection() as connection:
            entry = self._require_active(connection, entry_workflow_id, "entry")
            if self._pending_reentry_count(connection, entry_workflow_id):
                raise RepositoryError("entry workflow still has pending re-entry requests")
            paused = connection.execute(
                "SELECT * FROM workflows WHERE external_session_id = ? AND workflow_type = 'review' AND phase = 'paused_for_entry'",
                (entry["external_session_id"],),
            ).fetchone()
            if paused is not None:
                return {"kind": "resume", "workflow": dict(paused)}
            item = self._select_due_item(connection, entry["external_session_id"], timestamp)
            if item is None:
                return None
            return {"kind": "new", "entry": dict(entry), "item": dict(item)}

    def commit_entry_to_new_review_question(
        self,
        entry_workflow_id: str,
        review_workflow_id: str,
        review_child_event_id: str,
        review_system_prompt: str,
        trigger_message: str,
        prepared_request: dict[str, Any],
        provider: str,
        model: str,
        execution: dict[str, Any],
        question_mode: str,
        question_json: dict[str, Any],
        assistant_response: str,
        created_at: str,
    ) -> tuple[str, str]:
        """Atomically close ready entry and create the first locked review question.

        The model call happens before this method. Its result is accepted only
        here, after all entry readiness and due-item checks are revalidated.
        """

        timestamp = canonical_time(created_at)
        for value, label in ((review_workflow_id, "review workflow id"), (review_child_event_id, "review child event id"), (review_system_prompt, "review system prompt"), (trigger_message, "trigger message"), (provider, "provider"), (model, "model"), (question_mode, "question mode"), (assistant_response, "assistant response")):
            _require_text(value, label)
        if not isinstance(prepared_request, dict) or not isinstance(execution, dict) or not isinstance(question_json, dict):
            raise RepositoryError("handoff payload is invalid")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            entry = self._require_active(connection, entry_workflow_id, "entry")
            if self._pending_reentry_count(connection, entry_workflow_id):
                raise RepositoryError("entry workflow still has pending re-entry requests")
            if connection.execute("SELECT 1 FROM workflows WHERE external_session_id = ? AND workflow_type = 'review' AND phase = 'paused_for_entry'", (entry["external_session_id"],)).fetchone() is not None:
                raise RepositoryError("paused review must be resumed instead of creating a new review")
            item = self._select_due_item(connection, entry["external_session_id"], timestamp)
            if item is None:
                raise RepositoryError("no due review item is available")
            self._append_event(connection, entry_workflow_id, "state_transition", {"from_state": "entry_collecting", "to_state": None, "cause": "switch"}, timestamp)
            connection.execute("UPDATE workflows SET phase = 'closed', closed_at = ? WHERE workflow_id = ?", (timestamp, entry_workflow_id))
            connection.execute(
                """INSERT INTO workflows(workflow_id, external_session_id, workflow_type, phase, learning_item_id,
                   question_sequence, locked_question_mode, locked_question_json, locked_item_revision, locked_at,
                   started_at, paused_at, closed_at)
                   VALUES (?, ?, 'review', 'active', ?, 0, NULL, NULL, NULL, NULL, ?, NULL, NULL)""",
                (review_workflow_id, entry["external_session_id"], item["learning_item_id"], timestamp),
            )
            self._append_event(connection, review_workflow_id, "system_prompt", {"text": review_system_prompt}, timestamp)
            self._append_event(connection, review_workflow_id, "state_transition", {"from_state": None, "to_state": "review_active", "cause": "switch"}, timestamp)
            self._append_event(connection, review_workflow_id, "child_message", {"text": trigger_message}, timestamp, event_id=review_child_event_id)
            self._append_event(connection, review_workflow_id, "llm_request", {"provider": provider, "model": model, "request": prepared_request, "source_child_event_id": review_child_event_id}, timestamp)
            reasoning = execution.get("internal_reasoning")
            if reasoning is None:
                self._append_event(connection, review_workflow_id, "internal_reasoning_unavailable", {"reason_code": "provider_not_returned"}, timestamp)
            else:
                self._append_event(connection, review_workflow_id, "internal_reasoning", {"text": reasoning}, timestamp)
            llm_response_id = self._append_event(
                connection, review_workflow_id, "llm_response",
                {"task_contract_name": "dada.review_state_machine_turn", "task_contract_version": 3, "output": execution["output"], "source_child_event_id": review_child_event_id}, timestamp,
            )
            question_sequence = 1
            self._append_event(connection, review_workflow_id, "question_locked", {"question_sequence": question_sequence, "question_mode": question_mode, "question_json": question_json, "item_revision": item["revision"]}, timestamp)
            connection.execute(
                """UPDATE workflows SET question_sequence = 1, locked_question_mode = ?, locked_question_json = ?,
                   locked_item_revision = ?, locked_at = ? WHERE workflow_id = ?""",
                (question_mode, canonical_json(question_json), item["revision"], timestamp, review_workflow_id),
            )
            assistant_event_id = self._append_event(connection, review_workflow_id, "assistant_response", {"text": assistant_response, "source_llm_response_event_id": llm_response_id}, timestamp)
            return review_workflow_id, assistant_event_id

    def resume_review_from_entry(self, entry_workflow_id: str, created_at: str) -> tuple[str, str]:
        """Atomically close a ready entry and restore the exact paused review."""

        timestamp = canonical_time(created_at)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            entry = self._require_active(connection, entry_workflow_id, "entry")
            if self._pending_reentry_count(connection, entry_workflow_id):
                raise RepositoryError("entry workflow still has pending re-entry requests")
            paused = connection.execute(
                "SELECT * FROM workflows WHERE external_session_id = ? AND workflow_type = 'review' AND phase = 'paused_for_entry'",
                (entry["external_session_id"],),
            ).fetchone()
            if paused is None:
                raise RepositoryError("paused review was not found")
            self._append_event(connection, entry_workflow_id, "state_transition", {"from_state": "entry_collecting", "to_state": None, "cause": "switch"}, timestamp)
            connection.execute("UPDATE workflows SET phase = 'closed', closed_at = ? WHERE workflow_id = ?", (timestamp, entry_workflow_id))
            connection.execute("UPDATE workflows SET phase = 'active', paused_at = NULL WHERE workflow_id = ?", (paused["workflow_id"],))
            event_id = self._append_event(connection, paused["workflow_id"], "state_transition", {"from_state": None, "to_state": "review_active", "cause": "resume"}, timestamp)
            return str(paused["workflow_id"]), event_id

    def append_review_system_prompt(self, workflow_id: str, system_prompt: str, created_at: str) -> str:
        """Persist the fixed review task prompt once before the first model turn."""

        _require_text(system_prompt, "system prompt")
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._require_active(connection, workflow_id, "review")
            existing = connection.execute(
                "SELECT event_id FROM workflow_log_events WHERE workflow_id = ? AND event_type = 'system_prompt'", (workflow_id,)
            ).fetchone()
            if existing is not None:
                return str(existing["event_id"])
            return self._append_event(connection, workflow_id, "system_prompt", {"text": system_prompt}, canonical_time(created_at))

    def get_review_context(self, workflow_id: str) -> dict[str, Any]:
        """Read one active/paused review's business context; no English decision occurs."""

        with self._connection() as connection:
            row = connection.execute(
                """SELECT workflow.*, item.learning_item_id, item.reference_text AS item_reference_text,
                          item.meaning_zh, item.review_stage, item.next_review_at, item.completed_at, item.revision,
                          material.material_id, material.status AS material_status, material.unit_type, material.needs_parent_review
                   FROM workflows AS workflow
                   JOIN learning_items AS item ON item.learning_item_id = workflow.learning_item_id
                   JOIN learning_materials AS material ON material.material_id = item.material_id
                   WHERE workflow.workflow_id = ? AND workflow.workflow_type = 'review'""",
                (workflow_id,),
            ).fetchone()
        if row is None:
            raise RepositoryError("review workflow was not found")
        return dict(row)

    def list_review_history(self, workflow_id: str, limit: int = 20) -> tuple[dict[str, Any], ...]:
        """Rebuild bounded same-workflow history from immutable event facts."""

        if type(limit) is not int or not 1 <= limit <= 100:
            raise RepositoryError("history limit is invalid")
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT event_id, sequence_no, event_type, payload_json FROM workflow_log_events
                   WHERE workflow_id = ? AND event_type IN ('question_locked', 'child_message', 'llm_response')
                   ORDER BY sequence_no DESC LIMIT ?""",
                (workflow_id, limit),
            ).fetchall()
        result = [
            {"event_id": row["event_id"], "sequence_no": row["sequence_no"], "event_type": row["event_type"], "data": json.loads(row["payload_json"])["data"]}
            for row in reversed(rows)
        ]
        return tuple(result)

    def list_review_question_modes(self, workflow_id: str) -> tuple[str, ...]:
        with self._connection() as connection:
            rows = connection.execute(
                """SELECT payload_json FROM workflow_log_events
                   WHERE workflow_id = ? AND event_type = 'question_locked'
                   ORDER BY sequence_no""",
                (workflow_id,),
            ).fetchall()
        modes: list[str] = []
        for row in rows:
            mode = json.loads(row["payload_json"])["data"].get("question_mode")
            if isinstance(mode, str):
                modes.append(mode)
        return tuple(modes)

    def get_unprocessed_review_child_event(self, workflow_id: str) -> dict[str, str] | None:
        """Return the oldest answer after the current lock without a committed outcome.

        A provider failure can occur before an `llm_response`; a malformed
        model response can be logged but rejected before scheduling, releasing
        the lock, or locking a follow-up. Retry that exact answer on the next
        active turn; do not silently grade a later retry as a second answer or
        lose the original audit fact.
        """

        with self._connection() as connection:
            locked = connection.execute(
                """SELECT sequence_no FROM workflow_log_events
                   WHERE workflow_id = ? AND event_type = 'question_locked'
                   ORDER BY sequence_no DESC LIMIT 1""",
                (workflow_id,),
            ).fetchone()
            if locked is None:
                return None
            row = connection.execute(
                """SELECT child.event_id, child.payload_json
                   FROM workflow_log_events AS child
                   WHERE child.workflow_id = ? AND child.event_type = 'child_message'
                     AND child.sequence_no > ?
                     AND NOT EXISTS (
                       SELECT 1 FROM workflow_log_events AS outcome
                       WHERE outcome.workflow_id = child.workflow_id AND outcome.sequence_no > child.sequence_no
                         AND outcome.event_type IN ('question_locked', 'question_released', 'schedule_applied', 'state_transition', 'assistant_response')
                     )
                   ORDER BY child.sequence_no LIMIT 1""",
                (workflow_id, locked["sequence_no"]),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload_json"])["data"]
        return {"event_id": str(row["event_id"]), "text": _require_text(payload.get("text"), "unprocessed child text")}

    def lock_review_question(
        self,
        workflow_id: str,
        question_mode: str,
        question_json: dict[str, Any],
        assistant_response: str,
        source_llm_response_event_id: str,
        created_at: str,
    ) -> tuple[int, str]:
        """Atomically record and expose exactly one new current question."""

        _require_text(question_mode, "question mode")
        _require_text(assistant_response, "assistant response")
        if not isinstance(question_json, dict):
            raise RepositoryError("question json must be an object")
        timestamp = canonical_time(created_at)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            workflow = self._require_active(connection, workflow_id, "review")
            item = self._require_review_item(connection, workflow)
            if item["status"] != "active" or item["needs_parent_review"] or item["completed_at"] is not None:
                raise RepositoryError("review item is not eligible")
            source = connection.execute(
                "SELECT event_type, workflow_id FROM workflow_log_events WHERE event_id = ?", (source_llm_response_event_id,)
            ).fetchone()
            if source is None or source["workflow_id"] != workflow_id or source["event_type"] != "llm_response":
                raise RepositoryError("question source response is invalid")
            sequence = int(workflow["question_sequence"]) + 1
            event_id = self._append_event(
                connection,
                workflow_id,
                "question_locked",
                {"question_sequence": sequence, "question_mode": question_mode, "question_json": question_json, "item_revision": item["revision"]},
                timestamp,
            )
            connection.execute(
                """UPDATE workflows SET question_sequence = ?, locked_question_mode = ?, locked_question_json = ?,
                   locked_item_revision = ?, locked_at = ? WHERE workflow_id = ?""",
                (sequence, question_mode, canonical_json(question_json), item["revision"], timestamp, workflow_id),
            )
            connection.execute(
                """UPDATE review_queue_items SET status = 'locked'
                   WHERE workflow_id = ? AND learning_item_id = ? AND status = 'pending'""",
                (workflow_id, item["learning_item_id"]),
            )
            assistant_event_id = self._append_event(
                connection,
                workflow_id,
                "assistant_response",
                {"text": assistant_response, "source_llm_response_event_id": source_llm_response_event_id},
                timestamp,
            )
            return sequence, assistant_event_id

    def get_locked_question_delivery(self, workflow_id: str) -> str | None:
        """Return the already-committed child reply for the current locked question.

        This is a recovery read only.  It never regenerates a question or asks
        a model, so a caller that lost its checkpoint can safely re-deliver the
        exact committed text.
        """

        with self._connection() as connection:
            workflow = self._require_active(connection, workflow_id, "review")
            if workflow["locked_question_mode"] is None:
                return None
            locked = connection.execute(
                """SELECT sequence_no FROM workflow_log_events
                   WHERE workflow_id = ? AND event_type = 'question_locked'
                   ORDER BY sequence_no DESC LIMIT 1""",
                (workflow_id,),
            ).fetchone()
            if locked is None:
                raise RepositoryError("locked review question has no event")
            response = connection.execute(
                """SELECT payload_json FROM workflow_log_events
                   WHERE workflow_id = ? AND event_type = 'assistant_response' AND sequence_no > ?
                   ORDER BY sequence_no ASC LIMIT 1""",
                (workflow_id, locked["sequence_no"]),
            ).fetchone()
        if response is None:
            raise RepositoryError("locked review question has no delivery")
        payload = json.loads(response["payload_json"])["data"]
        text = payload.get("text") if isinstance(payload, dict) else None
        _require_text(text, "locked question delivery")
        return text

    def get_review_queue_progress(self, workflow_id: str) -> dict[str, int]:
        """Read the active workflow's frozen queue without changing it."""

        with self._connection() as connection:
            self._require_active(connection, workflow_id, "review")
            row = connection.execute(
                """SELECT COUNT(*) AS total,
                          SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
                          SUM(CASE WHEN status <> 'completed' THEN 1 ELSE 0 END) AS remaining
                   FROM review_queue_items WHERE workflow_id = ?""",
                (workflow_id,),
            ).fetchone()
        total = int(row["total"])
        if total <= 0:
            raise RepositoryError("review workflow has no frozen queue")
        return {"total": total, "completed": int(row["completed"]), "remaining": int(row["remaining"])}

    def continue_locked_question(
        self, workflow_id: str, guidance_text: str, source_llm_response_event_id: str, created_at: str
    ) -> tuple[str, str]:
        """Persist a guidance reply while retaining the current locked question unchanged."""

        _require_text(guidance_text, "guidance text")
        timestamp = canonical_time(created_at)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            workflow = self._require_active(connection, workflow_id, "review")
            if workflow["locked_question_mode"] is None:
                raise RepositoryError("review workflow has no locked question")
            source = connection.execute(
                "SELECT event_type, workflow_id FROM workflow_log_events WHERE event_id = ?", (source_llm_response_event_id,)
            ).fetchone()
            if source is None or source["workflow_id"] != workflow_id or source["event_type"] != "llm_response":
                raise RepositoryError("guidance source response is invalid")
            locked = connection.execute(
                """SELECT sequence_no FROM workflow_log_events
                   WHERE workflow_id = ? AND event_type = 'question_locked'
                   ORDER BY sequence_no DESC LIMIT 1""",
                (workflow_id,),
            ).fetchone()
            if locked is None:
                raise RepositoryError("locked review question has no event")
            original = connection.execute(
                """SELECT payload_json FROM workflow_log_events
                   WHERE workflow_id = ? AND event_type = 'assistant_response' AND sequence_no > ?
                   ORDER BY sequence_no ASC LIMIT 1""",
                (workflow_id, locked["sequence_no"]),
            ).fetchone()
            if original is None:
                raise RepositoryError("locked review question has no delivery")
            original_text = json.loads(original["payload_json"]).get("data", {}).get("text")
            _require_text(original_text, "locked question delivery")
            delivery_text = f"{guidance_text}\n\n{original_text}"
            event_id = self._append_event(
                connection,
                workflow_id,
                "assistant_response",
                {"text": delivery_text, "source_llm_response_event_id": source_llm_response_event_id},
                timestamp,
            )
            return event_id, delivery_text

    def apply_review_assessment(
        self,
        workflow_id: str,
        assessment: dict[str, Any],
        policy: Any,
        assistant_response: str,
        source_llm_response_event_id: str,
        created_at: str,
    ) -> ReviewAssessmentOutcome:
        """Apply one assessment and advance the frozen queue when items remain."""

        _require_text(assistant_response, "assistant response")
        timestamp = canonical_time(created_at)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            workflow = self._require_active(connection, workflow_id, "review")
            if workflow["locked_question_mode"] is None:
                raise RepositoryError("review workflow has no locked question")
            item = self._require_review_item(connection, workflow)
            if item["status"] != "active" or item["needs_parent_review"] or item["completed_at"] is not None:
                raise RepositoryError("review item is not eligible")
            if assessment["question_sequence"] != workflow["question_sequence"]:
                raise RepositoryError("assessment does not match the current question")
            if item["revision"] != workflow["locked_item_revision"]:
                raise RepositoryError("review item revision changed after question lock")
            source = connection.execute("SELECT event_type, workflow_id FROM workflow_log_events WHERE event_id = ?", (source_llm_response_event_id,)).fetchone()
            if source is None or source["workflow_id"] != workflow_id or source["event_type"] != "llm_response":
                raise RepositoryError("assessment source response is invalid")
            decision = policy.decide(item["review_stage"], assessment["accuracy"], timestamp)
            revision_after = int(item["revision"]) + 1
            self._append_event(connection, workflow_id, "question_released", {"reason": "assessment_completed"}, timestamp)
            schedule_event_id = self._append_event(
                connection,
                workflow_id,
                "schedule_applied",
                {
                    "source_llm_response_event_id": source_llm_response_event_id,
                    "policy_id": decision.policy_id,
                    "policy_version": decision.policy_version,
                    "review_stage_before": decision.review_stage_before,
                    "review_stage_after": decision.review_stage_after,
                    "next_review_at_after": decision.next_review_at_after,
                    "item_revision_after": revision_after,
                },
                timestamp,
            )
            if decision.archive:
                connection.execute(
                    """UPDATE learning_items SET review_stage = ?, next_review_at = NULL, completed_at = ?, revision = ?, updated_at = ?
                       WHERE learning_item_id = ?""",
                    (decision.review_stage_after, timestamp, revision_after, timestamp, item["learning_item_id"]),
                )
                connection.execute("UPDATE learning_materials SET status = 'archived', updated_at = ? WHERE material_id = ?", (timestamp, item["material_id"]))
                self._append_event(connection, workflow_id, "material_archived", {"material_id": item["material_id"]}, timestamp)
            else:
                connection.execute(
                    """UPDATE learning_items SET review_stage = ?, next_review_at = ?, completed_at = NULL, revision = ?, updated_at = ?
                       WHERE learning_item_id = ?""",
                    (decision.review_stage_after, decision.next_review_at_after, revision_after, timestamp, item["learning_item_id"]),
                )
            connection.execute(
                """UPDATE review_queue_items SET status = 'completed', completed_at = ?
                   WHERE workflow_id = ? AND learning_item_id = ? AND status = 'locked'""",
                (timestamp, workflow_id, item["learning_item_id"]),
            )
            next_item = connection.execute(
                """SELECT learning_item_id FROM review_queue_items
                   WHERE workflow_id = ? AND status = 'pending' ORDER BY queue_position LIMIT 1""",
                (workflow_id,),
            ).fetchone()
            if next_item is not None:
                connection.execute(
                    """UPDATE workflows SET learning_item_id = ?, locked_question_mode = NULL,
                       locked_question_json = NULL, locked_item_revision = NULL, locked_at = NULL WHERE workflow_id = ?""",
                    (next_item["learning_item_id"], workflow_id),
                )
                return ReviewAssessmentOutcome(schedule_event_id, True)
            self._append_event(connection, workflow_id, "state_transition", {"from_state": "review_active", "to_state": None, "cause": "close"}, timestamp)
            connection.execute(
                """UPDATE workflows SET phase = 'closed', closed_at = ?, locked_question_mode = NULL,
                   locked_question_json = NULL, locked_item_revision = NULL, locked_at = NULL WHERE workflow_id = ?""",
                (timestamp, workflow_id),
            )
            event_id = self._append_event(
                connection, workflow_id, "assistant_response", {"text": assistant_response, "source_llm_response_event_id": source_llm_response_event_id}, timestamp
            )
            return ReviewAssessmentOutcome(event_id, False)

    def stop_review(self, workflow_id: str, response_text: str, created_at: str) -> str:
        """Close an active review without grade or schedule mutation."""

        _require_text(response_text, "response text")
        timestamp = canonical_time(created_at)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            workflow = self._require_active(connection, workflow_id, "review")
            if workflow["locked_question_mode"] is not None:
                self._append_event(connection, workflow_id, "question_released", {"reason": "review_stopped"}, timestamp)
            self._append_event(connection, workflow_id, "state_transition", {"from_state": "review_active", "to_state": None, "cause": "close"}, timestamp)
            connection.execute(
                """UPDATE workflows SET phase = 'closed', closed_at = ?, locked_question_mode = NULL,
                   locked_question_json = NULL, locked_item_revision = NULL, locked_at = NULL WHERE workflow_id = ?""",
                (timestamp, workflow_id),
            )
            return self._append_event(connection, workflow_id, "assistant_response", {"text": response_text}, timestamp)

    def pause_review_for_entry(
        self,
        review_workflow_id: str,
        entry_workflow_id: str,
        entry_system_prompt: str,
        trigger_message: str,
        response_text: str,
        created_at: str,
    ) -> tuple[str, str]:
        """Atomically freeze an active locked review and create its entry successor."""

        for value, label in ((entry_workflow_id, "entry workflow id"), (entry_system_prompt, "entry system prompt"), (trigger_message, "trigger message"), (response_text, "response text")):
            _require_text(value, label)
        timestamp = canonical_time(created_at)
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            review = self._require_active(connection, review_workflow_id, "review")
            self._append_event(connection, review_workflow_id, "state_transition", {"from_state": "review_active", "to_state": None, "cause": "switch"}, timestamp)
            connection.execute("UPDATE workflows SET phase = 'paused_for_entry', paused_at = ? WHERE workflow_id = ?", (timestamp, review_workflow_id))
            connection.execute(
                """INSERT INTO workflows(workflow_id, external_session_id, workflow_type, phase, learning_item_id,
                   question_sequence, locked_question_mode, locked_question_json, locked_item_revision, locked_at,
                   started_at, paused_at, closed_at)
                   VALUES (?, ?, 'entry', 'active', NULL, 0, NULL, NULL, NULL, NULL, ?, NULL, NULL)""",
                (entry_workflow_id, review["external_session_id"], timestamp),
            )
            self._append_event(connection, entry_workflow_id, "system_prompt", {"text": entry_system_prompt}, timestamp)
            self._append_event(connection, entry_workflow_id, "state_transition", {"from_state": None, "to_state": "entry_collecting", "cause": "switch"}, timestamp)
            child_event_id = self._append_event(connection, entry_workflow_id, "child_message", {"text": trigger_message}, timestamp)
            assistant_event_id = self._append_event(connection, entry_workflow_id, "assistant_response", {"text": response_text}, timestamp)
            return child_event_id, assistant_event_id

    def entry_handoff_has_initial_reply(self, workflow_id: str) -> bool:
        """Whether a controlled review→entry handoff already provided its first reply."""

        with self._connection() as connection:
            row = connection.execute(
                """SELECT event_type FROM workflow_log_events WHERE workflow_id = ?
                   ORDER BY sequence_no DESC LIMIT 1""", (workflow_id,)
            ).fetchone()
        return row is not None and row["event_type"] == "assistant_response"

    def _append_event(
        self, connection: sqlite3.Connection, workflow_id: str, event_type: str, payload: dict[str, Any], created_at: str, related_event_id: str | None = None, *, event_id: str | None = None
    ) -> str:
        try:
            envelope = validate_workflow_log_event(event_type, payload)
        except WorkflowContractError as exc:
            raise RepositoryError(str(exc)) from exc
        self._validate_event_references(connection, workflow_id, event_type, envelope["data"], related_event_id)
        sequence = connection.execute(
            "SELECT COALESCE(MAX(sequence_no), 0) + 1 AS next_sequence FROM workflow_log_events WHERE workflow_id = ?", (workflow_id,)
        ).fetchone()["next_sequence"]
        event_id = event_id or str(uuid4())
        connection.execute(
            """INSERT INTO workflow_log_events(event_id, workflow_id, sequence_no, event_type, related_event_id, payload_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (event_id, workflow_id, sequence, event_type, related_event_id, canonical_json(envelope), created_at),
        )
        return event_id

    @staticmethod
    def _require_active(connection: sqlite3.Connection, workflow_id: str, workflow_type: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,)).fetchone()
        if row is None or row["workflow_type"] != workflow_type or row["phase"] != "active":
            raise RepositoryError("workflow is not in the required active state")
        return row

    @staticmethod
    def _require_review_item(connection: sqlite3.Connection, workflow: sqlite3.Row) -> sqlite3.Row:
        item = connection.execute(
            """SELECT item.*, material.material_id, material.status, material.needs_parent_review
               FROM learning_items AS item JOIN learning_materials AS material ON material.material_id = item.material_id
               WHERE item.learning_item_id = ?""",
            (workflow["learning_item_id"],),
        ).fetchone()
        if item is None:
            raise RepositoryError("review workflow item was not found")
        return item

    @staticmethod
    def _pending_reentry_count(connection: sqlite3.Connection, workflow_id: str) -> int:
        return int(connection.execute(
            """SELECT COUNT(*) AS count FROM workflow_log_events AS requested WHERE requested.workflow_id = ?
               AND requested.event_type = 'reentry_requested' AND NOT EXISTS (
                 SELECT 1 FROM workflow_log_events AS resolved WHERE resolved.workflow_id = requested.workflow_id
                   AND resolved.event_type = 'reentry_resolved' AND resolved.related_event_id = requested.event_id)""",
            (workflow_id,),
        ).fetchone()["count"])

    @staticmethod
    def _select_due_item(connection: sqlite3.Connection, external_session_id: str, timestamp: str) -> sqlite3.Row | None:
        return connection.execute(
            """SELECT item.*, material.unit_type FROM learning_items AS item JOIN learning_materials AS material ON material.material_id = item.material_id
               WHERE material.source_workflow_id IN (SELECT workflow_id FROM workflows WHERE external_session_id = ? AND workflow_type = 'entry')
                 AND material.status = 'active' AND material.needs_parent_review = 0
                 AND item.review_stage IS NOT NULL AND item.completed_at IS NULL AND item.next_review_at <= ?
                 AND NOT EXISTS (SELECT 1 FROM workflows AS existing WHERE existing.workflow_type = 'review'
                   AND existing.learning_item_id = item.learning_item_id AND existing.phase <> 'closed')
               ORDER BY item.next_review_at, item.learning_item_id LIMIT 1""",
            (external_session_id, timestamp),
        ).fetchone()

    @staticmethod
    def _validate_event_references(connection: sqlite3.Connection, workflow_id: str, event_type: str, data: dict[str, Any], related_event_id: str | None) -> None:
        if event_type == "reentry_resolved":
            if not related_event_id:
                raise RepositoryError("re-entry resolution requires its source event")
            related = connection.execute("SELECT workflow_id, event_type FROM workflow_log_events WHERE event_id = ?", (related_event_id,)).fetchone()
            if related is None or related["workflow_id"] != workflow_id or related["event_type"] != "reentry_requested":
                raise RepositoryError("re-entry resolution source is invalid")
        elif related_event_id is not None:
            raise RepositoryError("only re-entry resolution may use related_event_id")
        for key, expected_type in (("source_child_event_id", "child_message"), ("source_llm_response_event_id", "llm_response")):
            if key not in data:
                continue
            source = connection.execute("SELECT workflow_id, event_type FROM workflow_log_events WHERE event_id = ?", (data[key],)).fetchone()
            if source is None or source["workflow_id"] != workflow_id or source["event_type"] != expected_type:
                raise RepositoryError(f"{key} does not reference a prior event in this workflow")


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise RepositoryError(f"{label} must be non-empty")
    return value
