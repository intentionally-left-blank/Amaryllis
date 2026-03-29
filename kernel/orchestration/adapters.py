from __future__ import annotations

from typing import Any

from kernel.contracts import CheckpointWriter, ExecutorContract


class KernelExecutorAdapter:
    """Adapter used by runtime/services to consume execution through kernel contracts."""

    def __init__(self, delegate: ExecutorContract) -> None:
        self._delegate = delegate

    def execute(
        self,
        agent: Any,
        user_id: str,
        session_id: str | None,
        user_message: str,
        checkpoint: CheckpointWriter | None = None,
        run_deadline_monotonic: float | None = None,
        resume_state: dict[str, Any] | None = None,
        run_budget: dict[str, Any] | None = None,
        run_source: str | None = None,
    ) -> dict[str, Any]:
        execute_kwargs: dict[str, Any] = {
            "agent": agent,
            "user_id": user_id,
            "session_id": session_id,
            "user_message": user_message,
            "checkpoint": checkpoint,
            "run_deadline_monotonic": run_deadline_monotonic,
            "resume_state": resume_state,
            "run_budget": run_budget,
            "run_source": run_source,
        }
        try:
            return self._delegate.execute(**execute_kwargs)
        except TypeError as exc:
            if "run_source" not in str(exc):
                raise
            execute_kwargs.pop("run_source", None)
            return self._delegate.execute(**execute_kwargs)

    def simulate_run(
        self,
        *,
        agent: Any,
        user_id: str,
        session_id: str | None,
        user_message: str,
        requested_budget: dict[str, Any] | None = None,
        max_attempts: int | None = None,
    ) -> dict[str, Any]:
        delegate_simulate = getattr(self._delegate, "simulate_run", None)
        if delegate_simulate is None or not callable(delegate_simulate):
            raise ValueError("Task executor does not support simulation mode")
        return delegate_simulate(
            agent=agent,
            user_id=user_id,
            session_id=session_id,
            user_message=user_message,
            requested_budget=requested_budget,
            max_attempts=max_attempts,
        )
