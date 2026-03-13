from __future__ import annotations

import json
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
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

ISSUE_PLANNED = "planned"
ISSUE_RUNNING = "running"
ISSUE_BLOCKED = "blocked"
ISSUE_DONE = "done"
ISSUE_FAILED = "failed"
ISSUE_STATUSES = {ISSUE_PLANNED, ISSUE_RUNNING, ISSUE_BLOCKED, ISSUE_DONE, ISSUE_FAILED}


class TaskGuardrailError(RuntimeError):
    pass


class TaskTimeoutError(RuntimeError):
    pass


class TaskBudgetError(TaskGuardrailError):
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
        verifier_enabled: bool = True,
        verifier_max_repair_attempts: int = 1,
        verifier_min_response_chars: int = 8,
        issue_parallel_workers: int = 2,
        issue_timeout_sec: float = 15.0,
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
        self.verifier_enabled = bool(verifier_enabled)
        self.verifier_max_repair_attempts = max(0, int(verifier_max_repair_attempts))
        self.verifier_min_response_chars = max(1, int(verifier_min_response_chars))
        self.issue_parallel_workers = max(1, int(issue_parallel_workers))
        self.issue_timeout_sec = max(0.01, float(issue_timeout_sec))

    def execute(
        self,
        agent: Agent,
        user_id: str,
        session_id: str | None,
        user_message: str,
        checkpoint: CheckpointWriter | None = None,
        run_deadline_monotonic: float | None = None,
        resume_state: dict[str, Any] | None = None,
        run_budget: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        run_budget_limits = self._normalize_run_budget(run_budget)
        budget_duration_sec = run_budget_limits.get("max_duration_sec")
        if budget_duration_sec is not None and budget_duration_sec > 0:
            budget_deadline = started + budget_duration_sec
            if run_deadline_monotonic is None:
                run_deadline_monotonic = budget_deadline
            else:
                run_deadline_monotonic = min(run_deadline_monotonic, budget_deadline)
        self._check_runtime(started=started, run_deadline_monotonic=run_deadline_monotonic)
        self._check_message_size(user_message)

        state = self._normalize_resume_state(resume_state)
        completed_steps = set(state.get("completed_steps", []))
        issue_states = self._normalize_issue_states(state.get("issues"))
        self._ensure_issue(
            issue_states=issue_states,
            issue_id=STEP_PREPARE_CONTEXT,
            title="Prepare context",
            issue_order=10,
            depends_on=[],
        )
        self._ensure_issue(
            issue_states=issue_states,
            issue_id=STEP_REASONING,
            title="Reasoning",
            issue_order=20,
            depends_on=[STEP_PREPARE_CONTEXT],
        )
        self._ensure_issue(
            issue_states=issue_states,
            issue_id=STEP_PERSIST,
            title="Persist memory",
            issue_order=30,
            depends_on=[STEP_REASONING],
        )

        used_tokens_baseline = int(run_budget_limits.get("used_tokens", 0))
        used_tool_calls_baseline = int(run_budget_limits.get("used_tool_calls", 0))
        used_tool_errors_baseline = int(run_budget_limits.get("used_tool_errors", 0))

        strategy = str(state.get("strategy", "")).strip() or None
        plan: list[dict[str, Any]] = []
        if isinstance(state.get("plan"), list):
            plan = [item for item in state["plan"] if isinstance(item, dict)]

        model_calls = int(state.get("model_calls", 0))
        tool_rounds = max(used_tool_calls_baseline, int(state.get("tool_rounds", used_tool_calls_baseline)))
        tool_errors = max(used_tool_errors_baseline, int(state.get("tool_errors", used_tool_errors_baseline)))
        estimated_tokens = max(used_tokens_baseline, int(state.get("estimated_tokens", used_tokens_baseline)))
        response_text = str(state.get("response_text", "")).strip() if state.get("response_text") is not None else ""
        provider_used = str(state.get("provider", "")).strip() if state.get("provider") is not None else ""
        model_used = str(state.get("model", "")).strip() if state.get("model") is not None else ""

        tool_events: list[dict[str, Any]] = []
        raw_events = state.get("tool_events")
        if isinstance(raw_events, list):
            tool_events = [item for item in raw_events if isinstance(item, dict)]

        tools_available = bool(agent.tools)

        if STEP_PREPARE_CONTEXT not in completed_steps:
            self._set_issue_status(
                issue_states=issue_states,
                issue_id=STEP_PREPARE_CONTEXT,
                status=ISSUE_RUNNING,
                checkpoint=checkpoint,
                attempt=1,
                payload={"message": "Preparation step started."},
            )
            try:
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
            except Exception as exc:
                self._set_issue_status(
                    issue_states=issue_states,
                    issue_id=STEP_PREPARE_CONTEXT,
                    status=ISSUE_FAILED,
                    checkpoint=checkpoint,
                    attempt=1,
                    last_error=str(exc),
                    payload={"error": str(exc)},
                )
                raise

            completed_steps.add(STEP_PREPARE_CONTEXT)
            self._set_issue_status(
                issue_states=issue_states,
                issue_id=STEP_PREPARE_CONTEXT,
                status=ISSUE_DONE,
                checkpoint=checkpoint,
                attempt=1,
                last_error=None,
                payload={"message": "Preparation step completed."},
            )
            snapshot = self._build_resume_snapshot(
                completed_steps=completed_steps,
                strategy=strategy,
                plan=plan,
                model_calls=model_calls,
                tool_rounds=tool_rounds,
                tool_errors=tool_errors,
                estimated_tokens=estimated_tokens,
                response_text=response_text,
                provider_used=provider_used,
                model_used=model_used,
                tool_events=tool_events,
                issues=issue_states,
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
            self._set_issue_status(
                issue_states=issue_states,
                issue_id=STEP_PREPARE_CONTEXT,
                status=ISSUE_DONE,
                checkpoint=checkpoint,
                attempt=1,
                payload={"message": "Preparation step resumed from checkpoint."},
            )
            self._emit_checkpoint(
                checkpoint,
                stage="step_resumed",
                step=STEP_PREPARE_CONTEXT,
                message="Preparation step resumed from checkpoint.",
                completed_steps=sorted(completed_steps),
            )

        plan_issue_ids = self._register_plan_issues(
            issue_states=issue_states,
            plan=plan,
            checkpoint=checkpoint,
        )
        if plan_issue_ids:
            self._execute_plan_issues(
                plan=plan,
                plan_issue_ids=plan_issue_ids,
                issue_states=issue_states,
                completed_steps=completed_steps,
                tools_available=tools_available,
                checkpoint=checkpoint,
                run_deadline_monotonic=run_deadline_monotonic,
            )

        if STEP_REASONING not in completed_steps:
            self._set_issue_status(
                issue_states=issue_states,
                issue_id=STEP_REASONING,
                status=ISSUE_RUNNING,
                checkpoint=checkpoint,
                attempt=1,
                payload={"message": "Reasoning step started."},
            )
            self._emit_checkpoint(
                checkpoint,
                stage="reasoning_started",
                message="LLM reasoning started.",
                tools_allowed=agent.tools,
            )
            try:
                (
                    response_text,
                    provider_used,
                    model_used,
                    model_calls,
                    tool_rounds,
                    estimated_tokens,
                    tool_errors,
                ) = self._reason_with_optional_tools(
                    messages=messages,
                    agent=agent,
                    tool_events=tool_events,
                    user_id=user_id,
                    session_id=session_id,
                    checkpoint=checkpoint,
                    model_calls=model_calls,
                    tool_rounds=tool_rounds,
                    tool_errors=tool_errors,
                    estimated_tokens=estimated_tokens,
                    started=started,
                    run_deadline_monotonic=run_deadline_monotonic,
                    run_budget=run_budget_limits,
                )
            except Exception as exc:
                self._set_issue_status(
                    issue_states=issue_states,
                    issue_id=STEP_REASONING,
                    status=ISSUE_FAILED,
                    checkpoint=checkpoint,
                    attempt=1,
                    last_error=str(exc),
                    payload={"error": str(exc)},
                )
                raise
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
            self._set_issue_status(
                issue_states=issue_states,
                issue_id=STEP_REASONING,
                status=ISSUE_DONE,
                checkpoint=checkpoint,
                attempt=1,
                last_error=None,
                payload={"message": "Reasoning step completed."},
            )
            snapshot = self._build_resume_snapshot(
                completed_steps=completed_steps,
                strategy=strategy,
                plan=plan,
                model_calls=model_calls,
                tool_rounds=tool_rounds,
                tool_errors=tool_errors,
                estimated_tokens=estimated_tokens,
                response_text=response_text,
                provider_used=provider_used,
                model_used=model_used,
                tool_events=tool_events,
                issues=issue_states,
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
                (
                    response_text,
                    provider_used,
                    model_used,
                    model_calls,
                    tool_rounds,
                    estimated_tokens,
                    tool_errors,
                ) = self._reason_with_optional_tools(
                    messages=messages,
                    agent=agent,
                    tool_events=tool_events,
                    user_id=user_id,
                    session_id=session_id,
                    checkpoint=checkpoint,
                    model_calls=model_calls,
                    tool_rounds=tool_rounds,
                    tool_errors=tool_errors,
                    estimated_tokens=estimated_tokens,
                    started=started,
                    run_deadline_monotonic=run_deadline_monotonic,
                    run_budget=run_budget_limits,
                )
            self._emit_checkpoint(
                checkpoint,
                stage="step_resumed",
                step=STEP_REASONING,
                message="Reasoning step resumed from checkpoint.",
                model_calls=model_calls,
                tool_rounds=tool_rounds,
            )
            self._set_issue_status(
                issue_states=issue_states,
                issue_id=STEP_REASONING,
                status=ISSUE_DONE,
                checkpoint=checkpoint,
                attempt=1,
                payload={"message": "Reasoning step resumed from checkpoint."},
            )

        response_text, provider_used, model_used, model_calls, estimated_tokens = self._verify_and_repair_response(
            messages=messages,
            agent=agent,
            session_id=session_id,
            response_text=response_text,
            provider_used=provider_used,
            model_used=model_used,
            tool_events=tool_events,
            checkpoint=checkpoint,
            model_calls=model_calls,
            estimated_tokens=estimated_tokens,
            started=started,
            run_deadline_monotonic=run_deadline_monotonic,
            run_budget=run_budget_limits,
        )

        if STEP_PERSIST not in completed_steps:
            self._set_issue_status(
                issue_states=issue_states,
                issue_id=STEP_PERSIST,
                status=ISSUE_RUNNING,
                checkpoint=checkpoint,
                attempt=1,
                payload={"message": "Persist step started."},
            )
            self._check_runtime(started=started, run_deadline_monotonic=run_deadline_monotonic)
            try:
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
            except Exception as exc:
                self._set_issue_status(
                    issue_states=issue_states,
                    issue_id=STEP_PERSIST,
                    status=ISSUE_FAILED,
                    checkpoint=checkpoint,
                    attempt=1,
                    last_error=str(exc),
                    payload={"error": str(exc)},
                )
                raise
            completed_steps.add(STEP_PERSIST)
            self._set_issue_status(
                issue_states=issue_states,
                issue_id=STEP_PERSIST,
                status=ISSUE_DONE,
                checkpoint=checkpoint,
                attempt=1,
                last_error=None,
                payload={"message": "Persist step completed."},
            )
            snapshot = self._build_resume_snapshot(
                completed_steps=completed_steps,
                strategy=strategy,
                plan=plan,
                model_calls=model_calls,
                tool_rounds=tool_rounds,
                tool_errors=tool_errors,
                estimated_tokens=estimated_tokens,
                response_text=response_text,
                provider_used=provider_used,
                model_used=model_used,
                tool_events=tool_events,
                issues=issue_states,
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
            self._set_issue_status(
                issue_states=issue_states,
                issue_id=STEP_PERSIST,
                status=ISSUE_DONE,
                checkpoint=checkpoint,
                attempt=1,
                payload={"message": "Persist step resumed from checkpoint."},
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
                "tool_calls": tool_rounds,
                "tool_errors": tool_errors,
                "estimated_tokens": estimated_tokens,
                "attempt_count": 1,
                "total_attempt_duration_ms": duration_ms,
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
        tool_errors: int = 0,
        estimated_tokens: int = 0,
        started: float | None = None,
        run_deadline_monotonic: float | None = None,
        run_budget: dict[str, Any] | None = None,
    ) -> tuple[str, str, str, int, int, int, int]:
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

        first, model_calls, estimated_tokens, usage = self._chat_with_limits(
            messages=reasoning_messages,
            model=agent.model,
            session_id=session_id,
            model_calls=model_calls,
            estimated_tokens=estimated_tokens,
            max_tokens_budget=self._budget_limit_int(run_budget, "max_tokens"),
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
            estimated_tokens_total=estimated_tokens,
            estimated_tokens_delta=usage["delta_tokens_est"],
            estimated_prompt_tokens=usage["prompt_tokens_est"],
            estimated_completion_tokens=usage["completion_tokens_est"],
        )

        if not allowed_tools:
            return response_text, provider_used, model_used, model_calls, tool_rounds, estimated_tokens, tool_errors

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

            max_tool_calls_budget = self._budget_limit_int(run_budget, "max_tool_calls")
            if max_tool_calls_budget is not None and (tool_rounds + 1) > max_tool_calls_budget:
                raise TaskBudgetError(
                    f"Run tool-call budget exceeded ({tool_rounds + 1} > {max_tool_calls_budget})."
                )

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
                tool_errors += 1
                tool_events.append(event)
                self._emit_checkpoint(
                    checkpoint,
                    stage="tool_call_blocked",
                    message=f"Tool is not allowed: {tool_name}",
                    tool=tool_name,
                    attempt=attempt,
                    estimated_tokens_total=estimated_tokens,
                    tool_errors=tool_errors,
                )
                self._enforce_tool_error_budget(
                    tool_errors=tool_errors,
                    max_tool_errors_budget=self._budget_limit_int(run_budget, "max_tool_errors"),
                )
                break

            validation_error = self._validate_tool_arguments(tool_name=tool_name, arguments=arguments)
            if validation_error is not None:
                tool_result = {
                    "tool": tool_name,
                    "error": validation_error,
                    "contract_error": True,
                }
                event["status"] = "invalid_arguments"
                event["error"] = validation_error
                event["duration_ms"] = 0.0
                tool_errors += 1
                tool_events.append(event)
                self._emit_checkpoint(
                    checkpoint,
                    stage="tool_call_invalid",
                    message=f"Tool call arguments rejected: {tool_name}",
                    tool=tool_name,
                    attempt=attempt,
                    error=validation_error,
                    tool_errors=tool_errors,
                )
                self._enforce_tool_error_budget(
                    tool_errors=tool_errors,
                    max_tool_errors_budget=self._budget_limit_int(run_budget, "max_tool_errors"),
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
                        "content": "Tool call was rejected by deterministic contract validation. Correct arguments or continue without tool.",
                    }
                )
                self._check_prompt_size(reasoning_messages)

                followup, model_calls, estimated_tokens, usage = self._chat_with_limits(
                    messages=reasoning_messages,
                    model=agent.model,
                    session_id=session_id,
                    model_calls=model_calls,
                    estimated_tokens=estimated_tokens,
                    max_tokens_budget=self._budget_limit_int(run_budget, "max_tokens"),
                    started=started,
                    run_deadline_monotonic=run_deadline_monotonic,
                )
                response_text = str(followup.get("content", "")).strip()
                provider_used = str(followup.get("provider", provider_used))
                model_used = str(followup.get("model", model_used))
                self._emit_checkpoint(
                    checkpoint,
                    stage="llm_followup_response",
                    message="Received follow-up response after invalid tool arguments.",
                    provider=provider_used,
                    model=model_used,
                    attempt=attempt,
                    preview=response_text[:240],
                    model_calls=model_calls,
                    estimated_tokens_total=estimated_tokens,
                    estimated_tokens_delta=usage["delta_tokens_est"],
                    estimated_prompt_tokens=usage["prompt_tokens_est"],
                    estimated_completion_tokens=usage["completion_tokens_est"],
                )
                continue

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
                tool_errors += 1
                self._emit_checkpoint(
                    checkpoint,
                    stage="tool_call_permission_required",
                    message=f"Permission required for tool: {tool_name}",
                    tool=tool_name,
                    attempt=attempt,
                    permission_prompt_id=exc.prompt_id,
                    tool_errors=tool_errors,
                )
            except ToolExecutionError as exc:
                tool_result = {
                    "tool": tool_name,
                    "error": str(exc),
                }
                event["status"] = "failed"
                event["error"] = str(exc)
                tool_errors += 1
                self._emit_checkpoint(
                    checkpoint,
                    stage="tool_call_failed",
                    message=f"Tool execution failed: {tool_name}",
                    tool=tool_name,
                    attempt=attempt,
                    error=str(exc),
                    tool_errors=tool_errors,
                )

            event["duration_ms"] = round((time.perf_counter() - started_at) * 1000.0, 2)
            tool_events.append(event)
            if str(event.get("status", "")).strip().lower() in {
                "failed",
                "invalid_arguments",
                "blocked",
                "permission_required",
            }:
                self._enforce_tool_error_budget(
                    tool_errors=tool_errors,
                    max_tool_errors_budget=self._budget_limit_int(run_budget, "max_tool_errors"),
                )
            self._emit_checkpoint(
                checkpoint,
                stage="tool_call_finished",
                message=f"Tool call finished: {tool_name}",
                tool=tool_name,
                attempt=attempt,
                status=event.get("status"),
                duration_ms=event["duration_ms"],
                tool_errors=tool_errors,
                estimated_tokens_total=estimated_tokens,
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

            followup, model_calls, estimated_tokens, usage = self._chat_with_limits(
                messages=reasoning_messages,
                model=agent.model,
                session_id=session_id,
                model_calls=model_calls,
                estimated_tokens=estimated_tokens,
                max_tokens_budget=self._budget_limit_int(run_budget, "max_tokens"),
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
                estimated_tokens_total=estimated_tokens,
                estimated_tokens_delta=usage["delta_tokens_est"],
                estimated_prompt_tokens=usage["prompt_tokens_est"],
                estimated_completion_tokens=usage["completion_tokens_est"],
            )

        if self.tool_executor.parse_tool_call(response_text):
            raise TaskGuardrailError(
                f"Tool round limit exceeded (max={self.max_tool_rounds})."
            )

        return (
            response_text,
            provider_used,
            model_used,
            model_calls,
            tool_rounds,
            estimated_tokens,
            tool_errors,
        )

    def _verify_and_repair_response(
        self,
        *,
        messages: list[dict[str, Any]],
        agent: Agent,
        session_id: str | None,
        response_text: str,
        provider_used: str,
        model_used: str,
        tool_events: list[dict[str, Any]],
        checkpoint: CheckpointWriter | None,
        model_calls: int,
        estimated_tokens: int,
        started: float | None,
        run_deadline_monotonic: float | None,
        run_budget: dict[str, Any] | None,
    ) -> tuple[str, str, str, int, int]:
        if not self.verifier_enabled:
            return response_text, provider_used, model_used, model_calls, estimated_tokens

        issues = self._collect_verification_issues(response_text=response_text, tool_events=tool_events)
        self._emit_checkpoint(
            checkpoint,
            stage="verification_started",
            message="Response verification started.",
            issues=issues,
            issues_count=len(issues),
        )
        if not issues:
            self._emit_checkpoint(
                checkpoint,
                stage="verification_passed",
                message="Response verification passed.",
            )
            return response_text, provider_used, model_used, model_calls, estimated_tokens

        repaired_text = response_text
        repaired_provider = provider_used
        repaired_model = model_used
        remaining_issues = list(issues)

        for attempt in range(1, self.verifier_max_repair_attempts + 1):
            if model_calls >= self.max_model_calls:
                break

            self._check_runtime(started=started, run_deadline_monotonic=run_deadline_monotonic)
            verification_prompt = (
                "Your previous answer did not pass runtime verification.\n"
                f"Issues: {', '.join(remaining_issues)}.\n"
                "Provide a corrected final answer for the user.\n"
                "Do not emit <tool_call> and do not request extra steps.\n"
                "Return only final user-facing answer."
            )
            verify_messages = list(messages)
            verify_messages.append({"role": "assistant", "content": repaired_text})
            verify_messages.append({"role": "system", "content": verification_prompt})
            self._check_prompt_size(verify_messages)

            followup, model_calls, estimated_tokens, usage = self._chat_with_limits(
                messages=verify_messages,
                model=agent.model,
                session_id=session_id,
                model_calls=model_calls,
                estimated_tokens=estimated_tokens,
                max_tokens_budget=self._budget_limit_int(run_budget, "max_tokens"),
                started=started,
                run_deadline_monotonic=run_deadline_monotonic,
            )
            repaired_text = str(followup.get("content", "")).strip()
            repaired_provider = str(followup.get("provider", repaired_provider))
            repaired_model = str(followup.get("model", repaired_model))
            remaining_issues = self._collect_verification_issues(
                response_text=repaired_text,
                tool_events=tool_events,
            )
            self._emit_checkpoint(
                checkpoint,
                stage="verification_repair_attempt",
                message="Verification repair attempt completed.",
                attempt=attempt,
                issues_after=remaining_issues,
                model_calls=model_calls,
                preview=repaired_text[:240],
                estimated_tokens_total=estimated_tokens,
                estimated_tokens_delta=usage["delta_tokens_est"],
            )
            if not remaining_issues:
                self._emit_checkpoint(
                    checkpoint,
                    stage="verification_repair_succeeded",
                    message="Verification repair succeeded.",
                    attempt=attempt,
                )
                return repaired_text, repaired_provider, repaired_model, model_calls, estimated_tokens

        critical = {"response_empty", "unfinished_tool_call"}
        critical_issues = [item for item in remaining_issues if item in critical]
        if critical_issues:
            raise TaskGuardrailError(
                "Verification failed for critical response issues: " + ", ".join(critical_issues)
            )

        self._emit_checkpoint(
            checkpoint,
            stage="verification_warning",
            message="Response has non-critical verification issues; returning best effort response.",
            issues=remaining_issues,
            issues_count=len(remaining_issues),
        )
        return repaired_text, repaired_provider, repaired_model, model_calls, estimated_tokens

    def _collect_verification_issues(
        self,
        *,
        response_text: str,
        tool_events: list[dict[str, Any]],
    ) -> list[str]:
        issues: list[str] = []
        trimmed = (response_text or "").strip()
        if not trimmed:
            issues.append("response_empty")
        elif len(trimmed) < self.verifier_min_response_chars:
            issues.append("response_too_short")

        if self.tool_executor.parse_tool_call(trimmed) is not None:
            issues.append("unfinished_tool_call")

        failed_tools = 0
        for item in tool_events:
            status = str(item.get("status", "")).strip().lower()
            if status in {"failed", "invalid_arguments", "blocked"}:
                failed_tools += 1
        if failed_tools > 0:
            lower = trimmed.lower()
            acknowledges_failure = any(
                marker in lower
                for marker in ("cannot", "can't", "failed", "error", "unable", "not possible")
            )
            if not acknowledges_failure:
                issues.append("tool_failure_not_acknowledged")

        return issues

    def _validate_tool_arguments(self, *, tool_name: str, arguments: dict[str, Any]) -> str | None:
        tool = self.tool_registry.get(tool_name)
        if tool is None:
            return f"Unknown tool: {tool_name}"

        try:
            encoded = json.dumps(arguments, ensure_ascii=False)
        except Exception:
            return "Arguments must be JSON-serializable."
        if len(encoded) > 20000:
            return f"Arguments payload is too large ({len(encoded)} > 20000 chars)."

        schema = tool.input_schema if isinstance(tool.input_schema, dict) else {}
        required = schema.get("required", [])
        if isinstance(required, list):
            for field in required:
                key = str(field)
                if key not in arguments:
                    return f"Missing required argument '{key}' for tool '{tool_name}'."

        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for key, value in arguments.items():
                spec = properties.get(key)
                if not isinstance(spec, dict):
                    continue
                enum_values = spec.get("enum")
                if isinstance(enum_values, list) and enum_values and value not in enum_values:
                    return f"Argument '{key}' has invalid value '{value}'. Allowed: {enum_values}."
                expected_type = spec.get("type")
                if isinstance(expected_type, str) and not self._matches_json_type(expected_type, value):
                    return (
                        f"Argument '{key}' must be type '{expected_type}', "
                        f"got '{type(value).__name__}'."
                    )

        if schema.get("additionalProperties") is False and isinstance(properties, dict):
            unknown = sorted(key for key in arguments.keys() if key not in properties)
            if unknown:
                return "Unknown arguments are not allowed: " + ", ".join(unknown)
        return None

    @staticmethod
    def _matches_json_type(expected: str, value: Any) -> bool:
        normalized = expected.strip().lower()
        if normalized == "string":
            return isinstance(value, str)
        if normalized == "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        if normalized == "number":
            return (isinstance(value, int) and not isinstance(value, bool)) or isinstance(value, float)
        if normalized == "boolean":
            return isinstance(value, bool)
        if normalized == "object":
            return isinstance(value, dict)
        if normalized == "array":
            return isinstance(value, list)
        if normalized == "null":
            return value is None
        return True

    def _chat_with_limits(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None,
        session_id: str | None,
        model_calls: int,
        estimated_tokens: int,
        max_tokens_budget: int | None,
        started: float | None,
        run_deadline_monotonic: float | None,
    ) -> tuple[dict[str, Any], int, int, dict[str, int]]:
        if model_calls >= self.max_model_calls:
            raise TaskGuardrailError(
                f"Model call limit exceeded (max={self.max_model_calls})."
            )
        if max_tokens_budget is not None and estimated_tokens >= max_tokens_budget:
            raise TaskBudgetError(
                f"Run token budget exceeded ({estimated_tokens} >= {max_tokens_budget})."
            )

        self._check_runtime(started=started, run_deadline_monotonic=run_deadline_monotonic)
        self._check_prompt_size(messages)

        prompt_tokens_est = self._estimate_prompt_tokens(messages)
        call_started = time.monotonic()
        response = self.model_manager.chat(
            messages=messages,
            model=model,
            session_id=session_id,
        )
        model_calls += 1
        completion_tokens_est = self._estimate_text_tokens(str(response.get("content", "")))
        delta_tokens_est = max(0, prompt_tokens_est + completion_tokens_est)
        estimated_tokens_total = max(0, int(estimated_tokens)) + delta_tokens_est
        if max_tokens_budget is not None and estimated_tokens_total > max_tokens_budget:
            raise TaskBudgetError(
                f"Run token budget exceeded ({estimated_tokens_total} > {max_tokens_budget})."
            )

        if (time.monotonic() - call_started) > self.max_duration_sec:
            raise TaskTimeoutError(
                "Single model call exceeded task max duration guardrail."
            )

        self._check_runtime(started=started, run_deadline_monotonic=run_deadline_monotonic)
        usage = {
            "prompt_tokens_est": prompt_tokens_est,
            "completion_tokens_est": completion_tokens_est,
            "delta_tokens_est": delta_tokens_est,
            "total_tokens_est": estimated_tokens_total,
        }
        return response, model_calls, estimated_tokens_total, usage

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

    @classmethod
    def _estimate_prompt_tokens(cls, messages: list[dict[str, Any]]) -> int:
        return cls._estimate_text_tokens_from_chars(cls._estimate_prompt_chars(messages))

    @classmethod
    def _estimate_text_tokens(cls, text: str) -> int:
        return cls._estimate_text_tokens_from_chars(len(text or ""))

    @staticmethod
    def _estimate_text_tokens_from_chars(char_count: int) -> int:
        safe_chars = max(0, int(char_count))
        if safe_chars == 0:
            return 0
        return max(1, (safe_chars + 3) // 4)

    @staticmethod
    def _normalize_run_budget(run_budget: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(run_budget, dict):
            return {
                "max_tokens": None,
                "max_duration_sec": None,
                "max_tool_calls": None,
                "max_tool_errors": None,
                "used_tokens": 0,
                "used_tool_calls": 0,
                "used_tool_errors": 0,
            }
        return {
            "max_tokens": TaskExecutor._safe_int_or_none(run_budget.get("max_tokens")),
            "max_duration_sec": TaskExecutor._safe_float_or_none(run_budget.get("max_duration_sec")),
            "max_tool_calls": TaskExecutor._safe_int_or_none(run_budget.get("max_tool_calls")),
            "max_tool_errors": TaskExecutor._safe_int_or_none(run_budget.get("max_tool_errors")),
            "used_tokens": max(0, TaskExecutor._safe_int_or_none(run_budget.get("used_tokens")) or 0),
            "used_tool_calls": max(0, TaskExecutor._safe_int_or_none(run_budget.get("used_tool_calls")) or 0),
            "used_tool_errors": max(0, TaskExecutor._safe_int_or_none(run_budget.get("used_tool_errors")) or 0),
        }

    @staticmethod
    def _budget_limit_int(run_budget: dict[str, Any] | None, key: str) -> int | None:
        if not isinstance(run_budget, dict):
            return None
        value = run_budget.get(key)
        if value is None:
            return None
        try:
            parsed = int(value)
        except Exception:
            return None
        return parsed if parsed >= 0 else None

    @staticmethod
    def _enforce_tool_error_budget(*, tool_errors: int, max_tool_errors_budget: int | None) -> None:
        if max_tool_errors_budget is None:
            return
        if tool_errors > max_tool_errors_budget:
            raise TaskBudgetError(
                f"Run tool-error budget exceeded ({tool_errors} > {max_tool_errors_budget})."
            )

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
                if step and step not in completed_steps:
                    completed_steps.append(step)

        normalized: dict[str, Any] = {
            "completed_steps": completed_steps,
        }
        for key in (
            "strategy",
            "plan",
            "model_calls",
            "tool_rounds",
            "tool_errors",
            "estimated_tokens",
            "response_text",
            "provider",
            "model",
            "tool_events",
            "issues",
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
        tool_errors: int,
        estimated_tokens: int,
        response_text: str,
        provider_used: str,
        model_used: str,
        tool_events: list[dict[str, Any]],
        issues: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "version": 2,
            "completed_steps": sorted(completed_steps),
            "strategy": strategy,
            "plan": plan,
            "model_calls": max(0, int(model_calls)),
            "tool_rounds": max(0, int(tool_rounds)),
            "tool_errors": max(0, int(tool_errors)),
            "estimated_tokens": max(0, int(estimated_tokens)),
            "response_text": response_text,
            "provider": provider_used,
            "model": model_used,
            "tool_events": tool_events,
            "issues": issues,
        }

    def _register_plan_issues(
        self,
        *,
        issue_states: dict[str, dict[str, Any]],
        plan: list[dict[str, Any]],
        checkpoint: CheckpointWriter | None,
    ) -> list[str]:
        result: list[str] = []
        index_to_issue_id: dict[int, str] = {}
        for index, item in enumerate(plan, start=1):
            issue_id = self._plan_issue_id(index=index)
            description = str(item.get("description") or f"Plan step {index}").strip() or f"Plan step {index}"
            index_to_issue_id[index] = issue_id
            raw_depends = item.get("depends_on")
            depends_on = self._normalize_plan_dependencies(
                raw=raw_depends,
                index_to_issue_id=index_to_issue_id,
            )
            if STEP_PREPARE_CONTEXT not in depends_on:
                depends_on.insert(0, STEP_PREPARE_CONTEXT)
            created = self._ensure_issue(
                issue_states=issue_states,
                issue_id=issue_id,
                title=description,
                issue_order=100 + index,
                depends_on=depends_on,
                payload={"plan_step": item},
            )
            if created:
                self._emit_checkpoint(
                    checkpoint,
                    stage="issue_registered",
                    message=f"Issue registered: {issue_id}",
                    issue={
                        "id": issue_id,
                        "title": description,
                        "order": 100 + index,
                        "status": ISSUE_PLANNED,
                        "depends_on": depends_on,
                        "payload": {"plan_step": item},
                    },
            )
            result.append(issue_id)

        if result:
            reasoning = issue_states.get(STEP_REASONING)
            if isinstance(reasoning, dict):
                reasoning["depends_on"] = list(result)
            persist = issue_states.get(STEP_PERSIST)
            if isinstance(persist, dict):
                persist["depends_on"] = [STEP_REASONING]
        return result

    def _execute_plan_issues(
        self,
        *,
        plan: list[dict[str, Any]],
        plan_issue_ids: list[str],
        issue_states: dict[str, dict[str, Any]],
        completed_steps: set[str],
        tools_available: bool,
        checkpoint: CheckpointWriter | None,
        run_deadline_monotonic: float | None,
    ) -> None:
        for issue_id in plan_issue_ids:
            if issue_id in completed_steps:
                self._set_issue_status(
                    issue_states=issue_states,
                    issue_id=issue_id,
                    status=ISSUE_DONE,
                    checkpoint=checkpoint,
                    attempt=1,
                    payload={"message": "Plan issue resumed from checkpoint."},
                )

        pending = [issue_id for issue_id in plan_issue_ids if issue_id not in completed_steps]
        if not pending:
            return

        step_by_issue: dict[str, dict[str, Any]] = {}
        for index, issue_id in enumerate(plan_issue_ids, start=1):
            step_by_issue[issue_id] = plan[index - 1] if index - 1 < len(plan) else {}

        active: dict[Future[dict[str, Any]], str] = {}
        with ThreadPoolExecutor(max_workers=self.issue_parallel_workers) as pool:
            while pending or active:
                scheduled_any = False

                while len(active) < self.issue_parallel_workers:
                    ready_issue = self._next_ready_plan_issue(
                        pending=pending,
                        issue_states=issue_states,
                    )
                    if ready_issue is None:
                        break

                    issue = issue_states.get(ready_issue) or {}
                    dependencies = [str(item) for item in issue.get("depends_on", []) if str(item).strip()]
                    blocked_dep = self._first_blocking_dependency(issue_states=issue_states, dependencies=dependencies)
                    if blocked_dep is not None:
                        error_message = f"Dependency {blocked_dep} is not complete."
                        self._set_issue_status(
                            issue_states=issue_states,
                            issue_id=ready_issue,
                            status=ISSUE_BLOCKED,
                            checkpoint=checkpoint,
                            attempt=1,
                            last_error=error_message,
                            payload={
                                "error": error_message,
                                "dependency": blocked_dep,
                            },
                        )
                        pending.remove(ready_issue)
                        continue

                    pending.remove(ready_issue)
                    step_payload = step_by_issue.get(ready_issue, {})
                    issue_deadline = self._compute_issue_deadline(run_deadline_monotonic=run_deadline_monotonic)
                    self._set_issue_status(
                        issue_states=issue_states,
                        issue_id=ready_issue,
                        status=ISSUE_RUNNING,
                        checkpoint=checkpoint,
                        attempt=1,
                        payload={
                            "message": "Plan issue started.",
                            "plan_step": step_payload,
                            "deadline_monotonic": issue_deadline,
                        },
                    )
                    future = pool.submit(
                        self._evaluate_plan_issue,
                        issue_id=ready_issue,
                        step_payload=step_payload,
                        tools_available=tools_available,
                        issue_deadline_monotonic=issue_deadline,
                    )
                    active[future] = ready_issue
                    scheduled_any = True

                if not active:
                    if pending and not scheduled_any:
                        for issue_id in list(pending):
                            error_message = "No schedulable issue (dependency cycle or blocked dependencies)."
                            self._set_issue_status(
                                issue_states=issue_states,
                                issue_id=issue_id,
                                status=ISSUE_BLOCKED,
                                checkpoint=checkpoint,
                                attempt=1,
                                last_error=error_message,
                                payload={"error": error_message},
                            )
                            pending.remove(issue_id)
                        raise TaskGuardrailError("Plan issues cannot be scheduled due to unresolved dependencies.")
                    continue

                done, _ = wait(active.keys(), timeout=0.05, return_when=FIRST_COMPLETED)
                if not done:
                    self._enforce_active_issue_deadlines(
                        active=active,
                        issue_states=issue_states,
                        checkpoint=checkpoint,
                    )
                    continue

                for future in done:
                    issue_id = active.pop(future)
                    issue = issue_states.get(issue_id) or {}
                    issue_payload = issue.get("payload")
                    deadline_monotonic: float | None = None
                    if isinstance(issue_payload, dict):
                        try:
                            deadline_monotonic = float(issue_payload.get("deadline_monotonic"))
                        except Exception:
                            deadline_monotonic = None
                    if deadline_monotonic is not None and time.monotonic() > deadline_monotonic:
                        error_message = f"Issue exceeded deadline ({self.issue_timeout_sec:.2f}s)."
                        self._set_issue_status(
                            issue_states=issue_states,
                            issue_id=issue_id,
                            status=ISSUE_FAILED,
                            checkpoint=checkpoint,
                            attempt=1,
                            last_error=error_message,
                            payload={"error": error_message},
                        )
                        raise TaskTimeoutError(error_message)
                    try:
                        result = future.result()
                    except Exception as exc:
                        self._set_issue_status(
                            issue_states=issue_states,
                            issue_id=issue_id,
                            status=ISSUE_FAILED,
                            checkpoint=checkpoint,
                            attempt=1,
                            last_error=str(exc),
                            payload={"error": str(exc)},
                        )
                        raise

                    status = str(result.get("status") or ISSUE_DONE).strip().lower() or ISSUE_DONE
                    payload = dict(result.get("payload") or {}) if isinstance(result.get("payload"), dict) else {}
                    if status not in ISSUE_STATUSES:
                        status = ISSUE_DONE

                    if status == ISSUE_DONE:
                        completed_steps.add(issue_id)
                        self._emit_checkpoint(
                            checkpoint,
                            stage="plan_step_executed",
                            message=f"Plan issue executed: {issue_id}",
                            issue_id=issue_id,
                            plan_step=step_by_issue.get(issue_id, {}),
                        )
                        self._set_issue_status(
                            issue_states=issue_states,
                            issue_id=issue_id,
                            status=ISSUE_DONE,
                            checkpoint=checkpoint,
                            attempt=1,
                            payload={
                                "message": "Plan issue completed.",
                                **payload,
                            },
                        )
                        continue

                    if status == ISSUE_BLOCKED:
                        reason = str(result.get("reason") or "Plan issue is blocked.")
                        self._set_issue_status(
                            issue_states=issue_states,
                            issue_id=issue_id,
                            status=ISSUE_BLOCKED,
                            checkpoint=checkpoint,
                            attempt=1,
                            last_error=reason,
                            payload={"error": reason, **payload},
                        )
                        raise TaskGuardrailError(reason)

                    if status == ISSUE_FAILED:
                        reason = str(result.get("reason") or "Plan issue failed.")
                        self._set_issue_status(
                            issue_states=issue_states,
                            issue_id=issue_id,
                            status=ISSUE_FAILED,
                            checkpoint=checkpoint,
                            attempt=1,
                            last_error=reason,
                            payload={"error": reason, **payload},
                        )
                        raise TaskGuardrailError(reason)

                    self._set_issue_status(
                        issue_states=issue_states,
                        issue_id=issue_id,
                        status=ISSUE_DONE,
                        checkpoint=checkpoint,
                        attempt=1,
                        payload={"message": "Plan issue completed."},
                    )

    def _normalize_issue_states(self, raw: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(raw, dict):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for key, value in raw.items():
            issue_id = str(key).strip()
            if not issue_id or not isinstance(value, dict):
                continue
            status = str(value.get("status") or ISSUE_PLANNED).strip().lower() or ISSUE_PLANNED
            if status not in ISSUE_STATUSES:
                status = ISSUE_PLANNED
            depends_on_raw = value.get("depends_on")
            depends_on = (
                [str(item) for item in depends_on_raw if str(item).strip()]
                if isinstance(depends_on_raw, list)
                else []
            )
            result[issue_id] = {
                "id": issue_id,
                "title": str(value.get("title") or issue_id).strip() or issue_id,
                "order": max(0, self._safe_int_or_none(value.get("order")) or 0),
                "status": status,
                "depends_on": depends_on,
                "attempt": max(0, self._safe_int_or_none(value.get("attempt")) or 0),
                "last_error": str(value.get("last_error")) if value.get("last_error") is not None else None,
                "payload": dict(value.get("payload") or {}) if isinstance(value.get("payload"), dict) else {},
            }
        return result

    def _next_ready_plan_issue(
        self,
        *,
        pending: list[str],
        issue_states: dict[str, dict[str, Any]],
    ) -> str | None:
        sorted_pending = sorted(
            pending,
            key=lambda issue_id: max(0, int((issue_states.get(issue_id) or {}).get("order", 0))),
        )
        for issue_id in sorted_pending:
            issue = issue_states.get(issue_id) or {}
            dependencies = [str(item) for item in issue.get("depends_on", []) if str(item).strip()]
            if not dependencies:
                return issue_id
            if all(self._dependency_satisfied(issue_states=issue_states, dependency=dep) for dep in dependencies):
                return issue_id
        return None

    @classmethod
    def _normalize_plan_dependencies(
        cls,
        *,
        raw: Any,
        index_to_issue_id: dict[int, str],
    ) -> list[str]:
        if not isinstance(raw, list):
            return []
        result: list[str] = []
        for item in raw:
            dep: str | None = None
            if isinstance(item, int):
                dep = index_to_issue_id.get(max(1, int(item))) or cls._plan_issue_id(index=max(1, int(item)))
            elif isinstance(item, str):
                text = item.strip()
                if not text:
                    continue
                if text.startswith("plan_step:"):
                    dep = text
                else:
                    try:
                        parsed = int(text)
                    except Exception:
                        dep = text
                    else:
                        dep = index_to_issue_id.get(max(1, parsed)) or cls._plan_issue_id(index=max(1, parsed))
            if dep and dep not in result:
                result.append(dep)
        return result

    @staticmethod
    def _dependency_satisfied(*, issue_states: dict[str, dict[str, Any]], dependency: str) -> bool:
        if dependency == STEP_PREPARE_CONTEXT:
            status = str((issue_states.get(dependency) or {}).get("status") or ISSUE_PLANNED).strip().lower()
            return status == ISSUE_DONE
        state = issue_states.get(dependency)
        if not isinstance(state, dict):
            return True
        status = str(state.get("status") or ISSUE_PLANNED).strip().lower()
        return status == ISSUE_DONE

    @staticmethod
    def _first_blocking_dependency(*, issue_states: dict[str, dict[str, Any]], dependencies: list[str]) -> str | None:
        for dependency in dependencies:
            state = issue_states.get(dependency)
            if not isinstance(state, dict):
                continue
            status = str(state.get("status") or ISSUE_PLANNED).strip().lower()
            if status in {ISSUE_FAILED, ISSUE_BLOCKED}:
                return dependency
        return None

    def _compute_issue_deadline(self, *, run_deadline_monotonic: float | None) -> float:
        issue_deadline = time.monotonic() + self.issue_timeout_sec
        if run_deadline_monotonic is None:
            return issue_deadline
        return min(issue_deadline, run_deadline_monotonic)

    def _enforce_active_issue_deadlines(
        self,
        *,
        active: dict[Future[dict[str, Any]], str],
        issue_states: dict[str, dict[str, Any]],
        checkpoint: CheckpointWriter | None,
    ) -> None:
        now = time.monotonic()
        timed_out: list[Future[dict[str, Any]]] = []
        for future, issue_id in active.items():
            issue = issue_states.get(issue_id) or {}
            payload = issue.get("payload")
            if not isinstance(payload, dict):
                continue
            raw_deadline = payload.get("deadline_monotonic")
            try:
                deadline = float(raw_deadline)
            except Exception:
                continue
            if now > deadline:
                timed_out.append(future)

        for future in timed_out:
            issue_id = active.pop(future, None)
            if issue_id is None:
                continue
            future.cancel()
            error_message = f"Issue exceeded deadline ({self.issue_timeout_sec:.2f}s)."
            self._set_issue_status(
                issue_states=issue_states,
                issue_id=issue_id,
                status=ISSUE_FAILED,
                checkpoint=checkpoint,
                attempt=1,
                last_error=error_message,
                payload={"error": error_message},
            )
            raise TaskTimeoutError(error_message)

    def _evaluate_plan_issue(
        self,
        *,
        issue_id: str,
        step_payload: dict[str, Any],
        tools_available: bool,
        issue_deadline_monotonic: float,
    ) -> dict[str, Any]:
        started = time.monotonic()
        if started > issue_deadline_monotonic:
            return {
                "status": ISSUE_FAILED,
                "reason": "Issue deadline already exceeded.",
                "payload": {},
            }

        description = str(step_payload.get("description") or issue_id).strip() or issue_id
        lower = description.lower()
        requires_tools = any(
            token in lower
            for token in (
                "tool",
                "search",
                "fetch",
                "read",
                "write",
                "file",
                "website",
                "url",
                "python",
            )
        )
        if requires_tools and not tools_available:
            return {
                "status": ISSUE_BLOCKED,
                "reason": "Plan step requires tools but agent has no tools configured.",
                "payload": {
                    "requires_tools": True,
                    "description": description,
                },
            }

        if time.monotonic() > issue_deadline_monotonic:
            return {
                "status": ISSUE_FAILED,
                "reason": "Issue deadline exceeded during execution.",
                "payload": {
                    "description": description,
                },
            }

        return {
            "status": ISSUE_DONE,
            "payload": {
                "description": description,
                "requires_tools": requires_tools,
                "duration_ms": round((time.monotonic() - started) * 1000.0, 3),
            },
        }

    def _ensure_issue(
        self,
        *,
        issue_states: dict[str, dict[str, Any]],
        issue_id: str,
        title: str,
        issue_order: int,
        depends_on: list[str],
        payload: dict[str, Any] | None = None,
    ) -> bool:
        if issue_id in issue_states:
            issue = issue_states[issue_id]
            issue.setdefault("id", issue_id)
            issue.setdefault("title", title)
            issue.setdefault("order", issue_order)
            issue.setdefault("status", ISSUE_PLANNED)
            issue.setdefault("depends_on", list(depends_on))
            issue.setdefault("attempt", 0)
            issue.setdefault("last_error", None)
            issue.setdefault("payload", dict(payload or {}))
            return False
        issue_states[issue_id] = {
            "id": issue_id,
            "title": title,
            "order": max(0, int(issue_order)),
            "status": ISSUE_PLANNED,
            "depends_on": list(depends_on),
            "attempt": 0,
            "last_error": None,
            "payload": dict(payload or {}),
        }
        return True

    def _set_issue_status(
        self,
        *,
        issue_states: dict[str, dict[str, Any]],
        issue_id: str,
        status: str,
        checkpoint: CheckpointWriter | None,
        attempt: int,
        payload: dict[str, Any] | None = None,
        last_error: str | None = None,
    ) -> None:
        issue = issue_states.get(issue_id)
        if not isinstance(issue, dict):
            title = issue_id.replace("_", " ").strip().title() or "Issue"
            self._ensure_issue(
                issue_states=issue_states,
                issue_id=issue_id,
                title=title,
                issue_order=1000,
                depends_on=[],
            )
            issue = issue_states[issue_id]

        normalized_status = str(status or ISSUE_PLANNED).strip().lower() or ISSUE_PLANNED
        if normalized_status not in ISSUE_STATUSES:
            normalized_status = ISSUE_PLANNED
        issue["status"] = normalized_status
        issue["attempt"] = max(int(issue.get("attempt", 0)), int(attempt))
        if last_error is not None:
            issue["last_error"] = str(last_error)
        elif normalized_status == ISSUE_DONE:
            issue["last_error"] = None
        merged_payload: dict[str, Any] = {}
        current_payload = issue.get("payload")
        if isinstance(current_payload, dict):
            merged_payload.update(current_payload)
        if payload is not None:
            merged_payload.update(dict(payload))
        issue["payload"] = merged_payload

        self._emit_checkpoint(
            checkpoint,
            stage="issue_state",
            message=f"Issue state updated: {issue_id} -> {normalized_status}",
            issue={
                "id": issue_id,
                "title": str(issue.get("title") or issue_id),
                "order": max(0, int(issue.get("order", 0))),
                "status": normalized_status,
                "depends_on": list(issue.get("depends_on", [])),
                "attempt": int(issue.get("attempt", 0)),
                "last_error": issue.get("last_error"),
                "payload": dict(merged_payload),
            },
        )

    @staticmethod
    def _plan_issue_id(*, index: int) -> str:
        return f"plan_step:{max(1, int(index))}"

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

    @staticmethod
    def _safe_int_or_none(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except Exception:
            return None

    @staticmethod
    def _safe_float_or_none(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except Exception:
            return None
