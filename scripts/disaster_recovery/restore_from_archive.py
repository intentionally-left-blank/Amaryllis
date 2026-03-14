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
    parser = argparse.ArgumentParser(description="Restore Amaryllis data directory from backup archive.")
    parser.add_argument(
        "--archive",
        required=True,
        help="Absolute or relative path to .tar.gz backup archive.",
    )
    parser.add_argument(
        "--target-data-dir",
        default=None,
        help="Optional data dir override. Default: AMARYLLIS_DATA_DIR.",
    )
    parser.add_argument(
        "--preserve-existing",
        default="true",
        choices=("true", "false"),
        help="Move previous data dir to rollback folder before restore.",
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
    target_data_dir = Path(args.target_data_dir).expanduser() if args.target_data_dir else config.data_dir
    preserve = args.preserve_existing.strip().lower() == "true"

    with tempfile.TemporaryDirectory(prefix="amaryllis-restore-helper-") as helper:
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
            verify = manager.verify_backup(archive_path=Path(args.archive).expanduser())
            if not bool(verify.get("ok", False)):
                print(
                    json.dumps(
                        {"ok": False, "error": "verify_failed", "verify": verify},
                        ensure_ascii=False,
                        indent=2,
                    )
                )
                return 2
            restored = manager.restore_backup(
                archive_path=Path(args.archive).expanduser(),
                target_data_dir=target_data_dir,
                preserve_existing=preserve,
            )
        finally:
            database.close()
            vector_store.persist()

    result = {
        "ok": True,
        "verify": verify,
        "restore": restored,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
