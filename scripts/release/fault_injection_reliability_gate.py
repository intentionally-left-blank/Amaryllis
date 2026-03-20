#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, cast


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run blocking fault-injection reliability gate against AgentRunManager "
            "(provider/network/tool fault classes with retry/recovery assertions)."
        )
    )
    parser.add_argument(
        "--retry-max-attempts",
        type=int,
        default=2,
        help="Max attempts used for transient fault scenarios.",
    )
    parser.add_argument(
        "--scenario-timeout-sec",
        type=float,
        default=8.0,
        help="Timeout per scenario while waiting for terminal run status.",
    )
    parser.add_argument(
        "--min-pass-rate-pct",
        type=float,
        default=100.0,
        help="Minimum required scenario pass rate percent.",
    )
    parser.add_argument(
        "--output",
        default="artifacts/fault-injection-reliability-report.json",
        help="Output report JSON path.",
    )
    return parser.parse_args()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _wait_for_status(
    manager: Any,
    run_id: str,
    expected_statuses: set[str],
    *,
    timeout_sec: float,
) -> dict[str, Any] | None:
    deadline = time.time() + max(0.1, float(timeout_sec))
    while time.time() < deadline:
        run = manager.get_run(run_id)
        if run is None:
            return None
        status = str(run.get("status") or "").strip().lower()
        if status in expected_statuses:
            return run
        time.sleep(0.05)
    return None


class _FaultInjectionTaskExecutor:
    def __init__(
        self,
        *,
        error_sequence: list[Exception] | None = None,
        emit_tool_finished_count: int = 0,
        emit_tool_error_count: int = 0,
    ) -> None:
        self.error_sequence = list(error_sequence or [])
        self.emit_tool_finished_count = max(0, int(emit_tool_finished_count))
        self.emit_tool_error_count = max(0, int(emit_tool_error_count))
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
        self.call_count += 1

        if callable(checkpoint):
            for idx in range(self.emit_tool_finished_count):
                status = "failed" if idx < self.emit_tool_error_count else "succeeded"
                checkpoint(
                    {
                        "stage": "tool_call_finished",
                        "status": status,
                        "duration_ms": 12.0,
                    }
                )

        if self.call_count <= len(self.error_sequence):
            raise self.error_sequence[self.call_count - 1]

        if callable(checkpoint):
            checkpoint(
                {
                    "stage": "fault_injection_executor",
                    "message": "Fault injection scenario completed.",
                }
            )

        return {
            "agent_id": agent.id,
            "user_id": user_id,
            "session_id": session_id,
            "response": f"ok:{user_message}",
            "metrics": {
                "model_calls": 1,
                "tool_calls": self.emit_tool_finished_count,
                "tool_errors": self.emit_tool_error_count,
                "estimated_tokens": 64,
                "attempt_count": 1,
                "duration_ms": 20.0,
                "total_attempt_duration_ms": 20.0,
            },
        }


def _provider_error(*, error_class: str, message: str, retryable: bool) -> Exception:
    from models.provider_errors import ProviderErrorClass, ProviderErrorInfo, ProviderOperationError

    normalized_class = cast(ProviderErrorClass, error_class)

    return ProviderOperationError(
        ProviderErrorInfo(
            provider="chaos",
            operation="chat",
            error_class=normalized_class,
            message=message,
            raw_message=message,
            retryable=retryable,
            status_code=429 if error_class == "rate_limit" else 503,
        )
    )


def _evaluate_result(
    *,
    scenario: dict[str, Any],
    final: dict[str, Any] | None,
) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if final is None:
        return False, ["run did not reach terminal status before timeout"]

    expected_status = str(scenario.get("expected_status") or "")
    actual_status = str(final.get("status") or "")
    if expected_status and actual_status != expected_status:
        reasons.append(f"status expected={expected_status} got={actual_status}")

    attempts_min = int(scenario.get("expected_attempts_min", 0) or 0)
    attempts = int(final.get("attempts", 0) or 0)
    if attempts_min > 0 and attempts < attempts_min:
        reasons.append(f"attempts expected>={attempts_min} got={attempts}")

    expected_failure_class = str(scenario.get("expected_failure_class") or "")
    actual_failure_class = str(final.get("failure_class") or "")
    if expected_failure_class and actual_failure_class != expected_failure_class:
        reasons.append(
            f"failure_class expected={expected_failure_class} got={actual_failure_class}"
        )

    expected_stop_reason = str(scenario.get("expected_stop_reason") or "")
    actual_stop_reason = str(final.get("stop_reason") or "")
    if expected_stop_reason and actual_stop_reason != expected_stop_reason:
        reasons.append(f"stop_reason expected={expected_stop_reason} got={actual_stop_reason}")

    checkpoints = list(final.get("checkpoints") or [])
    checkpoint_stages = [str(item.get("stage") or "") for item in checkpoints]
    require_retry_stage = bool(scenario.get("require_retry_stage", False))
    if require_retry_stage and "retry_scheduled" not in checkpoint_stages:
        reasons.append("retry_scheduled checkpoint missing")
    if not require_retry_stage and bool(scenario.get("forbid_retry_stage", False)):
        if "retry_scheduled" in checkpoint_stages:
            reasons.append("unexpected retry_scheduled checkpoint")

    expected_error_class = str(scenario.get("expected_error_checkpoint_class") or "")
    if expected_error_class:
        errors = [item for item in checkpoints if str(item.get("stage") or "") == "error"]
        found = any(str(item.get("failure_class") or "") == expected_error_class for item in errors)
        if not found:
            reasons.append(
                f"error checkpoint with failure_class={expected_error_class} not found"
            )

    return (len(reasons) == 0), reasons


def _run_scenario(*, scenario: dict[str, Any], timeout_sec: float, retry_max_attempts: int) -> dict[str, Any]:
    from agents.agent import Agent
    from agents.agent_run_manager import AgentRunManager
    from storage.database import Database

    with tempfile.TemporaryDirectory(prefix="amaryllis-fault-injection-") as tmp:
        tmp_root = Path(tmp)
        database = Database(tmp_root / "state.db")
        executor = _FaultInjectionTaskExecutor(
            error_sequence=list(scenario.get("error_sequence") or []),
            emit_tool_finished_count=int(scenario.get("emit_tool_finished_count", 0) or 0),
            emit_tool_error_count=int(scenario.get("emit_tool_error_count", 0) or 0),
        )
        manager = AgentRunManager(
            database=database,
            task_executor=executor,  # type: ignore[arg-type]
            worker_count=1,
            default_max_attempts=max(1, int(retry_max_attempts)),
            retry_backoff_sec=0.0,
            retry_max_backoff_sec=0.0,
            retry_jitter_sec=0.0,
        )

        try:
            agent = Agent.create(
                name=f"Fault Injection {scenario['id']}",
                system_prompt="Fault injection gate",
                model=None,
                tools=[],
                user_id="user-1",
            )
            database.upsert_agent(agent.to_record())
            manager.start()
            run = manager.create_run(
                agent=agent,
                user_id="user-1",
                session_id=f"fault-{scenario['id']}",
                user_message=str(scenario.get("message") or scenario["id"]),
                max_attempts=max(1, int(scenario.get("max_attempts") or retry_max_attempts)),
                budget=scenario.get("budget"),
            )

            final = _wait_for_status(
                manager,
                str(run.get("id") or ""),
                {"succeeded", "failed", "canceled"},
                timeout_sec=timeout_sec,
            )

            passed, reasons = _evaluate_result(scenario=scenario, final=final)

            return {
                "id": scenario["id"],
                "title": scenario["title"],
                "passed": passed,
                "reasons": reasons,
                "final_status": str((final or {}).get("status") or ""),
                "attempts": int((final or {}).get("attempts", 0) or 0),
                "failure_class": str((final or {}).get("failure_class") or ""),
                "stop_reason": str((final or {}).get("stop_reason") or ""),
            }
        finally:
            manager.stop()
            database.close()


def main() -> int:
    args = _parse_args()
    if args.retry_max_attempts <= 0:
        print("[fault-injection] --retry-max-attempts must be >= 1", file=sys.stderr)
        return 2
    if args.scenario_timeout_sec <= 0:
        print("[fault-injection] --scenario-timeout-sec must be > 0", file=sys.stderr)
        return 2
    if args.min_pass_rate_pct < 0 or args.min_pass_rate_pct > 100:
        print("[fault-injection] --min-pass-rate-pct must be in range 0..100", file=sys.stderr)
        return 2

    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    scenarios: list[dict[str, Any]] = [
        {
            "id": "provider_rate_limit_recovery",
            "title": "Provider rate-limit transient failure retries then succeeds",
            "error_sequence": [
                _provider_error(
                    error_class="rate_limit",
                    message="429 Too Many Requests",
                    retryable=True,
                )
            ],
            "expected_status": "succeeded",
            "expected_attempts_min": 2,
            "require_retry_stage": True,
            "expected_error_checkpoint_class": "rate_limit",
        },
        {
            "id": "network_fault_recovery",
            "title": "Network transient failure retries then succeeds",
            "error_sequence": [
                _provider_error(
                    error_class="network",
                    message="connection reset by peer",
                    retryable=True,
                )
            ],
            "expected_status": "succeeded",
            "expected_attempts_min": 2,
            "require_retry_stage": True,
            "expected_error_checkpoint_class": "network",
        },
        {
            "id": "tool_fault_budget_guardrail",
            "title": "Tool fault class triggers deterministic budget guardrail failure",
            "emit_tool_finished_count": 1,
            "emit_tool_error_count": 1,
            "budget": {
                "max_tokens": 10000,
                "max_duration_sec": 120,
                "max_tool_calls": 8,
                "max_tool_errors": 0,
            },
            "expected_status": "failed",
            "expected_failure_class": "budget_exceeded",
            "expected_stop_reason": "budget_exceeded",
            "forbid_retry_stage": True,
        },
    ]

    results: list[dict[str, Any]] = []
    for scenario in scenarios:
        result = _run_scenario(
            scenario=scenario,
            timeout_sec=float(args.scenario_timeout_sec),
            retry_max_attempts=int(args.retry_max_attempts),
        )
        results.append(result)

    total = len(results)
    passed = sum(1 for item in results if bool(item.get("passed")))
    failed = total - passed
    pass_rate_pct = (float(passed) / float(total) * 100.0) if total > 0 else 0.0

    report = {
        "generated_at": _utc_now_iso(),
        "suite": "fault_injection_reliability_v1",
        "summary": {
            "scenario_count": total,
            "passed": passed,
            "failed": failed,
            "pass_rate_pct": round(pass_rate_pct, 4),
            "min_pass_rate_pct": float(args.min_pass_rate_pct),
            "retry_max_attempts": int(args.retry_max_attempts),
            "scenario_timeout_sec": float(args.scenario_timeout_sec),
        },
        "scenarios": results,
    }

    output_path = Path(str(args.output).strip())
    if not output_path.is_absolute():
        output_path = project_root / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[fault-injection] report={output_path}")
    print(json.dumps(report["summary"], ensure_ascii=False))

    failures: list[str] = []
    if pass_rate_pct < float(args.min_pass_rate_pct):
        failures.append(f"pass_rate_pct={round(pass_rate_pct, 4)} < {float(args.min_pass_rate_pct)}")
    for item in results:
        if not bool(item.get("passed")):
            reasons = "; ".join(str(reason) for reason in list(item.get("reasons") or []))
            failures.append(f"{item['id']}: {reasons}")

    if failures:
        print("[fault-injection] FAILED")
        for reason in failures:
            print(f"- {reason}")
        return 1

    print("[fault-injection] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
