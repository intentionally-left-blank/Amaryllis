from __future__ import annotations

from typing import Any, Callable, Iterator, Protocol, runtime_checkable

# Versioned cognitive-kernel contract surface.
KERNEL_CONTRACTS_VERSION = "kernel.contracts.v1"

CheckpointWriter = Callable[[dict[str, Any]], None]


@runtime_checkable
class PlannerContract(Protocol):
    """Planner interface used by orchestration/runtime layers."""

    def create_plan(self, task: str, strategy: str) -> list[dict[str, Any]]:
        ...


@runtime_checkable
class ExecutorContract(Protocol):
    """Execution interface used by agent/chat/run orchestration."""

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
    ) -> dict[str, Any]:
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

    def get(self, name: str) -> Any:
        ...

    def list(self) -> list[Any]:
        ...

    def names(self) -> list[str]:
        ...

    def openai_schemas(self, selected: list[str] | None = None) -> list[dict[str, Any]]:
        ...


@runtime_checkable
class CognitionBackendContract(Protocol):
    """Backend cognition facade used by chat/runtime/model API layers."""

    active_provider: str
    active_model: str

    def list_models(
        self,
        *,
        include_suggested: bool = True,
        include_remote_providers: bool = True,
        max_items_per_provider: int | None = None,
    ) -> dict[str, Any]:
        ...

    def provider_capabilities(self) -> dict[str, Any]:
        ...

    def provider_health(self) -> dict[str, Any]:
        ...

    def model_capability_matrix(
        self,
        *,
        include_suggested: bool = True,
        limit_per_provider: int = 120,
    ) -> dict[str, Any]:
        ...

    def recommend_onboarding_profile(self) -> dict[str, Any]:
        ...

    def onboarding_activation_plan(
        self,
        *,
        profile: str | None = None,
        include_remote_providers: bool = True,
        limit: int = 120,
        require_metadata: bool | None = None,
    ) -> dict[str, Any]:
        ...

    def onboarding_activate(
        self,
        *,
        profile: str | None = None,
        include_remote_providers: bool = True,
        limit: int = 120,
        require_metadata: bool | None = None,
        activate: bool = True,
        run_smoke_test: bool = True,
        smoke_prompt: str | None = None,
    ) -> dict[str, Any]:
        ...

    def model_package_catalog(
        self,
        *,
        profile: str | None = None,
        include_remote_providers: bool = True,
        limit: int = 120,
    ) -> dict[str, Any]:
        ...

    def model_package_license_admission(
        self,
        *,
        package_id: str,
        require_metadata: bool | None = None,
    ) -> dict[str, Any]:
        ...

    def install_model_package(
        self,
        *,
        package_id: str,
        activate: bool = True,
    ) -> dict[str, Any]:
        ...

    def choose_route(
        self,
        *,
        mode: str = "balanced",
        provider: str | None = None,
        model: str | None = None,
        require_stream: bool = True,
        require_tools: bool = False,
        prefer_local: bool | None = None,
        min_params_b: float | None = None,
        max_params_b: float | None = None,
        include_suggested: bool = False,
        limit_per_provider: int = 120,
    ) -> dict[str, Any]:
        ...

    def debug_failover_state(
        self,
        *,
        session_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        ...

    def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        provider: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 512,
        routing: dict[str, Any] | None = None,
        fallback_targets: list[tuple[str, str]] | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        ...

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        provider: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 512,
        routing: dict[str, Any] | None = None,
        fallback_targets: list[tuple[str, str]] | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
    ) -> tuple[Iterator[str], str, str, dict[str, Any] | None]:
        ...

    def download_model(self, model_id: str, provider: str | None = None) -> dict[str, Any]:
        ...

    def start_model_download(self, model_id: str, provider: str | None = None) -> dict[str, Any]:
        ...

    def get_model_download_job(self, job_id: str) -> dict[str, Any]:
        ...

    def list_model_download_jobs(self, limit: int = 100) -> dict[str, Any]:
        ...

    def load_model(self, model_id: str, provider: str | None = None) -> dict[str, Any]:
        ...
