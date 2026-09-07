#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=workspace.sh
source "$project_root/scripts/workspace.sh"

python3 "$project_root/scripts/workspace_config.py" --repository "$project_root" --create --shell >/dev/null

python3 -c 'import sys; assert sys.version_info >= (3, 12), sys.version' || {
  echo "Python 3.12 or newer is required" >&2
  exit 1
}
command -v node >/dev/null || { echo "Node.js is required" >&2; exit 1; }
command -v pnpm >/dev/null || { echo "pnpm is required" >&2; exit 1; }
command -v rsync >/dev/null || { echo "rsync is required" >&2; exit 1; }

mkdir -p "$dada_workspace_root" "$dada_node_root" "$dada_tmp_root" "$dada_logs_root" \
  "$dada_evaluation_results_root" "$dada_course_outputs_root" "$dada_archive_root" "$dada_media_root"

if [[ ! -x "$dada_python_bin" ]]; then
  python3 -m venv "$dada_workspace_root/.venv"
fi
"$dada_python_bin" -m pip install --upgrade pip
"$dada_python_bin" -m pip install -r "$project_root/requirements-v3-entry.txt"
"$dada_python_bin" -m pip check

cp "$project_root/package.json" "$dada_node_root/package.json"
if [[ -f "$project_root/pnpm-lock.yaml" ]]; then
  cp "$project_root/pnpm-lock.yaml" "$dada_node_root/pnpm-lock.yaml"
  workspace_pnpm install --frozen-lockfile
else
  workspace_pnpm install
fi

printf 'DADA_WORKSPACE_ROOT=%s\n' "$DADA_WORKSPACE_ROOT"
printf 'DADA_PYTHON_BIN=%s\n' "$dada_python_bin"
printf 'DADA_NODE_ROOT=%s\n' "$dada_node_root"
printf 'DADA_ARCHIVE_ROOT=%s\n' "$dada_archive_root"
printf 'DADA_LOGS_ROOT=%s\n' "$dada_logs_root"
printf 'DADA_COURSE_WORKSPACE=%s\n' "$dada_course_workspace"
printf 'DADA_COURSE_OUTPUTS=%s\n' "$dada_course_outputs_root"
