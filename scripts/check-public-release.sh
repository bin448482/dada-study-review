#!/usr/bin/env bash
set -euo pipefail

git diff --check

forbidden_paths='^(data/|review_reports/|\.env($|\.)|.*\.sqlite3($|-)|.*(^|/)node_modules/)'
if git ls-files | rg -n "$forbidden_paths"; then
  echo "public tree contains a forbidden tracked path" >&2
  exit 1
fi

private_marker='-----'"BEGIN"
if git grep -n -I -E '/(home|mnt)/' -- . ':!scripts/check-public-release.sh' || git grep -n -I -F -e "$private_marker" -- . ':!scripts/check-public-release.sh'; then
  echo "public tree contains a machine path or private key marker" >&2
  exit 1
fi
