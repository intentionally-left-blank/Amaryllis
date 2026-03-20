from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable

from agents.agent import Agent
from memory.models import MemoryContext
from planner.planner import PlanStep
from tools.tool_registry import ToolDefinition

# Versioned cognitive-kernel contract surface.
KERNEL_CONTRACTS_VERSION = "kernel.contracts.v1"

CheckpointWriter = Callable[[dict[str, Any]], None]


@runtime_checkable
class PlannerContract(Protocol):
    """Planner interface used by orchestration/runtime layers."""

    def create_plan(self, task: str, strategy: str) -> list[PlanStep]:
        ...


@runtime_checkable
class ExecutorContract(Protocol):
    """Execution interface used by agent/chat/run orchestration."""

    def execute(
        self,
        agent: Agent,
        user_id: str,
        session_id: str | None,
        user_message: str,
        checkpoint: CheckpointWriter | None = None,
        run_deadline_monotonic: float | None = None,
        resume_state: dict[str, Any] | None = None,
        run_budget: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...


@runtime_checkable
class MemoryContract(Protocol):
    """Memory facade expected by orchestration and API components."""

    def build_context(
        self,
        user_id: str,
        agent_id: str | None,
        query: str,
        session_id: str | None = None,
        working_limit: int = 12,
        episodic_limit: int = 16,
        semantic_top_k: int = 8,
    ) -> MemoryContext:
        ...

    def add_interaction(
        self,
        user_id: str,
        agent_id: str | None,
        role: str,
        content: str,
        session_id: str | None = None,
    ) -> None:
        ...

    def remember_fact(
        self,
        user_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        ...

    def get_context(
        self,
        user_id: str,
        agent_id: str | None,
        query: str,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        ...


@runtime_checkable
class ToolRouterContract(Protocol):
    """Tool routing/registry interface expected by orchestrators."""

    def get(self, name: str) -> ToolDefinition | None:
        ...

    def list(self) -> list[ToolDefinition]:
        ...

    def names(self) -> list[str]:
        ...

    def openai_schemas(self, selected: list[str] | None = None) -> list[dict[str, Any]]:
        ...
