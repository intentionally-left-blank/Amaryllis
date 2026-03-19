from __future__ import annotations

import logging
import random
from queue import Empty, Queue
from threading import Event, Thread
from datetime import datetime, timedelta, timezone
import time
from typing import Any, Protocol
from uuid import uuid4

from agents.agent import Agent
from models.provider_errors import ProviderOperationError, classify_provider_error
from storage.database import Database
from tasks.task_executor import (
    STEP_PERSIST,
    STEP_PREPARE_CONTEXT,
    STEP_REASONING,
    TaskExecutor,
    TaskGuardrailError,
    TaskTimeoutError,
)


class TelemetrySink(Protocol):
    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        ...


class RunBudgetExceededError(TaskGuardrailError):
    pass


class RunLeaseLostError(TaskGuardrailError):
    pass


RUN_RETRYABLE_FAILURE_CLASSES: set[str] = {
    "timeout",
    "rate_limit",
    "network",
    "server",
    "unavailable",
    "circuit_open",
}
KILL_SWITCH_STOP_REASON = "kill_switch_triggered"

CORE_ISSUE_DEFINITIONS: tuple[tuple[str, str, int, list[str]], ...] = (
    (STEP_PREPARE_CONTEXT, "Prepare context", 10, []),
    (STEP_REASONING, "Reasoning", 20, [STEP_PREPARE_CONTEXT]),
    (STEP_PERSIST, "Persist memory", 30, [STEP_REASONING]),
)


class AgentRunManager:
    def __init__(
        self,
        database: Database,
        task_executor: TaskExecutor,
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
    ) -> dict[str, Any]:
        if not include_running and not include_queued:
            raise ValueError("Kill switch requires include_running and/or include_queued.")

        normalized_actor = str(actor or "").strip() or None
        normalized_reason = str(reason or "").strip()
        normalized_limit = max(1, min(int(limit), 50_000))
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
            failure = self._classify_failure(exc)
            retryable = bool(failure.get("retryable", False))
            failure_class = str(failure.get("failure_class", "unknown"))
            stop_reason = str(failure.get("stop_reason", "unknown_error"))
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

                schedule_retry = attempt < max_attempts and not canceled and retryable
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
                    final_status = "canceled" if canceled else "failed"
                    final_failure_class = "canceled" if canceled else failure_class
                    final_stop_reason = cancel_stop_reason if canceled else stop_reason
                    if not canceled and retryable and attempt >= max_attempts:
                        final_stop_reason = "max_attempts_exhausted"
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
                    backoff_sec = self._retry_delay_seconds(attempt=attempt)
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
                backoff_sec = self._retry_delay_seconds(attempt=attempt)
                if backoff_sec > 0:
                    time.sleep(backoff_sec)
                self._queue.put(run_id)
            else:
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

    def _classify_failure(self, exc: Exception) -> dict[str, Any]:
        if isinstance(exc, RunBudgetExceededError):
            return {
                "failure_class": "budget_exceeded",
                "stop_reason": "budget_exceeded",
                "retryable": False,
            }
        if isinstance(exc, RunLeaseLostError):
            return {
                "failure_class": "lease_lost",
                "stop_reason": "lease_lost",
                "retryable": True,
            }
        if isinstance(exc, TaskTimeoutError):
            return {
                "failure_class": "timeout",
                "stop_reason": "timeout",
                "retryable": True,
            }
        if isinstance(exc, TaskGuardrailError):
            message = str(exc).lower()
            if "budget" in message:
                return {
                    "failure_class": "budget_exceeded",
                    "stop_reason": "budget_exceeded",
                    "retryable": False,
                }
            return {
                "failure_class": "guardrail",
                "stop_reason": "guardrail_rejected",
                "retryable": False,
            }
        if isinstance(exc, ProviderOperationError):
            error_class = str(exc.info.error_class)
            return {
                "failure_class": error_class,
                "stop_reason": f"provider_{error_class}",
                "retryable": error_class in RUN_RETRYABLE_FAILURE_CLASSES,
            }
        if isinstance(exc, (ValueError, TypeError, AssertionError)):
            return {
                "failure_class": "invalid_request",
                "stop_reason": "invalid_request",
                "retryable": False,
            }

        provider_info = classify_provider_error(
            provider="unknown",
            operation="agent_run",
            error=exc,
        )
        provider_class = str(provider_info.error_class)
        if provider_class != "unknown":
            return {
                "failure_class": provider_class,
                "stop_reason": f"provider_{provider_class}",
                "retryable": provider_class in RUN_RETRYABLE_FAILURE_CLASSES,
            }

        return {
            "failure_class": "unknown",
            "stop_reason": "unknown_error",
            "retryable": False,
        }

    @staticmethod
    def _resolve_cancel_stop_reason(run: dict[str, Any]) -> str:
        stop_reason = str(run.get("stop_reason") or "").strip().lower()
        if stop_reason == KILL_SWITCH_STOP_REASON:
            return KILL_SWITCH_STOP_REASON
        return "canceled_by_user"

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

    def _retry_delay_seconds(self, *, attempt: int) -> float:
        if self.retry_backoff_sec <= 0:
            return 0.0
        exponential = self.retry_backoff_sec * (2 ** max(0, attempt - 1))
        bounded = min(exponential, self.retry_max_backoff_sec) if self.retry_max_backoff_sec > 0 else exponential
        jitter = random.uniform(0.0, self.retry_jitter_sec) if self.retry_jitter_sec > 0 else 0.0
        return round(max(0.0, bounded + jitter), 3)

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
