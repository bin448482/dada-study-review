#!/usr/bin/env python3
"""Validate a page-semantic Dialogue Unit v3 candidate without writes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any


PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "runtime"))

from v3_dialogue.unit_definition import UnitDefinitionError, load_unit_definition


UNCERTAINTY_MARKERS = ("[unclear:", "[illegible]", "[page number unclear]")
HEADING_PATTERN = re.compile(r"^#{2,}\s+(.+?)\s*$", re.MULTILINE)


class CandidateError(ValueError):
    """A candidate cannot be safely promoted to a Unit package."""


def _read_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidateError(f"{label} is not readable JSON") from exc
    if not isinstance(value, dict):
        raise CandidateError(f"{label} must be an object")
    return value


def parse_pages(english_text: Path, pages: tuple[int, ...]) -> dict[int, set[str]]:
    if not (english_text / "README.md").is_file():
        raise CandidateError("english-text README is missing")
    result: dict[int, set[str]] = {}
    for page in pages:
        path = english_text / f"page-{page}.md"
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise CandidateError(f"source page {page} is missing") from exc
        if not text.strip():
            raise CandidateError(f"source page {page} is empty")
        if any(marker in text for marker in UNCERTAINTY_MARKERS):
            raise CandidateError(f"source page {page} has unresolved transcription")
        if not re.search(rf"^# Page {page}(?:\s|—)", text, re.MULTILINE):
            raise CandidateError(f"source page {page} has a mismatched page heading")
        result[page] = set(HEADING_PATTERN.findall(text))
    return result


def _validate_v3_sources(unit: Any, headings_by_page: dict[int, set[str]]) -> int:
    refs = 0
    for target in unit.targets:
        if unit.version >= 6 and target["target_type"] == "phrase" and "review_design" not in target:
            raise CandidateError("new phrase targets require review design")
        for ref in target["source_refs"]:
            refs += 1
            page = int(ref["page"])
            if page not in headings_by_page:
                raise CandidateError("target source ref page is outside selected source pages")
            if str(ref["raw_heading"]) not in headings_by_page[page]:
                raise CandidateError("target source ref heading is not present in source Markdown")
            locator = str(ref["source_locator"])
            if len(locator) > 240:
                raise CandidateError("target source locator exceeds maximum length")
    return refs


def validate_task_audit(path: Path, unit: Any, source_pages: tuple[int, ...]) -> dict[str, int]:
    raw = _read_object(path, "task audit")
    fields = {"audit_schema_version", "unit_id", "version", "source_pages", "pages"}
    if set(raw) != fields:
        raise CandidateError("task audit has unknown or missing fields")
    if raw["audit_schema_version"] != 1 or raw["unit_id"] != unit.unit_id or raw["version"] != unit.version:
        raise CandidateError("task audit metadata differs from candidate")
    if raw["source_pages"] != list(source_pages) or not isinstance(raw["pages"], list):
        raise CandidateError("task audit source pages differ from candidate")
    target_ids = set(unit.targets_by_id)
    scenario_ids = set(unit.scenarios_by_id)
    seen_pages: set[int] = set()
    covered_target_ids: set[str] = set()
    section_count = 0
    for page_entry in raw["pages"]:
        if not isinstance(page_entry, dict) or set(page_entry) != {"page", "sections"}:
            raise CandidateError("task audit page entry is invalid")
        page = page_entry["page"]
        if type(page) is not int or page not in source_pages or page in seen_pages:
            raise CandidateError("task audit page is invalid or duplicated")
        sections = page_entry["sections"]
        if not isinstance(sections, list) or not sections:
            raise CandidateError("task audit page must contain sections")
        seen_pages.add(page)
        for section in sections:
            required = {"locator", "section_kind", "learner_action", "information_dimensions", "output", "modality", "coverage_target_ids", "coverage_scenario_ids", "exclusion_reason"}
            if not isinstance(section, dict) or set(section) != required:
                raise CandidateError("task audit section has unknown or missing fields")
            for key in ("locator", "section_kind", "learner_action", "output", "modality", "exclusion_reason"):
                if not isinstance(section[key], str):
                    raise CandidateError("task audit section text fields are invalid")
            if section["section_kind"] not in {"learning_task", "language_material", "information_relation", "support", "excluded"}:
                raise CandidateError("task audit section kind is invalid")
            if section["modality"] not in {"required", "invited_practice", "example", "background"}:
                raise CandidateError("task audit modality is invalid")
            if not isinstance(section["information_dimensions"], list) or any(not isinstance(item, str) or not item.strip() for item in section["information_dimensions"]):
                raise CandidateError("task audit information dimensions are invalid")
            for key, valid in (("coverage_target_ids", target_ids), ("coverage_scenario_ids", scenario_ids)):
                values = section[key]
                if not isinstance(values, list) or any(not isinstance(item, str) or item not in valid for item in values):
                    raise CandidateError("task audit coverage reference is invalid")
            if section["section_kind"] in {"learning_task", "language_material", "information_relation"} and not section["coverage_target_ids"] and not section["coverage_scenario_ids"]:
                raise CandidateError("task audit learning section has no coverage")
            if (section["section_kind"] in {"support", "excluded"} or (not section["coverage_target_ids"] and not section["coverage_scenario_ids"])) and not section["exclusion_reason"].strip():
                raise CandidateError("task audit exclusion reason is missing")
            covered_target_ids.update(section["coverage_target_ids"])
            section_count += 1
    if seen_pages != set(source_pages):
        raise CandidateError("task audit must cover every source page")
    if covered_target_ids != target_ids:
        raise CandidateError("task audit must cover every candidate target")
    return {"page_count": len(seen_pages), "section_count": section_count}


def validate_semantic_review(path: Path, unit: Any) -> dict[str, int]:
    raw = _read_object(path, "semantic review")
    fields = {"review_schema_version", "unit_id", "version", "candidate_content_hash", "review_status", "groups"}
    if set(raw) != fields:
        raise CandidateError("semantic review has unknown or missing fields")
    if raw["review_schema_version"] != 1 or raw["unit_id"] != unit.unit_id or raw["version"] != unit.version:
        raise CandidateError("semantic review metadata differs from candidate")
    if raw["candidate_content_hash"] != unit.content_hash:
        raise CandidateError("semantic review hash differs from candidate")
    if raw["review_status"] != "complete" or not isinstance(raw["groups"], list) or not raw["groups"]:
        raise CandidateError("semantic review is not complete")
    target_ids = set(unit.targets_by_id)
    seen: set[str] = set()
    for group in raw["groups"]:
        if not isinstance(group, dict) or set(group) != {"group_id", "target_ids", "decision", "rationale"}:
            raise CandidateError("semantic review group has unknown or missing fields")
        if not isinstance(group["group_id"], str) or not group["group_id"].strip() or not isinstance(group["target_ids"], list) or not group["target_ids"]:
            raise CandidateError("semantic review group is invalid")
        if group["decision"] not in {"retain_distinct", "merge", "remove"}:
            raise CandidateError("semantic review decision is invalid")
        if not isinstance(group["rationale"], str) or not group["rationale"].strip():
            raise CandidateError("semantic review rationale is missing")
        for target_id in group["target_ids"]:
            if target_id not in target_ids or target_id in seen:
                raise CandidateError("semantic review target coverage is invalid")
            seen.add(target_id)
        if group["decision"] != "retain_distinct":
            raise CandidateError("semantic review contains unresolved merge or remove decision")
    if seen != target_ids:
        raise CandidateError("semantic review must cover every candidate target")
    return {"group_count": len(raw["groups"]), "target_count": len(seen)}


def _target_identity(target: dict[str, Any]) -> str:
    return " ".join(str(target["english"]).casefold().split())


def validate_stable_target_ids(unit: Any, previous_unit_path: Path | None) -> dict[str, int] | None:
    """Require unchanged content to retain its previous curriculum target ID."""

    if previous_unit_path is None:
        return None
    try:
        previous = load_unit_definition(previous_unit_path, expected_unit_id=unit.unit_id)
    except (OSError, UnitDefinitionError) as exc:
        raise CandidateError(f"previous Unit package is invalid: {exc}") from exc
    if previous.version >= unit.version:
        raise CandidateError("previous Unit package version must be lower than candidate")
    previous_by_id = {str(target["target_id"]): target for target in previous.targets}
    current_by_id = {str(target["target_id"]): target for target in unit.targets}
    for target_id in set(previous_by_id) & set(current_by_id):
        if _target_identity(previous_by_id[target_id]) != _target_identity(current_by_id[target_id]):
            raise CandidateError(f"target_id {target_id} changed content; create a new target_id")
    previous_by_content = {_target_identity(target): str(target["target_id"]) for target in previous.targets}
    for target_id, target in current_by_id.items():
        prior_id = previous_by_content.get(_target_identity(target))
        if prior_id is not None and prior_id != target_id:
            raise CandidateError(f"same target content must reuse target_id {prior_id}")
    return {
        "previous_version": previous.version,
        "retained_target_count": len(set(previous_by_id) & set(current_by_id)),
        "removed_target_count": len(set(previous_by_id) - set(current_by_id)),
        "new_target_count": len(set(current_by_id) - set(previous_by_id)),
    }


def validate_candidate(
    candidate: Path,
    english_text: Path,
    unit_id: str,
    version: int,
    source_pages: tuple[int, ...],
    task_audit: Path | None = None,
    semantic_review: Path | None = None,
    previous_unit: Path | None = None,
) -> dict[str, Any]:
    headings_by_page = parse_pages(english_text, source_pages)
    try:
        raw = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CandidateError("candidate is not readable JSON") from exc
    if not isinstance(raw, dict):
        raise CandidateError("candidate must be an object")
    if raw.get("definition_schema_version") != 3:
        raise CandidateError("candidate must use definition schema version 3")
    if raw.get("unit_id") != unit_id or raw.get("version") != version:
        raise CandidateError("candidate Unit metadata differs from request")
    if raw.get("source_pages") != list(source_pages):
        raise CandidateError("candidate source pages differ from request")
    try:
        unit = load_unit_definition(candidate, expected_unit_id=unit_id)
    except UnitDefinitionError as exc:
        raise CandidateError(str(exc)) from exc
    stable_id_result = validate_stable_target_ids(unit, previous_unit)
    refs = _validate_v3_sources(unit, headings_by_page)
    audit_result = validate_task_audit(task_audit, unit, source_pages) if task_audit is not None else None
    review_result = validate_semantic_review(semantic_review, unit) if semantic_review is not None else None
    english_seen: set[str] = set()
    for target in unit.targets:
        english = " ".join(str(target["english"]).casefold().split())
        if english in english_seen:
            raise CandidateError("candidate repeats target English text")
        english_seen.add(english)
    for intent in unit.question_intents:
        if intent["dialogue_evidence"] == "initiate_question":
            if "信息缺口" not in intent["prompt_constraint"]:
                raise CandidateError("initiated question intent must require an information gap")
        elif intent["dialogue_evidence"] == "understand_and_respond":
            if "完整回答" not in intent["prompt_constraint"]:
                raise CandidateError("understanding intent must require a complete answer")
    return {
        "ok": True,
        "unit_id": unit.unit_id,
        "version": unit.version,
        "definition_schema_version": unit.definition_schema_version,
        "content_hash": unit.content_hash,
        "target_count": len(unit.targets),
        "source_reference_count": refs,
        "task_audit": audit_result,
        "semantic_review": review_result,
        "stable_target_ids": stable_id_result,
    }


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
    parser.add_argument("--unit-id", required=True)
    parser.add_argument("--version", type=int, required=True)
    parser.add_argument("--source-pages", type=_pages, required=True)
    parser.add_argument("--task-audit", type=Path)
    parser.add_argument("--semantic-review", type=Path)
    parser.add_argument("--previous-unit", type=Path, help="prior Unit package used to enforce stable target IDs")
    args = parser.parse_args()
    try:
        result = validate_candidate(
            args.candidate,
            args.english_text,
            args.unit_id,
            args.version,
            args.source_pages,
            args.task_audit,
            args.semantic_review,
            args.previous_unit,
        )
    except CandidateError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
