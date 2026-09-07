from __future__ import annotations

from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "runtime"))

from v3_workflow.persistence.repository import RepositoryError, WorkflowRepository


class AdminArchiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "archive.sqlite3"
        self.repository = WorkflowRepository(self.path)
        self.repository.initialize()
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "INSERT INTO workflows(workflow_id, external_session_id, workflow_type, phase, started_at, closed_at) VALUES ('entry', 'child-1', 'entry', 'closed', '2026-08-31T00:00:00Z', '2026-08-31T00:01:00Z')"
            )
            connection.execute(
                "INSERT INTO learning_materials(material_id, source_workflow_id, status, title, language, unit_type, reference_text, needs_parent_review, audit_result_json, created_at, updated_at) VALUES ('material', 'entry', 'active', 'title', 'en', 'sentence', 'I go.', 0, '{}', '2026-08-31T00:00:00Z', '2026-08-31T00:00:00Z')"
            )
            connection.execute(
                "INSERT INTO learning_items(learning_item_id, material_id, item_order, reference_text, meaning_zh, review_stage, next_review_at, completed_at, revision, created_at, updated_at) VALUES ('item', 'material', 1, 'I go.', '我去。', 0, '2026-08-31T01:00:00Z', NULL, 1, '2026-08-31T00:00:00Z', '2026-08-31T00:00:00Z')"
            )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_admin_archive_preserves_item_and_writes_audit_event(self) -> None:
        result = self.repository.admin_archive_learning_item("child-1", "item", "duplicate entry", "2026-08-31T02:00:00Z")
        self.assertEqual(result["learning_item_id"], "item")
        with sqlite3.connect(self.path) as connection:
            item = connection.execute("SELECT review_stage, next_review_at, completed_at, revision FROM learning_items").fetchone()
            material = connection.execute("SELECT status FROM learning_materials").fetchone()
            event = connection.execute("SELECT event_type, payload_json FROM workflow_log_events").fetchone()
        self.assertEqual(item, (0, None, "2026-08-31T02:00:00Z", 2))
        self.assertEqual(material, ("archived",))
        self.assertEqual(event[0], "material_archived")
        self.assertIn('"archive_mode":"admin"', event[1])
        self.assertIn('"archive_scope":"material"', event[1])
        self.assertIn('"reason":"duplicate entry"', event[1])

    def test_admin_archive_rejects_in_progress_review(self) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "INSERT INTO workflows(workflow_id, external_session_id, workflow_type, phase, learning_item_id, started_at) VALUES ('review', 'child-1', 'review', 'active', 'item', '2026-08-31T01:00:00Z')"
            )
            connection.execute(
                "INSERT INTO review_queue_items(workflow_id, queue_position, learning_item_id, item_revision_at_start, status) VALUES ('review', 1, 'item', 1, 'pending')"
            )
        with self.assertRaisesRegex(RepositoryError, "in-progress review"):
            self.repository.admin_archive_learning_item("child-1", "item", "duplicate entry", "2026-08-31T02:00:00Z")

    def test_admin_archive_keeps_multi_item_material_active(self) -> None:
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "INSERT INTO learning_items(learning_item_id, material_id, item_order, reference_text, meaning_zh, review_stage, next_review_at, completed_at, revision, created_at, updated_at) VALUES ('item-2', 'material', 2, 'You go.', '你去。', 0, '2026-08-31T01:00:00Z', NULL, 1, '2026-08-31T00:00:00Z', '2026-08-31T00:00:00Z')"
            )
        self.repository.admin_archive_learning_item("child-1", "item", "duplicate entry", "2026-08-31T02:00:00Z")
        with sqlite3.connect(self.path) as connection:
            item = connection.execute("SELECT completed_at FROM learning_items WHERE learning_item_id = 'item'").fetchone()
            material = connection.execute("SELECT status FROM learning_materials").fetchone()
            event = connection.execute("SELECT payload_json FROM workflow_log_events").fetchone()
        self.assertEqual(item, ("2026-08-31T02:00:00Z",))
        self.assertEqual(material, ("active",))
        self.assertIn('"archive_scope":"learning_item"', event[0])


if __name__ == "__main__":
    unittest.main()
