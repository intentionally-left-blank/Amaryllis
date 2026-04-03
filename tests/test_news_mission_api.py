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
class NewsMissionAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-news-mission-api-")
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

    def test_news_contract_and_plan(self) -> None:
        contract = self.client.get("/news/contract", headers=self._auth("user-token"))
        self.assertEqual(contract.status_code, 200)
        contract_payload = contract.json()
        self.assertEqual(str(contract_payload.get("contract_version")), "news_mission_v1")
        self.assertIn("web", contract_payload.get("supported_sources", []))

        created = self.client.post(
            "/agents/create",
            headers=self._auth("user-token"),
            json={
                "name": "News Agent",
                "system_prompt": "news-planner",
                "user_id": "user-1",
                "tools": ["web_search"],
            },
        )
        self.assertEqual(created.status_code, 200)
        agent_id = str(created.json().get("id"))
        self.assertTrue(agent_id)

        planned = self.client.post(
            "/news/missions/plan",
            headers=self._auth("user-token"),
            json={
                "agent_id": agent_id,
                "user_id": "user-1",
                "topic": "AI",
                "sources": ["web", "reddit"],
                "window_hours": 24,
                "max_items_per_source": 15,
                "timezone": "UTC",
                "start_immediately": False,
            },
        )
        self.assertEqual(planned.status_code, 200)
        payload = planned.json()
        mission_plan = payload.get("mission_plan", {})
        self.assertEqual(str(mission_plan.get("agent_id")), agent_id)
        self.assertEqual(str(mission_plan.get("topic")), "AI")
        self.assertEqual(mission_plan.get("sources"), ["web", "reddit"])
        self.assertEqual(str(mission_plan.get("schedule_type")), "weekly")
        self.assertIn("next_run_at", mission_plan)
        self.assertIn("citation links", str(mission_plan.get("message", "")).lower())

        apply_hint = payload.get("apply_hint", {})
        self.assertEqual(str(apply_hint.get("endpoint")), "/automations/create")
        apply_payload = apply_hint.get("payload", {})
        self.assertEqual(str(apply_payload.get("agent_id")), agent_id)
        self.assertEqual(str(apply_payload.get("schedule_type")), "weekly")
        self.assertEqual(str(apply_payload.get("mission_policy", {}).get("topic")), "AI")

    def test_news_plan_cross_tenant_is_blocked(self) -> None:
        created = self.client.post(
            "/agents/create",
            headers=self._auth("user-token"),
            json={
                "name": "Owner News Agent",
                "system_prompt": "owner-news",
                "user_id": "user-1",
                "tools": ["web_search"],
            },
        )
        self.assertEqual(created.status_code, 200)
        agent_id = str(created.json().get("id"))

        denied = self.client.post(
            "/news/missions/plan",
            headers=self._auth("user2-token"),
            json={
                "agent_id": agent_id,
                "user_id": "user-2",
                "topic": "AI",
                "sources": ["web"],
                "timezone": "UTC",
            },
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(str(denied.json().get("error", {}).get("type")), "permission_denied")


if __name__ == "__main__":
    unittest.main()

