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
class APILifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-api-lifecycle-")
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
                "AMARYLLIS_API_VERSION": "v1",
                "AMARYLLIS_RELEASE_CHANNEL": "stable",
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

    def test_legacy_endpoint_has_deprecation_headers(self) -> None:
        response = self.client.get("/models", headers=self._auth("user-token"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("Deprecation"), "true")
        self.assertTrue(str(response.headers.get("Sunset", "")).strip())
        self.assertEqual(response.headers.get("X-Amaryllis-API-Version"), "v1")
        self.assertEqual(response.headers.get("X-Amaryllis-Release-Channel"), "stable")

    def test_versioned_endpoint_available_without_deprecation_header(self) -> None:
        response = self.client.get("/v1/models", headers=self._auth("user-token"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Amaryllis-API-Version"), "v1")
        self.assertNotEqual(response.headers.get("Deprecation"), "true")

    def test_canonical_auth_is_applied_for_versioned_debug_endpoint(self) -> None:
        response = self.client.get("/v1/debug/models/failover", headers=self._auth("user-token"))
        self.assertEqual(response.status_code, 403)
        payload = response.json()
        self.assertEqual(payload["error"]["type"], "permission_denied")
        self.assertIn("Admin scope is required", payload["error"]["message"])

    def test_service_api_lifecycle_endpoint(self) -> None:
        response = self.client.get("/service/api/lifecycle", headers=self._auth("service-token"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        policy = payload.get("policy", {})
        self.assertEqual(str(policy.get("version")), "v1")
        self.assertEqual(str(policy.get("release_channel")), "stable")

    def test_service_observability_endpoints_require_service_scope(self) -> None:
        denied = self.client.get("/service/observability/slo", headers=self._auth("user-token"))
        self.assertEqual(denied.status_code, 403)
        denied_qos = self.client.get("/service/qos", headers=self._auth("user-token"))
        self.assertEqual(denied_qos.status_code, 403)

        allowed = self.client.get("/service/observability/slo", headers=self._auth("service-token"))
        self.assertEqual(allowed.status_code, 200)
        payload = allowed.json()
        self.assertIn("snapshot", payload)
        profiles = payload.get("profiles", {})
        self.assertEqual(str(profiles.get("runtime")), "dev")
        self.assertEqual(str(profiles.get("slo")), "dev")
        quality_budget = payload.get("quality_budget", {})
        self.assertIn("perf_max_p95_latency_ms", quality_budget)
        self.assertIn("perf_max_error_rate_pct", quality_budget)
        qos = payload.get("qos", {})
        self.assertIn(str(qos.get("active_mode")), {"quality", "balanced", "power_save"})
        self.assertIn(str(qos.get("route_mode")), {"quality_first", "balanced", "local_first"})
        self.assertIn(str(qos.get("thermal_state")), {"unknown", "cool", "warm", "hot", "critical"})

        qos_get = self.client.get("/service/qos", headers=self._auth("service-token"))
        self.assertEqual(qos_get.status_code, 200)
        qos_payload = qos_get.json().get("qos", {})
        self.assertIn(str(qos_payload.get("active_mode")), {"quality", "balanced", "power_save"})
        self.assertIn(str(qos_payload.get("thermal_state")), {"unknown", "cool", "warm", "hot", "critical"})

        qos_set = self.client.post(
            "/service/qos/mode",
            headers=self._auth("service-token"),
            json={"mode": "power_save", "auto_enabled": False, "thermal_state": "hot"},
        )
        self.assertEqual(qos_set.status_code, 200)
        qos_set_payload = qos_set.json().get("qos", {})
        self.assertEqual(str(qos_set_payload.get("active_mode")), "power_save")
        self.assertFalse(bool(qos_set_payload.get("auto_enabled", True)))
        self.assertEqual(str(qos_set_payload.get("thermal_state")), "hot")

        qos_set_invalid = self.client.post(
            "/service/qos/mode",
            headers=self._auth("service-token"),
            json={"mode": "ultra"},
        )
        self.assertEqual(qos_set_invalid.status_code, 400)

        qos_thermal_set = self.client.post(
            "/service/qos/thermal",
            headers=self._auth("service-token"),
            json={"thermal_state": "cool"},
        )
        self.assertEqual(qos_thermal_set.status_code, 200)
        qos_thermal_payload = qos_thermal_set.json().get("qos", {})
        self.assertEqual(str(qos_thermal_payload.get("thermal_state")), "cool")

        qos_thermal_invalid = self.client.post(
            "/service/qos/thermal",
            headers=self._auth("service-token"),
            json={"thermal_state": "lava"},
        )
        self.assertEqual(qos_thermal_invalid.status_code, 400)

        metrics = self.client.get("/service/observability/metrics", headers=self._auth("service-token"))
        self.assertEqual(metrics.status_code, 200)
        self.assertIn("amaryllis_request_availability_ratio", metrics.text)
        self.assertIn("amaryllis_release_quality_snapshot_loaded", metrics.text)
        self.assertIn("amaryllis_nightly_mission_snapshot_loaded", metrics.text)


if __name__ == "__main__":
    unittest.main()
