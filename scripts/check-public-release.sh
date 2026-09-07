#!/usr/bin/env bash
set -euo pipefail

git diff --check

while IFS= read -r path; do
  case "$path" in
    data/*|review_reports/*|.env|.env.*|*.sqlite3|*.sqlite3-*|*/node_modules/*)
      echo "public tree contains a forbidden tracked path: $path" >&2
      exit 1
      ;;
  esac
done < <(git ls-files)

private_marker='-----'"BEGIN"
if git grep -n -I -E '/(home|mnt)/' -- . ':!scripts/check-public-release.sh' || git grep -n -I -F -e "$private_marker" -- . ':!scripts/check-public-release.sh'; then
  echo "public tree contains a machine path or private key marker" >&2
  exit 1
fi
