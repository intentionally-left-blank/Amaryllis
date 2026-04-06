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
    parser.add_argument(
        "--qos-mode",
        default=os.getenv("AMARYLLIS_QOS_MODE", "balanced"),
        help="QoS mode to benchmark (`quality`, `balanced`, `power_save`).",
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


def _normalize_qos_mode(value: str | None) -> str:
    mode = str(value or "").strip().lower()
    if mode in {"quality", "balanced", "power_save"}:
        return mode
    return "balanced"


def _route_mode_for_qos(qos_mode: str) -> str:
    mapping = {
        "quality": "quality_first",
        "balanced": "balanced",
        "power_save": "local_first",
    }
    return mapping.get(_normalize_qos_mode(qos_mode), "balanced")


def _install_perf_smoke_stubs(app: Any) -> None:
    services = getattr(getattr(app, "state", None), "services", None)
    if services is None:
        return
    model_manager = getattr(services, "model_manager", None)
    if model_manager is None:
        return

    def _fake_list_models(
        *,
        include_suggested: bool = True,
        include_remote_providers: bool = True,
        max_items_per_provider: int = 120,
    ) -> dict[str, Any]:
        _ = (include_suggested, include_remote_providers, max_items_per_provider)
        provider = str(getattr(model_manager, "active_provider", "smoke"))
        model = str(getattr(model_manager, "active_model", "smoke-model"))
        return {
            "active_provider": provider,
            "active_model": model,
            "items": [
                {
                    "provider": provider,
                    "model": model,
                    "source": "smoke_stub",
                    "ready": True,
                    "supports_stream": True,
                    "supports_tools": True,
                }
            ],
            "count": 1,
        }

    def _fake_choose_route(
        *,
        mode: str,
        provider: str | None = None,
        model: str | None = None,
        require_stream: bool = True,
        require_tools: bool = False,
        prefer_local: bool | None = None,
        min_params_b: float | None = None,
        max_params_b: float | None = None,
        include_suggested: bool = False,
        limit_per_provider: int = 120,
    ) -> dict[str, Any]:
        _ = (
            mode,
            require_stream,
            require_tools,
            prefer_local,
            min_params_b,
            max_params_b,
            include_suggested,
            limit_per_provider,
        )
        provider_value = provider or str(getattr(model_manager, "active_provider", "smoke"))
        model_value = model or str(getattr(model_manager, "active_model", "smoke-model"))
        return {
            "provider": provider_value,
            "model": model_value,
            "reason": "perf_smoke_stub",
            "alternates": [],
        }

    def _fake_provider_health() -> dict[str, Any]:
        provider = str(getattr(model_manager, "active_provider", "smoke"))
        model = str(getattr(model_manager, "active_model", "smoke-model"))
        return {
            provider: {
                "status": "ok",
                "provider": provider,
                "active_model": model,
                "latency_ms": 1.0,
                "source": "perf_smoke_stub",
            }
        }

    def _fake_chat(
        *,
        messages: list[dict[str, Any]],
        model: str | None = None,
        provider: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 512,
        routing: dict[str, Any] | None = None,
        fallback_targets: list[tuple[str, str]] | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        **extra_kwargs: Any,
    ) -> dict[str, Any]:
        _ = (messages, temperature, max_tokens, routing, fallback_targets, session_id, user_id, extra_kwargs)
        provider_value = provider or str(getattr(model_manager, "active_provider", "smoke"))
        model_value = model or str(getattr(model_manager, "active_model", "smoke-model"))
        return {
            "content": "perf-smoke-ok",
            "provider": provider_value,
            "model": model_value,
            "routing": {"mode": "perf_smoke_stub"},
        }

    def _fake_stream_chat(
        *,
        messages: list[dict[str, Any]],
        model: str | None = None,
        provider: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 512,
        routing: dict[str, Any] | None = None,
        fallback_targets: list[tuple[str, str]] | None = None,
        session_id: str | None = None,
        user_id: str | None = None,
        **extra_kwargs: Any,
    ) -> tuple[Any, str, str, dict[str, Any]]:
        _ = (messages, temperature, max_tokens, routing, fallback_targets, session_id, user_id, extra_kwargs)
        provider_value = provider or str(getattr(model_manager, "active_provider", "smoke"))
        model_value = model or str(getattr(model_manager, "active_model", "smoke-model"))
        return iter(["perf", "-smoke", "-stream"]), provider_value, model_value, {"mode": "perf_smoke_stub"}

    model_manager.list_models = _fake_list_models
    model_manager.choose_route = _fake_choose_route
    model_manager.provider_health = _fake_provider_health
    model_manager.chat = _fake_chat
    model_manager.stream_chat = _fake_stream_chat


def _request_checks(*, qos_mode: str) -> list[dict[str, Any]]:
    route_mode = _route_mode_for_qos(qos_mode)
    return [
        {"label": "health", "method": "GET", "path": "/health", "expected": 200, "token": None, "payload": None},
        {
            "label": "service_health",
            "method": "GET",
            "path": "/service/health",
            "expected": 200,
            "token": "service-token",
            "payload": None,
        },
        {
            "label": "models_list",
            "method": "GET",
            "path": "/v1/models",
            "expected": 200,
            "token": "user-token",
            "payload": None,
        },
        {
            "label": "slo_snapshot",
            "method": "GET",
            "path": "/service/observability/slo",
            "expected": 200,
            "token": "service-token",
            "payload": None,
        },
        {
            "label": "api_lifecycle",
            "method": "GET",
            "path": "/service/api/lifecycle",
            "expected": 200,
            "token": "service-token",
            "payload": None,
        },
        {
            "label": "backup_status",
            "method": "GET",
            "path": "/service/backup/status",
            "expected": 200,
            "token": "service-token",
            "payload": None,
        },
        {
            "label": "route",
            "method": "POST",
            "path": "/v1/models/route",
            "expected": 200,
            "token": "user-token",
            "payload": {"mode": route_mode},
        },
        {
            "label": "voice_stt_health",
            "method": "GET",
            "path": "/voice/stt/health",
            "expected": 200,
            "token": "user-token",
            "payload": None,
        },
        {
            "label": "chat_non_stream",
            "method": "POST",
            "path": "/v1/chat/completions",
            "expected": 200,
            "token": "user-token",
            "payload": {
                "messages": [{"role": "user", "content": "perf smoke non-stream"}],
                "stream": False,
                "max_tokens": 64,
                "routing": {"mode": route_mode},
            },
        },
        {
            "label": "chat_stream",
            "method": "POST",
            "path": "/v1/chat/completions",
            "expected": 200,
            "token": "user-token",
            "payload": {
                "messages": [{"role": "user", "content": "perf smoke stream"}],
                "stream": True,
                "max_tokens": 64,
                "routing": {"mode": route_mode},
            },
        },
    ]


def _run_check(
    *,
    client: Any,
    check: dict[str, Any],
    round_number: int,
    latencies_ms: list[float],
    failures: list[dict[str, Any]],
    latency_by_label: dict[str, list[float]],
) -> Any:
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

    label = str(check.get("label") or check.get("path") or "unknown")
    latency_by_label.setdefault(label, []).append(duration_ms)

    expected_status = int(check["expected"])
    actual_status = int(response.status_code)
    if actual_status != expected_status:
        failures.append(
            {
                "round": round_number,
                "label": label,
                "method": check["method"],
                "path": check["path"],
                "expected_status": expected_status,
                "actual_status": actual_status,
                "latency_ms": round(duration_ms, 2),
            }
        )

    return response


def main() -> int:
    args = _parse_args()
    qos_mode = _normalize_qos_mode(args.qos_mode)
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
        os.environ["AMARYLLIS_QOS_MODE"] = qos_mode

        import runtime.server as server_module

        server_module = importlib.reload(server_module)
        app = server_module.app
        _install_perf_smoke_stubs(app)
        latencies_ms: list[float] = []
        failures: list[dict[str, Any]] = []
        latency_by_label: dict[str, list[float]] = {}
        checks = _request_checks(qos_mode=qos_mode)
        started_at = datetime.now(timezone.utc).isoformat()

        with TestClient(app) as client:
            # Warm up internal lazy paths before measured rounds.
            _ = client.get("/health")

            agent_response = _run_check(
                client=client,
                check={
                    "label": "run_agent_create",
                    "method": "POST",
                    "path": "/agents/create",
                    "expected": 200,
                    "token": "user-token",
                    "payload": {
                        "name": "perf-smoke-agent",
                        "system_prompt": "Respond briefly for perf smoke checks.",
                    },
                },
                round_number=0,
                latencies_ms=latencies_ms,
                failures=failures,
                latency_by_label=latency_by_label,
            )
            agent_id = (
                str(agent_response.json().get("id") or "").strip()
                if int(getattr(agent_response, "status_code", 0)) == 200
                else ""
            )
            if not agent_id:
                failures.append(
                    {
                        "round": 0,
                        "label": "run_agent_create",
                        "method": "POST",
                        "path": "/agents/create",
                        "expected_status": 200,
                        "actual_status": int(getattr(agent_response, "status_code", 0)),
                        "latency_ms": 0.0,
                        "error": "agent_id_missing",
                    }
                )

            for round_number in range(1, args.iterations + 1):
                for check in checks:
                    _run_check(
                        client=client,
                        check=check,
                        round_number=round_number,
                        latencies_ms=latencies_ms,
                        failures=failures,
                        latency_by_label=latency_by_label,
                    )

                if agent_id:
                    run_response = _run_check(
                        client=client,
                        check={
                            "label": "run_create",
                            "method": "POST",
                            "path": f"/agents/{agent_id}/runs",
                            "expected": 200,
                            "token": "user-token",
                            "payload": {
                                "user_id": "user-1",
                                "message": f"perf smoke run round {round_number}",
                                "max_attempts": 1,
                            },
                        },
                        round_number=round_number,
                        latencies_ms=latencies_ms,
                        failures=failures,
                        latency_by_label=latency_by_label,
                    )
                    run_id = (
                        str(run_response.json().get("run", {}).get("id") or "").strip()
                        if int(getattr(run_response, "status_code", 0)) == 200
                        else ""
                    )
                    if run_id:
                        _run_check(
                            client=client,
                            check={
                                "label": "run_get",
                                "method": "GET",
                                "path": f"/agents/runs/{run_id}",
                                "expected": 200,
                                "token": "user-token",
                                "payload": None,
                            },
                            round_number=round_number,
                            latencies_ms=latencies_ms,
                            failures=failures,
                            latency_by_label=latency_by_label,
                        )

                voice_start_response = _run_check(
                    client=client,
                    check={
                        "label": "voice_start",
                        "method": "POST",
                        "path": "/voice/sessions/start",
                        "expected": 200,
                        "token": "user-token",
                        "payload": {"mode": "ptt"},
                    },
                    round_number=round_number,
                    latencies_ms=latencies_ms,
                    failures=failures,
                    latency_by_label=latency_by_label,
                )
                voice_session_id = (
                    str(voice_start_response.json().get("voice_session", {}).get("id") or "").strip()
                    if int(getattr(voice_start_response, "status_code", 0)) == 200
                    else ""
                )
                if voice_session_id:
                    _run_check(
                        client=client,
                        check={
                            "label": "voice_stop",
                            "method": "POST",
                            "path": f"/voice/sessions/{voice_session_id}/stop",
                            "expected": 200,
                            "token": "user-token",
                            "payload": {"reason": "perf_smoke"},
                        },
                        round_number=round_number,
                        latencies_ms=latencies_ms,
                        failures=failures,
                        latency_by_label=latency_by_label,
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
            "endpoint_count": len(latency_by_label),
            "summary": {
                "total_requests": total_requests,
                "failed_requests": failed_requests,
                "error_rate_pct": round(error_rate_pct, 4),
                "avg_latency_ms": round(avg_latency_ms, 2),
                "p95_latency_ms": round(p95_latency_ms, 2),
            },
            "latency_p95_by_check": {
                key: round(_percentile(values, 95), 2)
                for key, values in sorted(latency_by_label.items())
            },
            "critical_path_p95_ms": {
                key: round(_percentile(latency_by_label.get(key, []), 95), 2)
                for key in (
                    "chat_non_stream",
                    "chat_stream",
                    "run_create",
                    "run_get",
                    "voice_start",
                    "voice_stop",
                )
            },
            "thresholds": {
                "max_p95_latency_ms": args.max_p95_latency_ms,
                "max_error_rate_pct": args.max_error_rate_pct,
            },
            "qos": {
                "mode": qos_mode,
                "route_mode": _route_mode_for_qos(qos_mode),
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
