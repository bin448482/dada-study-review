"""Fixed Volcengine Seed TTS wire adapter."""

from __future__ import annotations

import base64
import binascii
import json
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request

from .contracts import TtsConfig, TtsError


class VolcengineSeedTtsProvider:
    def synthesize_mp3(self, config: TtsConfig, text: str, open_request: Callable[..., object]) -> bytes:
        if config.resource_id is None or config.speaker is None:
            raise TtsError("TTS provider configuration is invalid")
        request = Request(
            config.endpoint,
            data=json.dumps({"req_params": {"text": text, "speaker": config.speaker, "audio_params": {"format": "mp3", "sample_rate": 24000}}}, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            headers={"X-Api-Key": config.api_key, "X-Api-Resource-Id": config.resource_id, "Content-Type": "application/json", "X-Control-Require-Usage-Tokens-Return": "*"}, method="POST",
        )
        audio = bytearray(); completed = False
        try:
            with open_request(request, timeout=config.timeout_seconds) as response:
                for raw_line in response:
                    if len(raw_line) > 128 * 1024:
                        raise TtsError("TTS response line exceeds the fixed limit")
                    if not raw_line.strip():
                        continue
                    try: event = json.loads(raw_line)
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc: raise TtsError("TTS response is invalid") from exc
                    if not isinstance(event, dict) or not isinstance(event.get("code"), int): raise TtsError("TTS response event is invalid")
                    code = event["code"]
                    if code == 20000000: completed = True; break
                    if code != 0: raise TtsError("TTS provider rejected the request")
                    payload = event.get("data")
                    if payload is None: continue
                    if not isinstance(payload, str): raise TtsError("TTS audio event is invalid")
                    try: audio.extend(base64.b64decode(payload, validate=True))
                    except (ValueError, binascii.Error) as exc: raise TtsError("TTS audio encoding is invalid") from exc
                    if len(audio) > config.max_audio_bytes: raise TtsError("TTS audio exceeds the fixed limit")
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise TtsError("TTS request failed") from exc
        if not completed or not audio: raise TtsError("TTS returned no complete audio")
        return bytes(audio)
