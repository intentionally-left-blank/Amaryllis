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
class ProviderAuthAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-provider-auth-api-")
        support_dir = Path(cls._tmp.name) / "support"
        auth_tokens = {
            "admin-token": {"user_id": "admin", "scopes": ["admin", "user"]},
            "user-token": {"user_id": "user-1", "scopes": ["user"]},
            "user2-token": {"user_id": "user-2", "scopes": ["user"]},
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

    def test_provider_session_create_list_revoke_and_entitlements(self) -> None:
        contract = self.client.get("/auth/providers/contract", headers=self._auth("user-token"))
        self.assertEqual(contract.status_code, 200)
        providers = contract.json().get("providers", [])
        self.assertIn("openai", providers)
        self.assertIn("reddit", providers)

        created = self.client.post(
            "/auth/providers/sessions",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "provider": "openai",
                "credential_ref": "secret://vault/openai/user-1",
                "display_name": "Personal OpenAI",
                "scopes": ["chat", "news"],
                "metadata": {"source": "test"},
            },
        )
        self.assertEqual(created.status_code, 200)
        session = created.json().get("session", {})
        session_id = str(session.get("id") or "")
        self.assertTrue(session_id)
        self.assertEqual(str(session.get("user_id")), "user-1")
        self.assertEqual(str(session.get("provider")), "openai")
        self.assertEqual(str(session.get("status")), "active")

        listed = self.client.get(
            "/auth/providers/sessions",
            headers=self._auth("user-token"),
            params={"user_id": "user-1", "provider": "openai"},
        )
        self.assertEqual(listed.status_code, 200)
        self.assertGreaterEqual(int(listed.json().get("count", 0)), 1)

        ent_before = self.client.get(
            "/auth/providers/entitlements",
            headers=self._auth("user-token"),
            params={"user_id": "user-1", "provider": "openai"},
        )
        self.assertEqual(ent_before.status_code, 200)
        ent_payload = ent_before.json()
        self.assertTrue(bool(ent_payload.get("available")))
        self.assertIn(str(ent_payload.get("access_mode")), {"user_session", "server_api_key"})

        foreign_revoke = self.client.post(
            f"/auth/providers/sessions/{session_id}/revoke",
            headers=self._auth("user2-token"),
            json={"reason": "try to revoke foreign session"},
        )
        self.assertEqual(foreign_revoke.status_code, 403)
        self.assertEqual(str(foreign_revoke.json().get("error", {}).get("type")), "permission_denied")

        revoked = self.client.post(
            f"/auth/providers/sessions/{session_id}/revoke",
            headers=self._auth("user-token"),
            json={"reason": "rotated credential"},
        )
        self.assertEqual(revoked.status_code, 200)
        revoked_session = revoked.json().get("session", {})
        self.assertEqual(str(revoked_session.get("status")), "revoked")
        self.assertTrue(bool(revoked_session.get("revoked_at")))

    def test_admin_can_list_other_user_sessions(self) -> None:
        created = self.client.post(
            "/auth/providers/sessions",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "provider": "reddit",
                "credential_ref": "secret://vault/reddit/user-1",
            },
        )
        self.assertEqual(created.status_code, 200)

        listed = self.client.get(
            "/auth/providers/sessions",
            headers=self._auth("admin-token"),
            params={"user_id": "user-1", "provider": "reddit"},
        )
        self.assertEqual(listed.status_code, 200)
        items = listed.json().get("items", [])
        self.assertTrue(items)
        self.assertTrue(all(str(item.get("user_id")) == "user-1" for item in items))


if __name__ == "__main__":
    unittest.main()

