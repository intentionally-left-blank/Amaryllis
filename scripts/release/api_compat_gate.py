#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Amaryllis API compatibility gate.")
    parser.add_argument(
        "--contract",
        default="contracts/api_compat_v1.json",
        help="Path to API compatibility contract JSON.",
    )
    parser.add_argument(
        "--print-openapi-paths",
        action="store_true",
        help="Print discovered OpenAPI paths for debugging.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    tmp_dir = tempfile.TemporaryDirectory(prefix="amaryllis-compat-gate-")
    support_dir = Path(tmp_dir.name) / "support"
    if not (os.getenv("AMARYLLIS_AUTH_TOKENS") or os.getenv("AMARYLLIS_API_TOKEN")):
        os.environ["AMARYLLIS_AUTH_TOKENS"] = json.dumps(
            {
                "gate-user-token": {"user_id": "gate-user", "scopes": ["user"]},
                "gate-admin-token": {"user_id": "gate-admin", "scopes": ["admin", "user"]},
                "gate-service-token": {"user_id": "gate-service", "scopes": ["service"]},
            },
            ensure_ascii=False,
        )
    os.environ.setdefault("AMARYLLIS_AUTH_ENABLED", "true")
    os.environ.setdefault("AMARYLLIS_SUPPORT_DIR", str(support_dir))
    os.environ.setdefault("AMARYLLIS_MEMORY_CONSOLIDATION_ENABLED", "false")
    os.environ.setdefault("AMARYLLIS_MCP_ENDPOINTS", "")
    os.environ.setdefault("AMARYLLIS_SECURITY_PROFILE", "production")

    from runtime.api_compat import load_contract, validate_openapi_contract  # noqa: PLC0415
    from runtime.server import create_app  # noqa: PLC0415

    app = create_app()
    schema = app.openapi()

    if args.print_openapi_paths:
        print(json.dumps(sorted((schema.get("paths") or {}).keys()), indent=2))

    contract_path = Path(args.contract)
    if not contract_path.is_absolute():
        contract_path = project_root / contract_path
    if not contract_path.exists():
        print(f"[compat-gate] contract not found: {contract_path}")
        return 2

    contract = load_contract(contract_path)
    errors = validate_openapi_contract(openapi=schema, contract=contract)
    _shutdown_app(app)
    tmp_dir.cleanup()
    if errors:
        print("[compat-gate] FAILED")
        for item in errors:
            print(f"- {item}")
        return 1

    version = str(contract.get("contract_version") or "unknown")
    count = len(contract.get("endpoints", [])) if isinstance(contract.get("endpoints"), list) else 0
    print(f"[compat-gate] OK version={version} endpoints={count}")
    return 0


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


if __name__ == "__main__":
    raise SystemExit(main())
