#!/usr/bin/env python3
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run Agent Factory quickstart plan latency gate under concurrent load "
            "and validate API SLO envelope."
        )
    )
    parser.add_argument(
        "--requests-total",
        type=int,
        default=30,
        help="Total number of quickstart plan requests to execute.",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=6,
        help="Concurrent request workers.",
    )
    parser.add_argument(
        "--max-p95-latency-ms",
        type=float,
        default=2000.0,
        help="Maximum allowed p95 latency in milliseconds.",
    )
    parser.add_argument(
        "--max-error-rate-pct",
        type=float,
        default=0.0,
        help="Maximum allowed error rate in percent.",
    )
    parser.add_argument(
        "--output",
        default="artifacts/agent-factory-plan-perf-gate-report.json",
        help="Output report path.",
    )
    return parser.parse_args()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _percentile(values: list[float], p: int) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    rank = max(0, min(len(sorted_values) - 1, int(round((p / 100.0) * (len(sorted_values) - 1)))))
    return float(sorted_values[rank])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _shutdown_app(app: object) -> None:
    services = getattr(getattr(app, "state", None), "services", None)
    if services is None:
        return
    try:
        services.automation_scheduler.stop()
        if services.memory_consolidation_worker is not None:
            services.memory_consolidation_worker.stop()
        if services.backup_scheduler is not None:
            services.backup_scheduler.stop()
        services.agent_run_manager.stop()
        services.database.close()
        services.vector_store.persist()
    except Exception:
        pass


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    args = _parse_args()
    if args.requests_total <= 0:
        print("[agent-factory-plan-perf-gate] --requests-total must be >= 1", file=sys.stderr)
        return 2
    if args.concurrency <= 0:
        print("[agent-factory-plan-perf-gate] --concurrency must be >= 1", file=sys.stderr)
        return 2
    if args.max_p95_latency_ms < 0:
        print("[agent-factory-plan-perf-gate] --max-p95-latency-ms must be >= 0", file=sys.stderr)
        return 2
    if args.max_error_rate_pct < 0 or args.max_error_rate_pct > 100:
        print("[agent-factory-plan-perf-gate] --max-error-rate-pct must be in range 0..100", file=sys.stderr)
        return 2

    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    try:
        from fastapi.testclient import TestClient  # noqa: PLC0415
    except Exception as exc:
        print(f"[agent-factory-plan-perf-gate] import_error={exc}", file=sys.stderr)
        return 2

    request_pool = [
        "создай агента для AI новостей каждый день в 08:15 из reddit и twitter",
        "create an agent for python package maintenance from pypi.org and github.com every 6 hours at 10 minute",
        "создай агента для AI новостей по будням в 09:30 timezone Asia/Almaty",
        "create an agent for AI digest on weekends at 11:45 UTC+5",
        "create an agent for AI digest every day at 8:30pm PST",
        "создай агента для AI новостей по будням утром по времени мск",
        "create an agent for AI digest in 3 hours CET",
        "создай агента для личной продуктивности",
    ]

    with tempfile.TemporaryDirectory(prefix="amaryllis-agent-factory-plan-perf-gate-") as tmp:
        support_dir = Path(tmp) / "support"
        auth_tokens = {
            "perf-user-token": {"user_id": "perf-user-1", "scopes": ["user"]},
            "perf-admin-token": {"user_id": "perf-admin", "scopes": ["admin", "user"]},
        }
        os.environ["AMARYLLIS_SUPPORT_DIR"] = str(support_dir)
        os.environ["AMARYLLIS_AUTH_ENABLED"] = "true"
        os.environ["AMARYLLIS_AUTH_TOKENS"] = json.dumps(auth_tokens, ensure_ascii=False)
        os.environ["AMARYLLIS_MEMORY_CONSOLIDATION_ENABLED"] = "false"
        os.environ["AMARYLLIS_MCP_ENDPOINTS"] = ""
        os.environ["AMARYLLIS_SECURITY_PROFILE"] = "production"
        os.environ["AMARYLLIS_COGNITION_BACKEND"] = "deterministic"
        os.environ["AMARYLLIS_AUTOMATION_ENABLED"] = "false"
        os.environ["AMARYLLIS_BACKUP_ENABLED"] = "false"
        os.environ["AMARYLLIS_BACKUP_RESTORE_DRILL_ENABLED"] = "false"
        os.environ["AMARYLLIS_REQUEST_TRACE_LOGS_ENABLED"] = "false"

        try:
            import runtime.server as server_module  # noqa: PLC0415
            server_module = importlib.reload(server_module)
        except Exception as exc:
            print(f"[agent-factory-plan-perf-gate] import_error={exc}", file=sys.stderr)
            return 2
        app = server_module.create_app()
        samples: list[dict[str, Any]] = []
        started = time.perf_counter()
        try:
            with TestClient(app) as client:

                def _submit(index: int) -> dict[str, Any]:
                    request_text = request_pool[index % len(request_pool)]
                    payload = {
                        "user_id": "perf-user-1",
                        "request": request_text,
                    }
                    began = time.perf_counter()
                    response = client.post(
                        "/v1/agents/quickstart/plan",
                        headers=_auth("perf-user-token"),
                        json=payload,
                    )
                    elapsed_ms = (time.perf_counter() - began) * 1000.0
                    body: dict[str, Any] = {}
                    if response.headers.get("content-type", "").startswith("application/json"):
                        raw_body = response.json()
                        if isinstance(raw_body, dict):
                            body = raw_body
                    quickstart_plan = body.get("quickstart_plan")
                    is_valid_payload = isinstance(quickstart_plan, dict) and isinstance(
                        (quickstart_plan or {}).get("inference_reason"), dict
                    )
                    success = bool(response.status_code == 200 and is_valid_payload)
                    return {
                        "index": index,
                        "request": request_text,
                        "status_code": int(response.status_code),
                        "success": success,
                        "latency_ms": round(elapsed_ms, 3),
                    }

                with ThreadPoolExecutor(max_workers=int(args.concurrency)) as pool:
                    samples = list(pool.map(_submit, range(int(args.requests_total))))
        finally:
            _shutdown_app(app)

        total_duration_ms = (time.perf_counter() - started) * 1000.0

    latency_values = [float(item.get("latency_ms") or 0.0) for item in samples]
    success_count = len([item for item in samples if bool(item.get("success", False))])
    failure_samples = [item for item in samples if not bool(item.get("success", False))]
    error_rate_pct = (float(len(failure_samples)) / float(len(samples))) * 100.0 if samples else 100.0
    p50_latency_ms = _percentile(latency_values, 50)
    p95_latency_ms = _percentile(latency_values, 95)
    max_latency_ms = max(latency_values) if latency_values else 0.0

    breaches: list[str] = []
    if p95_latency_ms > float(args.max_p95_latency_ms):
        breaches.append(f"p95_latency_ms={p95_latency_ms:.3f} > {float(args.max_p95_latency_ms):.3f}")
    if error_rate_pct > float(args.max_error_rate_pct):
        breaches.append(f"error_rate_pct={error_rate_pct:.3f} > {float(args.max_error_rate_pct):.3f}")

    status = "pass" if not breaches else "fail"
    report = {
        "generated_at": _utc_now_iso(),
        "suite": "agent_factory_plan_perf_gate_v1",
        "summary": {
            "status": status,
            "requests_total": int(len(samples)),
            "requests_succeeded": int(success_count),
            "requests_failed": int(len(failure_samples)),
            "error_rate_pct": round(error_rate_pct, 4),
            "p50_latency_ms": round(p50_latency_ms, 4),
            "p95_latency_ms": round(p95_latency_ms, 4),
            "max_latency_ms": round(max_latency_ms, 4),
            "total_duration_ms": round(total_duration_ms, 4),
        },
        "thresholds": {
            "max_p95_latency_ms": float(args.max_p95_latency_ms),
            "max_error_rate_pct": float(args.max_error_rate_pct),
        },
        "breaches": breaches,
        "failure_samples": failure_samples[:20],
    }

    output_path = Path(str(args.output)).expanduser()
    if not output_path.is_absolute():
        output_path = (project_root / output_path).resolve()
    _write_report(output_path, report)

    if status == "fail":
        print("[agent-factory-plan-perf-gate] FAILED")
        for reason in breaches:
            print(f" - {reason}")
        print(f"[agent-factory-plan-perf-gate] report={output_path}")
        return 1

    print(
        "[agent-factory-plan-perf-gate] OK "
        f"requests={len(samples)} p95={p95_latency_ms:.3f}ms error_rate={error_rate_pct:.3f}%"
    )
    print(f"[agent-factory-plan-perf-gate] report={output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
