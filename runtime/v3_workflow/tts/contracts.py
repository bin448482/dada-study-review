"""Provider-neutral TTS configuration and adapter contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol


class TtsError(RuntimeError):
    """A redacted synthesis failure that must fall back to committed text."""


@dataclass(frozen=True)
class TtsConfig:
    """One explicit TTS deployment, independent from any workflow model."""

    api_key: str
    endpoint: str
    resource_id: str | None
    speaker: str | None
    timeout_seconds: int
    max_text_chars: int
    max_audio_bytes: int
    retention_seconds: int
    provider: str = "volcengine_seed"
    model: str | None = None
    voice_id: str | None = None

    def __post_init__(self) -> None:
        common = bool(self.api_key) and self.endpoint.startswith("https://") and self.timeout_seconds > 0 and self.max_text_chars > 0 and self.max_audio_bytes > 0 and self.retention_seconds > 0
        seed = self.provider == "volcengine_seed" and self.resource_id == "seed-tts-2.0" and bool(self.speaker) and self.model is None and self.voice_id is None
        minimax = self.provider == "minimax" and self.endpoint == "https://api.minimaxi.com/v1/t2a_v2" and self.model == "speech-2.8-turbo" and bool(self.voice_id) and self.resource_id is None and self.speaker is None
        if not common or not (seed or minimax):
            raise ValueError("TTS configuration is invalid")

    @classmethod
    def from_environment(cls, environment: dict[str, str], prefix: str = "DADA_TTS") -> "TtsConfig | None":
        if environment.get(f"{prefix}_ENABLED", "") != "1":
            return None
        try:
            common = (
                environment[f"{prefix}_API_KEY"], environment[f"{prefix}_ENDPOINT"],
                int(environment[f"{prefix}_TIMEOUT_SECONDS"]), int(environment[f"{prefix}_MAX_TEXT_CHARS"]),
                int(environment[f"{prefix}_MAX_AUDIO_BYTES"]), int(environment[f"{prefix}_RETENTION_SECONDS"]),
            )
            provider = environment.get(f"{prefix}_PROVIDER", "volcengine_seed")
            if provider == "volcengine_seed":
                return cls(common[0], common[1], environment[f"{prefix}_RESOURCE_ID"], environment[f"{prefix}_SPEAKER"], common[2], common[3], common[4], common[5], provider)
            if provider == "minimax":
                return cls(common[0], common[1], None, None, common[2], common[3], common[4], common[5], provider=provider, model=environment[f"{prefix}_MODEL"], voice_id=environment[f"{prefix}_VOICE_ID"])
            raise ValueError("TTS provider is unsupported")
        except (KeyError, ValueError) as exc:
            raise ValueError("TTS deployment is not configured") from exc


class TtsProvider(Protocol):
    def synthesize_mp3(self, config: TtsConfig, text: str, open_request: Callable[..., object]) -> bytes: ...
