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
class DesktopActionRuntimeAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-desktop-action-api-")
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

    def test_desktop_action_tool_is_registered(self) -> None:
        response = self.client.get("/tools", headers=self._auth("user-token"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        items = payload.get("items", [])
        desktop = next((item for item in items if str(item.get("name")) == "desktop_action"), None)
        self.assertIsNotNone(desktop)
        assert desktop is not None
        self.assertEqual(str(desktop.get("risk_level")), "medium")
        self.assertEqual(str(desktop.get("approval_mode")), "conditional")

    def test_desktop_action_invoke_read_is_allowed(self) -> None:
        response = self.client.post(
            "/mcp/tools/desktop_action/invoke",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "session_id": "desktop-session-1",
                "arguments": {
                    "action": "clipboard_read",
                },
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        result = payload.get("result", {})
        self.assertEqual(str(result.get("tool")), "desktop_action")
        adapter_payload = result.get("result", {})
        status = str(adapter_payload.get("status"))
        self.assertIn(status, {"succeeded", "unavailable", "stubbed"})
        adapter_kind = str(adapter_payload.get("adapter", {}).get("kind"))
        self.assertIn(adapter_kind, {"linux", "macos", "stub"})

    def test_desktop_action_write_requires_permission_under_strict_enforcement(self) -> None:
        response = self.client.post(
            "/mcp/tools/desktop_action/invoke",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "session_id": "desktop-session-2",
                "arguments": {
                    "action": "clipboard_write",
                    "text": "hello",
                },
            },
        )
        self.assertEqual(response.status_code, 403)
        payload = response.json()
        self.assertEqual(str(payload.get("error", {}).get("type")), "permission_denied")

    def test_desktop_action_receipt_contains_rollback_hint(self) -> None:
        invoke = self.client.post(
            "/mcp/tools/desktop_action/invoke",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "session_id": "desktop-session-3",
                "arguments": {
                    "action": "clipboard_read",
                },
            },
        )
        self.assertEqual(invoke.status_code, 200)

        receipts = self.client.get(
            "/tools/actions/terminal",
            headers=self._auth("user-token"),
            params={"tool_name": "desktop_action", "session_id": "desktop-session-3", "limit": 20},
        )
        self.assertEqual(receipts.status_code, 200)
        items = receipts.json().get("items", [])
        self.assertTrue(items)
        first = items[0]
        self.assertEqual(str(first.get("tool_name")), "desktop_action")
        self.assertTrue(bool(str(first.get("rollback_hint") or "").strip()))


if __name__ == "__main__":
    unittest.main()
