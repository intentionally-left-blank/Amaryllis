from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tools.policy import ToolIsolationPolicy
from tools.tool_executor import ToolExecutionError, ToolExecutor
from tools.tool_registry import ToolDefinition, ToolRegistry


class ToolIsolationPolicyTests(unittest.TestCase):
    def test_strict_profile_blocks_high_risk_tools_by_default(self) -> None:
        registry = ToolRegistry()
        registry.load_builtin_tools()
        policy = ToolIsolationPolicy(profile="strict")
        executor = ToolExecutor(registry=registry, policy=policy, approval_enforcement_mode="strict")

        with self.assertRaises(ToolExecutionError) as ctx:
            executor.execute("python_exec", {"code": "print('x')", "timeout": 2})

        self.assertIn("high-risk", str(ctx.exception).lower())

    def test_filesystem_write_can_be_disabled_by_policy(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-tools-policy-") as tmp:
            root = Path(tmp)
            registry = ToolRegistry()
            registry.load_builtin_tools()
            policy = ToolIsolationPolicy(
                profile="balanced",
                filesystem_allow_write=False,
            )
            executor = ToolExecutor(registry=registry, policy=policy)

            with self.assertRaises(ToolExecutionError) as ctx:
                executor.execute(
                    "filesystem",
                    {
                        "action": "write",
                        "path": str(root / "a.txt"),
                        "content": "hello",
                    },
                )

            self.assertIn("disabled", str(ctx.exception).lower())

    def test_plugin_network_capability_is_blocked_by_default(self) -> None:
        policy = ToolIsolationPolicy(profile="balanced")
        tool = ToolDefinition(
            name="plugin_network_tool",
            description="plugin network",
            input_schema={"type": "object"},
            handler=lambda _: {"ok": True},
            source="plugin:test",
            risk_level="medium",
            execution_target={
                "kind": "plugin",
                "capabilities": ["filesystem_read", "network"],
            },
        )

        decision = policy.evaluate(tool=tool, arguments={})
        self.assertFalse(decision.allow)
        self.assertIn("not allowed by policy", str(decision.reason or "").lower())

    def test_plugin_filesystem_write_requires_approval_when_allowed(self) -> None:
        policy = ToolIsolationPolicy(
            profile="balanced",
            allowed_plugin_capabilities=["filesystem_read", "filesystem_write"],
            filesystem_allow_write=True,
        )
        tool = ToolDefinition(
            name="plugin_writer",
            description="plugin write",
            input_schema={"type": "object"},
            handler=lambda _: {"ok": True},
            source="plugin:test",
            risk_level="low",
            execution_target={
                "kind": "plugin",
                "capabilities": ["filesystem_read", "filesystem_write"],
            },
        )

        decision = policy.evaluate(tool=tool, arguments={})
        self.assertTrue(decision.allow)
        self.assertTrue(decision.requires_approval)

    def test_python_exec_blocks_unsafe_deserialization_snippet(self) -> None:
        registry = ToolRegistry()
        registry.load_builtin_tools()
        policy = ToolIsolationPolicy(profile="balanced")
        executor = ToolExecutor(registry=registry, policy=policy, approval_enforcement_mode="strict")

        with self.assertRaises(ToolExecutionError) as ctx:
            executor.execute(
                "python_exec",
                {
                    "code": "import pickle\npayload = b'\\x80\\x04.'\npickle.loads(payload)\nprint('x')",
                    "timeout": 2,
                },
            )

        self.assertIn("unsafe deserialization", str(ctx.exception).lower())

    def test_filesystem_write_with_safe_yaml_content_is_not_blocked_by_denylist(self) -> None:
        registry = ToolRegistry()
        registry.load_builtin_tools()
        tool = registry.get("filesystem")
        assert tool is not None

        policy = ToolIsolationPolicy(profile="balanced")
        decision = policy.evaluate(
            tool=tool,
            arguments={
                "action": "write",
                "path": "safe.yaml",
                "content": "config:\n  loader: yaml.safe_load\n",
            },
        )
        self.assertTrue(decision.allow)
        self.assertTrue(decision.requires_approval)

    def test_python_exec_blocks_cloudpickle_deserialization_snippet(self) -> None:
        registry = ToolRegistry()
        registry.load_builtin_tools()
        tool = registry.get("python_exec")
        assert tool is not None

        policy = ToolIsolationPolicy(profile="balanced")
        decision = policy.evaluate(
            tool=tool,
            arguments={
                "code": "import cloudpickle\ncloudpickle.loads(payload)\n",
                "timeout": 2,
            },
        )
        self.assertFalse(decision.allow)
        self.assertIn("cloudpickle_load", str(decision.reason or ""))

    def test_python_exec_blocks_pandas_read_pickle_snippet(self) -> None:
        registry = ToolRegistry()
        registry.load_builtin_tools()
        tool = registry.get("python_exec")
        assert tool is not None

        policy = ToolIsolationPolicy(profile="balanced")
        decision = policy.evaluate(
            tool=tool,
            arguments={
                "code": "import pandas as pd\npd.read_pickle('payload.pkl')\n",
                "timeout": 2,
            },
        )
        self.assertFalse(decision.allow)
        self.assertIn("pd_read_pickle", str(decision.reason or ""))


if __name__ == "__main__":
    unittest.main()
