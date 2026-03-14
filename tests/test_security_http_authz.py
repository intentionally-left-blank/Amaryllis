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
except Exception:  # pragma: no cover - dependency may be unavailable in base test env
    TestClient = None  # type: ignore[assignment]


@unittest.skipIf(TestClient is None, "fastapi dependency is not available")
class SecurityHTTPAuthzTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-http-security-")
        support_dir = Path(cls._tmp.name) / "support"
        auth_tokens = {
            "admin-token": {
                "user_id": "admin",
                "scopes": ["admin", "user"],
            },
            "user-token": {
                "user_id": "user-1",
                "scopes": ["user"],
            },
            "service-token": {
                "user_id": "svc-runtime",
                "scopes": ["service"],
            },
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

    def test_missing_token_returns_401_with_structured_error(self) -> None:
        response = self.client.get("/models")
        self.assertEqual(response.status_code, 401)
        payload = response.json()
        self.assertEqual(payload["error"]["type"], "authentication_error")
        self.assertTrue(str(payload["error"]["request_id"]).strip())

    def test_invalid_token_returns_401_with_structured_error(self) -> None:
        response = self.client.get("/models", headers=self._auth("invalid-token"))
        self.assertEqual(response.status_code, 401)
        payload = response.json()
        self.assertEqual(payload["error"]["type"], "authentication_error")
        self.assertTrue(str(payload["error"]["request_id"]).strip())

    def test_user_token_cannot_access_admin_endpoints(self) -> None:
        response = self.client.get("/security/identity", headers=self._auth("user-token"))
        self.assertEqual(response.status_code, 403)
        payload = response.json()
        self.assertEqual(payload["error"]["type"], "permission_denied")
        self.assertIn("Admin scope is required", payload["error"]["message"])

        response = self.client.get("/debug/models/failover", headers=self._auth("user-token"))
        self.assertEqual(response.status_code, 403)
        payload = response.json()
        self.assertEqual(payload["error"]["type"], "permission_denied")

    def test_service_scope_isolated_from_user_endpoints(self) -> None:
        response = self.client.get("/service/health", headers=self._auth("service-token"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["actor"], "svc-runtime")
        self.assertIn("service", payload["scopes"])

        response = self.client.get("/models", headers=self._auth("service-token"))
        self.assertEqual(response.status_code, 403)
        payload = response.json()
        self.assertEqual(payload["error"]["type"], "permission_denied")
        self.assertIn("User scope is required", payload["error"]["message"])

    def test_admin_can_rotate_identity(self) -> None:
        first = self.client.get("/security/identity", headers=self._auth("admin-token"))
        self.assertEqual(first.status_code, 200)
        first_key = str(first.json()["identity"]["key_id"])

        rotate = self.client.post(
            "/security/identity/rotate",
            headers=self._auth("admin-token"),
            json={"reason": "wave1-hardening"},
        )
        self.assertEqual(rotate.status_code, 200)
        rotate_payload = rotate.json()
        self.assertTrue(bool(rotate_payload["action_receipt"].get("signature")))
        self.assertEqual(
            str(rotate_payload["rotation"]["previous"]["key_id"]),
            first_key,
        )

        second = self.client.get("/security/identity", headers=self._auth("admin-token"))
        self.assertEqual(second.status_code, 200)
        second_key = str(second.json()["identity"]["key_id"])
        self.assertNotEqual(first_key, second_key)

    def test_authn_and_authz_denials_are_audited(self) -> None:
        self.client.get("/models")
        self.client.get("/security/identity", headers=self._auth("user-token"))

        response = self.client.get(
            "/security/audit",
            headers=self._auth("admin-token"),
            params={"status": "failed", "limit": 500},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        event_types = {str(item.get("event_type")) for item in payload["items"]}
        self.assertIn("authn_fail", event_types)
        self.assertIn("authz_deny", event_types)


if __name__ == "__main__":
    unittest.main()
