#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate KV pressure telemetry contract and QoS policy transitions "
            "under generation-loop pressure."
        )
    )
    parser.add_argument(
        "--expect-initial-mode",
        default="quality",
        help="Expected QoS mode before pressure scenario.",
    )
    parser.add_argument(
        "--expect-pressure-mode",
        default="power_save",
        help="Expected QoS mode after high/critical KV pressure.",
    )
    parser.add_argument(
        "--min-pressure-events",
        type=int,
        default=1,
        help="Minimum generation_loop_metrics events with pressure_state in {high, critical}.",
    )
    parser.add_argument(
        "--min-critical-events",
        type=int,
        default=1,
        help="Minimum generation_loop_metrics events with pressure_state=critical.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional JSON report output path.",
    )
    return parser.parse_args()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def _qos_step(client: Any, *, label: str) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    response = client.get("/service/qos", headers=_auth("service-token"))
    errors: list[str] = []
    body = (
        response.json()
        if response.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    qos = body.get("qos") if isinstance(body, dict) and isinstance(body.get("qos"), dict) else {}
    if int(response.status_code) != 200:
        errors.append(f"{label}:status={response.status_code}:expected=200")
    return {
        "label": label,
        "status_code": int(response.status_code),
        "qos": qos,
    }, errors, qos


def _chat_step(
    client: Any,
    *,
    label: str,
    prompt: str,
    max_tokens: int,
) -> tuple[dict[str, Any], list[str], str]:
    response = client.post(
        "/v1/chat/completions",
        headers=_auth("gate-user-token"),
        json={
            "user_id": "gate-user",
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "max_tokens": int(max_tokens),
        },
    )
    errors: list[str] = []
    body = (
        response.json()
        if response.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    if int(response.status_code) != 200:
        errors.append(f"{label}:status={response.status_code}:expected=200")
    request_id = str(body.get("request_id") or "") if isinstance(body, dict) else ""
    if not request_id:
        errors.append(f"{label}:missing_request_id")
    return {
        "label": label,
        "status_code": int(response.status_code),
        "request_id": request_id,
    }, errors, request_id


def _set_mode_step(
    client: Any,
    *,
    mode: str,
    auto_enabled: bool,
) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    response = client.post(
        "/service/qos/mode",
        headers=_auth("service-token"),
        json={
            "mode": str(mode),
            "auto_enabled": bool(auto_enabled),
            "thermal_state": "cool",
        },
    )
    errors: list[str] = []
    body = (
        response.json()
        if response.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    qos = body.get("qos") if isinstance(body, dict) and isinstance(body.get("qos"), dict) else {}
    if int(response.status_code) != 200:
        errors.append(f"qos_set_mode:status={response.status_code}:expected=200")
    return {
        "label": "qos_set_mode",
        "status_code": int(response.status_code),
        "qos": qos,
    }, errors, qos


def _load_generation_events(path: Path, request_ids: set[str]) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        row: dict[str, Any]
        try:
            raw = json.loads(line)
        except Exception:
            continue
        if not isinstance(raw, dict):
            continue
        if str(raw.get("event_type") or "") != "generation_loop_metrics":
            continue
        payload = raw.get("payload") if isinstance(raw.get("payload"), dict) else {}
        request_id = str(payload.get("request_id") or "")
        if request_ids and request_id not in request_ids:
            continue
        events.append(
            {
                "request_id": request_id,
                "provider": str(payload.get("provider") or ""),
                "model": str(payload.get("model") or ""),
                "kv_cache": payload.get("kv_cache") if isinstance(payload.get("kv_cache"), dict) else {},
            }
        )
    return events


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    min_pressure_events = max(0, int(args.min_pressure_events))
    min_critical_events = max(0, int(args.min_critical_events))
    expect_initial_mode = str(args.expect_initial_mode or "").strip().lower() or "quality"
    expect_pressure_mode = str(args.expect_pressure_mode or "").strip().lower() or "power_save"

    try:
        from fastapi.testclient import TestClient  # noqa: PLC0415
    except Exception as exc:
        print(f"[kv-pressure-policy-gate] FAILED import_error={exc}")
        return 2

    errors: list[str] = []
    steps: list[dict[str, Any]] = []
    request_ids: set[str] = set()
    report: dict[str, Any] = {}
    app: Any | None = None

    with tempfile.TemporaryDirectory(prefix="amaryllis-kv-pressure-policy-gate-") as tmp:
        support_dir = Path(tmp) / "support"
        telemetry_path = support_dir / "data" / "telemetry.jsonl"
        auth_tokens = {
            "gate-user-token": {"user_id": "gate-user", "scopes": ["user"]},
            "service-token": {"user_id": "svc-runtime", "scopes": ["service"]},
            "gate-admin-token": {"user_id": "gate-admin", "scopes": ["admin", "user"]},
        }
        os.environ["AMARYLLIS_SUPPORT_DIR"] = str(support_dir)
        os.environ["AMARYLLIS_AUTH_ENABLED"] = "true"
        os.environ["AMARYLLIS_AUTH_TOKENS"] = json.dumps(auth_tokens, ensure_ascii=False)
        os.environ["AMARYLLIS_MEMORY_CONSOLIDATION_ENABLED"] = "false"
        os.environ["AMARYLLIS_MCP_ENDPOINTS"] = ""
        os.environ["AMARYLLIS_SECURITY_PROFILE"] = "production"
        os.environ["AMARYLLIS_COGNITION_BACKEND"] = "deterministic"
        os.environ["AMARYLLIS_QOS_MODE"] = "quality"
        os.environ["AMARYLLIS_QOS_AUTO_ENABLED"] = "true"
        os.environ["AMARYLLIS_QOS_THERMAL_STATE"] = "cool"
        os.environ["AMARYLLIS_QOS_TTFT_TARGET_MS"] = "60000"
        os.environ["AMARYLLIS_QOS_TTFT_CRITICAL_MS"] = "120000"
        os.environ["AMARYLLIS_QOS_REQUEST_LATENCY_TARGET_MS"] = "60000"
        os.environ["AMARYLLIS_QOS_REQUEST_LATENCY_CRITICAL_MS"] = "120000"
        os.environ["AMARYLLIS_QOS_KV_PRESSURE_TARGET_EVENTS"] = "0"
        os.environ["AMARYLLIS_QOS_KV_PRESSURE_CRITICAL_EVENTS"] = "1"
        os.environ["AMARYLLIS_KV_PRESSURE_ELEVATED_TOKENS"] = "512"
        os.environ["AMARYLLIS_KV_PRESSURE_HIGH_TOKENS"] = "1024"
        os.environ["AMARYLLIS_KV_PRESSURE_CRITICAL_TOKENS"] = "1536"
        os.environ["AMARYLLIS_CHAT_MAX_INPUT_CHARS"] = "60000"
        os.environ["AMARYLLIS_CHAT_MAX_TOKENS"] = "4096"

        try:
            import importlib  # noqa: PLC0415
            import runtime.server as server_module  # noqa: PLC0415

            server_module = importlib.reload(server_module)
            app = server_module.app
        except Exception as exc:
            print(f"[kv-pressure-policy-gate] FAILED import_or_boot_error={exc}")
            return 2

        try:
            with TestClient(app) as client:
                step, step_errors, initial_qos = _qos_step(client, label="qos_initial")
                steps.append(step)
                errors.extend(step_errors)
                initial_mode = str(initial_qos.get("active_mode") or "")
                if initial_mode != expect_initial_mode:
                    errors.append(
                        "qos_initial:unexpected_mode"
                        f":actual={initial_mode}:expected={expect_initial_mode}"
                    )

                step, step_errors, _ = _set_mode_step(client, mode="quality", auto_enabled=True)
                steps.append(step)
                errors.extend(step_errors)

                low_step, low_errors, low_request_id = _chat_step(
                    client,
                    label="chat_low_pressure",
                    prompt="Hello, keep it short.",
                    max_tokens=64,
                )
                steps.append(low_step)
                errors.extend(low_errors)
                if low_request_id:
                    request_ids.add(low_request_id)

                step, step_errors, low_qos = _qos_step(client, label="qos_after_low_pressure")
                steps.append(step)
                errors.extend(step_errors)
                low_mode = str(low_qos.get("active_mode") or "")
                if low_mode != expect_initial_mode:
                    errors.append(
                        "qos_after_low_pressure:unexpected_mode"
                        f":actual={low_mode}:expected={expect_initial_mode}"
                    )

                high_prompt = ("KV pressure probe. " * 1200).strip()
                high_step, high_errors, high_request_id = _chat_step(
                    client,
                    label="chat_high_pressure",
                    prompt=high_prompt,
                    max_tokens=4096,
                )
                steps.append(high_step)
                errors.extend(high_errors)
                if high_request_id:
                    request_ids.add(high_request_id)

                step, step_errors, pressure_qos = _qos_step(client, label="qos_after_high_pressure")
                steps.append(step)
                errors.extend(step_errors)

                pressure_mode = str(pressure_qos.get("active_mode") or "")
                if pressure_mode != expect_pressure_mode:
                    errors.append(
                        "qos_after_high_pressure:unexpected_mode"
                        f":actual={pressure_mode}:expected={expect_pressure_mode}"
                    )
                pressure_reason = str(pressure_qos.get("reason") or "")
                if "pressure" not in pressure_reason:
                    errors.append(
                        "qos_after_high_pressure:reason_not_pressure"
                        f":actual={pressure_reason}"
                    )
                pressure_metrics = (
                    pressure_qos.get("metrics")
                    if isinstance(pressure_qos.get("metrics"), dict)
                    else {}
                )
                kv_pressure_events = int(round(float(pressure_metrics.get("kv_pressure_events") or 0.0)))
                if kv_pressure_events < min_pressure_events:
                    errors.append(
                        "qos_after_high_pressure:kv_pressure_events_below_min"
                        f":actual={kv_pressure_events}:expected>={min_pressure_events}"
                    )

            generation_events = _load_generation_events(telemetry_path, request_ids=request_ids)
            pressure_states: list[str] = []
            critical_events = 0
            pressure_events = 0
            unknown_events = 0
            max_estimated_tokens = 0
            max_estimated_bytes = 0
            for event in generation_events:
                kv_cache = event.get("kv_cache") if isinstance(event.get("kv_cache"), dict) else {}
                state = str(kv_cache.get("pressure_state") or "").strip().lower()
                pressure_states.append(state)
                if state in {"high", "critical"}:
                    pressure_events += 1
                if state == "critical":
                    critical_events += 1
                if state in {"", "unknown"}:
                    unknown_events += 1
                try:
                    max_estimated_tokens = max(max_estimated_tokens, int(kv_cache.get("estimated_tokens") or 0))
                except Exception:
                    pass
                try:
                    max_estimated_bytes = max(max_estimated_bytes, int(kv_cache.get("estimated_bytes") or 0))
                except Exception:
                    pass

            if len(generation_events) < 2:
                errors.append(
                    "telemetry:generation_loop_metrics_events_below_min"
                    f":actual={len(generation_events)}:expected>=2"
                )
            if pressure_events < min_pressure_events:
                errors.append(
                    "telemetry:pressure_events_below_min"
                    f":actual={pressure_events}:expected>={min_pressure_events}"
                )
            if critical_events < min_critical_events:
                errors.append(
                    "telemetry:critical_events_below_min"
                    f":actual={critical_events}:expected>={min_critical_events}"
                )
            if unknown_events > 0:
                errors.append(
                    "telemetry:unknown_pressure_state_detected"
                    f":count={unknown_events}"
                )
            if max_estimated_tokens <= 0:
                errors.append("telemetry:missing_estimated_tokens")
            if max_estimated_bytes <= 0:
                errors.append("telemetry:missing_estimated_bytes")

            report = {
                "suite": "kv_pressure_policy_gate_v1",
                "summary": {
                    "status": "pass" if not errors else "fail",
                    "checks_total": len(steps) + 8,
                    "checks_failed": len(errors),
                    "errors": errors,
                    "expect_initial_mode": expect_initial_mode,
                    "expect_pressure_mode": expect_pressure_mode,
                },
                "steps": steps,
                "telemetry": {
                    "generation_events_total": len(generation_events),
                    "pressure_states": pressure_states,
                    "pressure_events": pressure_events,
                    "critical_events": critical_events,
                    "unknown_events": unknown_events,
                    "max_estimated_tokens": max_estimated_tokens,
                    "max_estimated_bytes": max_estimated_bytes,
                },
            }
        finally:
            if app is not None:
                _shutdown_app(app)

    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = repo_root / output_path
        _write_json(output_path, report)
        print(f"[kv-pressure-policy-gate] report={output_path}")

    if errors:
        print("[kv-pressure-policy-gate] FAILED")
        for item in errors:
            print(f"- {item}")
        return 1

    print(
        "[kv-pressure-policy-gate] OK "
        f"pressure_events>={min_pressure_events} critical_events>={min_critical_events}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
