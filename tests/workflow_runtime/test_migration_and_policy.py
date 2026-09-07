from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "runtime"))

from v3_workflow.persistence.migration import MigrationError, migrate_dialogue_target_identity_schema, migrate_entry_database, migrate_model_turn_event_schema, migrate_phrase_review_context_schema
from v3_workflow.persistence.repository import RepositoryError, canonical_json
from v3_workflow.persistence.v1_active_import import V1ActiveImportError, import_v1_active_materials, preview_v1_active_materials
from v3_workflow.policy.review_schedule import PolicyError, ReviewSchedulePolicy


LEGACY_DDL = """
CREATE TABLE entry_workflows(entry_workflow_id TEXT PRIMARY KEY, external_session_id TEXT, phase TEXT NOT NULL, started_at TEXT NOT NULL, closed_at TEXT);
CREATE TABLE entry_log_events(event_id TEXT PRIMARY KEY, entry_workflow_id TEXT NOT NULL, sequence_no INTEGER NOT NULL, event_type TEXT NOT NULL, related_event_id TEXT, payload_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE learning_materials(material_id TEXT PRIMARY KEY, entry_workflow_id TEXT NOT NULL, status TEXT NOT NULL, title TEXT NOT NULL, language TEXT NOT NULL, unit_type TEXT NOT NULL, reference_text TEXT NOT NULL, needs_parent_review INTEGER NOT NULL, audit_result_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE learning_items(learning_item_id TEXT PRIMARY KEY, material_id TEXT NOT NULL, item_order INTEGER NOT NULL, reference_text TEXT NOT NULL, meaning_zh TEXT, mastery TEXT, next_review_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
"""


class MigrationAndPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "legacy.sqlite3"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _legacy(self, session: str | None = "child-1") -> None:
        payload = json.dumps({"contract_name": "dada.entry_log_event", "contract_version": 1, "data": {"text": "开始录入"}}, ensure_ascii=False, separators=(",", ":"))
        with sqlite3.connect(self.path) as connection:
            connection.executescript(LEGACY_DDL)
            connection.execute("INSERT INTO entry_workflows VALUES ('entry-1', ?, 'closed', '2026-08-22T00:00:00Z', '2026-08-22T00:01:00Z')", (session,))
            connection.execute("INSERT INTO entry_log_events VALUES ('event-1', 'entry-1', 1, 'child_message', NULL, ?, '2026-08-22T00:00:00Z')", (payload,))
            connection.execute("INSERT INTO learning_materials VALUES ('material-1', 'entry-1', 'active', 'title', 'en', 'sentence', 'I go.', 0, '{}', '2026-08-22T00:00:00Z', '2026-08-22T00:00:00Z')")
            connection.execute("INSERT INTO learning_items VALUES ('item-1', 'material-1', 1, 'I go.', '我去。', 'pending_first_review', '2026-08-22T01:00:00Z', '2026-08-22T00:00:00Z', '2026-08-22T00:00:00Z')")

    def test_migration_preserves_entry_ids_event_payload_and_source_chain(self) -> None:
        self._legacy()
        with sqlite3.connect(self.path) as connection:
            before = connection.execute("SELECT event_id, entry_workflow_id, sequence_no, event_type, related_event_id, payload_json, created_at FROM entry_log_events").fetchone()
        migrate_entry_database(self.path)
        with sqlite3.connect(self.path) as connection:
            workflow = connection.execute("SELECT workflow_id, external_session_id, workflow_type, phase, started_at, closed_at FROM workflows").fetchone()
            after = connection.execute("SELECT event_id, workflow_id, sequence_no, event_type, related_event_id, payload_json, created_at FROM workflow_log_events").fetchone()
            material = connection.execute("SELECT source_workflow_id FROM learning_materials WHERE material_id = 'material-1'").fetchone()
            item = connection.execute("SELECT review_stage, next_review_at, completed_at, revision FROM learning_items WHERE learning_item_id = 'item-1'").fetchone()
        self.assertEqual(workflow, ('entry-1', 'child-1', 'entry', 'closed', '2026-08-22T00:00:00Z', '2026-08-22T00:01:00Z'))
        self.assertEqual(after, (before[0], before[1], before[2], before[3], before[4], before[5], before[6]))
        self.assertEqual(material, ('entry-1',))
        self.assertEqual(item, (0, '2026-08-22T01:00:00Z', None, 1))
        with self.assertRaises(MigrationError):
            migrate_entry_database(self.path)

    def test_invalid_session_rejects_without_target_tables_or_rename(self) -> None:
        self._legacy(None)
        with self.assertRaises(MigrationError):
            migrate_entry_database(self.path)
        with sqlite3.connect(self.path) as connection:
            names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertIn('learning_materials', names)
        self.assertNotIn('legacy_learning_materials', names)
        self.assertNotIn('workflows', names)

    def test_model_turn_event_migration_preserves_existing_events_and_is_explicit(self) -> None:
        old_ddl = """
        CREATE TABLE workflows(workflow_id TEXT PRIMARY KEY);
        CREATE TABLE workflow_log_events(
          event_id TEXT PRIMARY KEY, workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id),
          sequence_no INTEGER NOT NULL, event_type TEXT NOT NULL CHECK(event_type IN ('child_message','assistant_response')),
          related_event_id TEXT REFERENCES workflow_log_events(event_id), payload_json TEXT NOT NULL,
          created_at TEXT NOT NULL, UNIQUE(workflow_id, sequence_no),
          CHECK ((event_type = 'reentry_resolved' AND related_event_id IS NOT NULL) OR (event_type <> 'reentry_resolved' AND related_event_id IS NULL))
        );
        CREATE INDEX workflow_log_events_by_workflow ON workflow_log_events(workflow_id, sequence_no);
        """
        with sqlite3.connect(self.path) as connection:
            connection.executescript(old_ddl)
            connection.execute("INSERT INTO workflows VALUES ('workflow-1')")
            connection.execute("INSERT INTO workflow_log_events VALUES ('event-1','workflow-1',1,'child_message',NULL,?,?)", (json.dumps({"data": {"text": "x"}}), "2026-08-22T00:00:00Z"))
        migrate_model_turn_event_schema(self.path)
        with sqlite3.connect(self.path) as connection:
            row = connection.execute("SELECT event_id, workflow_id, sequence_no, event_type FROM workflow_log_events").fetchone()
            sql = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='workflow_log_events'").fetchone()[0]
            connection.execute("INSERT INTO workflow_log_events VALUES ('event-2','workflow-1',2,'model_turn_attempt_failed',NULL,?,?)", (json.dumps({"data": {"source_child_event_id": "event-1", "phase": "gateway", "attempt": 1}}), "2026-08-22T00:00:01Z"))
        self.assertEqual(row, ('event-1', 'workflow-1', 1, 'child_message'))
        self.assertIn('model_turn_attempt_failed', sql)
        with self.assertRaises(MigrationError):
            migrate_model_turn_event_schema(self.path)

    def test_phrase_review_context_schema_migration_preserves_existing_items(self) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute("CREATE TABLE learning_items(learning_item_id TEXT PRIMARY KEY, material_id TEXT NOT NULL, item_order INTEGER NOT NULL, reference_text TEXT NOT NULL, meaning_zh TEXT, review_stage INTEGER, next_review_at TEXT, completed_at TEXT, revision INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)")
            connection.execute("INSERT INTO learning_items VALUES ('item', 'material', 1, 'go to school', '去上学', 0, '2026-08-23T00:00:00Z', NULL, 1, '2026-08-23T00:00:00Z', '2026-08-23T00:00:00Z')")
        migrate_phrase_review_context_schema(self.path)
        with sqlite3.connect(self.path) as connection:
            columns = {row[1] for row in connection.execute("PRAGMA table_info(learning_items)")}
            row = connection.execute("SELECT reference_text, meaning_zh, review_context_json FROM learning_items").fetchone()
        self.assertIn("review_context_json", columns)
        self.assertEqual(row, ("go to school", "去上学", None))
        with self.assertRaises(MigrationError):
            migrate_phrase_review_context_schema(self.path)

    def test_dialogue_target_identity_migration_preserves_events(self) -> None:
        from v3_workflow.persistence.schema import DDL

        with sqlite3.connect(self.path) as connection:
            legacy_round_ddl = DDL.replace(",\n    'dialogue_target_reopened'", "")
            connection.executescript(legacy_round_ddl)
            connection.execute("INSERT INTO workflows(workflow_id, external_session_id, workflow_type, phase, started_at, closed_at) VALUES ('dialogue', 'child', 'dialogue', 'closed', '2026-08-23T00:00:00Z', '2026-08-23T00:01:00Z')")
            connection.execute("INSERT INTO workflow_log_events(event_id, workflow_id, sequence_no, event_type, payload_json, created_at) VALUES ('event', 'dialogue', 1, 'system_prompt', ?, '2026-08-23T00:00:00Z')", (json.dumps({"contract_name": "dada.workflow_log_event", "contract_version": 1, "data": {"text": "x"}}),))
        migrate_dialogue_target_identity_schema(self.path)
        with sqlite3.connect(self.path) as connection:
            sql = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='workflow_log_events'").fetchone()[0]
            row = connection.execute("SELECT event_id, event_type FROM workflow_log_events").fetchone()
        self.assertIn("dialogue_target_reopened", sql)
        self.assertEqual(row, ("event", "system_prompt"))
        with self.assertRaises(MigrationError):
            migrate_dialogue_target_identity_schema(self.path)

    def test_policy_decides_from_accuracy_and_rejects_overlap(self) -> None:
        policy = ReviewSchedulePolicy.from_mapping(
            {
                "policy_id": "test", "policy_version": 1, "initial_stage": 0,
                "stages": [{"stage": 0, "interval_seconds": 60}, {"stage": 1, "interval_seconds": 120}],
                "accuracy_bands": [{"minimum": 0.0, "maximum": 0.8, "action": "reset_to_initial"}, {"minimum": 0.8, "maximum": 1.0, "action": "advance"}],
                "archive_after_stage": 1,
            }
        )
        self.assertEqual(policy.decide(0, 0.8, '2026-08-22T00:00:00Z').next_review_at_after, '2026-08-22T00:01:00Z')
        self.assertTrue(policy.decide(1, 1.0, '2026-08-22T00:00:00Z').archive)
        with self.assertRaises(PolicyError):
            ReviewSchedulePolicy.from_mapping(
                {"policy_id": "bad", "policy_version": 1, "initial_stage": 0, "stages": [{"stage": 0, "interval_seconds": 1}],
                 "accuracy_bands": [{"minimum": 0.0, "maximum": 0.8, "action": "advance"}, {"minimum": 0.7, "maximum": 1.0, "action": "advance"}], "archive_after_stage": 0}
            )

    def test_formal_policy_has_configured_1h_3h_5d_30d_progression(self) -> None:
        policy = ReviewSchedulePolicy.from_file(PROJECT / "config" / "review-schedule.json")
        at = '2026-08-22T00:00:00Z'
        self.assertEqual(policy.initial_due_at(at), '2026-08-22T01:00:00Z')
        self.assertEqual(policy.decide(0, 0.95, at).next_review_at_after, '2026-08-22T01:00:00Z')
        self.assertEqual(policy.decide(1, 0.95, at).next_review_at_after, '2026-08-22T03:00:00Z')
        self.assertEqual(policy.decide(2, 0.95, at).next_review_at_after, '2026-08-27T00:00:00Z')
        self.assertEqual(policy.decide(3, 0.95, at).next_review_at_after, '2026-09-21T00:00:00Z')
        self.assertTrue(policy.decide(4, 0.95, at).archive)

    def test_credential_shaped_keys_cannot_enter_new_event_payload(self) -> None:
        with self.assertRaises(RepositoryError):
            canonical_json({"request": {"api_key": "not-a-fixture"}})

    def test_v1_active_material_import_is_atomic_and_defaults_items_due_now(self) -> None:
        source = Path(self.temp.name) / "v1" / "active" / "materials"
        source.mkdir(parents=True)
        source.joinpath("material.json").write_text(json.dumps({
            "material_id": "eng-v1-one", "material_status": "active", "title": "title", "language": "en", "unit_type": "sentence", "reference_text": "I go.",
            "revision": 9, "audit_confidence": "high", "audit_changes": [], "semantic_duplicate_review": {"source": "llm_semantic_review", "decision": "no_semantic_duplicate", "reason": "ok"},
            "learning_items": [{"item_id": "s01", "reference": "I go.", "meaning_zh": "我去。"}], "created_at": "2026-08-22T00:00:00Z", "updated_at": "2026-08-22T00:00:00Z",
        }, ensure_ascii=False), encoding="utf-8")
        preview = preview_v1_active_materials(source.parent.parent)
        self.assertEqual((preview.source_material_count, preview.source_item_count), (1, 1))
        report = import_v1_active_materials(source.parent.parent, self.path, "child_0123456789abcdef", "2026-08-23T00:00:00Z")
        self.assertEqual((report.imported_material_count, report.imported_item_count), (1, 1))
        with sqlite3.connect(self.path) as connection:
            self.assertEqual(connection.execute("SELECT status, source_workflow_id FROM learning_materials").fetchone()[0], "active")
            self.assertEqual(connection.execute("SELECT review_stage, next_review_at, revision FROM learning_items").fetchone(), (0, "2026-08-23T00:00:00Z", 1))
        with self.assertRaises(V1ActiveImportError):
            import_v1_active_materials(source.parent.parent, self.path, "child_0123456789abcdef", "2026-08-23T00:01:00Z")


if __name__ == '__main__':
    unittest.main()
