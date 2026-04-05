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
            "Validate provider session security policy: no raw credential leakage, "
            "revocation propagation, and auth boundary enforcement."
        )
    )
    parser.add_argument(
        "--expected-session-count-after-revoke",
        type=int,
        default=int(os.getenv("AMARYLLIS_PROVIDER_SESSION_EXPECTED_COUNT_AFTER_REVOKE", "0")),
        help="Expected active session count for provider after revoke.",
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


def _json_payload(response: Any) -> dict[str, Any]:
    if not str(response.headers.get("content-type") or "").startswith("application/json"):
        return {}
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def main() -> int:
    args = _parse_args()
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    expected_after_revoke = max(0, int(args.expected_session_count_after_revoke))

    try:
        from fastapi.testclient import TestClient  # noqa: PLC0415
    except Exception as exc:
        print(f"[provider-session-policy-check] FAILED import_error={exc}")
        return 2

    errors: list[str] = []
    checks: list[dict[str, Any]] = []
    app: Any | None = None
    report: dict[str, Any] = {}

    with tempfile.TemporaryDirectory(prefix="amaryllis-provider-session-policy-check-") as tmp:
        support_dir = Path(tmp) / "support"
        auth_tokens = {
            "admin-token": {"user_id": "admin", "scopes": ["admin", "user"]},
            "user-token": {"user_id": "user-1", "scopes": ["user"]},
            "user2-token": {"user_id": "user-2", "scopes": ["user"]},
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
            print(f"[provider-session-policy-check] FAILED import_or_boot_error={exc}")
            return 2

        try:
            with TestClient(app) as client:
                contract = client.get("/auth/providers/contract", headers=_auth("user-token"))
                checks.append({"name": "contract_status", "status": contract.status_code, "expected": 200})
                if contract.status_code != 200:
                    errors.append(f"contract_status:{contract.status_code}")

                secret_ref = "secret://vault/openai/super-secret-token-12345"
                created = client.post(
                    "/auth/providers/sessions",
                    headers=_auth("user-token"),
                    json={
                        "user_id": "user-1",
                        "provider": "openai",
                        "credential_ref": secret_ref,
                        "display_name": "Personal OpenAI",
                        "scopes": ["chat", "news"],
                    },
                )
                checks.append({"name": "create_status", "status": created.status_code, "expected": 200})
                if created.status_code != 200:
                    errors.append(f"create_status:{created.status_code}")
                created_payload = _json_payload(created)
                session = created_payload.get("session") if isinstance(created_payload, dict) else {}
                session_id = str((session or {}).get("id") or "")
                credential_hint = str((session or {}).get("credential_ref_hint") or "")
                checks.append({"name": "session_id_present", "value": bool(session_id)})
                if not session_id:
                    errors.append("session_id_missing")
                has_raw_credential = "credential_ref" in (session or {})
                checks.append({"name": "create_no_raw_credential_ref", "value": not has_raw_credential})
                if has_raw_credential:
                    errors.append("raw_credential_ref_exposed")
                hint_leaks_secret = bool(secret_ref in credential_hint)
                checks.append({"name": "credential_hint_not_equal_full_secret", "value": not hint_leaks_secret})
                if hint_leaks_secret:
                    errors.append("credential_hint_leaks_full_secret")

                listed_owner = client.get(
                    "/auth/providers/sessions",
                    headers=_auth("user-token"),
                    params={"user_id": "user-1", "provider": "openai"},
                )
                checks.append({"name": "list_owner_status", "status": listed_owner.status_code, "expected": 200})
                if listed_owner.status_code != 200:
                    errors.append(f"list_owner_status:{listed_owner.status_code}")
                listed_owner_payload = _json_payload(listed_owner)
                listed_owner_text = json.dumps(listed_owner_payload, ensure_ascii=False)
                checks.append(
                    {
                        "name": "list_owner_no_raw_secret_material",
                        "value": secret_ref not in listed_owner_text,
                    }
                )
                if secret_ref in listed_owner_text:
                    errors.append("list_owner_leaks_secret")

                denied_list = client.get(
                    "/auth/providers/sessions",
                    headers=_auth("user2-token"),
                    params={"user_id": "user-1", "provider": "openai"},
                )
                checks.append({"name": "cross_user_list_denied", "status": denied_list.status_code, "expected": 403})
                if denied_list.status_code != 403:
                    errors.append(f"cross_user_list_status:{denied_list.status_code}")

                denied_revoke = client.post(
                    f"/auth/providers/sessions/{session_id}/revoke",
                    headers=_auth("user2-token"),
                    json={"reason": "cross-user-revoke"},
                )
                checks.append({"name": "cross_user_revoke_denied", "status": denied_revoke.status_code, "expected": 403})
                if denied_revoke.status_code != 403:
                    errors.append(f"cross_user_revoke_status:{denied_revoke.status_code}")

                revoked = client.post(
                    f"/auth/providers/sessions/{session_id}/revoke",
                    headers=_auth("user-token"),
                    json={"reason": "rotation"},
                )
                checks.append({"name": "revoke_owner_status", "status": revoked.status_code, "expected": 200})
                if revoked.status_code != 200:
                    errors.append(f"revoke_owner_status:{revoked.status_code}")
                revoked_payload = _json_payload(revoked)
                revoked_session = revoked_payload.get("session") if isinstance(revoked_payload, dict) else {}
                revoked_status = str((revoked_session or {}).get("status") or "")
                checks.append({"name": "revoke_status_value", "value": revoked_status, "expected": "revoked"})
                if revoked_status != "revoked":
                    errors.append(f"revoke_status_value:{revoked_status}")

                listed_after = client.get(
                    "/auth/providers/sessions",
                    headers=_auth("user-token"),
                    params={"user_id": "user-1", "provider": "openai", "include_revoked": False},
                )
                checks.append({"name": "list_after_revoke_status", "status": listed_after.status_code, "expected": 200})
                if listed_after.status_code != 200:
                    errors.append(f"list_after_revoke_status:{listed_after.status_code}")
                listed_after_payload = _json_payload(listed_after)
                active_count_after = int(listed_after_payload.get("count") or 0)
                checks.append(
                    {
                        "name": "active_session_count_after_revoke",
                        "value": active_count_after,
                        "expected": expected_after_revoke,
                    }
                )
                if active_count_after != expected_after_revoke:
                    errors.append(
                        f"active_session_count_after_revoke:{active_count_after}!={expected_after_revoke}"
                    )

                ent = client.get(
                    "/auth/providers/entitlements",
                    headers=_auth("user-token"),
                    params={"user_id": "user-1", "provider": "openai"},
                )
                checks.append({"name": "entitlements_status", "status": ent.status_code, "expected": 200})
                if ent.status_code != 200:
                    errors.append(f"entitlements_status:{ent.status_code}")
                ent_payload = _json_payload(ent)
                available = bool(ent_payload.get("available"))
                session_count = int(ent_payload.get("session_count") or 0)
                checks.append({"name": "entitlements_available_after_revoke", "value": available, "expected": False})
                checks.append({"name": "entitlements_session_count_after_revoke", "value": session_count, "expected": 0})
                if available:
                    errors.append("entitlements_available_after_revoke_should_be_false")
                if session_count != 0:
                    errors.append(f"entitlements_session_count_after_revoke:{session_count}")

                unauth = client.get("/auth/providers/sessions")
                checks.append({"name": "unauthenticated_request_blocked", "status": unauth.status_code, "expected": 401})
                if unauth.status_code != 401:
                    errors.append(f"unauthenticated_status:{unauth.status_code}")

            report = {
                "suite": "provider_session_policy_check_v1",
                "summary": {
                    "status": "pass" if not errors else "fail",
                    "checks_total": len(checks),
                    "checks_failed": len(errors),
                    "errors": errors,
                    "expected_session_count_after_revoke": expected_after_revoke,
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
        print(f"[provider-session-policy-check] report={output}")

    if errors:
        print("[provider-session-policy-check] FAILED")
        for item in errors:
            print(f"- {item}")
        return 1
    print("[provider-session-policy-check] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

