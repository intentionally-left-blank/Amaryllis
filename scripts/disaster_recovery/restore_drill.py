#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Amaryllis disaster-recovery restore drill.")
    parser.add_argument(
        "--backup-id",
        default=None,
        help="Optional backup id. If omitted, latest backup is used.",
    )
    return parser.parse_args()


def main() -> int:
    project_root = _project_root()
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    from runtime.backup import BackupManager
    from runtime.config import AppConfig
    from runtime.telemetry import LocalTelemetry
    from storage.database import Database
    from storage.vector_store import VectorStore

    args = _parse_args()
    config = AppConfig.from_env()
    config.ensure_directories()
    with tempfile.TemporaryDirectory(prefix="amaryllis-dr-helper-") as helper:
        helper_root = Path(helper)
        database = Database(helper_root / "helper.db")
        vector_store = VectorStore(helper_root / "helper.index")
        telemetry = LocalTelemetry(config.telemetry_path)
        manager = BackupManager(
            database=database,
            vector_store=vector_store,
            data_dir=config.data_dir,
            backup_dir=config.backup_dir,
            database_path=config.database_path,
            identity_path=config.identity_path,
            app_version=config.app_version,
            retention_count=config.backup_retention_count,
            retention_days=config.backup_retention_days,
            verify_on_create=config.backup_verify_on_create,
            telemetry=telemetry,
        )
        try:
            result = manager.run_restore_drill(backup_id=args.backup_id)
        finally:
            database.close()
            vector_store.persist()

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if bool(result.get("ok", False)) else 2


if __name__ == "__main__":
    raise SystemExit(main())
