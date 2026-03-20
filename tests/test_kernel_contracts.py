import unittest

from kernel.contracts import (
    KERNEL_CONTRACTS_VERSION,
    ExecutorContract,
    MemoryContract,
    PlannerContract,
    ToolRouterContract,
)
from memory.memory_manager import MemoryManager
from planner.planner import Planner
from tasks.task_executor import TaskExecutor
from tools.tool_registry import ToolRegistry


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


if __name__ == "__main__":
    unittest.main()
