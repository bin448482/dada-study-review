"""Injected ModelGateway port and deterministic fake for v3 entry tests."""

from .fake import FakeModelGateway, audit_result, reply_only
from .port import GatewayError, ModelGateway

__all__ = ["FakeModelGateway", "GatewayError", "ModelGateway", "audit_result", "reply_only"]
