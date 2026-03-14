from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from runtime.backup import BackupManager
from runtime.telemetry import LocalTelemetry
from storage.database import Database
from storage.vector_store import VectorStore


class BackupManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-backup-")
        self.base = Path(self._tmp.name)
        self.data_dir = self.base / "data"
        self.backup_dir = self.base / "backups"
        self.db_path = self.data_dir / "amaryllis.db"
        self.vector_path = self.data_dir / "semantic.index"
        self.identity_path = self.data_dir / "identity.json"
        self.telemetry_path = self.data_dir / "telemetry.jsonl"

        self.database = Database(self.db_path)
        self.vector_store = VectorStore(self.vector_path)
        self.telemetry = LocalTelemetry(self.telemetry_path)

    def tearDown(self) -> None:
        self.database.close()
        self.vector_store.persist()
        self._tmp.cleanup()

    def _manager(
        self,
        *,
        retention_count: int = 120,
        retention_days: int = 30,
        verify_on_create: bool = True,
    ) -> BackupManager:
        return BackupManager(
            database=self.database,
            vector_store=self.vector_store,
            data_dir=self.data_dir,
            backup_dir=self.backup_dir,
            database_path=self.db_path,
            identity_path=self.identity_path,
            app_version="0.9.0-test",
            retention_count=retention_count,
            retention_days=retention_days,
            verify_on_create=verify_on_create,
            telemetry=self.telemetry,
        )

    def test_create_backup_with_verification(self) -> None:
        manager = self._manager()
        self.database.set_setting("runtime.mode", "test")
        self.database.add_episodic_event(
            user_id="user-1",
            agent_id="agent-1",
            role="user",
            content="hello",
            session_id="session-1",
        )
        self.vector_store.add_text("hello vector", {"user_id": "user-1"})

        created = manager.create_backup(trigger="unit-test", verify=True)
        self.assertTrue(bool(created.get("backup_id")))
        self.assertTrue(bool(created.get("archive_path")))
        self.assertTrue(bool(created.get("verification", {}).get("ok")))

        listed = manager.list_backups(limit=10)
        ids = {str(item.get("backup_id")) for item in listed}
        self.assertIn(str(created.get("backup_id")), ids)

        verified = manager.verify_backup(backup_id=str(created.get("backup_id")))
        self.assertTrue(bool(verified.get("ok")))

    def test_retention_keeps_only_recent_backups(self) -> None:
        manager = self._manager(retention_count=2, retention_days=365, verify_on_create=False)
        self.database.set_setting("retention.seed", "1")

        manager.create_backup(trigger="retention-1", verify=False)
        time.sleep(0.02)
        manager.create_backup(trigger="retention-2", verify=False)
        time.sleep(0.02)
        manager.create_backup(trigger="retention-3", verify=False)

        listed = manager.list_backups(limit=10)
        self.assertLessEqual(len(listed), 2)
        retention = manager.enforce_retention()
        self.assertEqual(int(retention.get("kept", 0)), len(manager.list_backups(limit=10)))

    def test_restore_backup_and_restore_drill(self) -> None:
        manager = self._manager()
        self.database.set_setting("restore.key", "restore-value")
        created = manager.create_backup(trigger="restore-test", verify=True)
        archive_path = Path(str(created.get("archive_path")))
        self.assertTrue(archive_path.exists())

        restored_dir = self.base / "restored-data"
        restored = manager.restore_backup(
            archive_path=archive_path,
            target_data_dir=restored_dir,
            preserve_existing=False,
        )
        self.assertTrue(bool(restored.get("ok")))
        restored_db = Database(restored_dir / self.db_path.name)
        try:
            self.assertEqual(restored_db.get_setting("restore.key"), "restore-value")
        finally:
            restored_db.close()

        drill = manager.run_restore_drill(backup_id=str(created.get("backup_id")))
        self.assertTrue(bool(drill.get("ok")))


if __name__ == "__main__":
    unittest.main()
