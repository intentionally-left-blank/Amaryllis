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

    def test_service_health_reports_backend_provider(self) -> None:
        response = self.client.get("/service/health", headers=self._auth("service-token"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(str(payload.get("active_provider")), "deterministic")
        providers = payload.get("providers", {})
        self.assertIn("deterministic", providers)


if __name__ == "__main__":
    unittest.main()
