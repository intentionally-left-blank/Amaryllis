from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from tools.plugin_capabilities import (
    default_allowed_plugin_capabilities,
    plugin_capabilities_requiring_approval,
    plugin_capability_policy_snapshot,
    supported_plugin_capabilities,
)
from tools.tool_registry import ToolDefinition

_MAX_DESERIALIZATION_SCAN_VALUES = 200
_MAX_DESERIALIZATION_SCAN_TEXT_CHARS = 4000
_UNSAFE_DESERIALIZATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("pickle_load", re.compile(r"\bpickle\s*\.\s*loads?\s*\(", flags=re.IGNORECASE)),
    ("pickle_unpickler", re.compile(r"\bpickle\s*\.\s*Unpickler\s*\(", flags=re.IGNORECASE)),
    ("cpickle_load", re.compile(r"\bcpickle\s*\.\s*loads?\s*\(", flags=re.IGNORECASE)),
    ("cloudpickle_load", re.compile(r"\bcloudpickle\s*\.\s*loads?\s*\(", flags=re.IGNORECASE)),
    ("dill_load", re.compile(r"\bdill\s*\.\s*loads?\s*\(", flags=re.IGNORECASE)),
    ("marshal_load", re.compile(r"\bmarshal\s*\.\s*loads?\s*\(", flags=re.IGNORECASE)),
    ("yaml_load", re.compile(r"\byaml\s*\.\s*load\s*\(", flags=re.IGNORECASE)),
    ("yaml_full_load", re.compile(r"\byaml\s*\.\s*full_load\s*\(", flags=re.IGNORECASE)),
    ("yaml_unsafe_load", re.compile(r"\byaml\s*\.\s*unsafe_load\s*\(", flags=re.IGNORECASE)),
    (
        "yaml_unsafe_loader",
        re.compile(r"\bLoader\s*=\s*yaml\s*\.\s*(Loader|UnsafeLoader)\b", flags=re.IGNORECASE),
    ),
    (
        "yaml_python_tag",
        re.compile(
            r"!!python/(object|object/new|name|module)|tag:yaml\.org,2002:python/(object|object/new|name|module)",
            flags=re.IGNORECASE,
        ),
    ),
    (
        "numpy_allow_pickle",
        re.compile(r"\bnumpy\s*\.\s*load\s*\([^)]*allow_pickle\s*=\s*true", flags=re.IGNORECASE | re.DOTALL),
    ),
    ("torch_load", re.compile(r"\btorch\s*\.\s*load\s*\(", flags=re.IGNORECASE)),
    ("joblib_load", re.compile(r"\bjoblib\s*\.\s*load\s*\(", flags=re.IGNORECASE)),
    ("pandas_read_pickle", re.compile(r"\bpandas\s*\.\s*read_pickle\s*\(", flags=re.IGNORECASE)),
    ("pd_read_pickle", re.compile(r"\bpd\s*\.\s*read_pickle\s*\(", flags=re.IGNORECASE)),
    ("jsonpickle_decode", re.compile(r"\bjsonpickle\s*\.\s*decode\s*\(", flags=re.IGNORECASE)),
)


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
        allowed_plugin_capabilities: list[str] | None = None,
        blocked_plugin_capabilities: list[str] | None = None,
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
        supported_capabilities = supported_plugin_capabilities()
        allowed_source = (
            list(allowed_plugin_capabilities)
            if allowed_plugin_capabilities is not None
            else default_allowed_plugin_capabilities()
        )
        self.allowed_plugin_capabilities = {
            str(item).strip().lower()
            for item in allowed_source
            if str(item).strip().lower() in supported_capabilities
        }
        self.blocked_plugin_capabilities = {
            str(item).strip().lower()
            for item in (blocked_plugin_capabilities or [])
            if str(item).strip().lower() in supported_capabilities
        }
        self._plugin_capabilities_requiring_approval = plugin_capabilities_requiring_approval()

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

        plugin_allowed, plugin_requires_approval, plugin_reason = self._evaluate_plugin_capabilities(tool)
        if not plugin_allowed:
            return ToolDecision(
                allow=False,
                requires_approval=False,
                reason=plugin_reason or "Plugin capability policy blocked tool.",
            )

        tool_name = str(tool.name).strip().lower()
        unsafe_deserialization_match = self._unsafe_deserialization_match(arguments)
        if unsafe_deserialization_match is not None:
            return ToolDecision(
                allow=False,
                requires_approval=False,
                reason=(
                    f"Unsafe deserialization pattern '{unsafe_deserialization_match}' "
                    f"is blocked by isolation policy."
                ),
            )

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

        requires_approval = bool(plugin_requires_approval)
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

    def _evaluate_plugin_capabilities(self, tool: ToolDefinition) -> tuple[bool, bool, str | None]:
        if not str(tool.source or "").startswith("plugin:"):
            return True, False, None

        target = tool.execution_target if isinstance(tool.execution_target, dict) else {}
        raw = target.get("capabilities")
        if not isinstance(raw, list) or not raw:
            return False, False, f"Plugin tool '{tool.name}' is missing declared capabilities."

        capabilities = sorted({str(item).strip().lower() for item in raw if str(item).strip()})
        if not capabilities:
            return False, False, f"Plugin tool '{tool.name}' is missing declared capabilities."

        supported = supported_plugin_capabilities()
        unknown = [item for item in capabilities if item not in supported]
        if unknown:
            return (
                False,
                False,
                f"Plugin tool '{tool.name}' declares unsupported capabilities: {', '.join(unknown)}",
            )

        blocked = [item for item in capabilities if item in self.blocked_plugin_capabilities]
        if blocked:
            return (
                False,
                False,
                f"Plugin tool '{tool.name}' declares blocked capabilities: {', '.join(blocked)}",
            )

        disallowed = [item for item in capabilities if item not in self.allowed_plugin_capabilities]
        if disallowed:
            return (
                False,
                False,
                f"Plugin tool '{tool.name}' capabilities are not allowed by policy: {', '.join(disallowed)}",
            )

        if "filesystem_write" in capabilities and not self.filesystem_allow_write:
            return False, False, "Plugin filesystem write is disabled by isolation policy."

        requires_approval = any(
            capability in self._plugin_capabilities_requiring_approval
            for capability in capabilities
        )
        return True, requires_approval, None

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
            "plugin_capabilities": {
                "allowed": sorted(self.allowed_plugin_capabilities),
                "blocked": sorted(self.blocked_plugin_capabilities),
                "policy": plugin_capability_policy_snapshot(),
            },
            "unsafe_deserialization_denylist": [item[0] for item in _UNSAFE_DESERIALIZATION_PATTERNS],
        }

    def _unsafe_deserialization_match(self, arguments: dict[str, Any]) -> str | None:
        for value in self._iter_candidate_string_values(arguments):
            for pattern_id, pattern in _UNSAFE_DESERIALIZATION_PATTERNS:
                if pattern.search(value):
                    return pattern_id
        return None

    @staticmethod
    def _iter_candidate_string_values(payload: Any) -> list[str]:
        queue: list[Any] = [payload]
        values: list[str] = []
        while queue and len(values) < _MAX_DESERIALIZATION_SCAN_VALUES:
            current = queue.pop(0)
            if isinstance(current, str):
                normalized = current.strip()
                if normalized:
                    values.append(normalized[:_MAX_DESERIALIZATION_SCAN_TEXT_CHARS])
                continue
            if isinstance(current, dict):
                queue.extend(current.values())
                continue
            if isinstance(current, (list, tuple, set)):
                queue.extend(list(current))
        return values
