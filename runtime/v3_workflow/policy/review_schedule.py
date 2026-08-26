"""Pure, versioned review scheduling policy loaded from JSON."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from typing import Any


class PolicyError(ValueError):
    """The schedule policy is structurally invalid or cannot decide."""


@dataclass(frozen=True)
class ScheduleDecision:
    policy_id: str
    policy_version: int
    review_stage_before: int
    review_stage_after: int
    next_review_at_after: str | None
    archive: bool


@dataclass(frozen=True)
class ReviewSchedulePolicy:
    policy_id: str
    policy_version: int
    initial_stage: int
    intervals: dict[int, int]
    bands: tuple[tuple[float, float, str | int], ...]
    archive_after_stage: int

    @classmethod
    def from_file(cls, path: Path) -> "ReviewSchedulePolicy":
        try:
            raw = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PolicyError("review schedule cannot be loaded") from exc
        return cls.from_mapping(raw)

    @classmethod
    def from_mapping(cls, raw: Any) -> "ReviewSchedulePolicy":
        if not isinstance(raw, dict) or set(raw) != {"policy_id", "policy_version", "initial_stage", "stages", "accuracy_bands", "archive_after_stage"}:
            raise PolicyError("policy has unknown or missing fields")
        policy_id = _text(raw["policy_id"], "policy id")
        version = raw["policy_version"]
        initial_stage = raw["initial_stage"]
        archive_stage = raw["archive_after_stage"]
        if type(version) is not int or version <= 0 or type(initial_stage) is not int or initial_stage < 0 or type(archive_stage) is not int or archive_stage < 0:
            raise PolicyError("policy integer field is invalid")
        stages = raw["stages"]
        if not isinstance(stages, list) or not stages:
            raise PolicyError("policy stages must be a non-empty array")
        intervals: dict[int, int] = {}
        for value in stages:
            if not isinstance(value, dict) or set(value) != {"stage", "interval_seconds"}:
                raise PolicyError("policy stage has unknown or missing fields")
            stage, interval = value["stage"], value["interval_seconds"]
            if type(stage) is not int or stage < 0 or type(interval) is not int or interval <= 0 or stage in intervals:
                raise PolicyError("policy stage is invalid")
            intervals[stage] = interval
        if initial_stage not in intervals or archive_stage not in intervals:
            raise PolicyError("policy refers to an unknown stage")
        bands_raw = raw["accuracy_bands"]
        if not isinstance(bands_raw, list) or not bands_raw:
            raise PolicyError("accuracy bands must be a non-empty array")
        bands: list[tuple[float, float, str | int]] = []
        for value in bands_raw:
            if not isinstance(value, dict) or set(value) not in ({"minimum", "maximum", "action"}, {"minimum", "maximum", "next_stage"}):
                raise PolicyError("accuracy band has unknown or missing fields")
            low, high = value["minimum"], value["maximum"]
            target: str | int = value.get("action", value.get("next_stage"))
            if type(low) not in (int, float) or type(high) not in (int, float) or not 0 <= float(low) <= float(high) <= 1:
                raise PolicyError("accuracy band is invalid")
            if target not in {"reset_to_initial", "advance"} and (type(target) is not int or target not in intervals):
                raise PolicyError("accuracy band target is invalid")
            bands.append((float(low), float(high), target))
        bands.sort(key=lambda value: value[0])
        if bands[0][0] != 0 or bands[-1][1] != 1 or any(next_low != high for (_, high, _), (next_low, _, _) in zip(bands, bands[1:])):
            raise PolicyError("accuracy bands must cover [0, 1] without overlap")
        return cls(policy_id, version, initial_stage, intervals, tuple(bands), archive_stage)

    def initial_due_at(self, at: str) -> str:
        return _add_seconds(at, self.intervals[self.initial_stage])

    def decide(self, review_stage: int, accuracy: float, at: str) -> ScheduleDecision:
        if type(review_stage) is not int or review_stage not in self.intervals or type(accuracy) not in (int, float) or not 0 <= float(accuracy) <= 1:
            raise PolicyError("schedule decision inputs are invalid")
        target = next(
            (
                target
                for index, (low, high, target) in enumerate(self.bands)
                if low <= float(accuracy) < high or (index == len(self.bands) - 1 and float(accuracy) == high)
            ),
            None,
        )
        if target is None:
            raise PolicyError("accuracy does not match policy")
        if target == "reset_to_initial":
            next_stage = self.initial_stage
            interval_stage = self.initial_stage
        elif target == "advance":
            if review_stage >= self.archive_after_stage:
                return ScheduleDecision(self.policy_id, self.policy_version, review_stage, review_stage, None, True)
            next_stage = review_stage + 1
            interval_stage = review_stage
        else:
            next_stage = target
            interval_stage = target
        return ScheduleDecision(self.policy_id, self.policy_version, review_stage, next_stage, _add_seconds(at, self.intervals[interval_stage]), False)


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise PolicyError(f"{label} must be non-empty text")
    return value


def _add_seconds(value: str, seconds: int) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PolicyError("timestamp must be RFC3339 UTC") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise PolicyError("timestamp must be UTC")
    return (parsed.astimezone(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec="seconds").replace("+00:00", "Z")
