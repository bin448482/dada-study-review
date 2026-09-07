"""Fixed stdin/stdout process boundary for a future Dada v3 review inbound plugin."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from v3_review.contracts.review_turn import AuthorizedReviewIngress
from v3_workflow.policy.review_schedule import ReviewSchedulePolicy
from v3_entry_production.bundle import load_state_machine_definition as load_entry_definition

from .gateway import ProductionGatewayConfig, UrllibBearerResponsesTransport
from .service import build_review_service
from .tts import ReviewTtsConfig, TtsError, synthesize_review_delivery


_REQUIRED_ENV = (
    "DADA_REVIEW_ARCHIVE_ROOT", "DADA_REVIEW_DEFINITION_DIR", "DADA_REVIEW_DEFINITION_DIGEST",
    "DADA_REVIEW_MODEL_PROVIDER", "DADA_REVIEW_MODEL", "DADA_REVIEW_MODEL_ENDPOINT",
    "DADA_REVIEW_MODEL_API_STYLE", "DADA_REVIEW_MODEL_API_KEY", "DADA_REVIEW_MODEL_USER_AGENT", "DADA_REVIEW_SCHEDULE_PATH",
    "DADA_REVIEW_ENTRY_DEFINITION_DIR", "DADA_REVIEW_ENTRY_DEFINITION_DIGEST",
)


def _environment() -> dict[str, str]:
    values = {name: os.environ.get(name, "") for name in _REQUIRED_ENV}
    if any(not value for value in values.values()):
        raise ValueError("v3 review deployment is not configured")
    return values


def _ingress(value: Any) -> AuthorizedReviewIngress:
    if not isinstance(value, dict) or set(value) != {"message_text", "received_at", "external_session_ref", "start_requested"}:
        raise ValueError("v3 review ingress is invalid")
    message, received_at, session, started = value["message_text"], value["received_at"], value["external_session_ref"], value["start_requested"]
    if not isinstance(message, str) or not message or not isinstance(received_at, str) or not isinstance(session, str) or not session or not isinstance(started, bool):
        raise ValueError("v3 review ingress fields are invalid")
    return AuthorizedReviewIngress(message, received_at, session, started)


def run(value: Any) -> dict[str, object]:
    environment = {**os.environ, **_environment()}
    archive_root = Path(environment["DADA_REVIEW_ARCHIVE_ROOT"]).resolve(strict=False)
    service = build_review_service(
        archive_root / "workflow-v3.sqlite3", Path(environment["DADA_REVIEW_DEFINITION_DIR"]), environment["DADA_REVIEW_DEFINITION_DIGEST"],
        ProductionGatewayConfig(environment["DADA_REVIEW_MODEL_PROVIDER"], environment["DADA_REVIEW_MODEL"], environment["DADA_REVIEW_MODEL_ENDPOINT"], 120, environment["DADA_REVIEW_MODEL_API_STYLE"], environment["DADA_REVIEW_MODEL_USER_AGENT"]),
        UrllibBearerResponsesTransport(environment["DADA_REVIEW_MODEL_API_KEY"], environment["DADA_REVIEW_MODEL_USER_AGENT"]), ReviewSchedulePolicy.from_file(Path(environment["DADA_REVIEW_SCHEDULE_PATH"])),
        load_entry_definition(Path(environment["DADA_REVIEW_ENTRY_DEFINITION_DIR"]), "dada-entry-state-machine", environment["DADA_REVIEW_ENTRY_DEFINITION_DIGEST"]).system_prompt,
    )
    try:
        delivery = service.handle(_ingress(value))
        media_path: str | None = None
        try:
            tts = ReviewTtsConfig.from_environment(environment)
            if tts is not None:
                generated = synthesize_review_delivery(
                    tts, delivery.speech_text or delivery.reply_text, delivery.question_mode, delivery.question_json, archive_root / "outbound-media"
                )
                media_path = None if generated is None else str(generated)
        except (TtsError, ValueError, OSError):
            media_path = None
        return {"ok": True, "handled": delivery.handled, "reply_text": delivery.reply_text, "progress_text": delivery.progress_text, "no_due_item": delivery.no_due_item, "media_path": media_path}
    finally:
        service.close()


def main() -> int:
    try: result = run(json.loads(sys.stdin.read()))
    except (ValueError, OSError, json.JSONDecodeError): result = {"ok": False, "handled": False, "reply_text": None, "progress_text": None, "no_due_item": False, "media_path": None}
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
