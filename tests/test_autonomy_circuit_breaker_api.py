from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

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

    def _create_agent(self, *, token: str, user_id: str, name: str) -> str:
        created = self.client.post(
            "/agents/create",
            headers=self._auth(token),
            json={
                "name": name,
                "system_prompt": "autonomy-circuit-breaker-api-test",
                "user_id": user_id,
                "tools": ["web_search"],
            },
        )
        self.assertEqual(created.status_code, 200)
        return str(created.json().get("id") or "")

    def _disarm_global_for_cleanup(self) -> None:
        self.client.post(
            "/service/runs/autonomy-circuit-breaker",
            headers=self._auth("service-token"),
            json={"action": "disarm", "reason": "cleanup", "scope_type": "global"},
        )

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
        agent_id = self._create_agent(token="user-token", user_id="user-1", name="Autonomy Circuit Breaker Agent")

        arm = self.client.post(
            "/service/runs/autonomy-circuit-breaker",
            headers=self._auth("service-token"),
            json={
                "action": "arm",
                "reason": "incident-response",
                "scope_type": "global",
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
                "scope_type": "global",
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

    def test_scoped_user_breaker_blocks_only_target_user(self) -> None:
        user1_agent = self._create_agent(token="user-token", user_id="user-1", name="Scoped User Agent 1")
        user2_agent = self._create_agent(token="user2-token", user_id="user-2", name="Scoped User Agent 2")

        arm = self.client.post(
            "/service/runs/autonomy-circuit-breaker",
            headers=self._auth("service-token"),
            json={
                "action": "arm",
                "scope_type": "user",
                "scope_user_id": "user-1",
                "reason": "scope-user-1",
                "apply_kill_switch": False,
            },
        )
        self.assertEqual(arm.status_code, 200)

        blocked_user1 = self.client.post(
            f"/agents/{user1_agent}/runs",
            headers=self._auth("user-token"),
            json={"user_id": "user-1", "message": "blocked by user scope"},
        )
        self.assertEqual(blocked_user1.status_code, 400)

        allowed_user2 = self.client.post(
            f"/agents/{user2_agent}/runs",
            headers=self._auth("user2-token"),
            json={"user_id": "user-2", "message": "must stay allowed"},
        )
        self.assertEqual(allowed_user2.status_code, 200)

        disarm = self.client.post(
            "/service/runs/autonomy-circuit-breaker",
            headers=self._auth("service-token"),
            json={
                "action": "disarm",
                "scope_type": "user",
                "scope_user_id": "user-1",
                "reason": "scope-user-1-done",
            },
        )
        self.assertEqual(disarm.status_code, 200)

        self._disarm_global_for_cleanup()

    def test_scoped_agent_breaker_blocks_only_target_agent(self) -> None:
        agent1 = self._create_agent(token="user-token", user_id="user-1", name="Scoped Agent 1")
        agent2 = self._create_agent(token="user-token", user_id="user-1", name="Scoped Agent 2")

        arm = self.client.post(
            "/service/runs/autonomy-circuit-breaker",
            headers=self._auth("service-token"),
            json={
                "action": "arm",
                "scope_type": "agent",
                "scope_agent_id": agent1,
                "reason": "scope-agent-1",
                "apply_kill_switch": False,
            },
        )
        self.assertEqual(arm.status_code, 200)

        blocked_agent1 = self.client.post(
            f"/agents/{agent1}/runs",
            headers=self._auth("user-token"),
            json={"user_id": "user-1", "message": "blocked by agent scope"},
        )
        self.assertEqual(blocked_agent1.status_code, 400)

        allowed_agent2 = self.client.post(
            f"/agents/{agent2}/runs",
            headers=self._auth("user-token"),
            json={"user_id": "user-1", "message": "must stay allowed"},
        )
        self.assertEqual(allowed_agent2.status_code, 200)

        disarm = self.client.post(
            "/service/runs/autonomy-circuit-breaker",
            headers=self._auth("service-token"),
            json={
                "action": "disarm",
                "scope_type": "agent",
                "scope_agent_id": agent1,
                "reason": "scope-agent-1-done",
            },
        )
        self.assertEqual(disarm.status_code, 200)

        self._disarm_global_for_cleanup()

    def test_timeline_endpoint_returns_transition_entries(self) -> None:
        marker = uuid4().hex
        arm_reason = f"timeline-arm-{marker}"
        disarm_reason = f"timeline-disarm-{marker}"

        arm = self.client.post(
            "/service/runs/autonomy-circuit-breaker",
            headers=self._auth("service-token"),
            json={
                "action": "arm",
                "scope_type": "global",
                "reason": arm_reason,
                "apply_kill_switch": False,
            },
        )
        self.assertEqual(arm.status_code, 200)
        armed_status = self.client.get(
            "/service/runs/autonomy-circuit-breaker",
            headers=self._auth("service-token"),
        )
        self.assertEqual(armed_status.status_code, 200)
        armed_guidance = armed_status.json().get("recovery_guidance", {})
        self.assertTrue(isinstance(armed_guidance.get("recommendations"), list))
        self.assertIn(str(armed_guidance.get("status")), {"action_required", "monitoring"})

        disarm = self.client.post(
            "/service/runs/autonomy-circuit-breaker",
            headers=self._auth("service-token"),
            json={
                "action": "disarm",
                "scope_type": "global",
                "reason": disarm_reason,
            },
        )
        self.assertEqual(disarm.status_code, 200)

        timeline = self.client.get(
            "/service/runs/autonomy-circuit-breaker/timeline",
            headers=self._auth("service-token"),
            params={"limit": 200},
        )
        self.assertEqual(timeline.status_code, 200)
        payload = timeline.json()
        items = payload.get("items", [])
        self.assertGreaterEqual(len(items), 2)
        timeline_guidance = payload.get("recovery_guidance", {})
        self.assertTrue(isinstance(timeline_guidance.get("recommendations"), list))

        arm_item = next(
            (
                item
                for item in items
                if str((item.get("transition") or {}).get("reason")) == arm_reason
                and str((item.get("transition") or {}).get("action")) == "arm"
            ),
            None,
        )
        self.assertIsNotNone(arm_item)
        assert isinstance(arm_item, dict)
        self.assertEqual(str(arm_item.get("actor")), "svc-runtime")
        self.assertTrue(bool(str(arm_item.get("request_id") or "").strip()))
        self.assertEqual(str((arm_item.get("transition") or {}).get("scope_type")), "global")

        disarm_item = next(
            (
                item
                for item in items
                if str((item.get("transition") or {}).get("reason")) == disarm_reason
                and str((item.get("transition") or {}).get("action")) == "disarm"
            ),
            None,
        )
        self.assertIsNotNone(disarm_item)

        filtered = self.client.get(
            "/service/runs/autonomy-circuit-breaker/timeline",
            headers=self._auth("service-token"),
            params={
                "limit": 200,
                "transition": "arm",
                "request_id": str(arm_item.get("request_id") or ""),
            },
        )
        self.assertEqual(filtered.status_code, 200)
        filtered_items = filtered.json().get("items", [])
        self.assertGreaterEqual(len(filtered_items), 1)
        self.assertTrue(
            all(
                str((item.get("transition") or {}).get("action")) == "arm"
                and str(item.get("request_id") or "") == str(arm_item.get("request_id") or "")
                for item in filtered_items
            )
        )

    def test_validation_for_invalid_action_and_scope_contract(self) -> None:
        invalid_action = self.client.post(
            "/service/runs/autonomy-circuit-breaker",
            headers=self._auth("service-token"),
            json={"action": "freeze"},
        )
        self.assertEqual(invalid_action.status_code, 400)

        invalid_kill_scope = self.client.post(
            "/service/runs/autonomy-circuit-breaker",
            headers=self._auth("service-token"),
            json={
                "action": "arm",
                "apply_kill_switch": True,
                "include_running": False,
                "include_queued": False,
            },
        )
        self.assertEqual(invalid_kill_scope.status_code, 400)

        missing_user_scope = self.client.post(
            "/service/runs/autonomy-circuit-breaker",
            headers=self._auth("service-token"),
            json={"action": "arm", "scope_type": "user"},
        )
        self.assertEqual(missing_user_scope.status_code, 400)

        missing_agent_scope = self.client.post(
            "/service/runs/autonomy-circuit-breaker",
            headers=self._auth("service-token"),
            json={"action": "arm", "scope_type": "agent"},
        )
        self.assertEqual(missing_agent_scope.status_code, 400)

        invalid_global_extra = self.client.post(
            "/service/runs/autonomy-circuit-breaker",
            headers=self._auth("service-token"),
            json={"action": "arm", "scope_type": "global", "scope_user_id": "user-1"},
        )
        self.assertEqual(invalid_global_extra.status_code, 400)

        self._disarm_global_for_cleanup()

    def test_z_breaker_state_restores_after_server_restart(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-tests-autonomy-circuit-breaker-restart-") as tmp:
            support_dir = Path(tmp) / "support"
            auth_tokens = {
                "service-token": {"user_id": "svc-runtime", "scopes": ["service"]},
                "user-token": {"user_id": "user-1", "scopes": ["user"]},
            }
            with patch.dict(
                os.environ,
                {
                    "AMARYLLIS_SUPPORT_DIR": str(support_dir),
                    "AMARYLLIS_AUTH_ENABLED": "true",
                    "AMARYLLIS_AUTH_TOKENS": json.dumps(auth_tokens, ensure_ascii=False),
                    "AMARYLLIS_MEMORY_CONSOLIDATION_ENABLED": "false",
                    "AMARYLLIS_MCP_ENDPOINTS": "",
                    "AMARYLLIS_SECURITY_PROFILE": "production",
                    "AMARYLLIS_COGNITION_BACKEND": "deterministic",
                    "AMARYLLIS_AUTOMATION_ENABLED": "false",
                    "AMARYLLIS_BACKUP_ENABLED": "false",
                    "AMARYLLIS_BACKUP_RESTORE_DRILL_ENABLED": "false",
                },
                clear=False,
            ):
                import runtime.server as server_module

                first_boot = importlib.reload(server_module)
                with TestClient(first_boot.app) as boot1_client:
                    create_agent = boot1_client.post(
                        "/agents/create",
                        headers=self._auth("user-token"),
                        json={
                            "name": "Restart Restore Agent",
                            "system_prompt": "restart-restore-check",
                            "user_id": "user-1",
                            "tools": ["web_search"],
                        },
                    )
                    self.assertEqual(create_agent.status_code, 200)
                    agent_id = str(create_agent.json().get("id") or "")
                    self.assertTrue(bool(agent_id))

                    arm = boot1_client.post(
                        "/service/runs/autonomy-circuit-breaker",
                        headers=self._auth("service-token"),
                        json={
                            "action": "arm",
                            "scope_type": "global",
                            "reason": "restart-restore-check",
                            "apply_kill_switch": False,
                        },
                    )
                    self.assertEqual(arm.status_code, 200)

                second_boot = importlib.reload(server_module)
                with TestClient(second_boot.app) as boot2_client:
                    status = boot2_client.get(
                        "/service/runs/autonomy-circuit-breaker",
                        headers=self._auth("service-token"),
                    )
                    self.assertEqual(status.status_code, 200)
                    state = status.json().get("circuit_breaker", {})
                    self.assertTrue(bool(state.get("armed")))

                    create_blocked = boot2_client.post(
                        f"/agents/{agent_id}/runs",
                        headers=self._auth("user-token"),
                        json={"user_id": "user-1", "message": "blocked after restart"},
                    )
                    self.assertEqual(create_blocked.status_code, 400)

                    disarm = boot2_client.post(
                        "/service/runs/autonomy-circuit-breaker",
                        headers=self._auth("service-token"),
                        json={"action": "disarm", "scope_type": "global", "reason": "cleanup"},
                    )
                    self.assertEqual(disarm.status_code, 200)


if __name__ == "__main__":
    unittest.main()
