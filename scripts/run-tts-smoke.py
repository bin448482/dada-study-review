#!/usr/bin/env python3
"""Run an explicit live TTS probe with a fixed, non-private sentence."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from urllib.request import urlopen

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "runtime"))

from v3_workflow.tts.contracts import TtsConfig, TtsError
from v3_workflow.tts.minimax import MiniMaxTtsProvider
from v3_workflow.tts.volcengine_seed import VolcengineSeedTtsProvider

DEFAULT_TEXT = "Hello. This is a public TTS smoke test."


def load_config(path: Path, api_key: str) -> TtsConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("enabled") is not True:
        raise ValueError("TTS smoke requires a config with enabled=true")
    provider = raw.get("provider")
    common = (
        api_key,
        raw["endpoint"],
        int(raw["timeoutSeconds"]),
        int(raw["maxTextChars"]),
        int(raw["maxAudioBytes"]),
        int(raw["retentionSeconds"]),
    )
    if provider == "minimax":
        return TtsConfig(
            common[0], common[1], None, None, common[2], common[3], common[4], common[5],
            provider="minimax", model=raw["model"], voice_id=raw["voiceId"],
        )
    if provider == "volcengine_seed":
        return TtsConfig(
            common[0], common[1], raw["resourceId"], raw["speaker"], common[2], common[3], common[4], common[5],
            provider="volcengine_seed",
        )
    raise ValueError("unsupported public TTS provider")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("minimax", "volcengine_seed"), required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--text", default=DEFAULT_TEXT)
    args = parser.parse_args()
    if args.text != DEFAULT_TEXT:
        raise SystemExit("--text must remain the fixed public smoke sentence")
    key = os.environ.get("DADA_REVIEW_TTS_API_KEY")
    if not key:
        print("TTS smoke test not run: DADA_REVIEW_TTS_API_KEY is not configured", file=sys.stderr)
        return 2
    try:
        config = load_config(args.config, key)
        if config.provider != args.provider:
            raise ValueError("--provider does not match the config")
        provider = MiniMaxTtsProvider() if args.provider == "minimax" else VolcengineSeedTtsProvider()
        audio = provider.synthesize_mp3(config, DEFAULT_TEXT, urlopen)
        if not audio.startswith(b"ID3") and not audio.startswith(b"\xff\xfb"):
            raise TtsError("provider did not return MP3")
        if len(audio) > config.max_audio_bytes:
            raise TtsError("provider returned oversized audio")
    except (OSError, KeyError, TypeError, ValueError, TtsError, json.JSONDecodeError):
        print(json.dumps({"ok": False, "reason": "TTS smoke failed"}), file=sys.stderr)
        return 1
    print(json.dumps({"ok": True, "provider": config.provider, "format": "mp3", "bytes": len(audio)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
