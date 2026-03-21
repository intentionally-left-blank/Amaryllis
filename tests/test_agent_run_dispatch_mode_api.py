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
class AgentRunDispatchModeAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-run-dispatch-mode-api-")
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

    def _create_agent(self, *, name: str = "Dispatch Agent") -> str:
        created = self.client.post(
            "/agents/create",
            headers=self._auth("user-token"),
            json={
                "name": name,
                "system_prompt": "dispatch-mode-test",
                "user_id": "user-1",
                "tools": ["web_search"],
            },
        )
        self.assertEqual(created.status_code, 200)
        return str(created.json().get("id") or "")

    def test_modes_contract_endpoint(self) -> None:
        response = self.client.get(
            "/agents/runs/interaction-modes",
            headers=self._auth("user-token"),
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("plan", payload.get("supported_interaction_modes", []))
        self.assertIn("execute", payload.get("supported_interaction_modes", []))
        modes = payload.get("modes", [])
        self.assertTrue(any(str(item.get("mode")) == "plan" for item in modes))
        self.assertTrue(any(str(item.get("mode")) == "execute" for item in modes))

    def test_dispatch_plan_mode_is_dry_run_and_returns_execute_hint(self) -> None:
        agent_id = self._create_agent(name="Dispatch Plan Agent")
        response = self.client.post(
            f"/agents/{agent_id}/runs/dispatch",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "message": "Investigate issue and produce plan",
                "session_id": "dispatch-session-1",
                "interaction_mode": "plan",
                "max_attempts": 2,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(str(payload.get("interaction_mode")), "plan")
        self.assertFalse(bool(payload.get("trust_boundary", {}).get("execution_performed")))
        simulation = payload.get("simulation", {})
        self.assertEqual(str(simulation.get("mode")), "dry_run")
        self.assertTrue(bool(payload.get("dry_run_receipt", {}).get("signature")))

        execute_hint = payload.get("execute_hint", {})
        self.assertEqual(str(execute_hint.get("endpoint")), f"/agents/{agent_id}/runs/dispatch")
        execute_payload = execute_hint.get("payload", {})
        self.assertEqual(str(execute_payload.get("interaction_mode")), "execute")

        runs = self.client.get(
            f"/agents/{agent_id}/runs",
            headers=self._auth("user-token"),
            params={"user_id": "user-1"},
        )
        self.assertEqual(runs.status_code, 200)
        self.assertEqual(int(runs.json().get("count", -1)), 0)

    def test_dispatch_execute_mode_creates_run(self) -> None:
        agent_id = self._create_agent(name="Dispatch Execute Agent")
        response = self.client.post(
            f"/agents/{agent_id}/runs/dispatch",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "message": "Execute investigation now",
                "interaction_mode": "execute",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(str(payload.get("interaction_mode")), "execute")
        self.assertTrue(bool(payload.get("trust_boundary", {}).get("execution_performed")))
        run = payload.get("run", {})
        self.assertTrue(bool(str(run.get("id") or "").strip()))
        self.assertTrue(bool(payload.get("action_receipt", {}).get("signature")))

    def test_dispatch_rejects_invalid_mode(self) -> None:
        agent_id = self._create_agent(name="Dispatch Invalid Mode Agent")
        response = self.client.post(
            f"/agents/{agent_id}/runs/dispatch",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "message": "invalid mode",
                "interaction_mode": "auto",
            },
        )
        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(str(payload.get("error", {}).get("type")), "validation_error")

    def test_dispatch_cross_tenant_is_blocked(self) -> None:
        agent_id = self._create_agent(name="Dispatch Owner Agent")
        response = self.client.post(
            f"/agents/{agent_id}/runs/dispatch",
            headers=self._auth("user2-token"),
            json={
                "user_id": "user-2",
                "message": "attempt foreign execute",
                "interaction_mode": "execute",
            },
        )
        self.assertEqual(response.status_code, 403)
        payload = response.json()
        self.assertEqual(str(payload.get("error", {}).get("type")), "permission_denied")


if __name__ == "__main__":
    unittest.main()
