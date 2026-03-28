#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run blocking autonomy circuit-breaker stability soak gate "
            "(multi-cycle arm/disarm drills across global/user/agent scopes)."
        )
    )
    parser.add_argument(
        "--cycles",
        type=int,
        default=int(os.getenv("AMARYLLIS_BREAKER_SOAK_CYCLES", "6")),
        help="Number of arm/disarm drill cycles.",
    )
    parser.add_argument(
        "--min-success-rate-pct",
        type=float,
        default=float(os.getenv("AMARYLLIS_BREAKER_SOAK_MIN_SUCCESS_RATE_PCT", "100")),
        help="Minimum required successful cycle rate.",
    )
    parser.add_argument(
        "--max-failed-cycles",
        type=int,
        default=int(os.getenv("AMARYLLIS_BREAKER_SOAK_MAX_FAILED_CYCLES", "0")),
        help="Maximum allowed failed cycles.",
    )
    parser.add_argument(
        "--max-p95-cycle-latency-ms",
        type=float,
        default=float(os.getenv("AMARYLLIS_BREAKER_SOAK_MAX_P95_CYCLE_MS", "4500")),
        help="Maximum allowed p95 cycle latency in milliseconds.",
    )
    parser.add_argument(
        "--token",
        default="dev-token",
        help="Primary user auth token used for runtime checks.",
    )
    parser.add_argument(
        "--service-token",
        default="service-token",
        help="Service auth token used for runtime checks.",
    )
    parser.add_argument(
        "--output",
        default="artifacts/autonomy-circuit-breaker-soak-gate-report.json",
        help="JSON report output path.",
    )
    return parser.parse_args()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_path(repo_root: Path, raw: str) -> Path:
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


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


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _is_json_response(headers: dict[str, Any]) -> bool:
    return str(headers.get("content-type") or "").startswith("application/json")


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    clamped_q = max(0.0, min(1.0, float(q)))
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    raw = (len(ordered) - 1) * clamped_q
    low = int(math.floor(raw))
    high = int(math.ceil(raw))
    if low == high:
        return ordered[low]
    weight = raw - low
    return ordered[low] * (1.0 - weight) + ordered[high] * weight


def _required_scopes(cycles: int) -> set[str]:
    if cycles <= 1:
        return {"global"}
    if cycles == 2:
        return {"global", "user"}
    return {"global", "user", "agent"}


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    cycles_total = int(args.cycles)
    min_success_rate_pct = _safe_float(args.min_success_rate_pct)
    max_failed_cycles = int(args.max_failed_cycles)
    max_p95_cycle_latency_ms = _safe_float(args.max_p95_cycle_latency_ms)

    if cycles_total < 1:
        print("[autonomy-circuit-breaker-soak-gate] --cycles must be >= 1", file=sys.stderr)
        return 2
    if not (0.0 <= min_success_rate_pct <= 100.0):
        print("[autonomy-circuit-breaker-soak-gate] --min-success-rate-pct must be within [0, 100]", file=sys.stderr)
        return 2
    if max_failed_cycles < 0:
        print("[autonomy-circuit-breaker-soak-gate] --max-failed-cycles must be >= 0", file=sys.stderr)
        return 2
    if max_p95_cycle_latency_ms < 0.0:
        print("[autonomy-circuit-breaker-soak-gate] --max-p95-cycle-latency-ms must be >= 0", file=sys.stderr)
        return 2

    user_token = str(args.token).strip() or "dev-token"
    service_token = str(args.service_token).strip() or "service-token"

    checks: list[dict[str, Any]] = []
    cycles: list[dict[str, Any]] = []

    def add_check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    tmp_dir = tempfile.TemporaryDirectory(prefix="amaryllis-autonomy-circuit-breaker-soak-gate-")
    support_dir = Path(tmp_dir.name) / "support"
    app = None

    os.environ["AMARYLLIS_AUTH_ENABLED"] = "true"
    os.environ["AMARYLLIS_AUTH_TOKENS"] = json.dumps(
        {
            user_token: {"user_id": "soak-user-a", "scopes": ["user"]},
            "soak-user-b-token": {"user_id": "soak-user-b", "scopes": ["user"]},
            service_token: {"user_id": "soak-service", "scopes": ["service"]},
        },
        ensure_ascii=False,
    )
    os.environ["AMARYLLIS_SUPPORT_DIR"] = str(support_dir)
    os.environ["AMARYLLIS_MEMORY_CONSOLIDATION_ENABLED"] = "false"
    os.environ["AMARYLLIS_MCP_ENDPOINTS"] = ""
    os.environ["AMARYLLIS_SECURITY_PROFILE"] = "production"
    os.environ["AMARYLLIS_COGNITION_BACKEND"] = "deterministic"
    os.environ["AMARYLLIS_AUTOMATION_ENABLED"] = "false"
    os.environ["AMARYLLIS_BACKUP_ENABLED"] = "false"
    os.environ["AMARYLLIS_BACKUP_RESTORE_DRILL_ENABLED"] = "false"
    os.environ["AMARYLLIS_REQUEST_TRACE_LOGS_ENABLED"] = "false"

    try:
        from fastapi.testclient import TestClient  # noqa: PLC0415
        from runtime.server import create_app  # noqa: PLC0415

        app = create_app()
        with TestClient(app) as client:
            status = client.get("/service/runs/autonomy-circuit-breaker", headers=_auth(service_token))
            add_check("runtime_status_endpoint_ok", status.status_code == 200, f"status={status.status_code}")
            payload = status.json() if _is_json_response(dict(status.headers)) else {}
            armed = bool((payload.get("circuit_breaker") or {}).get("armed"))
            add_check("runtime_initially_disarmed", not armed, f"armed={armed}")

            create_a_primary = client.post(
                "/agents/create",
                headers=_auth(user_token),
                json={
                    "name": "Breaker Soak User A Primary",
                    "system_prompt": "breaker-soak",
                    "user_id": "soak-user-a",
                    "tools": ["web_search"],
                },
            )
            create_a_secondary = client.post(
                "/agents/create",
                headers=_auth(user_token),
                json={
                    "name": "Breaker Soak User A Secondary",
                    "system_prompt": "breaker-soak",
                    "user_id": "soak-user-a",
                    "tools": ["web_search"],
                },
            )
            create_b = client.post(
                "/agents/create",
                headers=_auth("soak-user-b-token"),
                json={
                    "name": "Breaker Soak User B",
                    "system_prompt": "breaker-soak",
                    "user_id": "soak-user-b",
                    "tools": ["web_search"],
                },
            )
            add_check("create_agent_a_primary_ok", create_a_primary.status_code == 200, f"status={create_a_primary.status_code}")
            add_check(
                "create_agent_a_secondary_ok",
                create_a_secondary.status_code == 200,
                f"status={create_a_secondary.status_code}",
            )
            add_check("create_agent_b_ok", create_b.status_code == 200, f"status={create_b.status_code}")

            a_primary_id = str((create_a_primary.json() if _is_json_response(dict(create_a_primary.headers)) else {}).get("id") or "")
            a_secondary_id = str((create_a_secondary.json() if _is_json_response(dict(create_a_secondary.headers)) else {}).get("id") or "")
            b_id = str((create_b.json() if _is_json_response(dict(create_b.headers)) else {}).get("id") or "")
            add_check("create_agent_a_primary_id", bool(a_primary_id), f"id={a_primary_id}")
            add_check("create_agent_a_secondary_id", bool(a_secondary_id), f"id={a_secondary_id}")
            add_check("create_agent_b_id", bool(b_id), f"id={b_id}")

            scope_order = ["global", "user", "agent"]
            for idx in range(cycles_total):
                scope_type = scope_order[idx % len(scope_order)]
                cycle_checks: list[dict[str, Any]] = []

                def cycle_check(name: str, ok: bool, detail: str) -> None:
                    cycle_checks.append({"name": name, "ok": bool(ok), "detail": detail})

                started = time.perf_counter()
                arm_reason = f"soak-arm-cycle-{idx + 1}-{scope_type}"
                disarm_reason = f"soak-disarm-cycle-{idx + 1}-{scope_type}"
                arm_request_id = ""
                cycle_error = ""

                try:
                    arm_body: dict[str, Any] = {
                        "action": "arm",
                        "scope_type": scope_type,
                        "reason": arm_reason,
                        "apply_kill_switch": False,
                    }
                    if scope_type == "user":
                        arm_body["scope_user_id"] = "soak-user-a"
                    if scope_type == "agent":
                        arm_body["scope_agent_id"] = a_primary_id

                    arm = client.post(
                        "/service/runs/autonomy-circuit-breaker",
                        headers=_auth(service_token),
                        json=arm_body,
                    )
                    cycle_check("arm_ok", arm.status_code == 200, f"status={arm.status_code}")
                    arm_payload = arm.json() if _is_json_response(dict(arm.headers)) else {}
                    arm_state = arm_payload.get("circuit_breaker") if isinstance(arm_payload, dict) else {}
                    cycle_check("arm_state_armed", bool((arm_state or {}).get("armed")), f"armed={bool((arm_state or {}).get('armed'))}")
                    action_receipt = arm_payload.get("action_receipt") if isinstance(arm_payload, dict) else {}
                    arm_request_id = str((action_receipt or {}).get("request_id") or "").strip()
                    cycle_check("arm_request_id_present", bool(arm_request_id), f"request_id={arm_request_id}")

                    blocked = client.post(
                        f"/agents/{a_primary_id}/runs",
                        headers=_auth(user_token),
                        json={
                            "user_id": "soak-user-a",
                            "message": f"breaker soak blocked check cycle={idx + 1}",
                        },
                    )
                    cycle_check("target_execute_blocked", blocked.status_code == 400, f"status={blocked.status_code}")

                    if scope_type == "global":
                        non_target = client.post(
                            f"/agents/{b_id}/runs",
                            headers=_auth("soak-user-b-token"),
                            json={
                                "user_id": "soak-user-b",
                                "message": f"breaker global scope block check cycle={idx + 1}",
                            },
                        )
                        cycle_check("non_target_blocked_global", non_target.status_code == 400, f"status={non_target.status_code}")
                    elif scope_type == "user":
                        non_target = client.post(
                            f"/agents/{b_id}/runs",
                            headers=_auth("soak-user-b-token"),
                            json={
                                "user_id": "soak-user-b",
                                "message": f"breaker user scope allow check cycle={idx + 1}",
                            },
                        )
                        cycle_check("non_target_user_allowed", non_target.status_code == 200, f"status={non_target.status_code}")
                    else:
                        non_target = client.post(
                            f"/agents/{a_secondary_id}/runs",
                            headers=_auth(user_token),
                            json={
                                "user_id": "soak-user-a",
                                "message": f"breaker agent scope allow check cycle={idx + 1}",
                            },
                        )
                        cycle_check("non_target_agent_allowed", non_target.status_code == 200, f"status={non_target.status_code}")

                    timeline = client.get(
                        "/service/runs/autonomy-circuit-breaker/timeline",
                        headers=_auth(service_token),
                        params={
                            "limit": 100,
                            "transition": "arm",
                            "request_id": arm_request_id,
                        },
                    )
                    timeline_payload = timeline.json() if _is_json_response(dict(timeline.headers)) else {}
                    timeline_items = timeline_payload.get("items") if isinstance(timeline_payload, dict) else []
                    timeline_items = timeline_items if isinstance(timeline_items, list) else []
                    timeline_item = next(
                        (
                            item
                            for item in timeline_items
                            if str(((item.get("transition") or {}).get("reason") or "")).strip() == arm_reason
                        ),
                        None,
                    )
                    cycle_check("timeline_endpoint_ok", timeline.status_code == 200, f"status={timeline.status_code}")
                    cycle_check(
                        "timeline_traceability",
                        isinstance(timeline_item, dict)
                        and bool(str(timeline_item.get("actor") or "").strip())
                        and bool(str(timeline_item.get("request_id") or "").strip()),
                        "timeline includes actor/request_id for arm transition",
                    )
                    cycle_check(
                        "timeline_recovery_guidance_present",
                        isinstance((timeline_payload.get("recovery_guidance") or {}).get("recommendations"), list),
                        "timeline includes recovery guidance recommendations",
                    )

                    disarm_body: dict[str, Any] = {
                        "action": "disarm",
                        "scope_type": scope_type,
                        "reason": disarm_reason,
                    }
                    if scope_type == "user":
                        disarm_body["scope_user_id"] = "soak-user-a"
                    if scope_type == "agent":
                        disarm_body["scope_agent_id"] = a_primary_id

                    disarm = client.post(
                        "/service/runs/autonomy-circuit-breaker",
                        headers=_auth(service_token),
                        json=disarm_body,
                    )
                    cycle_check("disarm_ok", disarm.status_code == 200, f"status={disarm.status_code}")
                    disarm_payload = disarm.json() if _is_json_response(dict(disarm.headers)) else {}
                    disarm_state = disarm_payload.get("circuit_breaker") if isinstance(disarm_payload, dict) else {}
                    cycle_check("disarm_state_disarmed", not bool((disarm_state or {}).get("armed")), f"armed={bool((disarm_state or {}).get('armed'))}")

                    restored = client.post(
                        f"/agents/{a_primary_id}/runs",
                        headers=_auth(user_token),
                        json={
                            "user_id": "soak-user-a",
                            "message": f"breaker restore check cycle={idx + 1}",
                        },
                    )
                    cycle_check("execute_restored_after_disarm", restored.status_code == 200, f"status={restored.status_code}")
                except Exception as exc:  # pragma: no cover - integration fallback
                    cycle_error = str(exc)
                    cycle_check("cycle_exception", False, cycle_error)

                duration_ms = (time.perf_counter() - started) * 1000.0
                cycle_failed = [item for item in cycle_checks if not bool(item.get("ok"))]
                cycles.append(
                    {
                        "cycle": idx + 1,
                        "scope_type": scope_type,
                        "status": "pass" if not cycle_failed else "fail",
                        "duration_ms": round(duration_ms, 4),
                        "arm_request_id": arm_request_id,
                        "error": cycle_error,
                        "checks": cycle_checks,
                    }
                )

    except Exception as exc:  # pragma: no cover - integration fallback
        add_check("runtime_exception", False, str(exc))
    finally:
        if app is not None:
            _shutdown_app(app)
        tmp_dir.cleanup()

    durations = [_safe_float(item.get("duration_ms")) for item in cycles]
    cycle_failures = [item for item in cycles if str(item.get("status") or "") != "pass"]
    cycle_success_rate_pct = (100.0 * (len(cycles) - len(cycle_failures)) / float(len(cycles))) if cycles else 0.0
    p95_cycle_latency_ms = _quantile(durations, 0.95)
    scopes_covered = sorted({str(item.get("scope_type") or "") for item in cycles if str(item.get("scope_type") or "")})
    required = _required_scopes(cycles_total)

    add_check("cycles_executed", len(cycles) == cycles_total, f"expected={cycles_total} actual={len(cycles)}")
    add_check(
        "success_rate_threshold",
        cycle_success_rate_pct >= min_success_rate_pct,
        f"success_rate_pct={round(cycle_success_rate_pct, 4)} threshold={min_success_rate_pct}",
    )
    add_check(
        "max_failed_cycles",
        len(cycle_failures) <= max_failed_cycles,
        f"failed_cycles={len(cycle_failures)} threshold={max_failed_cycles}",
    )
    add_check(
        "p95_cycle_latency_threshold",
        p95_cycle_latency_ms <= max_p95_cycle_latency_ms,
        f"p95_cycle_latency_ms={round(p95_cycle_latency_ms, 4)} threshold={max_p95_cycle_latency_ms}",
    )
    add_check(
        "required_scopes_covered",
        required.issubset(set(scopes_covered)),
        f"required={sorted(required)} covered={scopes_covered}",
    )

    failed = [item for item in checks if not bool(item.get("ok"))]
    report = {
        "suite": "autonomy_circuit_breaker_soak_gate_v1",
        "generated_at": _utc_now_iso(),
        "config": {
            "cycles": cycles_total,
            "min_success_rate_pct": min_success_rate_pct,
            "max_failed_cycles": max_failed_cycles,
            "max_p95_cycle_latency_ms": max_p95_cycle_latency_ms,
        },
        "summary": {
            "status": "pass" if not failed else "fail",
            "checks_total": len(checks),
            "checks_passed": len(checks) - len(failed),
            "checks_failed": len(failed),
            "cycles_total": len(cycles),
            "cycles_passed": len(cycles) - len(cycle_failures),
            "cycles_failed": len(cycle_failures),
            "success_rate_pct": round(cycle_success_rate_pct, 4),
            "p95_cycle_latency_ms": round(p95_cycle_latency_ms, 4),
            "max_cycle_latency_ms": round(max(durations) if durations else 0.0, 4),
            "scopes_covered": scopes_covered,
        },
        "checks": checks,
        "cycles": cycles,
    }

    output_path = _resolve_path(repo_root, str(args.output))
    _write_json(output_path, report)

    if failed:
        names = ", ".join(str(item.get("name")) for item in failed)
        print(f"[autonomy-circuit-breaker-soak-gate] FAILED checks={names}")
        return 1

    print("[autonomy-circuit-breaker-soak-gate] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
