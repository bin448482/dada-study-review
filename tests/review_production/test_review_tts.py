from __future__ import annotations

import base64
import json
from pathlib import Path
import sys
import tempfile
import unittest


PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / "runtime"))

from v3_review_production.tts import ReviewTtsConfig, TtsError, question_speech_text, review_delivery_speech_text, synthesize_review_delivery


def config() -> ReviewTtsConfig:
    return ReviewTtsConfig("test-key", "https://tts.invalid/unidirectional", "seed-tts-2.0", "test-speaker", 5, 100, 1024, 60)


def minimax_config() -> ReviewTtsConfig:
    return ReviewTtsConfig(
        "test-key",
        "https://api.minimaxi.com/v1/t2a_v2",
        None,
        None,
        5,
        100,
        1024,
        60,
        provider="minimax",
        model="speech-2.8-turbo",
        voice_id="male-qn-qingse",
    )


class _Response:
    def __init__(self, lines: list[bytes]) -> None:
        self._lines = lines
    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def __iter__(self): return iter(self._lines)


class _JsonResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._raw = json.dumps(payload, separators=(",", ":")).encode()

    def __enter__(self): return self
    def __exit__(self, *_args): return False
    def read(self, _limit: int) -> bytes: return self._raw


class ReviewTtsTests(unittest.TestCase):
    def test_environment_requires_the_only_implemented_provider(self) -> None:
        environment = {
            "DADA_REVIEW_TTS_ENABLED": "1",
            "DADA_REVIEW_TTS_API_KEY": "test-key",
            "DADA_REVIEW_TTS_ENDPOINT": "https://tts.invalid/unidirectional",
            "DADA_REVIEW_TTS_RESOURCE_ID": "seed-tts-2.0",
            "DADA_REVIEW_TTS_SPEAKER": "test-speaker",
            "DADA_REVIEW_TTS_TIMEOUT_SECONDS": "5",
            "DADA_REVIEW_TTS_MAX_TEXT_CHARS": "100",
            "DADA_REVIEW_TTS_MAX_AUDIO_BYTES": "1024",
            "DADA_REVIEW_TTS_RETENTION_SECONDS": "60",
            "DADA_REVIEW_TTS_PROVIDER": "volcengine_seed",
        }
        self.assertEqual(ReviewTtsConfig.from_environment(environment).provider, "volcengine_seed")
        environment["DADA_REVIEW_TTS_PROVIDER"] = "qwen_bailian"
        with self.assertRaises(ValueError):
            ReviewTtsConfig.from_environment(environment)

    def test_environment_accepts_fixed_minimax_branch(self) -> None:
        environment = {
            "DADA_REVIEW_TTS_ENABLED": "1",
            "DADA_REVIEW_TTS_API_KEY": "test-key",
            "DADA_REVIEW_TTS_PROVIDER": "minimax",
            "DADA_REVIEW_TTS_ENDPOINT": "https://api.minimaxi.com/v1/t2a_v2",
            "DADA_REVIEW_TTS_MODEL": "speech-2.8-turbo",
            "DADA_REVIEW_TTS_VOICE_ID": "male-qn-qingse",
            "DADA_REVIEW_TTS_TIMEOUT_SECONDS": "5",
            "DADA_REVIEW_TTS_MAX_TEXT_CHARS": "100",
            "DADA_REVIEW_TTS_MAX_AUDIO_BYTES": "1024",
            "DADA_REVIEW_TTS_RETENTION_SECONDS": "60",
        }
        self.assertEqual(ReviewTtsConfig.from_environment(environment), minimax_config())

    def test_mode_selects_only_locked_speech_source(self) -> None:
        self.assertEqual(question_speech_text("en_to_zh", {"prompt": "I go to school."}), "I go to school.")
        self.assertEqual(question_speech_text("spelling", {"prompt": "请听音拼写。", "speech_text": "school"}), "school")
        self.assertEqual(question_speech_text("sentence_recall", {"prompt": "请复述。", "speech_text": "I go to school."}), "I go to school.")
        self.assertIsNone(question_speech_text("mask", {"prompt": "I ___ school."}))

    def test_full_committed_reply_is_spoken_and_hidden_speech_is_appended_only_for_audio(self) -> None:
        self.assertEqual(
            review_delivery_speech_text("答得很好。\n\n请听音后输入完整英文单词。", "spelling", {"prompt": "请听音后输入完整英文单词。", "speech_text": "school"}),
            "答得很好。\n\n请听音后输入完整英文单词。\n\nschool",
        )
        self.assertEqual(
            review_delivery_speech_text("I go to school.\n\n请说中文意思。", "en_to_zh", {"prompt": "I go to school.", "instruction": "请说中文意思。"}),
            "I go to school.\n\n请说中文意思。",
        )
        self.assertEqual(review_delivery_speech_text("这次复习先停在这里。", None, None), "这次复习先停在这里。")
        self.assertIsNone(review_delivery_speech_text(None, "en_to_zh", {"prompt": "I go."}))

    def test_synthesis_writes_bounded_mp3_after_terminal_success(self) -> None:
        encoded = base64.b64encode(b"ID3test-audio").decode()
        events = [f'{{"code":0,"data":"{encoded}"}}\n'.encode(), b'{"code":20000000,"message":"OK"}\n']
        with tempfile.TemporaryDirectory() as directory:
            requests = []
            def open_request(request, **_kwargs):
                requests.append(request)
                return _Response(events)
            path = synthesize_review_delivery(config(), "请听音拼写。", "spelling", {"prompt": "请听音拼写。", "speech_text": "school"}, Path(directory), open_request)
            self.assertIsNotNone(path)
            self.assertEqual(path.read_bytes(), b"ID3test-audio")
            self.assertEqual(path.suffix, ".mp3")
            self.assertEqual(json.loads(requests[0].data)["req_params"]["text"], "请听音拼写。\n\nschool")

    def test_common_outbox_uses_only_frozen_text_with_an_injected_provider(self) -> None:
        class Provider:
            def __init__(self) -> None:
                self.calls = []

            def synthesize_mp3(self, seen_config, text, _open_request):
                self.calls.append((seen_config, text))
                return b"ID3adapter-audio"

        provider = Provider()
        with tempfile.TemporaryDirectory() as directory:
            path = synthesize_review_delivery(
                config(), "请听音拼写。", "spelling",
                {"prompt": "请听音拼写。", "speech_text": "school"},
                Path(directory), provider=provider,
            )
            self.assertEqual(provider.calls, [(config(), "请听音拼写。\n\nschool")])
            self.assertEqual(path.read_bytes(), b"ID3adapter-audio")

    def test_minimax_adapter_requests_fixed_mp3_and_decodes_hex(self) -> None:
        requests = []
        response = {
            "base_resp": {"status_code": 0},
            "data": {"status": 2, "audio": b"ID3minimax-audio".hex()},
            "extra_info": {"audio_format": "mp3", "audio_size": 17},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = synthesize_review_delivery(
                minimax_config(),
                "Please listen.",
                "en_to_zh",
                {"prompt": "Please listen."},
                Path(directory),
                lambda request, **_kwargs: requests.append(request) or _JsonResponse(response),
            )
            self.assertEqual(path.read_bytes(), b"ID3minimax-audio")
        request = requests[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
        self.assertEqual(
            json.loads(request.data),
            {
                "model": "speech-2.8-turbo",
                "text": "Please listen.",
                "stream": False,
                "voice_setting": {"voice_id": "male-qn-qingse", "speed": 1, "vol": 1, "pitch": 0},
                "audio_setting": {"sample_rate": 24000, "bitrate": 64000, "format": "mp3", "channel": 1},
                "subtitle_enable": False,
            },
        )

    def test_minimax_rejects_non_hex_or_non_mp3_response(self) -> None:
        invalid = {
            "base_resp": {"status_code": 0},
            "data": {"status": 2, "audio": "not-hex"},
            "extra_info": {"audio_format": "wav"},
        }
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(TtsError):
                synthesize_review_delivery(
                    minimax_config(), "Please listen.", "en_to_zh", {"prompt": "Please listen."}, Path(directory),
                    lambda *_args, **_kwargs: _JsonResponse(invalid),
                )
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_minimax_rejects_oversized_decoded_audio(self) -> None:
        oversized = b"x" * 1025
        response = {
            "base_resp": {"status_code": 0},
            "data": {"status": 2, "audio": oversized.hex()},
            "extra_info": {"audio_format": "mp3", "audio_size": len(oversized)},
        }
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(TtsError):
                synthesize_review_delivery(
                    minimax_config(), "Please listen.", "en_to_zh", {"prompt": "Please listen."}, Path(directory),
                    lambda *_args, **_kwargs: _JsonResponse(response),
                )
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_error_or_empty_audio_never_creates_media(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(TtsError):
                synthesize_review_delivery(config(), "I go.", "en_to_zh", {"prompt": "I go."}, Path(directory), lambda *_args, **_kwargs: _Response([b'{"code":20000000}\n']))
            self.assertEqual(list(Path(directory).iterdir()), [])
