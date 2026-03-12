from __future__ import annotations

import unittest

from tools.tool_budget import ToolBudgetGuard
from tools.tool_executor import ToolBudgetLimitError, ToolExecutor
from tools.tool_registry import ToolRegistry


def _echo_handler(arguments: dict[str, object]) -> dict[str, object]:
    return {"ok": True, "arguments": arguments}


class ToolBudgetGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ToolRegistry()
        schema = {"type": "object", "properties": {}}
        self.registry.register(
            name="low_a",
            description="Low risk A",
            input_schema=schema,
            handler=_echo_handler,
            risk_level="low",
        )
        self.registry.register(
            name="low_b",
            description="Low risk B",
            input_schema=schema,
            handler=_echo_handler,
            risk_level="low",
        )
        self.registry.register(
            name="high_a",
            description="High risk A",
            input_schema=schema,
            handler=_echo_handler,
            risk_level="high",
            approval_mode="none",
        )

    def test_per_tool_limit_blocks_excess_calls(self) -> None:
        guard = ToolBudgetGuard(
            window_sec=60,
            max_calls_per_tool=2,
            max_total_calls=20,
            max_high_risk_calls=10,
        )
        executor = ToolExecutor(registry=self.registry, budget_guard=guard)
        executor.execute("low_a", {})
        executor.execute("low_a", {})

        with self.assertRaises(ToolBudgetLimitError) as ctx:
            executor.execute("low_a", {})

        self.assertIn("per_tool_calls", str(ctx.exception))

    def test_total_limit_blocks_cross_tool_spam(self) -> None:
        guard = ToolBudgetGuard(
            window_sec=60,
            max_calls_per_tool=20,
            max_total_calls=2,
            max_high_risk_calls=10,
        )
        executor = ToolExecutor(registry=self.registry, budget_guard=guard)
        executor.execute("low_a", {})
        executor.execute("low_b", {})

        with self.assertRaises(ToolBudgetLimitError) as ctx:
            executor.execute("low_a", {})

        self.assertIn("total_calls", str(ctx.exception))

    def test_high_risk_limit_applies_for_high_risk_tools(self) -> None:
        guard = ToolBudgetGuard(
            window_sec=60,
            max_calls_per_tool=20,
            max_total_calls=20,
            max_high_risk_calls=1,
        )
        executor = ToolExecutor(registry=self.registry, budget_guard=guard)
        executor.execute("high_a", {})

        with self.assertRaises(ToolBudgetLimitError) as ctx:
            executor.execute("high_a", {})

        self.assertIn("high-risk", str(ctx.exception).lower())

    def test_session_scope_isolated_between_sessions(self) -> None:
        guard = ToolBudgetGuard(
            window_sec=60,
            max_calls_per_tool=1,
            max_total_calls=20,
            max_high_risk_calls=20,
        )
        executor = ToolExecutor(registry=self.registry, budget_guard=guard)

        executor.execute("low_a", {}, session_id="s1")
        executor.execute("low_a", {}, session_id="s2")

        with self.assertRaises(ToolBudgetLimitError):
            executor.execute("low_a", {}, session_id="s1")

    def test_telemetry_emits_budget_blocked_event(self) -> None:
        guard = ToolBudgetGuard(
            window_sec=60,
            max_calls_per_tool=1,
            max_total_calls=20,
            max_high_risk_calls=20,
        )
        events: list[tuple[str, dict[str, object]]] = []

        def emit(event_type: str, payload: dict[str, object]) -> None:
            events.append((event_type, payload))

        executor = ToolExecutor(
            registry=self.registry,
            budget_guard=guard,
            telemetry_emitter=emit,
        )
        executor.execute("low_a", {}, session_id="s1")
        with self.assertRaises(ToolBudgetLimitError):
            executor.execute("low_a", {}, session_id="s1")

        event_types = [item[0] for item in events]
        self.assertIn("tool_budget_recorded", event_types)
        self.assertIn("tool_budget_blocked", event_types)

    def test_debug_snapshot_returns_scope_usage(self) -> None:
        guard = ToolBudgetGuard(
            window_sec=60,
            max_calls_per_tool=5,
            max_total_calls=20,
            max_high_risk_calls=20,
        )
        executor = ToolExecutor(registry=self.registry, budget_guard=guard)
        executor.execute("low_a", {}, session_id="s-debug")
        executor.execute("low_b", {}, session_id="s-debug")

        snapshot = executor.debug_guardrails(
            session_id="s-debug",
            scopes_limit=10,
            top_tools_limit=3,
        )
        budget = snapshot["budget"]
        selected = budget["selected_scope"]
        self.assertEqual(selected["scope"], "session:s-debug")
        self.assertEqual(selected["total_calls"], 2)
        self.assertEqual(selected["tools_count"], 2)
        self.assertTrue(len(selected["top_tools"]) >= 2)


if __name__ == "__main__":
    unittest.main()
