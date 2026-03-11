from __future__ import annotations

import unittest
from unittest.mock import patch

from tools.mcp_client_registry import MCPClientRegistry
from tools.tool_executor import PermissionRequiredError, ToolExecutor
from tools.tool_registry import ToolRegistry


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class ToolsMCPTests(unittest.TestCase):
    def test_strict_permission_flow_for_python_exec(self) -> None:
        registry = ToolRegistry()
        registry.load_builtin_tools()
        executor = ToolExecutor(
            registry,
            approval_enforcement_mode="strict",
        )

        with self.assertRaises(PermissionRequiredError) as ctx:
            executor.execute("python_exec", {"code": "print('ok')"})

        prompt_id = ctx.exception.prompt_id
        prompts = executor.list_permission_prompts(status="pending")
        self.assertTrue(any(item["id"] == prompt_id for item in prompts))

        approved = executor.approve_permission_prompt(prompt_id)
        self.assertEqual(approved["status"], "approved")

        result = executor.execute(
            "python_exec",
            {"code": "print('ok')"},
            permission_id=prompt_id,
        )
        self.assertEqual(result["tool"], "python_exec")
        self.assertEqual(result["result"]["returncode"], 0)
        self.assertIn("ok", result["result"]["stdout"])

        # Approval is one-time and should require a new prompt afterward.
        with self.assertRaises(PermissionRequiredError):
            executor.execute(
                "python_exec",
                {"code": "print('ok')"},
                permission_id=prompt_id,
            )

    def test_mcp_client_registry_registers_remote_tool_proxy(self) -> None:
        registry = ToolRegistry()
        mcp = MCPClientRegistry(["http://mcp.local"])

        with patch("tools.mcp_client_registry.httpx.get") as mock_get, patch(
            "tools.mcp_client_registry.httpx.post"
        ) as mock_post:
            mock_get.return_value = _FakeResponse(
                {
                    "items": [
                        {
                            "name": "echo",
                            "description": "Echo from remote MCP",
                            "input_schema": {
                                "type": "object",
                                "properties": {"text": {"type": "string"}},
                                "required": ["text"],
                            },
                            "risk_level": "low",
                            "approval_mode": "none",
                        }
                    ]
                }
            )
            mock_post.return_value = _FakeResponse(
                {
                    "result": {
                        "tool": "echo",
                        "result": {"text": "hello"},
                    }
                }
            )

            discovered = mcp.register_remote_tools(registry)
            self.assertEqual(discovered, 1)

            names = registry.names()
            self.assertIn("mcp_mcp_local_echo", names)

            executor = ToolExecutor(registry)
            call = executor.execute("mcp_mcp_local_echo", {"text": "hello"})
            self.assertEqual(call["tool"], "mcp_mcp_local_echo")
            self.assertEqual(call["result"]["result"]["text"], "hello")

    def test_permission_ids_batch_can_unlock_execution(self) -> None:
        registry = ToolRegistry()
        registry.load_builtin_tools()
        executor = ToolExecutor(
            registry,
            approval_enforcement_mode="strict",
        )

        with self.assertRaises(PermissionRequiredError) as ctx:
            executor.execute("python_exec", {"code": "print('batch')"})
        prompt_id = ctx.exception.prompt_id
        executor.approve_permission_prompt(prompt_id)

        result = executor.execute(
            "python_exec",
            {"code": "print('batch')"},
            permission_ids=[prompt_id],
        )
        self.assertEqual(result["tool"], "python_exec")
        self.assertEqual(result["result"]["returncode"], 0)
        self.assertIn("batch", result["result"]["stdout"])


if __name__ == "__main__":
    unittest.main()
