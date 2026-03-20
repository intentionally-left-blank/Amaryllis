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
    ) -> dict[str, Any]:
        return self._delegate.execute(
            agent=agent,
            user_id=user_id,
            session_id=session_id,
            user_message=user_message,
            checkpoint=checkpoint,
            run_deadline_monotonic=run_deadline_monotonic,
            resume_state=resume_state,
            run_budget=run_budget,
        )
