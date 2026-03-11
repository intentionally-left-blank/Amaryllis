from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.tool_registry import ToolRegistry

WORKSPACE_ROOT = Path.cwd()


def _safe_path(raw_path: str) -> Path:
    candidate = (WORKSPACE_ROOT / raw_path).resolve()
    candidate.relative_to(WORKSPACE_ROOT)
    return candidate


def _filesystem_handler(arguments: dict[str, Any]) -> dict[str, Any]:
    action = str(arguments.get("action", "")).strip().lower()
    path = str(arguments.get("path", ".")).strip()

    target = _safe_path(path)

    if action == "list":
        if not target.exists() or not target.is_dir():
            raise FileNotFoundError(f"Directory not found: {target}")
        items = sorted(p.name for p in target.iterdir())
        return {"items": items}

    if action == "read":
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(f"File not found: {target}")
        return {"content": target.read_text(encoding="utf-8")}

    if action == "write":
        content = str(arguments.get("content", ""))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"written": True, "path": str(target)}

    raise ValueError("Unsupported action. Use one of: list, read, write")


def register(registry: ToolRegistry) -> None:
    registry.register(
        name="filesystem",
        description="Read, write, and list files inside the current workspace.",
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "read", "write"],
                },
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["action", "path"],
        },
        handler=_filesystem_handler,
        source="builtin",
        risk_level="medium",
        approval_mode="conditional",
        approval_predicate=lambda args: str(args.get("action", "")).strip().lower() == "write",
        isolation="workspace_only",
    )
