"""Explicit, atomic no-loss migration from the implemented entry-only schema."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from .schema import initialize_schema


class MigrationError(RuntimeError):
    """Legacy facts cannot be migrated without loss or unsafe inference."""


LEGACY_TABLES = ("entry_workflows", "entry_log_events", "learning_materials", "learning_items")


def migrate_model_turn_event_schema(database_path: Path) -> None:
    """Explicitly add the model-turn failure audit event to one v3 archive.

    SQLite cannot alter this table's event-type CHECK constraint in place. This
    operation is intentionally never called by `initialize_schema()` or a
    service constructor: production archive migration remains an authorized
    maintenance action.
    """

    path = Path(database_path)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        _preflight_model_turn_event_schema(connection)
        before_count = int(connection.execute("SELECT COUNT(*) FROM workflow_log_events").fetchone()[0])
        before_sequences = [tuple(row) for row in connection.execute(
            "SELECT workflow_id, COUNT(*), MIN(sequence_no), MAX(sequence_no) FROM workflow_log_events GROUP BY workflow_id"
        )]
        connection.execute(
            """CREATE TABLE workflow_log_events_model_turn_new (
              event_id TEXT PRIMARY KEY,
              workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id),
              sequence_no INTEGER NOT NULL CHECK (sequence_no > 0),
              event_type TEXT NOT NULL CHECK (event_type IN (
                'system_prompt', 'child_message', 'llm_request', 'internal_reasoning',
                'internal_reasoning_unavailable', 'tool_call', 'tool_result', 'llm_response',
                'model_turn_attempt_failed', 'assistant_response', 'state_transition',
                'reentry_requested', 'reentry_resolved', 'question_locked', 'question_released',
                'schedule_applied', 'material_archived'
              )),
              related_event_id TEXT REFERENCES workflow_log_events_model_turn_new(event_id),
              payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
              created_at TEXT NOT NULL,
              UNIQUE (workflow_id, sequence_no),
              CHECK ((event_type = 'reentry_resolved' AND related_event_id IS NOT NULL) OR
                     (event_type <> 'reentry_resolved' AND related_event_id IS NULL))
            )"""
        )
        connection.execute(
            """INSERT INTO workflow_log_events_model_turn_new(
                 event_id, workflow_id, sequence_no, event_type, related_event_id, payload_json, created_at
               ) SELECT event_id, workflow_id, sequence_no, event_type, related_event_id, payload_json, created_at
                 FROM workflow_log_events ORDER BY workflow_id, sequence_no"""
        )
        connection.execute("DROP TABLE workflow_log_events")
        connection.execute("ALTER TABLE workflow_log_events_model_turn_new RENAME TO workflow_log_events")
        connection.execute("CREATE INDEX workflow_log_events_by_workflow ON workflow_log_events(workflow_id, sequence_no)")
        after_count = int(connection.execute("SELECT COUNT(*) FROM workflow_log_events").fetchone()[0])
        after_sequences = [tuple(row) for row in connection.execute(
            "SELECT workflow_id, COUNT(*), MIN(sequence_no), MAX(sequence_no) FROM workflow_log_events GROUP BY workflow_id"
        )]
        if after_count != before_count or after_sequences != before_sequences:
            raise MigrationError("model-turn event migration did not preserve events")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise MigrationError("model-turn event migration introduced a foreign-key violation")
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise MigrationError("model-turn event migration failed integrity check")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _preflight_model_turn_event_schema(connection: sqlite3.Connection) -> None:
    row = connection.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'workflow_log_events'"
    ).fetchone()
    if row is None:
        raise MigrationError("v3 workflow event table is missing")
    sql = str(row[0] or "")
    if "model_turn_attempt_failed" in sql:
        raise MigrationError("model-turn event schema is already migrated")
    if "workflow_log_events" not in sql or "event_type" not in sql:
        raise MigrationError("workflow event table is not a supported v3 schema")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise MigrationError("v3 archive has foreign-key violations")
    integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        raise MigrationError("v3 archive failed integrity check")


def migrate_entry_database(database_path: Path) -> None:
    """Migrate one explicitly selected SQLite copy, or roll the whole operation back."""

    path = Path(database_path)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        _require_legacy_shape(connection)
        _preflight(connection)
        connection.execute("ALTER TABLE learning_items RENAME TO legacy_learning_items")
        connection.execute("ALTER TABLE learning_materials RENAME TO legacy_learning_materials")
        initialize_schema(connection)
        connection.execute(
            """INSERT INTO workflows(workflow_id, external_session_id, workflow_type, phase, learning_item_id,
               question_sequence, locked_question_mode, locked_question_json, locked_item_revision, locked_at,
               started_at, paused_at, closed_at)
               SELECT entry_workflow_id, external_session_id, 'entry',
                 CASE phase WHEN 'collecting' THEN 'active' WHEN 'closed' THEN 'closed' END,
                 NULL, 0, NULL, NULL, NULL, NULL, started_at, NULL, closed_at
               FROM entry_workflows"""
        )
        connection.execute(
            """INSERT INTO workflow_log_events(event_id, workflow_id, sequence_no, event_type, related_event_id, payload_json, created_at)
               SELECT event_id, entry_workflow_id, sequence_no, event_type, related_event_id, payload_json, created_at
               FROM entry_log_events"""
        )
        connection.execute(
            """INSERT INTO learning_materials(material_id, source_workflow_id, status, title, language, unit_type,
               reference_text, needs_parent_review, audit_result_json, created_at, updated_at)
               SELECT material_id, entry_workflow_id, status, title, language, unit_type, reference_text,
                 needs_parent_review, audit_result_json, created_at, updated_at
               FROM legacy_learning_materials"""
        )
        connection.execute(
            """INSERT INTO learning_items(learning_item_id, material_id, item_order, reference_text, meaning_zh,
               review_stage, next_review_at, completed_at, revision, created_at, updated_at)
               SELECT learning_item_id, material_id, item_order, reference_text, meaning_zh,
                 CASE WHEN mastery IS NULL THEN NULL ELSE 0 END,
                 CASE WHEN mastery IN ('completed') OR mastery IS NULL THEN NULL ELSE next_review_at END,
                 CASE WHEN mastery = 'completed' THEN updated_at ELSE NULL END,
                 1, created_at, updated_at
               FROM legacy_learning_items"""
        )
        _verify_copy(connection)
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()


def _require_legacy_shape(connection: sqlite3.Connection) -> None:
    names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "workflows" in names or "workflow_log_events" in names or "legacy_learning_materials" in names:
        raise MigrationError("database is already migrated or has a partial migration")
    missing = set(LEGACY_TABLES) - names
    if missing:
        raise MigrationError("legacy entry schema is incomplete")


def _preflight(connection: sqlite3.Connection) -> None:
    invalid_session = connection.execute(
        "SELECT 1 FROM entry_workflows WHERE external_session_id IS NULL OR external_session_id = '' LIMIT 1"
    ).fetchone()
    if invalid_session is not None:
        raise MigrationError("legacy workflow has no explicit external session")
    duplicate = connection.execute(
        """SELECT 1 FROM entry_workflows WHERE phase = 'collecting'
           GROUP BY external_session_id HAVING COUNT(*) > 1 LIMIT 1"""
    ).fetchone()
    if duplicate is not None:
        raise MigrationError("legacy database has duplicate collecting workflows")
    dangling = connection.execute(
        """SELECT 1 FROM learning_materials AS material
           LEFT JOIN entry_workflows AS workflow ON workflow.entry_workflow_id = material.entry_workflow_id
           WHERE workflow.entry_workflow_id IS NULL LIMIT 1"""
    ).fetchone()
    if dangling is not None:
        raise MigrationError("legacy material has no source workflow")
    bad_json = connection.execute("SELECT 1 FROM entry_log_events WHERE json_valid(payload_json) = 0 LIMIT 1").fetchone()
    if bad_json is not None:
        raise MigrationError("legacy event payload is not valid JSON")
    broken_event = connection.execute(
        """SELECT 1 FROM entry_log_events AS event
           LEFT JOIN entry_log_events AS related ON related.event_id = event.related_event_id
           WHERE (event.event_type = 'reentry_resolved' AND (related.event_id IS NULL OR related.entry_workflow_id <> event.entry_workflow_id))
              OR (event.event_type <> 'reentry_resolved' AND event.related_event_id IS NOT NULL)
           LIMIT 1"""
    ).fetchone()
    if broken_event is not None:
        raise MigrationError("legacy event relation is invalid")


def _verify_copy(connection: sqlite3.Connection) -> None:
    pairs = (
        ("entry_workflows", "workflows", "entry_workflow_id", "workflow_id"),
        ("entry_log_events", "workflow_log_events", "event_id", "event_id"),
        ("legacy_learning_materials", "learning_materials", "material_id", "material_id"),
        ("legacy_learning_items", "learning_items", "learning_item_id", "learning_item_id"),
    )
    for source, target, source_id, target_id in pairs:
        source_count = connection.execute(f"SELECT COUNT(*) FROM {source}").fetchone()[0]
        target_count = connection.execute(f"SELECT COUNT(*) FROM {target}").fetchone()[0]
        if source_count != target_count:
            raise MigrationError(f"{source} copy count differs")
        missing = connection.execute(
            f"SELECT 1 FROM {source} AS source LEFT JOIN {target} AS target ON target.{target_id} = source.{source_id} WHERE target.{target_id} IS NULL LIMIT 1"
        ).fetchone()
        if missing is not None:
            raise MigrationError(f"{source} copy is incomplete")
    for row in connection.execute(
        """SELECT old.event_id, old.entry_workflow_id, old.sequence_no, old.event_type, old.related_event_id,
                  old.payload_json, old.created_at, new.workflow_id, new.sequence_no AS new_sequence_no,
                  new.event_type AS new_event_type, new.related_event_id AS new_related_event_id,
                  new.payload_json AS new_payload_json, new.created_at AS new_created_at
           FROM entry_log_events AS old JOIN workflow_log_events AS new ON new.event_id = old.event_id"""
    ):
        if (
            row["entry_workflow_id"] != row["workflow_id"]
            or row["sequence_no"] != row["new_sequence_no"]
            or row["event_type"] != row["new_event_type"]
            or row["related_event_id"] != row["new_related_event_id"]
            or row["payload_json"] != row["new_payload_json"]
            or row["created_at"] != row["new_created_at"]
        ):
            raise MigrationError("legacy event changed during copy")
        # Parse after equality verification to make the preserved payload explicit.
        json.loads(row["payload_json"])
