from __future__ import annotations

import time
from typing import Any, Callable

CheckpointWriter = Callable[[dict[str, Any]], None]


def execute_task_run(
    executor: Any,
    *,
    agent: Any,
    user_id: str,
    session_id: str | None,
    user_message: str,
    checkpoint: CheckpointWriter | None = None,
    run_deadline_monotonic: float | None = None,
    resume_state: dict[str, Any] | None = None,
    run_budget: dict[str, Any] | None = None,
    run_source: str | None = None,
    step_prepare_context: str,
    step_reasoning: str,
    step_persist: str,
    issue_running: str,
    issue_done: str,
    issue_failed: str,
) -> dict[str, Any]:
    started = time.monotonic()
    run_budget_limits = executor._normalize_run_budget(run_budget)
    budget_duration_sec = run_budget_limits.get("max_duration_sec")
    if budget_duration_sec is not None and budget_duration_sec > 0:
        budget_deadline = started + budget_duration_sec
        if run_deadline_monotonic is None:
            run_deadline_monotonic = budget_deadline
        else:
            run_deadline_monotonic = min(run_deadline_monotonic, budget_deadline)
    executor._check_runtime(started=started, run_deadline_monotonic=run_deadline_monotonic)
    executor._check_message_size(user_message)

    state = executor._normalize_resume_state(resume_state)
    completed_steps = set(state.get("completed_steps", []))
    issue_states = executor._normalize_issue_states(state.get("issues"))
    issue_artifacts = executor._normalize_issue_artifacts(state.get("issue_artifacts"))
    tool_call_cache = executor._normalize_tool_call_cache(state.get("tool_call_cache"))
    executor._ensure_issue(
        issue_states=issue_states,
        issue_id=step_prepare_context,
        title="Prepare context",
        issue_order=10,
        depends_on=[],
    )
    executor._ensure_issue(
        issue_states=issue_states,
        issue_id=step_reasoning,
        title="Reasoning",
        issue_order=20,
        depends_on=[step_prepare_context],
    )
    executor._ensure_issue(
        issue_states=issue_states,
        issue_id=step_persist,
        title="Persist memory",
        issue_order=30,
        depends_on=[step_reasoning],
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

    if step_prepare_context not in completed_steps:
        executor._set_issue_status(
            issue_states=issue_states,
            issue_id=step_prepare_context,
            status=issue_running,
            checkpoint=checkpoint,
            attempt=1,
            payload={"message": "Preparation step started."},
        )
        try:
            strategy = executor.meta_controller.choose_strategy(
                user_message=user_message,
                tools_available=tools_available,
            )
            executor._emit_checkpoint(
                checkpoint,
                stage="strategy_selected",
                message=f"Strategy selected: {strategy}",
                strategy=strategy,
                tools_available=tools_available,
            )

            created_plan = executor.planner.create_plan(task=user_message, strategy=strategy)
            plan = [step.__dict__ for step in created_plan]
            executor._emit_checkpoint(
                checkpoint,
                stage="plan_created",
                message=f"Plan created with {len(plan)} steps.",
                plan_steps=plan,
            )

            executor.memory_manager.add_interaction(
                user_id=user_id,
                agent_id=agent.id,
                role="user",
                content=user_message,
                session_id=session_id,
            )

            executor._check_runtime(started=started, run_deadline_monotonic=run_deadline_monotonic)
            memory_context = executor.memory_manager.get_context(
                user_id=user_id,
                agent_id=agent.id,
                query=user_message,
                session_id=session_id,
            )
            executor._emit_checkpoint(
                checkpoint,
                stage="memory_loaded",
                message="Memory context loaded.",
                working_count=len(memory_context.get("working", [])),
                episodic_count=len(memory_context.get("episodic", [])),
                semantic_count=len(memory_context.get("semantic", [])),
                profile_count=len(memory_context.get("profile", [])),
            )

            messages = executor._build_messages(
                agent=agent,
                user_message=user_message,
                memory_context=memory_context,
                session_id=session_id,
            )
            executor._check_prompt_size(messages)
        except Exception as exc:
            executor._set_issue_status(
                issue_states=issue_states,
                issue_id=step_prepare_context,
                status=issue_failed,
                checkpoint=checkpoint,
                attempt=1,
                last_error=str(exc),
                payload={"error": str(exc)},
            )
            raise

        completed_steps.add(step_prepare_context)
        executor._set_issue_status(
            issue_states=issue_states,
            issue_id=step_prepare_context,
            status=issue_done,
            checkpoint=checkpoint,
            attempt=1,
            last_error=None,
            payload={"message": "Preparation step completed."},
        )
        snapshot = executor._build_resume_snapshot(
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
            issue_artifacts=issue_artifacts,
            tool_call_cache=tool_call_cache,
        )
        executor._emit_checkpoint(
            checkpoint,
            stage="step_completed",
            step=step_prepare_context,
            message="Preparation step completed.",
            resume_state=snapshot,
        )
    else:
        if not strategy:
            strategy = executor.meta_controller.choose_strategy(
                user_message=user_message,
                tools_available=tools_available,
            )
        if not plan:
            created_plan = executor.planner.create_plan(task=user_message, strategy=strategy)
            plan = [step.__dict__ for step in created_plan]

        executor._check_runtime(started=started, run_deadline_monotonic=run_deadline_monotonic)
        memory_context = executor.memory_manager.get_context(
            user_id=user_id,
            agent_id=agent.id,
            query=user_message,
            session_id=session_id,
        )
        messages = executor._build_messages(
            agent=agent,
            user_message=user_message,
            memory_context=memory_context,
            session_id=session_id,
        )
        executor._check_prompt_size(messages)
        executor._set_issue_status(
            issue_states=issue_states,
            issue_id=step_prepare_context,
            status=issue_done,
            checkpoint=checkpoint,
            attempt=1,
            payload={"message": "Preparation step resumed from checkpoint."},
        )
        executor._emit_checkpoint(
            checkpoint,
            stage="step_resumed",
            step=step_prepare_context,
            message="Preparation step resumed from checkpoint.",
            completed_steps=sorted(completed_steps),
        )

    plan_issue_ids = executor._register_plan_issues(
        issue_states=issue_states,
        plan=plan,
        checkpoint=checkpoint,
    )
    artifact_quality_summary: dict[str, Any] | None = None
    if plan_issue_ids:
        executor._execute_plan_issues(
            plan=plan,
            plan_issue_ids=plan_issue_ids,
            issue_states=issue_states,
            issue_artifacts=issue_artifacts,
            completed_steps=completed_steps,
            tools_available=tools_available,
            checkpoint=checkpoint,
            run_deadline_monotonic=run_deadline_monotonic,
        )
        artifact_quality_summary = executor._ensure_issue_artifact_quality(
            plan=plan,
            plan_issue_ids=plan_issue_ids,
            issue_states=issue_states,
            issue_artifacts=issue_artifacts,
            completed_steps=completed_steps,
            tools_available=tools_available,
            checkpoint=checkpoint,
            run_deadline_monotonic=run_deadline_monotonic,
        )
        artifact_note = executor._render_issue_artifact_note(
            issue_artifacts=issue_artifacts,
            plan_issue_ids=plan_issue_ids,
            merged_artifact=artifact_quality_summary.get("merged_artifact", {})
            if isinstance(artifact_quality_summary, dict)
            else {},
            conflicts=artifact_quality_summary.get("conflicts", [])
            if isinstance(artifact_quality_summary, dict)
            else [],
            scorecard=artifact_quality_summary.get("scorecard", {})
            if isinstance(artifact_quality_summary, dict)
            else {},
        )
        if artifact_note:
            messages.append({"role": "system", "content": artifact_note})
            executor._emit_checkpoint(
                checkpoint,
                stage="issue_artifacts_loaded",
                message="Issue artifacts injected into reasoning context.",
                artifact_issue_count=len(issue_artifacts),
                artifact_conflicts_count=len(
                    artifact_quality_summary.get("conflicts", [])
                    if isinstance(artifact_quality_summary, dict)
                    else []
                ),
                artifact_quality_score=(
                    artifact_quality_summary.get("scorecard", {}).get("overall_score")
                    if isinstance(artifact_quality_summary, dict)
                    else None
                ),
            )

    if step_reasoning not in completed_steps:
        executor._set_issue_status(
            issue_states=issue_states,
            issue_id=step_reasoning,
            status=issue_running,
            checkpoint=checkpoint,
            attempt=1,
            payload={"message": "Reasoning step started."},
        )
        executor._emit_checkpoint(
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
            ) = executor._reason_with_optional_tools(
                messages=messages,
                agent=agent,
                tool_events=tool_events,
                tool_call_cache=tool_call_cache,
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
                run_source=run_source,
            )
        except Exception as exc:
            executor._set_issue_status(
                issue_states=issue_states,
                issue_id=step_reasoning,
                status=issue_failed,
                checkpoint=checkpoint,
                attempt=1,
                last_error=str(exc),
                payload={"error": str(exc)},
            )
            raise
        executor._emit_checkpoint(
            checkpoint,
            stage="reasoning_completed",
            message="LLM reasoning completed.",
            provider=provider_used,
            model=model_used,
            tool_events_count=len(tool_events),
            model_calls=model_calls,
            tool_rounds=tool_rounds,
        )

        completed_steps.add(step_reasoning)
        executor._set_issue_status(
            issue_states=issue_states,
            issue_id=step_reasoning,
            status=issue_done,
            checkpoint=checkpoint,
            attempt=1,
            last_error=None,
            payload={"message": "Reasoning step completed."},
        )
        snapshot = executor._build_resume_snapshot(
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
            issue_artifacts=issue_artifacts,
            tool_call_cache=tool_call_cache,
        )
        executor._emit_checkpoint(
            checkpoint,
            stage="step_completed",
            step=step_reasoning,
            message="Reasoning step completed.",
            resume_state=snapshot,
        )
    else:
        if not response_text:
            executor._emit_checkpoint(
                checkpoint,
                stage="step_resume_fallback",
                step=step_reasoning,
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
            ) = executor._reason_with_optional_tools(
                messages=messages,
                agent=agent,
                tool_events=tool_events,
                tool_call_cache=tool_call_cache,
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
        executor._emit_checkpoint(
            checkpoint,
            stage="step_resumed",
            step=step_reasoning,
            message="Reasoning step resumed from checkpoint.",
            model_calls=model_calls,
            tool_rounds=tool_rounds,
        )
        executor._set_issue_status(
            issue_states=issue_states,
            issue_id=step_reasoning,
            status=issue_done,
            checkpoint=checkpoint,
            attempt=1,
            payload={"message": "Reasoning step resumed from checkpoint."},
        )

    response_text, provider_used, model_used, model_calls, estimated_tokens = executor._verify_and_repair_response(
        messages=messages,
        agent=agent,
        user_id=user_id,
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

    if step_persist not in completed_steps:
        executor._set_issue_status(
            issue_states=issue_states,
            issue_id=step_persist,
            status=issue_running,
            checkpoint=checkpoint,
            attempt=1,
            payload={"message": "Persist step started."},
        )
        executor._check_runtime(started=started, run_deadline_monotonic=run_deadline_monotonic)
        try:
            executor.memory_manager.add_interaction(
                user_id=user_id,
                agent_id=agent.id,
                role="assistant",
                content=response_text,
                session_id=session_id,
            )
            executor.memory_manager.remember_fact(
                user_id=user_id,
                text=f"Agent {agent.name} response: {response_text[:1000]}",
                metadata={
                    "agent_id": agent.id,
                    "kind": "response",
                },
            )
        except Exception as exc:
            executor._set_issue_status(
                issue_states=issue_states,
                issue_id=step_persist,
                status=issue_failed,
                checkpoint=checkpoint,
                attempt=1,
                last_error=str(exc),
                payload={"error": str(exc)},
            )
            raise
        completed_steps.add(step_persist)
        executor._set_issue_status(
            issue_states=issue_states,
            issue_id=step_persist,
            status=issue_done,
            checkpoint=checkpoint,
            attempt=1,
            last_error=None,
            payload={"message": "Persist step completed."},
        )
        snapshot = executor._build_resume_snapshot(
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
            issue_artifacts=issue_artifacts,
            tool_call_cache=tool_call_cache,
        )
        executor._emit_checkpoint(
            checkpoint,
            stage="step_completed",
            step=step_persist,
            message="Persist step completed.",
            resume_state=snapshot,
        )
    else:
        executor._emit_checkpoint(
            checkpoint,
            stage="step_resumed",
            step=step_persist,
            message="Persist step already completed in previous attempt.",
        )
        executor._set_issue_status(
            issue_states=issue_states,
            issue_id=step_persist,
            status=issue_done,
            checkpoint=checkpoint,
            attempt=1,
            payload={"message": "Persist step resumed from checkpoint."},
        )

    duration_ms = round((time.monotonic() - started) * 1000.0, 2)
    executor._emit_checkpoint(
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
