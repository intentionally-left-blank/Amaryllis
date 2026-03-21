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
except Exception:  # pragma: no cover
    TestClient = None  # type: ignore[assignment]


@unittest.skipIf(TestClient is None, "fastapi dependency is not available")
class FlowAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-flow-api-")
        support_dir = Path(cls._tmp.name) / "support"
        auth_tokens = {
            "admin-token": {"user_id": "admin", "scopes": ["admin", "user"]},
            "user-token": {"user_id": "user-1", "scopes": ["user"]},
            "user2-token": {"user_id": "user-2", "scopes": ["user"]},
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

    def test_contract_endpoint(self) -> None:
        response = self.client.get("/flow/sessions/contract", headers=self._auth("user-token"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("planning", payload.get("states", []))
        self.assertIn("voice", payload.get("channels", []))

    def test_owner_scope_and_transition_flow(self) -> None:
        started = self.client.post(
            "/flow/sessions/start",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "channels": ["text", "voice"],
                "initial_state": "listening",
                "metadata": {"source": "api-test"},
            },
        )
        self.assertEqual(started.status_code, 200)
        session = started.json().get("flow_session", {})
        session_id = str(session.get("id"))
        self.assertTrue(session_id.startswith("flow-"))
        self.assertEqual(str(session.get("state")), "listening")

        foreign_get = self.client.get(
            f"/flow/sessions/{session_id}",
            headers=self._auth("user2-token"),
        )
        self.assertEqual(foreign_get.status_code, 403)
        self.assertEqual(foreign_get.json()["error"]["type"], "permission_denied")

        transition = self.client.post(
            f"/flow/sessions/{session_id}/transition",
            headers=self._auth("user-token"),
            json={"to_state": "planning", "reason": "plan_requested"},
        )
        self.assertEqual(transition.status_code, 200)
        self.assertEqual(str(transition.json().get("flow_session", {}).get("state")), "planning")

        activity = self.client.post(
            f"/flow/sessions/{session_id}/activity",
            headers=self._auth("user-token"),
            json={"channel": "text", "event": "prompt_submitted"},
        )
        self.assertEqual(activity.status_code, 200)
        text_activity = (
            activity.json()
            .get("flow_session", {})
            .get("channel_activity", {})
            .get("text", {})
        )
        self.assertEqual(int(text_activity.get("events_count", 0)), 1)

        invalid_transition = self.client.post(
            f"/flow/sessions/{session_id}/transition",
            headers=self._auth("user-token"),
            json={"to_state": "created", "reason": "should-fail"},
        )
        self.assertEqual(invalid_transition.status_code, 400)
        self.assertEqual(invalid_transition.json()["error"]["type"], "validation_error")

    def test_listing_is_user_scoped(self) -> None:
        owner_start = self.client.post(
            "/flow/sessions/start",
            headers=self._auth("user-token"),
            json={"user_id": "user-1", "channels": ["text"]},
        )
        self.assertEqual(owner_start.status_code, 200)

        other_start = self.client.post(
            "/flow/sessions/start",
            headers=self._auth("user2-token"),
            json={"user_id": "user-2", "channels": ["text", "visual"]},
        )
        self.assertEqual(other_start.status_code, 200)

        owner_list = self.client.get("/flow/sessions", headers=self._auth("user-token"))
        self.assertEqual(owner_list.status_code, 200)
        owner_items = owner_list.json().get("items", [])
        self.assertTrue(owner_items)
        self.assertTrue(all(str(item.get("user_id")) == "user-1" for item in owner_items))

        admin_list_user2 = self.client.get(
            "/flow/sessions",
            headers=self._auth("admin-token"),
            params={"user_id": "user-2"},
        )
        self.assertEqual(admin_list_user2.status_code, 200)
        admin_items = admin_list_user2.json().get("items", [])
        self.assertTrue(admin_items)
        self.assertTrue(all(str(item.get("user_id")) == "user-2" for item in admin_items))

        service_denied = self.client.get("/flow/sessions", headers=self._auth("service-token"))
        self.assertEqual(service_denied.status_code, 403)
        self.assertEqual(service_denied.json()["error"]["type"], "permission_denied")


if __name__ == "__main__":
    unittest.main()
