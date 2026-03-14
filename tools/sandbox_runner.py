from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.tool_registry import ToolDefinition


class ToolSandboxError(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolSandboxConfig:
    timeout_sec: int = 12
    max_cpu_sec: int = 6
    max_memory_mb: int = 512
    allow_network_tools: tuple[str, ...] = ("web_search",)
    allowed_roots: tuple[str, ...] = (str(Path.cwd()),)
    filesystem_allow_write: bool = True
    max_python_code_chars: int = 4000


class ToolSandboxRunner:
    def __init__(
        self,
        config: ToolSandboxConfig | None = None,
        worker_module: str = "tools.sandbox_worker",
    ) -> None:
        self.config = config or ToolSandboxConfig()
        self.worker_module = worker_module
        self.logger = logging.getLogger("amaryllis.tools.sandbox")

    def execute(
        self,
        *,
        tool: ToolDefinition,
        arguments: dict[str, Any],
        request_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> Any:
        target = tool.execution_target
        if not isinstance(target, dict):
            raise ToolSandboxError(f"Tool '{tool.name}' has no sandbox execution target")

        payload = {
            "target": target,
            "arguments": arguments,
            "context": {
                "tool_name": tool.name,
                "request_id": request_id,
                "user_id": user_id,
                "session_id": session_id,
            },
            "limits": self._limits_for_tool(tool),
        }
        command = [sys.executable, "-m", self.worker_module]
        try:
            completed = subprocess.run(
                command,
                input=json.dumps(payload, ensure_ascii=False),
                capture_output=True,
                text=True,
                timeout=max(1, int(self.config.timeout_sec)),
                env=self._sandbox_env(),
            )
        except subprocess.TimeoutExpired as exc:
            raise ToolSandboxError(
                f"Sandbox timeout for tool '{tool.name}' ({self.config.timeout_sec}s)"
            ) from exc
        except Exception as exc:
            raise ToolSandboxError(f"Sandbox spawn failed for tool '{tool.name}': {exc}") from exc

        stderr = (completed.stderr or "").strip()
        if stderr:
            self.logger.warning(
                "tool_sandbox_stderr tool=%s request_id=%s stderr=%s",
                tool.name,
                request_id,
                stderr[:2000],
            )

        stdout = (completed.stdout or "").strip()
        if not stdout:
            raise ToolSandboxError(f"Sandbox returned empty stdout for tool '{tool.name}'")
        try:
            decoded = json.loads(stdout)
        except Exception as exc:
            raise ToolSandboxError(
                f"Sandbox returned non-JSON output for tool '{tool.name}'"
            ) from exc
        if not isinstance(decoded, dict):
            raise ToolSandboxError(f"Sandbox response must be object for tool '{tool.name}'")

        if not bool(decoded.get("ok")):
            message = str(decoded.get("error") or "sandbox_execution_failed")
            raise ToolSandboxError(message)
        return decoded.get("result")

    def _limits_for_tool(self, tool: ToolDefinition) -> dict[str, Any]:
        allow_network = tool.name in set(self.config.allow_network_tools)
        if str(tool.source).startswith("plugin:"):
            allow_network = False
        allowed_roots = [
            str(Path(item).expanduser().resolve())
            for item in self.config.allowed_roots
            if str(item).strip()
        ]
        if not allowed_roots:
            allowed_roots = [str(Path.cwd().resolve())]
        return {
            "max_cpu_sec": max(1, int(self.config.max_cpu_sec)),
            "max_memory_mb": max(64, int(self.config.max_memory_mb)),
            "max_timeout_sec": max(1, int(self.config.timeout_sec)),
            "max_code_chars": max(100, int(self.config.max_python_code_chars)),
            "allow_network": bool(allow_network),
            "allowed_roots": allowed_roots,
            "filesystem_allow_write": bool(self.config.filesystem_allow_write),
        }

    @staticmethod
    def _sandbox_env() -> dict[str, str]:
        env: dict[str, str] = {
            "PYTHONUNBUFFERED": "1",
            "PATH": os.getenv("PATH", ""),
            "LANG": os.getenv("LANG", "C.UTF-8"),
            "LC_ALL": os.getenv("LC_ALL", "C.UTF-8"),
            "TMPDIR": os.getenv("TMPDIR", "/tmp"),
        }
        return env
