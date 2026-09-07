"""Fixed stdin/stdout boundary for the Dialogue inbound hook."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from v3_dialogue.contracts.dialogue_turn import AuthorizedDialogueIngress, DialogueTurnDelivery
from v3_workflow.tts import TtsConfig, TtsError, synthesize_tts_delivery

from .gateway import ProductionGatewayConfig, UrllibBearerResponsesTransport
from .service import build_dialogue_service


_REQUIRED_ENV = (
    "DADA_DIALOGUE_ARCHIVE_ROOT", "DADA_DIALOGUE_DEFINITION_DIR", "DADA_DIALOGUE_DEFINITION_DIGEST",
    "DADA_DIALOGUE_MODEL_PROVIDER", "DADA_DIALOGUE_MODEL", "DADA_DIALOGUE_MODEL_ENDPOINT",
    "DADA_DIALOGUE_MODEL_API_STYLE", "DADA_DIALOGUE_MODEL_API_KEY", "DADA_DIALOGUE_MODEL_USER_AGENT",
    "DADA_DIALOGUE_UNIT_PATH", "DADA_DIALOGUE_POLICY_PATH", "DADA_DIALOGUE_REVIEW_SCHEDULE_PATH",
)


def _environment() -> dict[str, str]:
    values = {name: os.environ.get(name, "") for name in _REQUIRED_ENV}
    if any(not value for value in values.values()):
        raise ValueError("v3 dialogue deployment is not configured")
    return values


def _ingress(value: Any) -> AuthorizedDialogueIngress:
    if not isinstance(value, dict) or set(value) != {"message_text", "received_at", "external_session_ref", "start_requested"}:
        raise ValueError("v3 dialogue ingress is invalid")
    message, received_at, session, started = value["message_text"], value["received_at"], value["external_session_ref"], value["start_requested"]
    if not isinstance(message, str) or not message or not isinstance(received_at, str) or not isinstance(session, str) or not session or not isinstance(started, bool):
        raise ValueError("v3 dialogue ingress fields are invalid")
    return AuthorizedDialogueIngress(message, received_at, session, started)


def _dialogue_tts_text(delivery: DialogueTurnDelivery) -> str | None:
    """Select model dialogue content without program-owned status text."""

    text = delivery.reply_text
    if not isinstance(text, str) or not text:
        return text
    program_prefix = "\n\n".join(part for part in (delivery.state_text, delivery.progress_text) if part)
    if program_prefix and text.startswith(program_prefix):
        text = text[len(program_prefix):].lstrip()
    return text or delivery.reply_text


def run(value: Any) -> dict[str, object]:
    environment = {**os.environ, **_environment()}
    root = Path(environment["DADA_DIALOGUE_ARCHIVE_ROOT"]).resolve(strict=False)
    service = build_dialogue_service(
        root / "workflow-v3.sqlite3", Path(environment["DADA_DIALOGUE_DEFINITION_DIR"]), environment["DADA_DIALOGUE_DEFINITION_DIGEST"],
        ProductionGatewayConfig(environment["DADA_DIALOGUE_MODEL_PROVIDER"], environment["DADA_DIALOGUE_MODEL"], environment["DADA_DIALOGUE_MODEL_ENDPOINT"], 120, environment["DADA_DIALOGUE_MODEL_API_STYLE"], environment["DADA_DIALOGUE_MODEL_USER_AGENT"]),
        UrllibBearerResponsesTransport(environment["DADA_DIALOGUE_MODEL_API_KEY"], environment["DADA_DIALOGUE_MODEL_USER_AGENT"]),
        Path(environment["DADA_DIALOGUE_UNIT_PATH"]), Path(environment["DADA_DIALOGUE_POLICY_PATH"]), Path(environment["DADA_DIALOGUE_REVIEW_SCHEDULE_PATH"]),
    )
    try:
        delivery = service.handle(_ingress(value))
        media_path: str | None = None
        try:
            tts = TtsConfig.from_environment(environment, "DADA_REVIEW_TTS")
            if tts is not None:
                generated = synthesize_tts_delivery(
                    # Dialogue TTS reads the complete model dialogue content,
                    # including Chinese guidance, the English model sentence,
                    # and the repeat instruction. Program-owned status and
                    # progress text remain a separate visible channel.
                    tts, _dialogue_tts_text(delivery), root / "outbound-media"
                )
                media_path = None if generated is None else str(generated)
        except (TtsError, ValueError, OSError):
            media_path = None
        return {
            "ok": True, "handled": delivery.handled, "reply_text": delivery.reply_text,
            "state_text": delivery.state_text, "progress_text": delivery.progress_text,
            "media_path": media_path,
        }
    finally:
        service.close()


def main() -> int:
    try:
        result = run(json.loads(sys.stdin.read()))
    except (ValueError, OSError, json.JSONDecodeError):
        result = {"ok": False, "handled": False, "reply_text": None}
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
