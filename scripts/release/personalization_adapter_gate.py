#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import hmac
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
            "Validate personalization adapter registry contract: signed adapter registration, "
            "activation, rollback, and signature rejection semantics."
        )
    )
    parser.add_argument(
        "--min-registered-adapters",
        type=int,
        default=int(os.getenv("AMARYLLIS_PERSONALIZATION_GATE_MIN_REGISTERED", "2")),
        help="Minimum required registered adapters in scope after registration flow.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional JSON report output path.",
    )
    return parser.parse_args()


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


def _sign_adapter(
    *,
    signing_key: str,
    key_id: str,
    user_id: str,
    adapter_id: str,
    base_package_id: str,
    artifact_sha256: str,
    recipe_id: str,
    metadata: dict[str, Any],
) -> dict[str, str]:
    unsigned_payload = {
        "adapter_id": adapter_id,
        "artifact_sha256": artifact_sha256,
        "base_package_id": base_package_id,
        "metadata": metadata,
        "recipe_id": recipe_id,
        "user_id": user_id,
    }
    canonical = json.dumps(unsigned_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    signature = hmac.new(signing_key.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "algorithm": "hmac-sha256",
        "key_id": key_id,
        "value": signature,
        "trust_level": "managed",
    }


def _post_json(
    client: Any,
    *,
    path: str,
    headers: dict[str, str],
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    response = client.post(path, headers=headers, json=payload)
    try:
        body = response.json()
    except Exception:
        body = {}
    return int(response.status_code), (body if isinstance(body, dict) else {})


def _get_json(
    client: Any,
    *,
    path: str,
    headers: dict[str, str],
    params: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    response = client.get(path, headers=headers, params=params)
    try:
        body = response.json()
    except Exception:
        body = {}
    return int(response.status_code), (body if isinstance(body, dict) else {})


def main() -> int:
    args = _parse_args()
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    min_registered_adapters = max(1, int(args.min_registered_adapters))

    try:
        from fastapi.testclient import TestClient  # noqa: PLC0415
    except Exception as exc:
        print(f"[personalization-adapter-gate] FAILED import_error={exc}")
        return 2

    errors: list[str] = []
    checks: list[dict[str, Any]] = []
    app: Any | None = None
    report: dict[str, Any] = {}

    with tempfile.TemporaryDirectory(prefix="amaryllis-personalization-gate-") as tmp:
        support_dir = Path(tmp) / "support"

        signing_key = str(os.getenv("AMARYLLIS_ADAPTER_SIGNING_KEY", "personalization-gate-key")).strip()
        key_id = str(os.getenv("AMARYLLIS_ADAPTER_KEY_ID", "personalization-gate-kid")).strip()

        auth_tokens = {
            "admin-token": {"user_id": "admin", "scopes": ["admin", "user"]},
        }
        os.environ["AMARYLLIS_SUPPORT_DIR"] = str(support_dir)
        os.environ["AMARYLLIS_AUTH_ENABLED"] = "true"
        os.environ["AMARYLLIS_AUTH_TOKENS"] = json.dumps(auth_tokens, ensure_ascii=False)
        os.environ["AMARYLLIS_MEMORY_CONSOLIDATION_ENABLED"] = "false"
        os.environ["AMARYLLIS_MCP_ENDPOINTS"] = ""
        os.environ["AMARYLLIS_SECURITY_PROFILE"] = "production"
        os.environ["AMARYLLIS_ADAPTER_SIGNING_KEY"] = signing_key
        os.environ["AMARYLLIS_ADAPTER_KEY_ID"] = key_id

        try:
            import runtime.server as server_module  # noqa: PLC0415

            server_module = importlib.reload(server_module)
            app = server_module.app
        except Exception as exc:
            print(f"[personalization-adapter-gate] FAILED import_or_boot_error={exc}")
            return 2

        try:
            with TestClient(app) as client:
                user_id = "admin"
                base_package_id = "mlx::personalization-gate-base"

                # Register v1 and activate.
                v1_signature = _sign_adapter(
                    signing_key=signing_key,
                    key_id=key_id,
                    user_id=user_id,
                    adapter_id="gate-adapter-v1",
                    base_package_id=base_package_id,
                    artifact_sha256="a" * 64,
                    recipe_id="gate-recipe-v1",
                    metadata={"tier": "v1"},
                )
                register_v1_status, register_v1 = _post_json(
                    client,
                    path="/models/personalization/adapters/register",
                    headers=_auth("admin-token"),
                    payload={
                        "user_id": user_id,
                        "adapter_id": "gate-adapter-v1",
                        "base_package_id": base_package_id,
                        "artifact_sha256": "a" * 64,
                        "recipe_id": "gate-recipe-v1",
                        "metadata": {"tier": "v1"},
                        "signature": v1_signature,
                        "activate": True,
                    },
                )
                checks.append({"name": "register_v1_status", "status": register_v1_status, "expected": 200})
                if register_v1_status != 200:
                    errors.append(f"register_v1_status:{register_v1_status}")

                # Register v2 and activate.
                v2_signature = _sign_adapter(
                    signing_key=signing_key,
                    key_id=key_id,
                    user_id=user_id,
                    adapter_id="gate-adapter-v2",
                    base_package_id=base_package_id,
                    artifact_sha256="b" * 64,
                    recipe_id="gate-recipe-v2",
                    metadata={"tier": "v2"},
                )
                register_v2_status, register_v2 = _post_json(
                    client,
                    path="/models/personalization/adapters/register",
                    headers=_auth("admin-token"),
                    payload={
                        "user_id": user_id,
                        "adapter_id": "gate-adapter-v2",
                        "base_package_id": base_package_id,
                        "artifact_sha256": "b" * 64,
                        "recipe_id": "gate-recipe-v2",
                        "metadata": {"tier": "v2"},
                        "signature": v2_signature,
                        "activate": True,
                    },
                )
                checks.append({"name": "register_v2_status", "status": register_v2_status, "expected": 200})
                if register_v2_status != 200:
                    errors.append(f"register_v2_status:{register_v2_status}")

                # List current stack.
                list_status, listed = _get_json(
                    client,
                    path="/models/personalization/adapters",
                    headers=_auth("admin-token"),
                    params={"user_id": user_id, "base_package_id": base_package_id},
                )
                checks.append({"name": "list_status", "status": list_status, "expected": 200})
                if list_status != 200:
                    errors.append(f"list_status:{list_status}")
                listed_count = int(listed.get("count") or 0)
                checks.append(
                    {
                        "name": "list_count_min",
                        "value": listed_count,
                        "min": min_registered_adapters,
                    }
                )
                if listed_count < min_registered_adapters:
                    errors.append(f"list_count_below_min:{listed_count}<{min_registered_adapters}")
                active_before = str((listed.get("active_by_base_package") or {}).get(base_package_id) or "")
                checks.append({"name": "active_before_rollback", "value": active_before})
                if active_before != "gate-adapter-v2":
                    errors.append(f"active_before_rollback_unexpected:{active_before}")

                # Rollback should return v1 as active.
                rollback_status, rollback = _post_json(
                    client,
                    path="/models/personalization/adapters/rollback",
                    headers=_auth("admin-token"),
                    payload={"user_id": user_id, "base_package_id": base_package_id},
                )
                checks.append({"name": "rollback_status", "status": rollback_status, "expected": 200})
                if rollback_status != 200:
                    errors.append(f"rollback_status:{rollback_status}")
                active_after = str((rollback.get("active_adapter") or {}).get("adapter_id") or "")
                checks.append({"name": "active_after_rollback", "value": active_after})
                if active_after != "gate-adapter-v1":
                    errors.append(f"active_after_rollback_unexpected:{active_after}")

                # Bad signature must be rejected.
                bad_signature_status, _ = _post_json(
                    client,
                    path="/models/personalization/adapters/register",
                    headers=_auth("admin-token"),
                    payload={
                        "user_id": user_id,
                        "adapter_id": "gate-adapter-invalid-signature",
                        "base_package_id": base_package_id,
                        "artifact_sha256": "c" * 64,
                        "recipe_id": "gate-recipe-invalid",
                        "metadata": {"tier": "invalid"},
                        "signature": {
                            "algorithm": "hmac-sha256",
                            "key_id": key_id,
                            "value": "0" * 64,
                            "trust_level": "managed",
                        },
                        "activate": False,
                    },
                )
                checks.append(
                    {
                        "name": "bad_signature_rejected_status",
                        "status": bad_signature_status,
                        "expected": 400,
                    }
                )
                if bad_signature_status != 400:
                    errors.append(f"bad_signature_status:{bad_signature_status}")

            report = {
                "suite": "personalization_adapter_gate_v1",
                "summary": {
                    "status": "pass" if not errors else "fail",
                    "checks_total": len(checks),
                    "checks_failed": len(errors),
                    "errors": errors,
                    "min_registered_adapters": min_registered_adapters,
                },
                "checks": checks,
                "register_v1": register_v1,
                "register_v2": register_v2,
                "list": listed,
                "rollback": rollback,
            }
        finally:
            if app is not None:
                _shutdown_app(app)

    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = project_root / output_path
        _write_json(output_path, report)
        print(f"[personalization-adapter-gate] report={output_path}")

    if errors:
        print("[personalization-adapter-gate] FAILED")
        for item in errors:
            print(f"- {item}")
        return 1

    print("[personalization-adapter-gate] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
