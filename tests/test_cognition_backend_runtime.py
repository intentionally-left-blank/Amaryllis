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
class CognitionBackendRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-cognition-runtime-")
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

    def test_health_uses_selected_cognition_backend(self) -> None:
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(str(payload.get("active_provider")), "deterministic")
        self.assertEqual(str(payload.get("active_model")), "deterministic-v1")

    def test_chat_endpoint_works_without_api_changes(self) -> None:
        response = self.client.post(
            "/v1/chat/completions",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "messages": [{"role": "user", "content": "hello runtime"}],
                "stream": False,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(str(payload.get("provider")), "deterministic")
        self.assertEqual(str(payload.get("model")), "deterministic-v1")
        content = str(((payload.get("choices") or [{}])[0].get("message", {}) or {}).get("content", ""))
        self.assertIn("hello runtime", content)
        provenance = payload.get("provenance")
        self.assertIsInstance(provenance, dict)
        assert isinstance(provenance, dict)
        self.assertEqual(str(provenance.get("version")), "provenance_v1")
        self.assertIn("grounded", provenance)
        self.assertIn("sources", provenance)

    def test_chat_phrase_can_create_agent_directly(self) -> None:
        response = self.client.post(
            "/v1/chat/completions",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "messages": [{"role": "user", "content": "хочу такого то агента сделай пж, для AI новостей"}],
                "stream": False,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(str(payload.get("provider")), "amaryllis")
        content = str(((payload.get("choices") or [{}])[0].get("message", {}) or {}).get("content", ""))
        self.assertIn("Создал агента", content)
        quick_action = payload.get("quick_action", {})
        self.assertEqual(str(quick_action.get("type")), "agent_created")
        agent = quick_action.get("agent", {})
        created_agent_id = str(agent.get("id") or "")
        self.assertTrue(created_agent_id)
        self.assertIsNone(quick_action.get("automation"))

        listed = self.client.get(
            "/agents",
            headers=self._auth("user-token"),
            params={"user_id": "user-1"},
        )
        self.assertEqual(listed.status_code, 200)
        listed_payload = listed.json()
        items = listed_payload.get("items", [])
        self.assertIsInstance(items, list)
        ids = {str(item.get("id")) for item in items if isinstance(item, dict)}
        self.assertIn(created_agent_id, ids)

    def test_chat_quickstart_replays_when_session_request_retried(self) -> None:
        before = self.client.get(
            "/agents",
            headers=self._auth("user-token"),
            params={"user_id": "user-1"},
        )
        self.assertEqual(before.status_code, 200)
        before_count = int(before.json().get("count", 0))

        body = {
            "user_id": "user-1",
            "session_id": "chat-quickstart-idempotency-session-1",
            "messages": [{"role": "user", "content": "сделай агента для AI новостей"}],
            "stream": False,
        }
        first = self.client.post(
            "/v1/chat/completions",
            headers=self._auth("user-token"),
            json=body,
        )
        self.assertEqual(first.status_code, 200)
        first_payload = first.json()
        first_quick_action = first_payload.get("quick_action", {})
        first_agent = first_quick_action.get("agent", {})
        first_agent_id = str(first_agent.get("id") or "")
        self.assertTrue(first_agent_id)
        first_idempotency = first_quick_action.get("idempotency", {})
        self.assertFalse(bool(first_idempotency.get("replayed", False)))
        self.assertTrue(bool(first_idempotency.get("derived", False)))

        second = self.client.post(
            "/v1/chat/completions",
            headers=self._auth("user-token"),
            json=body,
        )
        self.assertEqual(second.status_code, 200)
        second_payload = second.json()
        second_quick_action = second_payload.get("quick_action", {})
        second_agent = second_quick_action.get("agent", {})
        self.assertEqual(str(second_agent.get("id") or ""), first_agent_id)
        second_idempotency = second_quick_action.get("idempotency", {})
        self.assertTrue(bool(second_idempotency.get("replayed", False)))
        self.assertTrue(bool(second_idempotency.get("derived", False)))

        after = self.client.get(
            "/agents",
            headers=self._auth("user-token"),
            params={"user_id": "user-1"},
        )
        self.assertEqual(after.status_code, 200)
        after_count = int(after.json().get("count", 0))
        self.assertEqual(after_count, before_count + 1)

    def test_chat_phrase_can_create_scheduled_news_agent(self) -> None:
        response = self.client.post(
            "/v1/chat/completions",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "messages": [
                    {
                        "role": "user",
                        "content": (
                            "создай новостного агента для AI каждый день в 08:15 "
                            "из reddit и twitter, сразу запускай"
                        ),
                    }
                ],
                "stream": False,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(str(payload.get("provider")), "amaryllis")
        quick_action = payload.get("quick_action", {})
        self.assertEqual(str(quick_action.get("type")), "agent_created")

        agent = quick_action.get("agent", {})
        agent_id = str(agent.get("id") or "")
        self.assertTrue(agent_id)
        self.assertIn("web_search", agent.get("tools", []))
        system_prompt = str(agent.get("system_prompt") or "").lower()
        self.assertIn("reddit", system_prompt)
        self.assertIn("twitter", system_prompt)

        automation = quick_action.get("automation", {})
        self.assertIsInstance(automation, dict)
        self.assertTrue(str(automation.get("id") or "").strip())
        self.assertEqual(str(automation.get("schedule_type")), "weekly")
        schedule = automation.get("schedule", {})
        self.assertIsInstance(schedule, dict)
        self.assertEqual(int(schedule.get("hour", -1)), 8)
        self.assertEqual(int(schedule.get("minute", -1)), 15)

        listed = self.client.get(
            "/automations",
            headers=self._auth("user-token"),
            params={"user_id": "user-1", "agent_id": agent_id},
        )
        self.assertEqual(listed.status_code, 200)
        listed_payload = listed.json()
        self.assertGreaterEqual(int(listed_payload.get("count", 0)), 1)

    def test_chat_phrase_can_create_hourly_automation(self) -> None:
        response = self.client.post(
            "/v1/chat/completions",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "messages": [
                    {
                        "role": "user",
                        "content": "создай агента для мониторинга AI, каждые 3 часа в 05 минут",
                    }
                ],
                "stream": False,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        quick_action = payload.get("quick_action", {})
        automation = quick_action.get("automation", {})
        self.assertIsInstance(automation, dict)
        self.assertTrue(str(automation.get("id") or "").strip())
        self.assertEqual(str(automation.get("schedule_type")), "hourly")
        schedule = automation.get("schedule", {})
        self.assertEqual(int(schedule.get("interval_hours", -1)), 3)
        self.assertEqual(int(schedule.get("minute", -1)), 5)

    def test_agents_quickstart_endpoint_creates_agent_and_automation(self) -> None:
        response = self.client.post(
            "/v1/agents/quickstart",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "request": "создай новостного агента для AI каждый день в 08:15 из reddit и twitter",
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        agent = payload.get("agent", {})
        self.assertTrue(str(agent.get("id") or "").strip())
        self.assertIn("web_search", agent.get("tools", []))
        self.assertIn("news", str(payload.get("quickstart_spec", {}).get("kind", "")).lower())
        automation = payload.get("automation", {})
        self.assertIsInstance(automation, dict)
        self.assertTrue(str(automation.get("id") or "").strip())
        self.assertEqual(str(automation.get("schedule_type")), "weekly")
        schedule = automation.get("schedule", {})
        self.assertEqual(int(schedule.get("hour", -1)), 8)
        self.assertEqual(int(schedule.get("minute", -1)), 15)

    def test_agents_quickstart_endpoint_is_idempotent_with_same_key(self) -> None:
        before = self.client.get(
            "/agents",
            headers=self._auth("user-token"),
            params={"user_id": "user-1"},
        )
        self.assertEqual(before.status_code, 200)
        before_count = int(before.json().get("count", 0))

        body = {
            "user_id": "user-1",
            "idempotency_key": "runtime-test-news-ai-idem-1",
            "request": "создай агента для AI новостей каждый день в 08:15 из reddit и twitter",
        }
        first = self.client.post(
            "/v1/agents/quickstart",
            headers=self._auth("user-token"),
            json=body,
        )
        self.assertEqual(first.status_code, 200)
        first_payload = first.json()
        first_agent = first_payload.get("agent", {})
        first_agent_id = str(first_agent.get("id") or "")
        self.assertTrue(first_agent_id)
        first_automation = first_payload.get("automation", {})
        self.assertIsInstance(first_automation, dict)
        first_automation_id = str(first_automation.get("id") or "")
        self.assertTrue(first_automation_id)
        first_idempotency = first_payload.get("idempotency", {})
        self.assertFalse(bool(first_idempotency.get("replayed", False)))

        second = self.client.post(
            "/v1/agents/quickstart",
            headers=self._auth("user-token"),
            json=body,
        )
        self.assertEqual(second.status_code, 200)
        second_payload = second.json()
        second_agent = second_payload.get("agent", {})
        second_automation = second_payload.get("automation", {})
        self.assertEqual(str(second_agent.get("id") or ""), first_agent_id)
        self.assertEqual(str(second_automation.get("id") or ""), first_automation_id)
        second_idempotency = second_payload.get("idempotency", {})
        self.assertTrue(bool(second_idempotency.get("replayed", False)))

        after = self.client.get(
            "/agents",
            headers=self._auth("user-token"),
            params={"user_id": "user-1"},
        )
        self.assertEqual(after.status_code, 200)
        after_count = int(after.json().get("count", 0))
        self.assertEqual(after_count, before_count + 1)

    def test_agents_quickstart_idempotency_key_rejects_payload_change(self) -> None:
        first = self.client.post(
            "/v1/agents/quickstart",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "idempotency_key": "runtime-test-news-ai-idem-2",
                "request": "создай агента для AI новостей из reddit",
            },
        )
        self.assertEqual(first.status_code, 200)

        second = self.client.post(
            "/v1/agents/quickstart",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "idempotency_key": "runtime-test-news-ai-idem-2",
                "request": "создай агента для Python разработки",
            },
        )
        self.assertEqual(second.status_code, 400)
        error = second.json().get("error", {})
        self.assertEqual(str(error.get("type")), "validation_error")
        self.assertIn("Idempotency key", str(error.get("message", "")))

    def test_agents_quickstart_plan_endpoint_has_no_side_effects(self) -> None:
        before = self.client.get(
            "/agents",
            headers=self._auth("user-token"),
            params={"user_id": "user-1"},
        )
        self.assertEqual(before.status_code, 200)
        before_count = int(before.json().get("count", 0))

        planned = self.client.post(
            "/v1/agents/quickstart/plan",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "request": "создай агента для AI новостей каждый день в 09:30 из reddit и twitter",
            },
        )
        self.assertEqual(planned.status_code, 200)
        payload = planned.json()
        quickstart_plan = payload.get("quickstart_plan", {})
        self.assertEqual(str(quickstart_plan.get("kind")), "news")
        self.assertEqual(str(quickstart_plan.get("name")), "News Scout")
        self.assertIn("web_search", quickstart_plan.get("tools", []))
        automation_plan = quickstart_plan.get("automation", {})
        self.assertIsInstance(automation_plan, dict)
        self.assertTrue(bool(automation_plan.get("enabled", False)))
        self.assertEqual(str(automation_plan.get("schedule_type")), "weekly")
        self.assertEqual(int((automation_plan.get("schedule") or {}).get("hour", -1)), 9)
        self.assertEqual(int((automation_plan.get("schedule") or {}).get("minute", -1)), 30)

        apply_hint = payload.get("apply_hint", {})
        self.assertEqual(str(apply_hint.get("endpoint")), "/agents/quickstart")
        apply_payload = apply_hint.get("payload", {})
        self.assertEqual(str(apply_payload.get("user_id")), "user-1")
        self.assertTrue(str(apply_payload.get("request") or "").strip())
        self.assertTrue(str(apply_payload.get("idempotency_key") or "").strip())

        after = self.client.get(
            "/agents",
            headers=self._auth("user-token"),
            params={"user_id": "user-1"},
        )
        self.assertEqual(after.status_code, 200)
        after_count = int(after.json().get("count", 0))
        self.assertEqual(after_count, before_count)

    def test_agents_factory_contract_endpoint_is_available(self) -> None:
        response = self.client.get(
            "/v1/agents/factory/contract",
            headers=self._auth("user-token"),
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(str(payload.get("contract_version")), "agent_factory_v1")
        self.assertTrue(bool((payload.get("capabilities") or {}).get("structured_overrides", False)))
        self.assertTrue(bool((payload.get("capabilities") or {}).get("explainable_planning", False)))
        entrypoints = payload.get("entrypoints", [])
        self.assertIsInstance(entrypoints, list)
        signature = {
            (str(item.get("method") or "").upper(), str(item.get("path") or ""))
            for item in entrypoints
            if isinstance(item, dict)
        }
        self.assertIn(("POST", "/agents/quickstart/plan"), signature)
        self.assertIn(("POST", "/agents/quickstart"), signature)

    def test_quickstart_plan_infers_domain_allowlist_source_policy(self) -> None:
        planned = self.client.post(
            "/v1/agents/quickstart/plan",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "request": (
                    "создай новостного агента для AI с сайтов "
                    "https://openai.com/blog и huggingface.co каждый день в 07:45"
                ),
            },
        )
        self.assertEqual(planned.status_code, 200)
        payload = planned.json()
        quickstart_plan = payload.get("quickstart_plan", {})
        self.assertEqual(str(quickstart_plan.get("kind")), "news")
        source_policy = quickstart_plan.get("source_policy", {})
        self.assertIsInstance(source_policy, dict)
        self.assertEqual(str(source_policy.get("mode")), "allowlist")
        domains = source_policy.get("domains", [])
        self.assertIsInstance(domains, list)
        self.assertIn("openai.com", domains)
        self.assertIn("huggingface.co", domains)
        sources = quickstart_plan.get("sources", [])
        self.assertIn("web", sources)
        automation = quickstart_plan.get("automation", {})
        self.assertEqual(str(automation.get("schedule_type")), "weekly")
        self.assertEqual(int((automation.get("schedule") or {}).get("hour", -1)), 7)
        self.assertEqual(int((automation.get("schedule") or {}).get("minute", -1)), 45)
        inference_reason = quickstart_plan.get("inference_reason", {})
        self.assertIsInstance(inference_reason, dict)
        self.assertEqual(str(inference_reason.get("resolved_kind")), "news")

    def test_quickstart_plan_accepts_structured_overrides(self) -> None:
        planned = self.client.post(
            "/v1/agents/quickstart/plan",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "request": "создай агента для AI новостей каждый день в 09:30",
                "overrides": {
                    "kind": "coding",
                    "name": "Build Pilot",
                    "focus": "python tooling",
                    "source_policy": {
                        "mode": "allowlist",
                        "domains": ["pypi.org", "github.com"],
                    },
                    "automation": {
                        "enabled": True,
                        "schedule_type": "hourly",
                        "schedule": {"interval_hours": 6, "minute": 10},
                    },
                },
            },
        )
        self.assertEqual(planned.status_code, 200)
        payload = planned.json()
        quickstart_plan = payload.get("quickstart_plan", {})
        self.assertEqual(str(quickstart_plan.get("kind")), "coding")
        self.assertEqual(str(quickstart_plan.get("name")), "Build Pilot")
        self.assertEqual(str(quickstart_plan.get("focus")), "python tooling")
        source_policy = quickstart_plan.get("source_policy", {})
        self.assertEqual(str(source_policy.get("mode")), "allowlist")
        self.assertIn("pypi.org", source_policy.get("domains", []))
        self.assertIn("github.com", source_policy.get("domains", []))
        self.assertIn("web_search", quickstart_plan.get("tools", []))
        automation = quickstart_plan.get("automation", {})
        self.assertEqual(str(automation.get("schedule_type")), "hourly")
        self.assertEqual(int((automation.get("schedule") or {}).get("interval_hours", -1)), 6)
        self.assertEqual(int((automation.get("schedule") or {}).get("minute", -1)), 10)
        inference_reason = quickstart_plan.get("inference_reason", {})
        self.assertIsInstance(inference_reason, dict)
        self.assertIn("kind", inference_reason.get("overrides_applied", []))

    def test_agents_quickstart_idempotency_key_rejects_override_change(self) -> None:
        first = self.client.post(
            "/v1/agents/quickstart",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "idempotency_key": "runtime-test-news-ai-overrides-idem-1",
                "request": "создай агента для AI новостей",
                "overrides": {"name": "Scout A"},
            },
        )
        self.assertEqual(first.status_code, 200)

        second = self.client.post(
            "/v1/agents/quickstart",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "idempotency_key": "runtime-test-news-ai-overrides-idem-1",
                "request": "создай агента для AI новостей",
                "overrides": {"name": "Scout B"},
            },
        )
        self.assertEqual(second.status_code, 400)
        error = second.json().get("error", {})
        self.assertEqual(str(error.get("type")), "validation_error")
        self.assertIn("Idempotency key", str(error.get("message", "")))

    def test_chat_endpoint_returns_grounded_provenance_when_memory_fact_exists(self) -> None:
        services = self.server_module.app.state.services
        services.memory_manager.remember_fact(
            user_id="user-1",
            text="The codename for this release is amaryllis-orbit.",
            metadata={"source": "runtime_test"},
        )
        response = self.client.post(
            "/v1/chat/completions",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "messages": [{"role": "user", "content": "What is the codename for this release?"}],
                "stream": False,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        provenance = payload.get("provenance")
        self.assertIsInstance(provenance, dict)
        assert isinstance(provenance, dict)
        self.assertTrue(bool(provenance.get("grounded", False)))
        sources = provenance.get("sources")
        self.assertIsInstance(sources, list)
        assert isinstance(sources, list)
        self.assertGreaterEqual(len(sources), 1)

    def test_model_route_endpoint_uses_same_backend_contract(self) -> None:
        response = self.client.post(
            "/models/route",
            headers=self._auth("user-token"),
            json={
                "mode": "balanced",
                "require_stream": True,
                "require_tools": False,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        selected = payload.get("selected", {})
        self.assertEqual(str(selected.get("provider")), "deterministic")
        self.assertEqual(str(selected.get("model")), "deterministic-v1")

    def test_onboarding_profile_endpoint_uses_backend_contract(self) -> None:
        response = self.client.get(
            "/models/onboarding/profile",
            headers=self._auth("user-token"),
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(str(payload.get("recommended_profile")), "balanced")
        profiles = payload.get("profiles", {})
        self.assertIsInstance(profiles, dict)
        self.assertIn("balanced", profiles)
        self.assertIn("request_id", payload)

    def test_onboarding_activation_plan_endpoint_uses_backend_contract(self) -> None:
        response = self.client.get(
            "/models/onboarding/activation-plan?profile=balanced&include_remote_providers=true&limit=20",
            headers=self._auth("user-token"),
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(str(payload.get("plan_version")), "onboarding_activation_plan_v1")
        self.assertEqual(str(payload.get("selected_profile")), "balanced")
        self.assertTrue(str(payload.get("selected_package_id", "")).strip())
        self.assertIn("install", payload)
        self.assertIn("request_id", payload)

    def test_onboarding_activate_endpoint_uses_backend_contract(self) -> None:
        response = self.client.post(
            "/models/onboarding/activate",
            headers=self._auth("user-token"),
            json={
                "profile": "balanced",
                "include_remote_providers": True,
                "limit": 20,
                "require_metadata": False,
                "activate": True,
                "run_smoke_test": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(str(payload.get("activation_version")), "onboarding_activate_v1")
        self.assertIn(str(payload.get("status")), {"activated", "activated_with_smoke_warning"})
        self.assertIn("request_id", payload)
        self.assertTrue(bool(payload.get("action_receipt", {}).get("signature")))

    def test_model_package_catalog_and_install_endpoints_use_backend_contract(self) -> None:
        catalog_response = self.client.get(
            "/models/packages?profile=balanced&include_remote_providers=true&limit=20",
            headers=self._auth("user-token"),
        )
        self.assertEqual(catalog_response.status_code, 200)
        catalog_payload = catalog_response.json()
        self.assertEqual(str(catalog_payload.get("catalog_version")), "model_package_catalog_v1")
        packages = catalog_payload.get("packages", [])
        self.assertIsInstance(packages, list)
        self.assertTrue(packages)
        package_id = str((packages[0] or {}).get("package_id", ""))
        self.assertTrue(package_id)

        install_response = self.client.post(
            "/models/packages/install",
            headers=self._auth("user-token"),
            json={"package_id": package_id, "activate": True},
        )
        self.assertEqual(install_response.status_code, 200)
        install_payload = install_response.json()
        self.assertEqual(str(install_payload.get("package_id")), package_id)
        active = install_payload.get("active", {})
        self.assertEqual(str(active.get("provider")), "deterministic")
        self.assertIn("request_id", install_payload)

    def test_model_package_license_admission_endpoint_uses_backend_contract(self) -> None:
        package_id = "deterministic::deterministic-v1"
        response = self.client.get(
            f"/models/packages/license-admission?package_id={package_id}",
            headers=self._auth("user-token"),
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(str(payload.get("package_id")), package_id)
        self.assertTrue(bool(payload.get("admitted")))
        self.assertEqual(str(payload.get("status")), "allow")
        self.assertIn("request_id", payload)

    def test_generation_loop_contract_endpoint_returns_conformance_matrix(self) -> None:
        response = self.client.get(
            "/models/generation-loop/contract",
            headers=self._auth("user-token"),
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(str(payload.get("contract_version")), "generation_loop_contract_v1")
        self.assertIn("request_id", payload)
        cache = ((payload.get("contract") or {}).get("cache") or {}) if isinstance(payload, dict) else {}
        self.assertIn("pressure_states", cache)
        pressure_states = cache.get("pressure_states", [])
        self.assertIsInstance(pressure_states, list)
        self.assertIn("critical", pressure_states)
        self.assertEqual(str(cache.get("pressure_budget_units")), "estimated_tokens")

        providers = payload.get("providers", {})
        self.assertIsInstance(providers, dict)
        assert isinstance(providers, dict)
        self.assertIn("deterministic", providers)
        deterministic = providers.get("deterministic", {})
        self.assertIsInstance(deterministic, dict)
        conformance = deterministic.get("conformance", {})
        self.assertIsInstance(conformance, dict)
        self.assertIn(str(conformance.get("status")), {"pass", "warn"})

    def test_service_health_reports_backend_provider(self) -> None:
        response = self.client.get("/service/health", headers=self._auth("service-token"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(str(payload.get("active_provider")), "deterministic")
        providers = payload.get("providers", {})
        self.assertIn("deterministic", providers)


if __name__ == "__main__":
    unittest.main()
