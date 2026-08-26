"""Program-owned review question-mode selection without English scoring."""

from __future__ import annotations

from collections import Counter
import secrets
from typing import Callable, Iterable


QUESTION_MODES: dict[str, tuple[str, ...]] = {
    "word": ("word_mask", "spelling", "zh_to_en", "en_to_zh"),
    "phrase": ("mask", "zh_to_en", "en_to_zh"),
    "sentence": ("mask", "sentence_recall", "zh_to_en", "en_to_zh"),
}


def allowed_question_modes(unit_type: str) -> tuple[str, ...]:
    try:
        return QUESTION_MODES[unit_type]
    except KeyError as error:
        raise ValueError("review unit type has no question modes") from error


def choose_question_mode(
    unit_type: str,
    previous_modes: Iterable[str],
    chooser: Callable[[list[str]], str] | None = None,
) -> str:
    """Randomly choose a permitted mode, balanced within the current workflow.

    The program selects only an exercise mode. It never writes an English
    prompt, blank, answer, or assessment. Selection is random among the
    least-used permitted modes and avoids an immediate repeat where possible.
    """

    allowed = allowed_question_modes(unit_type)
    prior = tuple(mode for mode in previous_modes if mode in allowed)
    candidates = [mode for mode in allowed if mode != (prior[-1] if prior else None)] or list(allowed)
    counts = Counter(prior)
    least_count = min(counts[mode] for mode in candidates)
    least_used = [mode for mode in candidates if counts[mode] == least_count]
    selected = (chooser or secrets.choice)(least_used)
    if selected not in least_used:
        raise ValueError("question mode selector returned an invalid mode")
    return selected
