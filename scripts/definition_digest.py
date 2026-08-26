#!/usr/bin/env python3
"""Calculate the stable digest of one reviewed state-machine definition directory."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import re
import stat


class DefinitionDigestError(ValueError):
    """The definition bundle does not have a safe, deterministic shape."""


_DEFINITION_CONTRACTS = {
    "dada-entry-state-machine": ("references/entry-turn-contract.md", "dada.entry_state_machine_turn"),
    "dada-review-state-machine": ("references/review-turn-contract.md", "dada.review_state_machine_turn"),
}
_CREDENTIAL_SHAPE = re.compile(r"(?:api[_-]?key|authorization|bearer\s+[a-z0-9._-]+)", re.IGNORECASE)


def _definition_files(root: Path) -> list[Path]:
    try:
        contract_path, _ = _DEFINITION_CONTRACTS[root.name]
    except KeyError as exc:
        raise DefinitionDigestError("definition directory is not an approved state-machine definition") from exc
    expected_files = frozenset({"SKILL.md", "agents/openai.yaml", contract_path})
    files = sorted(path for path in root.rglob("*") if path.is_file() and path.name != ".DS_Store")
    actual = {path.relative_to(root).as_posix() for path in files}
    if actual != expected_files:
        raise DefinitionDigestError("definition files do not match the fixed bundle contract")
    return files


def validate_definition(directory: Path) -> None:
    root = directory.resolve(strict=True)
    if not root.is_dir() or root.is_symlink():
        raise DefinitionDigestError("definition root must be a real directory")
    files = _definition_files(root)
    contents: dict[str, str] = {}
    for path in files:
        if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
            raise DefinitionDigestError(f"definition contains an unsafe file: {path.relative_to(root)}")
        try:
            contents[path.relative_to(root).as_posix()] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise DefinitionDigestError(f"definition file is not UTF-8: {path.relative_to(root)}") from exc
    _, task_name = _DEFINITION_CONTRACTS[root.name]
    if task_name not in "\n".join(contents.values()):
        raise DefinitionDigestError("definition does not declare the fixed task")
    if "https://" in "\n".join(contents.values()) or "http://" in "\n".join(contents.values()):
        raise DefinitionDigestError("definition must not contain a provider URL")
    if _CREDENTIAL_SHAPE.search("\n".join(contents.values())):
        raise DefinitionDigestError("definition must not contain credential-shaped text")
    metadata = contents["agents/openai.yaml"]
    if "allow_implicit_invocation: false" not in metadata:
        raise DefinitionDigestError("state-machine definition must disable implicit invocation")
    if re.search(r"^dependencies:\s*$", metadata, re.MULTILINE):
        raise DefinitionDigestError("state-machine definition must not declare tools")


def definition_digest(directory: Path) -> str:
    root = directory.resolve(strict=True)
    validate_definition(root)
    digest = hashlib.sha256()
    files = _definition_files(root)
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        data = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    try:
        print(definition_digest(args.directory))
    except (DefinitionDigestError, OSError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
