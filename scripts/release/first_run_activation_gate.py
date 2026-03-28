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
            "Validate first-run onboarding and model package catalog contract "
            "(docs + runtime API flow for profile -> activation-plan -> catalog -> activation)."
        )
    )
    parser.add_argument(
        "--onboarding-doc",
        default="docs/model-onboarding-profiles.md",
        help="Path to onboarding profile documentation.",
    )
    parser.add_argument(
        "--catalog-doc",
        default="docs/model-package-catalog.md",
        help="Path to model package catalog documentation.",
    )
    parser.add_argument(
        "--token",
        default="dev-token",
        help="Auth token used for runtime checks.",
    )
    parser.add_argument(
        "--profile",
        default="balanced",
        help="Onboarding profile for runtime checks (fast|balanced|quality).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Package catalog limit for runtime checks.",
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

    onboarding_doc = _resolve_path(repo_root, str(args.onboarding_doc))
    catalog_doc = _resolve_path(repo_root, str(args.catalog_doc))
    token = str(args.token).strip() or "dev-token"
    profile = str(args.profile).strip().lower() or "balanced"
    if profile not in {"fast", "balanced", "quality"}:
        print("[first-run-activation-gate] invalid --profile, expected fast|balanced|quality", file=sys.stderr)
        return 2
    limit = max(1, int(args.limit))

    checks: list[dict[str, Any]] = []

    def add_check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    if onboarding_doc.exists():
        text = onboarding_doc.read_text(encoding="utf-8")
        add_check("onboarding_doc_exists", True, str(onboarding_doc))
        add_check(
            "onboarding_doc_profile_endpoint",
            "GET /models/onboarding/profile" in text,
            "docs must include onboarding profile endpoint",
        )
        add_check(
            "onboarding_doc_activation_plan_endpoint",
            "GET /models/onboarding/activation-plan" in text,
            "docs must include onboarding activation-plan endpoint",
        )
        add_check(
            "onboarding_doc_activate_endpoint",
            "POST /models/onboarding/activate" in text,
            "docs must include onboarding activate endpoint",
        )
    else:
        add_check("onboarding_doc_exists", False, f"missing: {onboarding_doc}")

    if catalog_doc.exists():
        text = catalog_doc.read_text(encoding="utf-8")
        add_check("catalog_doc_exists", True, str(catalog_doc))
        add_check(
            "catalog_doc_packages_endpoint",
            "GET /models/packages" in text,
            "docs must include model package list endpoint",
        )
        add_check(
            "catalog_doc_install_endpoint",
            "POST /models/packages/install" in text,
            "docs must include package install endpoint",
        )
        add_check(
            "catalog_doc_license_admission_endpoint",
            "GET /models/packages/license-admission" in text,
            "docs must include package license-admission endpoint",
        )
    else:
        add_check("catalog_doc_exists", False, f"missing: {catalog_doc}")

    tmp_dir = tempfile.TemporaryDirectory(prefix="amaryllis-first-run-gate-")
    support_dir = Path(tmp_dir.name) / "support"

    os.environ["AMARYLLIS_AUTH_ENABLED"] = "true"
    os.environ["AMARYLLIS_AUTH_TOKENS"] = json.dumps(
        {
            token: {"user_id": "first-run-user", "scopes": ["user"]},
            "first-run-admin-token": {"user_id": "first-run-admin", "scopes": ["admin", "user"]},
            "first-run-service-token": {"user_id": "first-run-service", "scopes": ["service"]},
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

    app = None
    try:
        from fastapi.testclient import TestClient  # noqa: PLC0415
        from runtime.server import create_app  # noqa: PLC0415

        app = create_app()
        with TestClient(app) as client:
            onboarding_profile_resp = client.get("/models/onboarding/profile", headers=_auth(token))
            add_check(
                "runtime_onboarding_profile_ok",
                onboarding_profile_resp.status_code == 200,
                f"status={onboarding_profile_resp.status_code}",
            )
            onboarding_profile_payload = (
                onboarding_profile_resp.json() if _is_json_response(dict(onboarding_profile_resp.headers)) else {}
            )
            recommended_profile = str(
                onboarding_profile_payload.get("recommended_profile")
                if isinstance(onboarding_profile_payload, dict)
                else ""
            ).strip()
            add_check(
                "runtime_onboarding_profile_recommended_profile_valid",
                recommended_profile in {"fast", "balanced", "quality"},
                f"recommended_profile={recommended_profile}",
            )
            profiles = (
                onboarding_profile_payload.get("profiles")
                if isinstance(onboarding_profile_payload, dict)
                else {}
            )
            profile_keys = sorted(str(item) for item in (profiles.keys() if isinstance(profiles, dict) else []))
            add_check(
                "runtime_onboarding_profile_profiles_map",
                profile_keys == ["balanced", "fast", "quality"],
                f"profile_keys={profile_keys}",
            )

            activation_plan_resp = client.get(
                f"/models/onboarding/activation-plan?profile={profile}&include_remote_providers=true&limit={limit}&require_metadata=false",
                headers=_auth(token),
            )
            add_check(
                "runtime_onboarding_activation_plan_ok",
                activation_plan_resp.status_code == 200,
                f"status={activation_plan_resp.status_code}",
            )
            activation_plan_payload = (
                activation_plan_resp.json() if _is_json_response(dict(activation_plan_resp.headers)) else {}
            )
            plan_version = str(
                activation_plan_payload.get("plan_version")
                if isinstance(activation_plan_payload, dict)
                else ""
            ).strip()
            selected_package_id = str(
                activation_plan_payload.get("selected_package_id")
                if isinstance(activation_plan_payload, dict)
                else ""
            ).strip()
            add_check(
                "runtime_onboarding_activation_plan_version",
                plan_version == "onboarding_activation_plan_v1",
                f"plan_version={plan_version}",
            )
            add_check(
                "runtime_onboarding_activation_plan_selected_package",
                bool(selected_package_id),
                f"selected_package_id={selected_package_id}",
            )
            ready_to_install = bool(
                activation_plan_payload.get("ready_to_install")
                if isinstance(activation_plan_payload, dict)
                else False
            )
            add_check(
                "runtime_onboarding_activation_plan_ready_flag_present",
                isinstance(
                    activation_plan_payload.get("ready_to_install")
                    if isinstance(activation_plan_payload, dict)
                    else None,
                    bool,
                ),
                f"ready_to_install={ready_to_install}",
            )

            catalog_resp = client.get(
                f"/models/packages?profile={profile}&include_remote_providers=true&limit={limit}",
                headers=_auth(token),
            )
            add_check(
                "runtime_model_package_catalog_ok",
                catalog_resp.status_code == 200,
                f"status={catalog_resp.status_code}",
            )
            catalog_payload = catalog_resp.json() if _is_json_response(dict(catalog_resp.headers)) else {}
            catalog_version = str(catalog_payload.get("catalog_version") if isinstance(catalog_payload, dict) else "").strip()
            packages = catalog_payload.get("packages") if isinstance(catalog_payload, dict) else []
            package_count = len(packages) if isinstance(packages, list) else 0
            add_check(
                "runtime_model_package_catalog_version",
                catalog_version == "model_package_catalog_v1",
                f"catalog_version={catalog_version}",
            )
            add_check(
                "runtime_model_package_catalog_non_empty",
                package_count > 0,
                f"package_count={package_count}",
            )
            install_endpoint = ""
            if isinstance(packages, list) and packages and isinstance(packages[0], dict):
                install = packages[0].get("install")
                if isinstance(install, dict):
                    install_endpoint = str(install.get("endpoint") or "")
            add_check(
                "runtime_model_package_catalog_install_endpoint",
                install_endpoint == "/models/packages/install",
                f"install_endpoint={install_endpoint}",
            )

            if selected_package_id:
                admission_resp = client.get(
                    f"/models/packages/license-admission?package_id={selected_package_id}&require_metadata=false",
                    headers=_auth(token),
                )
                add_check(
                    "runtime_model_package_license_admission_ok",
                    admission_resp.status_code == 200,
                    f"status={admission_resp.status_code}",
                )
                admission_payload = (
                    admission_resp.json() if _is_json_response(dict(admission_resp.headers)) else {}
                )
                admission_status = str(
                    admission_payload.get("status") if isinstance(admission_payload, dict) else ""
                ).strip()
                add_check(
                    "runtime_model_package_license_admission_status_valid",
                    admission_status in {"allow", "allow_with_warning", "deny"},
                    f"status={admission_status}",
                )

            activate_resp = client.post(
                "/models/onboarding/activate",
                headers=_auth(token),
                json={
                    "profile": profile,
                    "include_remote_providers": True,
                    "limit": limit,
                    "require_metadata": False,
                    "activate": True,
                    "run_smoke_test": True,
                    "smoke_prompt": "first-run-gate",
                },
            )
            add_check(
                "runtime_onboarding_activate_ok",
                activate_resp.status_code == 200,
                f"status={activate_resp.status_code}",
            )
            activate_payload = activate_resp.json() if _is_json_response(dict(activate_resp.headers)) else {}
            activation_version = str(
                activate_payload.get("activation_version") if isinstance(activate_payload, dict) else ""
            ).strip()
            activation_status = str(
                activate_payload.get("status") if isinstance(activate_payload, dict) else ""
            ).strip()
            add_check(
                "runtime_onboarding_activate_version",
                activation_version == "onboarding_activate_v1",
                f"activation_version={activation_version}",
            )
            add_check(
                "runtime_onboarding_activate_status_valid",
                activation_status in {"activated", "activated_with_smoke_warning", "blocked"},
                f"status={activation_status}",
            )

    except Exception as exc:
        add_check("first_run_runtime_check_error", False, str(exc))
    finally:
        if app is not None:
            _shutdown_app(app)
        tmp_dir.cleanup()

    failed = [item for item in checks if not bool(item.get("ok"))]
    report = {
        "generated_at": _utc_now_iso(),
        "suite": "first_run_activation_gate_v1",
        "summary": {
            "status": "pass" if not failed else "fail",
            "checks_total": len(checks),
            "checks_failed": len(failed),
        },
        "checks": checks,
    }

    output_raw = str(args.output or "").strip()
    if output_raw:
        output_path = _resolve_path(repo_root, output_raw)
        _write_json(output_path, report)

    if failed:
        print("[first-run-activation-gate] FAILED")
        for item in failed:
            print(f"- {item.get('name')}: {item.get('detail')}")
        return 1

    print(f"[first-run-activation-gate] OK checks={len(checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
