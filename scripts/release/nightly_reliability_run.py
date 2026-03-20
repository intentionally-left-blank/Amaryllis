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


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run extended reliability smoke checks and produce nightly report with "
            "trend deltas for success/latency/stability."
        )
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=int(os.getenv("AMARYLLIS_NIGHTLY_ITERATIONS", "12")),
        help="Number of request rounds across reliability endpoints.",
    )
    parser.add_argument(
        "--min-success-rate-pct",
        type=float,
        default=float(os.getenv("AMARYLLIS_NIGHTLY_MIN_SUCCESS_RATE_PCT", "99.0")),
        help="Minimum allowed success rate percent for strict mode.",
    )
    parser.add_argument(
        "--max-p95-latency-ms",
        type=float,
        default=float(os.getenv("AMARYLLIS_NIGHTLY_MAX_P95_MS", "600")),
        help="Maximum allowed p95 latency in milliseconds for strict mode.",
    )
    parser.add_argument(
        "--max-latency-jitter-ms",
        type=float,
        default=float(os.getenv("AMARYLLIS_NIGHTLY_MAX_JITTER_MS", "120")),
        help="Maximum allowed latency jitter (population stddev) for strict mode.",
    )
    parser.add_argument(
        "--baseline",
        default="eval/baselines/reliability/nightly_smoke_baseline.json",
        help="Optional baseline metrics JSON for trend delta calculation.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional output report path. Default: eval/reports/reliability/nightly_<timestamp>.json",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail with non-zero exit code if strict thresholds are breached.",
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


def _as_float(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


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


def _stability_score(jitter_ms: float) -> float:
    score = 100.0 - min(100.0, max(0.0, jitter_ms))
    return round(score, 2)


def _summarize(latencies_ms: list[float], failures_count: int) -> dict[str, Any]:
    total_requests = len(latencies_ms)
    success_rate_pct = 0.0
    error_rate_pct = 100.0
    if total_requests > 0:
        success_rate_pct = ((total_requests - failures_count) / total_requests) * 100.0
        error_rate_pct = (failures_count / total_requests) * 100.0

    avg_latency_ms = statistics.mean(latencies_ms) if latencies_ms else 0.0
    p95_latency_ms = _percentile(latencies_ms, 95)
    jitter_ms = statistics.pstdev(latencies_ms) if len(latencies_ms) > 1 else 0.0

    return {
        "total_requests": total_requests,
        "failed_requests": failures_count,
        "success_rate_pct": round(success_rate_pct, 4),
        "error_rate_pct": round(error_rate_pct, 4),
        "avg_latency_ms": round(avg_latency_ms, 2),
        "p95_latency_ms": round(p95_latency_ms, 2),
        "latency_jitter_ms": round(jitter_ms, 2),
        "stability_score": _stability_score(jitter_ms=jitter_ms),
    }


def _extract_burn_rate_sample(*, response_payload: Any, round_number: int) -> dict[str, Any] | None:
    if not isinstance(response_payload, dict):
        return None
    quality_budget = response_payload.get("quality_budget")
    snapshot = response_payload.get("snapshot")
    if not isinstance(quality_budget, dict) or not isinstance(snapshot, dict):
        return None

    error_budget = snapshot.get("error_budget")
    sli = snapshot.get("sli")
    if not isinstance(error_budget, dict) or not isinstance(sli, dict):
        return None

    requests_budget = error_budget.get("requests")
    runs_budget = error_budget.get("runs")
    requests_sli = sli.get("requests")
    runs_sli = sli.get("runs")
    if (
        not isinstance(requests_budget, dict)
        or not isinstance(runs_budget, dict)
        or not isinstance(requests_sli, dict)
        or not isinstance(runs_sli, dict)
    ):
        return None

    request_burn_rate = _as_float(requests_budget.get("burn_rate"))
    run_burn_rate = _as_float(runs_budget.get("burn_rate"))
    request_budget = _as_float(quality_budget.get("request_burn_rate"))
    run_budget = _as_float(quality_budget.get("run_burn_rate"))
    request_total = _as_float(requests_sli.get("total"))
    run_total = _as_float(runs_sli.get("total"))
    if (
        request_burn_rate is None
        or run_burn_rate is None
        or request_budget is None
        or run_budget is None
        or request_total is None
        or run_total is None
    ):
        return None

    return {
        "round": int(round_number),
        "request_burn_rate": round(request_burn_rate, 6),
        "run_burn_rate": round(run_burn_rate, 6),
        "request_budget": round(request_budget, 6),
        "run_budget": round(run_budget, 6),
        "request_samples": int(request_total),
        "run_samples": int(run_total),
    }


def _max_consecutive_breach(values: list[float], *, budget: float) -> int:
    if not values:
        return 0
    streak = 0
    max_streak = 0
    for value in values:
        if float(value) > float(budget):
            streak += 1
            max_streak = max(max_streak, streak)
            continue
        streak = 0
    return max_streak


def _summarize_burn_rate(samples: list[dict[str, Any]]) -> dict[str, Any]:
    request_values: list[float] = []
    run_values: list[float] = []
    request_budget = 0.0
    run_budget = 0.0

    for item in samples:
        request_value = _as_float(item.get("request_burn_rate"))
        run_value = _as_float(item.get("run_burn_rate"))
        if request_value is not None:
            request_values.append(request_value)
        if run_value is not None:
            run_values.append(run_value)

        sample_request_budget = _as_float(item.get("request_budget"))
        sample_run_budget = _as_float(item.get("run_budget"))
        if sample_request_budget is not None:
            request_budget = sample_request_budget
        if sample_run_budget is not None:
            run_budget = sample_run_budget

    request_breach_rounds = [
        int(item.get("round", 0))
        for item in samples
        if _as_float(item.get("request_burn_rate")) is not None
        and _as_float(item.get("request_burn_rate")) > request_budget
    ]
    run_breach_rounds = [
        int(item.get("round", 0))
        for item in samples
        if _as_float(item.get("run_burn_rate")) is not None and _as_float(item.get("run_burn_rate")) > run_budget
    ]

    return {
        "sample_count": len(samples),
        "request": {
            "budget": round(float(request_budget), 6),
            "avg": round(statistics.mean(request_values), 6) if request_values else 0.0,
            "p95": round(_percentile(request_values, 95), 6),
            "max": round(max(request_values), 6) if request_values else 0.0,
            "breach_samples": len(request_breach_rounds),
            "max_consecutive_breach_samples": _max_consecutive_breach(
                request_values,
                budget=float(request_budget),
            ),
            "breach_rounds": request_breach_rounds,
        },
        "runs": {
            "budget": round(float(run_budget), 6),
            "avg": round(statistics.mean(run_values), 6) if run_values else 0.0,
            "p95": round(_percentile(run_values, 95), 6),
            "max": round(max(run_values), 6) if run_values else 0.0,
            "breach_samples": len(run_breach_rounds),
            "max_consecutive_breach_samples": _max_consecutive_breach(
                run_values,
                budget=float(run_budget),
            ),
            "breach_rounds": run_breach_rounds,
        },
    }


def _load_baseline(path: Path) -> dict[str, float] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return None
    raw = payload.get("summary", payload)
    if not isinstance(raw, dict):
        return None

    metrics: dict[str, float] = {}
    for key in ("success_rate_pct", "p95_latency_ms", "stability_score", "latency_jitter_ms"):
        value = raw.get(key)
        if isinstance(value, int | float):
            metrics[key] = float(value)
    return metrics or None


def _compute_trend_deltas(summary: dict[str, Any], baseline: dict[str, float] | None) -> dict[str, float] | None:
    if not baseline:
        return None
    deltas: dict[str, float] = {}
    for key in ("success_rate_pct", "p95_latency_ms", "stability_score", "latency_jitter_ms"):
        current = summary.get(key)
        previous = baseline.get(key)
        if isinstance(current, int | float) and isinstance(previous, int | float):
            deltas[f"{key}_delta"] = round(float(current) - float(previous), 4)
    return deltas or None


def _default_output_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return Path("eval/reports/reliability") / f"nightly_{stamp}.json"


def main() -> int:
    args = _parse_args()
    if args.iterations <= 0:
        print("[nightly-reliability] --iterations must be >= 1", file=sys.stderr)
        return 2
    if args.min_success_rate_pct < 0 or args.min_success_rate_pct > 100:
        print("[nightly-reliability] --min-success-rate-pct must be in range 0..100", file=sys.stderr)
        return 2
    if args.max_p95_latency_ms < 0:
        print("[nightly-reliability] --max-p95-latency-ms must be >= 0", file=sys.stderr)
        return 2
    if args.max_latency_jitter_ms < 0:
        print("[nightly-reliability] --max-latency-jitter-ms must be >= 0", file=sys.stderr)
        return 2

    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    try:
        from fastapi.testclient import TestClient
    except Exception as exc:  # pragma: no cover - environment-dependent
        print(f"[nightly-reliability] fastapi testclient unavailable: {exc}", file=sys.stderr)
        return 2

    checks = _request_checks()
    failures: list[dict[str, Any]] = []
    latencies_ms: list[float] = []
    burn_rate_samples: list[dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="amaryllis-nightly-reliability-") as tmp:
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

        with TestClient(app) as client:
            _ = client.get("/health")
            for round_number in range(1, args.iterations + 1):
                for check in checks:
                    headers: dict[str, str] = {}
                    token = check.get("token")
                    if isinstance(token, str) and token.strip():
                        headers = _auth(token)

                    started = time.perf_counter()
                    response = client.request(
                        str(check["method"]),
                        str(check["path"]),
                        headers=headers,
                        json=check.get("payload"),
                    )
                    duration_ms = (time.perf_counter() - started) * 1000.0
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
                        continue

                    if str(check["path"]) == "/service/observability/slo":
                        try:
                            payload = response.json()
                        except Exception:
                            payload = None
                        sample = _extract_burn_rate_sample(
                            response_payload=payload,
                            round_number=round_number,
                        )
                        if sample is not None:
                            burn_rate_samples.append(sample)

        _shutdown_app(app)

    summary = _summarize(latencies_ms=latencies_ms, failures_count=len(failures))
    burn_rate_summary = _summarize_burn_rate(burn_rate_samples)

    baseline_path = Path(str(args.baseline).strip()) if str(args.baseline).strip() else None
    if baseline_path is not None and not baseline_path.is_absolute():
        baseline_path = project_root / baseline_path
    baseline_metrics = _load_baseline(baseline_path) if baseline_path is not None else None
    trend_deltas = _compute_trend_deltas(summary=summary, baseline=baseline_metrics)

    report = {
        "generated_at": _utc_now_iso(),
        "suite": "nightly_reliability_smoke_v1",
        "iterations": args.iterations,
        "endpoint_count": len(checks),
        "thresholds": {
            "min_success_rate_pct": args.min_success_rate_pct,
            "max_p95_latency_ms": args.max_p95_latency_ms,
            "max_latency_jitter_ms": args.max_latency_jitter_ms,
        },
        "summary": summary,
        "baseline": {
            "path": str(baseline_path) if baseline_path is not None else "",
            "metrics": baseline_metrics or {},
        },
        "trend_deltas": trend_deltas or {},
        "burn_rate": {
            "samples": burn_rate_samples,
            "summary": burn_rate_summary,
        },
        "failures": failures,
    }

    output_path = Path(str(args.output).strip()) if str(args.output).strip() else _default_output_path()
    if not output_path.is_absolute():
        output_path = project_root / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[nightly-reliability] report={output_path}")
    print(json.dumps(report["summary"], ensure_ascii=False))
    if trend_deltas:
        print(json.dumps(report["trend_deltas"], ensure_ascii=False))
    if burn_rate_samples:
        print(json.dumps(report["burn_rate"]["summary"], ensure_ascii=False))

    breaches: list[str] = []
    if float(summary["success_rate_pct"]) < float(args.min_success_rate_pct):
        breaches.append(f"success_rate_pct={summary['success_rate_pct']} < {args.min_success_rate_pct}")
    if float(summary["p95_latency_ms"]) > float(args.max_p95_latency_ms):
        breaches.append(f"p95_latency_ms={summary['p95_latency_ms']} > {args.max_p95_latency_ms}")
    if float(summary["latency_jitter_ms"]) > float(args.max_latency_jitter_ms):
        breaches.append(f"latency_jitter_ms={summary['latency_jitter_ms']} > {args.max_latency_jitter_ms}")

    if args.strict and breaches:
        print("[nightly-reliability] FAILED")
        for reason in breaches:
            print(f"- {reason}")
        for item in failures[:20]:
            print(
                "- "
                f"round={item['round']} {item['method']} {item['path']} "
                f"expected={item['expected_status']} got={item['actual_status']} "
                f"latency_ms={item['latency_ms']}"
            )
        return 1

    print("[nightly-reliability] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
