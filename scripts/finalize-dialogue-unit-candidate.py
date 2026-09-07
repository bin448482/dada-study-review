#!/usr/bin/env python3
"""Revalidate and atomically promote one Dialogue Unit candidate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile


PROJECT = Path(__file__).resolve().parents[1]


def _pages(value: str) -> tuple[int, ...]:
    try:
        pages = tuple(int(item) for item in value.split(",") if item)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("source pages must be comma-separated positive integers") from exc
    if not pages or any(page <= 0 for page in pages) or len(pages) != len(set(pages)):
        raise argparse.ArgumentTypeError("source pages must be unique positive integers")
    return pages


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--english-text", type=Path, required=True)
    parser.add_argument("--task-audit", type=Path, required=True)
    parser.add_argument("--semantic-review", type=Path, required=True)
    parser.add_argument("--previous-unit", type=Path, help="prior Unit package used to enforce stable target IDs")
    parser.add_argument("--unit-id", required=True)
    parser.add_argument("--version", type=int, required=True)
    parser.add_argument("--source-pages", type=_pages, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    expected_name = f"unit.v{args.version}.json"
    if args.output.name != expected_name:
        print(json.dumps({"ok": False, "error": "output filename differs from requested version"}, ensure_ascii=False))
        return 1
    if args.output.exists():
        print(json.dumps({"ok": False, "error": "final Unit version already exists"}, ensure_ascii=False))
        return 1
    validator = PROJECT / "scripts" / "validate-dialogue-unit-candidate.py"
    command = [
        sys.executable,
        str(validator),
        "--candidate", str(args.candidate),
        "--english-text", str(args.english_text),
        "--task-audit", str(args.task_audit),
        "--semantic-review", str(args.semantic_review),
        "--unit-id", args.unit_id,
        "--version", str(args.version),
        "--source-pages", ",".join(str(page) for page in args.source_pages),
    ]
    if args.previous_unit is not None:
        command.extend(["--previous-unit", str(args.previous_unit)])
    validation = subprocess.run(command, text=True, capture_output=True, check=False)
    if validation.returncode != 0:
        print(validation.stdout.strip() or json.dumps({"ok": False, "error": "candidate validation failed"}, ensure_ascii=False))
        return validation.returncode or 1
    try:
        result = json.loads(validation.stdout)
        payload = args.candidate.read_bytes()
    except (json.JSONDecodeError, OSError):
        print(json.dumps({"ok": False, "error": "validated candidate cannot be finalized"}, ensure_ascii=False))
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(dir=args.output.parent, prefix=f".{args.output.name}.", delete=False) as stream:
            temp_path = Path(stream.name)
            stream.write(payload)
        temp_path.replace(args.output)
    except OSError as exc:
        try:
            temp_path.unlink(missing_ok=True)
        except UnboundLocalError:
            pass
        print(json.dumps({"ok": False, "error": f"final Unit write failed: {exc}"}, ensure_ascii=False))
        return 1
    print(json.dumps({
        "ok": True,
        "unit_id": result["unit_id"],
        "version": result["version"],
        "content_hash": result["content_hash"],
        "output": str(args.output),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
