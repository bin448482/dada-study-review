#!/usr/bin/env bash

# Shared workspace resolution for repository scripts. Source this file after
# setting project_root to the repository root.

if [[ -z "${project_root:-}" ]]; then
  project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi

workspace_config_output="$(python3 "$project_root/scripts/workspace_config.py" --repository "$project_root" --shell)" || exit $?
eval "$workspace_config_output"
dada_workspace_root="$DADA_WORKSPACE_ROOT"
dada_node_root="$DADA_WORKSPACE_NODE_ROOT"
dada_python_bin="$DADA_WORKSPACE_PYTHON"
dada_pip_bin="$DADA_WORKSPACE_PIP"
dada_tmp_root="$DADA_WORKSPACE_TMP"
dada_archive_root="$DADA_WORKSPACE_ARCHIVE"
dada_logs_root="$DADA_WORKSPACE_LOGS"
dada_evaluation_results_root="$DADA_WORKSPACE_EVALUATION_RESULTS"
dada_course_outputs_root="$DADA_WORKSPACE_COURSE_OUTPUTS"
dada_media_root="$DADA_WORKSPACE_MEDIA"
dada_course_workspace="$DADA_WORKSPACE_COURSE_WORKSPACE"
dada_textbook_images="$DADA_WORKSPACE_TEXTBOOK_IMAGES"

# Compatibility names consumed by Python/Node entry points. Their values are
# still resolved exclusively from the workspace configuration above.
export DADA_PYTHON_BIN="$dada_python_bin"
export DADA_ARCHIVE_ROOT="$dada_archive_root"
export DADA_WORKSPACE_LOGS="$dada_logs_root"
export DADA_WORKSPACE_EVALUATION_RESULTS="$dada_evaluation_results_root"
export DADA_WORKSPACE_COURSE_OUTPUTS="$dada_course_outputs_root"
export DADA_WORKSPACE_MEDIA="$dada_media_root"
export DADA_COURSE_WORKSPACE="$dada_course_workspace"
export DADA_TEXTBOOK_IMAGES="$dada_textbook_images"

require_workspace() {
  if [[ ! -d "$DADA_WORKSPACE_ROOT" ]]; then
    echo "workspace does not exist: $DADA_WORKSPACE_ROOT; run scripts/bootstrap-workspace.sh first" >&2
    return 1
  fi
}

workspace_pnpm() {
  require_workspace
  pnpm --dir "$dada_node_root" "$@"
}
