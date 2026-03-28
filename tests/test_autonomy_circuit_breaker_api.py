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
class AutonomyCircuitBreakerAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-autonomy-circuit-breaker-api-")
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
                "AMARYLLIS_COGNITION_BACKEND": "deterministic",
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

    def _create_agent(self, *, name: str) -> str:
        created = self.client.post(
            "/agents/create",
            headers=self._auth("user-token"),
            json={
                "name": name,
                "system_prompt": "autonomy-circuit-breaker-api-test",
                "user_id": "user-1",
                "tools": ["web_search"],
            },
        )
        self.assertEqual(created.status_code, 200)
        return str(created.json().get("id") or "")

    def test_service_scope_is_required_for_circuit_breaker_endpoints(self) -> None:
        denied_get = self.client.get(
            "/service/runs/autonomy-circuit-breaker",
            headers=self._auth("user-token"),
        )
        self.assertEqual(denied_get.status_code, 403)

        denied_post = self.client.post(
            "/service/runs/autonomy-circuit-breaker",
            headers=self._auth("user-token"),
            json={"action": "arm"},
        )
        self.assertEqual(denied_post.status_code, 403)

    def test_arm_blocks_execute_and_disarm_restores_create_run(self) -> None:
        agent_id = self._create_agent(name="Autonomy Circuit Breaker Agent")

        arm = self.client.post(
            "/service/runs/autonomy-circuit-breaker",
            headers=self._auth("service-token"),
            json={
                "action": "arm",
                "reason": "incident-response",
                "apply_kill_switch": False,
            },
        )
        self.assertEqual(arm.status_code, 200)
        arm_payload = arm.json()
        self.assertTrue(bool(arm_payload.get("circuit_breaker", {}).get("armed")))
        self.assertTrue(bool(arm_payload.get("action_receipt", {}).get("signature")))

        create_blocked = self.client.post(
            f"/agents/{agent_id}/runs",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "message": "execute while breaker is armed",
            },
        )
        self.assertEqual(create_blocked.status_code, 400)
        create_blocked_payload = create_blocked.json().get("error", {})
        self.assertEqual(str(create_blocked_payload.get("type")), "validation_error")
        self.assertIn("circuit breaker", str(create_blocked_payload.get("message", "")).lower())

        dispatch_blocked = self.client.post(
            f"/agents/{agent_id}/runs/dispatch",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "message": "execute dispatch while breaker is armed",
                "interaction_mode": "execute",
            },
        )
        self.assertEqual(dispatch_blocked.status_code, 400)

        dispatch_plan = self.client.post(
            f"/agents/{agent_id}/runs/dispatch",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "message": "plan still allowed",
                "interaction_mode": "plan",
            },
        )
        self.assertEqual(dispatch_plan.status_code, 200)
        self.assertEqual(str(dispatch_plan.json().get("interaction_mode")), "plan")

        status = self.client.get(
            "/service/runs/autonomy-circuit-breaker",
            headers=self._auth("service-token"),
        )
        self.assertEqual(status.status_code, 200)
        status_payload = status.json()
        self.assertTrue(bool(status_payload.get("circuit_breaker", {}).get("armed")))

        disarm = self.client.post(
            "/service/runs/autonomy-circuit-breaker",
            headers=self._auth("service-token"),
            json={
                "action": "disarm",
                "reason": "incident-mitigated",
            },
        )
        self.assertEqual(disarm.status_code, 200)
        self.assertFalse(bool(disarm.json().get("circuit_breaker", {}).get("armed")))

        create_after_disarm = self.client.post(
            f"/agents/{agent_id}/runs",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "message": "execute after disarm",
            },
        )
        self.assertEqual(create_after_disarm.status_code, 200)
        run_id = str(create_after_disarm.json().get("run", {}).get("id") or "")
        self.assertTrue(bool(run_id))

    def test_validation_for_invalid_action_and_kill_switch_scope(self) -> None:
        invalid_action = self.client.post(
            "/service/runs/autonomy-circuit-breaker",
            headers=self._auth("service-token"),
            json={"action": "freeze"},
        )
        self.assertEqual(invalid_action.status_code, 400)

        invalid_scope = self.client.post(
            "/service/runs/autonomy-circuit-breaker",
            headers=self._auth("service-token"),
            json={
                "action": "arm",
                "apply_kill_switch": True,
                "include_running": False,
                "include_queued": False,
            },
        )
        self.assertEqual(invalid_scope.status_code, 400)

        self.client.post(
            "/service/runs/autonomy-circuit-breaker",
            headers=self._auth("service-token"),
            json={"action": "disarm", "reason": "cleanup"},
        )


if __name__ == "__main__":
    unittest.main()
