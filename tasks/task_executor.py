from __future__ import annotations

import json
import time
from typing import Any, Callable

from agents.agent import Agent
from controller.meta_controller import MetaController
from memory.memory_manager import MemoryManager
from models.model_manager import ModelManager
from planner.planner import Planner
from tools.tool_executor import PermissionRequiredError, ToolExecutionError, ToolExecutor
from tools.tool_registry import ToolRegistry

CheckpointWriter = Callable[[dict[str, Any]], None]


class TaskExecutor:
    def __init__(
        self,
        model_manager: ModelManager,
        memory_manager: MemoryManager,
        tool_registry: ToolRegistry,
        tool_executor: ToolExecutor,
        meta_controller: MetaController,
        planner: Planner,
    ) -> None:
        self.model_manager = model_manager
        self.memory_manager = memory_manager
        self.tool_registry = tool_registry
        self.tool_executor = tool_executor
        self.meta_controller = meta_controller
        self.planner = planner

    def execute(
        self,
        agent: Agent,
        user_id: str,
        session_id: str | None,
        user_message: str,
        checkpoint: CheckpointWriter | None = None,
    ) -> dict[str, Any]:
        tools_available = bool(agent.tools)
        strategy = self.meta_controller.choose_strategy(
            user_message=user_message,
            tools_available=tools_available,
        )
        self._emit_checkpoint(
            checkpoint,
            stage="strategy_selected",
            message=f"Strategy selected: {strategy}",
            strategy=strategy,
            tools_available=tools_available,
        )
        plan = self.planner.create_plan(task=user_message, strategy=strategy)
        self._emit_checkpoint(
            checkpoint,
            stage="plan_created",
            message=f"Plan created with {len(plan)} steps.",
            plan_steps=[step.__dict__ for step in plan],
        )

        self.memory_manager.add_interaction(
            user_id=user_id,
            agent_id=agent.id,
            role="user",
            content=user_message,
            session_id=session_id,
        )

        memory_context = self.memory_manager.get_context(
            user_id=user_id,
            agent_id=agent.id,
            query=user_message,
            session_id=session_id,
        )
        self._emit_checkpoint(
            checkpoint,
            stage="memory_loaded",
            message="Memory context loaded.",
            working_count=len(memory_context.get("working", [])),
            episodic_count=len(memory_context.get("episodic", [])),
            semantic_count=len(memory_context.get("semantic", [])),
            profile_count=len(memory_context.get("profile", [])),
        )

        messages = self._build_messages(
            agent=agent,
            user_message=user_message,
            memory_context=memory_context,
            session_id=session_id,
        )

        tool_events: list[dict[str, Any]] = []
        self._emit_checkpoint(
            checkpoint,
            stage="reasoning_started",
            message="LLM reasoning started.",
            tools_allowed=agent.tools,
        )
        response_text, provider_used, model_used = self._reason_with_optional_tools(
            messages=messages,
            agent=agent,
            tool_events=tool_events,
            user_id=user_id,
            session_id=session_id,
            checkpoint=checkpoint,
        )
        self._emit_checkpoint(
            checkpoint,
            stage="reasoning_completed",
            message="LLM reasoning completed.",
            provider=provider_used,
            model=model_used,
            tool_events_count=len(tool_events),
        )

        self.memory_manager.add_interaction(
            user_id=user_id,
            agent_id=agent.id,
            role="assistant",
            content=response_text,
            session_id=session_id,
        )
        self.memory_manager.remember_fact(
            user_id=user_id,
            text=f"Agent {agent.name} response: {response_text[:1000]}",
            metadata={
                "agent_id": agent.id,
                "kind": "response",
            },
        )
        self._emit_checkpoint(
            checkpoint,
            stage="memory_updated",
            message="Assistant response stored in memory layers.",
        )

        return {
            "agent_id": agent.id,
            "session_id": session_id,
            "strategy": strategy,
            "plan": [step.__dict__ for step in plan],
            "provider": provider_used,
            "model": model_used,
            "tools": tool_events,
            "response": response_text,
        }

    def _build_messages(
        self,
        agent: Agent,
        user_message: str,
        memory_context: dict[str, Any],
        session_id: str | None,
    ) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []

        system_prompt = agent.system_prompt.strip()
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        memory_note = self._render_memory_note(memory_context=memory_context, session_id=session_id)
        if memory_note:
            messages.append({"role": "system", "content": memory_note})

        for event in memory_context.get("episodic", []):
            role = str(event.get("role", "user"))
            content = str(event.get("content", ""))
            if content:
                messages.append({"role": role, "content": content})

        messages.append({"role": "user", "content": user_message})
        return messages

    def _reason_with_optional_tools(
        self,
        messages: list[dict[str, Any]],
        agent: Agent,
        tool_events: list[dict[str, Any]],
        user_id: str,
        session_id: str | None,
        checkpoint: CheckpointWriter | None = None,
    ) -> tuple[str, str, str]:
        allowed_tools = [name for name in agent.tools if self.tool_registry.get(name) is not None]

        reasoning_messages = list(messages)
        if allowed_tools:
            reasoning_messages.append(
                {
                    "role": "system",
                    "content": self.tool_executor.render_tool_instruction(allowed_tools),
                }
            )

        first = self.model_manager.chat(
            messages=reasoning_messages,
            model=agent.model,
        )
        response_text = str(first.get("content", "")).strip()
        provider_used = str(first.get("provider", "unknown"))
        model_used = str(first.get("model", agent.model or "unknown"))
        self._emit_checkpoint(
            checkpoint,
            stage="llm_response",
            message="Received model response.",
            provider=provider_used,
            model=model_used,
            preview=response_text[:240],
        )

        if not allowed_tools:
            return response_text, provider_used, model_used

        for attempt in range(1, 3):
            parsed = self.tool_executor.parse_tool_call(response_text)
            if not parsed:
                self._emit_checkpoint(
                    checkpoint,
                    stage="tool_loop_done",
                    message="No tool call requested by model.",
                    attempt=attempt,
                )
                break

            tool_name = str(parsed["name"])
            arguments = parsed["arguments"]
            self._emit_checkpoint(
                checkpoint,
                stage="tool_call_started",
                message=f"Tool call started: {tool_name}",
                tool=tool_name,
                attempt=attempt,
            )
            event: dict[str, Any] = {
                "attempt": attempt,
                "tool": tool_name,
                "arguments": arguments,
                "status": "started",
            }
            if tool_name not in allowed_tools:
                event["status"] = "blocked"
                event["error"] = "Tool is not allowed for this agent"
                tool_events.append(event)
                self._emit_checkpoint(
                    checkpoint,
                    stage="tool_call_blocked",
                    message=f"Tool is not allowed: {tool_name}",
                    tool=tool_name,
                    attempt=attempt,
                )
                break

            started_at = time.perf_counter()
            try:
                tool_result = self.tool_executor.execute(
                    name=tool_name,
                    arguments=arguments,
                    user_id=user_id,
                    session_id=session_id,
                )
                event["status"] = "succeeded"
                event["result"] = tool_result.get("result")
                if "permission_prompt" in tool_result:
                    event["permission_prompt"] = tool_result["permission_prompt"]
                self._emit_checkpoint(
                    checkpoint,
                    stage="tool_call_succeeded",
                    message=f"Tool executed successfully: {tool_name}",
                    tool=tool_name,
                    attempt=attempt,
                )
            except PermissionRequiredError as exc:
                tool_result = {
                    "tool": tool_name,
                    "error": str(exc),
                    "permission_prompt_id": exc.prompt_id,
                }
                event["status"] = "permission_required"
                event["error"] = str(exc)
                event["permission_prompt_id"] = exc.prompt_id
                self._emit_checkpoint(
                    checkpoint,
                    stage="tool_call_permission_required",
                    message=f"Permission required for tool: {tool_name}",
                    tool=tool_name,
                    attempt=attempt,
                    permission_prompt_id=exc.prompt_id,
                )
            except ToolExecutionError as exc:
                tool_result = {
                    "tool": tool_name,
                    "error": str(exc),
                }
                event["status"] = "failed"
                event["error"] = str(exc)
                self._emit_checkpoint(
                    checkpoint,
                    stage="tool_call_failed",
                    message=f"Tool execution failed: {tool_name}",
                    tool=tool_name,
                    attempt=attempt,
                    error=str(exc),
                )

            event["duration_ms"] = round((time.perf_counter() - started_at) * 1000.0, 2)
            tool_events.append(event)
            self._emit_checkpoint(
                checkpoint,
                stage="tool_call_finished",
                message=f"Tool call finished: {tool_name}",
                tool=tool_name,
                attempt=attempt,
                status=event.get("status"),
                duration_ms=event["duration_ms"],
            )

            reasoning_messages.append({"role": "assistant", "content": response_text})
            reasoning_messages.append(
                {
                    "role": "tool",
                    "name": tool_name,
                    "content": json.dumps(tool_result, ensure_ascii=False),
                }
            )
            reasoning_messages.append(
                {
                    "role": "system",
                    "content": "Tool output is provided. Produce a final user-facing answer.",
                }
            )

            followup = self.model_manager.chat(
                messages=reasoning_messages,
                model=agent.model,
            )
            response_text = str(followup.get("content", "")).strip()
            provider_used = str(followup.get("provider", provider_used))
            model_used = str(followup.get("model", model_used))
            self._emit_checkpoint(
                checkpoint,
                stage="llm_followup_response",
                message="Received follow-up model response after tool output.",
                provider=provider_used,
                model=model_used,
                attempt=attempt,
                preview=response_text[:240],
            )

        return response_text, provider_used, model_used

    @staticmethod
    def _render_memory_note(memory_context: dict[str, Any], session_id: str | None) -> str:
        user_profile = memory_context.get("user", {})
        semantic = memory_context.get("semantic", [])
        working = memory_context.get("working", [])
        profile = memory_context.get("profile", [])

        payload = {
            "session_id": session_id,
            "working_memory": working,
            "user_profile": user_profile,
            "profile_memory": profile,
            "semantic_memory": semantic,
        }
        return "Memory context: " + json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _emit_checkpoint(
        checkpoint: CheckpointWriter | None,
        *,
        stage: str,
        message: str,
        **extra: Any,
    ) -> None:
        if checkpoint is None:
            return
        payload: dict[str, Any] = {
            "stage": stage,
            "message": message,
        }
        payload.update(extra)
        try:
            checkpoint(payload)
        except Exception:
            return
