from __future__ import annotations

import json
import re
from typing import Any

from tools.permission_manager import ToolPermissionManager
from tools.policy import ToolIsolationPolicy
from tools.tool_registry import ToolRegistry

TOOL_CALL_PATTERN = re.compile(r"<tool_call>(.*?)</tool_call>", flags=re.DOTALL)


class ToolExecutionError(Exception):
    pass


class PermissionRequiredError(ToolExecutionError):
    def __init__(self, message: str, prompt_id: str) -> None:
        super().__init__(message)
        self.prompt_id = prompt_id


class ToolExecutor:
    def __init__(
        self,
        registry: ToolRegistry,
        policy: ToolIsolationPolicy | None = None,
        permission_manager: ToolPermissionManager | None = None,
        approval_enforcement_mode: str = "prompt_and_allow",
    ) -> None:
        self.registry = registry
        self.policy = policy or ToolIsolationPolicy()
        self.permission_manager = permission_manager or ToolPermissionManager()
        self.approval_enforcement_mode = approval_enforcement_mode

    def execute(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        request_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        permission_id: str | None = None,
    ) -> dict[str, Any]:
        tool = self.registry.get(name)
        if tool is None:
            raise ToolExecutionError(f"Unknown tool: {name}")

        decision = self.policy.evaluate(tool=tool, arguments=arguments)
        if not decision.allow:
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
                    raise PermissionRequiredError(
                        f"Permission required for tool '{name}'. prompt_id={prompt_id}",
                        prompt_id=prompt_id,
                    )

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
