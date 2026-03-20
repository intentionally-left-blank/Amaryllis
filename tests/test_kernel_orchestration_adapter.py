from __future__ import annotations

import unittest

from kernel.contracts import ExecutorContract
from kernel.orchestration import KernelExecutorAdapter
from kernel.orchestration.core import execute_task_run as kernel_execute_task_run
from tasks.execution.orchestrator import execute_task_run as legacy_execute_task_run


class _FakeExecutor:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def execute(
        self,
        agent: object,
        user_id: str,
        session_id: str | None,
        user_message: str,
        checkpoint=None,
        run_deadline_monotonic: float | None = None,
        resume_state: dict[str, object] | None = None,
        run_budget: dict[str, object] | None = None,
    ) -> dict[str, object]:
        self.calls.append(
            {
                "agent": agent,
                "user_id": user_id,
                "session_id": session_id,
                "user_message": user_message,
                "checkpoint": checkpoint,
                "run_deadline_monotonic": run_deadline_monotonic,
                "resume_state": resume_state,
                "run_budget": run_budget,
            }
        )
        return {"ok": True, "message": user_message}


class KernelOrchestrationAdapterTests(unittest.TestCase):
    def test_kernel_adapter_delegates_execute(self) -> None:
        delegate = _FakeExecutor()
        adapter = KernelExecutorAdapter(delegate)

        result = adapter.execute(
            agent={"id": "agent-1"},
            user_id="user-1",
            session_id="session-1",
            user_message="hello",
            run_budget={"max_tokens": 123},
        )

        self.assertTrue(result.get("ok"))
        self.assertEqual(result.get("message"), "hello")
        self.assertEqual(len(delegate.calls), 1)
        self.assertEqual(delegate.calls[0].get("user_id"), "user-1")

    def test_kernel_adapter_satisfies_executor_contract(self) -> None:
        adapter = KernelExecutorAdapter(_FakeExecutor())
        self.assertIsInstance(adapter, ExecutorContract)

    def test_legacy_orchestrator_is_kernel_shim(self) -> None:
        self.assertIs(legacy_execute_task_run, kernel_execute_task_run)


if __name__ == "__main__":
    unittest.main()
