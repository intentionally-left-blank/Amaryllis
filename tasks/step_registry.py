from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

ISSUE_DONE = "done"
ISSUE_FAILED = "failed"


@dataclass(frozen=True)
class StepExecutionContext:
    issue_id: str
    step_kind: str
    description: str
    requires_tools: bool
    objective: str
    expected_output: str
    hints: dict[str, Any] = field(default_factory=dict)
    dependency_artifacts: dict[str, dict[str, Any]] = field(default_factory=dict)
    dependency_points: list[str] = field(default_factory=list)
    issue_deadline_monotonic: float = 0.0


@dataclass(frozen=True)
class StepExecutionResult:
    status: str = ISSUE_DONE
    payload: dict[str, Any] = field(default_factory=dict)
    artifact_key: str = "result"
    reason: str | None = None

    @staticmethod
    def done(payload: dict[str, Any], *, artifact_key: str = "result") -> StepExecutionResult:
        return StepExecutionResult(
            status=ISSUE_DONE,
            payload=dict(payload),
            artifact_key=str(artifact_key or "result").strip() or "result",
            reason=None,
        )

    @staticmethod
    def failed(reason: str, payload: dict[str, Any] | None = None) -> StepExecutionResult:
        return StepExecutionResult(
            status=ISSUE_FAILED,
            payload=dict(payload or {}),
            artifact_key="result",
            reason=str(reason or "Step execution failed.").strip() or "Step execution failed.",
        )


StepExecutor = Callable[[StepExecutionContext], StepExecutionResult]


class StepExecutorRegistry:
    def __init__(self) -> None:
        self._executors: dict[str, StepExecutor] = {}

    def register(self, kinds: str | list[str] | tuple[str, ...], executor: StepExecutor) -> None:
        if isinstance(kinds, str):
            values = [kinds]
        else:
            values = [str(item) for item in kinds]
        for item in values:
            normalized = str(item or "").strip().lower()
            if not normalized:
                continue
            self._executors[normalized] = executor

    def resolve(self, step_kind: str) -> StepExecutor | None:
        normalized = str(step_kind or "").strip().lower()
        if not normalized:
            return None
        return self._executors.get(normalized)

    def known_kinds(self) -> list[str]:
        return sorted(self._executors.keys())
