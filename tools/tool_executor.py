from __future__ import annotations

import json
import logging
import re
from typing import Any, Callable

from tools.permission_manager import ToolPermissionManager
from tools.policy import ToolIsolationPolicy
from tools.tool_budget import ToolBudgetExceededError, ToolBudgetGuard
from tools.tool_registry import ToolRegistry

TOOL_CALL_PATTERN = re.compile(r"<tool_call>(.*?)</tool_call>", flags=re.DOTALL)


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
        telemetry_emitter: Callable[[str, dict[str, Any]], None] | None = None,
    ) -> None:
        self.registry = registry
        self.policy = policy or ToolIsolationPolicy()
        self.permission_manager = permission_manager or ToolPermissionManager()
        self.budget_guard = budget_guard or ToolBudgetGuard()
        self.approval_enforcement_mode = approval_enforcement_mode
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

        permission_prompt: dict[str, Any] | None = None
        if decision.requires_approval:
            approved = False
            if permission_id:
                approved = self.permission_manager.consume_if_approved(
                    permission_id,
                    tool_name=name,
                    arguments=arguments,
                )
            if not approved and permission_ids:
                for candidate in permission_ids:
                    if not candidate:
                        continue
                    approved = self.permission_manager.consume_if_approved(
                        candidate,
                        tool_name=name,
                        arguments=arguments,
                    )
                    if approved:
                        break
            if not approved:
                permission_prompt = self.permission_manager.request(
                    tool_name=name,
                    arguments=arguments,
                    reason=f"Tool '{name}' requires manual approval.",
                    request_id=request_id,
                    user_id=user_id,
                    session_id=session_id,
                )
                if self.approval_enforcement_mode == "strict":
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
            "isolation_policy": {
                "profile": self.policy.profile,
                "blocked_tools": sorted(self.policy.blocked_tools),
                "allowed_high_risk_tools": sorted(self.policy.allowed_high_risk_tools),
                "python_exec_max_timeout_sec": self.policy.python_exec_max_timeout_sec,
                "python_exec_max_code_chars": self.policy.python_exec_max_code_chars,
                "filesystem_allow_write": self.policy.filesystem_allow_write,
            },
            "budget": self.budget_guard.debug_snapshot(
                request_id=request_id,
                user_id=user_id,
                session_id=session_id,
                scopes_limit=scopes_limit,
                top_tools_limit=top_tools_limit,
            ),
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
