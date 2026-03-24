from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from tools.autonomy_policy import AutonomyPolicy
from tools.permission_manager import ToolPermissionManager
from tools.policy import ToolIsolationPolicy
from tools.sandbox_runner import ToolSandboxRunner
from tools.tool_budget import ToolBudgetExceededError, ToolBudgetGuard
from tools.tool_registry import ToolRegistry

TOOL_CALL_PATTERN = re.compile(r"\s*<tool_call>\s*(\{.*\})\s*</tool_call>\s*", flags=re.DOTALL)


class ToolExecutionError(Exception):
    pass


class PermissionRequiredError(ToolExecutionError):
    def __init__(self, message: str, prompt_id: str) -> None:
        super().__init__(message)
        self.prompt_id = prompt_id


class ToolBudgetLimitError(ToolExecutionError):
    pass


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        policy: ToolIsolationPolicy | None = None,
        permission_manager: ToolPermissionManager | None = None,
        budget_guard: ToolBudgetGuard | None = None,
        approval_enforcement_mode: str = "prompt_and_allow",
        autonomy_policy: AutonomyPolicy | None = None,
        sandbox_runner: ToolSandboxRunner | None = None,
        telemetry_emitter: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.registry = registry
        self.policy = policy or ToolIsolationPolicy()
        self.permission_manager = permission_manager or ToolPermissionManager()
        self.budget_guard = budget_guard or ToolBudgetGuard()
        self.approval_enforcement_mode = approval_enforcement_mode
        self.autonomy_policy = autonomy_policy or AutonomyPolicy(level="l3")
        self.sandbox_runner = sandbox_runner
        self.telemetry_emitter = telemetry_emitter
        self.logger = logging.getLogger("amaryllis.tools.executor")

    def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        request_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        permission_id: str | None = None,
        permission_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        tool = self.registry.get(name)
        if tool is None:
            raise ToolExecutionError(f"Unknown tool: {name}")

        autonomy_decision = self.autonomy_policy.evaluate(
            tool_name=name,
            risk_level=str(getattr(tool, "risk_level", "medium")),
        )
        if not autonomy_decision.allow:
            self._emit_telemetry(
                "tool_autonomy_blocked",
                {
                    "tool": name,
                    "request_id": request_id,
                    "user_id": user_id,
                    "session_id": session_id,
                    "reason": autonomy_decision.reason,
                    "autonomy_level": self.autonomy_policy.level,
                    "risk_level": str(getattr(tool, "risk_level", "medium")),
                },
            )
            raise ToolExecutionError(autonomy_decision.reason or f"Tool '{name}' blocked by autonomy policy")

        decision = self.policy.evaluate(tool=tool, arguments=arguments)
        if not decision.allow:
            self._emit_telemetry(
                "tool_policy_blocked",
                {
                    "tool": name,
                    "request_id": request_id,
                    "user_id": user_id,
                    "session_id": session_id,
                    "reason": decision.reason,
                },
            )
            raise ToolExecutionError(decision.reason or f"Tool '{name}' is blocked by policy")

        requires_approval = bool(decision.requires_approval or autonomy_decision.requires_approval)
        approval_scope = self._merge_approval_scope(
            decision.approval_scope,
            autonomy_decision.approval_scope,
        )
        approval_ttl_sec = self._merge_approval_ttl(
            decision.approval_ttl_sec,
            autonomy_decision.approval_ttl_sec,
        )
        approval_reason = autonomy_decision.reason or f"Tool '{name}' requires manual approval."

        permission_prompt: dict[str, Any] | None = None
        if requires_approval:
            approved = False
            if permission_id:
                approved = self.permission_manager.consume_if_approved(
                    permission_id,
                    tool_name=name,
                    arguments=arguments,
                    request_id=request_id,
                    user_id=user_id,
                    session_id=session_id,
                )
            if not approved and permission_ids:
                for candidate in permission_ids:
                    if not candidate:
                        continue
                    approved = self.permission_manager.consume_if_approved(
                        candidate,
                        tool_name=name,
                        arguments=arguments,
                        request_id=request_id,
                        user_id=user_id,
                        session_id=session_id,
                    )
                    if approved:
                        break
            if not approved:
                permission_prompt = self.permission_manager.request(
                    tool_name=name,
                    arguments=arguments,
                    reason=approval_reason,
                    scope=str(approval_scope or "request"),
                    ttl_sec=int(approval_ttl_sec or 300),
                    request_id=request_id,
                    user_id=user_id,
                    session_id=session_id,
                )
                if self._must_block_without_approval(tool=tool):
                    prompt_id = str(permission_prompt.get("id"))
                    self._emit_telemetry(
                        "tool_permission_required",
                        {
                            "tool": name,
                            "request_id": request_id,
                            "user_id": user_id,
                            "session_id": session_id,
                            "prompt_id": prompt_id,
                            "approval_mode": self.approval_enforcement_mode,
                            "risk_level": tool.risk_level,
                            "approval_mode_tool": tool.approval_mode,
                            "autonomy_level": self.autonomy_policy.level,
                        },
                    )
                    raise PermissionRequiredError(
                        f"Permission required for tool '{name}'. prompt_id={prompt_id}",
                        prompt_id=prompt_id,
                    )

        try:
            budget_status = self.budget_guard.check_and_record(
                tool_name=name,
                risk_level=tool.risk_level,
                request_id=request_id,
                user_id=user_id,
                session_id=session_id,
            )
            self.logger.info(
                "tool_budget_recorded tool=%s scope=%s total=%s/%s per_tool=%s/%s high_risk=%s/%s",
                name,
                budget_status.scope,
                budget_status.total_calls,
                budget_status.max_total_calls,
                budget_status.per_tool_calls,
                budget_status.max_calls_per_tool,
                budget_status.high_risk_calls,
                budget_status.max_high_risk_calls,
            )
            self._emit_telemetry(
                "tool_budget_recorded",
                {
                    "tool": name,
                    "risk_level": tool.risk_level,
                    "scope": budget_status.scope,
                    "request_id": request_id,
                    "user_id": user_id,
                    "session_id": session_id,
                    "total_calls": budget_status.total_calls,
                    "max_total_calls": budget_status.max_total_calls,
                    "per_tool_calls": budget_status.per_tool_calls,
                    "max_calls_per_tool": budget_status.max_calls_per_tool,
                    "high_risk_calls": budget_status.high_risk_calls,
                    "max_high_risk_calls": budget_status.max_high_risk_calls,
                    "window_sec": budget_status.window_sec,
                },
            )
        except ToolBudgetExceededError as exc:
            self._emit_telemetry(
                "tool_budget_blocked",
                {
                    "tool": name,
                    "risk_level": tool.risk_level,
                    "request_id": request_id,
                    "user_id": user_id,
                    "session_id": session_id,
                    "reason": str(exc),
                },
            )
            raise ToolBudgetLimitError(str(exc)) from exc

        try:
            if self.sandbox_runner is not None and tool.execution_target is not None:
                result = self.sandbox_runner.execute(
                    tool=tool,
                    arguments=arguments,
                    request_id=request_id,
                    user_id=user_id,
                    session_id=session_id,
                )
            else:
                result = tool.handler(arguments)
        except Exception as exc:
            raise ToolExecutionError(f"Tool '{name}' failed: {exc}") from exc

        payload: dict[str, Any] = {
            "tool": name,
            "result": result,
        }
        if permission_prompt is not None:
            payload["permission_prompt"] = permission_prompt
            payload["approval_mode"] = self.approval_enforcement_mode
        return payload

    def _must_block_without_approval(self, *, tool: Any) -> bool:
        if self.approval_enforcement_mode == "strict":
            return True

        risk = str(getattr(tool, "risk_level", "low") or "low").strip().lower()
        if risk in {"high", "critical"}:
            return True

        approval_mode = str(getattr(tool, "approval_mode", "none") or "none").strip().lower()
        if approval_mode == "required":
            return True
        return False

    def list_permission_prompts(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        return self.permission_manager.list(status=status, limit=limit)

    def approve_permission_prompt(self, prompt_id: str) -> dict[str, Any]:
        return self.permission_manager.approve(prompt_id)

    def deny_permission_prompt(self, prompt_id: str) -> dict[str, Any]:
        return self.permission_manager.deny(prompt_id)

    def debug_guardrails(
        self,
        *,
        request_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        scopes_limit: int = 20,
        top_tools_limit: int = 5,
    ) -> dict[str, Any]:
        return {
            "approval_enforcement_mode": self.approval_enforcement_mode,
            "autonomy_policy": self.autonomy_policy.describe(),
            "isolation_policy": self.policy.describe(),
            "sandbox": {
                "enabled": self.sandbox_runner is not None,
                "timeout_sec": (
                    int(self.sandbox_runner.config.timeout_sec)
                    if self.sandbox_runner is not None
                    else None
                ),
                "max_cpu_sec": (
                    int(self.sandbox_runner.config.max_cpu_sec)
                    if self.sandbox_runner is not None
                    else None
                ),
                "max_memory_mb": (
                    int(self.sandbox_runner.config.max_memory_mb)
                    if self.sandbox_runner is not None
                    else None
                ),
            },
            "budget": self.budget_guard.debug_snapshot(
                request_id=request_id,
                user_id=user_id,
                session_id=session_id,
                scopes_limit=scopes_limit,
                top_tools_limit=top_tools_limit,
            ),
            "plugin_signing": self.registry.plugin_discovery_report(limit=200),
        }

    @staticmethod
    def _merge_approval_scope(policy_scope: str | None, autonomy_scope: str | None) -> str | None:
        order = {"request": 1, "session": 2}
        current = str(policy_scope or "").strip().lower() or None
        extra = str(autonomy_scope or "").strip().lower() or None
        if current is None:
            return extra
        if extra is None:
            return current
        return extra if order.get(extra, 0) > order.get(current, 0) else current

    @staticmethod
    def _merge_approval_ttl(policy_ttl: int | None, autonomy_ttl: int | None) -> int | None:
        first = int(policy_ttl) if isinstance(policy_ttl, int) and policy_ttl > 0 else 0
        second = int(autonomy_ttl) if isinstance(autonomy_ttl, int) and autonomy_ttl > 0 else 0
        merged = max(first, second)
        return merged if merged > 0 else None

    @staticmethod
    def parse_tool_call(text: str) -> dict[str, Any] | None:
        match = TOOL_CALL_PATTERN.fullmatch(str(text or ""))
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
            f"Allowed tools: {joined}. "
            "Some tools may require manual approval."
        )

    def _emit_telemetry(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.telemetry_emitter is None:
            return
        try:
            self.telemetry_emitter(event_type, payload)
        except Exception as exc:
            self.logger.warning("tool_telemetry_emit_failed event=%s error=%s", event_type, exc)
