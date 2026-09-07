from __future__ import annotations

from pathlib import Path
import os
import tempfile
import unittest
from unittest.mock import patch

import sys

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "runtime"))

from v3_workflow.tts import TtsConfig, TtsError, synthesize_tts_delivery
from v3_dialogue.contracts import DialogueTurnDelivery
from v3_dialogue_production import cli


class FakeProvider:
    def __init__(self, audio: bytes = b"ID3-dialogue") -> None:
        self.audio = audio
        self.calls: list[tuple[TtsConfig, str]] = []

    def synthesize_mp3(self, config: TtsConfig, text: str, _open_request: object) -> bytes:
        self.calls.append((config, text))
        return self.audio


def config() -> TtsConfig:
    return TtsConfig("dialogue-tts-key", "https://tts.invalid/unidirectional", "seed-tts-2.0", "dialogue-speaker", 5, 200, 1024, 60)


class DialogueTtsTests(unittest.TestCase):
    def test_production_cli_synthesizes_model_reply_without_program_status(self) -> None:
        reply = "这个问题是在问你下一步想做什么项目。\n\nI would like to do an experiment next.\n\n请再说一遍。"
        state_text = "【英语对话中】"
        progress_text = "本单元进度：已完成 8 / 28 个目标。\n\n本轮计划：\n场景：介绍学校；交流步骤：Maths。"
        captured: list[str] = []

        class FakeService:
            def handle(self, _ingress: object) -> DialogueTurnDelivery:
                return DialogueTurnDelivery(True, reply_text=f"{state_text}\n\n{progress_text}\n\n{reply}", state_text=state_text, progress_text=progress_text, speech_text="I would like to do an experiment next.")

            def close(self) -> None:
                return None

        environment = {
            "DADA_DIALOGUE_ARCHIVE_ROOT": "/tmp/dada-dialogue-tts-test",
            "DADA_DIALOGUE_DEFINITION_DIR": str(PROJECT / "skill" / "dada-dialogue-state-machine"),
            "DADA_DIALOGUE_DEFINITION_DIGEST": "a" * 64,
            "DADA_DIALOGUE_MODEL_PROVIDER": "test-provider", "DADA_DIALOGUE_MODEL": "test-model",
            "DADA_DIALOGUE_MODEL_ENDPOINT": "https://provider.invalid/dialogue", "DADA_DIALOGUE_MODEL_API_STYLE": "responses",
            "DADA_DIALOGUE_MODEL_API_KEY": "test-model-key", "DADA_DIALOGUE_MODEL_USER_AGENT": "DadaDialogueTest/1",
            "DADA_DIALOGUE_UNIT_PATH": str(PROJECT / "config/dialogue-units/grade6-english-unit-1-school-life/unit.v1.json"),
            "DADA_DIALOGUE_POLICY_PATH": str(PROJECT / "config/dialogue-policy.json"),
            "DADA_DIALOGUE_REVIEW_SCHEDULE_PATH": str(PROJECT / "config/review-schedule.test.json"),
            "DADA_REVIEW_TTS_ENABLED": "1", "DADA_REVIEW_TTS_API_KEY": "test-tts-key",
            "DADA_REVIEW_TTS_PROVIDER": "volcengine_seed", "DADA_REVIEW_TTS_ENDPOINT": "https://tts.invalid/unidirectional",
            "DADA_REVIEW_TTS_RESOURCE_ID": "seed-tts-2.0", "DADA_REVIEW_TTS_SPEAKER": "speaker",
            "DADA_REVIEW_TTS_TIMEOUT_SECONDS": "5", "DADA_REVIEW_TTS_MAX_TEXT_CHARS": "400",
            "DADA_REVIEW_TTS_MAX_AUDIO_BYTES": "1024", "DADA_REVIEW_TTS_RETENTION_SECONDS": "60",
        }

        def fake_synthesize(_tts: TtsConfig, text: str, _outbox: Path) -> None:
            captured.append(text)
            return None

        with patch.dict(os.environ, environment, clear=False), patch.object(cli, "build_dialogue_service", return_value=FakeService()), patch.object(cli, "synthesize_tts_delivery", side_effect=fake_synthesize):
            result = cli.run({"message_text": "回答", "received_at": "2026-09-03T00:00:00Z", "external_session_ref": "child-test", "start_requested": False})

        self.assertTrue(result["ok"])
        self.assertEqual(captured, [reply])
        self.assertNotIn(state_text, captured[0])
        self.assertNotIn(progress_text, captured[0])
        self.assertNotIn("本轮计划", captured[0])

    def test_dialogue_uses_the_shared_review_tts_environment(self) -> None:
        environment = {
            "DADA_REVIEW_TTS_ENABLED": "1", "DADA_REVIEW_TTS_API_KEY": "shared-key",
            "DADA_REVIEW_TTS_ENDPOINT": "https://tts.invalid/unidirectional", "DADA_REVIEW_TTS_PROVIDER": "volcengine_seed",
            "DADA_REVIEW_TTS_RESOURCE_ID": "seed-tts-2.0", "DADA_REVIEW_TTS_SPEAKER": "speaker",
            "DADA_REVIEW_TTS_TIMEOUT_SECONDS": "5", "DADA_REVIEW_TTS_MAX_TEXT_CHARS": "200",
            "DADA_REVIEW_TTS_MAX_AUDIO_BYTES": "1024", "DADA_REVIEW_TTS_RETENTION_SECONDS": "60",
        }
        value = TtsConfig.from_environment(environment, "DADA_REVIEW_TTS")
        self.assertIsNotNone(value)
        self.assertEqual(value.api_key, "shared-key")
        self.assertIsNone(TtsConfig.from_environment({"DADA_REVIEW_TTS_ENABLED": "0"}, "DADA_REVIEW_TTS"))

    def test_shared_service_writes_complete_chinese_and_english_dialogue_reply_to_bounded_outbox(self) -> None:
        provider = FakeProvider()
        reply = "这个问题是在问你下一步想做什么项目。\n\nI would like to do an experiment next.\n\n请再说一遍。"
        with tempfile.TemporaryDirectory() as directory:
            path = synthesize_tts_delivery(config(), reply, Path(directory), provider=provider)
            self.assertIsNotNone(path)
            self.assertEqual(Path(path).read_bytes(), b"ID3-dialogue")
            self.assertEqual(provider.calls[0][1], reply)

    def test_shared_service_rejects_overlong_dialogue_reply_before_provider(self) -> None:
        provider = FakeProvider()
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(TtsError):
                synthesize_tts_delivery(TtsConfig("key", "https://tts.invalid", "seed-tts-2.0", "speaker", 5, 3, 1024, 60), "long", Path(directory), provider=provider)
            self.assertEqual(provider.calls, [])


if __name__ == "__main__":
    unittest.main()
