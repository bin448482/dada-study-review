#!/usr/bin/env python3
"""Resolve the portable workspace configuration for repository entry points."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import stat
import sys
from typing import Any


REQUIRED_PATHS = (
    "python",
    "pip",
    "node_root",
    "tmp",
    "logs",
    "evaluation_results",
    "course_outputs",
    "archive",
    "media",
    "course_workspace",
    "textbook_images",
)


def load_config(repository: Path, config_path: Path | None) -> dict[str, Any]:
    configured = os.environ.get("DADA_WORKSPACE_CONFIG")
    selected = config_path or (Path(configured) if configured else repository / "config" / "workspace.json")
    if not selected.is_absolute():
        selected = (repository / selected).resolve()
    if not selected.exists():
        selected = repository / "config" / "workspace.example.json"
    try:
        value = json.loads(selected.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid workspace configuration: {selected}") from exc
    if value.get("version") != 1 or not isinstance(value.get("paths"), dict):
        raise ValueError("workspace configuration version or paths are invalid")
    if not isinstance(value.get("workspace_root"), str) or not value["workspace_root"]:
        raise ValueError("workspace_root must be a non-empty string")
    if any(not isinstance(value["paths"].get(key), str) or not value["paths"][key] for key in REQUIRED_PATHS):
        raise ValueError("workspace configuration is missing a required path")
    root = Path(value["workspace_root"])
    if not root.is_absolute():
        root = (selected.parent / root).resolve()
    value["workspace_root"] = root
    value["config_path"] = selected
    return value


def resolve(value: dict[str, Any]) -> dict[str, str]:
    root = value["workspace_root"]
    result = {"DADA_WORKSPACE_ROOT": str(root), "DADA_WORKSPACE_CONFIG": str(value["config_path"])}
    for key in REQUIRED_PATHS:
        path = Path(value["paths"][key])
        result[f"DADA_WORKSPACE_{key.upper()}"] = str(path if path.is_absolute() else root / path)
    return result


def check_permissions(value: dict[str, Any], create: bool) -> None:
    root = value["workspace_root"]
    permissions = value.get("permissions", {})
    if create and permissions.get("create_directories", False):
        for key in ("workspace_root", "node_root", "tmp", "logs", "evaluation_results", "course_outputs", "archive", "media"):
            path = root if key == "workspace_root" else Path(resolve(value)[f"DADA_WORKSPACE_{key.upper()}"])
            path.mkdir(parents=True, exist_ok=True)
    if permissions.get("require_writable_workspace", True) and not os.access(root, os.W_OK):
        raise ValueError(f"workspace is not writable: {root}")
    if permissions.get("private_archive", False) and os.name == "posix":
        archive = Path(resolve(value)["DADA_WORKSPACE_ARCHIVE"])
        archive.mkdir(parents=True, exist_ok=True)
        archive.chmod(stat.S_IRWXU)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--shell", action="store_true")
    parser.add_argument("--create", action="store_true")
    args = parser.parse_args()
    try:
        value = load_config(args.repository.resolve(), args.config)
        if args.create:
            check_permissions(value, True)
        result = resolve(value)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.shell:
        for key, item in result.items():
            print(f"{key}={shlex.quote(item)}")
    else:
        print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
