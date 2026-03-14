#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export Amaryllis security/compliance audit evidence.")
    parser.add_argument(
        "--output-name",
        default=None,
        help="Optional evidence filename (JSON).",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=90,
        help="Audit evidence time window in days.",
    )
    parser.add_argument(
        "--event-limit",
        type=int,
        default=2000,
        help="Maximum number of audit events to include.",
    )
    parser.add_argument(
        "--actor",
        default="security-ops-cli",
        help="Actor identifier used for signed action/audit trail.",
    )
    return parser.parse_args()


def main() -> int:
    root = _project_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    from runtime.compliance import ComplianceManager
    from runtime.config import AppConfig
    from runtime.security import LocalIdentityManager, SecurityManager
    from runtime.telemetry import LocalTelemetry
    from storage.database import Database

    args = _parse_args()
    config = AppConfig.from_env()
    config.ensure_directories()
    database = Database(config.database_path)
    telemetry = LocalTelemetry(config.telemetry_path)
    identity_manager = LocalIdentityManager(config.identity_path)
    security_manager = SecurityManager(
        identity_manager=identity_manager,
        database=database,
        telemetry=telemetry,
    )
    compliance_manager = ComplianceManager(
        config=config,
        database=database,
        security_manager=security_manager,
    )

    try:
        compliance_manager.sync_secret_inventory(actor=args.actor, request_id="security-evidence-sync")
        result = compliance_manager.export_evidence_bundle(
            actor=args.actor,
            request_id="security-evidence-export",
            output_name=args.output_name,
            window_days=max(1, int(args.window_days)),
            event_limit=max(100, int(args.event_limit)),
        )
    finally:
        database.close()

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
