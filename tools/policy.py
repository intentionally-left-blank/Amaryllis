from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from tools.tool_registry import ToolDefinition


@dataclass(frozen=True)
class ToolDecision:
    allow: bool
    requires_approval: bool
    reason: str | None = None
    approval_scope: str | None = None
    approval_ttl_sec: int | None = None


class ToolIsolationPolicy:
    def __init__(
        self,
        blocked_tools: list[str] | None = None,
        profile: str = "balanced",
        allowed_high_risk_tools: list[str] | None = None,
        python_exec_max_timeout_sec: int = 10,
        python_exec_max_code_chars: int = 4000,
        filesystem_allow_write: bool = True,
    ) -> None:
        self.blocked_tools = {item.strip() for item in (blocked_tools or []) if item.strip()}
        normalized_profile = str(profile or "balanced").strip().lower()
        if normalized_profile not in {"balanced", "strict"}:
            normalized_profile = "balanced"
        self.profile = normalized_profile
        self.allowed_high_risk_tools = {
            item.strip()
            for item in (allowed_high_risk_tools or [])
            if item and item.strip()
        }
        self.python_exec_max_timeout_sec = max(1, int(python_exec_max_timeout_sec))
        self.python_exec_max_code_chars = max(100, int(python_exec_max_code_chars))
        self.filesystem_allow_write = bool(filesystem_allow_write)

    def evaluate(self, tool: ToolDefinition, arguments: dict[str, Any]) -> ToolDecision:
        if tool.name in self.blocked_tools:
            return ToolDecision(
                allow=False,
                requires_approval=False,
                reason="Tool is blocked by policy.",
            )

        normalized_risk = str(tool.risk_level or "low").strip().lower()
        if normalized_risk not in {"low", "medium", "high", "critical"}:
            normalized_risk = "medium"

        if self.profile == "balanced" and normalized_risk == "critical" and tool.name not in self.allowed_high_risk_tools:
            return ToolDecision(
                allow=False,
                requires_approval=False,
                reason=(
                    f"Tool '{tool.name}' is critical-risk and blocked in balanced isolation profile. "
                    "Allow explicitly via policy config."
                ),
            )

        if self.profile == "strict" and normalized_risk in {"high", "critical"} and tool.name not in self.allowed_high_risk_tools:
            return ToolDecision(
                allow=False,
                requires_approval=False,
                reason=(
                    f"Tool '{tool.name}' is high-risk and blocked in strict isolation profile. "
                    "Allow explicitly via policy config."
                ),
            )

        tool_name = str(tool.name).strip().lower()
        if tool_name == "python_exec":
            code = str(arguments.get("code", ""))
            if len(code) > self.python_exec_max_code_chars:
                return ToolDecision(
                    allow=False,
                    requires_approval=False,
                    reason=(
                        f"python_exec code size exceeds limit "
                        f"({len(code)} > {self.python_exec_max_code_chars})."
                    ),
                )
            try:
                timeout = int(arguments.get("timeout", 8))
            except Exception:
                timeout = 8
            if timeout > self.python_exec_max_timeout_sec:
                return ToolDecision(
                    allow=False,
                    requires_approval=False,
                    reason=(
                        f"python_exec timeout exceeds limit "
                        f"({timeout}s > {self.python_exec_max_timeout_sec}s)."
                    ),
                )

        if tool_name == "filesystem":
            action = str(arguments.get("action", "")).strip().lower()
            if action == "write" and not self.filesystem_allow_write:
                return ToolDecision(
                    allow=False,
                    requires_approval=False,
                    reason="filesystem write is disabled by isolation policy.",
                )

        requires_approval = False
        if tool.approval_mode == "required":
            requires_approval = True
        elif tool.approval_mode == "conditional" and tool.approval_predicate is not None:
            try:
                requires_approval = bool(tool.approval_predicate(arguments))
            except Exception:
                requires_approval = True

        if normalized_risk in {"high", "critical"}:
            requires_approval = True
        if self.profile == "strict" and normalized_risk in {"medium", "high", "critical"}:
            requires_approval = True
        if self.profile == "strict" and tool_name == "filesystem":
            action = str(arguments.get("action", "")).strip().lower()
            if action == "write":
                requires_approval = True

        approval_scope: str | None = None
        approval_ttl_sec: int | None = None
        if requires_approval:
            approval_scope = "request"
            approval_ttl_sec = 300
            if normalized_risk in {"high", "critical"}:
                approval_scope = "session"
                approval_ttl_sec = 600
            if tool_name == "filesystem":
                action = str(arguments.get("action", "")).strip().lower()
                if action == "write":
                    approval_scope = "session"
                    approval_ttl_sec = 300
            if tool_name == "python_exec":
                approval_scope = "request"
                approval_ttl_sec = 180

        return ToolDecision(
            allow=True,
            requires_approval=requires_approval,
            reason=None,
            approval_scope=approval_scope,
            approval_ttl_sec=approval_ttl_sec,
        )

    def describe(self) -> dict[str, Any]:
        tier_rules: dict[str, dict[str, str]] = {
            "low": {"balanced": "allow", "strict": "allow"},
            "medium": {"balanced": "allow", "strict": "allow_with_approval"},
            "high": {"balanced": "allow_with_approval", "strict": "blocked_unless_allowlist"},
            "critical": {"balanced": "blocked_unless_allowlist", "strict": "blocked_unless_allowlist"},
        }
        return {
            "profile": self.profile,
            "tier_rules": tier_rules,
            "blocked_tools": sorted(self.blocked_tools),
            "allowed_high_risk_tools": sorted(self.allowed_high_risk_tools),
            "python_exec_limits": {
                "max_timeout_sec": self.python_exec_max_timeout_sec,
                "max_code_chars": self.python_exec_max_code_chars,
            },
            "filesystem_allow_write": self.filesystem_allow_write,
        }
