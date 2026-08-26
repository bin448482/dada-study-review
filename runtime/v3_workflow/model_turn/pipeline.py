"""The one retry/audit/validation/commit lifecycle for v3 model turns."""

from __future__ import annotations

from typing import Any, Protocol

from .contracts import FailureKind, ModelTurnCommit, ModelTurnOutcome, ModelTurnSpec


class ModelTurnContractError(ValueError):
    """An adapter rejected a structured model result without business mutation."""


class ModelTurnAdapter(Protocol):
    """Narrow stage boundary consumed by :class:`ModelTurnPipeline`.

    Implementations may use a stage-specific Gateway and repository, but must
    not give the pipeline English business rules or raw SQL access.
    """

    def prepare(self, turn: Any) -> Any: ...
    def execute(self, prepared: Any) -> Any: ...
    def append_log_event(self, event_type: str, payload: dict[str, Any], created_at: str) -> str: ...
    def validate(self, output: Any, turn: Any) -> Any: ...
    def commit(self, result: Any, source_llm_response_event_id: str) -> ModelTurnCommit: ...
    def commit_terminal_failure(self, failure_kind: FailureKind) -> ModelTurnCommit: ...


class ModelTurnPipeline:
    """Execute at most two model attempts and at most two commit attempts.

    Model retries always reuse the exact frozen `ModelTurnSpec.turn`. A commit
    retry reuses the validated result and never asks the model again.
    """

    def run(self, spec: ModelTurnSpec, adapter: ModelTurnAdapter) -> ModelTurnOutcome:
        result: Any | None = None
        response_event_id: str | None = None
        for attempt in (1, 2):
            try:
                prepared = adapter.prepare(spec.turn)
                adapter.append_log_event(
                    "llm_request",
                    {
                        "provider": prepared.provider,
                        "model": prepared.model,
                        "request": prepared.request,
                        "source_child_event_id": spec.source_child_event_id,
                    },
                    spec.created_at,
                )
                execution = adapter.execute(prepared)
            except Exception:
                if not self._record_failure(adapter, spec, "gateway", attempt):
                    return self._infrastructure_failure()
                if attempt == 2:
                    return self._terminal(adapter, "gateway")
                continue

            try:
                self._record_execution(adapter, spec, execution)
                response_event_id = adapter.append_log_event(
                    "llm_response",
                    {
                        "task_contract_name": spec.task_contract_name,
                        "task_contract_version": spec.task_contract_version,
                        "output": execution.output,
                        "source_child_event_id": spec.source_child_event_id,
                    },
                    spec.created_at,
                )
                if execution.tool_calls or execution.tool_results:
                    raise ModelTurnContractError("state-machine model result may not include tools")
                result = adapter.validate(execution.output, spec.turn)
            except Exception:
                if not self._record_failure(adapter, spec, "contract", attempt):
                    return self._infrastructure_failure()
                if attempt == 2:
                    return self._terminal(adapter, "contract")
                continue
            break

        if result is None or response_event_id is None:
            return self._infrastructure_failure()
        for attempt in (1, 2):
            try:
                committed = adapter.commit(result, response_event_id)
                return ModelTurnOutcome(True, False, False, committed.last_event_id, committed.reply_text, result, committed.metadata)
            except Exception:
                if not self._record_failure(adapter, spec, "commit", attempt):
                    return self._infrastructure_failure()
                if attempt == 2:
                    return self._terminal(adapter, "commit")
        return self._infrastructure_failure()

    @staticmethod
    def _record_execution(adapter: ModelTurnAdapter, spec: ModelTurnSpec, execution: Any) -> None:
        if execution.internal_reasoning is None:
            adapter.append_log_event("internal_reasoning_unavailable", {"reason_code": "provider_not_returned"}, spec.created_at)
        else:
            adapter.append_log_event("internal_reasoning", {"text": execution.internal_reasoning}, spec.created_at)
        for tool_call in execution.tool_calls:
            adapter.append_log_event("tool_call", tool_call, spec.created_at)
        for tool_result in execution.tool_results:
            adapter.append_log_event("tool_result", tool_result, spec.created_at)

    @staticmethod
    def _record_failure(adapter: ModelTurnAdapter, spec: ModelTurnSpec, failure_kind: FailureKind, attempt: int) -> bool:
        try:
            # Keep the existing coarse observability event useful to un-migrated
            # archives, then add the precise attempt event where supported.
            reason = "provider_failed" if failure_kind == "gateway" else "contract_rejected"
            adapter.append_log_event("internal_reasoning_unavailable", {"reason_code": reason}, spec.created_at)
            try:
                adapter.append_log_event(
                    "model_turn_attempt_failed",
                    {"source_child_event_id": spec.source_child_event_id, "phase": failure_kind, "attempt": attempt},
                    spec.created_at,
                )
            except Exception:
                # Existing archives require an explicit DDL migration. The
                # coarse event above is enough to retain safe retry semantics.
                pass
            return True
        except Exception:
            return False

    @staticmethod
    def _terminal(adapter: ModelTurnAdapter, failure_kind: FailureKind) -> ModelTurnOutcome:
        try:
            committed = adapter.commit_terminal_failure(failure_kind)
            return ModelTurnOutcome(False, True, False, committed.last_event_id, committed.reply_text)
        except Exception:
            return ModelTurnPipeline._infrastructure_failure()

    @staticmethod
    def _infrastructure_failure() -> ModelTurnOutcome:
        return ModelTurnOutcome(False, False, True, None, None)
