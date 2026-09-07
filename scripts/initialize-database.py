#!/usr/bin/env python3
"""Create or verify a Dada v3 SQLite archive and its Graph checkpoint tables."""

from __future__ import annotations

import argparse
from pathlib import Path
import os
import sqlite3
import sys
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runtime"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: E402
from v3_workflow.persistence.repository import WorkflowRepository  # noqa: E402
from workspace_config import load_config, resolve  # noqa: E402


BUSINESS_TABLES = (
    "workflows",
    "workflow_log_events",
    "learning_materials",
    "learning_items",
    "review_queue_items",
    "dialogue_workflow_state",
    "dialogue_round_plans",
    "dialogue_review_batches",
    "dialogue_review_batch_items",
)
CHECKPOINT_TABLES = ("checkpoints", "writes")


def default_database_path() -> Path:
    archive_root = os.environ.get("DADA_ARCHIVE_ROOT")
    if archive_root:
        return Path(archive_root).expanduser() / "workflow-v3.sqlite3"
    workspace = resolve(load_config(PROJECT_ROOT, None))
    return Path(workspace["DADA_WORKSPACE_ARCHIVE"]) / "workflow-v3.sqlite3"


def initialize_database(database_path: Path) -> dict[str, Any]:
    path = database_path.expanduser().resolve()
    WorkflowRepository(path).initialize()

    checkpoint_connection = sqlite3.connect(path)
    try:
        checkpoint_connection.execute("PRAGMA foreign_keys = ON")
        SqliteSaver(checkpoint_connection).setup()
        tables = {
            str(row[0])
            for row in checkpoint_connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        missing = [name for name in (*BUSINESS_TABLES, *CHECKPOINT_TABLES) if name not in tables]
        if missing:
            raise RuntimeError(f"database initialization is incomplete: {', '.join(missing)}")
        integrity = str(checkpoint_connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_key_violations = checkpoint_connection.execute("PRAGMA foreign_key_check").fetchall()
        if integrity != "ok" or foreign_key_violations:
            raise RuntimeError("database integrity verification failed")
    finally:
        checkpoint_connection.close()

    return {
        "database": str(path),
        "business_tables": len(BUSINESS_TABLES),
        "checkpoint_tables": len(CHECKPOINT_TABLES),
        "integrity_check": "ok",
        "foreign_key_violations": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database",
        type=Path,
        help="SQLite path; defaults to <workspace archive>/workflow-v3.sqlite3",
    )
    arguments = parser.parse_args()
    try:
        result = initialize_database(arguments.database or default_database_path())
    except (OSError, RuntimeError, sqlite3.Error, ValueError) as error:
        print(f"database initialization failed: {error}", file=sys.stderr)
        return 2
    print(
        "initialized {database} ({business_tables} business tables, "
        "{checkpoint_tables} checkpoint tables; integrity={integrity_check})".format(**result)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
