from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from runtime.autonomy_circuit_breaker import AutonomyCircuitBreaker
from tools.autonomy_policy import AutonomyPolicy, normalize_autonomy_level
from tools.autonomy_policy_pack import default_policy_pack_path, load_autonomy_policy_pack
from tools.policy import ToolIsolationPolicy
from tools.tool_executor import PermissionRequiredError, ToolExecutionError, ToolExecutor
from tools.tool_registry import ToolRegistry


def _build_registry_with_medium_and_high_tools() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        name="medium_echo",
        description="Medium-risk synthetic tool",
        input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
        handler=lambda arguments: {"echo": str(arguments.get("text", ""))},
        risk_level="medium",
        approval_mode="none",
        source="test",
    )
    registry.register(
        name="high_echo",
        description="High-risk synthetic tool",
        input_schema={"type": "object", "properties": {"text": {"type": "string"}}},
        handler=lambda arguments: {"echo": str(arguments.get("text", ""))},
        risk_level="high",
        approval_mode="none",
        source="test",
    )
    return registry


class ToolAutonomyTests(unittest.TestCase):
    def test_invalid_autonomy_level_normalizes_to_l3(self) -> None:
        self.assertEqual(normalize_autonomy_level("invalid"), "l3")
        self.assertEqual(normalize_autonomy_level("L4"), "l4")

    def test_l0_blocks_low_risk_tool_execution(self) -> None:
        registry = ToolRegistry()
        registry.load_builtin_tools()
        executor = ToolExecutor(
            registry=registry,
            policy=ToolIsolationPolicy(profile="balanced"),
            autonomy_policy=AutonomyPolicy(level="l0"),
            approval_enforcement_mode="strict",
        )

        with self.assertRaises(ToolExecutionError) as ctx:
            executor.execute("web_search", {"query": "local test"})

        self.assertIn("autonomy level l0", str(ctx.exception).lower())

    def test_l2_blocks_high_risk_even_when_policy_would_allow_with_approval(self) -> None:
        registry = _build_registry_with_medium_and_high_tools()
        executor = ToolExecutor(
            registry=registry,
            policy=ToolIsolationPolicy(profile="balanced"),
            autonomy_policy=AutonomyPolicy(level="l2"),
            approval_enforcement_mode="prompt_and_allow",
        )

        with self.assertRaises(ToolExecutionError) as ctx:
            executor.execute("high_echo", {"text": "x"})

        self.assertIn("autonomy level l2 blocks", str(ctx.exception).lower())

    def test_l2_requires_approval_for_medium_risk_tool(self) -> None:
        registry = _build_registry_with_medium_and_high_tools()
        executor = ToolExecutor(
            registry=registry,
            policy=ToolIsolationPolicy(profile="balanced"),
            autonomy_policy=AutonomyPolicy(level="l2"),
            approval_enforcement_mode="prompt_and_allow",
        )

        result = executor.execute("medium_echo", {"text": "x"})
        self.assertEqual(result["tool"], "medium_echo")
        self.assertIn("permission_prompt", result)

    def test_debug_guardrails_exposes_autonomy_policy(self) -> None:
        registry = _build_registry_with_medium_and_high_tools()
        executor = ToolExecutor(
            registry=registry,
            policy=ToolIsolationPolicy(profile="balanced"),
            autonomy_policy=AutonomyPolicy(level="l4"),
            approval_enforcement_mode="strict",
        )

        snapshot = executor.debug_guardrails()
        autonomy = snapshot.get("autonomy_policy")
        self.assertIsInstance(autonomy, dict)
        assert isinstance(autonomy, dict)
        self.assertEqual(autonomy.get("level"), "l4")
        action_boundary = snapshot.get("action_boundary_policy")
        self.assertIsInstance(action_boundary, dict)
        assert isinstance(action_boundary, dict)
        self.assertIn("user_initiated", list(action_boundary.get("supported_action_classes") or []))
        self.assertIn("autonomous_agent", list(action_boundary.get("autonomous_action_classes") or []))

    def test_custom_policy_pack_can_override_l2_high_risk_behavior(self) -> None:
        base_pack = load_autonomy_policy_pack(default_policy_pack_path())
        payload = {
            "schema_version": base_pack.schema_version,
            "pack": "test_override_l2_high",
            "description": "Test pack overriding l2/high behavior.",
            "rules": {},
        }
        for level, rules in base_pack.levels.items():
            payload["rules"][level] = {}
            for risk, rule in rules.items():
                payload["rules"][level][risk] = {
                    "allow": bool(rule.allow),
                    "requires_approval": bool(rule.requires_approval),
                    "reason": rule.reason,
                    "approval_scope": rule.approval_scope,
                    "approval_ttl_sec": rule.approval_ttl_sec,
                }
        payload["rules"]["l2"]["high"] = {
            "allow": True,
            "requires_approval": True,
            "reason": "L2 override: high-risk tool requires approval.",
            "approval_scope": "request",
            "approval_ttl_sec": 60,
        }

        with tempfile.TemporaryDirectory(prefix="amaryllis-tool-autonomy-tests-") as tmp:
            pack_path = Path(tmp) / "override-policy-pack.json"
            pack_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            registry = _build_registry_with_medium_and_high_tools()
            executor = ToolExecutor(
                registry=registry,
                policy=ToolIsolationPolicy(profile="balanced"),
                autonomy_policy=AutonomyPolicy(level="l2", policy_pack_path=pack_path),
                approval_enforcement_mode="prompt_and_allow",
            )

            with self.assertRaises(PermissionRequiredError) as ctx:
                executor.execute("high_echo", {"text": "override"})

            self.assertIn("permission required for tool 'high_echo'", str(ctx.exception).lower())
            pending = executor.list_permission_prompts(status="pending", limit=10)
            self.assertTrue(any(str(item.get("tool_name")) == "high_echo" for item in pending))

    def test_high_risk_autonomous_action_is_blocked_when_breaker_scope_matches(self) -> None:
        registry = _build_registry_with_medium_and_high_tools()
        breaker = AutonomyCircuitBreaker()
        breaker.arm(actor="svc-runtime", reason="test", scope_type="global")
        executor = ToolExecutor(
            registry=registry,
            policy=ToolIsolationPolicy(profile="balanced"),
            autonomy_policy=AutonomyPolicy(level="l5"),
            autonomy_circuit_breaker=breaker,
            approval_enforcement_mode="prompt_and_allow",
        )

        with self.assertRaises(ToolExecutionError) as ctx:
            executor.execute(
                "high_echo",
                {"text": "blocked"},
                user_id="user-1",
                agent_id="agent-1",
                action_class="autonomous_agent",
            )

        self.assertIn("autonomy circuit breaker", str(ctx.exception).lower())
        self.assertIn("blocked", str(ctx.exception).lower())

    def test_high_risk_autonomous_action_respects_agent_scope_parity(self) -> None:
        registry = _build_registry_with_medium_and_high_tools()
        breaker = AutonomyCircuitBreaker()
        breaker.arm(
            actor="svc-runtime",
            reason="agent-scope",
            scope_type="agent",
            scope_agent_id="agent-1",
        )
        executor = ToolExecutor(
            registry=registry,
            policy=ToolIsolationPolicy(profile="balanced"),
            autonomy_policy=AutonomyPolicy(level="l5"),
            autonomy_circuit_breaker=breaker,
            approval_enforcement_mode="prompt_and_allow",
        )
        session_id = "tool-autonomy-agent-scope"
        allowed_arguments = {"text": "allowed"}

        with self.assertRaises(ToolExecutionError):
            executor.execute(
                "high_echo",
                {"text": "blocked"},
                user_id="user-1",
                agent_id="agent-1",
                session_id=session_id,
                action_class="autonomous_agent",
            )

        with self.assertRaises(PermissionRequiredError) as permission_ctx:
            executor.execute(
                "high_echo",
                allowed_arguments,
                user_id="user-1",
                session_id=session_id,
                action_class="user_initiated",
            )
        prompt_id = permission_ctx.exception.prompt_id
        executor.approve_permission_prompt(prompt_id)

        allowed = executor.execute(
            "high_echo",
            allowed_arguments,
            user_id="user-1",
            agent_id="agent-2",
            session_id=session_id,
            permission_id=prompt_id,
            action_class="autonomous_agent",
        )
        self.assertEqual(str(allowed.get("tool")), "high_echo")
        self.assertEqual(str((allowed.get("result") or {}).get("echo")), "allowed")

    def test_high_risk_user_initiated_action_is_not_blocked_by_breaker(self) -> None:
        registry = _build_registry_with_medium_and_high_tools()
        breaker = AutonomyCircuitBreaker()
        breaker.arm(actor="svc-runtime", reason="manual-override", scope_type="global")
        executor = ToolExecutor(
            registry=registry,
            policy=ToolIsolationPolicy(profile="balanced"),
            autonomy_policy=AutonomyPolicy(level="l5"),
            autonomy_circuit_breaker=breaker,
            approval_enforcement_mode="prompt_and_allow",
        )
        session_id = "tool-autonomy-user-initiated"
        manual_arguments = {"text": "manual-ok"}

        with self.assertRaises(PermissionRequiredError) as permission_ctx:
            executor.execute(
                "high_echo",
                manual_arguments,
                user_id="user-1",
                session_id=session_id,
                action_class="user_initiated",
            )
        prompt_id = permission_ctx.exception.prompt_id
        executor.approve_permission_prompt(prompt_id)

        result = executor.execute(
            "high_echo",
            manual_arguments,
            user_id="user-1",
            session_id=session_id,
            permission_id=prompt_id,
            action_class="user_initiated",
        )
        self.assertEqual(str(result.get("tool")), "high_echo")
        self.assertEqual(str((result.get("result") or {}).get("echo")), "manual-ok")


if __name__ == "__main__":
    unittest.main()
