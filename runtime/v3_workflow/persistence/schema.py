"""Target v3 workflow-table DDL; LangGraph owns separate checkpoint tables."""

from __future__ import annotations

import sqlite3


DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS workflows (
  workflow_id TEXT PRIMARY KEY,
  external_session_id TEXT NOT NULL,
  workflow_type TEXT NOT NULL CHECK (workflow_type IN ('entry', 'review')),
  phase TEXT NOT NULL CHECK (phase IN ('active', 'paused_for_entry', 'closed')),
  learning_item_id TEXT REFERENCES learning_items(learning_item_id),
  question_sequence INTEGER NOT NULL DEFAULT 0 CHECK (question_sequence >= 0),
  locked_question_mode TEXT,
  locked_question_json TEXT CHECK (locked_question_json IS NULL OR json_valid(locked_question_json)),
  locked_item_revision INTEGER CHECK (locked_item_revision IS NULL OR locked_item_revision > 0),
  locked_at TEXT,
  started_at TEXT NOT NULL,
  paused_at TEXT,
  closed_at TEXT,
  CHECK ((workflow_type = 'entry' AND learning_item_id IS NULL) OR (workflow_type = 'review' AND learning_item_id IS NOT NULL)),
  CHECK (workflow_type = 'review' OR (question_sequence = 0 AND locked_question_mode IS NULL AND locked_question_json IS NULL AND locked_item_revision IS NULL AND locked_at IS NULL)),
  CHECK ((locked_question_mode IS NULL AND locked_question_json IS NULL AND locked_item_revision IS NULL AND locked_at IS NULL) OR (locked_question_mode IS NOT NULL AND locked_question_json IS NOT NULL AND locked_item_revision IS NOT NULL AND locked_at IS NOT NULL)),
  CHECK (locked_question_mode IS NULL OR question_sequence > 0),
  CHECK ((phase = 'active' AND paused_at IS NULL AND closed_at IS NULL) OR (phase = 'paused_for_entry' AND workflow_type = 'review' AND paused_at IS NOT NULL AND closed_at IS NULL) OR (phase = 'closed' AND closed_at IS NOT NULL AND locked_question_mode IS NULL AND locked_question_json IS NULL AND locked_item_revision IS NULL AND locked_at IS NULL))
);

CREATE TABLE IF NOT EXISTS workflow_log_events (
  event_id TEXT PRIMARY KEY,
  workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id),
  sequence_no INTEGER NOT NULL CHECK (sequence_no > 0),
  event_type TEXT NOT NULL CHECK (event_type IN (
    'system_prompt', 'child_message', 'llm_request', 'internal_reasoning',
    'internal_reasoning_unavailable', 'tool_call', 'tool_result', 'llm_response',
    'model_turn_attempt_failed',
    'assistant_response', 'state_transition', 'reentry_requested', 'reentry_resolved',
    'question_locked', 'question_released', 'schedule_applied', 'material_archived'
  )),
  related_event_id TEXT REFERENCES workflow_log_events(event_id),
  payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
  created_at TEXT NOT NULL,
  UNIQUE (workflow_id, sequence_no),
  CHECK ((event_type = 'reentry_resolved' AND related_event_id IS NOT NULL) OR (event_type <> 'reentry_resolved' AND related_event_id IS NULL))
);

CREATE TABLE IF NOT EXISTS learning_materials (
  material_id TEXT PRIMARY KEY,
  source_workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id),
  status TEXT NOT NULL CHECK (status IN ('draft', 'active', 'archived', 'superseded')),
  title TEXT NOT NULL,
  language TEXT NOT NULL CHECK (language = 'en'),
  unit_type TEXT NOT NULL CHECK (unit_type IN ('word', 'phrase', 'sentence')),
  reference_text TEXT NOT NULL,
  needs_parent_review INTEGER NOT NULL CHECK (needs_parent_review IN (0, 1)),
  audit_result_json TEXT NOT NULL CHECK (json_valid(audit_result_json)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS learning_items (
  learning_item_id TEXT PRIMARY KEY,
  material_id TEXT NOT NULL REFERENCES learning_materials(material_id),
  item_order INTEGER NOT NULL CHECK (item_order > 0),
  reference_text TEXT NOT NULL,
  meaning_zh TEXT,
  review_stage INTEGER CHECK (review_stage >= 0),
  next_review_at TEXT,
  completed_at TEXT,
  revision INTEGER NOT NULL DEFAULT 1 CHECK (revision > 0),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (material_id, item_order),
  CHECK ((review_stage IS NULL AND next_review_at IS NULL AND completed_at IS NULL) OR (review_stage IS NOT NULL AND next_review_at IS NOT NULL AND completed_at IS NULL) OR (review_stage IS NOT NULL AND next_review_at IS NULL AND completed_at IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS review_queue_items (
  workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id),
  queue_position INTEGER NOT NULL CHECK (queue_position > 0),
  learning_item_id TEXT NOT NULL REFERENCES learning_items(learning_item_id),
  item_revision_at_start INTEGER NOT NULL CHECK (item_revision_at_start > 0),
  status TEXT NOT NULL CHECK (status IN ('pending', 'locked', 'completed')),
  completed_at TEXT,
  PRIMARY KEY (workflow_id, queue_position),
  UNIQUE (workflow_id, learning_item_id),
  CHECK ((status = 'completed' AND completed_at IS NOT NULL) OR (status <> 'completed' AND completed_at IS NULL))
);

CREATE UNIQUE INDEX IF NOT EXISTS one_active_workflow_per_session
  ON workflows(external_session_id) WHERE phase = 'active';
CREATE UNIQUE INDEX IF NOT EXISTS one_paused_review_per_session
  ON workflows(external_session_id) WHERE workflow_type = 'review' AND phase = 'paused_for_entry';
CREATE INDEX IF NOT EXISTS workflows_by_session_phase ON workflows(external_session_id, phase);
CREATE INDEX IF NOT EXISTS workflows_by_learning_item ON workflows(learning_item_id, started_at) WHERE workflow_type = 'review';
CREATE INDEX IF NOT EXISTS workflow_log_events_by_workflow ON workflow_log_events(workflow_id, sequence_no);
CREATE INDEX IF NOT EXISTS workflow_learning_materials_by_status ON learning_materials(status, created_at);
CREATE INDEX IF NOT EXISTS workflow_learning_items_due ON learning_items(next_review_at) WHERE review_stage IS NOT NULL AND completed_at IS NULL;
CREATE INDEX IF NOT EXISTS review_queue_items_by_workflow_status ON review_queue_items(workflow_id, status, queue_position);
"""


def initialize_schema(connection: sqlite3.Connection) -> None:
    """Create only the target tables for a new or already migrated database."""

    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(DDL)
    # v3 before queue-based review stored one current learning item directly
    # on each active/paused workflow. Preserve that in-flight fact as a
    # one-item queue rather than changing or deleting the existing workflow.
    connection.execute(
        """INSERT OR IGNORE INTO review_queue_items(
             workflow_id, queue_position, learning_item_id, item_revision_at_start, status, completed_at
           )
           SELECT workflow.workflow_id, 1, workflow.learning_item_id,
                  COALESCE(workflow.locked_item_revision, item.revision),
                  CASE WHEN workflow.locked_question_mode IS NULL THEN 'pending' ELSE 'locked' END,
                  NULL
           FROM workflows AS workflow JOIN learning_items AS item ON item.learning_item_id = workflow.learning_item_id
           WHERE workflow.workflow_type = 'review' AND workflow.phase IN ('active', 'paused_for_entry')"""
    )
