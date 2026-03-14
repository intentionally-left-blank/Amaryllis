from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from tools.policy import ToolIsolationPolicy
from tools.tool_executor import PermissionRequiredError, ToolExecutor
from tools.tool_registry import ToolRegistry


class ToolEnforcementModeTests(unittest.TestCase):
    def test_prompt_and_allow_still_blocks_high_risk_without_permission(self) -> None:
        registry = ToolRegistry()
        registry.load_builtin_tools()
        executor = ToolExecutor(
            registry=registry,
            policy=ToolIsolationPolicy(profile="balanced"),
            approval_enforcement_mode="prompt_and_allow",
        )

        with self.assertRaises(PermissionRequiredError):
            executor.execute("python_exec", {"code": "print('secure')"})

        prompts = executor.list_permission_prompts(status="pending", limit=10)
        self.assertEqual(len(prompts), 1)
        self.assertEqual(str(prompts[0].get("tool_name")), "python_exec")

    def test_prompt_and_allow_keeps_advisory_mode_for_medium_risk_conditional(self) -> None:
        temp_dir = Path(tempfile.mkdtemp(prefix="amaryllis-tool-mode-", dir=Path.cwd()))
        try:
            target_file = temp_dir / "out.txt"
            registry = ToolRegistry()
            registry.load_builtin_tools()
            executor = ToolExecutor(
                registry=registry,
                policy=ToolIsolationPolicy(profile="balanced"),
                approval_enforcement_mode="prompt_and_allow",
            )

            result = executor.execute(
                "filesystem",
                {
                    "action": "write",
                    "path": str(target_file),
                    "content": "ok",
                },
            )

            self.assertEqual(result["tool"], "filesystem")
            self.assertTrue(target_file.exists())
            self.assertIn("permission_prompt", result)
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
