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
class AutomationMissionPlanAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-automation-plan-api-")
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

        services = cls.server_module.app.state.services
        if services.tool_registry.get("dangerous_echo") is None:
            services.tool_registry.register(
                name="dangerous_echo",
                description="High-risk test tool.",
                input_schema={"type": "object", "properties": {"echo": {"type": "string"}}},
                handler=lambda arguments: {"ok": True, "echo": str(arguments.get("echo", ""))},
                source="test",
                risk_level="high",
                approval_mode="none",
                isolation="process_internal",
            )

    @classmethod
    def tearDownClass(cls) -> None:
        cls._client_cm.__exit__(None, None, None)
        cls._env_patch.stop()
        cls._tmp.cleanup()

    @staticmethod
    def _auth(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def test_plan_mission_returns_apply_hint_and_simulation(self) -> None:
        created = self.client.post(
            "/agents/create",
            headers=self._auth("user-token"),
            json={
                "name": "Planner Agent",
                "system_prompt": "planner",
                "user_id": "user-1",
                "tools": ["dangerous_echo", "web_search"],
            },
        )
        self.assertEqual(created.status_code, 200)
        agent_id = str(created.json().get("id"))

        planned = self.client.post(
            "/automations/mission/plan",
            headers=self._auth("user-token"),
            json={
                "agent_id": agent_id,
                "user_id": "user-1",
                "message": "Run autonomous daily code-health mission",
                "cadence_profile": "workday",
                "start_immediately": True,
                "timezone": "UTC",
            },
        )
        self.assertEqual(planned.status_code, 200)
        payload = planned.json()

        mission_plan = payload.get("mission_plan", {})
        self.assertEqual(str(mission_plan.get("agent_id")), agent_id)
        self.assertEqual(str(mission_plan.get("schedule_type")), "weekly")
        schedule = mission_plan.get("schedule", {})
        self.assertEqual(schedule.get("byday"), ["MO", "TU", "WE", "TH", "FR"])
        self.assertIn("next_run_at", mission_plan)

        simulation = payload.get("simulation", {})
        self.assertEqual(str(simulation.get("agent_id")), agent_id)
        risk_summary = simulation.get("risk_summary", {})
        self.assertIn(
            str(risk_summary.get("overall_risk_level")),
            {"low", "medium", "high", "critical", "unknown"},
        )

        apply_hint = payload.get("apply_hint", {})
        self.assertEqual(str(apply_hint.get("endpoint")), "/automations/create")
        apply_payload = apply_hint.get("payload", {})
        self.assertEqual(str(apply_payload.get("agent_id")), agent_id)
        self.assertEqual(str(apply_payload.get("user_id")), "user-1")
        self.assertEqual(str(apply_payload.get("schedule_type")), "weekly")
        self.assertIn("start_immediately", apply_payload)

    def test_mission_template_catalog_endpoint(self) -> None:
        listed = self.client.get(
            "/automations/mission/templates",
            headers=self._auth("user-token"),
        )
        self.assertEqual(listed.status_code, 200)
        payload = listed.json()
        self.assertGreaterEqual(int(payload.get("count", 0)), 4)
        items = payload.get("items", [])
        self.assertIsInstance(items, list)
        template_ids = {str(item.get("id")) for item in items if isinstance(item, dict)}
        self.assertTrue(
            {"code_health", "security_audit", "release_guard", "runtime_watchdog", "ai_news_daily"}.issubset(
                template_ids
            )
        )
        release_guard = next(
            (item for item in items if isinstance(item, dict) and str(item.get("id")) == "release_guard"),
            {},
        )
        self.assertEqual(str(release_guard.get("mission_policy_profile")), "release")
        ai_news = next(
            (item for item in items if isinstance(item, dict) and str(item.get("id")) == "ai_news_daily"),
            {},
        )
        self.assertEqual(str(ai_news.get("cadence_profile")), "daily")

    def test_mission_policy_catalog_endpoint(self) -> None:
        listed = self.client.get(
            "/automations/mission/policies",
            headers=self._auth("user-token"),
        )
        self.assertEqual(listed.status_code, 200)
        payload = listed.json()
        self.assertGreaterEqual(int(payload.get("count", 0)), 4)
        items = payload.get("items", [])
        self.assertIsInstance(items, list)
        policy_ids = {str(item.get("id")) for item in items if isinstance(item, dict)}
        self.assertTrue({"balanced", "strict", "watchdog", "release"}.issubset(policy_ids))

    def test_plan_mission_from_template_without_manual_prompt(self) -> None:
        created = self.client.post(
            "/agents/create",
            headers=self._auth("user-token"),
            json={
                "name": "Template Planner Agent",
                "system_prompt": "planner",
                "user_id": "user-1",
                "tools": ["web_search"],
            },
        )
        self.assertEqual(created.status_code, 200)
        agent_id = str(created.json().get("id"))

        planned = self.client.post(
            "/automations/mission/plan",
            headers=self._auth("user-token"),
            json={
                "agent_id": agent_id,
                "user_id": "user-1",
                "template_id": "release_guard",
                "timezone": "UTC",
            },
        )
        self.assertEqual(planned.status_code, 200)
        payload = planned.json()
        mission_plan = payload.get("mission_plan", {})
        message = str(mission_plan.get("message") or "")
        self.assertIn("release guard mission", message.lower())
        self.assertEqual(str(mission_plan.get("schedule_type")), "weekly")
        schedule = mission_plan.get("schedule", {})
        self.assertEqual(schedule.get("byday"), ["MO", "TU", "WE", "TH", "FR", "SA", "SU"])
        template = payload.get("template", {})
        self.assertEqual(str(template.get("id")), "release_guard")
        mission_policy = payload.get("mission_policy", {})
        self.assertEqual(str(mission_policy.get("profile")), "release")
        apply_payload = payload.get("apply_hint", {}).get("payload", {})
        self.assertEqual(str(apply_payload.get("mission_policy", {}).get("profile")), "release")

    def test_plan_mission_cross_tenant_is_blocked(self) -> None:
        created = self.client.post(
            "/agents/create",
            headers=self._auth("user-token"),
            json={
                "name": "Owner Agent",
                "system_prompt": "owner",
                "user_id": "user-1",
                "tools": ["web_search"],
            },
        )
        self.assertEqual(created.status_code, 200)
        agent_id = str(created.json().get("id"))

        denied = self.client.post(
            "/automations/mission/plan",
            headers=self._auth("user2-token"),
            json={
                "agent_id": agent_id,
                "user_id": "user-2",
                "message": "try to plan mission",
                "cadence_profile": "hourly",
                "start_immediately": False,
            },
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(str(denied.json().get("error", {}).get("type")), "permission_denied")

    def test_plan_mission_from_ai_news_daily_template(self) -> None:
        created = self.client.post(
            "/agents/create",
            headers=self._auth("user-token"),
            json={
                "name": "News Planner Agent",
                "system_prompt": "news planner",
                "user_id": "user-1",
                "tools": ["web_search"],
            },
        )
        self.assertEqual(created.status_code, 200)
        agent_id = str(created.json().get("id"))

        planned = self.client.post(
            "/automations/mission/plan",
            headers=self._auth("user-token"),
            json={
                "agent_id": agent_id,
                "user_id": "user-1",
                "template_id": "ai_news_daily",
                "timezone": "UTC",
            },
        )
        self.assertEqual(planned.status_code, 200)
        payload = planned.json()
        mission_plan = payload.get("mission_plan", {})
        self.assertEqual(str(mission_plan.get("schedule_type")), "weekly")
        schedule = mission_plan.get("schedule", {})
        self.assertEqual(schedule.get("byday"), ["MO", "TU", "WE", "TH", "FR", "SA", "SU"])
        message = str(mission_plan.get("message") or "").lower()
        self.assertIn("daily ai news mission", message)
        template = payload.get("template", {})
        self.assertEqual(str(template.get("id")), "ai_news_daily")


if __name__ == "__main__":
    unittest.main()
