"""Deterministic contract tests for the shared model-turn lifecycle."""

from __future__ import annotations

import unittest

from v3_workflow.model_turn import ModelTurnCommit, ModelTurnPipeline, ModelTurnSpec


class _Prepared:
    provider = "fake"
    model = "model"
    request = {"task": "frozen"}


class _Execution:
    def __init__(self, output: object) -> None:
        self.output = output
        self.internal_reasoning = None
        self.tool_calls = ()
        self.tool_results = ()


class _Adapter:
    def __init__(self, executions: list[object], *, commit_failures: int = 0) -> None:
        self.executions = list(executions)
        self.commit_failures = commit_failures
        self.prepared_turns: list[object] = []
        self.logs: list[tuple[str, dict]] = []
        self.commit_values: list[object] = []
        self.terminal_calls = 0

    def prepare(self, turn):
        self.prepared_turns.append(turn)
        return _Prepared()

    def execute(self, prepared):
        value = self.executions.pop(0)
        if isinstance(value, Exception):
            raise value
        return _Execution(value)

    def append_log_event(self, event_type, payload, created_at):
        self.logs.append((event_type, payload))
        return f"event-{len(self.logs)}"

    def validate(self, output, turn):
        if output == "invalid":
            raise ValueError("bad contract")
        return {"validated": output, "turn": turn}

    def commit(self, result, source_llm_response_event_id):
        self.commit_values.append(result)
        if self.commit_failures:
            self.commit_failures -= 1
            raise RuntimeError("transaction failed")
        return ModelTurnCommit("committed", "visible")

    def commit_terminal_failure(self, failure_kind):
        self.terminal_calls += 1
        return ModelTurnCommit("terminal", f"retry-{failure_kind}")


class ModelTurnPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.turn = {"message": "frozen"}
        self.spec = ModelTurnSpec("workflow", "child-event", "2026-08-25T00:00:00Z", "task", 1, self.turn)

    def test_contract_failure_retries_the_same_frozen_turn_once_then_commits_once(self) -> None:
        adapter = _Adapter(["invalid", "valid"])
        outcome = ModelTurnPipeline().run(self.spec, adapter)
        self.assertTrue(outcome.success)
        self.assertEqual(adapter.prepared_turns, [self.turn, self.turn])
        self.assertEqual(len(adapter.commit_values), 1)
        failures = [payload for event, payload in adapter.logs if event == "model_turn_attempt_failed"]
        self.assertEqual(failures, [{"source_child_event_id": "child-event", "phase": "contract", "attempt": 1}])

    def test_commit_retry_reuses_validated_result_without_second_model_call(self) -> None:
        adapter = _Adapter(["valid"], commit_failures=1)
        outcome = ModelTurnPipeline().run(self.spec, adapter)
        self.assertTrue(outcome.success)
        self.assertEqual(adapter.prepared_turns, [self.turn])
        self.assertEqual(len(adapter.commit_values), 2)
        self.assertEqual(adapter.commit_values[0], adapter.commit_values[1])

    def test_two_gateway_failures_are_a_terminal_visible_outcome(self) -> None:
        adapter = _Adapter([RuntimeError("offline"), RuntimeError("offline")])
        outcome = ModelTurnPipeline().run(self.spec, adapter)
        self.assertTrue(outcome.terminal_failure)
        self.assertEqual(outcome.reply_text, "retry-gateway")
        self.assertEqual(adapter.terminal_calls, 1)


if __name__ == "__main__":
    unittest.main()
