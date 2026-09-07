"""Provider-neutral TTS contracts and bounded MP3 synthesis."""

from .contracts import TtsConfig, TtsError, TtsProvider
from .service import synthesize_tts_delivery

__all__ = ["TtsConfig", "TtsError", "TtsProvider", "synthesize_tts_delivery"]
