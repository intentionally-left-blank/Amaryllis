from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tools.tool_registry import ToolDefinition


@dataclass(frozen=True)
class ToolDecision:
    allow: bool
    requires_approval: bool
    reason: str | None = None


class ToolIsolationPolicy:
    def __init__(self, blocked_tools: list[str] | None = None) -> None:
        self.blocked_tools = {item.strip() for item in (blocked_tools or []) if item.strip()}

    def evaluate(self, tool: ToolDefinition, arguments: dict[str, Any]) -> ToolDecision:
        if tool.name in self.blocked_tools:
            return ToolDecision(
                allow=False,
                requires_approval=False,
                reason="Tool is blocked by policy.",
            )

        requires_approval = False
        if tool.approval_mode == "required":
            requires_approval = True
        elif tool.approval_mode == "conditional" and tool.approval_predicate is not None:
            try:
                requires_approval = bool(tool.approval_predicate(arguments))
            except Exception:
                requires_approval = True

        return ToolDecision(
            allow=True,
            requires_approval=requires_approval,
            reason=None,
        )
