from __future__ import annotations

import importlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover - dependency may be unavailable
    TestClient = None  # type: ignore[assignment]


@unittest.skipIf(TestClient is None, "fastapi dependency is not available")
class AgentRunExplainAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-run-explain-api-")
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

    def _wait_run_terminal(
        self,
        *,
        run_id: str,
        token: str,
        timeout_sec: float = 8.0,
    ) -> dict[str, object]:
        deadline = time.time() + timeout_sec
        last_payload: dict[str, object] = {}
        while time.time() < deadline:
            response = self.client.get(
                f"/agents/runs/{run_id}",
                headers=self._auth(token),
            )
            if response.status_code == 200:
                payload = response.json().get("run", {})
                if isinstance(payload, dict):
                    last_payload = payload
                status = str(last_payload.get("status") or "")
                if status in {"succeeded", "failed", "canceled"}:
                    return last_payload
            time.sleep(0.05)
        self.fail(f"Run {run_id} did not reach terminal status, last_payload={last_payload}")

    def _create_agent_and_run(self) -> tuple[str, str]:
        created = self.client.post(
            "/agents/create",
            headers=self._auth("user-token"),
            json={
                "name": "Explain Agent",
                "system_prompt": "explain",
                "user_id": "user-1",
                "tools": ["web_search"],
            },
        )
        self.assertEqual(created.status_code, 200)
        agent_id = str(created.json().get("id") or "")

        run_created = self.client.post(
            f"/agents/{agent_id}/runs",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "session_id": "explain-session-1",
                "message": "Collect context and summarize.",
                "max_attempts": 1,
            },
        )
        self.assertEqual(run_created.status_code, 200)
        run_id = str(run_created.json().get("run", {}).get("id") or "")
        self._wait_run_terminal(run_id=run_id, token="user-token")
        return agent_id, run_id

    def test_explain_feed_has_reason_result_next_step(self) -> None:
        _, run_id = self._create_agent_and_run()

        response = self.client.get(
            f"/agents/runs/{run_id}/explain",
            headers=self._auth("user-token"),
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        explainability = payload.get("explainability", {})
        self.assertEqual(str(explainability.get("run_id")), run_id)
        self.assertEqual(str(explainability.get("feed_version")), "run_explainability_feed_v1")

        items = explainability.get("items", [])
        self.assertTrue(isinstance(items, list) and len(items) >= 1)
        assert isinstance(items, list)
        for item in items:
            self.assertTrue(bool(str(item.get("reason") or "").strip()))
            self.assertTrue(bool(str(item.get("result") or "").strip()))
            self.assertTrue(bool(str(item.get("next_step") or "").strip()))

        summary = explainability.get("summary", {})
        recommended_actions = summary.get("recommended_actions", [])
        self.assertTrue(isinstance(recommended_actions, list) and len(recommended_actions) >= 1)
        self.assertTrue(bool(payload.get("request_id")))

    def test_explain_owner_enforced_and_filters_applied(self) -> None:
        _, run_id = self._create_agent_and_run()

        denied = self.client.get(
            f"/agents/runs/{run_id}/explain",
            headers=self._auth("user2-token"),
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(str(denied.json().get("error", {}).get("type")), "permission_denied")

        filtered = self.client.get(
            f"/agents/runs/{run_id}/explain",
            headers=self._auth("user-token"),
            params={
                "include_tool_calls": "false",
                "include_security_actions": "false",
                "limit": 5,
            },
        )
        self.assertEqual(filtered.status_code, 200)
        payload = filtered.json().get("explainability", {})
        items = payload.get("items", [])
        self.assertTrue(isinstance(items, list) and len(items) >= 1)
        assert isinstance(items, list)
        self.assertLessEqual(len(items), 5)
        channels = {str(item.get("channel") or "") for item in items}
        self.assertEqual(channels, {"run_checkpoint"})


if __name__ == "__main__":
    unittest.main()
