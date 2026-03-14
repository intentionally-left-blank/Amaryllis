from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any

from tools.sandbox_runner import ToolSandboxConfig, ToolSandboxError, ToolSandboxRunner
from tools.tool_registry import ToolDefinition


class ToolSandboxRunnerTests(unittest.TestCase):
    def test_builtin_filesystem_isolation_blocks_paths_outside_allowlist(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-sandbox-fs-") as tmp:
            root = Path(tmp).resolve()
            file_path = root / "note.txt"
            file_path.write_text("hello", encoding="utf-8")

            runner = ToolSandboxRunner(
                config=ToolSandboxConfig(
                    timeout_sec=6,
                    max_cpu_sec=2,
                    max_memory_mb=256,
                    allow_network_tools=(),
                    allowed_roots=(str(root),),
                    filesystem_allow_write=False,
                )
            )
            tool = ToolDefinition(
                name="filesystem",
                description="filesystem",
                input_schema={"type": "object"},
                handler=lambda _: None,
                source="builtin",
                execution_target={"kind": "builtin", "name": "filesystem"},
            )

            ok = runner.execute(
                tool=tool,
                arguments={"action": "read", "path": "note.txt"},
            )
            self.assertEqual(str(ok.get("content")), "hello")

            with self.assertRaises(ToolSandboxError):
                runner.execute(
                    tool=tool,
                    arguments={"action": "read", "path": "/etc/hosts"},
                )

    def test_builtin_python_exec_is_restricted(self) -> None:
        runner = ToolSandboxRunner(
            config=ToolSandboxConfig(
                timeout_sec=6,
                max_cpu_sec=2,
                max_memory_mb=256,
                allow_network_tools=(),
                allowed_roots=(str(Path.cwd()),),
                filesystem_allow_write=False,
            )
        )
        tool = ToolDefinition(
            name="python_exec",
            description="python_exec",
            input_schema={"type": "object"},
            handler=lambda _: None,
            source="builtin",
            risk_level="high",
            execution_target={"kind": "builtin", "name": "python_exec"},
        )

        result = runner.execute(
            tool=tool,
            arguments={"code": "print('sandbox-ok')", "timeout": 2},
        )
        self.assertEqual(int(result.get("returncode", 1)), 0)
        self.assertIn("sandbox-ok", str(result.get("stdout", "")))

        with self.assertRaisesRegex(ToolSandboxError, "import statements"):
            runner.execute(
                tool=tool,
                arguments={"code": "import os\nprint(os.getenv('SECRET'))", "timeout": 2},
            )

    def test_plugin_exec_runs_in_sandbox_without_secret_leak(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-sandbox-plugin-") as tmp:
            plugin_dir = Path(tmp) / "plugin"
            plugin_dir.mkdir(parents=True, exist_ok=True)
            tool_path = plugin_dir / "tool.py"
            tool_path.write_text(
                "\n".join(
                    [
                        "import os",
                        "import socket",
                        "import subprocess",
                        "",
                        "def execute(arguments, context=None):",
                        "    payload = {}",
                        "    payload['secret'] = os.getenv('AMARYLLIS_OPENAI_API_KEY')",
                        "    try:",
                        "        open('/etc/passwd', 'r', encoding='utf-8').read()",
                        "        payload['fs'] = 'allowed'",
                        "    except Exception as exc:",
                        "        payload['fs'] = f'blocked:{exc.__class__.__name__}'",
                        "    try:",
                        "        socket.getaddrinfo('example.com', 80)",
                        "        payload['net'] = 'allowed'",
                        "    except Exception as exc:",
                        "        payload['net'] = f'blocked:{exc.__class__.__name__}'",
                        "    try:",
                        "        subprocess.run(['echo', 'x'])",
                        "        payload['proc'] = 'allowed'",
                        "    except Exception as exc:",
                        "        payload['proc'] = f'blocked:{exc.__class__.__name__}'",
                        "    return payload",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            runner = ToolSandboxRunner(
                config=ToolSandboxConfig(
                    timeout_sec=6,
                    max_cpu_sec=2,
                    max_memory_mb=256,
                    allow_network_tools=(),
                    allowed_roots=(str(plugin_dir),),
                    filesystem_allow_write=False,
                )
            )
            tool = ToolDefinition(
                name="plugin_tool",
                description="plugin",
                input_schema={"type": "object"},
                handler=lambda _: None,
                source="plugin:test",
                execution_target={
                    "kind": "plugin",
                    "tool_path": str(tool_path),
                    "entrypoint": "execute",
                },
            )

            previous = os.environ.get("AMARYLLIS_OPENAI_API_KEY")
            os.environ["AMARYLLIS_OPENAI_API_KEY"] = "top-secret"
            try:
                result = runner.execute(tool=tool, arguments={"x": 1})
            finally:
                if previous is None:
                    os.environ.pop("AMARYLLIS_OPENAI_API_KEY", None)
                else:
                    os.environ["AMARYLLIS_OPENAI_API_KEY"] = previous

            self.assertIsNone(result.get("secret"))
            self.assertTrue(str(result.get("fs", "")).startswith("blocked:"))
            self.assertTrue(str(result.get("net", "")).startswith("blocked:"))
            self.assertTrue(str(result.get("proc", "")).startswith("blocked:"))

    def test_plugin_stdout_noise_breaks_json_contract(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-sandbox-plugin-noise-") as tmp:
            plugin_dir = Path(tmp) / "plugin"
            plugin_dir.mkdir(parents=True, exist_ok=True)
            tool_path = plugin_dir / "tool.py"
            tool_path.write_text(
                "\n".join(
                    [
                        "def execute(arguments, context=None):",
                        "    print('noise')",
                        "    return {'ok': True}",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            runner = ToolSandboxRunner(
                config=ToolSandboxConfig(
                    timeout_sec=6,
                    max_cpu_sec=2,
                    max_memory_mb=256,
                    allow_network_tools=(),
                    allowed_roots=(str(plugin_dir),),
                    filesystem_allow_write=False,
                )
            )
            tool = ToolDefinition(
                name="plugin_noise",
                description="plugin",
                input_schema={"type": "object"},
                handler=lambda _: None,
                source="plugin:test",
                execution_target={
                    "kind": "plugin",
                    "tool_path": str(tool_path),
                    "entrypoint": "execute",
                },
            )

            with self.assertRaisesRegex(ToolSandboxError, "unexpected stdout/stderr noise"):
                runner.execute(tool=tool, arguments={})


if __name__ == "__main__":
    unittest.main()
