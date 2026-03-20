#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run blocking performance smoke checks against a local TestClient runtime "
            "and fail on latency/error-rate regression."
        )
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=int(os.getenv("AMARYLLIS_PERF_SMOKE_ITERATIONS", "3")),
        help="Number of request rounds across smoke endpoints.",
    )
    parser.add_argument(
        "--max-p95-latency-ms",
        type=float,
        default=float(
            os.getenv(
                "AMARYLLIS_PERF_SMOKE_MAX_P95_MS",
                os.getenv("AMARYLLIS_PERF_BUDGET_MAX_P95_MS", "350"),
            )
        ),
        help="Maximum allowed p95 request latency in milliseconds.",
    )
    parser.add_argument(
        "--max-error-rate-pct",
        type=float,
        default=float(
            os.getenv(
                "AMARYLLIS_PERF_SMOKE_MAX_ERROR_RATE_PCT",
                os.getenv("AMARYLLIS_PERF_BUDGET_MAX_ERROR_RATE_PCT", "0"),
            )
        ),
        help="Maximum allowed request error rate in percent.",
    )
    parser.add_argument(
        "--output",
        default=os.getenv("AMARYLLIS_PERF_SMOKE_OUTPUT", ""),
        help="Optional report path for JSON output.",
    )
    return parser.parse_args()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _percentile(values: list[float], p: int) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    rank = max(0, min(len(sorted_values) - 1, int(round((p / 100.0) * (len(sorted_values) - 1)))))
    return float(sorted_values[rank])


def _shutdown_app(app: Any) -> None:
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


def _request_checks() -> list[dict[str, Any]]:
    return [
        {"method": "GET", "path": "/health", "expected": 200, "token": None, "payload": None},
        {"method": "GET", "path": "/service/health", "expected": 200, "token": "service-token", "payload": None},
        {"method": "GET", "path": "/v1/models", "expected": 200, "token": "user-token", "payload": None},
        {"method": "GET", "path": "/models", "expected": 200, "token": "user-token", "payload": None},
        {
            "method": "GET",
            "path": "/service/observability/slo",
            "expected": 200,
            "token": "service-token",
            "payload": None,
        },
        {
            "method": "GET",
            "path": "/service/api/lifecycle",
            "expected": 200,
            "token": "service-token",
            "payload": None,
        },
        {
            "method": "GET",
            "path": "/service/backup/status",
            "expected": 200,
            "token": "service-token",
            "payload": None,
        },
        {
            "method": "POST",
            "path": "/v1/models/route",
            "expected": 200,
            "token": "user-token",
            "payload": {"mode": "balanced"},
        },
    ]


def main() -> int:
    args = _parse_args()
    if args.iterations <= 0:
        print("[perf-smoke] --iterations must be >= 1", file=sys.stderr)
        return 2
    if args.max_p95_latency_ms < 0:
        print("[perf-smoke] --max-p95-latency-ms must be >= 0", file=sys.stderr)
        return 2
    if args.max_error_rate_pct < 0:
        print("[perf-smoke] --max-error-rate-pct must be >= 0", file=sys.stderr)
        return 2

    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    try:
        from fastapi.testclient import TestClient
    except Exception as exc:  # pragma: no cover - environment-dependent
        print(f"[perf-smoke] fastapi testclient unavailable: {exc}")
        return 2

    report: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="amaryllis-perf-smoke-") as tmp:
        support_dir = Path(tmp) / "support"
        auth_tokens = {
            "admin-token": {"user_id": "admin", "scopes": ["admin", "user"]},
            "user-token": {"user_id": "user-1", "scopes": ["user"]},
            "service-token": {"user_id": "svc-runtime", "scopes": ["service"]},
        }
        os.environ["AMARYLLIS_SUPPORT_DIR"] = str(support_dir)
        os.environ["AMARYLLIS_AUTH_ENABLED"] = "true"
        os.environ["AMARYLLIS_AUTH_TOKENS"] = json.dumps(auth_tokens, ensure_ascii=False)
        os.environ["AMARYLLIS_MEMORY_CONSOLIDATION_ENABLED"] = "false"
        os.environ["AMARYLLIS_MCP_ENDPOINTS"] = ""
        os.environ["AMARYLLIS_SECURITY_PROFILE"] = "production"

        import runtime.server as server_module

        server_module = importlib.reload(server_module)
        app = server_module.app
        latencies_ms: list[float] = []
        failures: list[dict[str, Any]] = []
        checks = _request_checks()
        started_at = datetime.now(timezone.utc).isoformat()

        with TestClient(app) as client:
            # Warm up internal lazy paths before measured rounds.
            _ = client.get("/health")
            for round_number in range(1, args.iterations + 1):
                for check in checks:
                    headers: dict[str, str] = {}
                    token = check.get("token")
                    if isinstance(token, str) and token.strip():
                        headers = _auth(token)
                    request_started = time.perf_counter()
                    response = client.request(
                        str(check["method"]),
                        str(check["path"]),
                        headers=headers,
                        json=check.get("payload"),
                    )
                    duration_ms = (time.perf_counter() - request_started) * 1000.0
                    latencies_ms.append(duration_ms)

                    expected_status = int(check["expected"])
                    actual_status = int(response.status_code)
                    if actual_status != expected_status:
                        failures.append(
                            {
                                "round": round_number,
                                "method": check["method"],
                                "path": check["path"],
                                "expected_status": expected_status,
                                "actual_status": actual_status,
                                "latency_ms": round(duration_ms, 2),
                            }
                        )

        _shutdown_app(app)

        total_requests = len(latencies_ms)
        failed_requests = len(failures)
        error_rate_pct = (failed_requests / total_requests) * 100.0 if total_requests else 100.0
        avg_latency_ms = statistics.mean(latencies_ms) if latencies_ms else 0.0
        p95_latency_ms = _percentile(latencies_ms, 95)

        report = {
            "generated_at": started_at,
            "iterations": args.iterations,
            "endpoint_count": len(checks),
            "summary": {
                "total_requests": total_requests,
                "failed_requests": failed_requests,
                "error_rate_pct": round(error_rate_pct, 4),
                "avg_latency_ms": round(avg_latency_ms, 2),
                "p95_latency_ms": round(p95_latency_ms, 2),
            },
            "thresholds": {
                "max_p95_latency_ms": args.max_p95_latency_ms,
                "max_error_rate_pct": args.max_error_rate_pct,
            },
            "failures": failures,
        }

    output_path = Path(args.output) if str(args.output).strip() else None
    if output_path is not None:
        if not output_path.is_absolute():
            output_path = project_root / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[perf-smoke] report={output_path}")

    print(json.dumps(report["summary"], ensure_ascii=False))

    breach_reasons: list[str] = []
    p95_value = float(report["summary"]["p95_latency_ms"])
    error_rate_value = float(report["summary"]["error_rate_pct"])

    if p95_value > float(args.max_p95_latency_ms):
        breach_reasons.append(f"p95_latency_ms={p95_value} > {args.max_p95_latency_ms}")
    if error_rate_value > float(args.max_error_rate_pct):
        breach_reasons.append(f"error_rate_pct={error_rate_value} > {args.max_error_rate_pct}")

    if breach_reasons:
        print("[perf-smoke] FAILED")
        for reason in breach_reasons:
            print(f"- {reason}")
        for failure in report["failures"][:20]:
            print(
                "- "
                f"round={failure['round']} {failure['method']} {failure['path']} "
                f"expected={failure['expected_status']} got={failure['actual_status']} "
                f"latency_ms={failure['latency_ms']}"
            )
        return 1

    print("[perf-smoke] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
