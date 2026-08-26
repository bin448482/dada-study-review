"""Fixed stdin/stdout process boundary for the Dada v3 entry inbound plugin."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from v3_entry.contracts.entry_turn import AuthorizedEntryIngress
from v3_review_production import ProductionGatewayConfig as ReviewGatewayConfig
from v3_review_production import build_review_service
from v3_review_production.gateway import UrllibBearerResponsesTransport as ReviewTransport
from v3_workflow.policy.review_schedule import ReviewSchedulePolicy

from .gateway import ProductionGatewayConfig, UrllibBearerResponsesTransport
from .service import build_entry_service


_REQUIRED_ENV = (
    "DADA_ENTRY_ARCHIVE_ROOT",
    "DADA_ENTRY_DEFINITION_DIR",
    "DADA_ENTRY_DEFINITION_DIGEST",
    "DADA_ENTRY_MODEL_PROVIDER",
    "DADA_ENTRY_MODEL",
    "DADA_ENTRY_MODEL_ENDPOINT",
    "DADA_ENTRY_MODEL_API_STYLE",
    "DADA_ENTRY_MODEL_API_KEY",
    "DADA_ENTRY_REVIEW_DEFINITION_DIR",
    "DADA_ENTRY_REVIEW_DEFINITION_DIGEST",
    "DADA_ENTRY_REVIEW_MODEL_PROVIDER",
    "DADA_ENTRY_REVIEW_MODEL",
    "DADA_ENTRY_REVIEW_MODEL_ENDPOINT",
    "DADA_ENTRY_REVIEW_MODEL_API_STYLE",
    "DADA_ENTRY_REVIEW_SCHEDULE_PATH",
    "DADA_REVIEW_MODEL_API_KEY",
)


def _required_environment() -> dict[str, str]:
    values = {name: os.environ.get(name, "") for name in _REQUIRED_ENV}
    if any(not value for value in values.values()):
        raise ValueError("v3 entry deployment is not configured")
    return values


def _ingress(value: Any) -> AuthorizedEntryIngress:
    if not isinstance(value, dict) or set(value) != {"message_text", "received_at", "external_session_ref", "start_requested"}:
        raise ValueError("v3 entry ingress is invalid")
    message = value["message_text"]
    received_at = value["received_at"]
    external_session_ref = value["external_session_ref"]
    start_requested = value["start_requested"]
    if not isinstance(message, str) or not message or not isinstance(received_at, str) or not isinstance(external_session_ref, str) or not external_session_ref or not isinstance(start_requested, bool):
        raise ValueError("v3 entry ingress fields are invalid")
    return AuthorizedEntryIngress(message, received_at, external_session_ref, start_requested)


def run(value: Any) -> dict[str, object]:
    environment = _required_environment()
    archive_root = Path(environment["DADA_ENTRY_ARCHIVE_ROOT"]).resolve(strict=False)
    database_path = archive_root / "workflow-v3.sqlite3"
    policy = ReviewSchedulePolicy.from_file(Path(environment["DADA_ENTRY_REVIEW_SCHEDULE_PATH"]))
    review_service = build_review_service(
        database_path,
        Path(environment["DADA_ENTRY_REVIEW_DEFINITION_DIR"]),
        environment["DADA_ENTRY_REVIEW_DEFINITION_DIGEST"],
        ReviewGatewayConfig(
            environment["DADA_ENTRY_REVIEW_MODEL_PROVIDER"],
            environment["DADA_ENTRY_REVIEW_MODEL"],
            environment["DADA_ENTRY_REVIEW_MODEL_ENDPOINT"],
            120,
            environment["DADA_ENTRY_REVIEW_MODEL_API_STYLE"],
        ),
        ReviewTransport(environment["DADA_REVIEW_MODEL_API_KEY"]),
        policy,
    )
    service = build_entry_service(
        database_path,
        Path(environment["DADA_ENTRY_DEFINITION_DIR"]),
        environment["DADA_ENTRY_DEFINITION_DIGEST"],
        ProductionGatewayConfig(
            environment["DADA_ENTRY_MODEL_PROVIDER"],
            environment["DADA_ENTRY_MODEL"],
            environment["DADA_ENTRY_MODEL_ENDPOINT"],
            120,
            environment["DADA_ENTRY_MODEL_API_STYLE"],
        ),
        UrllibBearerResponsesTransport(environment["DADA_ENTRY_MODEL_API_KEY"]),
        policy=policy,
        review_handoff=review_service,
    )
    try:
        delivery = service.handle(_ingress(value))
        return {"ok": True, "handled": delivery.handled, "reply_text": delivery.reply_text}
    finally:
        service.close()
        review_service.close()


def main() -> int:
    try:
        raw = sys.stdin.read()
        result = run(json.loads(raw))
    except (ValueError, OSError, json.JSONDecodeError):
        # Never send provider details, credentials, paths, or exception text to the plugin or child.
        result = {"ok": False, "handled": False, "reply_text": None}
    sys.stdout.write(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
