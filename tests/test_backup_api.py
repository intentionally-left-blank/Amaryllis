from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover - dependency may be unavailable
    TestClient = None  # type: ignore[assignment]


@unittest.skipIf(TestClient is None, "fastapi dependency is not available")
class BackupAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-backup-api-")
        support_dir = Path(cls._tmp.name) / "support"
        auth_tokens = {
            "admin-token": {"user_id": "admin", "scopes": ["admin", "user"]},
            "user-token": {"user_id": "user-1", "scopes": ["user"]},
            "service-token": {"user_id": "svc-runtime", "scopes": ["service"]},
        }
        cls._env_patch = patch.dict(
            os.environ,
            {
                "AMARYLLIS_SUPPORT_DIR": str(support_dir),
                "AMARYLLIS_AUTH_ENABLED": "true",
                "AMARYLLIS_AUTH_TOKENS": json.dumps(auth_tokens, ensure_ascii=False),
                "AMARYLLIS_MEMORY_CONSOLIDATION_ENABLED": "false",
                "AMARYLLIS_MCP_ENDPOINTS": "",
                "AMARYLLIS_SECURITY_PROFILE": "production",
                "AMARYLLIS_BACKUP_ENABLED": "true",
                "AMARYLLIS_BACKUP_INTERVAL_SEC": "36000",
                "AMARYLLIS_BACKUP_RESTORE_DRILL_ENABLED": "false",
            },
            clear=False,
        )
        cls._env_patch.start()

        import runtime.server as server_module

        cls.server_module = importlib.reload(server_module)
        cls._client_cm = TestClient(cls.server_module.app)
        cls.client = cls._client_cm.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._client_cm.__exit__(None, None, None)
        cls._env_patch.stop()
        cls._tmp.cleanup()

    @staticmethod
    def _auth(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def test_backup_service_scope_required(self) -> None:
        denied = self.client.get("/service/backup/status", headers=self._auth("user-token"))
        self.assertEqual(denied.status_code, 403)

        allowed = self.client.get("/service/backup/status", headers=self._auth("service-token"))
        self.assertEqual(allowed.status_code, 200)
        payload = allowed.json()
        self.assertTrue(bool(payload.get("enabled")))
        self.assertIn("scheduler", payload)

    def test_backup_run_verify_and_restore_drill(self) -> None:
        created = self.client.post(
            "/service/backup/run",
            headers=self._auth("service-token"),
            json={"trigger": "api-test", "verify": True},
        )
        self.assertEqual(created.status_code, 200)
        created_payload = created.json()
        backup_id = str(created_payload.get("backup_id"))
        self.assertTrue(backup_id)
        self.assertTrue(bool(created_payload.get("verification", {}).get("ok")))

        verified = self.client.post(
            "/service/backup/verify",
            headers=self._auth("service-token"),
            json={"backup_id": backup_id},
        )
        self.assertEqual(verified.status_code, 200)
        verify_payload = verified.json()
        self.assertTrue(bool(verify_payload.get("ok")))

        drill = self.client.post(
            "/service/backup/restore-drill",
            headers=self._auth("service-token"),
            json={"backup_id": backup_id},
        )
        self.assertEqual(drill.status_code, 200)
        drill_payload = drill.json()
        self.assertTrue(bool(drill_payload.get("ok")))


if __name__ == "__main__":
    unittest.main()
