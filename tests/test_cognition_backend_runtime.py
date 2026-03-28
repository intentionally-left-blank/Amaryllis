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
