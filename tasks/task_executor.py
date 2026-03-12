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

STEP_PREPARE_CONTEXT = "prepare_context"
STEP_REASONING = "reasoning"
STEP_PERSIST = "persist"


class TaskGuardrailError(RuntimeError):
    pass


class TaskTimeoutError(RuntimeError):
    pass


class TaskExecutor:
    def __init__(
        self,
        model_manager: ModelManager,
        memory_manager: MemoryManager,
        tool_registry: ToolRegistry,
        tool_executor: ToolExecutor,
        meta_controller: MetaController,
        planner: Planner,
        max_duration_sec: float = 120.0,
        max_model_calls: int = 6,
        max_prompt_chars: int = 40000,
        max_tool_rounds: int = 3,
    ) -> None:
        self.model_manager = model_manager
        self.memory_manager = memory_manager
        self.tool_registry = tool_registry
        self.tool_executor = tool_executor
        self.meta_controller = meta_controller
        self.planner = planner
        self.max_duration_sec = max(10.0, float(max_duration_sec))
        self.max_model_calls = max(1, int(max_model_calls))
        self.max_prompt_chars = max(2000, int(max_prompt_chars))
        self.max_tool_rounds = max(1, int(max_tool_rounds))

    def execute(
        self,
        agent: Agent,
        user_id: str,
        session_id: str | None,
        user_message: str,
        checkpoint: CheckpointWriter | None = None,
        run_deadline_monotonic: float | None = None,
        resume_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        self._check_runtime(started=started, run_deadline_monotonic=run_deadline_monotonic)
        self._check_message_size(user_message)

        state = self._normalize_resume_state(resume_state)
        completed_steps = set(state.get("completed_steps", []))

        strategy = str(state.get("strategy", "")).strip() or None
        plan: list[dict[str, Any]] = []
        if isinstance(state.get("plan"), list):
            plan = [item for item in state["plan"] if isinstance(item, dict)]

        model_calls = int(state.get("model_calls", 0))
        tool_rounds = int(state.get("tool_rounds", 0))
        response_text = str(state.get("response_text", "")).strip() if state.get("response_text") is not None else ""
        provider_used = str(state.get("provider", "")).strip() if state.get("provider") is not None else ""
        model_used = str(state.get("model", "")).strip() if state.get("model") is not None else ""

        tool_events: list[dict[str, Any]] = []
        raw_events = state.get("tool_events")
        if isinstance(raw_events, list):
            tool_events = [item for item in raw_events if isinstance(item, dict)]

        tools_available = bool(agent.tools)

        if STEP_PREPARE_CONTEXT not in completed_steps:
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

            created_plan = self.planner.create_plan(task=user_message, strategy=strategy)
            plan = [step.__dict__ for step in created_plan]
            self._emit_checkpoint(
                checkpoint,
                stage="plan_created",
                message=f"Plan created with {len(plan)} steps.",
                plan_steps=plan,
            )

            self.memory_manager.add_interaction(
                user_id=user_id,
                agent_id=agent.id,
                role="user",
                content=user_message,
                session_id=session_id,
            )

            self._check_runtime(started=started, run_deadline_monotonic=run_deadline_monotonic)
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
            self._check_prompt_size(messages)

            completed_steps.add(STEP_PREPARE_CONTEXT)
            snapshot = self._build_resume_snapshot(
                completed_steps=completed_steps,
                strategy=strategy,
                plan=plan,
                model_calls=model_calls,
                tool_rounds=tool_rounds,
                response_text=response_text,
                provider_used=provider_used,
                model_used=model_used,
                tool_events=tool_events,
            )
            self._emit_checkpoint(
                checkpoint,
                stage="step_completed",
                step=STEP_PREPARE_CONTEXT,
                message="Preparation step completed.",
                resume_state=snapshot,
            )
        else:
            if not strategy:
                strategy = self.meta_controller.choose_strategy(
                    user_message=user_message,
                    tools_available=tools_available,
                )
            if not plan:
                created_plan = self.planner.create_plan(task=user_message, strategy=strategy)
                plan = [step.__dict__ for step in created_plan]

            self._check_runtime(started=started, run_deadline_monotonic=run_deadline_monotonic)
            memory_context = self.memory_manager.get_context(
                user_id=user_id,
                agent_id=agent.id,
                query=user_message,
                session_id=session_id,
            )
            messages = self._build_messages(
                agent=agent,
                user_message=user_message,
                memory_context=memory_context,
                session_id=session_id,
            )
            self._check_prompt_size(messages)
            self._emit_checkpoint(
                checkpoint,
                stage="step_resumed",
                step=STEP_PREPARE_CONTEXT,
                message="Preparation step resumed from checkpoint.",
                completed_steps=sorted(completed_steps),
            )

        if STEP_REASONING not in completed_steps:
            self._emit_checkpoint(
                checkpoint,
                stage="reasoning_started",
                message="LLM reasoning started.",
                tools_allowed=agent.tools,
            )
            response_text, provider_used, model_used, model_calls, tool_rounds = self._reason_with_optional_tools(
                messages=messages,
                agent=agent,
                tool_events=tool_events,
                user_id=user_id,
                session_id=session_id,
                checkpoint=checkpoint,
                model_calls=model_calls,
                tool_rounds=tool_rounds,
                started=started,
                run_deadline_monotonic=run_deadline_monotonic,
            )
            self._emit_checkpoint(
                checkpoint,
                stage="reasoning_completed",
                message="LLM reasoning completed.",
                provider=provider_used,
                model=model_used,
                tool_events_count=len(tool_events),
                model_calls=model_calls,
                tool_rounds=tool_rounds,
            )

            completed_steps.add(STEP_REASONING)
            snapshot = self._build_resume_snapshot(
                completed_steps=completed_steps,
                strategy=strategy,
                plan=plan,
                model_calls=model_calls,
                tool_rounds=tool_rounds,
                response_text=response_text,
                provider_used=provider_used,
                model_used=model_used,
                tool_events=tool_events,
            )
            self._emit_checkpoint(
                checkpoint,
                stage="step_completed",
                step=STEP_REASONING,
                message="Reasoning step completed.",
                resume_state=snapshot,
            )
        else:
            if not response_text:
                self._emit_checkpoint(
                    checkpoint,
                    stage="step_resume_fallback",
                    step=STEP_REASONING,
                    message="Checkpoint missing reasoning payload, recomputing reasoning.",
                )
                response_text, provider_used, model_used, model_calls, tool_rounds = self._reason_with_optional_tools(
                    messages=messages,
                    agent=agent,
                    tool_events=tool_events,
                    user_id=user_id,
                    session_id=session_id,
                    checkpoint=checkpoint,
                    model_calls=model_calls,
                    tool_rounds=tool_rounds,
                    started=started,
                    run_deadline_monotonic=run_deadline_monotonic,
                )
            self._emit_checkpoint(
                checkpoint,
                stage="step_resumed",
                step=STEP_REASONING,
                message="Reasoning step resumed from checkpoint.",
                model_calls=model_calls,
                tool_rounds=tool_rounds,
            )

        if STEP_PERSIST not in completed_steps:
            self._check_runtime(started=started, run_deadline_monotonic=run_deadline_monotonic)
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
            completed_steps.add(STEP_PERSIST)
            snapshot = self._build_resume_snapshot(
                completed_steps=completed_steps,
                strategy=strategy,
                plan=plan,
                model_calls=model_calls,
                tool_rounds=tool_rounds,
                response_text=response_text,
                provider_used=provider_used,
                model_used=model_used,
                tool_events=tool_events,
            )
            self._emit_checkpoint(
                checkpoint,
                stage="step_completed",
                step=STEP_PERSIST,
                message="Persist step completed.",
                resume_state=snapshot,
            )
        else:
            self._emit_checkpoint(
                checkpoint,
                stage="step_resumed",
                step=STEP_PERSIST,
                message="Persist step already completed in previous attempt.",
            )

        duration_ms = round((time.monotonic() - started) * 1000.0, 2)
        self._emit_checkpoint(
            checkpoint,
            stage="memory_updated",
            message="Assistant response stored in memory layers.",
            duration_ms=duration_ms,
        )

        return {
            "agent_id": agent.id,
            "session_id": session_id,
            "strategy": strategy,
            "plan": plan,
            "provider": provider_used,
            "model": model_used,
            "tools": tool_events,
            "response": response_text,
            "metrics": {
                "model_calls": model_calls,
                "tool_rounds": tool_rounds,
                "duration_ms": duration_ms,
            },
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
        model_calls: int = 0,
        tool_rounds: int = 0,
        started: float | None = None,
        run_deadline_monotonic: float | None = None,
    ) -> tuple[str, str, str, int, int]:
        allowed_tools = [name for name in agent.tools if self.tool_registry.get(name) is not None]

        reasoning_messages = list(messages)
        if allowed_tools:
            reasoning_messages.append(
                {
                    "role": "system",
                    "content": self.tool_executor.render_tool_instruction(allowed_tools),
                }
            )
        self._check_prompt_size(reasoning_messages)
        self._check_runtime(started=started, run_deadline_monotonic=run_deadline_monotonic)

        first, model_calls = self._chat_with_limits(
            messages=reasoning_messages,
            model=agent.model,
            session_id=session_id,
            model_calls=model_calls,
            started=started,
            run_deadline_monotonic=run_deadline_monotonic,
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
            model_calls=model_calls,
        )

        if not allowed_tools:
            return response_text, provider_used, model_used, model_calls, tool_rounds

        for attempt in range(1, self.max_tool_rounds + 1):
            self._check_runtime(started=started, run_deadline_monotonic=run_deadline_monotonic)
            parsed = self.tool_executor.parse_tool_call(response_text)
            if not parsed:
                self._emit_checkpoint(
                    checkpoint,
                    stage="tool_loop_done",
                    message="No tool call requested by model.",
                    attempt=attempt,
                )
                break

            tool_rounds += 1
            tool_name = str(parsed["name"])
            arguments = parsed["arguments"]
            self._emit_checkpoint(
                checkpoint,
                stage="tool_call_started",
                message=f"Tool call started: {tool_name}",
                tool=tool_name,
                attempt=attempt,
                tool_round=tool_rounds,
            )
            event: dict[str, Any] = {
                "attempt": attempt,
                "tool_round": tool_rounds,
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
            self._check_prompt_size(reasoning_messages)

            followup, model_calls = self._chat_with_limits(
                messages=reasoning_messages,
                model=agent.model,
                session_id=session_id,
                model_calls=model_calls,
                started=started,
                run_deadline_monotonic=run_deadline_monotonic,
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
                model_calls=model_calls,
            )

        if self.tool_executor.parse_tool_call(response_text):
            raise TaskGuardrailError(
                f"Tool round limit exceeded (max={self.max_tool_rounds})."
            )

        return response_text, provider_used, model_used, model_calls, tool_rounds

    def _chat_with_limits(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None,
        session_id: str | None,
        model_calls: int,
        started: float | None,
        run_deadline_monotonic: float | None,
    ) -> tuple[dict[str, Any], int]:
        if model_calls >= self.max_model_calls:
            raise TaskGuardrailError(
                f"Model call limit exceeded (max={self.max_model_calls})."
            )

        self._check_runtime(started=started, run_deadline_monotonic=run_deadline_monotonic)
        self._check_prompt_size(messages)

        call_started = time.monotonic()
        response = self.model_manager.chat(
            messages=messages,
            model=model,
            session_id=session_id,
        )
        model_calls += 1

        if (time.monotonic() - call_started) > self.max_duration_sec:
            raise TaskTimeoutError(
                "Single model call exceeded task max duration guardrail."
            )

        self._check_runtime(started=started, run_deadline_monotonic=run_deadline_monotonic)
        return response, model_calls

    def _check_runtime(
        self,
        *,
        started: float | None,
        run_deadline_monotonic: float | None,
    ) -> None:
        now = time.monotonic()
        if started is not None and (now - started) > self.max_duration_sec:
            raise TaskTimeoutError(
                f"Task duration exceeded limit ({self.max_duration_sec:.2f}s)."
            )
        if run_deadline_monotonic is not None and now > run_deadline_monotonic:
            raise TaskTimeoutError("Run attempt timeout exceeded.")

    def _check_message_size(self, text: str) -> None:
        if len(text or "") > self.max_prompt_chars:
            raise TaskGuardrailError(
                f"Input message is too large ({len(text)} chars, max {self.max_prompt_chars})."
            )

    def _check_prompt_size(self, messages: list[dict[str, Any]]) -> None:
        total_chars = self._estimate_prompt_chars(messages)
        if total_chars > self.max_prompt_chars:
            raise TaskGuardrailError(
                f"Prompt size exceeds limit ({total_chars} chars, max {self.max_prompt_chars})."
            )

    @staticmethod
    def _estimate_prompt_chars(messages: list[dict[str, Any]]) -> int:
        total = 0
        for item in messages:
            content = item.get("content")
            if isinstance(content, str):
                total += len(content)
            elif content is not None:
                total += len(str(content))
        return total

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

    def _normalize_resume_state(self, resume_state: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(resume_state, dict):
            return {}
        raw_steps = resume_state.get("completed_steps")
        completed_steps: list[str] = []
        if isinstance(raw_steps, list):
            for item in raw_steps:
                step = str(item).strip()
                if step in {STEP_PREPARE_CONTEXT, STEP_REASONING, STEP_PERSIST} and step not in completed_steps:
                    completed_steps.append(step)

        normalized: dict[str, Any] = {
            "completed_steps": completed_steps,
        }
        for key in (
            "strategy",
            "plan",
            "model_calls",
            "tool_rounds",
            "response_text",
            "provider",
            "model",
            "tool_events",
        ):
            if key in resume_state:
                normalized[key] = resume_state[key]
        return normalized

    @staticmethod
    def _build_resume_snapshot(
        *,
        completed_steps: set[str],
        strategy: str | None,
        plan: list[dict[str, Any]],
        model_calls: int,
        tool_rounds: int,
        response_text: str,
        provider_used: str,
        model_used: str,
        tool_events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "version": 1,
            "completed_steps": sorted(completed_steps),
            "strategy": strategy,
            "plan": plan,
            "model_calls": max(0, int(model_calls)),
            "tool_rounds": max(0, int(tool_rounds)),
            "response_text": response_text,
            "provider": provider_used,
            "model": model_used,
            "tool_events": tool_events,
        }

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
