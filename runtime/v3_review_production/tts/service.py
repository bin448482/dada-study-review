"""Compatibility wrapper for Review's shared TTS service."""

from __future__ import annotations

from pathlib import Path
import time
from typing import Callable
from urllib.request import urlopen

from v3_workflow.tts.service import synthesize_tts_delivery
from .contracts import ReviewTtsConfig, TtsError, TtsProvider


def question_speech_text(question_mode: str | None, question_json: dict[str, str] | None) -> str | None:
    if question_mode not in {"spelling", "sentence_recall", "zh_to_en", "en_to_zh"} or not isinstance(question_json, dict):
        return None
    key = "speech_text" if question_mode in {"spelling", "sentence_recall"} else "prompt"
    value = question_json.get(key)
    return value if isinstance(value, str) and value.strip() else None


def review_delivery_speech_text(
    reply_text: str | None, question_mode: str | None, question_json: dict[str, str] | None
) -> str | None:
    """Return the complete committed Review reply to speak, without altering archive facts."""
    if not isinstance(reply_text, str) or not reply_text.strip():
        return None
    source = question_speech_text(question_mode, question_json)
    if question_mode in {"spelling", "sentence_recall"} and source is not None:
        return f"{reply_text}\n\n{source}"
    return reply_text


def synthesize_review_delivery(
    config: ReviewTtsConfig,
    reply_text: str | None,
    question_mode: str | None,
    question_json: dict[str, str] | None,
    outbox: Path,
    open_request: Callable[..., object] = urlopen,
    now: Callable[[], float] = time.time,
    provider: TtsProvider | None = None,
) -> Path | None:
    text = review_delivery_speech_text(reply_text, question_mode, question_json)
    return synthesize_tts_delivery(config, text, outbox, open_request, now, provider)
