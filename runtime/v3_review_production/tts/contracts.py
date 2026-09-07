"""Compatibility names for the shared TTS contract."""

from v3_workflow.tts.contracts import TtsConfig, TtsError, TtsProvider


class ReviewTtsConfig(TtsConfig):
    @classmethod
    def from_environment(cls, environment: dict[str, str]) -> "ReviewTtsConfig | None":
        value = TtsConfig.from_environment(environment, "DADA_REVIEW_TTS")
        return None if value is None else cls(**value.__dict__)
