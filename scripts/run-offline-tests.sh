#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

if [[ ! -x .venv/bin/python ]]; then
  echo "missing .venv/bin/python; create a virtual environment and run .venv/bin/pip install -e ." >&2
  exit 2
fi

.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/test_coach_live_catalog.mjs tests/test_inbound_v3_workflow_hook.mjs tests/test_review_openclaw_replay.mjs
node --check runtime/inbound_v3_workflow_hook/index.mjs
