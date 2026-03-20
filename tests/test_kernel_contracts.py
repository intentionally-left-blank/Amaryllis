import unittest

from kernel.contracts import (
    CognitionBackendContract,
    KERNEL_CONTRACTS_VERSION,
    ExecutorContract,
    MemoryContract,
    PlannerContract,
    ToolRouterContract,
)
from memory.memory_manager import MemoryManager
from models.cognition_backends import DeterministicCognitionBackend, ModelManagerCognitionBackend
from planner.planner import Planner
from tasks.task_executor import TaskExecutor
from tools.tool_registry import ToolRegistry


class _FakeCognitionManager:
    def __init__(self) -> None:
        self.active_provider = "fake"
        self.active_model = "fake-model"
        self.providers: dict[str, object] = {}

    def list_models(self, **_: object) -> dict[str, object]:
        return {"active": {"provider": self.active_provider, "model": self.active_model}}

    @staticmethod
    def provider_capabilities() -> dict[str, object]:
        return {"fake": {"local": True}}

    @staticmethod
    def provider_health() -> dict[str, object]:
        return {"fake": {"status": "ok"}}

    def model_capability_matrix(self, **_: object) -> dict[str, object]:
        return {"active": {"provider": self.active_provider, "model": self.active_model}, "items": []}

    def choose_route(self, **_: object) -> dict[str, object]:
        return {"selected": {"provider": self.active_provider, "model": self.active_model}, "fallbacks": []}

    @staticmethod
    def debug_failover_state(**_: object) -> dict[str, object]:
        return {"recent_failovers": []}

    def chat(self, messages: list[dict[str, object]], **_: object) -> dict[str, object]:
        _ = messages
        return {
            "content": "ok",
            "provider": self.active_provider,
            "model": self.active_model,
        }

    def stream_chat(
        self,
        messages: list[dict[str, object]],
        **_: object,
    ) -> tuple[object, str, str, dict[str, object]]:
        _ = messages
        return iter(["ok"]), self.active_provider, self.active_model, {"selected": {"provider": self.active_provider}}

    def download_model(self, model_id: str, provider: str | None = None) -> dict[str, object]:
        return {"status": "downloaded", "provider": provider or self.active_provider, "model": model_id}

    def start_model_download(self, model_id: str, provider: str | None = None) -> dict[str, object]:
        return {
            "already_running": False,
            "job": {
                "id": "job-1",
                "provider": provider or self.active_provider,
                "model": model_id,
                "status": "succeeded",
                "progress": 1.0,
                "created_at": "now",
                "updated_at": "now",
                "finished_at": "now",
            },
        }

    @staticmethod
    def get_model_download_job(job_id: str) -> dict[str, object]:
        return {"id": job_id, "status": "succeeded"}

    @staticmethod
    def list_model_download_jobs(limit: int = 100) -> dict[str, object]:
        _ = limit
        return {"items": [], "count": 0}

    def load_model(self, model_id: str, provider: str | None = None) -> dict[str, object]:
        self.active_provider = provider or self.active_provider
        self.active_model = model_id
        return {"status": "loaded", "provider": self.active_provider, "model": self.active_model}


class KernelContractsTests(unittest.TestCase):
    def test_contract_version_is_v1(self) -> None:
        self.assertEqual(KERNEL_CONTRACTS_VERSION, "kernel.contracts.v1")

    def test_planner_implements_planner_contract(self) -> None:
        planner = Planner()
        self.assertIsInstance(planner, PlannerContract)

    def test_tool_registry_implements_tool_router_contract(self) -> None:
        registry = ToolRegistry()
        self.assertIsInstance(registry, ToolRouterContract)

    def test_memory_manager_exposes_memory_contract_surface(self) -> None:
        manager = MemoryManager.__new__(MemoryManager)
        self.assertIsInstance(manager, MemoryContract)

    def test_task_executor_exposes_executor_contract_surface(self) -> None:
        executor = TaskExecutor.__new__(TaskExecutor)
        self.assertIsInstance(executor, ExecutorContract)

    def test_cognition_backends_satisfy_contract(self) -> None:
        adapter = ModelManagerCognitionBackend(_FakeCognitionManager())  # type: ignore[arg-type]
        deterministic = DeterministicCognitionBackend()
        for backend in (adapter, deterministic):
            self.assertIsInstance(backend, CognitionBackendContract)
            route = backend.choose_route(mode="balanced")
            self.assertIn("selected", route)
            result = backend.chat(messages=[{"role": "user", "content": "hello"}])
            self.assertIn("content", result)
            stream, provider, model, _routing = backend.stream_chat(
                messages=[{"role": "user", "content": "hello"}]
            )
            first_chunk = next(stream)
            self.assertIsInstance(first_chunk, str)
            self.assertTrue(str(provider).strip())
            self.assertTrue(str(model).strip())


if __name__ == "__main__":
    unittest.main()
