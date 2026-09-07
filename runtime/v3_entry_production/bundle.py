"""Load one verified, non-discoverable state-machine definition bundle."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import stat


class DefinitionError(ValueError):
    """A definition bundle cannot safely be used by the production Gateway."""


_EXPECTED_FILES = frozenset({"SKILL.md", "agents/openai.yaml", "references/entry-turn-contract.md"})
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
    for path in files:
        if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
            raise DefinitionError(f"definition contains an unsafe file: {path.relative_to(root)}")
    return files


def definition_digest(directory: Path) -> str:
    root = directory.resolve(strict=True)
    digest = hashlib.sha256()
    for path in _bundle_files(root):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        data = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def _validated_texts(root: Path) -> dict[str, str]:
    texts: dict[str, str] = {}
    for path in _bundle_files(root):
        relative = path.relative_to(root).as_posix()
        try:
            texts[relative] = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise DefinitionError(f"definition file is not UTF-8: {relative}") from exc
    joined = "\n".join(texts.values())
    if "dada.entry_state_machine_turn" not in joined:
        raise DefinitionError("definition does not declare the fixed task")
    if "https://" in joined or "http://" in joined or _CREDENTIAL_SHAPE.search(joined):
        raise DefinitionError("definition contains a prohibited provider or credential value")
    metadata = texts["agents/openai.yaml"]
    if "allow_implicit_invocation: false" not in metadata or re.search(r"^dependencies:\s*$", metadata, re.MULTILINE):
        raise DefinitionError("definition invocation policy is unsafe")
    return texts


def _output_schema() -> dict[str, object]:
    # The Graph still applies its authoritative local validator after provider parsing.
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["contract_name", "contract_version", "data"],
        "properties": {
            "contract_name": {"const": "dada.entry_state_machine_result"},
            "contract_version": {"const": 3},
            "data": {"type": "object"},
        },
    }


def load_state_machine_definition(directory: Path, definition_id: str, expected_digest: str) -> StateMachineDefinition:
    if definition_id != "dada-entry-state-machine" or not re.fullmatch(r"[a-f0-9]{64}", expected_digest):
        raise DefinitionError("definition identity is unsupported")
    root = directory.resolve(strict=True)
    texts = _validated_texts(root)
    actual_digest = definition_digest(root)
    if actual_digest != expected_digest:
        raise DefinitionError("definition digest does not match the reviewed release")
    system_prompt = "\n\n".join((texts["SKILL.md"], texts["references/entry-turn-contract.md"]))
    return StateMachineDefinition(definition_id=definition_id, digest=actual_digest, system_prompt=system_prompt, output_schema=_output_schema())
