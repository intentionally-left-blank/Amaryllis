from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any, Iterator

from models.model_manager import ModelManager
from models.provider_errors import classify_provider_error
from runtime.config import AppConfig
from storage.database import Database


class _ScriptedProvider:
    def __init__(self, name: str, *, local: bool, scripted_chat: list[Any]) -> None:
        self.name = name
        self.local = local
        self.scripted_chat = list(scripted_chat)
        self.chat_calls = 0
        self.stream_calls = 0

    def list_models(self) -> list[dict[str, Any]]:
        return [
            {
                "id": f"{self.name}-model",
                "provider": self.name,
                "active": True,
                "metadata": {},
            }
        ]

    def health_check(self) -> dict[str, Any]:
        return {"status": "ok", "detail": "scripted"}

    def capabilities(self) -> dict[str, Any]:
        return {
            "local": self.local,
            "supports_download": self.local,
            "supports_load": True,
            "supports_stream": True,
            "supports_tools": False,
            "requires_api_key": not self.local,
        }

    def suggested_models(self, limit: int = 20) -> list[dict[str, str]]:
        return [{"id": f"{self.name}-model", "label": f"{self.name}-model"}]

    def download_model(self, model_id: str) -> dict[str, Any]:
        return {"status": "downloaded", "provider": self.name, "model": model_id}

    def load_model(self, model_id: str) -> dict[str, Any]:
        return {"status": "loaded", "provider": self.name, "model": model_id}

    def chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> str:
        del messages, model, temperature, max_tokens
        self.chat_calls += 1
        if not self.scripted_chat:
            return "ok"
        index = min(self.chat_calls - 1, len(self.scripted_chat) - 1)
        event = self.scripted_chat[index]
        if isinstance(event, Exception):
            raise event
        return str(event)

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> Iterator[str]:
        del messages, model, temperature, max_tokens
        self.stream_calls += 1
        yield self.chat([], "")


class ModelFailoverTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-model-failover-")
        self.base = Path(self._tmp.name)
        self._original_env = os.environ.copy()

        os.environ["AMARYLLIS_SUPPORT_DIR"] = str(self.base / "support")
        os.environ["AMARYLLIS_DATA_DIR"] = str(self.base / "support" / "data")
        os.environ["AMARYLLIS_MODELS_DIR"] = str(self.base / "support" / "models")
        os.environ["AMARYLLIS_PLUGINS_DIR"] = str(self.base / "plugins")
        os.environ["AMARYLLIS_DATABASE_PATH"] = str(self.base / "support" / "data" / "state.db")
        os.environ["AMARYLLIS_VECTOR_INDEX_PATH"] = str(self.base / "support" / "data" / "semantic.index")
        os.environ["AMARYLLIS_TELEMETRY_PATH"] = str(self.base / "support" / "data" / "telemetry.jsonl")
        os.environ["AMARYLLIS_OLLAMA_FALLBACK"] = "true"
        os.environ["AMARYLLIS_DEFAULT_PROVIDER"] = "openai"
        os.environ["AMARYLLIS_DEFAULT_MODEL"] = "mlx-community/Qwen2.5-1.5B-Instruct-4bit"
        os.environ["AMARYLLIS_OPENAI_API_KEY"] = "test-key"
        os.environ["AMARYLLIS_PROVIDER_RETRY_ATTEMPTS"] = "1"

        self.config = AppConfig.from_env()
        self.config.ensure_directories()
        self.database = Database(self.config.database_path)
        self.manager = ModelManager(config=self.config, database=self.database)

    def tearDown(self) -> None:
        self.database.close()
        os.environ.clear()
        os.environ.update(self._original_env)
        self._tmp.cleanup()

    def test_rate_limit_failover_prefers_local_and_sets_session_pin(self) -> None:
        openai = _ScriptedProvider(
            "openai",
            local=False,
            scripted_chat=[RuntimeError("429 Too Many Requests"), "cloud-ok"],
        )
        mlx = _ScriptedProvider(
            "mlx",
            local=True,
            scripted_chat=["local-ok", "local-ok-2"],
        )
        self.manager.providers = {"openai": openai, "mlx": mlx}
        self.manager.active_provider = "openai"
        self.manager.active_model = "gpt-4o-mini"

        result = self.manager.chat(
            messages=[{"role": "user", "content": "hello"}],
            provider="openai",
            model="gpt-4o-mini",
            session_id="session-A",
        )
        self.assertEqual(result["provider"], "mlx")
        self.assertTrue(bool(result.get("fallback", False)))
        routing = result.get("routing", {})
        failovers = routing.get("failover_events", [])
        self.assertTrue(failovers)
        self.assertEqual(failovers[0].get("error_class"), "rate_limit")
        self.assertEqual(openai.chat_calls, 1)
        self.assertEqual(mlx.chat_calls, 1)

        pinned = self.manager.chat(
            messages=[{"role": "user", "content": "next"}],
            session_id="session-A",
        )
        self.assertEqual(pinned["provider"], "mlx")
        self.assertEqual(openai.chat_calls, 1)
        self.assertEqual(mlx.chat_calls, 2)
        selected = dict((pinned.get("routing") or {}).get("selected") or {})
        self.assertEqual(selected.get("reason"), "session_pin")

        debug = self.manager.debug_failover_state(session_id="session-A", limit=20)
        self.assertEqual((debug.get("selected_pin") or {}).get("provider"), "mlx")
        self.assertGreaterEqual(int(debug.get("recent_failovers_count", 0)), 1)

    def test_error_taxonomy_detects_auth_and_quota(self) -> None:
        auth = classify_provider_error(
            provider="openai",
            operation="chat",
            error=RuntimeError("401 Unauthorized: invalid API key"),
        )
        self.assertEqual(auth.error_class, "auth")
        self.assertFalse(auth.retryable)

        quota = classify_provider_error(
            provider="openai",
            operation="chat",
            error=RuntimeError("insufficient_quota for this request"),
        )
        self.assertEqual(quota.error_class, "quota")
        self.assertFalse(quota.retryable)


if __name__ == "__main__":
    unittest.main()
