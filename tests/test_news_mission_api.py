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

    def _create_news_agent(self, *, token: str = "user-token", user_id: str = "user-1", name: str = "AI Pulse") -> str:
        response = self.client.post(
            "/news/agents/create",
            headers=self._auth(token),
            json={
                "user_id": user_id,
                "name": name,
                "focus": "AI",
                "set_default": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        news_agent_id = str((payload.get("news_agent") or {}).get("news_agent_id"))
        self.assertTrue(news_agent_id)
        return news_agent_id

    def test_news_contract_and_plan_without_manual_agent(self) -> None:
        contract = self.client.get("/news/contract", headers=self._auth("user-token"))
        self.assertEqual(contract.status_code, 200)
        contract_payload = contract.json()
        self.assertEqual(str(contract_payload.get("contract_version")), "news_mission_v1")
        self.assertIn("web", contract_payload.get("supported_sources", []))

        endpoints = {
            (str(item.get("method")), str(item.get("path")))
            for item in (contract_payload.get("endpoints") or [])
            if isinstance(item, dict)
        }
        self.assertIn(("POST", "/news/agents/create"), endpoints)
        self.assertIn(("GET", "/news/agents"), endpoints)
        self.assertIn(("POST", "/news/agents/quickstart"), endpoints)
        self.assertIn(("POST", "/news/missions/plan"), endpoints)

        planned = self.client.post(
            "/news/missions/plan",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "topic": "AI",
                "sources": ["web", "reddit"],
                "window_hours": 24,
                "max_items_per_source": 15,
                "timezone": "UTC",
                "start_immediately": False,
                "internet_scope": {
                    "queries": ["AI agents", "open source copilots"],
                    "include_domains": ["openai.com", "arxiv.org"],
                    "exclude_domains": ["example.com"],
                    "seed_urls": ["https://news.ycombinator.com/newest"],
                    "max_depth": 2,
                },
            },
        )
        self.assertEqual(planned.status_code, 200)
        payload = planned.json()

        news_agent = payload.get("news_agent", {})
        self.assertTrue(str(news_agent.get("news_agent_id") or "").strip())

        mission_plan = payload.get("mission_plan", {})
        self.assertEqual(str(mission_plan.get("user_id")), "user-1")
        self.assertEqual(str(mission_plan.get("topic")), "AI")
        self.assertEqual(mission_plan.get("sources"), ["web", "reddit"])
        self.assertEqual(str(mission_plan.get("schedule_type")), "weekly")
        self.assertIn("next_run_at", mission_plan)

        query_bundle = mission_plan.get("query_bundle", [])
        self.assertTrue(any("site:openai.com" in str(item) for item in query_bundle))
        self.assertTrue(any("site:arxiv.org" in str(item) for item in query_bundle))

        apply_hint = payload.get("apply_hint", {})
        self.assertEqual(str(apply_hint.get("endpoint")), "/news/missions/create")
        apply_payload = apply_hint.get("payload", {})
        self.assertEqual(str(apply_payload.get("user_id")), "user-1")
        self.assertTrue(str(apply_payload.get("news_agent_id") or "").strip())

    def test_news_agent_create_and_list(self) -> None:
        created_id = self._create_news_agent()

        listed = self.client.get(
            "/news/agents",
            headers=self._auth("user-token"),
            params={"user_id": "user-1"},
        )
        self.assertEqual(listed.status_code, 200)
        listed_payload = listed.json()
        self.assertGreaterEqual(int(listed_payload.get("count", 0)), 1)

        ids = {
            str(item.get("news_agent_id"))
            for item in listed_payload.get("items", [])
            if isinstance(item, dict)
        }
        self.assertIn(created_id, ids)

    def test_news_create_and_ingest_flow_without_manual_agent(self) -> None:
        created = self.client.post(
            "/news/missions/create",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "topic": "AI safety",
                "sources": ["web"],
                "window_hours": 12,
                "max_items_per_source": 10,
                "timezone": "UTC",
                "start_immediately": True,
                "internet_scope": {
                    "queries": ["ai safety policy"],
                    "include_domains": ["openai.com"],
                    "max_depth": 1,
                },
            },
        )
        self.assertEqual(created.status_code, 200)
        created_payload = created.json()
        self.assertIn("news_agent", created_payload)
        self.assertIn("mission_plan", created_payload)
        self.assertIn("automation", created_payload)
        self.assertTrue(str((created_payload.get("news_agent") or {}).get("news_agent_id") or "").strip())

        services = self.server_module.app.state.services
        fake_report = {
            "topic": "AI safety",
            "sources": ["web", "reddit"],
            "query_bundle": ["ai safety site:openai.com"],
            "per_source_count": {"web": 1, "reddit": 1},
            "connector_errors": {},
            "raw_count": 2,
            "deduped_count": 2,
            "generated_at": "2026-04-04T00:00:00+00:00",
            "items": [
                {
                    "source": "web",
                    "canonical_id": "web-1",
                    "url": "https://openai.com/news/one",
                    "title": "One",
                    "excerpt": "item one",
                    "author": "team",
                    "published_at": "2026-04-04T00:00:00+00:00",
                    "ingested_at": "2026-04-04T00:00:00+00:00",
                    "raw_score": 0.9,
                    "metadata": {"matched_query": "ai safety site:openai.com"},
                },
                {
                    "source": "reddit",
                    "canonical_id": "reddit-1",
                    "url": "https://www.reddit.com/r/MachineLearning/comments/abc",
                    "title": "Two",
                    "published_at": "2026-04-04T00:05:00+00:00",
                    "ingested_at": "2026-04-04T00:05:00+00:00",
                    "metadata": {"subreddit": "MachineLearning"},
                },
            ],
        }
        with patch.object(services.news_pipeline, "ingest_preview", return_value=fake_report):
            preview = self.client.post(
                "/news/ingest/preview",
                headers=self._auth("user-token"),
                json={
                    "user_id": "user-1",
                    "topic": "AI safety",
                    "sources": ["web", "reddit"],
                    "window_hours": 24,
                    "max_items_per_source": 20,
                    "internet_scope": {
                        "queries": ["ai safety"],
                        "include_domains": ["openai.com", "reddit.com"],
                        "max_depth": 2,
                    },
                    "persist": True,
                },
            )
        self.assertEqual(preview.status_code, 200)
        preview_payload = preview.json()
        self.assertEqual(int(preview_payload.get("persisted_count", -1)), 2)

        listed = self.client.get(
            "/news/items",
            headers=self._auth("user-token"),
            params={"user_id": "user-1", "topic": "AI safety", "limit": 10},
        )
        self.assertEqual(listed.status_code, 200)
        listed_payload = listed.json()
        self.assertEqual(int(listed_payload.get("count", -1)), 2)

    def test_news_quickstart_one_click_phrase(self) -> None:
        quickstart = self.client.post(
            "/news/agents/quickstart",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "request": "сделай пожалуйста такого агента",
            },
        )
        self.assertEqual(quickstart.status_code, 200)
        payload = quickstart.json()
        self.assertEqual(str(payload.get("resolved_topic")), "AI")
        self.assertTrue(str((payload.get("news_agent") or {}).get("news_agent_id") or "").strip())
        self.assertTrue(str((payload.get("automation") or {}).get("id") or "").strip())
        self.assertIn("Готово", str(payload.get("assistant_reply") or ""))

    def test_news_plan_cross_tenant_is_blocked(self) -> None:
        news_agent_id = self._create_news_agent(token="user-token", user_id="user-1", name="Owner News Agent")

        denied = self.client.post(
            "/news/missions/plan",
            headers=self._auth("user2-token"),
            json={
                "news_agent_id": news_agent_id,
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
