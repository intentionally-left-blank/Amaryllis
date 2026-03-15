from __future__ import annotations

import importlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Callable, Iterator
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover
    TestClient = None  # type: ignore[assignment]


class _FakeDownloadProvider:
    active_model: str | None = None

    def list_models(self) -> list[dict[str, Any]]:
        return []

    def suggested_models(self, limit: int = 100) -> list[dict[str, Any]]:
        _ = limit
        return [{"id": "fake/model", "label": "Fake Model", "size_bytes": 100}]

    def capabilities(self) -> dict[str, Any]:
        return {
            "local": True,
            "supports_download": True,
            "supports_load": True,
            "supports_stream": True,
            "supports_tools": False,
            "requires_api_key": False,
        }

    def health_check(self) -> dict[str, Any]:
        return {"status": "ok", "detail": "fake"}

    def download_model(
        self,
        model_id: str,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        if callable(progress_callback):
            progress_callback(
                {
                    "status": "running",
                    "progress": 0.5,
                    "completed_bytes": 50,
                    "total_bytes": 100,
                    "message": "mid",
                }
            )
        time.sleep(0.05)
        return {
            "status": "downloaded",
            "provider": "mlx",
            "model": model_id,
            "size_bytes": 100,
        }

    def load_model(self, model_id: str) -> dict[str, Any]:
        self.active_model = model_id
        return {"status": "loaded", "provider": "mlx", "model": model_id}

    def chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> str:
        _ = (messages, model, temperature, max_tokens)
        return "ok"

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> Iterator[str]:
        _ = (messages, model, temperature, max_tokens)
        return iter(["ok"])


@unittest.skipIf(TestClient is None, "fastapi dependency is not available")
class ModelAPIDownloadJobsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-model-api-download-")
        support_dir = Path(cls._tmp.name) / "support"
        auth_tokens = {
            "admin-token": {
                "user_id": "admin",
                "scopes": ["admin", "user"],
            }
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
        cls.client_cm = TestClient(cls.server_module.app)
        cls.client = cls.client_cm.__enter__()
        cls.server_module.app.state.services.model_manager.providers = {"mlx": _FakeDownloadProvider()}
        cls.server_module.app.state.services.model_manager.active_provider = "mlx"
        cls.server_module.app.state.services.model_manager.active_model = "fake/model"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.client_cm.__exit__(None, None, None)
        cls._env_patch.stop()
        cls._tmp.cleanup()

    @staticmethod
    def _auth() -> dict[str, str]:
        return {"Authorization": "Bearer admin-token"}

    def test_download_job_start_and_poll(self) -> None:
        start = self.client.post(
            "/models/download/start",
            headers=self._auth(),
            json={"model_id": "fake/model", "provider": "mlx"},
        )
        self.assertEqual(start.status_code, 200)
        start_payload = start.json()
        job_id = str(start_payload["job"]["id"])

        deadline = time.time() + 3.0
        final_payload: dict[str, Any] | None = None
        while time.time() < deadline:
            response = self.client.get(f"/models/download/{job_id}", headers=self._auth())
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            status = str(payload["job"]["status"])
            if status in {"succeeded", "failed"}:
                final_payload = payload
                break
            time.sleep(0.05)

        self.assertIsNotNone(final_payload)
        assert final_payload is not None
        self.assertEqual(final_payload["job"]["status"], "succeeded")
        self.assertEqual(final_payload["job"]["progress"], 1.0)


if __name__ == "__main__":
    unittest.main()
