from __future__ import annotations

import csv
from io import StringIO
import logging
from queue import Empty, Queue
from threading import Event, Thread
from datetime import datetime, timedelta, timezone
import time
from typing import Any, Protocol
from uuid import uuid4

from agents.agent import Agent
from agents.run_policy import (
    KILL_SWITCH_STOP_REASON,
    RUN_RETRYABLE_FAILURE_CLASSES,
    classify_failure,
    resolve_retry_decision,
    retry_delay_seconds,
)
from kernel.contracts import ExecutorContract
from models.provider_errors import ProviderOperationError, classify_provider_error
from storage.database import Database
from tasks.task_executor import (
    STEP_PERSIST,
    STEP_PREPARE_CONTEXT,
    STEP_REASONING,
    TaskGuardrailError,
    TaskTimeoutError,
)


class TelemetrySink(Protocol):
    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        ...


class AutonomyCircuitBreakerContract(Protocol):
    def is_armed(self) -> bool:
        ...

    def snapshot(self) -> dict[str, Any]:
        ...


class RunBudgetExceededError(TaskGuardrailError):
    pass


class RunLeaseLostError(TaskGuardrailError):
    pass


CORE_ISSUE_DEFINITIONS: tuple[tuple[str, str, int, list[str]], ...] = (
    (STEP_PREPARE_CONTEXT, "Prepare context", 10, []),
    (STEP_REASONING, "Reasoning", 20, [STEP_PREPARE_CONTEXT]),
    (STEP_PERSIST, "Persist memory", 30, [STEP_REASONING]),
)

REPLAY_PRESET_STAGE_FILTERS: dict[str, tuple[str, ...]] = {
    "errors": (
        "error",
        "failed",
        "canceled",
        "attempt_timeout_guardrail",
        "artifact_quality_failed",
    ),
    "tools": (
        "tool_call_recorded",
        "tool_call_started",
        "tool_call_finished",
        "tool_call_error",
        "tool_call_reused",
    ),
    "verify": (
        "verification_repair_attempt",
        "verification_repair_success",
        "verification_repair_failed",
        "artifact_quality_evaluated",
        "artifact_repair_attempt",
        "artifact_quality_passed",
        "artifact_quality_failed",
    ),
}

BUDGET_GUARDRAIL_PAUSE_STOP_REASON = "budget_guardrail_paused"
BUDGET_GUARDRAIL_KILL_SWITCH_STOP_REASON = "budget_guardrail_kill_switch"
BUDGET_GUARDRAIL_KILL_SWITCH_THRESHOLD = 2


class AgentRunManager:
    def __init__(
        self,
        database: Database,
        task_executor: ExecutorContract,
        worker_count: int = 2,
        recover_pending_on_start: bool = True,
        default_max_attempts: int = 2,
        attempt_timeout_sec: float = 180.0,
        retry_backoff_sec: float = 0.3,
        retry_max_backoff_sec: float = 2.0,
        retry_jitter_sec: float = 0.15,
        run_budget_max_tokens: int = 24000,
        run_budget_max_duration_sec: float = 300.0,
        run_budget_max_tool_calls: int = 8,
        run_budget_max_tool_errors: int = 3,
        run_lease_ttl_sec: float | None = None,
        telemetry: TelemetrySink | None = None,
        autonomy_circuit_breaker: AutonomyCircuitBreakerContract | None = None,
    ) -> None:
        self.logger = logging.getLogger("amaryllis.agents.runs")
        self.database = database
        self.task_executor = task_executor
        self.worker_count = max(1, worker_count)
        self.recover_pending_on_start = bool(recover_pending_on_start)
        self.default_max_attempts = max(1, default_max_attempts)
        self.attempt_timeout_sec = max(5.0, float(attempt_timeout_sec))
        self.retry_backoff_sec = max(0.0, float(retry_backoff_sec))
        self.retry_max_backoff_sec = max(0.0, float(retry_max_backoff_sec))
        self.retry_jitter_sec = max(0.0, float(retry_jitter_sec))
        lease_floor = max(10.0, self.attempt_timeout_sec + 5.0)
        if run_lease_ttl_sec is None:
            self.run_lease_ttl_sec = max(lease_floor, self.attempt_timeout_sec * 2.0 + 5.0)
        else:
            self.run_lease_ttl_sec = max(lease_floor, float(run_lease_ttl_sec))
        self.run_lease_heartbeat_sec = max(1.0, min(self.run_lease_ttl_sec / 3.0, 15.0))
        self.run_lease_heartbeat_max_failures = 3
        self.default_run_budget = {
            "max_tokens": max(256, int(run_budget_max_tokens)),
            "max_duration_sec": max(10.0, float(run_budget_max_duration_sec)),
            "max_tool_calls": max(1, int(run_budget_max_tool_calls)),
            "max_tool_errors": max(0, int(run_budget_max_tool_errors)),
        }
        self.telemetry = telemetry
        self.autonomy_circuit_breaker = autonomy_circuit_breaker

        self._queue: Queue[str | None] = Queue()
        self._workers: list[Thread] = []
        self._stop = Event()
        self._started = False
        self._lease_owner = f"run-manager-{uuid4()}"

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stop.clear()
        if self.recover_pending_on_start:
            self._recover_pending_runs()
        for index in range(self.worker_count):
            worker = Thread(
                target=self._worker_loop,
                name=f"amaryllis-run-worker-{index + 1}",
                daemon=True,
            )
            worker.start()
            self._workers.append(worker)
        self.logger.info("run_workers_started count=%s", self.worker_count)

    def stop(self) -> None:
        if not self._started:
            return
        self._stop.set()
        for _ in self._workers:
            self._queue.put(None)
        for worker in self._workers:
            worker.join(timeout=2.0)
        self._workers.clear()
        self._started = False
        self.logger.info("run_workers_stopped")

    def _recover_pending_runs(self) -> None:
        recovered_running = self.database.list_agent_runs(status="running", limit=5000)
        queued = self.database.list_agent_runs(status="queued", limit=5000)
        requeued_ids: set[str] = set()
        recovered_count = 0

        for item in recovered_running:
            run_id = str(item.get("id") or "").strip()
            if not run_id:
                continue
            self.database.update_agent_run_fields(
                run_id,
                status="queued",
                error_message=None,
                stop_reason=None,
                failure_class=None,
                finished_at=None,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
            )
            self.database.append_agent_run_checkpoint(
                run_id=run_id,
                checkpoint={
                    "stage": "recovered_after_crash",
                    "message": "Recovered running run after runtime restart.",
                    "previous_status": "running",
                },
            )
            self._reset_issue_states_for_resume(run_id=run_id)
            self._queue.put(run_id)
            requeued_ids.add(run_id)
            recovered_count += 1

        for item in queued:
            run_id = str(item.get("id") or "").strip()
            if not run_id or run_id in requeued_ids:
                continue
            self.database.update_agent_run_fields(
                run_id,
                lease_owner=None,
                lease_token=None,
                lease_expires_at=None,
            )
            self._queue.put(run_id)
            requeued_ids.add(run_id)

        if recovered_count or requeued_ids:
            self.logger.info(
                "run_recovery_completed recovered_running=%s queued_reenqueued=%s",
                recovered_count,
                max(0, len(requeued_ids) - recovered_count),
            )

    def create_run(
        self,
        agent: Agent,
        user_id: str,
        session_id: str | None,
        user_message: str,
        max_attempts: int | None = None,
        budget: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        owner = str(agent.user_id or "").strip()
        actor = str(user_id or "").strip()
        if not owner or not actor or owner != actor:
            raise ValueError(f"Agent ownership mismatch for agent: {agent.id}")
        if self.autonomy_circuit_breaker is not None and self.autonomy_circuit_breaker.is_armed():
            snapshot_raw = self.autonomy_circuit_breaker.snapshot()
            snapshot = dict(snapshot_raw) if isinstance(snapshot_raw, dict) else {}
            details: list[str] = []
            armed_by = str(snapshot.get("armed_by") or "").strip()
            armed_at = str(snapshot.get("armed_at") or "").strip()
            reason = str(snapshot.get("reason") or "").strip()
            if armed_by:
                details.append(f"armed_by={armed_by}")
            if armed_at:
                details.append(f"armed_at={armed_at}")
            if reason:
                details.append(f"reason={reason}")
            detail_suffix = f" ({', '.join(details)})" if details else ""
            self._emit(
                "agent_run_blocked_autonomy_circuit_breaker",
                {
                    "agent_id": agent.id,
                    "user_id": user_id,
                    "session_id": session_id,
                    "autonomy_circuit_breaker": snapshot,
                },
            )
            raise ValueError(
                "Autonomy circuit breaker is armed. "
                f"New runs are temporarily blocked{detail_suffix}."
            )

        run_id = str(uuid4())
        attempts_limit = max(1, max_attempts or self.default_max_attempts)
        effective_budget = self._normalize_run_budget(budget)
        with self.database.write_transaction():
            self.database.create_agent_run(
                run_id=run_id,
                agent_id=agent.id,
                user_id=user_id,
                session_id=session_id,
                input_message=user_message,
                status="queued",
                max_attempts=attempts_limit,
                budget=effective_budget,
            )
            self.database.append_agent_run_checkpoint(
                run_id=run_id,
                checkpoint={
                    "stage": "queued",
                    "message": "Run queued for execution.",
                    "budget": effective_budget,
                },
            )
            self._ensure_core_issue_records(run_id=run_id)
        if self._started:
            self._queue.put(run_id)
        self._emit(
            "agent_run_queued",
            {
                "run_id": run_id,
                "agent_id": agent.id,
                "user_id": user_id,
                "session_id": session_id,
                "max_attempts": attempts_limit,
                "budget": effective_budget,
            },
        )
        run = self.database.get_agent_run(run_id)
        assert run is not None
        return run

    def list_runs(
        self,
        user_id: str | None = None,
        agent_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        return self.database.list_agent_runs(
            user_id=user_id,
            agent_id=agent_id,
            status=status,
            limit=limit,
        )

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return self.database.get_agent_run(run_id, include_issues=True, include_artifacts=True)

    def list_run_issues(self, run_id: str, limit: int = 200) -> list[dict[str, Any]]:
        return self.database.list_agent_run_issues(run_id=run_id, limit=max(1, min(int(limit), 1000)))

    def list_run_artifacts(
        self,
        run_id: str,
        *,
        issue_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        return self.database.list_agent_run_issue_artifacts(
            run_id=run_id,
            issue_id=issue_id,
            limit=max(1, min(int(limit), 5000)),
        )

    def list_run_tool_calls(
        self,
        run_id: str,
        *,
        status: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        return self.database.list_agent_run_tool_calls(
            run_id=run_id,
            status=status,
            limit=max(1, min(int(limit), 5000)),
        )

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        run = self.database.get_agent_run(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")

        with self.database.write_transaction():
            self.database.update_agent_run_fields(run_id, cancel_requested=1)
            status = str(run.get("status", ""))
            if status == "queued":
                self.database.update_agent_run_fields(
                    run_id,
                    status="canceled",
                    stop_reason="canceled_by_user",
                    failure_class="canceled",
                    finished_at=self._utc_now(),
                )
                self.database.append_agent_run_checkpoint(
                    run_id=run_id,
                    checkpoint={
                        "stage": "canceled",
                        "message": "Run canceled before execution.",
                        "stop_reason": "canceled_by_user",
                        "failure_class": "canceled",
                    },
                )
                self._finalize_open_issues(
                    run_id=run_id,
                    target_status="blocked",
                    attempt=max(1, int(run.get("attempts", 0))),
                    message="Run canceled by user before execution.",
                )
            else:
                self.database.append_agent_run_checkpoint(
                    run_id=run_id,
                    checkpoint={
                        "stage": "cancel_requested",
                        "message": "Cancel requested.",
                        "stop_reason": "cancel_requested",
                    },
                )
                self._finalize_open_issues(
                    run_id=run_id,
                    target_status="blocked",
                    attempt=max(1, int(run.get("attempts", 0))),
                    message="Run cancel requested by user.",
                )
        updated = self.database.get_agent_run(run_id)
        assert updated is not None
        self._emit(
            "agent_run_canceled",
            {
                "run_id": run_id,
                "status": updated.get("status"),
            },
        )
        return updated

    def kill_switch_runs(
        self,
        *,
        actor: str | None = None,
        reason: str | None = None,
        include_running: bool = True,
        include_queued: bool = True,
        limit: int = 5000,
        user_id: str | None = None,
        agent_id: str | None = None,
        exclude_run_id: str | None = None,
    ) -> dict[str, Any]:
        if not include_running and not include_queued:
            raise ValueError("Kill switch requires include_running and/or include_queued.")

        normalized_actor = str(actor or "").strip() or None
        normalized_reason = str(reason or "").strip()
        normalized_limit = max(1, min(int(limit), 50_000))
        scope_user_id = str(user_id or "").strip() or None
        scope_agent_id = str(agent_id or "").strip() or None
        excluded_run_id = str(exclude_run_id or "").strip() or None
        statuses: list[str] = []
        if include_running:
            statuses.append("running")
        if include_queued:
            statuses.append("queued")

        target_ids: list[str] = []
        seen_ids: set[str] = set()
        for status in statuses:
            rows = self.database.list_agent_runs(status=status, limit=normalized_limit)
            for row in rows:
                run_id = str(row.get("id") or "").strip()
                if not run_id or run_id in seen_ids:
                    continue
                if excluded_run_id is not None and run_id == excluded_run_id:
                    continue
                row_user_id = str(row.get("user_id") or "").strip() or None
                row_agent_id = str(row.get("agent_id") or "").strip() or None
                if scope_user_id is not None and row_user_id != scope_user_id:
                    continue
                if scope_agent_id is not None and row_agent_id != scope_agent_id:
                    continue
                seen_ids.add(run_id)
                target_ids.append(run_id)

        canceled_running = 0
        canceled_queued = 0
        now_iso = self._utc_now()
        with self.database.write_transaction():
            for run_id in target_ids:
                current = self.database.get_agent_run(run_id)
                if current is None:
                    continue
                current_status = str(current.get("status") or "").strip().lower()
                if current_status not in {"queued", "running"}:
                    continue

                self.database.update_agent_run_fields(
                    run_id,
                    cancel_requested=1,
                    status="canceled",
                    stop_reason=KILL_SWITCH_STOP_REASON,
                    failure_class="canceled",
                    finished_at=now_iso,
                    lease_owner=None,
                    lease_token=None,
                    lease_expires_at=None,
                )
                if current_status == "queued":
                    canceled_queued += 1
                    self.database.append_agent_run_checkpoint(
                        run_id=run_id,
                        checkpoint={
                            "stage": "canceled",
                            "message": "Run canceled by kill switch before execution.",
                            "stop_reason": KILL_SWITCH_STOP_REASON,
                            "failure_class": "canceled",
                            "actor": normalized_actor,
                            "reason": normalized_reason,
                        },
                    )
                    self._finalize_open_issues(
                        run_id=run_id,
                        target_status="blocked",
                        attempt=max(1, int(current.get("attempts", 0))),
                        message="Run canceled by kill switch before execution.",
                    )
                else:
                    canceled_running += 1
                    self.database.append_agent_run_checkpoint(
                        run_id=run_id,
                        checkpoint={
                            "stage": "kill_switch_triggered",
                            "message": "Kill switch triggered for running run. Lease revoked.",
                            "stop_reason": KILL_SWITCH_STOP_REASON,
                            "failure_class": "canceled",
                            "actor": normalized_actor,
                            "reason": normalized_reason,
                        },
                    )
                    self._finalize_open_issues(
                        run_id=run_id,
                        target_status="blocked",
                        attempt=max(1, int(current.get("attempts", 0))),
                        message="Run interrupted by kill switch.",
                    )

        canceled_total = canceled_running + canceled_queued
        preview_ids = target_ids[:200]
        self._emit(
            "agent_runs_kill_switch",
            {
                "actor": normalized_actor,
                "reason": normalized_reason,
                "include_running": bool(include_running),
                "include_queued": bool(include_queued),
                "scope_user_id": scope_user_id,
                "scope_agent_id": scope_agent_id,
                "exclude_run_id": excluded_run_id,
                "targeted_count": len(target_ids),
                "canceled_running": canceled_running,
                "canceled_queued": canceled_queued,
                "canceled_total": canceled_total,
            },
        )
        return {
            "actor": normalized_actor,
            "reason": normalized_reason,
            "include_running": bool(include_running),
            "include_queued": bool(include_queued),
            "scope_user_id": scope_user_id,
            "scope_agent_id": scope_agent_id,
            "exclude_run_id": excluded_run_id,
            "targeted_count": len(target_ids),
            "targeted_run_ids": preview_ids,
            "canceled_running": canceled_running,
            "canceled_queued": canceled_queued,
            "canceled_total": canceled_total,
        }

    def resume_run(self, run_id: str) -> dict[str, Any]:
        run = self.database.get_agent_run(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")

        status = str(run.get("status", ""))
        if status not in {"failed", "canceled"}:
            raise ValueError(f"Run {run_id} is not resumable (status={status})")
        resume_state = self._extract_resume_state(run)

        with self.database.write_transaction():
            self.database.update_agent_run_fields(
                run_id,
                status="queued",
                attempts=0,
                cancel_requested=0,
                error_message=None,
                stop_reason=None,
                failure_class=None,
                metrics_json={},
                started_at=None,
                finished_at=None,
            )
            self.database.append_agent_run_checkpoint(
                run_id=run_id,
                checkpoint={
                    "stage": "resumed",
                    "message": "Run resumed and queued again.",
                    "resume_steps": sorted(resume_state.get("completed_steps", [])) if resume_state else [],
                    "resume_state": resume_state or {},
                },
            )
            self._reset_issue_states_for_resume(run_id=run_id)
        if self._started:
            self._queue.put(run_id)

        updated = self.database.get_agent_run(run_id)
        assert updated is not None
        self._emit(
            "agent_run_resumed",
            {
                "run_id": run_id,
                "status": updated.get("status"),
            },
        )
        return updated

    def replay_run(self, run_id: str) -> dict[str, Any]:
        run = self.database.get_agent_run(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")

        raw_checkpoints = run.get("checkpoints")
        checkpoints = raw_checkpoints if isinstance(raw_checkpoints, list) else []

        timeline: list[dict[str, Any]] = []
        attempt_index: dict[int, int] = {}
        attempt_summary: list[dict[str, Any]] = []
        resume_snapshots: list[dict[str, Any]] = []

        for index, item in enumerate(checkpoints):
            if not isinstance(item, dict):
                continue

            timestamp = str(item.get("timestamp", ""))
            stage = str(item.get("stage", "")).strip() or "unknown"
            attempt = self._normalize_attempt(item.get("attempt"))
            message = str(item.get("message", "")).strip()

            event: dict[str, Any] = {
                "index": index + 1,
                "timestamp": timestamp,
                "stage": stage,
                "attempt": attempt,
                "message": message,
            }
            if "status" in item:
                event_status = str(item.get("status") or "").strip().lower()
                if event_status:
                    event["status"] = event_status
            if "retryable" in item:
                event["retryable"] = bool(item.get("retryable"))
            if "failure_class" in item:
                event["failure_class"] = str(item.get("failure_class") or "")
            if "stop_reason" in item:
                event["stop_reason"] = str(item.get("stop_reason") or "")
            timeline.append(event)

            resume_state = item.get("resume_state")
            if isinstance(resume_state, dict):
                completed_steps = resume_state.get("completed_steps")
                resume_snapshots.append(
                    {
                        "timestamp": timestamp,
                        "attempt": attempt,
                        "completed_steps": list(completed_steps) if isinstance(completed_steps, list) else [],
                    }
                )

            if attempt is None:
                continue

            summary_idx = attempt_index.get(attempt)
            if summary_idx is None:
                summary_idx = len(attempt_summary)
                attempt_index[attempt] = summary_idx
                attempt_summary.append(
                    {
                        "attempt": attempt,
                        "stage_counts": {},
                        "started_at": None,
                        "finished_at": None,
                        "tool_rounds": 0,
                        "verification_repairs": 0,
                        "errors": [],
                    }
                )

            summary = attempt_summary[summary_idx]
            stage_counts = summary["stage_counts"]
            assert isinstance(stage_counts, dict)
            stage_counts[stage] = int(stage_counts.get(stage, 0)) + 1

            if stage == "running" and summary.get("started_at") is None:
                summary["started_at"] = timestamp
            if stage in {"succeeded", "failed", "canceled"}:
                summary["finished_at"] = timestamp
            if stage == "tool_call_finished":
                if not bool(item.get("cached")) and item.get("executed") is not False:
                    summary["tool_rounds"] = int(summary.get("tool_rounds", 0)) + 1
            if stage == "verification_repair_attempt":
                summary["verification_repairs"] = int(summary.get("verification_repairs", 0)) + 1
            if stage in {"error", "failed"} and message:
                errors = summary["errors"]
                assert isinstance(errors, list)
                errors.append(message)

        latest_resume_state = self._extract_resume_state(run)
        issue_artifacts = self.database.list_agent_run_issue_artifacts(run_id=run_id, limit=2000)
        tool_call_rows = self.database.list_agent_run_tool_calls(run_id=run_id, limit=5000)
        artifact_counts: dict[str, int] = {}
        for item in issue_artifacts:
            issue_id = str(item.get("issue_id") or "unknown")
            artifact_counts[issue_id] = int(artifact_counts.get(issue_id, 0)) + 1
        tool_call_status_counts: dict[str, int] = {}
        for item in tool_call_rows:
            status = str(item.get("status") or "unknown").strip().lower() or "unknown"
            tool_call_status_counts[status] = int(tool_call_status_counts.get(status, 0)) + 1
        issue_items = self.database.list_agent_run_issues(run_id=run_id, limit=500)
        issue_status_breakdown: dict[str, int] = {}
        for issue in issue_items:
            status = str(issue.get("status") or "planned").strip().lower() or "planned"
            issue_status_breakdown[status] = int(issue_status_breakdown.get(status, 0)) + 1
        return {
            "run_id": str(run.get("id", run_id)),
            "agent_id": run.get("agent_id"),
            "user_id": run.get("user_id"),
            "session_id": run.get("session_id"),
            "status": run.get("status"),
            "stop_reason": run.get("stop_reason"),
            "failure_class": run.get("failure_class"),
            "attempts": int(run.get("attempts", 0)),
            "max_attempts": int(run.get("max_attempts", 0)),
            "budget": run.get("budget", {}),
            "metrics": run.get("metrics", {}),
            "checkpoint_count": len(timeline),
            "timeline": timeline,
            "attempt_summary": attempt_summary,
            "resume_snapshots": resume_snapshots,
            "latest_resume_state": latest_resume_state or None,
            "issues": issue_items,
            "issue_artifacts": issue_artifacts,
            "tool_calls": tool_call_rows,
            "issue_summary": {
                "count": len(issue_items),
                "status_breakdown": issue_status_breakdown,
                "artifact_count": len(issue_artifacts),
                "artifact_breakdown": artifact_counts,
                "tool_call_count": len(tool_call_rows),
                "tool_call_status_breakdown": tool_call_status_counts,
            },
            "has_result": run.get("result") is not None,
            "error_message": run.get("error_message"),
        }

    def replay_run_filtered(
        self,
        run_id: str,
        *,
        preset: str | None = None,
        stages: list[str] | None = None,
        statuses: list[str] | None = None,
        failure_classes: list[str] | None = None,
        retryable: bool | None = None,
        attempt: int | None = None,
        timeline_limit: int | None = None,
    ) -> dict[str, Any]:
        replay = self.replay_run(run_id)
        raw_timeline = replay.get("timeline")
        timeline = [item for item in raw_timeline if isinstance(item, dict)] if isinstance(raw_timeline, list) else []
        total_count = len(timeline)

        def _normalize_tokens(values: list[str] | None) -> set[str]:
            return {
                str(item).strip().lower()
                for item in (values or [])
                if str(item).strip()
            }

        preset_name = str(preset or "").strip().lower() or None
        preset_stage_filter = (
            _normalize_tokens(list(REPLAY_PRESET_STAGE_FILTERS.get(preset_name or "", ())))
            if preset_name
            else set()
        )
        stage_filter = {
            str(item).strip().lower()
            for item in (stages or [])
            if str(item).strip()
        }
        status_filter = _normalize_tokens(statuses)
        failure_class_filter = _normalize_tokens(failure_classes)

        filtered = timeline
        if preset_stage_filter:
            filtered = [
                item
                for item in filtered
                if str(item.get("stage") or "").strip().lower() in preset_stage_filter
            ]
        if stage_filter:
            filtered = [item for item in filtered if str(item.get("stage") or "").strip().lower() in stage_filter]

        if status_filter:
            filtered = [
                item
                for item in filtered
                if str(item.get("status") or "").strip().lower() in status_filter
            ]

        if failure_class_filter:
            filtered = [
                item
                for item in filtered
                if str(item.get("failure_class") or "").strip().lower() in failure_class_filter
            ]

        retryable_filter = retryable if isinstance(retryable, bool) else None
        if retryable_filter is not None:
            filtered = [
                item
                for item in filtered
                if "retryable" in item and bool(item.get("retryable")) is retryable_filter
            ]

        attempt_filter = int(attempt) if attempt is not None else None
        if attempt_filter is not None and attempt_filter > 0:
            filtered = [item for item in filtered if self._normalize_attempt(item.get("attempt")) == attempt_filter]

        limit = max(0, int(timeline_limit or 0))
        if limit > 0 and len(filtered) > limit:
            filtered = filtered[-limit:]

        replay_filtered = dict(replay)
        replay_filtered["timeline"] = filtered
        replay_filtered["timeline_total_count"] = total_count
        replay_filtered["timeline_filtered_count"] = len(filtered)
        replay_filtered["timeline_filters"] = {
            "preset": preset_name,
            "preset_stages": sorted(preset_stage_filter),
            "stages": sorted(stage_filter),
            "statuses": sorted(status_filter),
            "failure_classes": sorted(failure_class_filter),
            "retryable": retryable_filter,
            "attempt": attempt_filter,
            "timeline_limit": limit,
        }
        return replay_filtered

    def diagnose_run(self, run_id: str) -> dict[str, Any]:
        replay = self.replay_run(run_id)
        metrics = replay.get("metrics")
        metrics_dict = dict(metrics) if isinstance(metrics, dict) else {}

        attempts = max(0, int(replay.get("attempts", 0)))
        max_attempts = max(0, int(replay.get("max_attempts", 0)))
        status = str(replay.get("status") or "").strip().lower() or "unknown"
        stop_reason = str(replay.get("stop_reason") or "").strip().lower() or "none"
        failure_class = str(replay.get("failure_class") or "").strip().lower() or "none"

        checkpoint_count = max(0, int(replay.get("checkpoint_count", 0)))
        timeline = replay.get("timeline")
        stage_breakdown: dict[str, int] = {}
        if isinstance(timeline, list):
            for event in timeline:
                if not isinstance(event, dict):
                    continue
                stage = str(event.get("stage") or "").strip().lower() or "unknown"
                stage_breakdown[stage] = int(stage_breakdown.get(stage, 0)) + 1

        issue_summary = replay.get("issue_summary")
        issue_summary_dict = dict(issue_summary) if isinstance(issue_summary, dict) else {}
        status_breakdown_raw = issue_summary_dict.get("status_breakdown")
        status_breakdown = dict(status_breakdown_raw) if isinstance(status_breakdown_raw, dict) else {}
        blocked_issues = max(0, int(status_breakdown.get("blocked", 0)))

        tool_status_breakdown_raw = issue_summary_dict.get("tool_call_status_breakdown")
        tool_status_breakdown = (
            dict(tool_status_breakdown_raw) if isinstance(tool_status_breakdown_raw, dict) else {}
        )
        tool_call_total = 0
        tool_call_failures = 0
        for key, value in tool_status_breakdown.items():
            count = max(0, self._safe_int(value, 0))
            tool_call_total += count
            normalized_key = str(key or "").strip().lower()
            if normalized_key not in {"succeeded"}:
                tool_call_failures += count

        warnings: list[str] = []
        if attempts > 1:
            warnings.append("run_required_retries")
        if status in {"failed", "canceled"}:
            warnings.append("run_terminal_non_success")
        if blocked_issues > 0:
            warnings.append("issues_blocked")
        if tool_call_failures > 0:
            warnings.append("tool_failures_detected")
        if stop_reason == "max_attempts_exhausted":
            warnings.append("max_attempts_exhausted")
        if failure_class in {"timeout", "rate_limit", "network", "server", "unavailable", "circuit_open"}:
            warnings.append("transient_infra_failures")
        if failure_class == "budget_exceeded":
            warnings.append("budget_exceeded")
        if stop_reason == BUDGET_GUARDRAIL_PAUSE_STOP_REASON:
            warnings.append("budget_guardrail_paused")
        if stop_reason == BUDGET_GUARDRAIL_KILL_SWITCH_STOP_REASON:
            warnings.append("budget_guardrail_kill_switch")

        recommendations: list[str] = []
        if stop_reason == "max_attempts_exhausted":
            recommendations.append("Increase max_attempts or reduce upstream/provider instability.")
        if failure_class == "budget_exceeded":
            recommendations.append("Increase run budget limits or reduce token/tool usage per attempt.")
        if failure_class == "invalid_request":
            recommendations.append("Validate run input, tool schemas, and prompt contract before execution.")
        if failure_class == "guardrail":
            recommendations.append("Review guardrail thresholds and adjust policy only with explicit risk sign-off.")
        if stop_reason == BUDGET_GUARDRAIL_PAUSE_STOP_REASON:
            recommendations.append("Tune mission budget or task scope, then resume the run.")
        if stop_reason == BUDGET_GUARDRAIL_KILL_SWITCH_STOP_REASON:
            recommendations.append("Review sibling runs canceled by budget escalation and relaunch with stricter budgets.")
        if stop_reason == KILL_SWITCH_STOP_REASON:
            recommendations.append("Inspect kill-switch trigger source and restart mission with updated constraints.")
        if tool_call_failures > 0:
            recommendations.append("Inspect tool failure details and stabilize tool dependencies before rerun.")
        if not recommendations and status == "succeeded" and attempts == 1:
            recommendations.append("No corrective action required.")

        return {
            "run_id": str(replay.get("run_id") or run_id),
            "status": status,
            "stop_reason": stop_reason,
            "failure_class": failure_class,
            "attempts": attempts,
            "max_attempts": max_attempts,
            "metrics": metrics_dict,
            "issue_summary": issue_summary_dict,
            "timeline_summary": {
                "checkpoint_count": checkpoint_count,
                "stage_breakdown": stage_breakdown,
            },
            "diagnostics": {
                "warnings": warnings,
                "recommended_actions": recommendations,
                "signals": {
                    "blocked_issues": blocked_issues,
                    "tool_calls_total": tool_call_total,
                    "tool_call_failures": tool_call_failures,
                    "retry_count": max(0, attempts - 1),
                },
            },
        }

    def build_run_diagnostics_package(self, run_id: str) -> dict[str, Any]:
        replay = self.replay_run(run_id)
        diagnostics = self.diagnose_run(run_id)
        run_id_normalized = str(replay.get("run_id") or run_id)
        run_snapshot = {
            "run_id": run_id_normalized,
            "agent_id": replay.get("agent_id"),
            "user_id": replay.get("user_id"),
            "session_id": replay.get("session_id"),
            "status": replay.get("status"),
            "stop_reason": replay.get("stop_reason"),
            "failure_class": replay.get("failure_class"),
            "attempts": replay.get("attempts"),
            "max_attempts": replay.get("max_attempts"),
            "budget": replay.get("budget", {}),
            "metrics": replay.get("metrics", {}),
            "has_result": bool(replay.get("has_result")),
            "error_message": replay.get("error_message"),
        }

        replay_bundle = {
            "checkpoint_count": max(0, int(replay.get("checkpoint_count", 0))),
            "timeline": replay.get("timeline", []),
            "attempt_summary": replay.get("attempt_summary", []),
            "resume_snapshots": replay.get("resume_snapshots", []),
            "latest_resume_state": replay.get("latest_resume_state"),
        }

        evidence_bundle = {
            "issues": replay.get("issues", []),
            "issue_artifacts": replay.get("issue_artifacts", []),
            "tool_calls": replay.get("tool_calls", []),
            "issue_summary": replay.get("issue_summary", {}),
        }

        return {
            "package_version": "run-diagnostics.v1",
            "generated_at": self._utc_now(),
            "run": run_snapshot,
            "diagnostics": diagnostics,
            "replay": replay_bundle,
            "evidence": evidence_bundle,
        }

    @staticmethod
    def _plain_event_result(*, action: str, status: str) -> str:
        normalized_status = str(status or "").strip().lower()
        normalized_action = str(action or "").strip() or "Action"
        if normalized_status in {"succeeded", "success", "completed", "done"}:
            return f"{normalized_action} completed successfully."
        if normalized_status in {"failed", "error", "denied"}:
            return f"{normalized_action} failed."
        if normalized_status in {"canceled", "cancelled"}:
            return f"{normalized_action} was canceled."
        if normalized_status in {"queued", "running", "pending", "in_progress"}:
            return f"{normalized_action} is in progress."
        if normalized_status:
            return f"{normalized_action} status is {normalized_status}."
        return f"{normalized_action} status is unknown."

    @staticmethod
    def _plain_event_reason(*, event: dict[str, Any], policy_context: dict[str, Any]) -> str:
        message = str(event.get("message") or "").strip()
        if message:
            return message

        failure_class = str(policy_context.get("failure_class") or "").strip().lower()
        if failure_class:
            return f"Failure class: {failure_class}."

        stop_reason = str(policy_context.get("stop_reason") or "").strip().lower()
        if stop_reason:
            return f"Stop reason: {stop_reason}."

        stage = str(event.get("stage") or "").strip().lower()
        if stage:
            return f"Runtime reported stage '{stage}'."

        return "Runtime recorded this action event."

    @staticmethod
    def _plain_event_next_step(
        *,
        event: dict[str, Any],
        policy_context: dict[str, Any],
        fallback_next_step: str,
    ) -> str:
        channel = str(event.get("channel") or "").strip().lower()
        status = str(event.get("status") or "").strip().lower()
        action = str(event.get("action") or "").strip().lower()
        failure_class = str(policy_context.get("failure_class") or "").strip().lower()
        stop_reason = str(policy_context.get("stop_reason") or "").strip().lower()

        if channel == "security_audit" and ("deny" in action or status in {"failed", "denied"}):
            return "Review policy decision and request approval if this action is expected."
        if status in {"failed", "error", "denied"}:
            if failure_class:
                return f"Fix the {failure_class} issue and retry this step."
            if stop_reason:
                return f"Resolve stop reason '{stop_reason}' before retrying."
            return "Inspect this failed step and retry after fixing the root cause."
        if status in {"canceled", "cancelled"}:
            return "Confirm cancellation intent and relaunch only if still needed."
        if status in {"queued", "running", "pending", "in_progress"}:
            return "Wait for completion and monitor the next timeline event."
        if status in {"succeeded", "success", "completed", "done"}:
            return "Proceed to the next mission step."
        return fallback_next_step

    def build_run_explainability_feed(
        self,
        run_id: str,
        *,
        include_tool_calls: bool = True,
        include_security_actions: bool = True,
        limit: int = 2000,
    ) -> dict[str, Any]:
        audit = self.build_run_audit_timeline(
            run_id,
            include_tool_calls=include_tool_calls,
            include_security_actions=include_security_actions,
            limit=limit,
        )
        diagnostics = self.diagnose_run(run_id)
        diagnostics_payload = diagnostics.get("diagnostics")
        diagnostics_dict = dict(diagnostics_payload) if isinstance(diagnostics_payload, dict) else {}
        recommended_actions_raw = diagnostics_dict.get("recommended_actions")
        recommended_actions = [
            str(item).strip()
            for item in (recommended_actions_raw if isinstance(recommended_actions_raw, list) else [])
            if str(item).strip()
        ]
        fallback_next_step = recommended_actions[0] if recommended_actions else "Continue monitoring mission progress."

        raw_timeline = audit.get("timeline")
        timeline = raw_timeline if isinstance(raw_timeline, list) else []
        explain_items: list[dict[str, Any]] = []
        for index, event in enumerate(timeline, start=1):
            if not isinstance(event, dict):
                continue
            policy_context_raw = event.get("policy_context")
            policy_context = dict(policy_context_raw) if isinstance(policy_context_raw, dict) else {}
            action = str(event.get("action") or "").strip() or "action"
            status = str(event.get("status") or "").strip()
            explain_items.append(
                {
                    "index": index,
                    "event_id": str(event.get("event_id") or ""),
                    "timestamp": str(event.get("timestamp") or ""),
                    "channel": str(event.get("channel") or ""),
                    "action": action,
                    "status": status,
                    "reason": self._plain_event_reason(event=event, policy_context=policy_context),
                    "result": self._plain_event_result(action=action, status=status),
                    "next_step": self._plain_event_next_step(
                        event=event,
                        policy_context=policy_context,
                        fallback_next_step=fallback_next_step,
                    ),
                }
            )

        summary_payload = audit.get("summary")
        summary = dict(summary_payload) if isinstance(summary_payload, dict) else {}
        return {
            "feed_version": "run_explainability_feed_v1",
            "generated_at": self._utc_now(),
            "run_id": str(audit.get("run_id") or run_id),
            "agent_id": audit.get("agent_id"),
            "user_id": audit.get("user_id"),
            "session_id": audit.get("session_id"),
            "status": str(audit.get("status") or ""),
            "timeline_event_count": len(explain_items),
            "summary": {
                "channel_counts": dict(summary.get("channel_counts") or {}),
                "status_counts": dict(summary.get("status_counts") or {}),
                "terminal_stop_reason": summary.get("terminal_stop_reason"),
                "terminal_failure_class": summary.get("terminal_failure_class"),
                "recommended_actions": recommended_actions,
            },
            "items": explain_items,
        }

    def build_run_audit_timeline(
        self,
        run_id: str,
        *,
        include_tool_calls: bool = True,
        include_security_actions: bool = True,
        limit: int = 2000,
    ) -> dict[str, Any]:
        replay = self.replay_run(run_id)
        run_id_normalized = str(replay.get("run_id") or run_id).strip()
        if not run_id_normalized:
            raise ValueError(f"Run not found: {run_id}")

        timeline: list[dict[str, Any]] = []
        raw_timeline = replay.get("timeline")
        if isinstance(raw_timeline, list):
            for index, item in enumerate(raw_timeline, start=1):
                if not isinstance(item, dict):
                    continue
                timeline.append(
                    {
                        "event_id": f"checkpoint:{index}",
                        "timestamp": str(item.get("timestamp") or ""),
                        "channel": "run_checkpoint",
                        "stage": str(item.get("stage") or "unknown"),
                        "action": str(item.get("stage") or "unknown"),
                        "status": str(item.get("status") or ""),
                        "attempt": self._normalize_attempt(item.get("attempt")),
                        "actor": str(item.get("actor") or ""),
                        "target_type": "agent_run",
                        "target_id": run_id_normalized,
                        "policy_context": {
                            "stop_reason": str(item.get("stop_reason") or ""),
                            "failure_class": str(item.get("failure_class") or ""),
                            "retryable": bool(item.get("retryable")) if "retryable" in item else None,
                        },
                        "message": str(item.get("message") or ""),
                    }
                )

        if include_tool_calls:
            tool_call_rows = self.database.list_agent_run_tool_calls(run_id=run_id_normalized, limit=5000)
            for index, row in enumerate(tool_call_rows, start=1):
                if not isinstance(row, dict):
                    continue
                timeline.append(
                    {
                        "event_id": f"tool_call:{index}",
                        "timestamp": str(row.get("updated_at") or row.get("created_at") or ""),
                        "channel": "tool_call",
                        "stage": "tool_call",
                        "action": str(row.get("tool_name") or "unknown"),
                        "status": str(row.get("status") or "unknown"),
                        "attempt": self._normalize_attempt(row.get("attempt")),
                        "actor": "",
                        "target_type": "agent_run",
                        "target_id": run_id_normalized,
                        "policy_context": {
                            "idempotency_key": str(row.get("idempotency_key") or ""),
                        },
                        "message": str(row.get("error_message") or ""),
                    }
                )

        if include_security_actions:
            security_limit = max(1000, int(limit) * 4)
            security_events = self.database.list_security_audit_events(limit=security_limit)
            for index, row in enumerate(security_events, start=1):
                if not isinstance(row, dict):
                    continue
                target_type = str(row.get("target_type") or "")
                target_id = str(row.get("target_id") or "")
                details = dict(row.get("details") or {})
                details_run_id = str(details.get("run_id") or "").strip()
                if not (
                    (target_type == "agent_run" and target_id == run_id_normalized)
                    or details_run_id == run_id_normalized
                ):
                    continue
                timeline.append(
                    {
                        "event_id": f"security:{index}",
                        "timestamp": str(row.get("created_at") or ""),
                        "channel": "security_audit",
                        "stage": "security_action",
                        "action": str(row.get("action") or ""),
                        "status": str(row.get("status") or ""),
                        "attempt": None,
                        "actor": str(row.get("actor") or ""),
                        "target_type": target_type,
                        "target_id": target_id,
                        "policy_context": {
                            "event_type": str(row.get("event_type") or ""),
                            "request_id": str(row.get("request_id") or ""),
                        },
                        "message": str(details.get("message") or details.get("error") or ""),
                    }
                )

        timeline.sort(
            key=lambda item: (
                str(item.get("timestamp") or ""),
                str(item.get("channel") or ""),
                str(item.get("event_id") or ""),
            )
        )
        max_items = max(1, min(int(limit), 20_000))
        if len(timeline) > max_items:
            timeline = timeline[-max_items:]

        channel_counts: dict[str, int] = {}
        status_counts: dict[str, int] = {}
        for event in timeline:
            channel = str(event.get("channel") or "unknown")
            status = str(event.get("status") or "").strip().lower()
            channel_counts[channel] = int(channel_counts.get(channel, 0)) + 1
            if status:
                status_counts[status] = int(status_counts.get(status, 0)) + 1

        return {
            "run_id": run_id_normalized,
            "agent_id": replay.get("agent_id"),
            "user_id": replay.get("user_id"),
            "session_id": replay.get("session_id"),
            "status": replay.get("status"),
            "generated_at": self._utc_now(),
            "timeline": timeline,
            "event_count": len(timeline),
            "summary": {
                "channel_counts": channel_counts,
                "status_counts": status_counts,
                "include_tool_calls": bool(include_tool_calls),
                "include_security_actions": bool(include_security_actions),
                "terminal_stop_reason": replay.get("stop_reason"),
                "terminal_failure_class": replay.get("failure_class"),
            },
        }

    def export_run_audit_timeline(
        self,
        run_id: str,
        *,
        export_format: str = "json",
        include_tool_calls: bool = True,
        include_security_actions: bool = True,
        limit: int = 2000,
    ) -> dict[str, Any]:
        audit = self.build_run_audit_timeline(
            run_id,
            include_tool_calls=include_tool_calls,
            include_security_actions=include_security_actions,
            limit=limit,
        )
        normalized_format = str(export_format or "json").strip().lower() or "json"
        if normalized_format not in {"json", "csv"}:
            raise ValueError(f"Unsupported export format: {export_format}")
        if normalized_format == "json":
            return {
                "format": "json",
                "filename": f"run-audit-{str(audit.get('run_id') or run_id)}.json",
                "content_type": "application/json",
                "payload": audit,
            }

        rows = list(audit.get("timeline") or [])
        output = StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=[
                "timestamp",
                "channel",
                "event_id",
                "stage",
                "action",
                "status",
                "attempt",
                "actor",
                "target_type",
                "target_id",
                "message",
                "stop_reason",
                "failure_class",
                "request_id",
            ],
        )
        writer.writeheader()
        for item in rows:
            if not isinstance(item, dict):
                continue
            policy_context = dict(item.get("policy_context") or {})
            writer.writerow(
                {
                    "timestamp": str(item.get("timestamp") or ""),
                    "channel": str(item.get("channel") or ""),
                    "event_id": str(item.get("event_id") or ""),
                    "stage": str(item.get("stage") or ""),
                    "action": str(item.get("action") or ""),
                    "status": str(item.get("status") or ""),
                    "attempt": str(item.get("attempt") if item.get("attempt") is not None else ""),
                    "actor": str(item.get("actor") or ""),
                    "target_type": str(item.get("target_type") or ""),
                    "target_id": str(item.get("target_id") or ""),
                    "message": str(item.get("message") or ""),
                    "stop_reason": str(policy_context.get("stop_reason") or ""),
                    "failure_class": str(policy_context.get("failure_class") or ""),
                    "request_id": str(policy_context.get("request_id") or ""),
                }
            )
        csv_content = output.getvalue()
        return {
            "format": "csv",
            "filename": f"run-audit-{str(audit.get('run_id') or run_id)}.csv",
            "content_type": "text/csv; charset=utf-8",
            "content": csv_content,
            "line_count": max(0, len(csv_content.splitlines())),
        }

    def get_run_health(
        self,
        *,
        user_id: str | None = None,
        agent_id: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        runs = self.database.list_agent_runs(
            user_id=user_id,
            agent_id=agent_id,
            status=None,
            limit=max(1, min(int(limit), 2000)),
        )
        total_runs = len(runs)
        terminal = [item for item in runs if str(item.get("status", "")).lower() in {"succeeded", "failed", "canceled"}]
        succeeded = sum(1 for item in terminal if str(item.get("status", "")).lower() == "succeeded")
        failed = sum(1 for item in terminal if str(item.get("status", "")).lower() == "failed")
        canceled = sum(1 for item in terminal if str(item.get("status", "")).lower() == "canceled")
        retry_runs = sum(1 for item in terminal if int(item.get("attempts", 0)) > 1)

        run_durations_ms: list[float] = []
        attempts_per_run: list[int] = []
        stop_reason_counts: dict[str, int] = {}
        failure_class_counts: dict[str, int] = {}
        run_attempt_durations_ms: list[float] = []
        run_attempt_successes = 0
        run_attempt_total = 0
        tool_call_durations_ms: list[float] = []
        tool_call_successes = 0
        tool_call_total = 0
        verification_repair_total = 0
        issue_status_breakdown: dict[str, int] = {}
        runs_with_blocked_issues = 0

        for run in runs:
            attempts_per_run.append(max(0, int(run.get("attempts", 0))))
            stop_reason = str(run.get("stop_reason") or "").strip() or "none"
            failure_class = str(run.get("failure_class") or "").strip() or "none"
            stop_reason_counts[stop_reason] = int(stop_reason_counts.get(stop_reason, 0)) + 1
            failure_class_counts[failure_class] = int(failure_class_counts.get(failure_class, 0)) + 1

            duration = self._duration_ms(started_at=run.get("started_at"), finished_at=run.get("finished_at"))
            if duration is not None:
                run_durations_ms.append(duration)

            run_issues = self.database.list_agent_run_issues(run_id=str(run.get("id")), limit=500)
            blocked_for_run = False
            for issue in run_issues:
                issue_status = str(issue.get("status") or "planned").strip().lower() or "planned"
                issue_status_breakdown[issue_status] = int(issue_status_breakdown.get(issue_status, 0)) + 1
                if issue_status == "blocked":
                    blocked_for_run = True
            if blocked_for_run:
                runs_with_blocked_issues += 1

            checkpoints = run.get("checkpoints")
            if not isinstance(checkpoints, list):
                continue

            running_by_attempt: dict[int, str] = {}
            terminal_by_attempt: dict[int, str] = {}
            terminal_stage_by_attempt: dict[int, str] = {}

            for item in checkpoints:
                if not isinstance(item, dict):
                    continue
                stage = str(item.get("stage", "")).strip()
                attempt = self._normalize_attempt(item.get("attempt"))
                timestamp = str(item.get("timestamp", "")).strip()

                if attempt is not None and stage == "running":
                    running_by_attempt.setdefault(attempt, timestamp)
                if attempt is not None and stage in {"succeeded", "failed", "canceled"}:
                    terminal_by_attempt[attempt] = timestamp
                    terminal_stage_by_attempt[attempt] = stage
                if stage == "tool_call_finished":
                    if not bool(item.get("cached")) and item.get("executed") is not False:
                        tool_call_total += 1
                        status = str(item.get("status", "")).strip().lower()
                        if status == "succeeded":
                            tool_call_successes += 1
                        try:
                            duration_ms = float(item.get("duration_ms", 0.0))
                        except Exception:
                            duration_ms = 0.0
                        if duration_ms > 0:
                            tool_call_durations_ms.append(duration_ms)
                if stage == "verification_repair_attempt":
                    verification_repair_total += 1

            for attempt, started_at in running_by_attempt.items():
                finished_at = terminal_by_attempt.get(attempt)
                if not finished_at:
                    continue
                attempt_duration = self._duration_ms(started_at=started_at, finished_at=finished_at)
                if attempt_duration is not None:
                    run_attempt_durations_ms.append(attempt_duration)
                run_attempt_total += 1
                if terminal_stage_by_attempt.get(attempt) == "succeeded":
                    run_attempt_successes += 1

        terminal_count = len(terminal)
        success_rate = (succeeded / terminal_count) if terminal_count else 0.0
        retry_rate = (retry_runs / terminal_count) if terminal_count else 0.0

        return {
            "sample_size": total_runs,
            "terminal_runs": terminal_count,
            "status_breakdown": {
                "succeeded": succeeded,
                "failed": failed,
                "canceled": canceled,
            },
            "issue_status_breakdown": issue_status_breakdown,
            "runs_with_blocked_issues": runs_with_blocked_issues,
            "success_rate": round(success_rate, 6),
            "retry_rate": round(retry_rate, 6),
            "stop_reason_breakdown": stop_reason_counts,
            "failure_class_breakdown": failure_class_counts,
            "slo": {
                "run": {
                    "success_rate": round(success_rate, 6),
                    "retry_rate": round(retry_rate, 6),
                    "duration_ms": self._distribution(run_durations_ms),
                    "attempts_per_run": self._distribution([float(item) for item in attempts_per_run]),
                },
                "run_attempt": {
                    "count": run_attempt_total,
                    "success_rate": round((run_attempt_successes / run_attempt_total), 6)
                    if run_attempt_total
                    else 0.0,
                    "duration_ms": self._distribution(run_attempt_durations_ms),
                },
                "tool_call": {
                    "count": tool_call_total,
                    "success_rate": round((tool_call_successes / tool_call_total), 6) if tool_call_total else 0.0,
                    "duration_ms": self._distribution(tool_call_durations_ms),
                },
                "verification": {
                    "repair_attempts": verification_repair_total,
                },
            },
        }

    def _worker_loop(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._queue.get(timeout=0.5)
            except Empty:
                continue

            if item is None:
                self._queue.task_done()
                break

            lease_token: str | None = None
            try:
                lease_token = self._process_run(item)
            except Exception as exc:
                self.logger.exception("run_worker_unhandled run_id=%s error=%s", item, exc)
            finally:
                self._release_run_lease(run_id=str(item), lease_token=lease_token)
                self._queue.task_done()

    def _process_run(self, run_id: str) -> str | None:
        run = self.database.get_agent_run(run_id)
        if run is None:
            return None
        lease_token = str(uuid4())
        claimed = self.database.claim_agent_run_lease(
            run_id=run_id,
            lease_owner=self._lease_owner,
            lease_token=lease_token,
            lease_expires_at=self._run_lease_expiry_iso(),
            allowed_statuses=("queued", "running"),
        )
        if claimed is None:
            self._emit(
                "agent_run_claim_skipped",
                {
                    "run_id": run_id,
                },
            )
            return None
        run = claimed

        latest_before_start = self.database.get_agent_run(run_id)
        budget = self._normalize_run_budget((latest_before_start or run).get("budget"))
        metrics_base = self._normalize_run_metrics((latest_before_start or run).get("metrics"))

        if latest_before_start is not None:
            canceled_before_start = int(latest_before_start.get("cancel_requested", 0)) == 1 or str(
                latest_before_start.get("status") or ""
            ).strip().lower() == "canceled"
        else:
            canceled_before_start = int(run.get("cancel_requested", 0)) == 1
        if canceled_before_start:
            cancel_stop_reason = self._resolve_cancel_stop_reason(latest_before_start or run)
            with self.database.write_transaction():
                self.database.update_agent_run_fields(
                    run_id,
                    status="canceled",
                    stop_reason=cancel_stop_reason,
                    failure_class="canceled",
                    metrics_json=metrics_base,
                    finished_at=self._utc_now(),
                )
                self.database.append_agent_run_checkpoint(
                    run_id=run_id,
                    checkpoint={
                        "stage": "canceled",
                        "message": "Run canceled before worker execution.",
                        "stop_reason": cancel_stop_reason,
                        "failure_class": "canceled",
                    },
                )
            self._emit(
                "agent_run_canceled",
                {
                    "run_id": run_id,
                    "agent_id": str(run.get("agent_id") or ""),
                    "status": "canceled",
                    "stop_reason": cancel_stop_reason,
                    "failure_class": "canceled",
                    "duration_ms": 0.0,
                },
            )
            return lease_token

        status = str(run.get("status", ""))
        if status not in {"queued", "running"}:
            return lease_token

        agent_record = self.database.get_agent(str(run["agent_id"]))
        if agent_record is None:
            error_message = f"Agent not found: {run['agent_id']}"
            with self.database.write_transaction():
                self.database.update_agent_run_fields(
                    run_id,
                    status="failed",
                    stop_reason="agent_not_found",
                    failure_class="not_found",
                    error_message=error_message,
                    metrics_json=metrics_base,
                    finished_at=self._utc_now(),
                )
                self.database.append_agent_run_checkpoint(
                    run_id=run_id,
                    checkpoint={
                        "stage": "failed",
                        "message": error_message,
                        "stop_reason": "agent_not_found",
                        "failure_class": "not_found",
                        "retryable": False,
                    },
                )
                self._mark_issue_failed_from_error(
                    run_id=run_id,
                    attempt=max(1, int(run.get("attempts", 0)) + 1),
                    error_message=error_message,
                )
            self._emit(
                "agent_run_failed",
                {
                    "run_id": run_id,
                    "agent_id": str(run.get("agent_id") or ""),
                    "status": "failed",
                    "stop_reason": "agent_not_found",
                    "failure_class": "not_found",
                    "duration_ms": 0.0,
                },
            )
            return lease_token

        agent = Agent.from_record(agent_record)
        attempt = int(run.get("attempts", 0)) + 1
        max_attempts = int(run.get("max_attempts", self.default_max_attempts))
        started_at = str(run.get("started_at") or "").strip()
        if not started_at:
            started_at = self._utc_now()
        if self._remaining_duration_sec(started_at=started_at, budget=budget) <= 0.0:
            error_message = "Run duration budget exceeded before attempt start."
            metrics_final = self._finalize_run_metrics(
                metrics=metrics_base,
                attempt=attempt,
                attempt_duration_ms=0.0,
            )
            with self.database.write_transaction():
                self.database.update_agent_run_fields(
                    run_id,
                    status="failed",
                    stop_reason="budget_exceeded",
                    failure_class="budget_exceeded",
                    error_message=error_message,
                    metrics_json=metrics_final,
                    finished_at=self._utc_now(),
                )
                self.database.append_agent_run_checkpoint(
                    run_id=run_id,
                    checkpoint={
                        "stage": "failed",
                        "attempt": attempt,
                        "message": error_message,
                        "retryable": False,
                        "stop_reason": "budget_exceeded",
                        "failure_class": "budget_exceeded",
                    },
                )
            self._emit(
                "agent_run_failed",
                {
                    "run_id": run_id,
                    "agent_id": str(run.get("agent_id") or ""),
                    "status": "failed",
                    "stop_reason": "budget_exceeded",
                    "failure_class": "budget_exceeded",
                    "duration_ms": 0.0,
                },
            )
            return lease_token

        with self.database.write_transaction():
            self.database.update_agent_run_fields(
                run_id,
                status="running",
                attempts=attempt,
                started_at=started_at,
                stop_reason=None,
                failure_class=None,
                error_message=None,
                metrics_json=metrics_base,
            )
            self.database.append_agent_run_checkpoint(
                run_id=run_id,
                checkpoint={
                    "stage": "running",
                    "attempt": attempt,
                    "message": f"Execution started (attempt {attempt}/{max_attempts}).",
                    "attempt_timeout_sec": self.attempt_timeout_sec,
                    "run_budget": budget,
                    "metrics_baseline": metrics_base,
                },
            )

        attempt_started_monotonic = time.monotonic()
        live_usage = {
            "estimated_tokens": int(metrics_base.get("estimated_tokens", 0)),
            "tool_calls": int(metrics_base.get("tool_calls", 0)),
            "tool_errors": int(metrics_base.get("tool_errors", 0)),
        }

        try:
            def push_checkpoint(payload: dict[str, Any]) -> None:
                data = dict(payload)
                data.setdefault("attempt", attempt)
                self._merge_checkpoint_usage(live_usage=live_usage, checkpoint=data)
                self._validate_live_budget_usage(budget=budget, usage=live_usage)
                issue_update = self._derive_issue_update_from_checkpoint(
                    run_id=run_id,
                    checkpoint=data,
                    attempt=attempt,
                )
                issue_artifact = self._derive_issue_artifact_from_checkpoint(checkpoint=data)
                tool_call_record = self._derive_tool_call_record_from_checkpoint(checkpoint=data)
                self.database.append_agent_run_checkpoint(
                    run_id=run_id,
                    checkpoint=data,
                    issue_update=issue_update,
                    issue_artifact=issue_artifact,
                    tool_call_record=tool_call_record,
                )

            resume_state = self._extract_resume_state(run)
            resume_state = self._merge_persisted_issue_artifacts(run_id=run_id, resume_state=resume_state)
            resume_state = self._merge_persisted_tool_call_cache(run_id=run_id, resume_state=resume_state)
            result = self._run_task_executor(
                run=run,
                agent=agent,
                attempt=attempt,
                started_at=started_at,
                budget=budget,
                run_id=run_id,
                lease_token=lease_token,
                checkpoint=push_checkpoint,
                resume_state=resume_state,
            )
        except Exception as exc:
            error_message = str(exc)
            failure = classify_failure(
                exc,
                run_budget_error_type=RunBudgetExceededError,
                run_lease_lost_error_type=RunLeaseLostError,
                task_timeout_error_type=TaskTimeoutError,
                task_guardrail_error_type=TaskGuardrailError,
                provider_operation_error_type=ProviderOperationError,
                provider_error_classifier=classify_provider_error,
                retryable_failure_classes=RUN_RETRYABLE_FAILURE_CLASSES,
            )
            retryable = bool(failure.retryable)
            failure_class = str(failure.failure_class)
            stop_reason = str(failure.stop_reason)
            attempt_duration_ms = round((time.monotonic() - attempt_started_monotonic) * 1000.0, 2)
            metrics_after_error = self._finalize_run_metrics(
                metrics=live_usage,
                attempt=attempt,
                attempt_duration_ms=attempt_duration_ms,
            )
            latest_after_error = self.database.get_agent_run(run_id)
            canceled = False
            if latest_after_error is not None:
                latest_status = str(latest_after_error.get("status") or "").strip().lower()
                canceled = int(latest_after_error.get("cancel_requested", 0)) == 1 or latest_status == "canceled"
            else:
                canceled = int(run.get("cancel_requested", 0)) == 1
            cancel_stop_reason = self._resolve_cancel_stop_reason(latest_after_error or run)
            retry_decision = resolve_retry_decision(
                attempt=attempt,
                max_attempts=max_attempts,
                retryable=retryable,
                canceled=canceled,
                stop_reason=stop_reason,
                failure_class=failure_class,
                cancel_stop_reason=cancel_stop_reason,
            )
            schedule_retry = bool(retry_decision.schedule_retry)
            backoff_sec = (
                retry_delay_seconds(
                    attempt=attempt,
                    retry_backoff_sec=self.retry_backoff_sec,
                    retry_max_backoff_sec=self.retry_max_backoff_sec,
                    retry_jitter_sec=self.retry_jitter_sec,
                )
                if schedule_retry
                else 0.0
            )
            final_status = retry_decision.final_status
            final_failure_class = retry_decision.final_failure_class
            final_stop_reason = retry_decision.final_stop_reason
            budget_guardrail_checkpoint: dict[str, Any] | None = None
            trigger_scope_kill_switch = False
            if not canceled and failure_class == "budget_exceeded":
                prior_budget_breach_count = self._count_budget_guardrail_breaches(latest_after_error or run)
                current_budget_breach_count = prior_budget_breach_count + 1
                if current_budget_breach_count >= BUDGET_GUARDRAIL_KILL_SWITCH_THRESHOLD:
                    schedule_retry = False
                    backoff_sec = 0.0
                    retryable = False
                    final_status = "canceled"
                    final_failure_class = "canceled"
                    final_stop_reason = BUDGET_GUARDRAIL_KILL_SWITCH_STOP_REASON
                    trigger_scope_kill_switch = True
                    budget_guardrail_checkpoint = {
                        "stage": "budget_guardrail_escalated",
                        "attempt": attempt,
                        "message": (
                            "Budget guardrail breached repeatedly. "
                            "Escalation moved mission to agent-scope kill switch."
                        ),
                        "failure_class": "budget_exceeded",
                        "stop_reason": final_stop_reason,
                        "breach_count": current_budget_breach_count,
                        "escalation_action": "kill_switch",
                        "escalation_scope": "agent",
                    }
                else:
                    schedule_retry = False
                    backoff_sec = 0.0
                    retryable = False
                    final_status = "failed"
                    final_failure_class = "budget_exceeded"
                    final_stop_reason = BUDGET_GUARDRAIL_PAUSE_STOP_REASON
                    budget_guardrail_checkpoint = {
                        "stage": "budget_guardrail_paused",
                        "attempt": attempt,
                        "message": "Budget guardrail breached. Mission paused for manual budget/scope adjustment.",
                        "failure_class": "budget_exceeded",
                        "stop_reason": final_stop_reason,
                        "breach_count": current_budget_breach_count,
                        "escalation_action": "pause",
                        "resume_hint": "Adjust budget or scope and call resume run.",
                    }
            with self.database.write_transaction():
                self.database.append_agent_run_checkpoint(
                    run_id=run_id,
                    checkpoint={
                        "stage": "error",
                        "attempt": attempt,
                        "message": error_message,
                        "retryable": retryable,
                        "failure_class": failure_class,
                        "stop_reason": stop_reason,
                        "estimated_tokens_total": metrics_after_error["estimated_tokens"],
                        "tool_calls_total": metrics_after_error["tool_calls"],
                        "tool_errors_total": metrics_after_error["tool_errors"],
                    },
                )
                self._mark_issue_failed_from_error(
                    run_id=run_id,
                    attempt=attempt,
                    error_message=error_message,
                )
                if budget_guardrail_checkpoint is not None:
                    self.database.append_agent_run_checkpoint(
                        run_id=run_id,
                        checkpoint=budget_guardrail_checkpoint,
                    )

                if schedule_retry:
                    self.database.update_agent_run_fields(
                        run_id,
                        status="queued",
                        error_message=error_message,
                        stop_reason=stop_reason,
                        failure_class=failure_class,
                        metrics_json=metrics_after_error,
                    )
                else:
                    if final_status is None or final_failure_class is None or final_stop_reason is None:
                        raise AssertionError("Retry policy produced invalid terminal decision.")
                    self.database.update_agent_run_fields(
                        run_id,
                        status=final_status,
                        error_message=error_message,
                        stop_reason=final_stop_reason,
                        failure_class=final_failure_class,
                        metrics_json=metrics_after_error,
                        finished_at=self._utc_now(),
                    )
                    if final_status == "canceled":
                        self._finalize_open_issues(
                            run_id=run_id,
                            target_status="blocked",
                            attempt=attempt,
                            message="Run canceled before issue completion.",
                        )

                if schedule_retry:
                    self.database.append_agent_run_checkpoint(
                        run_id=run_id,
                        checkpoint={
                            "stage": "retry_scheduled",
                            "attempt": attempt + 1,
                            "message": "Retry scheduled.",
                            "backoff_sec": backoff_sec,
                            "retryable": retryable,
                            "failure_class": failure_class,
                            "stop_reason": stop_reason,
                        },
                    )
                else:
                    if final_status is None or final_failure_class is None or final_stop_reason is None:
                        raise AssertionError("Retry policy produced invalid terminal checkpoint.")
                    self.database.append_agent_run_checkpoint(
                        run_id=run_id,
                        checkpoint={
                            "stage": final_status,
                            "attempt": attempt,
                            "message": error_message,
                            "retryable": retryable,
                            "failure_class": final_failure_class,
                            "stop_reason": final_stop_reason,
                        },
                    )

            if schedule_retry:
                if backoff_sec > 0:
                    time.sleep(backoff_sec)
                self._queue.put(run_id)
            else:
                if trigger_scope_kill_switch:
                    kill_switch_summary = self.kill_switch_runs(
                        actor="system:budget_guardrail",
                        reason=(
                            "Budget guardrail escalation triggered by repeated mission budget breaches."
                        ),
                        include_running=True,
                        include_queued=True,
                        limit=5000,
                        user_id=str(run.get("user_id") or ""),
                        agent_id=str(run.get("agent_id") or ""),
                        exclude_run_id=run_id,
                    )
                    self.database.append_agent_run_checkpoint(
                        run_id=run_id,
                        checkpoint={
                            "stage": "budget_guardrail_kill_switch_scope",
                            "attempt": attempt,
                            "message": "Agent-scope kill switch applied after repeated budget breaches.",
                            "scope_user_id": str(run.get("user_id") or ""),
                            "scope_agent_id": str(run.get("agent_id") or ""),
                            "canceled_running": int(kill_switch_summary.get("canceled_running", 0)),
                            "canceled_queued": int(kill_switch_summary.get("canceled_queued", 0)),
                            "canceled_total": int(kill_switch_summary.get("canceled_total", 0)),
                        },
                    )
                if final_status is None or final_failure_class is None or final_stop_reason is None:
                    raise AssertionError("Retry policy produced invalid terminal emit payload.")
                self._emit(
                    "agent_run_canceled" if final_status == "canceled" else "agent_run_failed",
                    {
                        "run_id": run_id,
                        "agent_id": str(run.get("agent_id") or ""),
                        "status": final_status,
                        "stop_reason": final_stop_reason,
                        "failure_class": final_failure_class,
                        "duration_ms": attempt_duration_ms,
                    },
                )
            return lease_token

        latest = self.database.get_agent_run(run_id)
        metrics_final = self._extract_result_metrics(result=result, fallback=live_usage, attempt=attempt)
        if latest is not None and int(latest.get("cancel_requested", 0)) == 1:
            cancel_stop_reason = self._resolve_cancel_stop_reason(latest)
            cancel_message = "Execution completed but run was canceled."
            if cancel_stop_reason == KILL_SWITCH_STOP_REASON:
                cancel_message = "Execution completed but run was interrupted by kill switch."
            with self.database.write_transaction():
                self.database.update_agent_run_fields(
                    run_id,
                    status="canceled",
                    stop_reason=cancel_stop_reason,
                    failure_class="canceled",
                    result_json=result,
                    metrics_json=metrics_final,
                    finished_at=self._utc_now(),
                )
                self.database.append_agent_run_checkpoint(
                    run_id=run_id,
                    checkpoint={
                        "stage": "canceled",
                        "attempt": attempt,
                        "message": cancel_message,
                        "failure_class": "canceled",
                        "stop_reason": cancel_stop_reason,
                    },
                )
                self._finalize_open_issues(
                    run_id=run_id,
                    target_status="blocked",
                    attempt=attempt,
                    message=cancel_message,
                )
            self._emit(
                "agent_run_canceled",
                {
                    "run_id": run_id,
                    "agent_id": agent.id,
                    "status": "canceled",
                    "stop_reason": cancel_stop_reason,
                    "failure_class": "canceled",
                    "duration_ms": float(metrics_final.get("total_attempt_duration_ms", 0.0)),
                },
            )
            return lease_token

        with self.database.write_transaction():
            self._finalize_open_issues(
                run_id=run_id,
                target_status="done",
                attempt=attempt,
                message="Run succeeded.",
            )
            self.database.update_agent_run_fields(
                run_id,
                status="succeeded",
                stop_reason="completed",
                failure_class=None,
                result_json=result,
                error_message=None,
                metrics_json=metrics_final,
                finished_at=self._utc_now(),
            )
            self.database.append_agent_run_checkpoint(
                run_id=run_id,
                checkpoint={
                    "stage": "succeeded",
                    "attempt": attempt,
                    "message": "Execution completed successfully.",
                    "stop_reason": "completed",
                    "metrics": metrics_final,
                },
            )
        self._emit(
            "agent_run_succeeded",
            {
                "run_id": run_id,
                "agent_id": agent.id,
                "status": "succeeded",
                "stop_reason": "completed",
                "failure_class": None,
                "attempts": attempt,
                "duration_ms": float(metrics_final.get("total_attempt_duration_ms", 0.0)),
                "metrics": metrics_final,
                "budget": budget,
            },
        )
        return lease_token

    def _ensure_core_issue_records(self, *, run_id: str) -> None:
        with self.database.write_transaction():
            for issue_id, title, issue_order, depends_on in CORE_ISSUE_DEFINITIONS:
                self.database.upsert_agent_run_issue(
                    run_id=run_id,
                    issue_id=issue_id,
                    issue_order=issue_order,
                    title=title,
                    status="planned",
                    depends_on=list(depends_on),
                    attempt_count=0,
                    last_error=None,
                    payload={},
                    started_at=None,
                    finished_at=None,
                )

    def _reset_issue_states_for_resume(self, *, run_id: str) -> None:
        items = self.database.list_agent_run_issues(run_id=run_id, limit=500)
        if not items:
            self._ensure_core_issue_records(run_id=run_id)
            return
        with self.database.write_transaction():
            for item in items:
                status = str(item.get("status") or "planned").strip().lower() or "planned"
                if status == "done":
                    continue
                self.database.upsert_agent_run_issue(
                    run_id=run_id,
                    issue_id=str(item.get("issue_id")),
                    issue_order=int(item.get("issue_order", 0)),
                    title=str(item.get("title") or item.get("issue_id") or "Issue"),
                    status="planned",
                    depends_on=[str(dep) for dep in item.get("depends_on", []) if str(dep).strip()],
                    attempt_count=max(0, int(item.get("attempt_count", 0))),
                    last_error=None,
                    payload=dict(item.get("payload") or {}),
                    started_at=None,
                    finished_at=None,
                )

    def _derive_issue_update_from_checkpoint(
        self,
        *,
        run_id: str,
        checkpoint: dict[str, Any],
        attempt: int,
    ) -> dict[str, Any] | None:
        stage = str(checkpoint.get("stage") or "").strip().lower()
        timestamp = str(checkpoint.get("timestamp") or self._utc_now())

        issue_payload: dict[str, Any] | None = None
        if stage == "issue_state":
            raw_issue = checkpoint.get("issue")
            if isinstance(raw_issue, dict):
                issue_payload = raw_issue
        elif stage in {"step_completed", "step_resumed", "step_resume_fallback"}:
            step = str(checkpoint.get("step") or "").strip()
            if step:
                mapped_status = "done" if stage in {"step_completed", "step_resumed"} else "running"
                title, issue_order, depends_on = self._issue_meta_for_id(step)
                issue_payload = {
                    "id": step,
                    "title": title,
                    "order": issue_order,
                    "depends_on": depends_on,
                    "status": mapped_status,
                    "attempt": attempt,
                    "payload": {
                        "stage": stage,
                        "message": str(checkpoint.get("message") or ""),
                    },
                }
        if issue_payload is None:
            return None

        issue_id = str(issue_payload.get("id") or "").strip()
        if not issue_id:
            return None
        title, issue_order_default, depends_on_default = self._issue_meta_for_id(issue_id)
        issue_order = self._safe_int(issue_payload.get("order"), issue_order_default)
        status = str(issue_payload.get("status") or "planned").strip().lower() or "planned"
        if status not in {"planned", "running", "blocked", "done", "failed"}:
            status = "planned"
        depends_on_raw = issue_payload.get("depends_on")
        depends_on = (
            [str(item) for item in depends_on_raw if str(item).strip()]
            if isinstance(depends_on_raw, list)
            else depends_on_default
        )
        attempt_count = max(0, self._safe_int(issue_payload.get("attempt"), attempt))
        last_error = issue_payload.get("last_error")
        if last_error is None:
            payload = issue_payload.get("payload")
            if isinstance(payload, dict):
                payload_error = payload.get("error")
                last_error = str(payload_error) if payload_error is not None else None
        else:
            last_error = str(last_error)

        existing = self.database.get_agent_run_issue(run_id=run_id, issue_id=issue_id)
        started_at = issue_payload.get("started_at")
        finished_at = issue_payload.get("finished_at")
        normalized_started_at = str(started_at) if started_at is not None else None
        normalized_finished_at = str(finished_at) if finished_at is not None else None
        if existing is not None:
            if normalized_started_at is None:
                normalized_started_at = str(existing.get("started_at") or "") or None
            if normalized_finished_at is None:
                normalized_finished_at = str(existing.get("finished_at") or "") or None

        if status == "running" and normalized_started_at is None:
            normalized_started_at = timestamp
        if status in {"done", "failed", "blocked"} and normalized_finished_at is None:
            normalized_finished_at = timestamp
        if status in {"planned", "running"}:
            normalized_finished_at = None
        payload_raw = issue_payload.get("payload")
        payload = dict(payload_raw) if isinstance(payload_raw, dict) else {}
        return {
            "issue_id": issue_id,
            "issue_order": max(0, int(issue_order)),
            "title": str(issue_payload.get("title") or title).strip() or title,
            "status": status,
            "depends_on": depends_on,
            "attempt_count": attempt_count,
            "last_error": last_error,
            "payload": payload,
            "started_at": normalized_started_at,
            "finished_at": normalized_finished_at,
        }

    @staticmethod
    def _derive_issue_artifact_from_checkpoint(
        checkpoint: dict[str, Any],
    ) -> dict[str, Any] | None:
        stage = str(checkpoint.get("stage") or "").strip().lower()
        if stage == "issue_artifact":
            issue_id = str(checkpoint.get("issue_id") or "").strip()
            artifact_key = str(checkpoint.get("artifact_key") or "result").strip() or "result"
            artifact = checkpoint.get("artifact")
            if not issue_id or not isinstance(artifact, dict):
                return None
            return {
                "issue_id": issue_id,
                "artifact_key": artifact_key,
                "artifact": artifact,
            }

        if stage != "issue_state":
            return None
        issue = checkpoint.get("issue")
        if not isinstance(issue, dict):
            return None
        issue_id = str(issue.get("id") or "").strip()
        if not issue_id:
            return None
        payload = issue.get("payload")
        if not isinstance(payload, dict):
            return None
        artifact = payload.get("artifact")
        if isinstance(artifact, dict):
            artifact_key = str(payload.get("artifact_key") or "result").strip() or "result"
            return {
                "issue_id": issue_id,
                "artifact_key": artifact_key,
                "artifact": artifact,
            }
        return None

    @staticmethod
    def _derive_tool_call_record_from_checkpoint(checkpoint: dict[str, Any]) -> dict[str, Any] | None:
        stage = str(checkpoint.get("stage") or "").strip().lower()
        if stage != "tool_call_recorded":
            return None
        idempotency_key = str(checkpoint.get("idempotency_key") or "").strip()
        tool_name = str(checkpoint.get("tool") or "").strip()
        status = str(checkpoint.get("status") or "").strip().lower()
        arguments = checkpoint.get("arguments")
        result = checkpoint.get("result")
        error_message = checkpoint.get("error")
        attempt = checkpoint.get("attempt")
        if not idempotency_key or not tool_name:
            return None
        if not isinstance(arguments, dict):
            arguments = {}
        if not isinstance(result, dict):
            result = None
        try:
            attempt_int = max(0, int(attempt))
        except Exception:
            attempt_int = 0
        normalized_status = status or "unknown"
        if normalized_status == "reused":
            normalized_status = "succeeded"
        return {
            "idempotency_key": idempotency_key,
            "tool_name": tool_name,
            "arguments": arguments,
            "status": normalized_status,
            "result": result,
            "error_message": str(error_message) if error_message not in (None, "") else None,
            "attempt": attempt_int,
        }

    def _merge_persisted_issue_artifacts(
        self,
        *,
        run_id: str,
        resume_state: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        state = dict(resume_state) if isinstance(resume_state, dict) else {}
        issue_rows = self.database.list_agent_run_issues(run_id=run_id, limit=5000)
        if issue_rows:
            existing_steps_raw = state.get("completed_steps")
            completed_steps: list[str] = []
            if isinstance(existing_steps_raw, list):
                completed_steps = [str(item) for item in existing_steps_raw if str(item).strip()]
            for issue in issue_rows:
                issue_id = str(issue.get("issue_id") or "").strip()
                if not issue_id:
                    continue
                status = str(issue.get("status") or "").strip().lower()
                if status == "done" and issue_id not in completed_steps:
                    completed_steps.append(issue_id)
            state["completed_steps"] = completed_steps

        rows = self.database.list_agent_run_issue_artifacts(run_id=run_id, limit=5000)
        if not rows:
            return state if state else resume_state
        raw = state.get("issue_artifacts")
        issue_artifacts: dict[str, dict[str, Any]] = {}
        if isinstance(raw, dict):
            for issue_id, artifacts in raw.items():
                if isinstance(artifacts, dict):
                    issue_artifacts[str(issue_id)] = dict(artifacts)
        for row in rows:
            issue_id = str(row.get("issue_id") or "").strip()
            artifact_key = str(row.get("artifact_key") or "result").strip() or "result"
            artifact = row.get("artifact")
            if not issue_id or not isinstance(artifact, dict):
                continue
            issue_artifacts.setdefault(issue_id, {})
            issue_artifacts[issue_id][artifact_key] = artifact
        state["issue_artifacts"] = issue_artifacts
        return state

    def _merge_persisted_tool_call_cache(
        self,
        *,
        run_id: str,
        resume_state: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        state = dict(resume_state) if isinstance(resume_state, dict) else {}
        rows = self.database.list_agent_run_tool_calls(run_id=run_id, limit=5000)
        if not rows:
            return state if state else resume_state

        raw_cache = state.get("tool_call_cache")
        tool_call_cache: dict[str, dict[str, Any]] = {}
        if isinstance(raw_cache, dict):
            for key, value in raw_cache.items():
                cache_key = str(key).strip()
                if cache_key and isinstance(value, dict):
                    tool_call_cache[cache_key] = dict(value)

        for row in rows:
            status = str(row.get("status") or "").strip().lower()
            if status != "succeeded":
                continue
            idempotency_key = str(row.get("idempotency_key") or "").strip()
            tool_name = str(row.get("tool_name") or "").strip()
            arguments = row.get("arguments")
            result = row.get("result")
            if not idempotency_key or not tool_name:
                continue
            if not isinstance(arguments, dict):
                arguments = {}
            if not isinstance(result, dict):
                continue
            tool_call_cache[idempotency_key] = {
                "tool_name": tool_name,
                "status": "succeeded",
                "arguments": arguments,
                "tool_result": result,
            }
        state["tool_call_cache"] = tool_call_cache
        return state

    def _mark_issue_failed_from_error(
        self,
        *,
        run_id: str,
        attempt: int,
        error_message: str,
    ) -> None:
        items = self.database.list_agent_run_issues(run_id=run_id, limit=500)
        target: dict[str, Any] | None = None
        running = [item for item in items if str(item.get("status") or "").lower() == "running"]
        if running:
            target = sorted(running, key=lambda item: int(item.get("issue_order", 0)))[-1]
        elif items:
            non_done = [item for item in items if str(item.get("status") or "").lower() != "done"]
            if non_done:
                target = sorted(non_done, key=lambda item: int(item.get("issue_order", 0)))[0]
        if target is None:
            self._ensure_core_issue_records(run_id=run_id)
            target = self.database.get_agent_run_issue(run_id=run_id, issue_id=STEP_REASONING)
            if target is None:
                return

        now_iso = self._utc_now()
        self.database.upsert_agent_run_issue(
            run_id=run_id,
            issue_id=str(target.get("issue_id")),
            issue_order=int(target.get("issue_order", 0)),
            title=str(target.get("title") or target.get("issue_id") or "Issue"),
            status="failed",
            depends_on=[str(dep) for dep in target.get("depends_on", []) if str(dep).strip()],
            attempt_count=max(int(target.get("attempt_count", 0)), int(attempt)),
            last_error=error_message,
            payload=dict(target.get("payload") or {}),
            started_at=str(target.get("started_at") or now_iso),
            finished_at=now_iso,
        )

    def _finalize_open_issues(
        self,
        *,
        run_id: str,
        target_status: str,
        attempt: int,
        message: str,
    ) -> None:
        normalized = str(target_status or "done").strip().lower() or "done"
        if normalized not in {"done", "blocked", "failed"}:
            normalized = "done"
        items = self.database.list_agent_run_issues(run_id=run_id, limit=500)
        if not items:
            self._ensure_core_issue_records(run_id=run_id)
            items = self.database.list_agent_run_issues(run_id=run_id, limit=500)
        now_iso = self._utc_now()
        with self.database.write_transaction():
            for item in items:
                status = str(item.get("status") or "planned").strip().lower() or "planned"
                if status == "done" and normalized == "done":
                    continue
                if status in {"failed", "blocked"} and normalized in {"blocked", "failed"}:
                    continue
                if status == "failed" and normalized == "done":
                    continue
                payload = dict(item.get("payload") or {})
                payload["terminal_message"] = message
                self.database.upsert_agent_run_issue(
                    run_id=run_id,
                    issue_id=str(item.get("issue_id")),
                    issue_order=int(item.get("issue_order", 0)),
                    title=str(item.get("title") or item.get("issue_id") or "Issue"),
                    status=normalized if status != "done" else "done",
                    depends_on=[str(dep) for dep in item.get("depends_on", []) if str(dep).strip()],
                    attempt_count=max(int(item.get("attempt_count", 0)), int(attempt)),
                    last_error=item.get("last_error") if normalized != "done" else None,
                    payload=payload,
                    started_at=str(item.get("started_at") or now_iso),
                    finished_at=str(item.get("finished_at") or now_iso),
                )

    @staticmethod
    def _issue_meta_for_id(issue_id: str) -> tuple[str, int, list[str]]:
        normalized = str(issue_id or "").strip()
        for known_id, title, issue_order, depends_on in CORE_ISSUE_DEFINITIONS:
            if normalized == known_id:
                return title, issue_order, list(depends_on)
        if normalized.startswith("plan_step:"):
            suffix = normalized.split(":", 1)[1].strip()
            order = 100 + max(0, AgentRunManager._safe_int(suffix, 0))
            return f"Plan step {suffix}", order, [STEP_PREPARE_CONTEXT]
        return normalized.replace("_", " ").strip().title() or "Issue", 1000, []

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.telemetry is None:
            return
        try:
            self.telemetry.emit(event_type, payload)
        except Exception:
            self.logger.debug("run_telemetry_emit_failed event=%s", event_type)

    def _release_run_lease(self, *, run_id: str, lease_token: str | None = None) -> None:
        normalized_run_id = str(run_id or "").strip()
        token = str(lease_token or "").strip()
        if not normalized_run_id or not token:
            return
        try:
            self.database.release_agent_run_lease(
                run_id=normalized_run_id,
                lease_owner=self._lease_owner,
                lease_token=token,
            )
        except Exception as exc:
            self.logger.debug("run_lease_release_failed run_id=%s error=%s", normalized_run_id, exc)

    def _run_lease_expiry_iso(self) -> str:
        return (datetime.now(timezone.utc) + timedelta(seconds=self.run_lease_ttl_sec)).isoformat()

    def _run_task_executor(
        self,
        *,
        run: dict[str, Any],
        agent: Agent,
        attempt: int,
        started_at: str,
        budget: dict[str, Any],
        run_id: str,
        lease_token: str | None,
        checkpoint: Any,
        resume_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        attempt_started = time.monotonic()
        attempt_deadline = attempt_started + self.attempt_timeout_sec
        remaining_duration = self._remaining_duration_sec(started_at=started_at, budget=budget)
        if remaining_duration <= 0.0:
            raise RunBudgetExceededError("Run duration budget exceeded.")
        attempt_deadline = min(attempt_deadline, attempt_started + remaining_duration)
        heartbeat = self._start_run_lease_heartbeat(run_id=run_id, lease_token=lease_token)
        self._assert_active_run_lease(
            run_id=run_id,
            lease_token=lease_token,
            heartbeat=heartbeat,
        )

        def guarded_checkpoint(payload: dict[str, Any]) -> None:
            self._assert_active_run_lease(
                run_id=run_id,
                lease_token=lease_token,
                heartbeat=heartbeat,
            )
            checkpoint(payload)

        result: dict[str, Any]
        try:
            result = self.task_executor.execute(
                agent=agent,
                user_id=str(run["user_id"]),
                session_id=run.get("session_id"),
                user_message=str(run["input_message"]),
                checkpoint=guarded_checkpoint,
                run_deadline_monotonic=attempt_deadline,
                resume_state=resume_state,
                run_budget=budget,
            )
        except TypeError as exc:
            # Backward compatibility for custom executors used in tests/tools.
            message = str(exc)
            if "run_budget" in message and "resume_state" in message and "run_deadline_monotonic" in message:
                result = self.task_executor.execute(
                    agent=agent,
                    user_id=str(run["user_id"]),
                    session_id=run.get("session_id"),
                    user_message=str(run["input_message"]),
                    checkpoint=guarded_checkpoint,
                )
            elif "run_budget" in message and "resume_state" in message:
                result = self.task_executor.execute(
                    agent=agent,
                    user_id=str(run["user_id"]),
                    session_id=run.get("session_id"),
                    user_message=str(run["input_message"]),
                    checkpoint=guarded_checkpoint,
                    run_deadline_monotonic=attempt_deadline,
                )
            elif "run_budget" in message and "run_deadline_monotonic" in message:
                result = self.task_executor.execute(
                    agent=agent,
                    user_id=str(run["user_id"]),
                    session_id=run.get("session_id"),
                    user_message=str(run["input_message"]),
                    checkpoint=guarded_checkpoint,
                    resume_state=resume_state,
                )
            elif "run_budget" in message:
                result = self.task_executor.execute(
                    agent=agent,
                    user_id=str(run["user_id"]),
                    session_id=run.get("session_id"),
                    user_message=str(run["input_message"]),
                    checkpoint=guarded_checkpoint,
                    run_deadline_monotonic=attempt_deadline,
                    resume_state=resume_state,
                )
            elif "resume_state" in message and "run_deadline_monotonic" in message:
                result = self.task_executor.execute(
                    agent=agent,
                    user_id=str(run["user_id"]),
                    session_id=run.get("session_id"),
                    user_message=str(run["input_message"]),
                    checkpoint=guarded_checkpoint,
                )
            elif "resume_state" in message:
                try:
                    result = self.task_executor.execute(
                        agent=agent,
                        user_id=str(run["user_id"]),
                        session_id=run.get("session_id"),
                        user_message=str(run["input_message"]),
                        checkpoint=guarded_checkpoint,
                        run_deadline_monotonic=attempt_deadline,
                    )
                except TypeError as nested_exc:
                    if "run_deadline_monotonic" not in str(nested_exc):
                        raise
                    result = self.task_executor.execute(
                        agent=agent,
                        user_id=str(run["user_id"]),
                        session_id=run.get("session_id"),
                        user_message=str(run["input_message"]),
                        checkpoint=guarded_checkpoint,
                    )
            elif "run_deadline_monotonic" in message:
                try:
                    result = self.task_executor.execute(
                        agent=agent,
                        user_id=str(run["user_id"]),
                        session_id=run.get("session_id"),
                        user_message=str(run["input_message"]),
                        checkpoint=guarded_checkpoint,
                        resume_state=resume_state,
                    )
                except TypeError as nested_exc:
                    if "resume_state" not in str(nested_exc):
                        raise
                    result = self.task_executor.execute(
                        agent=agent,
                        user_id=str(run["user_id"]),
                        session_id=run.get("session_id"),
                        user_message=str(run["input_message"]),
                        checkpoint=guarded_checkpoint,
                    )
            else:
                raise
        finally:
            self._stop_run_lease_heartbeat(heartbeat)

        self._assert_active_run_lease(
            run_id=run_id,
            lease_token=lease_token,
            heartbeat=heartbeat,
        )
        elapsed = time.monotonic() - attempt_started
        if elapsed > self.attempt_timeout_sec:
            self.database.append_agent_run_checkpoint(
                run_id=str(run["id"]),
                checkpoint={
                    "stage": "attempt_timeout_guardrail",
                    "attempt": attempt,
                    "message": (
                        f"Attempt exceeded timeout: elapsed={elapsed:.2f}s "
                        f"limit={self.attempt_timeout_sec:.2f}s"
                    ),
                },
            )
            raise TaskTimeoutError(
                f"Run attempt exceeded timeout ({elapsed:.2f}s > {self.attempt_timeout_sec:.2f}s)."
            )
        return result

    def _start_run_lease_heartbeat(
        self,
        *,
        run_id: str,
        lease_token: str | None,
    ) -> dict[str, Any] | None:
        token = str(lease_token or "").strip()
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id or not token:
            return None

        stop = Event()
        lost = Event()
        state: dict[str, Any] = {
            "stop": stop,
            "lost": lost,
            "reason": "",
            "failures": 0,
            "run_id": normalized_run_id,
            "lease_token": token,
            "owner": self._lease_owner,
            "thread": None,
        }

        def _heartbeat_loop() -> None:
            while not stop.wait(self.run_lease_heartbeat_sec):
                try:
                    refreshed = self.database.refresh_agent_run_lease(
                        run_id=normalized_run_id,
                        lease_owner=self._lease_owner,
                        lease_token=token,
                        lease_expires_at=self._run_lease_expiry_iso(),
                    )
                except Exception as exc:  # pragma: no cover - defensive logging
                    failures = int(state.get("failures", 0)) + 1
                    state["failures"] = failures
                    self.logger.error(
                        "run_lease_heartbeat_error run_id=%s attempt=%s error=%s",
                        normalized_run_id,
                        failures,
                        exc,
                    )
                    if failures >= self.run_lease_heartbeat_max_failures:
                        state["reason"] = (
                            "Run lease heartbeat failed repeatedly. "
                            "Stopping run to prevent duplicate side effects."
                        )
                        lost.set()
                        return
                    continue
                if not refreshed:
                    state["reason"] = "Run lease ownership lost during execution."
                    lost.set()
                    return
                state["failures"] = 0

        thread = Thread(
            target=_heartbeat_loop,
            name=f"amaryllis-run-lease-heartbeat-{normalized_run_id[:8]}",
            daemon=True,
        )
        thread.start()
        state["thread"] = thread
        return state

    @staticmethod
    def _stop_run_lease_heartbeat(heartbeat: dict[str, Any] | None) -> None:
        if not isinstance(heartbeat, dict):
            return
        stop = heartbeat.get("stop")
        if isinstance(stop, Event):
            stop.set()
        thread = heartbeat.get("thread")
        if isinstance(thread, Thread):
            thread.join(timeout=1.0)

    def _assert_active_run_lease(
        self,
        *,
        run_id: str,
        lease_token: str | None,
        heartbeat: dict[str, Any] | None,
    ) -> None:
        token = str(lease_token or "").strip()
        normalized_run_id = str(run_id or "").strip()
        if not normalized_run_id or not token:
            return
        if isinstance(heartbeat, dict):
            lost = heartbeat.get("lost")
            if isinstance(lost, Event) and lost.is_set():
                reason = str(heartbeat.get("reason") or "Run lease ownership lost.").strip()
                raise RunLeaseLostError(reason)
        refreshed = self.database.refresh_agent_run_lease(
            run_id=normalized_run_id,
            lease_owner=self._lease_owner,
            lease_token=token,
            lease_expires_at=self._run_lease_expiry_iso(),
        )
        if not refreshed:
            raise RunLeaseLostError("Run lease ownership lost during execution.")

    @staticmethod
    def _resolve_cancel_stop_reason(run: dict[str, Any]) -> str:
        stop_reason = str(run.get("stop_reason") or "").strip().lower()
        if stop_reason == KILL_SWITCH_STOP_REASON:
            return KILL_SWITCH_STOP_REASON
        return "canceled_by_user"

    @staticmethod
    def _count_budget_guardrail_breaches(run: dict[str, Any]) -> int:
        checkpoints = run.get("checkpoints")
        if not isinstance(checkpoints, list):
            return 0
        count = 0
        for item in checkpoints:
            if not isinstance(item, dict):
                continue
            stage = str(item.get("stage") or "").strip().lower()
            if stage in {
                "budget_guardrail_paused",
                "budget_guardrail_escalated",
            }:
                count += 1
        return count

    def _normalize_run_budget(self, budget: dict[str, Any] | None) -> dict[str, Any]:
        raw = budget if isinstance(budget, dict) else {}
        return {
            "max_tokens": max(
                256,
                self._safe_int(raw.get("max_tokens", self.default_run_budget["max_tokens"]), 256),
            ),
            "max_duration_sec": max(
                10.0,
                self._safe_float(raw.get("max_duration_sec", self.default_run_budget["max_duration_sec"]), 10.0),
            ),
            "max_tool_calls": max(
                1,
                self._safe_int(raw.get("max_tool_calls", self.default_run_budget["max_tool_calls"]), 1),
            ),
            "max_tool_errors": max(
                0,
                self._safe_int(raw.get("max_tool_errors", self.default_run_budget["max_tool_errors"]), 0),
            ),
        }

    @staticmethod
    def _normalize_run_metrics(metrics: dict[str, Any] | None) -> dict[str, Any]:
        source = metrics if isinstance(metrics, dict) else {}
        return {
            "estimated_tokens": max(0, AgentRunManager._safe_int(source.get("estimated_tokens", 0), 0)),
            "tool_calls": max(0, AgentRunManager._safe_int(source.get("tool_calls", 0), 0)),
            "tool_errors": max(0, AgentRunManager._safe_int(source.get("tool_errors", 0), 0)),
            "attempt_count": max(0, AgentRunManager._safe_int(source.get("attempt_count", 0), 0)),
            "retry_count": max(0, AgentRunManager._safe_int(source.get("retry_count", 0), 0)),
            "total_attempt_duration_ms": max(
                0.0,
                AgentRunManager._safe_float(source.get("total_attempt_duration_ms", 0.0), 0.0),
            ),
            "last_attempt_duration_ms": max(
                0.0,
                AgentRunManager._safe_float(source.get("last_attempt_duration_ms", 0.0), 0.0),
            ),
        }

    def _finalize_run_metrics(
        self,
        *,
        metrics: dict[str, Any],
        attempt: int,
        attempt_duration_ms: float,
    ) -> dict[str, Any]:
        normalized = self._normalize_run_metrics(metrics)
        normalized["attempt_count"] = max(int(normalized.get("attempt_count", 0)), int(attempt))
        normalized["retry_count"] = max(0, int(normalized["attempt_count"]) - 1)
        normalized["last_attempt_duration_ms"] = max(0.0, float(attempt_duration_ms))
        normalized["total_attempt_duration_ms"] = round(
            max(0.0, float(normalized.get("total_attempt_duration_ms", 0.0))) + max(0.0, float(attempt_duration_ms)),
            3,
        )
        return normalized

    @staticmethod
    def _merge_checkpoint_usage(*, live_usage: dict[str, Any], checkpoint: dict[str, Any]) -> None:
        if "estimated_tokens_total" in checkpoint:
            try:
                tokens_total = max(0, int(checkpoint.get("estimated_tokens_total", 0)))
                live_usage["estimated_tokens"] = max(int(live_usage.get("estimated_tokens", 0)), tokens_total)
            except Exception:
                pass
        stage = str(checkpoint.get("stage", "")).strip()
        if stage == "tool_call_finished":
            cached = bool(checkpoint.get("cached"))
            executed = checkpoint.get("executed")
            if cached or executed is False:
                return
            live_usage["tool_calls"] = max(0, int(live_usage.get("tool_calls", 0))) + 1
            status = str(checkpoint.get("status", "")).strip().lower()
            if status in {"failed", "invalid_arguments", "blocked", "permission_required"}:
                live_usage["tool_errors"] = max(0, int(live_usage.get("tool_errors", 0))) + 1

    @staticmethod
    def _validate_live_budget_usage(*, budget: dict[str, Any], usage: dict[str, Any]) -> None:
        max_tokens = int(budget.get("max_tokens", 0))
        max_tool_calls = int(budget.get("max_tool_calls", 0))
        max_tool_errors = int(budget.get("max_tool_errors", 0))
        estimated_tokens = int(usage.get("estimated_tokens", 0))
        tool_calls = int(usage.get("tool_calls", 0))
        tool_errors = int(usage.get("tool_errors", 0))
        if max_tokens > 0 and estimated_tokens > max_tokens:
            raise RunBudgetExceededError(
                f"Run token budget exceeded ({estimated_tokens} > {max_tokens})."
            )
        if max_tool_calls > 0 and tool_calls > max_tool_calls:
            raise RunBudgetExceededError(
                f"Run tool-call budget exceeded ({tool_calls} > {max_tool_calls})."
            )
        if max_tool_errors >= 0 and tool_errors > max_tool_errors:
            raise RunBudgetExceededError(
                f"Run tool-error budget exceeded ({tool_errors} > {max_tool_errors})."
            )

    def _extract_result_metrics(
        self,
        *,
        result: dict[str, Any],
        fallback: dict[str, Any],
        attempt: int,
    ) -> dict[str, Any]:
        metrics = result.get("metrics")
        if not isinstance(metrics, dict):
            return self._finalize_run_metrics(
                metrics=fallback,
                attempt=attempt,
                attempt_duration_ms=0.0,
            )
        merged = {
            "estimated_tokens": max(
                0,
                self._safe_int(metrics.get("estimated_tokens", fallback.get("estimated_tokens", 0)), 0),
            ),
            "tool_calls": max(
                0,
                self._safe_int(metrics.get("tool_calls", fallback.get("tool_calls", 0)), 0),
            ),
            "tool_errors": max(
                0,
                self._safe_int(metrics.get("tool_errors", fallback.get("tool_errors", 0)), 0),
            ),
            "attempt_count": max(self._safe_int(metrics.get("attempt_count", attempt), attempt), int(attempt)),
            "retry_count": max(0, self._safe_int(metrics.get("attempt_count", attempt), attempt) - 1),
            "total_attempt_duration_ms": max(
                0.0,
                self._safe_float(
                    metrics.get("total_attempt_duration_ms", metrics.get("duration_ms", 0.0)),
                    0.0,
                ),
            ),
            "last_attempt_duration_ms": max(0.0, self._safe_float(metrics.get("duration_ms", 0.0), 0.0)),
        }
        return self._normalize_run_metrics(merged)

    def _remaining_duration_sec(self, *, started_at: str, budget: dict[str, Any]) -> float:
        max_duration_sec = max(0.0, float(budget.get("max_duration_sec", 0.0)))
        if max_duration_sec <= 0:
            return 0.0
        start_dt = self._parse_iso_datetime(started_at)
        if start_dt is None:
            return max_duration_sec
        elapsed = (datetime.now(timezone.utc) - start_dt).total_seconds()
        return max(0.0, max_duration_sec - max(0.0, elapsed))

    @classmethod
    def _distribution(cls, values: list[float]) -> dict[str, float]:
        if not values:
            return {"count": 0.0, "min": 0.0, "max": 0.0, "median": 0.0, "p95": 0.0}
        normalized = sorted(max(0.0, float(item)) for item in values)
        return {
            "count": float(len(normalized)),
            "min": round(normalized[0], 3),
            "max": round(normalized[-1], 3),
            "median": round(cls._percentile(normalized, 50), 3),
            "p95": round(cls._percentile(normalized, 95), 3),
        }

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float:
        if not values:
            return 0.0
        if len(values) == 1:
            return float(values[0])
        position = max(0.0, min(100.0, float(percentile))) / 100.0 * (len(values) - 1)
        lower = int(position)
        upper = min(lower + 1, len(values) - 1)
        weight = position - lower
        return float(values[lower] * (1.0 - weight) + values[upper] * weight)

    @classmethod
    def _duration_ms(cls, *, started_at: Any, finished_at: Any) -> float | None:
        start_dt = cls._parse_iso_datetime(started_at)
        finish_dt = cls._parse_iso_datetime(finished_at)
        if start_dt is None or finish_dt is None:
            return None
        delta = (finish_dt - start_dt).total_seconds() * 1000.0
        if delta < 0:
            return 0.0
        return round(delta, 3)

    @staticmethod
    def _parse_iso_datetime(value: Any) -> datetime | None:
        raw = str(value or "").strip()
        if not raw:
            return None
        normalized = raw.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except Exception:
            return int(default)

    @staticmethod
    def _safe_float(value: Any, default: float) -> float:
        try:
            return float(value)
        except Exception:
            return float(default)

    @staticmethod
    def _extract_resume_state(run: dict[str, Any]) -> dict[str, Any] | None:
        checkpoints = run.get("checkpoints")
        if not isinstance(checkpoints, list):
            return None
        for item in reversed(checkpoints):
            if not isinstance(item, dict):
                continue
            payload = item.get("resume_state")
            if isinstance(payload, dict):
                return payload
        return None

    @staticmethod
    def _normalize_attempt(value: Any) -> int | None:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()
