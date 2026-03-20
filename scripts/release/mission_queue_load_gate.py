#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
from threading import Lock
import time
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run blocking mission queue load gate and validate queue stability/SLO under "
            "target concurrent submission pressure."
        )
    )
    parser.add_argument("--runs-total", type=int, default=40, help="Total number of runs to enqueue.")
    parser.add_argument(
        "--submit-concurrency",
        type=int,
        default=8,
        help="Concurrency used while submitting runs.",
    )
    parser.add_argument(
        "--worker-count",
        type=int,
        default=4,
        help="AgentRunManager worker count for queue processing.",
    )
    parser.add_argument(
        "--task-latency-ms",
        type=float,
        default=35.0,
        help="Synthetic executor latency per run attempt in milliseconds.",
    )
    parser.add_argument(
        "--inject-failure-every",
        type=int,
        default=0,
        help="Inject one deterministic executor failure every N calls (0 disables).",
    )
    parser.add_argument(
        "--scenario-timeout-sec",
        type=float,
        default=30.0,
        help="Timeout for waiting queue drain to terminal statuses.",
    )
    parser.add_argument(
        "--min-success-rate-pct",
        type=float,
        default=99.0,
        help="Minimum required run success rate percent.",
    )
    parser.add_argument(
        "--max-failed-runs",
        type=int,
        default=0,
        help="Maximum allowed failed/canceled runs.",
    )
    parser.add_argument(
        "--max-p95-queue-wait-ms",
        type=float,
        default=1500.0,
        help="Maximum allowed p95 queue wait latency.",
    )
    parser.add_argument(
        "--max-p95-end-to-end-ms",
        type=float,
        default=5000.0,
        help="Maximum allowed p95 end-to-end run latency.",
    )
    parser.add_argument(
        "--output",
        default="artifacts/mission-queue-load-report.json",
        help="Output report path.",
    )
    return parser.parse_args()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(raw: str) -> datetime | None:
    text = str(raw or "").strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _percentile(values: list[float], p: int) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    rank = max(0, min(len(sorted_values) - 1, int(round((p / 100.0) * (len(sorted_values) - 1)))))
    return float(sorted_values[rank])


class _LoadTaskExecutor:
    def __init__(self, *, latency_ms: float, inject_failure_every: int) -> None:
        self.latency_sec = max(0.0, float(latency_ms)) / 1000.0
        self.inject_failure_every = max(0, int(inject_failure_every))
        self._lock = Lock()
        self.call_count = 0

    def execute(
        self,
        agent: Any,
        user_id: str,
        session_id: str | None,
        user_message: str,
        checkpoint: Any = None,
        run_deadline_monotonic: float | None = None,
        resume_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _ = (run_deadline_monotonic, resume_state)
        with self._lock:
            self.call_count += 1
            call_index = self.call_count

        if self.latency_sec > 0:
            time.sleep(self.latency_sec)

        if self.inject_failure_every > 0 and call_index % self.inject_failure_every == 0:
            raise RuntimeError(f"synthetic load failure call={call_index}")

        if callable(checkpoint):
            checkpoint(
                {
                    "stage": "mission_queue_load_executor",
                    "message": "Synthetic load task completed.",
                    "call_index": call_index,
                }
            )

        duration_ms = round(self.latency_sec * 1000.0, 2)
        return {
            "agent_id": agent.id,
            "user_id": user_id,
            "session_id": session_id,
            "response": f"load-ok:{user_message}",
            "metrics": {
                "model_calls": 1,
                "tool_calls": 0,
                "tool_errors": 0,
                "estimated_tokens": 32,
                "attempt_count": 1,
                "duration_ms": duration_ms,
                "total_attempt_duration_ms": duration_ms,
            },
        }


def _collect_latency_metrics(run: dict[str, Any]) -> tuple[float | None, float | None]:
    created_at = _parse_iso(str(run.get("created_at") or ""))
    finished_at = _parse_iso(str(run.get("finished_at") or ""))
    if created_at is None:
        return None, None

    running_ts: datetime | None = None
    checkpoints = list(run.get("checkpoints") or [])
    for item in checkpoints:
        stage = str(item.get("stage") or "").strip().lower()
        if stage == "running":
            candidate = _parse_iso(str(item.get("timestamp") or ""))
            if candidate is not None:
                running_ts = candidate
                break

    queue_wait_ms: float | None = None
    if running_ts is not None:
        queue_wait_ms = max(0.0, (running_ts - created_at).total_seconds() * 1000.0)

    end_to_end_ms: float | None = None
    if finished_at is not None:
        end_to_end_ms = max(0.0, (finished_at - created_at).total_seconds() * 1000.0)

    return queue_wait_ms, end_to_end_ms


def main() -> int:
    args = _parse_args()
    if args.runs_total <= 0:
        print("[mission-queue-load] --runs-total must be >= 1", file=sys.stderr)
        return 2
    if args.submit_concurrency <= 0:
        print("[mission-queue-load] --submit-concurrency must be >= 1", file=sys.stderr)
        return 2
    if args.worker_count <= 0:
        print("[mission-queue-load] --worker-count must be >= 1", file=sys.stderr)
        return 2
    if args.task_latency_ms < 0:
        print("[mission-queue-load] --task-latency-ms must be >= 0", file=sys.stderr)
        return 2
    if args.scenario_timeout_sec <= 0:
        print("[mission-queue-load] --scenario-timeout-sec must be > 0", file=sys.stderr)
        return 2
    if args.min_success_rate_pct < 0 or args.min_success_rate_pct > 100:
        print("[mission-queue-load] --min-success-rate-pct must be in range 0..100", file=sys.stderr)
        return 2
    if args.max_failed_runs < 0:
        print("[mission-queue-load] --max-failed-runs must be >= 0", file=sys.stderr)
        return 2
    if args.max_p95_queue_wait_ms < 0:
        print("[mission-queue-load] --max-p95-queue-wait-ms must be >= 0", file=sys.stderr)
        return 2
    if args.max_p95_end_to_end_ms < 0:
        print("[mission-queue-load] --max-p95-end-to-end-ms must be >= 0", file=sys.stderr)
        return 2

    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from agents.agent import Agent
    from agents.agent_run_manager import AgentRunManager
    from storage.database import Database

    queue_wait_samples: list[float] = []
    end_to_end_samples: list[float] = []
    results: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="amaryllis-mission-queue-load-") as tmp:
        tmp_root = Path(tmp)
        database = Database(tmp_root / "state.db")
        executor = _LoadTaskExecutor(
            latency_ms=float(args.task_latency_ms),
            inject_failure_every=int(args.inject_failure_every),
        )
        manager = AgentRunManager(
            database=database,
            task_executor=executor,  # type: ignore[arg-type]
            worker_count=int(args.worker_count),
            default_max_attempts=1,
            retry_backoff_sec=0.0,
            retry_max_backoff_sec=0.0,
            retry_jitter_sec=0.0,
        )

        try:
            agent = Agent.create(
                name="Mission Queue Load Agent",
                system_prompt="Load gate synthetic executor",
                model=None,
                tools=[],
                user_id="user-1",
            )
            database.upsert_agent(agent.to_record())
            manager.start()

            def _submit(index: int) -> str:
                run = manager.create_run(
                    agent=agent,
                    user_id="user-1",
                    session_id=f"queue-load-{index}",
                    user_message=f"load-{index}",
                    max_attempts=1,
                )
                return str(run.get("id") or "")

            scenario_started = time.perf_counter()
            submit_started = scenario_started
            with ThreadPoolExecutor(max_workers=int(args.submit_concurrency)) as pool:
                run_ids = list(pool.map(_submit, range(1, int(args.runs_total) + 1)))
            submit_finished = time.perf_counter()

            run_ids = [item for item in run_ids if item]
            terminal_statuses = {"succeeded", "failed", "canceled"}
            deadline = time.time() + float(args.scenario_timeout_sec)
            finalized: dict[str, dict[str, Any]] = {}

            while time.time() < deadline and len(finalized) < len(run_ids):
                for run_id in run_ids:
                    if run_id in finalized:
                        continue
                    run = manager.get_run(run_id)
                    if run is None:
                        continue
                    status = str(run.get("status") or "").strip().lower()
                    if status in terminal_statuses:
                        finalized[run_id] = run
                if len(finalized) >= len(run_ids):
                    break
                time.sleep(0.05)

            for run_id in run_ids:
                run = finalized.get(run_id) or manager.get_run(run_id)
                if run is None:
                    results.append(
                        {
                            "run_id": run_id,
                            "status": "missing",
                            "queue_wait_ms": None,
                            "end_to_end_ms": None,
                            "failure_class": "missing",
                            "stop_reason": "missing",
                        }
                    )
                    continue

                queue_wait_ms, end_to_end_ms = _collect_latency_metrics(run)
                if isinstance(queue_wait_ms, (int, float)):
                    queue_wait_samples.append(float(queue_wait_ms))
                if isinstance(end_to_end_ms, (int, float)):
                    end_to_end_samples.append(float(end_to_end_ms))

                results.append(
                    {
                        "run_id": run_id,
                        "status": str(run.get("status") or ""),
                        "queue_wait_ms": round(float(queue_wait_ms), 2) if queue_wait_ms is not None else None,
                        "end_to_end_ms": round(float(end_to_end_ms), 2) if end_to_end_ms is not None else None,
                        "failure_class": str(run.get("failure_class") or ""),
                        "stop_reason": str(run.get("stop_reason") or ""),
                    }
                )

            submit_duration_ms = (submit_finished - submit_started) * 1000.0
            wall_total_ms = (time.perf_counter() - scenario_started) * 1000.0

            status_counts: dict[str, int] = {}
            for item in results:
                key = str(item.get("status") or "unknown").strip().lower() or "unknown"
                status_counts[key] = status_counts.get(key, 0) + 1

            succeeded = int(status_counts.get("succeeded", 0))
            failed = int(status_counts.get("failed", 0)) + int(status_counts.get("canceled", 0))
            missing = int(status_counts.get("missing", 0))
            total = len(results)

            success_rate_pct = (float(succeeded) / float(total) * 100.0) if total > 0 else 0.0
            completion_rate_pct = (
                float(total - missing) / float(total) * 100.0 if total > 0 else 0.0
            )
            queue_p95 = _percentile(queue_wait_samples, 95)
            end_to_end_p95 = _percentile(end_to_end_samples, 95)

            queued_remaining = len(manager.list_runs(status="queued", limit=max(1, total * 2)))
            running_remaining = len(manager.list_runs(status="running", limit=max(1, total * 2)))

        finally:
            manager.stop()
            database.close()

    report = {
        "generated_at": _utc_now_iso(),
        "suite": "mission_queue_load_gate_v1",
        "config": {
            "runs_total": int(args.runs_total),
            "submit_concurrency": int(args.submit_concurrency),
            "worker_count": int(args.worker_count),
            "task_latency_ms": float(args.task_latency_ms),
            "inject_failure_every": int(args.inject_failure_every),
            "scenario_timeout_sec": float(args.scenario_timeout_sec),
            "min_success_rate_pct": float(args.min_success_rate_pct),
            "max_failed_runs": int(args.max_failed_runs),
            "max_p95_queue_wait_ms": float(args.max_p95_queue_wait_ms),
            "max_p95_end_to_end_ms": float(args.max_p95_end_to_end_ms),
        },
        "summary": {
            "runs_total": total,
            "status_counts": status_counts,
            "succeeded": succeeded,
            "failed_or_canceled": failed,
            "missing": missing,
            "success_rate_pct": round(success_rate_pct, 4),
            "completion_rate_pct": round(completion_rate_pct, 4),
            "p95_queue_wait_ms": round(queue_p95, 2),
            "p95_end_to_end_ms": round(end_to_end_p95, 2),
            "submit_duration_ms": round(submit_duration_ms, 2),
            "wall_total_ms": round(wall_total_ms, 2),
            "queued_remaining": queued_remaining,
            "running_remaining": running_remaining,
        },
        "runs": results,
    }

    output_path = Path(str(args.output).strip())
    if not output_path.is_absolute():
        output_path = project_root / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[mission-queue-load] report={output_path}")
    print(json.dumps(report["summary"], ensure_ascii=False))

    failures: list[str] = []
    if success_rate_pct < float(args.min_success_rate_pct):
        failures.append(
            f"success_rate_pct={round(success_rate_pct, 4)} < {float(args.min_success_rate_pct)}"
        )
    if failed > int(args.max_failed_runs):
        failures.append(f"failed_or_canceled={failed} > {int(args.max_failed_runs)}")
    if queue_p95 > float(args.max_p95_queue_wait_ms):
        failures.append(f"p95_queue_wait_ms={round(queue_p95, 2)} > {float(args.max_p95_queue_wait_ms)}")
    if end_to_end_p95 > float(args.max_p95_end_to_end_ms):
        failures.append(
            f"p95_end_to_end_ms={round(end_to_end_p95, 2)} > {float(args.max_p95_end_to_end_ms)}"
        )
    if missing > 0:
        failures.append(f"missing_terminal_runs={missing} > 0")
    if queued_remaining > 0 or running_remaining > 0:
        failures.append(
            f"queue_not_drained queued_remaining={queued_remaining} running_remaining={running_remaining}"
        )

    if failures:
        print("[mission-queue-load] FAILED")
        for reason in failures:
            print(f"- {reason}")
        return 1

    print("[mission-queue-load] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
