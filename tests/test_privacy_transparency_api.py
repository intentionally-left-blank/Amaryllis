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
except Exception:  # pragma: no cover - dependency may be unavailable in some environments
    TestClient = None  # type: ignore[assignment]


@unittest.skipIf(TestClient is None, "fastapi dependency is not available")
class PrivacyTransparencyAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-privacy-transparency-")
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
                "AMARYLLIS_OTEL_ENABLED": "false",
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

    def test_user_privacy_transparency_endpoint(self) -> None:
        response = self.client.get("/privacy/transparency", headers=self._auth("user-token"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(str(payload.get("contract_version")), "privacy_offline_transparency_v1")
        self.assertTrue(isinstance(payload.get("network_intents"), list))
        self.assertIn("offline", payload)
        telemetry = payload.get("telemetry", {})
        self.assertEqual(str(telemetry.get("mode")), "local_only")
        self.assertEqual(bool(telemetry.get("export_enabled")), False)
        self.assertTrue(str(payload.get("request_id", "")).strip())

    def test_versioned_user_privacy_transparency_endpoint(self) -> None:
        response = self.client.get("/v1/privacy/transparency", headers=self._auth("user-token"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("offline", payload)
        self.assertIn("network_intents", payload)

    def test_service_privacy_transparency_scope(self) -> None:
        denied = self.client.get("/service/privacy/transparency", headers=self._auth("user-token"))
        self.assertEqual(denied.status_code, 403)

        allowed = self.client.get("/service/privacy/transparency", headers=self._auth("service-token"))
        self.assertEqual(allowed.status_code, 200)
        payload = allowed.json()
        self.assertEqual(str(payload.get("actor")), "svc-runtime")
        self.assertIn("service", payload.get("scopes", []))
        self.assertIn("telemetry", payload)


if __name__ == "__main__":
    unittest.main()
