from __future__ import annotations

import hashlib
import json
import re
import time
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Any, Callable

import httpx

from agents.agent import Agent
from controller.meta_controller import MetaController
from kernel.contracts import CognitionBackendContract
from kernel.orchestration import execute_task_run
from memory.memory_manager import MemoryManager
from planner.planner import Planner
from tasks.execution.step_executors import execute_step_general, register_default_step_executors
from tasks.step_registry import StepExecutionContext, StepExecutionResult, StepExecutorRegistry
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
SIMULATION_RISK_ORDER: dict[str, int] = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
    "unknown": 5,
}
RUN_SOURCE_TO_ACTION_CLASS: dict[str, str] = {
    "user": "autonomous_agent",
    "automation": "autonomous_automation",
    "supervisor": "autonomous_supervisor",
}


class TaskGuardrailError(RuntimeError):
    pass


class TaskTimeoutError(RuntimeError):
    pass


class TaskBudgetError(TaskGuardrailError):
    pass


class TaskExecutor:
    def __init__(
        self,
        model_manager: CognitionBackendContract,
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
        artifact_quality_enabled: bool = True,
        artifact_quality_max_repair_attempts: int = 1,
        step_verifier_enabled: bool = True,
        step_max_retries_default: int = 1,
        step_replan_max_attempts: int = 1,
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
        self.artifact_quality_enabled = bool(artifact_quality_enabled)
        self.artifact_quality_max_repair_attempts = max(0, int(artifact_quality_max_repair_attempts))
        self.step_verifier_enabled = bool(step_verifier_enabled)
        self.step_max_retries_default = max(0, int(step_max_retries_default))
        self.step_replan_max_attempts = max(0, int(step_replan_max_attempts))
        self.step_executor_registry = StepExecutorRegistry()
        register_default_step_executors(self.step_executor_registry)

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
        run_source: str | None = None,
    ) -> dict[str, Any]:
        return execute_task_run(
            self,
            agent=agent,
            user_id=user_id,
            session_id=session_id,
            user_message=user_message,
            checkpoint=checkpoint,
            run_deadline_monotonic=run_deadline_monotonic,
            resume_state=resume_state,
            run_budget=run_budget,
            run_source=run_source,
            step_prepare_context=STEP_PREPARE_CONTEXT,
            step_reasoning=STEP_REASONING,
            step_persist=STEP_PERSIST,
            issue_running=ISSUE_RUNNING,
            issue_done=ISSUE_DONE,
            issue_failed=ISSUE_FAILED,
        )

    def simulate_run(
        self,
        *,
        agent: Agent,
        user_id: str,
        session_id: str | None,
        user_message: str,
        requested_budget: dict[str, Any] | None = None,
        max_attempts: int | None = None,
    ) -> dict[str, Any]:
        _ = user_id
        normalized_message = str(user_message or "").strip()
        if not normalized_message:
            raise TaskGuardrailError("Input message must be non-empty for simulation.")
        self._check_message_size(normalized_message)

        tools_available = bool(agent.tools)
        strategy = self.meta_controller.choose_strategy(
            user_message=normalized_message,
            tools_available=tools_available,
        )
        created_plan = self.planner.create_plan(task=normalized_message, strategy=strategy)
        plan_raw = [dict(step.__dict__) for step in created_plan]

        tool_preview = self._build_tool_simulation_preview(tool_names=agent.tools)
        plan_preview = self._build_plan_simulation_preview(
            plan=plan_raw,
            tool_preview=tool_preview,
        )

        highest_plan_risk = self._max_risk_level(
            [str(item.get("risk_level") or "low") for item in plan_preview]
        )
        highest_tool_risk = str(tool_preview.get("summary", {}).get("highest_risk_level") or "low")
        overall_risk = self._max_risk_level([highest_plan_risk, highest_tool_risk])
        risk_tags = sorted(
            {
                str(tag)
                for item in plan_preview
                for tag in list(item.get("risk_tags") or [])
                if str(tag).strip()
            }
        )
        if tools_available:
            risk_tags.append("tools_available")
        if any(bool(item.get("requires_tools")) for item in plan_preview):
            risk_tags.append("plan_requires_tools")
        risk_tags = sorted(set(risk_tags))

        budget_preview = requested_budget if isinstance(requested_budget, dict) else {}
        digest_payload = {
            "agent_id": agent.id,
            "session_id": session_id,
            "message": normalized_message,
            "strategy": strategy,
            "plan": plan_preview,
            "tools": tool_preview,
            "requested_budget": budget_preview,
            "max_attempts": max_attempts,
        }
        simulation_id = hashlib.sha256(
            json.dumps(digest_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

        return {
            "simulation_id": simulation_id,
            "mode": "dry_run",
            "agent_id": agent.id,
            "session_id": session_id,
            "message": normalized_message,
            "strategy": strategy,
            "plan": plan_preview,
            "tools": tool_preview,
            "risk_summary": {
                "overall_risk_level": overall_risk,
                "highest_plan_risk_level": highest_plan_risk,
                "highest_tool_risk_level": highest_tool_risk,
                "risk_tags": risk_tags,
                "step_count": len(plan_preview),
                "steps_requiring_tools": sum(1 for item in plan_preview if bool(item.get("requires_tools"))),
                "high_risk_steps": sum(
                    1
                    for item in plan_preview
                    if str(item.get("risk_level") or "").strip().lower() in {"high", "critical"}
                ),
                "high_risk_tools": int(tool_preview.get("summary", {}).get("high_risk_tools", 0) or 0),
                "critical_risk_tools": int(tool_preview.get("summary", {}).get("critical_risk_tools", 0) or 0),
            },
            "requested_run": {
                "max_attempts": max_attempts,
                "budget": budget_preview,
            },
            "generated_at_epoch_sec": round(time.time(), 6),
        }

    def _build_tool_simulation_preview(self, *, tool_names: list[str]) -> dict[str, Any]:
        available: list[dict[str, Any]] = []
        unknown: list[dict[str, Any]] = []
        for name in tool_names:
            tool_name = str(name or "").strip()
            if not tool_name:
                continue
            tool = self.tool_registry.get(tool_name)
            if tool is None:
                unknown.append(
                    {
                        "name": tool_name,
                        "known": False,
                        "risk_level": "unknown",
                        "risk_tags": ["unknown_tool_contract", "risk:unknown"],
                        "rollback_hint": "Disable unknown tool mapping and verify registry/plugin manifests.",
                    }
                )
                continue

            normalized_risk = self._normalized_risk_level(str(getattr(tool, "risk_level", "medium")))
            autonomy_decision = self.tool_executor.autonomy_policy.evaluate(
                tool_name=tool_name,
                risk_level=normalized_risk,
            )
            policy_decision = self.tool_executor.policy.evaluate(tool=tool, arguments={})
            requires_approval = bool(
                autonomy_decision.requires_approval or policy_decision.requires_approval
            )
            blocked_reason = None
            if not bool(autonomy_decision.allow):
                blocked_reason = str(autonomy_decision.reason or "autonomy_policy_blocked")
            elif not bool(policy_decision.allow):
                blocked_reason = str(policy_decision.reason or "isolation_policy_blocked")

            risk_tags = [f"risk:{normalized_risk}"]
            if normalized_risk in {"high", "critical"}:
                risk_tags.append("high_risk_tool")
            if requires_approval:
                risk_tags.append("approval_required")
            if blocked_reason:
                risk_tags.append("blocked_by_policy")

            available.append(
                {
                    "name": tool_name,
                    "known": True,
                    "source": str(getattr(tool, "source", "local")),
                    "risk_level": normalized_risk,
                    "approval_mode": str(getattr(tool, "approval_mode", "none")),
                    "isolation": str(getattr(tool, "isolation", "restricted")),
                    "requires_approval": requires_approval,
                    "approval_scope": self._merge_scope_for_simulation(
                        policy_scope=policy_decision.approval_scope,
                        autonomy_scope=autonomy_decision.approval_scope,
                    ),
                    "approval_ttl_sec": self._merge_ttl_for_simulation(
                        policy_ttl=policy_decision.approval_ttl_sec,
                        autonomy_ttl=autonomy_decision.approval_ttl_sec,
                    ),
                    "allow": bool(autonomy_decision.allow and policy_decision.allow),
                    "blocked_reason": blocked_reason,
                    "risk_tags": sorted(set(risk_tags)),
                    "rollback_hint": self._rollback_hint_for_tool(
                        tool_name=tool_name,
                        risk_level=normalized_risk,
                    ),
                }
            )

        highest_risk = self._max_risk_level(
            [str(item.get("risk_level") or "low") for item in available] or ["low"]
        )
        return {
            "available": sorted(available, key=lambda item: str(item.get("name") or "")),
            "unknown": sorted(unknown, key=lambda item: str(item.get("name") or "")),
            "summary": {
                "total_tools": len(available) + len(unknown),
                "known_tools": len(available),
                "unknown_tools": len(unknown),
                "highest_risk_level": highest_risk,
                "high_risk_tools": sum(
                    1
                    for item in available
                    if str(item.get("risk_level") or "").strip().lower() in {"high", "critical"}
                ),
                "critical_risk_tools": sum(
                    1
                    for item in available
                    if str(item.get("risk_level") or "").strip().lower() == "critical"
                ),
                "blocked_tools": sum(1 for item in available if not bool(item.get("allow"))),
                "approval_required_tools": sum(1 for item in available if bool(item.get("requires_approval"))),
            },
        }

    def _build_plan_simulation_preview(
        self,
        *,
        plan: list[dict[str, Any]],
        tool_preview: dict[str, Any],
    ) -> list[dict[str, Any]]:
        tool_items = list(tool_preview.get("available") or [])
        highest_tool_risk = self._max_risk_level(
            [str(item.get("risk_level") or "low") for item in tool_items] or ["low"]
        )
        rollback_hints_by_risk: list[tuple[int, str]] = []
        for item in tool_items:
            hint = str(item.get("rollback_hint") or "").strip()
            if not hint:
                continue
            level = str(item.get("risk_level") or "low")
            rollback_hints_by_risk.append((SIMULATION_RISK_ORDER.get(level, 0), hint))
        rollback_hints_by_risk.sort(reverse=True)
        top_tool_hints: list[str] = []
        for _, hint in rollback_hints_by_risk:
            if hint in top_tool_hints:
                continue
            top_tool_hints.append(hint)
            if len(top_tool_hints) >= 3:
                break

        result: list[dict[str, Any]] = []
        for index, step in enumerate(plan, start=1):
            description = str(step.get("description") or f"Plan step {index}")
            step_kind = self._infer_plan_step_kind(
                description=description,
                step_payload=step,
            )
            requires_tools = self._plan_step_requires_tools(
                description=description,
                step_payload=step,
                step_kind=step_kind,
            )
            step_risk = self._estimate_simulation_step_risk(
                description=description,
                step_kind=step_kind,
                requires_tools=requires_tools,
                highest_tool_risk=highest_tool_risk,
            )
            step_tags = self._simulation_step_tags(
                description=description,
                step_kind=step_kind,
                requires_tools=requires_tools,
                step_risk=step_risk,
                highest_tool_risk=highest_tool_risk,
            )
            rollback_hints: list[str] = []
            if requires_tools:
                rollback_hints.extend(top_tool_hints)
            if not rollback_hints:
                rollback_hints.append(
                    "No direct side effects are expected for this step; rerun or adjust plan if output quality is insufficient."
                )

            row = dict(step)
            row["id"] = int(step.get("id", index))
            row["kind"] = step_kind
            row["requires_tools"] = requires_tools
            row["risk_level"] = step_risk
            row["risk_tags"] = sorted(set(step_tags))
            row["rollback_hints"] = rollback_hints
            result.append(row)
        return result

    @staticmethod
    def _normalized_risk_level(raw: str | None) -> str:
        value = str(raw or "").strip().lower()
        if value not in {"low", "medium", "high", "critical"}:
            return "medium"
        return value

    @staticmethod
    def _normalize_run_source(raw: str | None) -> str:
        normalized = str(raw or "").strip().lower()
        if normalized not in RUN_SOURCE_TO_ACTION_CLASS:
            return "user"
        return normalized

    @staticmethod
    def _tool_action_class_for_run_source(run_source: str | None) -> str:
        normalized_source = TaskExecutor._normalize_run_source(run_source)
        return RUN_SOURCE_TO_ACTION_CLASS.get(normalized_source, "autonomous_agent")

    @staticmethod
    def _max_risk_level(levels: list[str]) -> str:
        normalized = ["low"]
        for item in levels:
            text = str(item or "").strip().lower()
            if text not in {"low", "medium", "high", "critical", "unknown"}:
                continue
            normalized.append(text)
        return max(normalized, key=lambda level: SIMULATION_RISK_ORDER.get(level, 0))

    @staticmethod
    def _merge_scope_for_simulation(*, policy_scope: str | None, autonomy_scope: str | None) -> str | None:
        order = {"request": 1, "session": 2, "user": 3, "global": 4}
        left = str(policy_scope or "").strip().lower() or None
        right = str(autonomy_scope or "").strip().lower() or None
        if left is None:
            return right
        if right is None:
            return left
        return right if order.get(right, 0) > order.get(left, 0) else left

    @staticmethod
    def _merge_ttl_for_simulation(*, policy_ttl: int | None, autonomy_ttl: int | None) -> int | None:
        candidates: list[int] = []
        if isinstance(policy_ttl, int):
            candidates.append(max(1, policy_ttl))
        if isinstance(autonomy_ttl, int):
            candidates.append(max(1, autonomy_ttl))
        if not candidates:
            return None
        return max(candidates)

    def _estimate_simulation_step_risk(
        self,
        *,
        description: str,
        step_kind: str,
        requires_tools: bool,
        highest_tool_risk: str,
    ) -> str:
        lowered = str(description or "").lower()
        if any(token in lowered for token in ("delete", "drop", "wipe", "format", "shutdown", "kill-switch")):
            return "critical"
        if any(token in lowered for token in ("write", "modify", "execute", "deploy", "install", "migrate")):
            return "high"
        if requires_tools:
            return self._max_risk_level(["medium", highest_tool_risk])
        if step_kind in {"verify", "summarize", "synthesize", "merge_results"}:
            return "low"
        return "low"

    def _simulation_step_tags(
        self,
        *,
        description: str,
        step_kind: str,
        requires_tools: bool,
        step_risk: str,
        highest_tool_risk: str,
    ) -> list[str]:
        tags: list[str] = [f"risk:{step_risk}", f"kind:{step_kind}"]
        if requires_tools:
            tags.append("tool_execution")
        if step_kind in {"fetch_source", "tool_query"}:
            tags.append("external_io")
        if step_kind in {"verify"}:
            tags.append("verification_gate")
        if step_kind in {"merge_results", "synthesize", "summarize"}:
            tags.append("data_synthesis")
        if highest_tool_risk in {"high", "critical"} and requires_tools:
            tags.append("high_risk_tool_possible")
        lowered = str(description or "").lower()
        if any(token in lowered for token in ("rollback", "revert")):
            tags.append("rollback_sensitive")
        return tags

    @staticmethod
    def _rollback_hint_for_tool(*, tool_name: str, risk_level: str) -> str:
        name = str(tool_name or "").strip().lower()
        normalized_risk = str(risk_level or "").strip().lower()
        if name == "python_exec":
            return "Review stdout/stderr and revert any filesystem changes introduced by executed code."
        if name == "filesystem":
            return "Revert changed files from VCS or restore from backup snapshot."
        if normalized_risk == "critical":
            return "Trigger incident flow, disable related automation, and rollback affected resources."
        return "Review action impact and rollback changed resources from audit trail metadata."

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
        tool_call_cache: dict[str, dict[str, Any]],
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
        run_source: str | None = None,
    ) -> tuple[str, str, str, int, int, int, int]:
        allowed_tools = [name for name in agent.tools if self.tool_registry.get(name) is not None]
        tool_action_class = self._tool_action_class_for_run_source(run_source)

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

            tool_name = str(parsed["name"])
            arguments = parsed["arguments"]
            idempotency_key = self._tool_call_idempotency_key(tool_name=tool_name, arguments=arguments)
            cached_tool_result = self._get_cached_tool_result(
                tool_call_cache=tool_call_cache,
                idempotency_key=idempotency_key,
                tool_name=tool_name,
            )
            if cached_tool_result is not None:
                event: dict[str, Any] = {
                    "attempt": attempt,
                    "tool_round": tool_rounds,
                    "tool": tool_name,
                    "arguments": arguments,
                    "status": "reused",
                    "cached": True,
                    "idempotency_key": idempotency_key,
                    "duration_ms": 0.0,
                }
                if "result" in cached_tool_result:
                    event["result"] = cached_tool_result.get("result")
                tool_events.append(event)
                self._emit_checkpoint(
                    checkpoint,
                    stage="tool_call_reused",
                    message=f"Tool result reused from cache: {tool_name}",
                    tool=tool_name,
                    attempt=attempt,
                    idempotency_key=idempotency_key,
                )
                self._emit_checkpoint(
                    checkpoint,
                    stage="tool_call_recorded",
                    message=f"Tool call record upserted: {tool_name}",
                    tool=tool_name,
                    attempt=attempt,
                    idempotency_key=idempotency_key,
                    status="reused",
                    arguments=arguments,
                    result=cached_tool_result,
                    cached=True,
                    executed=False,
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
                    idempotency_key=idempotency_key,
                    cached=True,
                    executed=False,
                )

                reasoning_messages.append({"role": "assistant", "content": response_text})
                reasoning_messages.append(
                    {
                        "role": "tool",
                        "name": tool_name,
                        "content": json.dumps(cached_tool_result, ensure_ascii=False),
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
                    message="Received follow-up model response after cached tool output.",
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

            max_tool_calls_budget = self._budget_limit_int(run_budget, "max_tool_calls")
            if max_tool_calls_budget is not None and (tool_rounds + 1) > max_tool_calls_budget:
                raise TaskBudgetError(
                    f"Run tool-call budget exceeded ({tool_rounds + 1} > {max_tool_calls_budget})."
                )

            tool_rounds += 1
            self._emit_checkpoint(
                checkpoint,
                stage="tool_call_started",
                message=f"Tool call started: {tool_name}",
                tool=tool_name,
                attempt=attempt,
                tool_round=tool_rounds,
                idempotency_key=idempotency_key,
            )
            event: dict[str, Any] = {
                "attempt": attempt,
                "tool_round": tool_rounds,
                "tool": tool_name,
                "arguments": arguments,
                "status": "started",
                "cached": False,
                "idempotency_key": idempotency_key,
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
                self._emit_checkpoint(
                    checkpoint,
                    stage="tool_call_recorded",
                    message=f"Tool call record upserted: {tool_name}",
                    tool=tool_name,
                    attempt=attempt,
                    idempotency_key=idempotency_key,
                    status=event.get("status"),
                    arguments=arguments,
                    error=event.get("error"),
                    cached=False,
                    executed=False,
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
                self._emit_checkpoint(
                    checkpoint,
                    stage="tool_call_recorded",
                    message=f"Tool call record upserted: {tool_name}",
                    tool=tool_name,
                    attempt=attempt,
                    idempotency_key=idempotency_key,
                    status=event.get("status"),
                    arguments=arguments,
                    result=tool_result,
                    error=validation_error,
                    cached=False,
                    executed=False,
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
                    agent_id=agent.id,
                    session_id=session_id,
                    action_class=tool_action_class,
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
            status_for_record = str(event.get("status", "")).strip().lower() or "unknown"
            self._emit_checkpoint(
                checkpoint,
                stage="tool_call_recorded",
                message=f"Tool call record upserted: {tool_name}",
                tool=tool_name,
                attempt=attempt,
                idempotency_key=idempotency_key,
                status=status_for_record,
                arguments=arguments,
                result=tool_result if isinstance(tool_result, dict) else None,
                error=event.get("error"),
                cached=False,
                executed=True,
            )
            if status_for_record == "succeeded" and isinstance(tool_result, dict):
                self._record_tool_call_cache_entry(
                    tool_call_cache=tool_call_cache,
                    idempotency_key=idempotency_key,
                    tool_name=tool_name,
                    arguments=arguments,
                    status=status_for_record,
                    tool_result=tool_result,
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
                idempotency_key=idempotency_key,
                cached=False,
                executed=True,
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
            "issue_artifacts",
            "tool_call_cache",
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
        issue_artifacts: dict[str, dict[str, Any]],
        tool_call_cache: dict[str, dict[str, Any]],
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
            "issue_artifacts": issue_artifacts,
            "tool_call_cache": tool_call_cache,
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
        issue_artifacts: dict[str, dict[str, Any]],
        completed_steps: set[str],
        tools_available: bool,
        checkpoint: CheckpointWriter | None,
        run_deadline_monotonic: float | None,
    ) -> None:
        for issue_id in plan_issue_ids:
            if issue_id in completed_steps:
                issue = issue_states.get(issue_id) or {}
                attempt = max(1, int(issue.get("attempt", 0)))
                self._set_issue_status(
                    issue_states=issue_states,
                    issue_id=issue_id,
                    status=ISSUE_DONE,
                    checkpoint=checkpoint,
                    attempt=attempt,
                    payload={"message": "Plan issue resumed from checkpoint."},
                )

        pending = [issue_id for issue_id in plan_issue_ids if issue_id not in completed_steps]
        if not pending:
            return

        step_by_issue: dict[str, dict[str, Any]] = {}
        for index, issue_id in enumerate(plan_issue_ids, start=1):
            step_by_issue[issue_id] = plan[index - 1] if index - 1 < len(plan) else {}

        active: dict[Future[dict[str, Any]], tuple[str, int]] = {}
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
                    attempt = max(1, int(issue.get("attempt", 0)) + 1)
                    dependencies = [str(item) for item in issue.get("depends_on", []) if str(item).strip()]
                    blocked_dep = self._first_blocking_dependency(issue_states=issue_states, dependencies=dependencies)
                    if blocked_dep is not None:
                        error_message = f"Dependency {blocked_dep} is not complete."
                        self._set_issue_status(
                            issue_states=issue_states,
                            issue_id=ready_issue,
                            status=ISSUE_BLOCKED,
                            checkpoint=checkpoint,
                            attempt=attempt,
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
                    if not isinstance(step_payload, dict):
                        step_payload = {}
                    dependency_artifacts = self._collect_dependency_artifacts(
                        dependencies=dependencies,
                        issue_artifacts=issue_artifacts,
                    )
                    step_payload_for_execution = dict(step_payload)
                    if dependency_artifacts:
                        step_payload_for_execution["_dependency_artifacts"] = dependency_artifacts
                    issue_deadline = self._compute_issue_deadline(run_deadline_monotonic=run_deadline_monotonic)
                    self._set_issue_status(
                        issue_states=issue_states,
                        issue_id=ready_issue,
                        status=ISSUE_RUNNING,
                        checkpoint=checkpoint,
                        attempt=attempt,
                        payload={
                            "message": "Plan issue started.",
                            "plan_step": step_payload,
                            "deadline_monotonic": issue_deadline,
                        },
                    )
                    future = pool.submit(
                        self._evaluate_plan_issue,
                        issue_id=ready_issue,
                        step_payload=step_payload_for_execution,
                        tools_available=tools_available,
                        issue_deadline_monotonic=issue_deadline,
                    )
                    active[future] = (ready_issue, attempt)
                    scheduled_any = True

                if not active:
                    if pending and not scheduled_any:
                        for issue_id in list(pending):
                            issue = issue_states.get(issue_id) or {}
                            attempt = max(1, int(issue.get("attempt", 0)) + 1)
                            error_message = "No schedulable issue (dependency cycle or blocked dependencies)."
                            self._set_issue_status(
                                issue_states=issue_states,
                                issue_id=issue_id,
                                status=ISSUE_BLOCKED,
                                checkpoint=checkpoint,
                                attempt=attempt,
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
                    issue_id, issue_attempt = active.pop(future)
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
                            attempt=issue_attempt,
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
                            attempt=issue_attempt,
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
                        artifact_key = str(result.get("artifact_key") or "result").strip() or "result"
                        self._record_issue_artifact(
                            issue_artifacts=issue_artifacts,
                            issue_id=issue_id,
                            artifact_key=artifact_key,
                            artifact=payload,
                            checkpoint=checkpoint,
                        )
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
                            attempt=issue_attempt,
                            payload={
                                "message": "Plan issue completed.",
                                "artifact_key": artifact_key,
                                "artifact": payload,
                                **payload,
                            },
                        )
                        continue

                    if status == ISSUE_BLOCKED:
                        reason = str(result.get("reason") or "Plan issue is blocked.")
                        current_step_payload = (
                            dict(step_by_issue.get(issue_id, {}))
                            if isinstance(step_by_issue.get(issue_id), dict)
                            else {}
                        )
                        replanned = self._replan_step_payload(
                            step_payload=current_step_payload,
                            reason=reason,
                            tools_available=tools_available,
                        )
                        if isinstance(replanned, dict):
                            step_by_issue[issue_id] = replanned
                            self._set_issue_status(
                                issue_states=issue_states,
                                issue_id=issue_id,
                                status=ISSUE_PLANNED,
                                checkpoint=checkpoint,
                                attempt=issue_attempt,
                                last_error=reason,
                                payload={
                                    "message": "Plan issue replanned.",
                                    "error": reason,
                                    "replanned": True,
                                    "plan_step": replanned,
                                    **payload,
                                },
                            )
                            self._emit_checkpoint(
                                checkpoint,
                                stage="plan_step_replanned",
                                message=f"Plan issue replanned: {issue_id}",
                                issue_id=issue_id,
                                reason=reason,
                                plan_step=replanned,
                            )
                            pending.append(issue_id)
                            continue
                        max_retries = self._step_max_retries(current_step_payload)
                        if issue_attempt <= max_retries:
                            self._set_issue_status(
                                issue_states=issue_states,
                                issue_id=issue_id,
                                status=ISSUE_PLANNED,
                                checkpoint=checkpoint,
                                attempt=issue_attempt,
                                last_error=reason,
                                payload={
                                    "message": "Plan issue blocked; retry scheduled.",
                                    "error": reason,
                                    "retry_scheduled": True,
                                    "remaining_retries": max(0, max_retries - issue_attempt),
                                    **payload,
                                },
                            )
                            self._emit_checkpoint(
                                checkpoint,
                                stage="plan_step_retry_scheduled",
                                message=f"Plan issue retry scheduled: {issue_id}",
                                issue_id=issue_id,
                                reason=reason,
                                attempt=issue_attempt + 1,
                                max_retries=max_retries,
                            )
                            pending.append(issue_id)
                            continue
                        self._set_issue_status(
                            issue_states=issue_states,
                            issue_id=issue_id,
                            status=ISSUE_BLOCKED,
                            checkpoint=checkpoint,
                            attempt=issue_attempt,
                            last_error=reason,
                            payload={"error": reason, **payload},
                        )
                        raise TaskGuardrailError(reason)

                    if status == ISSUE_FAILED:
                        reason = str(result.get("reason") or "Plan issue failed.")
                        current_step_payload = (
                            dict(step_by_issue.get(issue_id, {}))
                            if isinstance(step_by_issue.get(issue_id), dict)
                            else {}
                        )
                        max_retries = self._step_max_retries(current_step_payload)
                        if issue_attempt <= max_retries:
                            self._set_issue_status(
                                issue_states=issue_states,
                                issue_id=issue_id,
                                status=ISSUE_PLANNED,
                                checkpoint=checkpoint,
                                attempt=issue_attempt,
                                last_error=reason,
                                payload={
                                    "message": "Plan issue failed; retry scheduled.",
                                    "error": reason,
                                    "retry_scheduled": True,
                                    "remaining_retries": max(0, max_retries - issue_attempt),
                                    **payload,
                                },
                            )
                            self._emit_checkpoint(
                                checkpoint,
                                stage="plan_step_retry_scheduled",
                                message=f"Plan issue retry scheduled: {issue_id}",
                                issue_id=issue_id,
                                reason=reason,
                                attempt=issue_attempt + 1,
                                max_retries=max_retries,
                            )
                            pending.append(issue_id)
                            continue
                        replanned = self._replan_step_payload(
                            step_payload=current_step_payload,
                            reason=reason,
                            tools_available=tools_available,
                        )
                        if isinstance(replanned, dict):
                            step_by_issue[issue_id] = replanned
                            self._set_issue_status(
                                issue_states=issue_states,
                                issue_id=issue_id,
                                status=ISSUE_PLANNED,
                                checkpoint=checkpoint,
                                attempt=issue_attempt,
                                last_error=reason,
                                payload={
                                    "message": "Plan issue replanned after failure.",
                                    "error": reason,
                                    "replanned": True,
                                    "plan_step": replanned,
                                    **payload,
                                },
                            )
                            self._emit_checkpoint(
                                checkpoint,
                                stage="plan_step_replanned",
                                message=f"Plan issue replanned: {issue_id}",
                                issue_id=issue_id,
                                reason=reason,
                                plan_step=replanned,
                            )
                            pending.append(issue_id)
                            continue
                        self._set_issue_status(
                            issue_states=issue_states,
                            issue_id=issue_id,
                            status=ISSUE_FAILED,
                            checkpoint=checkpoint,
                            attempt=issue_attempt,
                            last_error=reason,
                            payload={"error": reason, **payload},
                        )
                        raise TaskGuardrailError(reason)

                    self._set_issue_status(
                        issue_states=issue_states,
                        issue_id=issue_id,
                        status=ISSUE_DONE,
                        checkpoint=checkpoint,
                        attempt=issue_attempt,
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

    def _normalize_issue_artifacts(self, raw: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(raw, dict):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for issue_id, artifacts in raw.items():
            normalized_issue_id = str(issue_id).strip()
            if not normalized_issue_id or not isinstance(artifacts, dict):
                continue
            normalized_artifacts: dict[str, Any] = {}
            for artifact_key, artifact_value in artifacts.items():
                key = str(artifact_key).strip()
                if not key or not isinstance(artifact_value, dict):
                    continue
                normalized_artifacts[key] = dict(artifact_value)
            if normalized_artifacts:
                result[normalized_issue_id] = normalized_artifacts
        return result

    def _normalize_tool_call_cache(self, raw: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(raw, dict):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for idempotency_key, payload in raw.items():
            normalized_key = str(idempotency_key).strip()
            if not normalized_key or not isinstance(payload, dict):
                continue
            status = str(payload.get("status") or "").strip().lower()
            tool_name = str(payload.get("tool_name") or payload.get("tool") or "").strip()
            arguments = payload.get("arguments")
            tool_result = payload.get("tool_result") or payload.get("result")
            if status != "succeeded":
                continue
            if not tool_name:
                continue
            if not isinstance(arguments, dict):
                arguments = {}
            if not isinstance(tool_result, dict):
                continue
            result[normalized_key] = {
                "tool_name": tool_name,
                "status": "succeeded",
                "arguments": dict(arguments),
                "tool_result": dict(tool_result),
            }
        return result

    @staticmethod
    def _tool_call_idempotency_key(*, tool_name: str, arguments: dict[str, Any]) -> str:
        canonical = json.dumps(arguments or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        raw = f"{str(tool_name).strip().lower()}::{canonical}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _get_cached_tool_result(
        *,
        tool_call_cache: dict[str, dict[str, Any]],
        idempotency_key: str,
        tool_name: str,
    ) -> dict[str, Any] | None:
        entry = tool_call_cache.get(str(idempotency_key))
        if not isinstance(entry, dict):
            return None
        status = str(entry.get("status") or "").strip().lower()
        if status != "succeeded":
            return None
        cached_tool = str(entry.get("tool_name") or entry.get("tool") or "").strip()
        if not cached_tool or cached_tool != str(tool_name).strip():
            return None
        result_payload = entry.get("tool_result") or entry.get("result")
        if not isinstance(result_payload, dict):
            return None
        return dict(result_payload)

    @staticmethod
    def _record_tool_call_cache_entry(
        *,
        tool_call_cache: dict[str, dict[str, Any]],
        idempotency_key: str,
        tool_name: str,
        arguments: dict[str, Any],
        status: str,
        tool_result: dict[str, Any],
    ) -> None:
        normalized_key = str(idempotency_key).strip()
        if not normalized_key:
            return
        normalized_status = str(status or "").strip().lower()
        if normalized_status != "succeeded":
            return
        tool_call_cache[normalized_key] = {
            "tool_name": str(tool_name).strip() or "unknown",
            "status": "succeeded",
            "arguments": dict(arguments or {}),
            "tool_result": dict(tool_result),
        }

    def _record_issue_artifact(
        self,
        *,
        issue_artifacts: dict[str, dict[str, Any]],
        issue_id: str,
        artifact_key: str,
        artifact: dict[str, Any],
        checkpoint: CheckpointWriter | None,
    ) -> None:
        normalized_issue_id = str(issue_id).strip()
        normalized_key = str(artifact_key).strip() or "result"
        if not normalized_issue_id or not isinstance(artifact, dict):
            return
        issue_artifacts.setdefault(normalized_issue_id, {})
        issue_artifacts[normalized_issue_id][normalized_key] = dict(artifact)
        self._emit_checkpoint(
            checkpoint,
            stage="issue_artifact",
            message=f"Issue artifact recorded: {normalized_issue_id}/{normalized_key}",
            issue_id=normalized_issue_id,
            artifact_key=normalized_key,
            artifact=dict(artifact),
        )

    def _ensure_issue_artifact_quality(
        self,
        *,
        plan: list[dict[str, Any]],
        plan_issue_ids: list[str],
        issue_states: dict[str, dict[str, Any]],
        issue_artifacts: dict[str, dict[str, Any]],
        completed_steps: set[str],
        tools_available: bool,
        checkpoint: CheckpointWriter | None,
        run_deadline_monotonic: float | None,
    ) -> dict[str, Any]:
        quality = self._evaluate_issue_artifact_quality(
            plan=plan,
            plan_issue_ids=plan_issue_ids,
            issue_states=issue_states,
            issue_artifacts=issue_artifacts,
        )
        self._emit_checkpoint(
            checkpoint,
            stage="artifact_quality_evaluated",
            message="Issue artifact quality evaluated.",
            quality_passed=quality["passed"],
            problematic_issue_ids=quality["problematic_issue_ids"],
            repair_priority=quality["repair_priority"],
            missing_artifacts=quality["missing_artifacts"],
            invalid_artifacts=quality["invalid_artifacts"],
            conflicts=quality["conflicts"],
            merged_artifact=quality["merged_artifact"],
            scorecard=quality["scorecard"],
        )
        if not self.artifact_quality_enabled:
            self._emit_checkpoint(
                checkpoint,
                stage="artifact_quality_passed",
                message="Issue artifact quality gate is disabled.",
                quality_gate_enabled=False,
                scorecard=quality["scorecard"],
            )
            return quality

        if quality["passed"]:
            self._emit_checkpoint(
                checkpoint,
                stage="artifact_quality_passed",
                message="Issue artifact quality gate passed.",
                repair_attempt=0,
                conflicts_count=len(quality["conflicts"]),
                scorecard=quality["scorecard"],
            )
            return quality

        for repair_attempt in range(1, self.artifact_quality_max_repair_attempts + 1):
            problematic_issue_ids = list(quality["repair_priority"] or quality["problematic_issue_ids"])
            if not problematic_issue_ids:
                break

            self._emit_checkpoint(
                checkpoint,
                stage="artifact_repair_attempt",
                message="Attempting issue artifact quality repair.",
                repair_attempt=repair_attempt,
                problematic_issue_ids=problematic_issue_ids,
                issues_by_issue=quality["issues_by_issue"],
                scorecard=quality["scorecard"],
            )

            for issue_id in problematic_issue_ids:
                completed_steps.discard(issue_id)
                issue_artifacts.pop(issue_id, None)

                issue = issue_states.get(issue_id) or {}
                attempt = max(1, int(issue.get("attempt", 0)) + 1)
                plan_step = quality["step_by_issue"].get(issue_id, {})
                self._set_issue_status(
                    issue_states=issue_states,
                    issue_id=issue_id,
                    status=ISSUE_PLANNED,
                    checkpoint=checkpoint,
                    attempt=attempt,
                    last_error=None,
                    payload={
                        "message": "Issue marked for artifact quality repair.",
                        "repair_attempt": repair_attempt,
                        "artifact_quality_issues": quality["issues_by_issue"].get(issue_id, []),
                        "plan_step": plan_step,
                    },
                )

            self._execute_plan_issues(
                plan=plan,
                plan_issue_ids=plan_issue_ids,
                issue_states=issue_states,
                issue_artifacts=issue_artifacts,
                completed_steps=completed_steps,
                tools_available=tools_available,
                checkpoint=checkpoint,
                run_deadline_monotonic=run_deadline_monotonic,
            )
            quality = self._evaluate_issue_artifact_quality(
                plan=plan,
                plan_issue_ids=plan_issue_ids,
                issue_states=issue_states,
                issue_artifacts=issue_artifacts,
            )
            self._emit_checkpoint(
                checkpoint,
                stage="artifact_quality_evaluated",
                message="Issue artifact quality re-evaluated after repair.",
                repair_attempt=repair_attempt,
                quality_passed=quality["passed"],
                problematic_issue_ids=quality["problematic_issue_ids"],
                repair_priority=quality["repair_priority"],
                missing_artifacts=quality["missing_artifacts"],
                invalid_artifacts=quality["invalid_artifacts"],
                conflicts=quality["conflicts"],
                merged_artifact=quality["merged_artifact"],
                scorecard=quality["scorecard"],
            )
            if quality["passed"]:
                self._emit_checkpoint(
                    checkpoint,
                    stage="artifact_quality_passed",
                    message="Issue artifact quality gate passed after repair.",
                    repair_attempt=repair_attempt,
                    conflicts_count=len(quality["conflicts"]),
                    scorecard=quality["scorecard"],
                )
                return quality

        failure_message = "Issue artifact quality gate failed after repair attempts."
        self._emit_checkpoint(
            checkpoint,
            stage="artifact_quality_failed",
            message=failure_message,
            problematic_issue_ids=quality["problematic_issue_ids"],
            repair_priority=quality["repair_priority"],
            missing_artifacts=quality["missing_artifacts"],
            invalid_artifacts=quality["invalid_artifacts"],
            conflicts=quality["conflicts"],
            scorecard=quality["scorecard"],
            max_repair_attempts=self.artifact_quality_max_repair_attempts,
        )
        raise TaskGuardrailError(
            failure_message
            + " problematic="
            + ",".join(quality["problematic_issue_ids"])
        )

    def _evaluate_issue_artifact_quality(
        self,
        *,
        plan: list[dict[str, Any]],
        plan_issue_ids: list[str],
        issue_states: dict[str, dict[str, Any]],
        issue_artifacts: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        step_by_issue: dict[str, dict[str, Any]] = {}
        for index, issue_id in enumerate(plan_issue_ids, start=1):
            step_by_issue[issue_id] = plan[index - 1] if index - 1 < len(plan) else {}

        issues_by_issue: dict[str, list[str]] = {}
        missing_artifacts: list[dict[str, Any]] = []
        invalid_artifacts: list[dict[str, Any]] = []

        for issue_id in plan_issue_ids:
            issue_state = issue_states.get(issue_id) or {}
            status = str(issue_state.get("status") or ISSUE_PLANNED).strip().lower()
            if status != ISSUE_DONE:
                reason = f"issue_status_not_done:{status or ISSUE_PLANNED}"
                missing_artifacts.append({"issue_id": issue_id, "reason": reason})
                issues_by_issue.setdefault(issue_id, []).append(reason)
                continue

            artifacts = issue_artifacts.get(issue_id)
            if not isinstance(artifacts, dict) or not artifacts:
                reason = "artifact_missing"
                missing_artifacts.append({"issue_id": issue_id, "reason": reason})
                issues_by_issue.setdefault(issue_id, []).append(reason)
                continue

            primary_key = "result" if "result" in artifacts else sorted(artifacts.keys())[0]
            primary_artifact = artifacts.get(primary_key)
            if not isinstance(primary_artifact, dict):
                reason = "artifact_not_object"
                invalid_artifacts.append(
                    {
                        "issue_id": issue_id,
                        "artifact_key": primary_key,
                        "reason": reason,
                    }
                )
                issues_by_issue.setdefault(issue_id, []).append(reason)
                continue

            description = primary_artifact.get("description")
            if not isinstance(description, str) or not description.strip():
                reason = "description_missing"
                invalid_artifacts.append(
                    {
                        "issue_id": issue_id,
                        "artifact_key": primary_key,
                        "reason": reason,
                    }
                )
                issues_by_issue.setdefault(issue_id, []).append(reason)

            requires_tools = primary_artifact.get("requires_tools")
            if requires_tools is not None and not isinstance(requires_tools, bool):
                reason = "requires_tools_invalid_type"
                invalid_artifacts.append(
                    {
                        "issue_id": issue_id,
                        "artifact_key": primary_key,
                        "reason": reason,
                    }
                )
                issues_by_issue.setdefault(issue_id, []).append(reason)

            duration_ms = primary_artifact.get("duration_ms")
            if duration_ms is not None and not self._is_non_negative_number(duration_ms):
                reason = "duration_ms_invalid"
                invalid_artifacts.append(
                    {
                        "issue_id": issue_id,
                        "artifact_key": primary_key,
                        "reason": reason,
                    }
                )
                issues_by_issue.setdefault(issue_id, []).append(reason)

        merged_artifact, merged_sources, conflicts, selected = self._merge_issue_artifacts(
            issue_artifacts=issue_artifacts,
            plan_issue_ids=plan_issue_ids,
        )
        problematic_issue_ids = [
            issue_id for issue_id in plan_issue_ids if issues_by_issue.get(issue_id)
        ]
        scorecard = self._build_artifact_quality_scorecard(
            plan_issue_ids=plan_issue_ids,
            issues_by_issue=issues_by_issue,
            missing_artifacts=missing_artifacts,
            invalid_artifacts=invalid_artifacts,
            conflicts=conflicts,
            merged_sources=merged_sources,
        )
        return {
            "passed": len(problematic_issue_ids) == 0,
            "step_by_issue": step_by_issue,
            "issues_by_issue": issues_by_issue,
            "problematic_issue_ids": problematic_issue_ids,
            "repair_priority": scorecard.get("repair_priority", []),
            "missing_artifacts": missing_artifacts,
            "invalid_artifacts": invalid_artifacts,
            "conflicts": conflicts,
            "merged_artifact": merged_artifact,
            "merged_sources": merged_sources,
            "selected_artifacts": selected,
            "scorecard": scorecard,
        }

    @staticmethod
    def _merge_issue_artifacts(
        *,
        issue_artifacts: dict[str, dict[str, Any]],
        plan_issue_ids: list[str],
    ) -> tuple[dict[str, Any], dict[str, str], list[dict[str, Any]], list[dict[str, Any]]]:
        selected: list[dict[str, Any]] = []
        for issue_id in plan_issue_ids:
            artifacts = issue_artifacts.get(issue_id)
            if not isinstance(artifacts, dict):
                continue
            for artifact_key in sorted(artifacts.keys()):
                artifact = artifacts.get(artifact_key)
                if not isinstance(artifact, dict):
                    continue
                selected.append(
                    {
                        "issue_id": issue_id,
                        "artifact_key": str(artifact_key),
                        "artifact": dict(artifact),
                    }
                )

        merged: dict[str, Any] = {}
        merged_sources: dict[str, str] = {}
        conflicts: list[dict[str, Any]] = []
        for item in selected:
            issue_id = str(item.get("issue_id"))
            artifact_key = str(item.get("artifact_key"))
            source = f"{issue_id}/{artifact_key}"
            artifact = item.get("artifact")
            if not isinstance(artifact, dict):
                continue
            for raw_field, value in artifact.items():
                field = str(raw_field).strip()
                if not field:
                    continue
                if field in merged and merged[field] != value:
                    conflicts.append(
                        {
                            "field": field,
                            "previous_source": merged_sources[field],
                            "previous_value": merged[field],
                            "next_source": source,
                            "next_value": value,
                            "resolution": "latest_issue_wins",
                        }
                    )
                merged[field] = value
                merged_sources[field] = source

        return merged, merged_sources, conflicts, selected

    @staticmethod
    def _is_non_negative_number(value: Any) -> bool:
        if isinstance(value, bool):
            return False
        if not isinstance(value, (int, float)):
            return False
        return float(value) >= 0.0

    @staticmethod
    def _build_artifact_quality_scorecard(
        *,
        plan_issue_ids: list[str],
        issues_by_issue: dict[str, list[str]],
        missing_artifacts: list[dict[str, Any]],
        invalid_artifacts: list[dict[str, Any]],
        conflicts: list[dict[str, Any]],
        merged_sources: dict[str, str],
    ) -> dict[str, Any]:
        total_issues = max(1, len(plan_issue_ids))
        missing_issue_ids = {
            str(item.get("issue_id"))
            for item in missing_artifacts
            if str(item.get("issue_id")).strip()
        }
        invalid_issue_ids = {
            str(item.get("issue_id"))
            for item in invalid_artifacts
            if str(item.get("issue_id")).strip()
        }

        completeness = max(0.0, 1.0 - (len(missing_issue_ids) / total_issues))
        validity = max(0.0, 1.0 - (len(invalid_issue_ids) / total_issues))
        max_conflicts = max(1, len(merged_sources))
        consistency = max(0.0, 1.0 - min(1.0, len(conflicts) / max_conflicts))

        conflict_hits: dict[str, int] = {}
        for conflict in conflicts:
            for source_field in ("previous_source", "next_source"):
                source = str(conflict.get(source_field, "")).strip()
                if not source:
                    continue
                issue_id = source.split("/", 1)[0].strip()
                if not issue_id:
                    continue
                conflict_hits[issue_id] = conflict_hits.get(issue_id, 0) + 1

        issue_scores: list[dict[str, Any]] = []
        for issue_id in plan_issue_ids:
            problems = list(issues_by_issue.get(issue_id, []))
            issue_missing = any(
                item in {"artifact_missing"} or item.startswith("issue_status_not_done:")
                for item in problems
            )
            issue_invalid_count = sum(
                1
                for item in problems
                if item not in {"artifact_missing"} and not item.startswith("issue_status_not_done:")
            )
            issue_conflicts = max(0, int(conflict_hits.get(issue_id, 0)))

            score = 1.0
            if issue_missing:
                score -= 0.65
            score -= min(0.25, issue_invalid_count * 0.1)
            score -= min(0.15, issue_conflicts * 0.05)
            bounded_score = round(max(0.0, min(1.0, score)), 3)
            issue_scores.append(
                {
                    "issue_id": issue_id,
                    "score": bounded_score,
                    "problems": problems,
                    "conflict_hits": issue_conflicts,
                }
            )

        if issue_scores:
            issue_average = sum(float(item["score"]) for item in issue_scores) / len(issue_scores)
        else:
            issue_average = 1.0

        overall_score = round(
            max(
                0.0,
                min(
                    1.0,
                    (completeness * 0.35)
                    + (validity * 0.35)
                    + (consistency * 0.15)
                    + (issue_average * 0.15),
                ),
            ),
            3,
        )

        repair_priority = [
            item["issue_id"]
            for item in sorted(
                issue_scores,
                key=lambda value: (float(value["score"]), value["issue_id"]),
            )
            if item["problems"]
        ]
        return {
            "overall_score": overall_score,
            "component_scores": {
                "completeness": round(completeness, 3),
                "validity": round(validity, 3),
                "consistency": round(consistency, 3),
                "issue_average": round(issue_average, 3),
            },
            "issue_scores": issue_scores,
            "repair_priority": repair_priority,
            "counts": {
                "issues": len(plan_issue_ids),
                "missing_issues": len(missing_issue_ids),
                "invalid_issues": len(invalid_issue_ids),
                "conflicts": len(conflicts),
            },
        }

    @staticmethod
    def _render_issue_artifact_note(
        *,
        issue_artifacts: dict[str, dict[str, Any]],
        plan_issue_ids: list[str],
        merged_artifact: dict[str, Any],
        conflicts: list[dict[str, Any]],
        scorecard: dict[str, Any],
    ) -> str:
        if not plan_issue_ids:
            return ""
        selected: list[dict[str, Any]] = []
        for issue_id in plan_issue_ids:
            artifacts = issue_artifacts.get(issue_id)
            if not isinstance(artifacts, dict):
                continue
            for artifact_key, artifact in artifacts.items():
                if not isinstance(artifact, dict):
                    continue
                selected.append(
                    {
                        "issue_id": issue_id,
                        "artifact_key": str(artifact_key),
                        "artifact": artifact,
                    }
                )
        if not selected:
            return ""
        payload: dict[str, Any] = {
            "per_issue": selected,
            "merged": merged_artifact,
            "quality": {
                "overall_score": scorecard.get("overall_score"),
                "component_scores": scorecard.get("component_scores"),
                "repair_priority": scorecard.get("repair_priority"),
            },
        }
        if conflicts:
            payload["conflicts"] = conflicts
        return "Issue artifacts context: " + json.dumps(payload, ensure_ascii=False)

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
    def _collect_dependency_artifacts(
        *,
        dependencies: list[str],
        issue_artifacts: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for dependency in dependencies:
            artifacts = issue_artifacts.get(str(dependency))
            if not isinstance(artifacts, dict):
                continue
            normalized: dict[str, Any] = {}
            for key, value in artifacts.items():
                normalized_key = str(key).strip()
                if not normalized_key or not isinstance(value, dict):
                    continue
                normalized[normalized_key] = dict(value)
            if normalized:
                result[str(dependency)] = normalized
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
        active: dict[Future[dict[str, Any]], tuple[str, int]],
        issue_states: dict[str, dict[str, Any]],
        checkpoint: CheckpointWriter | None,
    ) -> None:
        now = time.monotonic()
        timed_out: list[Future[dict[str, Any]]] = []
        for future, active_entry in active.items():
            issue_id, _ = active_entry
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
            active_entry = active.pop(future, None)
            if active_entry is None:
                continue
            issue_id, issue_attempt = active_entry
            future.cancel()
            error_message = f"Issue exceeded deadline ({self.issue_timeout_sec:.2f}s)."
            self._set_issue_status(
                issue_states=issue_states,
                issue_id=issue_id,
                status=ISSUE_FAILED,
                checkpoint=checkpoint,
                attempt=issue_attempt,
                last_error=error_message,
                payload={"error": error_message},
            )
            raise TaskTimeoutError(error_message)

    @staticmethod
    def _default_step_contract_tokens(
        *,
        step_kind: str,
        requires_tools: bool,
        has_dependencies: bool,
    ) -> tuple[list[str], list[str]]:
        normalized_kind = str(step_kind or "").strip().lower()
        preconditions: list[str] = []
        postconditions = [
            "artifact_has_description",
            "artifact_has_step_kind",
        ]
        dependency_sensitive_kinds = {
            "extract_facts",
            "summarize",
            "synthesize",
            "merge_results",
            "verify",
            "compare_targets",
        }
        if has_dependencies or normalized_kind in dependency_sensitive_kinds:
            preconditions.append("dependency_context_available")
        if requires_tools or normalized_kind in {"fetch_source", "tool_query"}:
            preconditions.append("tools_available_if_required")
            postconditions.append("artifact_has_tool_blueprint")
        if normalized_kind == "extract_facts":
            postconditions.append("artifact_has_extracted_points")
        if normalized_kind in {"summarize", "synthesize", "merge_results"}:
            postconditions.append("artifact_has_summary_outline")
        if normalized_kind in {"verify", "compare_targets"}:
            postconditions.append("artifact_has_verification_checklist")
        return preconditions, postconditions

    def _normalize_step_contract_tokens(self, raw: Any, *, fallback: list[str]) -> list[str]:
        if isinstance(raw, list):
            values = [str(item).strip().lower() for item in raw]
            result = [item for item in values if item]
            if result:
                return result
        return [str(item).strip().lower() for item in fallback if str(item).strip()]

    def _check_step_preconditions(
        self,
        *,
        preconditions: list[str],
        requires_tools: bool,
        tools_available: bool,
        dependency_artifacts: dict[str, dict[str, Any]],
        dependency_points: list[str],
    ) -> tuple[bool, str]:
        for condition in preconditions:
            token = str(condition or "").strip().lower()
            if not token:
                continue
            if token == "tools_available_if_required":
                if requires_tools and not tools_available:
                    return False, "Plan step requires tools but agent has no tools configured."
                continue
            if token == "dependency_context_available":
                if not dependency_artifacts and not dependency_points:
                    return False, "Plan step requires dependency artifacts from previous steps."
                continue
        return True, ""

    @staticmethod
    def _check_step_postcondition(
        *,
        condition: str,
        step_kind: str,
        payload: dict[str, Any],
    ) -> tuple[bool, str]:
        token = str(condition or "").strip().lower()
        if not token:
            return True, ""
        if token == "artifact_has_description":
            value = str(payload.get("description") or "").strip()
            return (bool(value), "description is empty" if not value else "")
        if token == "artifact_has_step_kind":
            value = str(payload.get("step_kind") or "").strip().lower()
            return (value == step_kind, f"step_kind mismatch: expected={step_kind} actual={value}")
        if token == "artifact_has_tool_blueprint":
            value = payload.get("tool_blueprint")
            return (isinstance(value, dict), "tool_blueprint is missing")
        if token == "artifact_has_extracted_points":
            value = payload.get("extracted_points")
            return (isinstance(value, list) and bool(value), "extracted_points are missing")
        if token == "artifact_has_summary_outline":
            value = payload.get("summary_outline")
            return (isinstance(value, list) and bool(value), "summary_outline is missing")
        if token == "artifact_has_verification_checklist":
            value = payload.get("verification_checklist")
            return (isinstance(value, list) and bool(value), "verification_checklist is missing")
        return True, ""

    def _verify_step_payload(
        self,
        *,
        step_kind: str,
        payload: dict[str, Any],
        postconditions: list[str],
    ) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        for condition in postconditions:
            passed, reason = self._check_step_postcondition(
                condition=condition,
                step_kind=step_kind,
                payload=payload,
            )
            checks.append(
                {
                    "condition": condition,
                    "passed": bool(passed),
                    "reason": str(reason or ""),
                }
            )
        total = len(checks)
        passed_total = sum(1 for item in checks if bool(item.get("passed")))
        score = float(passed_total) / float(total) if total else 1.0
        failed = [item for item in checks if not bool(item.get("passed"))]
        return {
            "enabled": self.step_verifier_enabled,
            "checks": checks,
            "passed": not failed,
            "score": round(score, 6),
            "failed_conditions": failed,
        }

    def _step_max_retries(self, step_payload: dict[str, Any]) -> int:
        raw = step_payload.get("max_retries")
        if raw is None:
            return self.step_max_retries_default
        try:
            return max(0, int(raw))
        except Exception:
            return self.step_max_retries_default

    @staticmethod
    def _step_replan_attempts(step_payload: dict[str, Any]) -> int:
        try:
            return max(0, int(step_payload.get("_replan_attempts", 0)))
        except Exception:
            return 0

    @staticmethod
    def _step_replan_allowed(step_payload: dict[str, Any]) -> bool:
        value = step_payload.get("replan_allowed")
        if isinstance(value, bool):
            return value
        return True

    def _replan_step_payload(
        self,
        *,
        step_payload: dict[str, Any],
        reason: str,
        tools_available: bool,
    ) -> dict[str, Any] | None:
        if not self._step_replan_allowed(step_payload):
            return None
        attempts = self._step_replan_attempts(step_payload)
        if attempts >= self.step_replan_max_attempts:
            return None
        updated = dict(step_payload)
        updated["_replan_attempts"] = attempts + 1
        hints = updated.get("hints")
        normalized_hints = dict(hints) if isinstance(hints, dict) else {}
        normalized_hints["replan_reason"] = str(reason or "").strip()
        updated["hints"] = normalized_hints
        if bool(updated.get("requires_tools")) and not tools_available:
            updated["kind"] = "analyze_request"
            updated["requires_tools"] = False
            updated["preconditions"] = [
                item
                for item in self._normalize_step_contract_tokens(updated.get("preconditions"), fallback=[])
                if item != "tools_available_if_required"
            ]
            return updated
        if "dependency artifacts" in str(reason or "").lower() or "dependency context" in str(reason or "").lower():
            updated["kind"] = "analyze_request"
            updated["preconditions"] = [
                item
                for item in self._normalize_step_contract_tokens(updated.get("preconditions"), fallback=[])
                if item != "dependency_context_available"
            ]
            return updated
        if "postconditions" in str(reason or "").lower() or "verifier" in str(reason or "").lower():
            updated["max_retries"] = max(self._step_max_retries(updated), 1)
            return updated
        return None

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

        payload_input = dict(step_payload) if isinstance(step_payload, dict) else {}
        description = str(payload_input.get("description") or issue_id).strip() or issue_id
        step_kind = self._infer_plan_step_kind(description=description, step_payload=payload_input)
        hints = payload_input.get("hints")
        normalized_hints = dict(hints) if isinstance(hints, dict) else {}
        dependency_artifacts = self._normalize_dependency_artifacts(payload_input.get("_dependency_artifacts"))
        dependency_points = self._extract_dependency_points(dependency_artifacts)
        requires_tools = self._plan_step_requires_tools(
            description=description,
            step_payload=payload_input,
            step_kind=step_kind,
        )
        fallback_preconditions, fallback_postconditions = self._default_step_contract_tokens(
            step_kind=step_kind,
            requires_tools=requires_tools,
            has_dependencies=bool(dependency_artifacts) or bool(payload_input.get("depends_on")),
        )
        preconditions = self._normalize_step_contract_tokens(
            payload_input.get("preconditions"),
            fallback=fallback_preconditions,
        )
        postconditions = self._normalize_step_contract_tokens(
            payload_input.get("postconditions"),
            fallback=fallback_postconditions,
        )
        preconditions_ok, precondition_reason = self._check_step_preconditions(
            preconditions=preconditions,
            requires_tools=requires_tools,
            tools_available=tools_available,
            dependency_artifacts=dependency_artifacts,
            dependency_points=dependency_points,
        )
        if not preconditions_ok:
            return {
                "status": ISSUE_BLOCKED,
                "reason": precondition_reason,
                "payload": {
                    "step_kind": step_kind,
                    "requires_tools": requires_tools,
                    "description": description,
                    "dependency_artifacts_count": len(dependency_artifacts),
                    "preconditions": preconditions,
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

        context = StepExecutionContext(
            issue_id=issue_id,
            step_kind=step_kind,
            description=description,
            requires_tools=requires_tools,
            objective=str(payload_input.get("objective") or "").strip(),
            expected_output=str(payload_input.get("expected_output") or "").strip(),
            hints=normalized_hints,
            dependency_artifacts=dependency_artifacts,
            dependency_points=dependency_points,
            issue_deadline_monotonic=issue_deadline_monotonic,
        )
        executor = self.step_executor_registry.resolve(step_kind) or execute_step_general
        try:
            step_result = executor(context)
        except Exception as exc:
            return {
                "status": ISSUE_FAILED,
                "reason": f"Step executor crashed: {exc}",
                "payload": {
                    "description": description,
                    "step_kind": step_kind,
                    "requires_tools": requires_tools,
                },
            }
        if not isinstance(step_result, StepExecutionResult):
            return {
                "status": ISSUE_FAILED,
                "reason": "Step executor returned invalid result type.",
                "payload": {
                    "description": description,
                    "step_kind": step_kind,
                    "requires_tools": requires_tools,
                },
            }
        status = str(step_result.status or ISSUE_DONE).strip().lower() or ISSUE_DONE
        if status not in ISSUE_STATUSES:
            status = ISSUE_DONE
        payload = dict(step_result.payload or {})
        payload.setdefault("description", description)
        payload.setdefault("step_kind", step_kind)
        payload.setdefault("requires_tools", requires_tools)
        payload.setdefault("objective", context.objective)
        payload.setdefault("expected_output", context.expected_output)
        payload.setdefault("dependency_artifacts_count", len(dependency_artifacts))

        if status != ISSUE_DONE:
            return {
                "status": status,
                "reason": str(step_result.reason or "Step execution returned non-terminal success."),
                "payload": payload,
            }

        if time.monotonic() > issue_deadline_monotonic:
            return {
                "status": ISSUE_FAILED,
                "reason": "Issue deadline exceeded during execution.",
                "payload": {
                    "description": description,
                    "step_kind": step_kind,
                },
            }
        verifier = self._verify_step_payload(
            step_kind=step_kind,
            payload=payload,
            postconditions=postconditions,
        )
        payload["verifier"] = verifier
        if self.step_verifier_enabled and not bool(verifier.get("passed")):
            failed_tokens = [
                str(item.get("condition"))
                for item in verifier.get("failed_conditions", [])
                if isinstance(item, dict)
            ]
            reason = (
                "Step verifier failed postconditions: "
                + (", ".join(failed_tokens) if failed_tokens else "unspecified")
            )
            return {
                "status": ISSUE_FAILED,
                "reason": reason,
                "payload": payload,
            }
        payload["duration_ms"] = round((time.monotonic() - started) * 1000.0, 3)
        return {
            "status": ISSUE_DONE,
            "artifact_key": str(step_result.artifact_key or "result").strip() or "result",
            "payload": payload,
        }

    @staticmethod
    def _infer_plan_step_kind(*, description: str, step_payload: dict[str, Any]) -> str:
        explicit = str(step_payload.get("kind") or "").strip().lower()
        if explicit:
            return explicit
        text = description.lower()
        if any(token in text for token in ("fetch", "crawl", "website", "url", "source")):
            return "fetch_source"
        if any(token in text for token in ("extract", "parse", "facts", "claims")):
            return "extract_facts"
        if any(token in text for token in ("summarize", "summary", "digest")):
            return "summarize"
        if any(token in text for token in ("merge", "combine", "aggregate")):
            return "merge_results"
        if any(token in text for token in ("verify", "validate", "check")):
            return "verify"
        if any(token in text for token in ("compare", "versus", "vs")):
            return "compare_targets"
        if any(token in text for token in ("analyze", "clarify", "intent", "requirements")):
            return "analyze_request"
        if any(token in text for token in ("tool", "query", "search", "lookup")):
            return "tool_query"
        return "general"

    @staticmethod
    def _plan_step_requires_tools(*, description: str, step_payload: dict[str, Any], step_kind: str) -> bool:
        explicit = step_payload.get("requires_tools")
        if isinstance(explicit, bool):
            return explicit
        hints = step_payload.get("hints")
        if isinstance(hints, dict):
            hint_urls = hints.get("urls")
            if isinstance(hint_urls, list) and any(str(item).strip() for item in hint_urls):
                return True
        if step_kind in {"fetch_source", "tool_query"}:
            return True
        lowered = description.lower()
        return any(
            token in lowered
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
                "command",
            )
        )

    @staticmethod
    def _normalize_dependency_artifacts(raw: Any) -> dict[str, dict[str, Any]]:
        if not isinstance(raw, dict):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for issue_id, artifacts in raw.items():
            normalized_issue_id = str(issue_id).strip()
            if not normalized_issue_id or not isinstance(artifacts, dict):
                continue
            normalized: dict[str, Any] = {}
            for key, value in artifacts.items():
                normalized_key = str(key).strip()
                if not normalized_key or not isinstance(value, dict):
                    continue
                normalized[normalized_key] = dict(value)
            if normalized:
                result[normalized_issue_id] = normalized
        return result

    @staticmethod
    def _extract_dependency_points(dependency_artifacts: dict[str, dict[str, Any]]) -> list[str]:
        points: list[str] = []
        for issue_id in sorted(dependency_artifacts.keys()):
            artifacts = dependency_artifacts.get(issue_id) or {}
            for artifact in artifacts.values():
                if not isinstance(artifact, dict):
                    continue
                for key in (
                    "description",
                    "task_brief",
                    "objective",
                    "expected_output",
                    "query",
                    "subtask",
                ):
                    value = artifact.get(key)
                    if isinstance(value, str):
                        text = value.strip()
                        if text:
                            points.append(text)
                for key in (
                    "constraints",
                    "deliverables",
                    "extracted_points",
                    "summary_outline",
                    "verification_checklist",
                    "context_points",
                ):
                    value = artifact.get(key)
                    if isinstance(value, list):
                        for item in value:
                            text = str(item).strip()
                            if text:
                                points.append(text)
        seen: set[str] = set()
        deduped: list[str] = []
        for item in points:
            if item in seen:
                continue
            seen.add(item)
            deduped.append(item)
            if len(deduped) >= 24:
                break
        return deduped

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
        elif normalized_status in {ISSUE_DONE, ISSUE_RUNNING, ISSUE_PLANNED}:
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
