#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import platform
import sys
import tempfile
import time
import traceback
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run Linux parity smoke checks for run/voice/tools/observability paths "
            "and fail on contract regressions."
        )
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=int(os.getenv("AMARYLLIS_LINUX_PARITY_ITERATIONS", "1")),
        help="Number of full parity rounds to execute.",
    )
    parser.add_argument(
        "--max-run-poll-sec",
        type=float,
        default=float(os.getenv("AMARYLLIS_LINUX_PARITY_RUN_POLL_SEC", "5.0")),
        help="How long to wait for run status to leave queued/running before diagnostics checks.",
    )
    parser.add_argument(
        "--output",
        default=os.getenv("AMARYLLIS_LINUX_PARITY_OUTPUT", ""),
        help="Optional JSON report output path.",
    )
    parser.add_argument(
        "--require-linux",
        action="store_true",
        help="Fail if current platform is not Linux.",
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


def _install_runtime_stubs(app: Any) -> None:
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
        provider = str(getattr(model_manager, "active_provider", "linux-parity"))
        model = str(getattr(model_manager, "active_model", "linux-parity-model"))
        return {
            "active_provider": provider,
            "active_model": model,
            "items": [
                {
                    "provider": provider,
                    "model": model,
                    "source": "linux_parity_stub",
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
        provider_value = provider or str(getattr(model_manager, "active_provider", "linux-parity"))
        model_value = model or str(getattr(model_manager, "active_model", "linux-parity-model"))
        return {
            "provider": provider_value,
            "model": model_value,
            "reason": "linux_parity_stub",
            "alternates": [],
        }

    def _fake_provider_health() -> dict[str, Any]:
        provider = str(getattr(model_manager, "active_provider", "linux-parity"))
        model = str(getattr(model_manager, "active_model", "linux-parity-model"))
        return {
            provider: {
                "status": "ok",
                "provider": provider,
                "active_model": model,
                "latency_ms": 1.0,
                "source": "linux_parity_stub",
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
        provider_value = provider or str(getattr(model_manager, "active_provider", "linux-parity"))
        model_value = model or str(getattr(model_manager, "active_model", "linux-parity-model"))
        return {
            "content": "linux-parity-ok",
            "provider": provider_value,
            "model": model_value,
            "routing": {"mode": "linux_parity_stub"},
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
        provider_value = provider or str(getattr(model_manager, "active_provider", "linux-parity"))
        model_value = model or str(getattr(model_manager, "active_model", "linux-parity-model"))
        return (
            iter(["linux", "-parity", "-stream"]),
            provider_value,
            model_value,
            {"mode": "linux_parity_stub"},
        )

    model_manager.list_models = _fake_list_models
    model_manager.choose_route = _fake_choose_route
    model_manager.provider_health = _fake_provider_health
    model_manager.chat = _fake_chat
    model_manager.stream_chat = _fake_stream_chat


def _mark_failure(
    failures: list[dict[str, Any]],
    domain_stats: dict[str, dict[str, int]],
    *,
    round_number: int,
    domain: str,
    label: str,
    method: str,
    path: str,
    detail: str,
    latency_ms: float,
    expected_status: int | None = None,
    actual_status: int | None = None,
) -> None:
    domain_stats[domain]["failed"] += 1
    failures.append(
        {
            "round": round_number,
            "domain": domain,
            "label": label,
            "method": method,
            "path": path,
            "detail": detail,
            "latency_ms": round(latency_ms, 2),
            "expected_status": expected_status,
            "actual_status": actual_status,
        }
    )


def _check_request(
    *,
    client: Any,
    round_number: int,
    domain: str,
    label: str,
    method: str,
    path: str,
    token: str | None,
    expected_status: int,
    domain_stats: dict[str, dict[str, int]],
    failures: list[dict[str, Any]],
    latencies_ms: list[float],
    payload: dict[str, Any] | None = None,
    require_version_header: bool = True,
) -> Any:
    headers: dict[str, str] = _auth(token) if token else {}
    started = time.perf_counter()
    response = client.request(method, path, headers=headers, json=payload)
    latency_ms = (time.perf_counter() - started) * 1000.0
    latencies_ms.append(latency_ms)
    domain_stats[domain]["checks"] += 1

    if int(response.status_code) != int(expected_status):
        _mark_failure(
            failures,
            domain_stats,
            round_number=round_number,
            domain=domain,
            label=label,
            method=method,
            path=path,
            detail="unexpected status code",
            latency_ms=latency_ms,
            expected_status=expected_status,
            actual_status=int(response.status_code),
        )
        return response

    if require_version_header and not str(response.headers.get("X-Amaryllis-API-Version", "")).strip():
        _mark_failure(
            failures,
            domain_stats,
            round_number=round_number,
            domain=domain,
            label=label,
            method=method,
            path=path,
            detail="missing X-Amaryllis-API-Version header",
            latency_ms=latency_ms,
            expected_status=expected_status,
            actual_status=int(response.status_code),
        )
    return response


def _ensure_json_key(
    *,
    response: Any,
    key: str,
    round_number: int,
    domain: str,
    label: str,
    method: str,
    path: str,
    domain_stats: dict[str, dict[str, int]],
    failures: list[dict[str, Any]],
) -> dict[str, Any]:
    try:
        payload = response.json()
    except Exception:
        _mark_failure(
            failures,
            domain_stats,
            round_number=round_number,
            domain=domain,
            label=label,
            method=method,
            path=path,
            detail="response is not valid JSON",
            latency_ms=0.0,
            expected_status=200,
            actual_status=int(getattr(response, "status_code", 0)),
        )
        return {}

    value = payload.get(key)
    if value is None:
        _mark_failure(
            failures,
            domain_stats,
            round_number=round_number,
            domain=domain,
            label=label,
            method=method,
            path=path,
            detail=f"missing key '{key}' in JSON payload",
            latency_ms=0.0,
            expected_status=200,
            actual_status=int(getattr(response, "status_code", 0)),
        )
    return payload


def main() -> int:
    args = _parse_args()
    if args.iterations <= 0:
        print("[linux-parity] --iterations must be >= 1", file=sys.stderr)
        return 2
    if args.max_run_poll_sec < 0:
        print("[linux-parity] --max-run-poll-sec must be >= 0", file=sys.stderr)
        return 2
    if args.require_linux and not sys.platform.startswith("linux"):
        print(f"[linux-parity] FAILED: require-linux set but platform is '{sys.platform}'")
        return 1

    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    try:
        from fastapi.testclient import TestClient
    except Exception as exc:  # pragma: no cover - environment-dependent
        print(f"[linux-parity] fastapi testclient unavailable: {exc}")
        return 2

    started_at = datetime.now(timezone.utc)
    failures: list[dict[str, Any]] = []
    latencies_ms: list[float] = []
    domain_stats: dict[str, dict[str, int]] = {
        "run": {"checks": 0, "failed": 0},
        "voice": {"checks": 0, "failed": 0},
        "tools": {"checks": 0, "failed": 0},
        "observability": {"checks": 0, "failed": 0},
    }
    app: Any | None = None

    with tempfile.TemporaryDirectory(prefix="amaryllis-linux-parity-") as tmp:
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
        _install_runtime_stubs(app)

        with TestClient(app) as client:
            for round_number in range(1, args.iterations + 1):
                health = _check_request(
                    client=client,
                    round_number=round_number,
                    domain="observability",
                    label="health",
                    method="GET",
                    path="/health",
                    token=None,
                    expected_status=200,
                    domain_stats=domain_stats,
                    failures=failures,
                    latencies_ms=latencies_ms,
                )
                payload = _ensure_json_key(
                    response=health,
                    key="status",
                    round_number=round_number,
                    domain="observability",
                    label="health",
                    method="GET",
                    path="/health",
                    domain_stats=domain_stats,
                    failures=failures,
                )
                if str(payload.get("status") or "") != "ok":
                    _mark_failure(
                        failures,
                        domain_stats,
                        round_number=round_number,
                        domain="observability",
                        label="health",
                        method="GET",
                        path="/health",
                        detail="health status is not ok",
                        latency_ms=0.0,
                    )

                service_health = _check_request(
                    client=client,
                    round_number=round_number,
                    domain="observability",
                    label="service_health",
                    method="GET",
                    path="/service/health",
                    token="service-token",
                    expected_status=200,
                    domain_stats=domain_stats,
                    failures=failures,
                    latencies_ms=latencies_ms,
                )
                _ensure_json_key(
                    response=service_health,
                    key="providers",
                    round_number=round_number,
                    domain="observability",
                    label="service_health",
                    method="GET",
                    path="/service/health",
                    domain_stats=domain_stats,
                    failures=failures,
                )

                slo = _check_request(
                    client=client,
                    round_number=round_number,
                    domain="observability",
                    label="observability_slo",
                    method="GET",
                    path="/service/observability/slo",
                    token="service-token",
                    expected_status=200,
                    domain_stats=domain_stats,
                    failures=failures,
                    latencies_ms=latencies_ms,
                )
                _ensure_json_key(
                    response=slo,
                    key="quality_budget",
                    round_number=round_number,
                    domain="observability",
                    label="observability_slo",
                    method="GET",
                    path="/service/observability/slo",
                    domain_stats=domain_stats,
                    failures=failures,
                )

                lifecycle = _check_request(
                    client=client,
                    round_number=round_number,
                    domain="observability",
                    label="api_lifecycle",
                    method="GET",
                    path="/service/api/lifecycle",
                    token="service-token",
                    expected_status=200,
                    domain_stats=domain_stats,
                    failures=failures,
                    latencies_ms=latencies_ms,
                )
                _ensure_json_key(
                    response=lifecycle,
                    key="policy",
                    round_number=round_number,
                    domain="observability",
                    label="api_lifecycle",
                    method="GET",
                    path="/service/api/lifecycle",
                    domain_stats=domain_stats,
                    failures=failures,
                )

                metrics = _check_request(
                    client=client,
                    round_number=round_number,
                    domain="observability",
                    label="observability_metrics",
                    method="GET",
                    path="/service/observability/metrics",
                    token="service-token",
                    expected_status=200,
                    domain_stats=domain_stats,
                    failures=failures,
                    latencies_ms=latencies_ms,
                )
                content_type = str(metrics.headers.get("content-type", "")).lower()
                if not content_type.startswith("text/plain"):
                    _mark_failure(
                        failures,
                        domain_stats,
                        round_number=round_number,
                        domain="observability",
                        label="observability_metrics",
                        method="GET",
                        path="/service/observability/metrics",
                        detail=f"unexpected content-type '{content_type}'",
                        latency_ms=0.0,
                    )

                tools_list = _check_request(
                    client=client,
                    round_number=round_number,
                    domain="tools",
                    label="tools_list",
                    method="GET",
                    path="/tools",
                    token="user-token",
                    expected_status=200,
                    domain_stats=domain_stats,
                    failures=failures,
                    latencies_ms=latencies_ms,
                )
                _ensure_json_key(
                    response=tools_list,
                    key="items",
                    round_number=round_number,
                    domain="tools",
                    label="tools_list",
                    method="GET",
                    path="/tools",
                    domain_stats=domain_stats,
                    failures=failures,
                )

                _check_request(
                    client=client,
                    round_number=round_number,
                    domain="tools",
                    label="permission_prompts",
                    method="GET",
                    path="/tools/permissions/prompts?status=pending&limit=20",
                    token="user-token",
                    expected_status=200,
                    domain_stats=domain_stats,
                    failures=failures,
                    latencies_ms=latencies_ms,
                )

                _check_request(
                    client=client,
                    round_number=round_number,
                    domain="tools",
                    label="mcp_tools_list",
                    method="GET",
                    path="/mcp/tools",
                    token="user-token",
                    expected_status=200,
                    domain_stats=domain_stats,
                    failures=failures,
                    latencies_ms=latencies_ms,
                )

                _check_request(
                    client=client,
                    round_number=round_number,
                    domain="tools",
                    label="debug_tools_mcp_health",
                    method="GET",
                    path="/debug/tools/mcp-health",
                    token="admin-token",
                    expected_status=200,
                    domain_stats=domain_stats,
                    failures=failures,
                    latencies_ms=latencies_ms,
                )

                _check_request(
                    client=client,
                    round_number=round_number,
                    domain="tools",
                    label="debug_tools_guardrails",
                    method="GET",
                    path="/debug/tools/guardrails?session_id=linux-parity&scopes_limit=5&top_tools_limit=3",
                    token="admin-token",
                    expected_status=200,
                    domain_stats=domain_stats,
                    failures=failures,
                    latencies_ms=latencies_ms,
                )

                created_agent = _check_request(
                    client=client,
                    round_number=round_number,
                    domain="run",
                    label="create_agent",
                    method="POST",
                    path="/agents/create",
                    token="user-token",
                    expected_status=200,
                    domain_stats=domain_stats,
                    failures=failures,
                    latencies_ms=latencies_ms,
                    payload={
                        "name": f"linux-parity-agent-{round_number}",
                        "system_prompt": "Reply briefly for Linux parity smoke checks.",
                        "user_id": "user-1",
                    },
                )
                created_agent_payload = _ensure_json_key(
                    response=created_agent,
                    key="id",
                    round_number=round_number,
                    domain="run",
                    label="create_agent",
                    method="POST",
                    path="/agents/create",
                    domain_stats=domain_stats,
                    failures=failures,
                )
                agent_id = str(created_agent_payload.get("id") or "").strip()

                if agent_id:
                    created_run = _check_request(
                        client=client,
                        round_number=round_number,
                        domain="run",
                        label="create_run",
                        method="POST",
                        path=f"/agents/{agent_id}/runs",
                        token="user-token",
                        expected_status=200,
                        domain_stats=domain_stats,
                        failures=failures,
                        latencies_ms=latencies_ms,
                        payload={
                            "user_id": "user-1",
                            "message": f"linux parity round {round_number}",
                            "max_attempts": 1,
                        },
                    )
                    created_run_payload = _ensure_json_key(
                        response=created_run,
                        key="run",
                        round_number=round_number,
                        domain="run",
                        label="create_run",
                        method="POST",
                        path=f"/agents/{agent_id}/runs",
                        domain_stats=domain_stats,
                        failures=failures,
                    )
                    run = created_run_payload.get("run")
                    run_id = ""
                    if isinstance(run, dict):
                        run_id = str(run.get("id") or "").strip()
                    if not run_id:
                        _mark_failure(
                            failures,
                            domain_stats,
                            round_number=round_number,
                            domain="run",
                            label="create_run",
                            method="POST",
                            path=f"/agents/{agent_id}/runs",
                            detail="create run response has empty run.id",
                            latency_ms=0.0,
                        )

                    _check_request(
                        client=client,
                        round_number=round_number,
                        domain="run",
                        label="list_runs",
                        method="GET",
                        path=f"/agents/{agent_id}/runs?user_id=user-1&limit=20",
                        token="user-token",
                        expected_status=200,
                        domain_stats=domain_stats,
                        failures=failures,
                        latencies_ms=latencies_ms,
                    )

                    if run_id:
                        status_value = ""
                        deadline = time.monotonic() + float(args.max_run_poll_sec)
                        while True:
                            fetched = _check_request(
                                client=client,
                                round_number=round_number,
                                domain="run",
                                label="get_run",
                                method="GET",
                                path=f"/agents/runs/{run_id}",
                                token="user-token",
                                expected_status=200,
                                domain_stats=domain_stats,
                                failures=failures,
                                latencies_ms=latencies_ms,
                            )
                            fetched_payload = _ensure_json_key(
                                response=fetched,
                                key="run",
                                round_number=round_number,
                                domain="run",
                                label="get_run",
                                method="GET",
                                path=f"/agents/runs/{run_id}",
                                domain_stats=domain_stats,
                                failures=failures,
                            )
                            row = fetched_payload.get("run")
                            if isinstance(row, dict):
                                status_value = str(row.get("status") or "").strip().lower()
                            if status_value not in {"queued", "running"}:
                                break
                            if time.monotonic() >= deadline:
                                break
                            time.sleep(0.2)
                        if status_value not in {"queued", "running", "succeeded", "failed", "canceled"}:
                            _mark_failure(
                                failures,
                                domain_stats,
                                round_number=round_number,
                                domain="run",
                                label="get_run",
                                method="GET",
                                path=f"/agents/runs/{run_id}",
                                detail=f"unexpected run status '{status_value}'",
                                latency_ms=0.0,
                            )

                        _check_request(
                            client=client,
                            round_number=round_number,
                            domain="run",
                            label="run_diagnostics",
                            method="GET",
                            path=f"/agents/runs/{run_id}/diagnostics",
                            token="user-token",
                            expected_status=200,
                            domain_stats=domain_stats,
                            failures=failures,
                            latencies_ms=latencies_ms,
                        )

                        _check_request(
                            client=client,
                            round_number=round_number,
                            domain="run",
                            label="run_audit",
                            method="GET",
                            path=f"/agents/runs/{run_id}/audit?include_tool_calls=true&include_security_actions=true&limit=200",
                            token="user-token",
                            expected_status=200,
                            domain_stats=domain_stats,
                            failures=failures,
                            latencies_ms=latencies_ms,
                        )

                        _check_request(
                            client=client,
                            round_number=round_number,
                            domain="run",
                            label="run_audit_export_json",
                            method="GET",
                            path=f"/agents/runs/{run_id}/audit/export?format=json&include_tool_calls=true&include_security_actions=true&limit=200",
                            token="user-token",
                            expected_status=200,
                            domain_stats=domain_stats,
                            failures=failures,
                            latencies_ms=latencies_ms,
                        )

                stt = _check_request(
                    client=client,
                    round_number=round_number,
                    domain="voice",
                    label="voice_stt_health",
                    method="GET",
                    path="/voice/stt/health",
                    token="user-token",
                    expected_status=200,
                    domain_stats=domain_stats,
                    failures=failures,
                    latencies_ms=latencies_ms,
                )
                _ensure_json_key(
                    response=stt,
                    key="stt",
                    round_number=round_number,
                    domain="voice",
                    label="voice_stt_health",
                    method="GET",
                    path="/voice/stt/health",
                    domain_stats=domain_stats,
                    failures=failures,
                )

                started_session = _check_request(
                    client=client,
                    round_number=round_number,
                    domain="voice",
                    label="voice_start",
                    method="POST",
                    path="/voice/sessions/start",
                    token="user-token",
                    expected_status=200,
                    domain_stats=domain_stats,
                    failures=failures,
                    latencies_ms=latencies_ms,
                    payload={
                        "user_id": "user-1",
                        "mode": "ptt",
                        "sample_rate_hz": 16000,
                    },
                )
                started_payload = _ensure_json_key(
                    response=started_session,
                    key="voice_session",
                    round_number=round_number,
                    domain="voice",
                    label="voice_start",
                    method="POST",
                    path="/voice/sessions/start",
                    domain_stats=domain_stats,
                    failures=failures,
                )
                voice_session = started_payload.get("voice_session")
                session_id = ""
                if isinstance(voice_session, dict):
                    session_id = str(voice_session.get("id") or "").strip()
                if not session_id:
                    _mark_failure(
                        failures,
                        domain_stats,
                        round_number=round_number,
                        domain="voice",
                        label="voice_start",
                        method="POST",
                        path="/voice/sessions/start",
                        detail="voice session id is empty",
                        latency_ms=0.0,
                    )

                _check_request(
                    client=client,
                    round_number=round_number,
                    domain="voice",
                    label="voice_list",
                    method="GET",
                    path="/voice/sessions?user_id=user-1&limit=20",
                    token="user-token",
                    expected_status=200,
                    domain_stats=domain_stats,
                    failures=failures,
                    latencies_ms=latencies_ms,
                )

                if session_id:
                    _check_request(
                        client=client,
                        round_number=round_number,
                        domain="voice",
                        label="voice_get",
                        method="GET",
                        path=f"/voice/sessions/{session_id}",
                        token="user-token",
                        expected_status=200,
                        domain_stats=domain_stats,
                        failures=failures,
                        latencies_ms=latencies_ms,
                    )
                    _check_request(
                        client=client,
                        round_number=round_number,
                        domain="voice",
                        label="voice_stop",
                        method="POST",
                        path=f"/voice/sessions/{session_id}/stop",
                        token="user-token",
                        expected_status=200,
                        domain_stats=domain_stats,
                        failures=failures,
                        latencies_ms=latencies_ms,
                        payload={"reason": "linux parity smoke"},
                    )

    if app is not None:
        _shutdown_app(app)

    total_checks = sum(item["checks"] for item in domain_stats.values())
    failed_checks = len(failures)
    error_rate_pct = (float(failed_checks) / float(total_checks) * 100.0) if total_checks else 0.0

    report = {
        "suite": "linux_parity_smoke_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "started_at": started_at.isoformat(),
        "platform": {
            "python": platform.python_version(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "sys_platform": sys.platform,
        },
        "iterations": int(args.iterations),
        "run_poll_timeout_sec": float(args.max_run_poll_sec),
        "summary": {
            "checks_total": total_checks,
            "checks_failed": failed_checks,
            "error_rate_pct": round(error_rate_pct, 4),
            "latency_ms": {
                "p50": round(_percentile(latencies_ms, 50), 2),
                "p95": round(_percentile(latencies_ms, 95), 2),
                "max": round(max(latencies_ms), 2) if latencies_ms else 0.0,
            },
        },
        "domains": domain_stats,
        "failures": failures,
    }

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"[linux-parity] report={output_path}")

    if failures:
        print("[linux-parity] FAILED")
        for item in failures:
            round_number = item.get("round")
            domain = item.get("domain")
            label = item.get("label")
            detail = item.get("detail")
            method = item.get("method")
            path = item.get("path")
            expected = item.get("expected_status")
            actual = item.get("actual_status")
            if expected is None and actual is None:
                print(f"- round={round_number} domain={domain} label={label}: {detail} ({method} {path})")
            else:
                print(
                    f"- round={round_number} domain={domain} label={label}: {detail} "
                    f"({method} {path}) expected={expected} actual={actual}"
                )
        return 1

    print("[linux-parity] OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # pragma: no cover - defensive
        traceback.print_exc()
        raise SystemExit(1)
