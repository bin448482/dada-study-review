"""Shared provider dispatch and bounded MP3 outbox handling."""

from __future__ import annotations

import os
from pathlib import Path
import time
from typing import Callable
from urllib.request import urlopen
from uuid import uuid4

from .contracts import TtsConfig, TtsError, TtsProvider
from .minimax import MiniMaxTtsProvider
from .volcengine_seed import VolcengineSeedTtsProvider


def synthesize_tts_delivery(
    config: TtsConfig, text: str | None, outbox: Path,
    open_request: Callable[..., object] = urlopen, now: Callable[[], float] = time.time,
    provider: TtsProvider | None = None,
) -> Path | None:
    if not isinstance(text, str) or not text.strip():
        return None
    if len(text) > config.max_text_chars:
        raise TtsError("TTS speech text exceeds the fixed limit")
    _prune_outbox(outbox, config.retention_seconds, now())
    adapter = provider if provider is not None else _provider_for(config)
    audio = adapter.synthesize_mp3(config, text, open_request)
    if not audio:
        raise TtsError("TTS returned no complete audio")
    if len(audio) > config.max_audio_bytes:
        raise TtsError("TTS audio exceeds the fixed limit")
    return _write_mp3(outbox, audio)


def _provider_for(config: TtsConfig) -> TtsProvider:
    if config.provider == "volcengine_seed":
        return VolcengineSeedTtsProvider()
    if config.provider == "minimax":
        return MiniMaxTtsProvider()
    raise TtsError("TTS provider is unsupported")


def _write_mp3(outbox: Path, audio: bytes) -> Path:
    outbox.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = outbox / f"{uuid4()}.mp3"
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(audio)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return target.resolve(strict=True)


def _prune_outbox(outbox: Path, retention_seconds: int, current: float) -> None:
    if not outbox.is_dir() or outbox.is_symlink():
        return
    cutoff = current - retention_seconds
    for candidate in outbox.iterdir():
        try:
            if candidate.is_symlink() or not candidate.is_file() or candidate.suffix != ".mp3" or candidate.stat().st_mtime >= cutoff:
                continue
            candidate.unlink()
        except OSError:
            continue
