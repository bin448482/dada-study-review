"""Load one verified, non-discoverable review state-machine definition bundle."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import stat


class DefinitionError(ValueError):
    pass


_EXPECTED_FILES = frozenset({"SKILL.md", "agents/openai.yaml", "references/review-turn-contract.md"})
_CREDENTIAL_SHAPE = re.compile(r"(?:api[_-]?key|authorization|bearer\s+[a-z0-9._-]+)", re.IGNORECASE)


@dataclass(frozen=True)
class StateMachineDefinition:
    definition_id: str
    digest: str
    system_prompt: str
    output_schema: dict[str, object]


def _bundle_files(root: Path) -> list[Path]:
    if not root.is_dir() or root.is_symlink():
        raise DefinitionError("definition root must be a real directory")
    files = sorted(path for path in root.rglob("*") if path.is_file() and path.name != ".DS_Store")
    if {path.relative_to(root).as_posix() for path in files} != _EXPECTED_FILES:
        raise DefinitionError("definition files do not match the fixed bundle contract")
    if any(path.is_symlink() or not stat.S_ISREG(path.stat().st_mode) for path in files):
        raise DefinitionError("definition contains an unsafe file")
    return files


def definition_digest(directory: Path) -> str:
    root = directory.resolve(strict=True)
    digest = hashlib.sha256()
    for path in _bundle_files(root):
        relative, data = path.relative_to(root).as_posix().encode("utf-8"), path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big")); digest.update(relative)
        digest.update(len(data).to_bytes(8, "big")); digest.update(data)
    return digest.hexdigest()


def load_state_machine_definition(directory: Path, definition_id: str, expected_digest: str) -> StateMachineDefinition:
    if definition_id != "dada-review-state-machine" or not re.fullmatch(r"[a-f0-9]{64}", expected_digest):
        raise DefinitionError("definition identity is unsupported")
    root = directory.resolve(strict=True)
    texts: dict[str, str] = {}
    for path in _bundle_files(root):
        relative = path.relative_to(root).as_posix()
        try: texts[relative] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc: raise DefinitionError("definition file is not UTF-8") from exc
    joined = "\n".join(texts.values())
    if "dada.review_state_machine_turn" not in joined or "https://" in joined or "http://" in joined or _CREDENTIAL_SHAPE.search(joined):
        raise DefinitionError("definition contents are unsafe")
    if "allow_implicit_invocation: false" not in texts["agents/openai.yaml"] or re.search(r"^dependencies:\s*$", texts["agents/openai.yaml"], re.MULTILINE):
        raise DefinitionError("definition invocation policy is unsafe")
    actual = definition_digest(root)
    if actual != expected_digest:
        raise DefinitionError("definition digest does not match the reviewed release")
    return StateMachineDefinition(
        definition_id, actual, "\n\n".join((texts["SKILL.md"], texts["references/review-turn-contract.md"])),
        {"type": "object", "additionalProperties": False, "required": ["contract_name", "contract_version", "data"], "properties": {"contract_name": {"type": "string", "const": "dada.review_state_machine_result"}, "contract_version": {"type": "integer", "const": 4}, "data": {"type": "object"}}},
    )
