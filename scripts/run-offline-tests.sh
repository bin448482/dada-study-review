#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

source "$project_root/scripts/workspace.sh"
require_workspace

"$dada_python_bin" -m unittest discover -s tests -p 'test_*.py' -v
node --test tests/test_coach_live_catalog.mjs tests/test_inbound_v3_workflow_hook.mjs tests/test_review_openclaw_replay.mjs
node --check runtime/inbound_v3_workflow_hook/index.mjs
