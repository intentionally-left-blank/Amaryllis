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
            "Validate offline/privacy transparency contract and declared network intents "
            "for the local runtime."
        )
    )
    parser.add_argument(
        "--min-local-providers",
        type=int,
        default=int(os.getenv("AMARYLLIS_OFFLINE_GATE_MIN_LOCAL_PROVIDERS", "1")),
        help="Minimum number of local providers expected in the contract.",
    )
    parser.add_argument(
        "--require-intent",
        action="append",
        default=[],
        help="Intent id that must exist in network_intents (repeatable).",
    )
    parser.add_argument(
        "--require-doc-path",
        action="append",
        default=["/docs/privacy-offline-transparency"],
        help="Policy doc path that must exist in policy_docs (repeatable).",
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


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    tmp_dir = tempfile.TemporaryDirectory(prefix="amaryllis-offline-transparency-gate-")
    support_dir = Path(tmp_dir.name) / "support"
    os.environ.setdefault("AMARYLLIS_AUTH_ENABLED", "true")
    os.environ.setdefault(
        "AMARYLLIS_AUTH_TOKENS",
        json.dumps(
            {
                "gate-user-token": {"user_id": "gate-user", "scopes": ["user"]},
                "gate-service-token": {"user_id": "gate-service", "scopes": ["service"]},
                "gate-admin-token": {"user_id": "gate-admin", "scopes": ["admin", "user"]},
            },
            ensure_ascii=False,
        ),
    )
    os.environ.setdefault("AMARYLLIS_SUPPORT_DIR", str(support_dir))
    os.environ.setdefault("AMARYLLIS_MEMORY_CONSOLIDATION_ENABLED", "false")
    os.environ.setdefault("AMARYLLIS_MCP_ENDPOINTS", "")
    os.environ.setdefault("AMARYLLIS_SECURITY_PROFILE", "production")
    os.environ.setdefault("AMARYLLIS_OTEL_ENABLED", "false")

    try:
        from fastapi.testclient import TestClient  # noqa: PLC0415
        from runtime.server import create_app  # noqa: PLC0415
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[offline-transparency-gate] FAILED import_error={exc}")
        tmp_dir.cleanup()
        return 2

    app = create_app()
    errors: list[str] = []
    report: dict[str, Any] = {}

    required_intents = [str(item).strip() for item in args.require_intent if str(item).strip()]
    if not required_intents:
        required_intents = [
            "chat.local_inference",
            "chat.cloud_inference",
            "tools.mcp_remote",
            "observability.otel_export",
        ]
    required_doc_paths = [str(item).strip() for item in args.require_doc_path if str(item).strip()]

    try:
        with TestClient(app) as client:
            response = client.get(
                "/service/privacy/transparency",
                headers=_auth("gate-service-token"),
            )
            if response.status_code != 200:
                errors.append(f"endpoint_status={response.status_code}")
            payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            if not isinstance(payload, dict):
                errors.append("payload_not_object")
                payload = {}

            contract_version = str(payload.get("contract_version") or "")
            if contract_version != "privacy_offline_transparency_v1":
                errors.append("contract_version_mismatch")

            telemetry_raw = payload.get("telemetry")
            telemetry = telemetry_raw if isinstance(telemetry_raw, dict) else {}
            if bool(telemetry.get("export_opt_in_default")) is not True:
                errors.append("export_opt_in_default_false")
            if bool(telemetry.get("export_enabled")) is True:
                errors.append("otel_export_not_default_off")

            offline_raw = payload.get("offline")
            offline = offline_raw if isinstance(offline_raw, dict) else {}
            local_providers_raw = offline.get("local_providers")
            local_providers = [
                str(item).strip()
                for item in local_providers_raw
                if isinstance(item, str) and str(item).strip()
            ] if isinstance(local_providers_raw, list) else []
            if len(local_providers) < max(0, int(args.min_local_providers)):
                errors.append("local_provider_count_below_min")

            intents_raw = payload.get("network_intents")
            intents = intents_raw if isinstance(intents_raw, list) else []
            intent_ids = {
                str(item.get("id")).strip()
                for item in intents
                if isinstance(item, dict) and str(item.get("id", "")).strip()
            }
            missing_intents = [item for item in required_intents if item not in intent_ids]
            if missing_intents:
                errors.append(f"missing_required_intents:{','.join(sorted(missing_intents))}")

            docs_raw = payload.get("policy_docs")
            docs = docs_raw if isinstance(docs_raw, list) else []
            doc_paths = {
                str(item.get("path")).strip()
                for item in docs
                if isinstance(item, dict) and str(item.get("path", "")).strip()
            }
            missing_docs = [item for item in required_doc_paths if item not in doc_paths]
            if missing_docs:
                errors.append(f"missing_required_docs:{','.join(sorted(missing_docs))}")

            report = {
                "generated_at": _utc_now_iso(),
                "suite": "offline_transparency_gate_v1",
                "summary": {
                    "status": "pass" if not errors else "fail",
                    "errors": errors,
                    "required_intents": required_intents,
                    "required_doc_paths": required_doc_paths,
                    "local_provider_count": len(local_providers),
                    "min_local_providers": max(0, int(args.min_local_providers)),
                },
                "contract_version": contract_version,
                "telemetry_mode": str(telemetry.get("mode") or ""),
                "offline": {
                    "offline_possible": bool(offline.get("offline_possible")),
                    "offline_ready_now": bool(offline.get("offline_ready_now")),
                    "network_required_now": bool(offline.get("network_required_now")),
                },
            }
    finally:
        _shutdown_app(app)
        tmp_dir.cleanup()

    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = repo_root / output_path
        _write_json(output_path, report)

    if errors:
        print("[offline-transparency-gate] FAILED")
        for err in errors:
            print(f"- {err}")
        return 1

    print(
        "[offline-transparency-gate] OK "
        f"local_providers={report.get('summary', {}).get('local_provider_count', 0)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
