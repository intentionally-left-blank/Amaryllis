#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import traceback
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run end-to-end user journey benchmark for unified flow + plan/execute dispatch "
            "and produce KPI report for release/nightly quality packs."
        )
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=int(os.getenv("AMARYLLIS_USER_JOURNEY_ITERATIONS", "5")),
        help="Number of user journeys to execute.",
    )
    parser.add_argument(
        "--min-success-rate-pct",
        type=float,
        default=float(os.getenv("AMARYLLIS_USER_JOURNEY_MIN_SUCCESS_RATE_PCT", "100")),
        help="Minimum required journey success rate percent.",
    )
    parser.add_argument(
        "--max-p95-journey-latency-ms",
        type=float,
        default=float(os.getenv("AMARYLLIS_USER_JOURNEY_MAX_P95_MS", "3000")),
        help="Maximum allowed p95 end-to-end journey latency.",
    )
    parser.add_argument(
        "--max-p95-plan-dispatch-latency-ms",
        type=float,
        default=float(os.getenv("AMARYLLIS_USER_JOURNEY_MAX_P95_PLAN_MS", "1200")),
        help="Maximum allowed p95 plan dispatch latency.",
    )
    parser.add_argument(
        "--max-p95-execute-dispatch-latency-ms",
        type=float,
        default=float(os.getenv("AMARYLLIS_USER_JOURNEY_MAX_P95_EXECUTE_MS", "1200")),
        help="Maximum allowed p95 execute dispatch latency.",
    )
    parser.add_argument(
        "--min-plan-to-execute-conversion-rate-pct",
        type=float,
        default=float(os.getenv("AMARYLLIS_USER_JOURNEY_MIN_CONVERSION_PCT", "100")),
        help="Minimum required plan-to-execute conversion rate percent.",
    )
    parser.add_argument(
        "--min-activation-success-rate-pct",
        type=float,
        default=float(os.getenv("AMARYLLIS_USER_JOURNEY_MIN_ACTIVATION_SUCCESS_RATE_PCT", "100")),
        help="Minimum required onboarding activation success rate percent.",
    )
    parser.add_argument(
        "--max-blocked-activation-rate-pct",
        type=float,
        default=float(os.getenv("AMARYLLIS_USER_JOURNEY_MAX_BLOCKED_ACTIVATION_RATE_PCT", "0")),
        help="Maximum allowed blocked onboarding activation rate percent.",
    )
    parser.add_argument(
        "--max-p95-activation-latency-ms",
        type=float,
        default=float(os.getenv("AMARYLLIS_USER_JOURNEY_MAX_P95_ACTIVATION_MS", "600000")),
        help="Maximum allowed p95 onboarding activation latency (includes first-answer smoke when enabled).",
    )
    parser.add_argument(
        "--min-install-success-rate-pct",
        type=float,
        default=float(os.getenv("AMARYLLIS_USER_JOURNEY_MIN_INSTALL_SUCCESS_RATE_PCT", "100")),
        help="Minimum required onboarding install success rate percent.",
    )
    parser.add_argument(
        "--min-retention-proxy-success-rate-pct",
        type=float,
        default=float(os.getenv("AMARYLLIS_USER_JOURNEY_MIN_RETENTION_PROXY_SUCCESS_RATE_PCT", "100")),
        help="Minimum required return-journey retention proxy success rate percent.",
    )
    parser.add_argument(
        "--min-feature-adoption-rate-pct",
        type=float,
        default=float(os.getenv("AMARYLLIS_USER_JOURNEY_MIN_FEATURE_ADOPTION_RATE_PCT", "100")),
        help="Minimum required feature-adoption rate (plan->execute->result) percent.",
    )
    parser.add_argument(
        "--baseline",
        default="eval/baselines/quality/user_journey_benchmark_baseline.json",
        help="Optional baseline report for trend delta calculation.",
    )
    parser.add_argument(
        "--output",
        default="artifacts/user-journey-benchmark-report.json",
        help="Output report path.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail with non-zero exit code when any KPI check fails.",
    )
    parser.add_argument(
        "--qos-mode",
        default=os.getenv("AMARYLLIS_QOS_MODE", "balanced"),
        help="QoS mode to benchmark (`quality`, `balanced`, `power_save`).",
    )
    parser.add_argument(
        "--cognition-backend",
        default=os.getenv("AMARYLLIS_USER_JOURNEY_COGNITION_BACKEND", "deterministic"),
        help="Cognition backend for benchmark runtime (`deterministic` or `model_manager`).",
    )
    return parser.parse_args()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _normalize_qos_mode(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"quality", "balanced", "power_save"}:
        return normalized
    return "balanced"


def _normalize_cognition_backend(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"deterministic", "model_manager"}:
        return normalized
    return "deterministic"


def _percentile(values: list[float], p: int) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    rank = max(0, min(len(sorted_values) - 1, int(round((p / 100.0) * (len(sorted_values) - 1)))))
    return float(sorted_values[rank])


def _check(
    *,
    check_id: str,
    source: str,
    value: float,
    threshold: float,
    comparator: str,
    unit: str,
) -> dict[str, Any]:
    normalized = str(comparator).strip().lower()
    if normalized not in {"lte", "gte"}:
        raise ValueError(f"Unsupported comparator: {comparator}")
    passed = value <= threshold if normalized == "lte" else value >= threshold
    return {
        "id": check_id,
        "source": source,
        "value": round(float(value), 6),
        "threshold": round(float(threshold), 6),
        "comparator": normalized,
        "unit": unit,
        "passed": bool(passed),
    }


def _resolve_path(project_root: Path, raw_path: str) -> Path:
    path = Path(str(raw_path).strip())
    if not path.is_absolute():
        path = project_root / path
    return path


def _load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON payload must be object: {path}")
    return payload


def _request(
    client: Any,
    *,
    method: str,
    path: str,
    headers: dict[str, str],
    expected_status: int,
    json_payload: dict[str, Any] | None = None,
    name: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.perf_counter()
    response = client.request(method, path, headers=headers, json=json_payload)
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    payload: dict[str, Any] = {}
    try:
        parsed = response.json()
        if isinstance(parsed, dict):
            payload = parsed
    except Exception:
        payload = {}

    step = {
        "name": name,
        "method": method,
        "path": path,
        "status_code": int(response.status_code),
        "expected_status": int(expected_status),
        "latency_ms": round(elapsed_ms, 2),
        "ok": bool(response.status_code == expected_status),
    }
    return step, payload


def _run_journey(
    *,
    client: Any,
    agent_id: str,
    iteration: int,
) -> dict[str, Any]:
    headers = _auth("user-token")
    journey: dict[str, Any] = {
        "iteration": int(iteration),
        "success": False,
        "error": None,
        "session_id": None,
        "run_id": None,
        "steps": [],
        "metrics": {},
    }
    started = time.perf_counter()

    def _call(
        *,
        name: str,
        method: str,
        path: str,
        expected_status: int,
        payload: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        step, response_payload = _request(
            client,
            method=method,
            path=path,
            headers=headers,
            expected_status=expected_status,
            json_payload=payload,
            name=name,
        )
        journey["steps"].append(step)
        if not bool(step.get("ok")):
            raise RuntimeError(f"{name} failed status={step.get('status_code')} path={path}")
        return step, response_payload

    try:
        activation_step, activation_payload = _call(
            name="onboarding_activate",
            method="POST",
            path="/models/onboarding/activate",
            expected_status=200,
            payload={
                "profile": "balanced",
                "include_remote_providers": True,
                "limit": 20,
                "require_metadata": False,
                "activate": True,
                "run_smoke_test": True,
                "smoke_prompt": f"user journey benchmark smoke {iteration}",
            },
        )
        journey["metrics"]["activation_latency_ms"] = float(activation_step.get("latency_ms") or 0.0)
        activation_status = str(activation_payload.get("status") or "").strip().lower()
        activation_ready = bool(activation_payload.get("ready", False))
        activation_success = activation_status in {"activated", "activated_with_smoke_warning"}
        activation_blocked = activation_status == "blocked"
        smoke_payload = (
            activation_payload.get("smoke_test")
            if isinstance(activation_payload.get("smoke_test"), dict)
            else {}
        )
        install_payload = (
            activation_payload.get("install")
            if isinstance(activation_payload.get("install"), dict)
            else {}
        )
        install_steps = (
            install_payload.get("steps")
            if isinstance(install_payload.get("steps"), list)
            else []
        )
        install_step_status: dict[str, str] = {}
        for item in install_steps:
            if not isinstance(item, dict):
                continue
            step_name = str(item.get("step") or "").strip().lower()
            if not step_name:
                continue
            install_step_status[step_name] = str(item.get("status") or "").strip().lower()
        smoke_status = str(smoke_payload.get("status") or "").strip().lower()
        install_success = bool(
            str(install_payload.get("package_id") or "").strip()
            and str(install_payload.get("provider") or "").strip()
            and str(install_payload.get("model") or "").strip()
        )
        journey["metrics"]["activation_status"] = activation_status
        journey["metrics"]["activation_ready"] = activation_ready
        journey["metrics"]["activation_success"] = activation_success
        journey["metrics"]["activation_blocked"] = activation_blocked
        journey["metrics"]["activation_smoke_status"] = smoke_status
        journey["metrics"]["activation_smoke_passed"] = smoke_status == "passed"
        journey["metrics"]["install_success"] = install_success
        journey["metrics"]["install_download_status"] = install_step_status.get("download", "")
        journey["metrics"]["install_activate_status"] = install_step_status.get("activate", "")
        if not activation_success:
            raise RuntimeError(f"onboarding_activate_not_ready status={activation_status}")

        start_step, start_payload = _call(
            name="flow_session_start",
            method="POST",
            path="/flow/sessions/start",
            expected_status=200,
            payload={
                "user_id": "user-1",
                "channels": ["text", "voice", "visual"],
                "initial_state": "listening",
                "metadata": {"origin": "user_journey_benchmark"},
            },
        )
        _ = start_step
        flow_session = start_payload.get("flow_session") if isinstance(start_payload.get("flow_session"), dict) else {}
        session_id = str(flow_session.get("id") or "").strip()
        if not session_id:
            raise RuntimeError("flow_session_start missing session id")
        journey["session_id"] = session_id

        _call(
            name="flow_activity_intent_received",
            method="POST",
            path=f"/flow/sessions/{session_id}/activity",
            expected_status=200,
            payload={
                "channel": "text",
                "event": "intent_received",
                "metadata": {"iteration": iteration},
            },
        )
        _call(
            name="flow_transition_planning",
            method="POST",
            path=f"/flow/sessions/{session_id}/transition",
            expected_status=200,
            payload={
                "to_state": "planning",
                "reason": "benchmark_plan_requested",
                "metadata": {"iteration": iteration},
            },
        )

        plan_step, plan_payload = _call(
            name="dispatch_plan",
            method="POST",
            path=f"/agents/{agent_id}/runs/dispatch",
            expected_status=200,
            payload={
                "user_id": "user-1",
                "message": f"benchmark plan iteration {iteration}",
                "session_id": session_id,
                "interaction_mode": "plan",
                "max_attempts": 1,
            },
        )
        journey["metrics"]["plan_dispatch_latency_ms"] = float(plan_step.get("latency_ms") or 0.0)
        simulation = plan_payload.get("simulation") if isinstance(plan_payload.get("simulation"), dict) else {}
        if str(simulation.get("mode") or "") != "dry_run":
            raise RuntimeError("plan dispatch missing dry_run simulation")
        execute_hint = plan_payload.get("execute_hint") if isinstance(plan_payload.get("execute_hint"), dict) else {}
        execute_hint_payload = (
            execute_hint.get("payload")
            if isinstance(execute_hint.get("payload"), dict)
            else {}
        )
        if str(execute_hint_payload.get("interaction_mode") or "") != "execute":
            raise RuntimeError("plan dispatch missing execute hint payload")

        _call(
            name="flow_transition_acting",
            method="POST",
            path=f"/flow/sessions/{session_id}/transition",
            expected_status=200,
            payload={
                "to_state": "acting",
                "reason": "benchmark_execute_requested",
                "metadata": {"iteration": iteration},
            },
        )

        execute_step, execute_payload = _call(
            name="dispatch_execute",
            method="POST",
            path=f"/agents/{agent_id}/runs/dispatch",
            expected_status=200,
            payload={
                "user_id": "user-1",
                "message": f"benchmark execute iteration {iteration}",
                "session_id": session_id,
                "interaction_mode": "execute",
                "max_attempts": 1,
            },
        )
        journey["metrics"]["execute_dispatch_latency_ms"] = float(execute_step.get("latency_ms") or 0.0)
        run_payload = execute_payload.get("run") if isinstance(execute_payload.get("run"), dict) else {}
        run_id = str(run_payload.get("id") or "").strip()
        if not run_id:
            raise RuntimeError("execute dispatch missing run id")
        journey["run_id"] = run_id

        _call(
            name="flow_transition_reviewing",
            method="POST",
            path=f"/flow/sessions/{session_id}/transition",
            expected_status=200,
            payload={
                "to_state": "reviewing",
                "reason": "benchmark_review_requested",
                "metadata": {"iteration": iteration},
            },
        )
        _call(
            name="flow_activity_result_presented",
            method="POST",
            path=f"/flow/sessions/{session_id}/activity",
            expected_status=200,
            payload={
                "channel": "text",
                "event": "result_presented",
                "metadata": {"iteration": iteration},
            },
        )
        _call(
            name="flow_transition_closed",
            method="POST",
            path=f"/flow/sessions/{session_id}/transition",
            expected_status=200,
            payload={
                "to_state": "closed",
                "reason": "benchmark_complete",
                "metadata": {"iteration": iteration},
            },
        )
        journey["success"] = True
    except Exception as exc:
        journey["error"] = str(exc)

    journey_latency_ms = (time.perf_counter() - started) * 1000.0
    journey["metrics"]["journey_latency_ms"] = round(journey_latency_ms, 2)
    steps = journey.get("steps")
    if isinstance(steps, list):
        journey["metrics"]["steps_total"] = len(steps)
        journey["metrics"]["steps_passed"] = sum(1 for step in steps if bool(step.get("ok")))
    return journey


def _trend_status(*, delta: float, direction: str) -> str:
    if abs(delta) < 1e-9:
        return "unchanged"
    if direction == "higher_better":
        return "improved" if delta > 0 else "regressed"
    return "improved" if delta < 0 else "regressed"


def main() -> int:
    args = _parse_args()
    qos_mode = _normalize_qos_mode(args.qos_mode)
    cognition_backend = _normalize_cognition_backend(args.cognition_backend)
    if args.iterations <= 0:
        print("[user-journey-benchmark] --iterations must be >= 1", file=sys.stderr)
        return 2

    for field_name in (
        "min_success_rate_pct",
        "min_plan_to_execute_conversion_rate_pct",
        "min_activation_success_rate_pct",
        "max_blocked_activation_rate_pct",
        "min_install_success_rate_pct",
        "min_retention_proxy_success_rate_pct",
        "min_feature_adoption_rate_pct",
    ):
        value = float(getattr(args, field_name))
        if value < 0 or value > 100:
            print(
                f"[user-journey-benchmark] --{field_name.replace('_', '-')} must be in range 0..100",
                file=sys.stderr,
            )
            return 2

    for field_name in (
        "max_p95_journey_latency_ms",
        "max_p95_plan_dispatch_latency_ms",
        "max_p95_execute_dispatch_latency_ms",
        "max_p95_activation_latency_ms",
    ):
        value = float(getattr(args, field_name))
        if value < 0:
            print(
                f"[user-journey-benchmark] --{field_name.replace('_', '-')} must be >= 0",
                file=sys.stderr,
            )
            return 2

    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    try:
        from fastapi.testclient import TestClient
    except Exception as exc:  # pragma: no cover - environment dependent
        print(f"[user-journey-benchmark] fastapi testclient unavailable: {exc}", file=sys.stderr)
        return 2

    baseline_path = _resolve_path(project_root, str(args.baseline))
    baseline_payload: dict[str, Any] = {}
    baseline_summary: dict[str, float] = {}
    if baseline_path.exists():
        try:
            baseline_payload = _load_json_object(baseline_path)
            raw_summary = baseline_payload.get("summary")
            if isinstance(raw_summary, dict):
                baseline_summary = {
                    key: _safe_float(raw_summary.get(key))
                    for key in (
                        "journey_success_rate_pct",
                        "p95_journey_latency_ms",
                        "p95_plan_dispatch_latency_ms",
                        "p95_execute_dispatch_latency_ms",
                        "plan_to_execute_conversion_rate_pct",
                        "activation_success_rate_pct",
                        "activation_blocked_rate_pct",
                        "p95_activation_latency_ms",
                        "install_success_rate_pct",
                        "retention_proxy_success_rate_pct",
                        "feature_adoption_rate_pct",
                    )
                    if key in raw_summary
                }
        except Exception as exc:
            print(f"[user-journey-benchmark] invalid baseline ignored: {baseline_path} error={exc}")

    journeys: list[dict[str, Any]] = []
    qos_runtime: dict[str, Any] = {}

    with tempfile.TemporaryDirectory(prefix="amaryllis-user-journey-benchmark-") as tmp:
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
        os.environ["AMARYLLIS_COGNITION_BACKEND"] = cognition_backend

        import runtime.server as server_module

        server_module = importlib.reload(server_module)
        with TestClient(server_module.app) as client:
            create_agent = client.post(
                "/agents/create",
                headers=_auth("user-token"),
                json={
                    "name": "Journey Benchmark Agent",
                    "system_prompt": "Benchmark assistant for unified user-flow validation.",
                    "user_id": "user-1",
                    "tools": [],
                },
            )
            if create_agent.status_code != 200:
                print(
                    "[user-journey-benchmark] failed to create benchmark agent "
                    f"status={create_agent.status_code}",
                    file=sys.stderr,
                )
                return 1
            agent_payload = create_agent.json() if create_agent.headers.get("content-type", "").startswith("application/json") else {}
            agent_id = str(agent_payload.get("id") or "").strip()
            if not agent_id:
                print("[user-journey-benchmark] benchmark agent id missing", file=sys.stderr)
                return 1

            qos_response = client.get("/service/qos", headers=_auth("service-token"))
            if qos_response.status_code != 200:
                print(
                    "[user-journey-benchmark] qos status probe failed "
                    f"status={qos_response.status_code}",
                    file=sys.stderr,
                )
                return 1
            qos_payload = (
                qos_response.json()
                if qos_response.headers.get("content-type", "").startswith("application/json")
                else {}
            )
            qos_runtime = qos_payload.get("qos") if isinstance(qos_payload.get("qos"), dict) else {}

            for iteration in range(1, int(args.iterations) + 1):
                journeys.append(
                    _run_journey(
                        client=client,
                        agent_id=agent_id,
                        iteration=iteration,
                    )
                )

    successful_journeys = [row for row in journeys if bool(row.get("success"))]
    failed_journeys = [row for row in journeys if not bool(row.get("success"))]
    journey_latency_samples = [
        _safe_float(row.get("metrics", {}).get("journey_latency_ms"))  # type: ignore[arg-type]
        for row in successful_journeys
    ]
    plan_latency_samples = [
        _safe_float(row.get("metrics", {}).get("plan_dispatch_latency_ms"))  # type: ignore[arg-type]
        for row in journeys
        if _safe_float(row.get("metrics", {}).get("plan_dispatch_latency_ms"), default=-1) >= 0
    ]
    execute_latency_samples = [
        _safe_float(row.get("metrics", {}).get("execute_dispatch_latency_ms"))  # type: ignore[arg-type]
        for row in journeys
        if _safe_float(row.get("metrics", {}).get("execute_dispatch_latency_ms"), default=-1) >= 0
    ]
    activation_latency_samples = [
        _safe_float(row.get("metrics", {}).get("activation_latency_ms"))  # type: ignore[arg-type]
        for row in journeys
        if _safe_float(row.get("metrics", {}).get("activation_latency_ms"), default=-1) >= 0
    ]

    plan_dispatch_succeeded = 0
    execute_dispatch_succeeded = 0
    feature_adopted_journeys = 0
    for row in journeys:
        steps = row.get("steps")
        if not isinstance(steps, list):
            continue
        feature_plan = False
        feature_execute = False
        feature_result_presented = False
        for step in steps:
            if not isinstance(step, dict):
                continue
            name = str(step.get("name") or "")
            ok = bool(step.get("ok"))
            if name == "dispatch_plan" and ok:
                plan_dispatch_succeeded += 1
                feature_plan = True
            if name == "dispatch_execute" and ok:
                execute_dispatch_succeeded += 1
                feature_execute = True
            if name == "flow_activity_result_presented" and ok:
                feature_result_presented = True
        if feature_plan and feature_execute and feature_result_presented:
            feature_adopted_journeys += 1

    success_rate_pct = 0.0
    if journeys:
        success_rate_pct = (len(successful_journeys) / len(journeys)) * 100.0
    plan_to_execute_conversion_rate_pct = 0.0
    if plan_dispatch_succeeded > 0:
        plan_to_execute_conversion_rate_pct = (
            execute_dispatch_succeeded / plan_dispatch_succeeded
        ) * 100.0
    activation_attempts = 0
    activation_succeeded = 0
    activation_blocked = 0
    activation_smoke_passed = 0
    install_attempts = 0
    install_succeeded = 0
    for row in journeys:
        metrics = row.get("metrics")
        if not isinstance(metrics, dict):
            continue
        if "activation_success" not in metrics:
            continue
        activation_attempts += 1
        if bool(metrics.get("activation_success")):
            activation_succeeded += 1
        if bool(metrics.get("activation_blocked")):
            activation_blocked += 1
        if bool(metrics.get("activation_smoke_passed")):
            activation_smoke_passed += 1
        if "install_success" in metrics:
            install_attempts += 1
            if bool(metrics.get("install_success")):
                install_succeeded += 1
    activation_success_rate_pct = (
        (float(activation_succeeded) / float(activation_attempts)) * 100.0
        if activation_attempts > 0
        else 0.0
    )
    activation_blocked_rate_pct = (
        (float(activation_blocked) / float(activation_attempts)) * 100.0
        if activation_attempts > 0
        else 0.0
    )
    activation_smoke_pass_rate_pct = (
        (float(activation_smoke_passed) / float(activation_attempts)) * 100.0
        if activation_attempts > 0
        else 0.0
    )
    install_success_rate_pct = (
        (float(install_succeeded) / float(install_attempts)) * 100.0
        if install_attempts > 0
        else 0.0
    )
    feature_adoption_rate_pct = (
        (float(feature_adopted_journeys) / float(len(journeys))) * 100.0
        if journeys
        else 0.0
    )
    returning_journeys = []
    for row in journeys:
        try:
            iteration = int(row.get("iteration"))  # type: ignore[arg-type]
        except Exception:
            iteration = 0
        if iteration > 1:
            returning_journeys.append(row)
    returning_journeys_succeeded = sum(1 for row in returning_journeys if bool(row.get("success")))
    retention_proxy_rate_pct = (
        (float(returning_journeys_succeeded) / float(len(returning_journeys))) * 100.0
        if returning_journeys
        else success_rate_pct
    )
    retention_proxy_source = "returning_journeys" if returning_journeys else "all_journeys_fallback"

    summary = {
        "journeys_total": len(journeys),
        "journeys_succeeded": len(successful_journeys),
        "journeys_failed": len(failed_journeys),
        "journey_success_rate_pct": round(success_rate_pct, 4),
        "p50_journey_latency_ms": round(_percentile(journey_latency_samples, 50), 2),
        "p95_journey_latency_ms": round(_percentile(journey_latency_samples, 95), 2),
        "p95_plan_dispatch_latency_ms": round(_percentile(plan_latency_samples, 95), 2),
        "p95_execute_dispatch_latency_ms": round(_percentile(execute_latency_samples, 95), 2),
        "plan_dispatch_succeeded": int(plan_dispatch_succeeded),
        "execute_dispatch_succeeded": int(execute_dispatch_succeeded),
        "plan_to_execute_conversion_rate_pct": round(plan_to_execute_conversion_rate_pct, 4),
        "activation_attempts": int(activation_attempts),
        "activation_succeeded": int(activation_succeeded),
        "activation_blocked": int(activation_blocked),
        "activation_success_rate_pct": round(activation_success_rate_pct, 4),
        "activation_blocked_rate_pct": round(activation_blocked_rate_pct, 4),
        "p95_activation_latency_ms": round(_percentile(activation_latency_samples, 95), 2),
        "activation_smoke_pass_rate_pct": round(activation_smoke_pass_rate_pct, 4),
        "install_attempts": int(install_attempts),
        "install_succeeded": int(install_succeeded),
        "install_success_rate_pct": round(install_success_rate_pct, 4),
        "returning_journeys_total": len(returning_journeys),
        "returning_journeys_succeeded": int(returning_journeys_succeeded),
        "retention_proxy_success_rate_pct": round(retention_proxy_rate_pct, 4),
        "retention_proxy_source": retention_proxy_source,
        "feature_adopted_journeys": int(feature_adopted_journeys),
        "feature_adoption_rate_pct": round(feature_adoption_rate_pct, 4),
    }

    checks = [
        _check(
            check_id="journey.success_rate_pct",
            source="user_journey_benchmark",
            value=_safe_float(summary.get("journey_success_rate_pct")),
            threshold=float(args.min_success_rate_pct),
            comparator="gte",
            unit="pct",
        ),
        _check(
            check_id="journey.p95_end_to_end_ms",
            source="user_journey_benchmark",
            value=_safe_float(summary.get("p95_journey_latency_ms")),
            threshold=float(args.max_p95_journey_latency_ms),
            comparator="lte",
            unit="ms",
        ),
        _check(
            check_id="journey.p95_plan_dispatch_ms",
            source="user_journey_benchmark",
            value=_safe_float(summary.get("p95_plan_dispatch_latency_ms")),
            threshold=float(args.max_p95_plan_dispatch_latency_ms),
            comparator="lte",
            unit="ms",
        ),
        _check(
            check_id="journey.p95_execute_dispatch_ms",
            source="user_journey_benchmark",
            value=_safe_float(summary.get("p95_execute_dispatch_latency_ms")),
            threshold=float(args.max_p95_execute_dispatch_latency_ms),
            comparator="lte",
            unit="ms",
        ),
        _check(
            check_id="journey.plan_to_execute_conversion_rate_pct",
            source="user_journey_benchmark",
            value=_safe_float(summary.get("plan_to_execute_conversion_rate_pct")),
            threshold=float(args.min_plan_to_execute_conversion_rate_pct),
            comparator="gte",
            unit="pct",
        ),
        _check(
            check_id="journey.activation_success_rate_pct",
            source="user_journey_benchmark",
            value=_safe_float(summary.get("activation_success_rate_pct")),
            threshold=float(args.min_activation_success_rate_pct),
            comparator="gte",
            unit="pct",
        ),
        _check(
            check_id="journey.activation_blocked_rate_pct",
            source="user_journey_benchmark",
            value=_safe_float(summary.get("activation_blocked_rate_pct")),
            threshold=float(args.max_blocked_activation_rate_pct),
            comparator="lte",
            unit="pct",
        ),
        _check(
            check_id="journey.p95_activation_latency_ms",
            source="user_journey_benchmark",
            value=_safe_float(summary.get("p95_activation_latency_ms")),
            threshold=float(args.max_p95_activation_latency_ms),
            comparator="lte",
            unit="ms",
        ),
        _check(
            check_id="journey.install_success_rate_pct",
            source="user_journey_benchmark",
            value=_safe_float(summary.get("install_success_rate_pct")),
            threshold=float(args.min_install_success_rate_pct),
            comparator="gte",
            unit="pct",
        ),
        _check(
            check_id="journey.retention_proxy_success_rate_pct",
            source="user_journey_benchmark",
            value=_safe_float(summary.get("retention_proxy_success_rate_pct")),
            threshold=float(args.min_retention_proxy_success_rate_pct),
            comparator="gte",
            unit="pct",
        ),
        _check(
            check_id="journey.feature_adoption_rate_pct",
            source="user_journey_benchmark",
            value=_safe_float(summary.get("feature_adoption_rate_pct")),
            threshold=float(args.min_feature_adoption_rate_pct),
            comparator="gte",
            unit="pct",
        ),
    ]

    trend_rows: list[dict[str, Any]] = []
    metric_directions = {
        "journey_success_rate_pct": "higher_better",
        "p95_journey_latency_ms": "lower_better",
        "p95_plan_dispatch_latency_ms": "lower_better",
        "p95_execute_dispatch_latency_ms": "lower_better",
        "plan_to_execute_conversion_rate_pct": "higher_better",
        "activation_success_rate_pct": "higher_better",
        "activation_blocked_rate_pct": "lower_better",
        "p95_activation_latency_ms": "lower_better",
        "install_success_rate_pct": "higher_better",
        "retention_proxy_success_rate_pct": "higher_better",
        "feature_adoption_rate_pct": "higher_better",
    }
    for metric_id, direction in metric_directions.items():
        if metric_id not in baseline_summary:
            continue
        current_value = _safe_float(summary.get(metric_id))
        baseline_value = _safe_float(baseline_summary.get(metric_id))
        delta = current_value - baseline_value
        trend_rows.append(
            {
                "metric_id": metric_id,
                "current": round(current_value, 6),
                "baseline": round(baseline_value, 6),
                "delta": round(delta, 6),
                "direction": direction,
                "status": _trend_status(delta=delta, direction=direction),
            }
        )

    trend_summary = {
        "compared_metrics": len(trend_rows),
        "improved": sum(1 for row in trend_rows if str(row.get("status")) == "improved"),
        "regressed": sum(1 for row in trend_rows if str(row.get("status")) == "regressed"),
        "unchanged": sum(1 for row in trend_rows if str(row.get("status")) == "unchanged"),
    }

    passed_checks = sum(1 for item in checks if bool(item.get("passed")))
    failed_checks = len(checks) - passed_checks
    payload = {
        "generated_at": _utc_now_iso(),
        "suite": "user_journey_benchmark_v1",
        "config": {
            "iterations": int(args.iterations),
            "strict": bool(args.strict),
            "qos": {
                "requested_mode": qos_mode,
                "runtime": qos_runtime,
                "cognition_backend": cognition_backend,
            },
            "thresholds": {
                "min_success_rate_pct": float(args.min_success_rate_pct),
                "max_p95_journey_latency_ms": float(args.max_p95_journey_latency_ms),
                "max_p95_plan_dispatch_latency_ms": float(args.max_p95_plan_dispatch_latency_ms),
                "max_p95_execute_dispatch_latency_ms": float(args.max_p95_execute_dispatch_latency_ms),
                "min_plan_to_execute_conversion_rate_pct": float(args.min_plan_to_execute_conversion_rate_pct),
                "min_activation_success_rate_pct": float(args.min_activation_success_rate_pct),
                "max_blocked_activation_rate_pct": float(args.max_blocked_activation_rate_pct),
                "max_p95_activation_latency_ms": float(args.max_p95_activation_latency_ms),
                "min_install_success_rate_pct": float(args.min_install_success_rate_pct),
                "min_retention_proxy_success_rate_pct": float(args.min_retention_proxy_success_rate_pct),
                "min_feature_adoption_rate_pct": float(args.min_feature_adoption_rate_pct),
            },
        },
        "baseline": {
            "path": str(baseline_path),
            "loaded": bool(baseline_summary),
            "suite": str(baseline_payload.get("suite") or "").strip() if baseline_payload else "",
        },
        "journeys": journeys,
        "checks": checks,
        "summary": {
            **summary,
            "checks_total": len(checks),
            "checks_passed": passed_checks,
            "checks_failed": failed_checks,
            "status": "pass" if failed_checks == 0 else "fail",
        },
        "trend_deltas": {
            "metrics": trend_rows,
            "summary": trend_summary,
        },
    }

    output_path = _resolve_path(project_root, str(args.output))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[user-journey-benchmark] report={output_path}")
    print(json.dumps(payload["summary"], ensure_ascii=False))
    if bool(args.strict) and failed_checks > 0:
        print("[user-journey-benchmark] FAILED")
        return 1

    print("[user-journey-benchmark] OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # pragma: no cover - defensive
        traceback.print_exc()
        raise SystemExit(1)
