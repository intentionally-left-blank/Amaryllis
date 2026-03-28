#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate autonomy circuit breaker contract "
            "(docs + runtime API smoke for service controls and run blocking behavior)."
        )
    )
    parser.add_argument(
        "--doc",
        default="docs/autonomy-circuit-breaker.md",
        help="Path to autonomy circuit breaker documentation.",
    )
    parser.add_argument(
        "--token",
        default="dev-token",
        help="User auth token used for runtime checks.",
    )
    parser.add_argument(
        "--service-token",
        default="service-token",
        help="Service auth token used for runtime checks.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional JSON report output path.",
    )
    return parser.parse_args()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _resolve_path(repo_root: Path, raw: str) -> Path:
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


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


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    doc_path = _resolve_path(repo_root, str(args.doc))
    user_token = str(args.token).strip() or "dev-token"
    service_token = str(args.service_token).strip() or "service-token"

    checks: list[dict[str, Any]] = []

    def add_check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    if doc_path.exists():
        text = doc_path.read_text(encoding="utf-8")
        add_check("doc_exists", True, str(doc_path))
        add_check(
            "doc_status_endpoint",
            "/service/runs/autonomy-circuit-breaker" in text,
            "status endpoint documented",
        )
        add_check(
            "doc_update_endpoint",
            "POST /service/runs/autonomy-circuit-breaker" in text,
            "update endpoint documented",
        )
        add_check("doc_actions", "arm|disarm" in text, "arm/disarm action contract documented")
        add_check(
            "doc_execute_blocking",
            "interaction_mode=execute" in text,
            "execute-mode blocking behavior documented",
        )
    else:
        add_check("doc_exists", False, f"missing: {doc_path}")

    tmp_dir = tempfile.TemporaryDirectory(prefix="amaryllis-autonomy-circuit-breaker-gate-")
    support_dir = Path(tmp_dir.name) / "support"
    app = None

    os.environ["AMARYLLIS_AUTH_ENABLED"] = "true"
    os.environ["AMARYLLIS_AUTH_TOKENS"] = json.dumps(
        {
            user_token: {"user_id": "gate-user", "scopes": ["user"]},
            service_token: {"user_id": "gate-service", "scopes": ["service"]},
            "gate-admin-token": {"user_id": "gate-admin", "scopes": ["admin", "user"]},
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
            status_before = client.get(
                "/service/runs/autonomy-circuit-breaker",
                headers=_auth(service_token),
            )
            add_check(
                "runtime_status_endpoint_ok",
                status_before.status_code == 200,
                f"status={status_before.status_code}",
            )
            status_payload = status_before.json() if _is_json_response(dict(status_before.headers)) else {}
            armed_before = bool((status_payload.get("circuit_breaker") or {}).get("armed"))
            add_check(
                "runtime_initially_disarmed",
                not armed_before,
                f"armed={armed_before}",
            )

            create_agent = client.post(
                "/agents/create",
                headers=_auth(user_token),
                json={
                    "name": "Autonomy Circuit Breaker Gate Agent",
                    "system_prompt": "autonomy-circuit-breaker-gate",
                    "user_id": "gate-user",
                    "tools": ["web_search"],
                },
            )
            add_check(
                "runtime_create_agent_ok",
                create_agent.status_code == 200,
                f"status={create_agent.status_code}",
            )
            create_payload = create_agent.json() if _is_json_response(dict(create_agent.headers)) else {}
            agent_id = str(create_payload.get("id") or "").strip()
            add_check("runtime_create_agent_id", bool(agent_id), f"agent_id={agent_id}")

            arm = client.post(
                "/service/runs/autonomy-circuit-breaker",
                headers=_auth(service_token),
                json={
                    "action": "arm",
                    "reason": "gate-check",
                    "apply_kill_switch": False,
                },
            )
            add_check(
                "runtime_arm_ok",
                arm.status_code == 200,
                f"status={arm.status_code}",
            )
            arm_payload = arm.json() if _is_json_response(dict(arm.headers)) else {}
            arm_state = arm_payload.get("circuit_breaker") if isinstance(arm_payload, dict) else {}
            add_check(
                "runtime_arm_state",
                bool((arm_state or {}).get("armed")),
                f"armed={bool((arm_state or {}).get('armed'))}",
            )
            add_check(
                "runtime_arm_receipt",
                bool((arm_payload.get("action_receipt") or {}).get("signature")),
                "signed action receipt present",
            )

            run_blocked = client.post(
                f"/agents/{agent_id}/runs",
                headers=_auth(user_token),
                json={
                    "user_id": "gate-user",
                    "message": "execute while breaker armed",
                },
            )
            blocked_payload = run_blocked.json() if _is_json_response(dict(run_blocked.headers)) else {}
            blocked_error = blocked_payload.get("error") if isinstance(blocked_payload, dict) else {}
            add_check(
                "runtime_execute_create_blocked",
                run_blocked.status_code == 400
                and str((blocked_error or {}).get("type") or "") == "validation_error",
                f"status={run_blocked.status_code}",
            )

            dispatch_blocked = client.post(
                f"/agents/{agent_id}/runs/dispatch",
                headers=_auth(user_token),
                json={
                    "user_id": "gate-user",
                    "message": "dispatch execute while breaker armed",
                    "interaction_mode": "execute",
                },
            )
            add_check(
                "runtime_execute_dispatch_blocked",
                dispatch_blocked.status_code == 400,
                f"status={dispatch_blocked.status_code}",
            )

            dispatch_plan = client.post(
                f"/agents/{agent_id}/runs/dispatch",
                headers=_auth(user_token),
                json={
                    "user_id": "gate-user",
                    "message": "plan while breaker armed",
                    "interaction_mode": "plan",
                },
            )
            plan_payload = dispatch_plan.json() if _is_json_response(dict(dispatch_plan.headers)) else {}
            add_check(
                "runtime_plan_dispatch_allowed",
                dispatch_plan.status_code == 200
                and str(plan_payload.get("interaction_mode") or "") == "plan",
                f"status={dispatch_plan.status_code}",
            )

            disarm = client.post(
                "/service/runs/autonomy-circuit-breaker",
                headers=_auth(service_token),
                json={
                    "action": "disarm",
                    "reason": "gate-finished",
                },
            )
            disarm_payload = disarm.json() if _is_json_response(dict(disarm.headers)) else {}
            disarm_state = disarm_payload.get("circuit_breaker") if isinstance(disarm_payload, dict) else {}
            add_check(
                "runtime_disarm_ok",
                disarm.status_code == 200,
                f"status={disarm.status_code}",
            )
            add_check(
                "runtime_disarm_state",
                not bool((disarm_state or {}).get("armed")),
                f"armed={bool((disarm_state or {}).get('armed'))}",
            )

            create_run_after = client.post(
                f"/agents/{agent_id}/runs",
                headers=_auth(user_token),
                json={
                    "user_id": "gate-user",
                    "message": "execute after disarm",
                },
            )
            create_run_payload = (
                create_run_after.json() if _is_json_response(dict(create_run_after.headers)) else {}
            )
            run_payload = create_run_payload.get("run") if isinstance(create_run_payload, dict) else {}
            add_check(
                "runtime_execute_restored_after_disarm",
                create_run_after.status_code == 200 and bool(str((run_payload or {}).get("id") or "").strip()),
                f"status={create_run_after.status_code}",
            )

    except Exception as exc:  # pragma: no cover - integration fallback
        add_check("runtime_exception", False, str(exc))
    finally:
        if app is not None:
            _shutdown_app(app)
        tmp_dir.cleanup()

    failed = [item for item in checks if not bool(item.get("ok"))]
    report = {
        "suite": "autonomy_circuit_breaker_gate_v1",
        "generated_at": _utc_now_iso(),
        "checks": checks,
        "summary": {
            "status": "pass" if not failed else "fail",
            "total": len(checks),
            "passed": len(checks) - len(failed),
            "failed": len(failed),
        },
    }

    output = str(args.output or "").strip()
    if output:
        _write_json(_resolve_path(repo_root, output), report)

    if failed:
        names = ", ".join(str(item.get("name")) for item in failed)
        print(f"[autonomy-circuit-breaker-gate] FAILED checks={names}")
        return 1

    print("[autonomy-circuit-breaker-gate] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
