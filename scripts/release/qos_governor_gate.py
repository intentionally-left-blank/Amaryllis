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
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate QoS governor runtime contract, including thermal-aware mode "
            "switching and deterministic recovery transitions."
        )
    )
    parser.add_argument(
        "--initial-mode",
        default=os.getenv("AMARYLLIS_QOS_MODE", "balanced"),
        help="Initial QoS mode (`quality`, `balanced`, `power_save`).",
    )
    parser.add_argument(
        "--expect-critical-mode",
        default="power_save",
        help="Expected mode after `critical` thermal update.",
    )
    parser.add_argument(
        "--expect-final-mode",
        default="quality",
        help="Expected mode after thermal recovery sequence.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional JSON report output path.",
    )
    return parser.parse_args()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_mode(value: str | None) -> str:
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
    return mapping.get(_normalize_mode(qos_mode), "balanced")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def _request_step(
    client: Any,
    *,
    label: str,
    path: str,
    method: str = "POST",
    payload: dict[str, Any] | None = None,
    expected_status: int = 200,
) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    headers = _auth("service-token")
    if method.upper() == "GET":
        response = client.get(path, headers=headers)
    else:
        response = client.post(path, headers=headers, json=payload or {})
    errors: list[str] = []
    body = (
        response.json()
        if response.headers.get("content-type", "").startswith("application/json")
        else {}
    )
    qos = body.get("qos") if isinstance(body, dict) and isinstance(body.get("qos"), dict) else {}
    if int(response.status_code) != int(expected_status):
        errors.append(f"{label}:status={response.status_code}:expected={expected_status}")
    return {
        "label": label,
        "path": path,
        "method": method.upper(),
        "status_code": int(response.status_code),
        "expected_status": int(expected_status),
        "qos": qos,
    }, errors, qos


def main() -> int:
    args = _parse_args()
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    initial_mode = _normalize_mode(args.initial_mode)
    expect_critical_mode = _normalize_mode(args.expect_critical_mode)
    expect_final_mode = _normalize_mode(args.expect_final_mode)

    try:
        from fastapi.testclient import TestClient  # noqa: PLC0415
    except Exception as exc:
        print(f"[qos-governor-gate] FAILED import_error={exc}")
        return 2

    report_steps: list[dict[str, Any]] = []
    errors: list[str] = []
    app: Any | None = None

    with tempfile.TemporaryDirectory(prefix="amaryllis-qos-governor-gate-") as tmp:
        support_dir = Path(tmp) / "support"
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
        os.environ["AMARYLLIS_QOS_AUTO_ENABLED"] = "true"
        os.environ["AMARYLLIS_QOS_THERMAL_STATE"] = "unknown"
        os.environ["AMARYLLIS_QOS_MODE"] = initial_mode

        try:
            import runtime.server as server_module  # noqa: PLC0415

            server_module = importlib.reload(server_module)
            app = server_module.app
        except Exception as exc:
            print(f"[qos-governor-gate] FAILED import_or_boot_error={exc}")
            return 2

        try:
            with TestClient(app) as client:
                step, step_errors, qos = _request_step(
                    client,
                    label="qos_status_initial",
                    method="GET",
                    path="/service/qos",
                )
                report_steps.append(step)
                errors.extend(step_errors)
                if not qos:
                    errors.append("qos_status_initial:missing_qos_payload")

                step, step_errors, _ = _request_step(
                    client,
                    label="qos_set_quality_mode",
                    path="/service/qos/mode",
                    payload={"mode": "quality", "auto_enabled": True},
                )
                report_steps.append(step)
                errors.extend(step_errors)

                step, step_errors, warm_qos = _request_step(
                    client,
                    label="qos_set_thermal_warm",
                    path="/service/qos/thermal",
                    payload={"thermal_state": "warm"},
                )
                report_steps.append(step)
                errors.extend(step_errors)
                if warm_qos and str(warm_qos.get("active_mode")) == "quality":
                    errors.append("qos_set_thermal_warm:quality_mode_not_demoted")

                step, step_errors, _ = _request_step(
                    client,
                    label="qos_set_thermal_hot",
                    path="/service/qos/thermal",
                    payload={"thermal_state": "hot"},
                )
                report_steps.append(step)
                errors.extend(step_errors)

                step, step_errors, critical_qos = _request_step(
                    client,
                    label="qos_set_thermal_critical",
                    path="/service/qos/thermal",
                    payload={"thermal_state": "critical"},
                )
                report_steps.append(step)
                errors.extend(step_errors)
                if critical_qos:
                    critical_mode = str(critical_qos.get("active_mode") or "")
                    if critical_mode != expect_critical_mode:
                        errors.append(
                            "qos_set_thermal_critical:unexpected_mode"
                            f":actual={critical_mode}:expected={expect_critical_mode}"
                        )
                    critical_route_mode = str(critical_qos.get("route_mode") or "")
                    expected_route_mode = _route_mode_for_qos(expect_critical_mode)
                    if critical_route_mode != expected_route_mode:
                        errors.append(
                            "qos_set_thermal_critical:unexpected_route_mode"
                            f":actual={critical_route_mode}:expected={expected_route_mode}"
                        )

                step, step_errors, _ = _request_step(
                    client,
                    label="qos_set_thermal_cool_recovery_1",
                    path="/service/qos/thermal",
                    payload={"thermal_state": "cool"},
                )
                report_steps.append(step)
                errors.extend(step_errors)

                step, step_errors, final_qos = _request_step(
                    client,
                    label="qos_set_thermal_cool_recovery_2",
                    path="/service/qos/thermal",
                    payload={"thermal_state": "cool"},
                )
                report_steps.append(step)
                errors.extend(step_errors)
                if final_qos:
                    final_mode = str(final_qos.get("active_mode") or "")
                    if final_mode != expect_final_mode:
                        errors.append(
                            "qos_set_thermal_cool_recovery_2:unexpected_mode"
                            f":actual={final_mode}:expected={expect_final_mode}"
                        )

                step, step_errors, _ = _request_step(
                    client,
                    label="qos_set_thermal_invalid",
                    path="/service/qos/thermal",
                    payload={"thermal_state": "lava"},
                    expected_status=400,
                )
                report_steps.append(step)
                errors.extend(step_errors)
        finally:
            if app is not None:
                _shutdown_app(app)

    checks_total = len(report_steps)
    checks_failed = len(errors)
    summary = {
        "status": "pass" if checks_failed == 0 else "fail",
        "checks_total": checks_total,
        "checks_passed": checks_total - checks_failed,
        "checks_failed": checks_failed,
        "errors": errors,
        "expected": {
            "initial_mode": initial_mode,
            "critical_mode": expect_critical_mode,
            "final_mode": expect_final_mode,
        },
    }
    report = {
        "generated_at": _utc_now_iso(),
        "suite": "qos_governor_gate_v1",
        "summary": summary,
        "steps": report_steps,
    }

    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = project_root / output_path
        _write_json(output_path, report)
        print(f"[qos-governor-gate] report={output_path}")

    if checks_failed > 0:
        print("[qos-governor-gate] FAILED")
        for item in errors:
            print(f"- {item}")
        return 1

    print("[qos-governor-gate] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
