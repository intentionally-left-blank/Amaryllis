from __future__ import annotations

import logging
import random
from queue import Empty, Queue
from threading import Event, Thread
from datetime import datetime, timezone
import time
from typing import Any, Protocol
from uuid import uuid4

from agents.agent import Agent
from storage.database import Database
from tasks.task_executor import TaskExecutor, TaskGuardrailError, TaskTimeoutError


class TelemetrySink(Protocol):
    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        ...


class AgentRunManager:
    def __init__(
        self,
        database: Database,
        task_executor: TaskExecutor,
        worker_count: int = 2,
        default_max_attempts: int = 2,
        attempt_timeout_sec: float = 180.0,
        retry_backoff_sec: float = 0.3,
        retry_max_backoff_sec: float = 2.0,
        retry_jitter_sec: float = 0.15,
        telemetry: TelemetrySink | None = None,
    ) -> None:
        self.logger = logging.getLogger("amaryllis.agents.runs")
        self.database = database
        self.task_executor = task_executor
        self.worker_count = max(1, worker_count)
        self.default_max_attempts = max(1, default_max_attempts)
        self.attempt_timeout_sec = max(5.0, float(attempt_timeout_sec))
        self.retry_backoff_sec = max(0.0, float(retry_backoff_sec))
        self.retry_max_backoff_sec = max(0.0, float(retry_max_backoff_sec))
        self.retry_jitter_sec = max(0.0, float(retry_jitter_sec))
        self.telemetry = telemetry

        self._queue: Queue[str | None] = Queue()
        self._workers: list[Thread] = []
        self._stop = Event()
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stop.clear()
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

    def create_run(
        self,
        agent: Agent,
        user_id: str,
        session_id: str | None,
        user_message: str,
        max_attempts: int | None = None,
    ) -> dict[str, Any]:
        run_id = str(uuid4())
        attempts_limit = max(1, max_attempts or self.default_max_attempts)
        self.database.create_agent_run(
            run_id=run_id,
            agent_id=agent.id,
            user_id=user_id,
            session_id=session_id,
            input_message=user_message,
            status="queued",
            max_attempts=attempts_limit,
        )
        self.database.append_agent_run_checkpoint(
            run_id=run_id,
            checkpoint={
                "stage": "queued",
                "message": "Run queued for execution.",
            },
        )
        self._queue.put(run_id)
        self._emit(
            "agent_run_queued",
            {
                "run_id": run_id,
                "agent_id": agent.id,
                "user_id": user_id,
                "session_id": session_id,
                "max_attempts": attempts_limit,
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
        return self.database.get_agent_run(run_id)

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        run = self.database.get_agent_run(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")

        self.database.update_agent_run_fields(run_id, cancel_requested=1)
        status = str(run.get("status", ""))
        if status == "queued":
            self.database.update_agent_run_fields(
                run_id,
                status="canceled",
                finished_at=self._utc_now(),
            )
            self.database.append_agent_run_checkpoint(
                run_id=run_id,
                checkpoint={
                    "stage": "canceled",
                    "message": "Run canceled before execution.",
                },
            )
        else:
            self.database.append_agent_run_checkpoint(
                run_id=run_id,
                checkpoint={
                    "stage": "cancel_requested",
                    "message": "Cancel requested.",
                },
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

    def resume_run(self, run_id: str) -> dict[str, Any]:
        run = self.database.get_agent_run(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")

        status = str(run.get("status", ""))
        if status not in {"failed", "canceled"}:
            raise ValueError(f"Run {run_id} is not resumable (status={status})")
        resume_state = self._extract_resume_state(run)

        self.database.update_agent_run_fields(
            run_id,
            status="queued",
            attempts=0,
            cancel_requested=0,
            error_message=None,
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
                summary["tool_rounds"] = int(summary.get("tool_rounds", 0)) + 1
            if stage == "verification_repair_attempt":
                summary["verification_repairs"] = int(summary.get("verification_repairs", 0)) + 1
            if stage in {"error", "failed"} and message:
                errors = summary["errors"]
                assert isinstance(errors, list)
                errors.append(message)

        latest_resume_state = self._extract_resume_state(run)
        return {
            "run_id": str(run.get("id", run_id)),
            "agent_id": run.get("agent_id"),
            "user_id": run.get("user_id"),
            "session_id": run.get("session_id"),
            "status": run.get("status"),
            "attempts": int(run.get("attempts", 0)),
            "max_attempts": int(run.get("max_attempts", 0)),
            "checkpoint_count": len(timeline),
            "timeline": timeline,
            "attempt_summary": attempt_summary,
            "resume_snapshots": resume_snapshots,
            "latest_resume_state": latest_resume_state or None,
            "has_result": run.get("result") is not None,
            "error_message": run.get("error_message"),
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

            try:
                self._process_run(item)
            except Exception as exc:
                self.logger.exception("run_worker_unhandled run_id=%s error=%s", item, exc)
            finally:
                self._queue.task_done()

    def _process_run(self, run_id: str) -> None:
        run = self.database.get_agent_run(run_id)
        if run is None:
            return

        if int(run.get("cancel_requested", 0)) == 1:
            self.database.update_agent_run_fields(
                run_id,
                status="canceled",
                finished_at=self._utc_now(),
            )
            self.database.append_agent_run_checkpoint(
                run_id=run_id,
                checkpoint={
                    "stage": "canceled",
                    "message": "Run canceled before worker execution.",
                },
            )
            return

        status = str(run.get("status", ""))
        if status not in {"queued", "running"}:
            return

        agent_record = self.database.get_agent(str(run["agent_id"]))
        if agent_record is None:
            self.database.update_agent_run_fields(
                run_id,
                status="failed",
                error_message=f"Agent not found: {run['agent_id']}",
                finished_at=self._utc_now(),
            )
            self.database.append_agent_run_checkpoint(
                run_id=run_id,
                checkpoint={
                    "stage": "failed",
                    "message": f"Agent not found: {run['agent_id']}",
                },
            )
            return

        agent = Agent.from_record(agent_record)
        attempt = int(run.get("attempts", 0)) + 1
        max_attempts = int(run.get("max_attempts", self.default_max_attempts))

        self.database.update_agent_run_fields(
            run_id,
            status="running",
            attempts=attempt,
            started_at=self._utc_now(),
            error_message=None,
        )
        self.database.append_agent_run_checkpoint(
            run_id=run_id,
            checkpoint={
                "stage": "running",
                "attempt": attempt,
                "message": f"Execution started (attempt {attempt}/{max_attempts}).",
                "attempt_timeout_sec": self.attempt_timeout_sec,
            },
        )

        try:
            def push_checkpoint(payload: dict[str, Any]) -> None:
                data = dict(payload)
                data.setdefault("attempt", attempt)
                self.database.append_agent_run_checkpoint(run_id=run_id, checkpoint=data)
            resume_state = self._extract_resume_state(run)
            result = self._run_task_executor(
                run=run,
                agent=agent,
                attempt=attempt,
                checkpoint=push_checkpoint,
                resume_state=resume_state,
            )
        except Exception as exc:
            error_message = str(exc)
            retryable = self._is_retryable_error(exc)
            self.database.append_agent_run_checkpoint(
                run_id=run_id,
                checkpoint={
                    "stage": "error",
                    "attempt": attempt,
                    "message": error_message,
                    "retryable": retryable,
                },
            )

            if attempt < max_attempts and int(run.get("cancel_requested", 0)) != 1 and retryable:
                backoff_sec = self._retry_delay_seconds(attempt=attempt)
                self.database.update_agent_run_fields(
                    run_id,
                    status="queued",
                    error_message=error_message,
                )
                self.database.append_agent_run_checkpoint(
                    run_id=run_id,
                    checkpoint={
                        "stage": "retry_scheduled",
                        "attempt": attempt + 1,
                        "message": "Retry scheduled.",
                        "backoff_sec": backoff_sec,
                    },
                )
                if backoff_sec > 0:
                    time.sleep(backoff_sec)
                self._queue.put(run_id)
            else:
                final_status = "canceled" if int(run.get("cancel_requested", 0)) == 1 else "failed"
                self.database.update_agent_run_fields(
                    run_id,
                    status=final_status,
                    error_message=error_message,
                    finished_at=self._utc_now(),
                )
                self.database.append_agent_run_checkpoint(
                    run_id=run_id,
                    checkpoint={
                        "stage": final_status,
                        "attempt": attempt,
                        "message": error_message,
                    },
                )
            return

        latest = self.database.get_agent_run(run_id)
        if latest is not None and int(latest.get("cancel_requested", 0)) == 1:
            self.database.update_agent_run_fields(
                run_id,
                status="canceled",
                result_json=result,
                finished_at=self._utc_now(),
            )
            self.database.append_agent_run_checkpoint(
                run_id=run_id,
                checkpoint={
                    "stage": "canceled",
                    "attempt": attempt,
                    "message": "Execution completed but run was canceled.",
                },
            )
            return

        self.database.update_agent_run_fields(
            run_id,
            status="succeeded",
            result_json=result,
            error_message=None,
            finished_at=self._utc_now(),
        )
        self.database.append_agent_run_checkpoint(
            run_id=run_id,
            checkpoint={
                "stage": "succeeded",
                "attempt": attempt,
                "message": "Execution completed successfully.",
            },
        )
        self._emit(
            "agent_run_succeeded",
            {
                "run_id": run_id,
                "agent_id": agent.id,
                "attempts": attempt,
            },
        )

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.telemetry is None:
            return
        try:
            self.telemetry.emit(event_type, payload)
        except Exception:
            self.logger.debug("run_telemetry_emit_failed event=%s", event_type)

    def _run_task_executor(
        self,
        *,
        run: dict[str, Any],
        agent: Agent,
        attempt: int,
        checkpoint: Any,
        resume_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        attempt_started = time.monotonic()
        attempt_deadline = attempt_started + self.attempt_timeout_sec
        result: dict[str, Any]
        try:
            result = self.task_executor.execute(
                agent=agent,
                user_id=str(run["user_id"]),
                session_id=run.get("session_id"),
                user_message=str(run["input_message"]),
                checkpoint=checkpoint,
                run_deadline_monotonic=attempt_deadline,
                resume_state=resume_state,
            )
        except TypeError as exc:
            # Backward compatibility for custom executors used in tests/tools.
            message = str(exc)
            if "resume_state" in message and "run_deadline_monotonic" in message:
                result = self.task_executor.execute(
                    agent=agent,
                    user_id=str(run["user_id"]),
                    session_id=run.get("session_id"),
                    user_message=str(run["input_message"]),
                    checkpoint=checkpoint,
                )
            elif "resume_state" in message:
                try:
                    result = self.task_executor.execute(
                        agent=agent,
                        user_id=str(run["user_id"]),
                        session_id=run.get("session_id"),
                        user_message=str(run["input_message"]),
                        checkpoint=checkpoint,
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
                        checkpoint=checkpoint,
                    )
            elif "run_deadline_monotonic" in message:
                try:
                    result = self.task_executor.execute(
                        agent=agent,
                        user_id=str(run["user_id"]),
                        session_id=run.get("session_id"),
                        user_message=str(run["input_message"]),
                        checkpoint=checkpoint,
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
                        checkpoint=checkpoint,
                    )
            else:
                raise
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

    def _is_retryable_error(self, exc: Exception) -> bool:
        if isinstance(exc, TaskGuardrailError):
            return False
        if isinstance(exc, TaskTimeoutError):
            return True
        if isinstance(exc, (ValueError, TypeError, AssertionError)):
            return False
        message = str(exc).lower()
        retry_keywords = (
            "timeout",
            "temporarily",
            "temporary",
            "connection",
            "429",
            "too many requests",
            "rate limit",
            "503",
            "502",
            "504",
            "network",
            "unavailable",
            "overloaded",
            "try again",
        )
        return any(keyword in message for keyword in retry_keywords) or isinstance(exc, RuntimeError)

    def _retry_delay_seconds(self, *, attempt: int) -> float:
        if self.retry_backoff_sec <= 0:
            return 0.0
        exponential = self.retry_backoff_sec * (2 ** max(0, attempt - 1))
        bounded = min(exponential, self.retry_max_backoff_sec) if self.retry_max_backoff_sec > 0 else exponential
        jitter = random.uniform(0.0, self.retry_jitter_sec) if self.retry_jitter_sec > 0 else 0.0
        return round(max(0.0, bounded + jitter), 3)

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
