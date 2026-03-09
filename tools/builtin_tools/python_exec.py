from __future__ import annotations

import subprocess
import sys
from typing import Any

from tools.tool_registry import ToolRegistry


def _python_exec_handler(arguments: dict[str, Any]) -> dict[str, Any]:
    code = str(arguments.get("code", "")).strip()
    timeout = int(arguments.get("timeout", 8))

    if not code:
        raise ValueError("code is required")

    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=max(1, timeout),
    )

    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def register(registry: ToolRegistry) -> None:
    registry.register(
        name="python_exec",
        description="Execute a short Python snippet in a subprocess.",
        input_schema={
            "type": "object",
            "properties": {
                "code": {"type": "string"},
                "timeout": {"type": "integer", "minimum": 1, "maximum": 60},
            },
            "required": ["code"],
        },
        handler=_python_exec_handler,
    )
