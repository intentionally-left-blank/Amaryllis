#!/usr/bin/env python3
from __future__ import annotations

import argparse
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
            "Validate provider entitlement diagnostics contract: machine-readable "
            "diagnostics card for blocked/ready provider access states."
        )
    )
    parser.add_argument(
        "--expected-contract-version",
        default="provider_entitlement_diagnostics_v1",
        help="Expected diagnostics endpoint contract version.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional report output path.",
    )
    return parser.parse_args()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _json_payload(response: Any) -> dict[str, Any]:
    if not str(response.headers.get("content-type") or "").startswith("application/json"):
        return {}
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


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


def main() -> int:
    args = _parse_args()
    expected_contract_version = str(args.expected_contract_version or "").strip() or "provider_entitlement_diagnostics_v1"

    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    try:
        from fastapi.testclient import TestClient  # noqa: PLC0415
    except Exception as exc:
        print(f"[provider-entitlement-diagnostics-gate] FAILED import_error={exc}")
        return 2

    errors: list[str] = []
    checks: list[dict[str, Any]] = []
    app: Any | None = None
    report: dict[str, Any] = {}

    with tempfile.TemporaryDirectory(prefix="amaryllis-provider-entitlement-diagnostics-gate-") as tmp:
        support_dir = Path(tmp) / "support"
        auth_tokens = {
            "admin-token": {"user_id": "admin", "scopes": ["admin", "user"]},
            "user-token": {"user_id": "user-1", "scopes": ["user"]},
        }
        os.environ["AMARYLLIS_SUPPORT_DIR"] = str(support_dir)
        os.environ["AMARYLLIS_AUTH_ENABLED"] = "true"
        os.environ["AMARYLLIS_AUTH_TOKENS"] = json.dumps(auth_tokens, ensure_ascii=False)
        os.environ["AMARYLLIS_MEMORY_CONSOLIDATION_ENABLED"] = "false"
        os.environ["AMARYLLIS_MCP_ENDPOINTS"] = ""
        os.environ["AMARYLLIS_SECURITY_PROFILE"] = "production"
        os.environ["AMARYLLIS_OPENAI_API_KEY"] = ""
        os.environ["AMARYLLIS_ANTHROPIC_API_KEY"] = ""
        os.environ["AMARYLLIS_OPENROUTER_API_KEY"] = ""
        os.environ["AMARYLLIS_COGNITION_BACKEND"] = "deterministic"

        try:
            import runtime.server as server_module  # noqa: PLC0415

            server_module = importlib.reload(server_module)
            app = server_module.app
        except Exception as exc:
            print(f"[provider-entitlement-diagnostics-gate] FAILED import_or_boot_error={exc}")
            return 2

        try:
            with TestClient(app) as client:
                contract = client.get("/auth/providers/contract", headers=_auth("user-token"))
                checks.append({"name": "provider_contract_status", "status": contract.status_code, "expected": 200})
                if contract.status_code != 200:
                    errors.append(f"provider_contract_status:{contract.status_code}")
                contract_payload = _json_payload(contract)
                endpoints = contract_payload.get("session_endpoints", [])
                endpoint_signature = {
                    (str(item.get("method") or "").upper(), str(item.get("path") or ""))
                    for item in endpoints
                    if isinstance(item, dict)
                }
                diagnostics_endpoint_exists = ("GET", "/auth/providers/diagnostics") in endpoint_signature
                checks.append(
                    {
                        "name": "provider_contract_lists_diagnostics_endpoint",
                        "value": diagnostics_endpoint_exists,
                        "expected": True,
                    }
                )
                if not diagnostics_endpoint_exists:
                    errors.append("provider_contract_missing_diagnostics_endpoint")

                before = client.get(
                    "/auth/providers/diagnostics",
                    headers=_auth("admin-token"),
                    params={"user_id": "diag-user", "provider": "openai"},
                )
                checks.append({"name": "diagnostics_before_status", "status": before.status_code, "expected": 200})
                if before.status_code != 200:
                    errors.append(f"diagnostics_before_status:{before.status_code}")
                before_payload = _json_payload(before)
                before_version = str(before_payload.get("contract_version") or "")
                checks.append(
                    {
                        "name": "diagnostics_before_contract_version",
                        "value": before_version,
                        "expected": expected_contract_version,
                    }
                )
                if before_version != expected_contract_version:
                    errors.append(f"diagnostics_before_contract_version:{before_version}")
                before_card = before_payload.get("card") if isinstance(before_payload, dict) else {}
                before_card = before_card if isinstance(before_card, dict) else {}
                before_status = str(before_card.get("status") or "")
                checks.append({"name": "diagnostics_before_status_value", "value": before_status, "expected": "blocked"})
                if before_status != "blocked":
                    errors.append(f"diagnostics_before_status_value:{before_status}")
                before_error_code = str((before_card.get("error_contract") or {}).get("error_code") or "")
                checks.append(
                    {
                        "name": "diagnostics_before_error_code",
                        "value": before_error_code,
                        "expected": "provider_access_not_configured",
                    }
                )
                if before_error_code != "provider_access_not_configured":
                    errors.append(f"diagnostics_before_error_code:{before_error_code}")

                created = client.post(
                    "/auth/providers/sessions",
                    headers=_auth("admin-token"),
                    json={
                        "user_id": "diag-user",
                        "provider": "openai",
                        "credential_ref": "secret://vault/openai/diag-user",
                        "scopes": ["chat"],
                    },
                )
                checks.append({"name": "create_session_status", "status": created.status_code, "expected": 200})
                if created.status_code != 200:
                    errors.append(f"create_session_status:{created.status_code}")
                created_payload = _json_payload(created)
                session_id = str((created_payload.get("session") or {}).get("id") or "")
                checks.append({"name": "create_session_id_present", "value": bool(session_id), "expected": True})
                if not session_id:
                    errors.append("create_session_id_missing")

                after = client.get(
                    "/auth/providers/diagnostics",
                    headers=_auth("admin-token"),
                    params={"user_id": "diag-user", "provider": "openai"},
                )
                checks.append({"name": "diagnostics_after_status", "status": after.status_code, "expected": 200})
                if after.status_code != 200:
                    errors.append(f"diagnostics_after_status:{after.status_code}")
                after_payload = _json_payload(after)
                after_card = after_payload.get("card") if isinstance(after_payload, dict) else {}
                after_card = after_card if isinstance(after_card, dict) else {}
                after_status = str(after_card.get("status") or "")
                checks.append({"name": "diagnostics_after_status_value", "value": after_status, "expected": "ready"})
                if after_status != "ready":
                    errors.append(f"diagnostics_after_status_value:{after_status}")
                after_selected_route = str((after_card.get("route_policy") or {}).get("selected_route") or "")
                checks.append(
                    {
                        "name": "diagnostics_after_selected_route",
                        "value": after_selected_route,
                        "expected": "user_session",
                    }
                )
                if after_selected_route != "user_session":
                    errors.append(f"diagnostics_after_selected_route:{after_selected_route}")
                after_session_summary = after_card.get("session_summary") if isinstance(after_card, dict) else {}
                after_session_summary = after_session_summary if isinstance(after_session_summary, dict) else {}
                active_count = int(after_session_summary.get("active_count") or 0)
                checks.append({"name": "diagnostics_after_active_count", "value": active_count, "expected_min": 1})
                if active_count < 1:
                    errors.append(f"diagnostics_after_active_count:{active_count}")

                revoked = client.post(
                    f"/auth/providers/sessions/{session_id}/revoke",
                    headers=_auth("admin-token"),
                    json={"reason": "diagnostics-gate-cleanup"},
                )
                checks.append({"name": "revoke_session_status", "status": revoked.status_code, "expected": 200})
                if revoked.status_code != 200:
                    errors.append(f"revoke_session_status:{revoked.status_code}")

                after_revoke = client.get(
                    "/auth/providers/diagnostics",
                    headers=_auth("admin-token"),
                    params={"user_id": "diag-user", "provider": "openai"},
                )
                checks.append({"name": "diagnostics_after_revoke_status", "status": after_revoke.status_code, "expected": 200})
                if after_revoke.status_code != 200:
                    errors.append(f"diagnostics_after_revoke_status:{after_revoke.status_code}")
                after_revoke_payload = _json_payload(after_revoke)
                after_revoke_card = after_revoke_payload.get("card") if isinstance(after_revoke_payload, dict) else {}
                after_revoke_card = after_revoke_card if isinstance(after_revoke_card, dict) else {}
                after_revoke_status_value = str(after_revoke_card.get("status") or "")
                checks.append(
                    {
                        "name": "diagnostics_after_revoke_status_value",
                        "value": after_revoke_status_value,
                        "expected": "blocked",
                    }
                )
                if after_revoke_status_value != "blocked":
                    errors.append(f"diagnostics_after_revoke_status_value:{after_revoke_status_value}")

                aggregate = client.get(
                    "/auth/providers/diagnostics",
                    headers=_auth("user-token"),
                    params={"user_id": "user-1", "session_limit": 20},
                )
                checks.append({"name": "diagnostics_aggregate_status", "status": aggregate.status_code, "expected": 200})
                if aggregate.status_code != 200:
                    errors.append(f"diagnostics_aggregate_status:{aggregate.status_code}")
                aggregate_payload = _json_payload(aggregate)
                aggregate_version = str(aggregate_payload.get("contract_version") or "")
                checks.append(
                    {
                        "name": "diagnostics_aggregate_contract_version",
                        "value": aggregate_version,
                        "expected": expected_contract_version,
                    }
                )
                if aggregate_version != expected_contract_version:
                    errors.append(f"diagnostics_aggregate_contract_version:{aggregate_version}")
                aggregate_count = int(aggregate_payload.get("count") or 0)
                checks.append({"name": "diagnostics_aggregate_count", "value": aggregate_count, "expected_min": 3})
                if aggregate_count < 3:
                    errors.append(f"diagnostics_aggregate_count:{aggregate_count}")

            report = {
                "suite": "provider_entitlement_diagnostics_gate_v1",
                "summary": {
                    "status": "pass" if not errors else "fail",
                    "checks_total": len(checks),
                    "checks_failed": len(errors),
                    "errors": errors,
                    "expected_contract_version": expected_contract_version,
                },
                "checks": checks,
            }
        finally:
            if app is not None:
                _shutdown_app(app)

    if args.output:
        output = Path(args.output)
        if not output.is_absolute():
            output = project_root / output
        _write_json(output, report)
        print(f"[provider-entitlement-diagnostics-gate] report={output}")

    if errors:
        print("[provider-entitlement-diagnostics-gate] FAILED")
        for item in errors:
            print(f"- {item}")
        return 1
    print("[provider-entitlement-diagnostics-gate] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
