"""Review TTS composition boundary and its fixed provider adapters."""

from .contracts import ReviewTtsConfig, TtsError, TtsProvider
from .minimax import MiniMaxTtsProvider
from .service import question_speech_text, review_delivery_speech_text, synthesize_review_delivery

__all__ = [
    "ReviewTtsConfig",
    "TtsError",
    "TtsProvider",
    "MiniMaxTtsProvider",
    "question_speech_text",
    "review_delivery_speech_text",
    "synthesize_review_delivery",
]
