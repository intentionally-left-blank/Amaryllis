from __future__ import annotations

import json
import re
from typing import Any

from tools.tool_registry import ToolRegistry

TOOL_CALL_PATTERN = re.compile(r"<tool_call>(.*?)</tool_call>", flags=re.DOTALL)


class ToolExecutionError(Exception):
    pass


class ToolExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool = self.registry.get(name)
        if tool is None:
            raise ToolExecutionError(f"Unknown tool: {name}")

        try:
            result = tool.handler(arguments)
        except Exception as exc:
            raise ToolExecutionError(f"Tool '{name}' failed: {exc}") from exc

        return {
            "tool": name,
            "result": result,
        }

    @staticmethod
    def parse_tool_call(text: str) -> dict[str, Any] | None:
        match = TOOL_CALL_PATTERN.search(text)
        if not match:
            return None

        payload = match.group(1).strip()
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            return None

        if not isinstance(data, dict):
            return None
        if "name" not in data or "arguments" not in data:
            return None
        if not isinstance(data["name"], str) or not isinstance(data["arguments"], dict):
            return None

        return data

    @staticmethod
    def render_tool_instruction(tool_names: list[str]) -> str:
        joined = ", ".join(sorted(tool_names))
        return (
            "If you need a tool, respond with exactly one JSON object wrapped as "
            "<tool_call>{\"name\":\"tool_name\",\"arguments\":{...}}</tool_call>. "
            f"Allowed tools: {joined}."
        )
