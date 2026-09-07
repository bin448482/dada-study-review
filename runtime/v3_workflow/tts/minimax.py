"""Fixed MiniMax synchronous MP3 TTS wire adapter."""

from __future__ import annotations

import json
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request

from .contracts import TtsConfig, TtsError


class MiniMaxTtsProvider:
    def synthesize_mp3(self, config: TtsConfig, text: str, open_request: Callable[..., object]) -> bytes:
        if config.model != "speech-2.8-turbo" or not config.voice_id: raise TtsError("TTS provider configuration is invalid")
        request = Request(config.endpoint, data=json.dumps({"model": config.model, "text": text, "stream": False, "voice_setting": {"voice_id": config.voice_id, "speed": 1, "vol": 1, "pitch": 0}, "audio_setting": {"sample_rate": 24000, "bitrate": 64000, "format": "mp3", "channel": 1}, "subtitle_enable": False}, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), headers={"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"}, method="POST")
        try:
            with open_request(request, timeout=config.timeout_seconds) as response: raw = response.read(config.max_audio_bytes * 2 + 128 * 1024 + 1)
        except (HTTPError, URLError, TimeoutError, OSError) as exc: raise TtsError("TTS request failed") from exc
        if len(raw) > config.max_audio_bytes * 2 + 128 * 1024: raise TtsError("TTS response exceeds the fixed limit")
        try: response_json = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc: raise TtsError("TTS response is invalid") from exc
        base = response_json.get("base_resp") if isinstance(response_json, dict) else None; data = response_json.get("data") if isinstance(response_json, dict) else None; extra = response_json.get("extra_info") if isinstance(response_json, dict) else None; audio_hex = data.get("audio") if isinstance(data, dict) else None
        if not isinstance(base, dict) or base.get("status_code") != 0 or not isinstance(data, dict) or data.get("status") != 2 or not isinstance(extra, dict) or extra.get("audio_format") != "mp3" or not isinstance(audio_hex, str) or not audio_hex: raise TtsError("TTS provider rejected the request")
        try: audio = bytes.fromhex(audio_hex)
        except ValueError as exc: raise TtsError("TTS audio encoding is invalid") from exc
        if not audio or len(audio) > config.max_audio_bytes: raise TtsError("TTS audio exceeds the fixed limit")
        return audio
