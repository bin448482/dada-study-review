"""Explicit, atomic import of reviewed v1 active materials into v3 defaults."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid5, NAMESPACE_URL

from .repository import RepositoryError, canonical_json, canonical_time
from .schema import initialize_schema


class V1ActiveImportError(RuntimeError): pass


@dataclass(frozen=True)
class V1ActiveImportReport:
    source_material_count: int
    source_item_count: int
    imported_material_count: int
    imported_item_count: int
    scope_token: str


def preview_v1_active_materials(source_directory: Path) -> V1ActiveImportReport:
    materials = _load_active_materials(source_directory)
    return V1ActiveImportReport(len(materials), sum(len(value["learning_items"]) for value in materials), 0, 0, "")


def import_v1_active_materials(source_directory: Path, database_path: Path, scope_token: str, imported_at: str) -> V1ActiveImportReport:
    """Import only active material/items; v1 source files are never modified."""

    if not isinstance(scope_token, str) or not scope_token.startswith("child_") or len(scope_token) < 16:
        raise V1ActiveImportError("scope token is invalid")
    timestamp = canonical_time(imported_at)
    materials = _load_active_materials(source_directory)
    path = Path(database_path); path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path); connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON"); connection.execute("BEGIN IMMEDIATE"); initialize_schema(connection)
        existing = connection.execute("SELECT 1 FROM learning_materials WHERE audit_result_json LIKE ? LIMIT 1", ('%"v1_source_material_id"%',)).fetchone()
        if existing is not None:
            raise V1ActiveImportError("target already contains a v1 active-material import")
        workflow_id = f"v1-active-import-{scope_token}"
        connection.execute("""INSERT INTO workflows(workflow_id, external_session_id, workflow_type, phase, learning_item_id, question_sequence, started_at, closed_at)
          VALUES (?, ?, 'entry', 'closed', NULL, 0, ?, ?)""", (workflow_id, scope_token, timestamp, timestamp))
        _event(connection, workflow_id, 1, "system_prompt", {"text": "v1 active material import"}, timestamp)
        _event(connection, workflow_id, 2, "state_transition", {"from_state": None, "to_state": "entry_collecting", "cause": "start"}, timestamp)
        _event(connection, workflow_id, 3, "state_transition", {"from_state": "entry_collecting", "to_state": None, "cause": "close"}, timestamp)
        item_count = 0
        for material in materials:
            material_id = material["material_id"]
            audit = {"contract_name": "dada.v1_active_material_import", "contract_version": 1, "data": {"v1_source_material_id": material_id, "v1_revision": material["revision"], "v1_material_audit": {"audit_confidence": material["audit_confidence"], "audit_changes": material["audit_changes"], "semantic_duplicate_review": material["semantic_duplicate_review"]}}}
            connection.execute("""INSERT INTO learning_materials(material_id, source_workflow_id, status, title, language, unit_type, reference_text, needs_parent_review, audit_result_json, created_at, updated_at)
              VALUES (?, ?, 'active', ?, 'en', ?, ?, 0, ?, ?, ?)""", (material_id, workflow_id, material["title"], material["unit_type"], material["reference_text"], canonical_json(audit), material["created_at"], material["updated_at"]))
            for order, item in enumerate(material["learning_items"], 1):
                item_id = "v1-" + uuid5(NAMESPACE_URL, f"{material_id}:{item['item_id']}").hex
                connection.execute("""INSERT INTO learning_items(learning_item_id, material_id, item_order, reference_text, meaning_zh, review_stage, next_review_at, completed_at, revision, created_at, updated_at)
                  VALUES (?, ?, ?, ?, ?, 0, ?, NULL, 1, ?, ?)""", (item_id, material_id, order, item["reference"], item["meaning_zh"], timestamp, material["created_at"], material["updated_at"]))
                item_count += 1
        _verify(connection, workflow_id, materials, item_count)
        connection.commit()
        return V1ActiveImportReport(len(materials), item_count, len(materials), item_count, scope_token)
    except BaseException:
        connection.rollback(); raise
    finally: connection.close()


def _load_active_materials(source_directory: Path) -> list[dict[str, Any]]:
    root = Path(source_directory).resolve(strict=True); directory = root / "active" / "materials"
    if not directory.is_dir(): raise V1ActiveImportError("v1 active materials directory is missing")
    values: list[dict[str, Any]] = []
    ids: set[str] = set()
    for path in sorted(directory.glob("*.json")):
        try: raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc: raise V1ActiveImportError("v1 material is unreadable") from exc
        required = {"material_id", "material_status", "title", "language", "unit_type", "reference_text", "revision", "audit_confidence", "audit_changes", "semantic_duplicate_review", "learning_items", "created_at", "updated_at"}
        if not isinstance(raw, dict) or not required <= set(raw) or raw["material_status"] != "active" or raw["language"] != "en" or raw["unit_type"] not in {"word", "phrase", "sentence"}:
            raise V1ActiveImportError("v1 material has unsupported shape")
        if not isinstance(raw["material_id"], str) or not raw["material_id"] or raw["material_id"] in ids or not isinstance(raw["learning_items"], list) or not raw["learning_items"]:
            raise V1ActiveImportError("v1 material identity or items are invalid")
        ids.add(raw["material_id"]); canonical_time(raw["created_at"]); canonical_time(raw["updated_at"])
        for item in raw["learning_items"]:
            if not isinstance(item, dict) or not all(isinstance(item.get(key), str) and item[key] for key in ("item_id", "reference")) or item.get("meaning_zh") is not None and not isinstance(item.get("meaning_zh"), str):
                raise V1ActiveImportError("v1 learning item is invalid")
        values.append(raw)
    if not values: raise V1ActiveImportError("v1 active materials are empty")
    return values


def _event(connection: sqlite3.Connection, workflow_id: str, sequence: int, event_type: str, data: dict[str, Any], created_at: str) -> None:
    event_id = "v1-import-event-" + str(sequence)
    envelope = {"contract_name": "dada.workflow_log_event", "contract_version": 1, "data": data}
    connection.execute("INSERT INTO workflow_log_events(event_id, workflow_id, sequence_no, event_type, related_event_id, payload_json, created_at) VALUES (?, ?, ?, ?, NULL, ?, ?)", (event_id, workflow_id, sequence, event_type, canonical_json(envelope), created_at))


def _verify(connection: sqlite3.Connection, workflow_id: str, materials: list[dict[str, Any]], item_count: int) -> None:
    if connection.execute("SELECT COUNT(*) FROM learning_materials WHERE source_workflow_id = ?", (workflow_id,)).fetchone()[0] != len(materials): raise V1ActiveImportError("material import count differs")
    if connection.execute("SELECT COUNT(*) FROM learning_items AS item JOIN learning_materials AS material ON material.material_id = item.material_id WHERE material.source_workflow_id = ?", (workflow_id,)).fetchone()[0] != item_count: raise V1ActiveImportError("item import count differs")
