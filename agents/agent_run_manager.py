from __future__ import annotations

import logging
from queue import Empty, Queue
from threading import Event, Thread
from time import sleep
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import uuid4

from agents.agent import Agent
from storage.database import Database
from tasks.task_executor import TaskExecutor


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
        telemetry: TelemetrySink | None = None,
    ) -> None:
        self.logger = logging.getLogger("amaryllis.agents.runs")
        self.database = database
        self.task_executor = task_executor
        self.worker_count = max(1, worker_count)
        self.default_max_attempts = max(1, default_max_attempts)
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
            },
        )

        try:
            def push_checkpoint(payload: dict[str, Any]) -> None:
                data = dict(payload)
                data.setdefault("attempt", attempt)
                self.database.append_agent_run_checkpoint(run_id=run_id, checkpoint=data)

            result = self.task_executor.execute(
                agent=agent,
                user_id=str(run["user_id"]),
                session_id=run.get("session_id"),
                user_message=str(run["input_message"]),
                checkpoint=push_checkpoint,
            )
        except Exception as exc:
            error_message = str(exc)
            self.database.append_agent_run_checkpoint(
                run_id=run_id,
                checkpoint={
                    "stage": "error",
                    "attempt": attempt,
                    "message": error_message,
                },
            )

            if attempt < max_attempts and int(run.get("cancel_requested", 0)) != 1:
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
                    },
                )
                sleep(0.2)
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

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()
