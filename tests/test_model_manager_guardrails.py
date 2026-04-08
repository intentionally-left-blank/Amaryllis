from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any, Iterator

from models.model_manager import ModelManager
from runtime.config import AppConfig
from storage.database import Database


class _FakeCloudProvider:
    def __init__(self) -> None:
        self.calls = 0

    def chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> str:
        self.calls += 1
        return f"ok:{model}:{len(messages)}:{max_tokens}:{temperature}"

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> Iterator[str]:
        self.calls += 1
        return iter(["ok"])

    def list_models(self) -> list[dict[str, Any]]:
        return [{"id": "fake-model", "provider": "openai", "active": True, "metadata": {}}]

    def health_check(self) -> dict[str, Any]:
        return {"status": "ok", "detail": "fake"}

    def capabilities(self) -> dict[str, Any]:
        return {
            "local": False,
            "supports_download": False,
            "supports_load": True,
            "supports_stream": True,
            "supports_tools": False,
            "requires_api_key": True,
        }


class _FakeEntitlementResolver:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = dict(payload)
        self.calls: list[dict[str, Any]] = []

    def resolve_provider(self, *, user_id: str, provider: str) -> dict[str, Any]:
        self.calls.append({"user_id": user_id, "provider": provider})
        return dict(self.payload)


class ModelManagerGuardrailsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-model-guardrails-")
        self.base = Path(self._tmp.name)
        self._original_env = os.environ.copy()

        os.environ["AMARYLLIS_SUPPORT_DIR"] = str(self.base / "support")
        os.environ["AMARYLLIS_DATA_DIR"] = str(self.base / "support" / "data")
        os.environ["AMARYLLIS_MODELS_DIR"] = str(self.base / "support" / "models")
        os.environ["AMARYLLIS_PLUGINS_DIR"] = str(self.base / "plugins")
        os.environ["AMARYLLIS_DATABASE_PATH"] = str(self.base / "support" / "data" / "state.db")
        os.environ["AMARYLLIS_VECTOR_INDEX_PATH"] = str(self.base / "support" / "data" / "semantic.index")
        os.environ["AMARYLLIS_TELEMETRY_PATH"] = str(self.base / "support" / "data" / "telemetry.jsonl")
        os.environ["AMARYLLIS_OPENAI_API_KEY"] = "test-key"
        os.environ["AMARYLLIS_DEFAULT_PROVIDER"] = "openai"
        os.environ["AMARYLLIS_DEFAULT_MODEL"] = "fake-model"
        os.environ["AMARYLLIS_CLOUD_RATE_WINDOW_SEC"] = "60"
        os.environ["AMARYLLIS_CLOUD_RATE_MAX_REQUESTS"] = "2"
        os.environ["AMARYLLIS_CLOUD_BUDGET_WINDOW_SEC"] = "3600"
        os.environ["AMARYLLIS_CLOUD_BUDGET_MAX_UNITS"] = "1000"
        os.environ["AMARYLLIS_AUTH_TOKENS"] = "token-1:user-1:user"

        self.config = AppConfig.from_env()
        self.config.ensure_directories()
        self.database = Database(self.config.database_path)
        self.manager = ModelManager(config=self.config, database=self.database)
        self.fake = _FakeCloudProvider()
        self.manager.providers = {"openai": self.fake}
        self.manager.active_provider = "openai"
        self.manager.active_model = "fake-model"

    def tearDown(self) -> None:
        self.database.close()
        os.environ.clear()
        os.environ.update(self._original_env)
        self._tmp.cleanup()

    def test_cloud_rate_guardrail_blocks_excess_requests(self) -> None:
        messages = [{"role": "user", "content": "hello"}]

        self.manager.chat(messages=messages, provider="openai", model="fake-model", max_tokens=16)
        self.manager.chat(messages=messages, provider="openai", model="fake-model", max_tokens=16)

        with self.assertRaises(RuntimeError) as ctx:
            self.manager.chat(messages=messages, provider="openai", model="fake-model", max_tokens=16)

        self.assertIn("rate limit", str(ctx.exception).lower())
        self.assertEqual(self.fake.calls, 2)

    def test_cloud_budget_guardrail_blocks_excess_units(self) -> None:
        os.environ["AMARYLLIS_CLOUD_RATE_MAX_REQUESTS"] = "100"
        os.environ["AMARYLLIS_CLOUD_BUDGET_MAX_UNITS"] = "110"
        cfg = AppConfig.from_env()
        manager = ModelManager(config=cfg, database=self.database)
        fake = _FakeCloudProvider()
        manager.providers = {"openai": fake}
        manager.active_provider = "openai"
        manager.active_model = "fake-model"

        messages = [{"role": "user", "content": "x" * 40}]
        manager.chat(messages=messages, provider="openai", model="fake-model", max_tokens=5)

        with self.assertRaises(RuntimeError) as ctx:
            manager.chat(messages=messages, provider="openai", model="fake-model", max_tokens=5)

        self.assertIn("budget limit", str(ctx.exception).lower())
        self.assertEqual(fake.calls, 1)

    def test_cloud_entitlement_denied_blocks_provider_call(self) -> None:
        resolver = _FakeEntitlementResolver(
            {
                "provider": "openai",
                "available": False,
                "feature_flags": {"chat": False},
            }
        )
        manager = ModelManager(config=self.config, database=self.database, entitlement_resolver=resolver)
        fake = _FakeCloudProvider()
        manager.providers = {"openai": fake}
        manager.active_provider = "openai"
        manager.active_model = "fake-model"

        with self.assertRaises(RuntimeError) as ctx:
            manager.chat(
                messages=[{"role": "user", "content": "hello"}],
                provider="openai",
                model="fake-model",
                max_tokens=16,
                user_id="user-1",
            )

        self.assertIn("entitlement", str(ctx.exception).lower())
        self.assertIn("error_code=provider_access_not_configured", str(ctx.exception))
        self.assertEqual(fake.calls, 0)
        self.assertEqual(len(resolver.calls), 1)
        self.assertEqual(resolver.calls[0]["provider"], "openai")
        self.assertEqual(resolver.calls[0]["user_id"], "user-1")

    def test_cloud_entitlement_allows_provider_call_when_available(self) -> None:
        resolver = _FakeEntitlementResolver(
            {
                "provider": "openai",
                "available": True,
                "feature_flags": {"chat": True},
            }
        )
        manager = ModelManager(config=self.config, database=self.database, entitlement_resolver=resolver)
        fake = _FakeCloudProvider()
        manager.providers = {"openai": fake}
        manager.active_provider = "openai"
        manager.active_model = "fake-model"

        response = manager.chat(
            messages=[{"role": "user", "content": "hello"}],
            provider="openai",
            model="fake-model",
            max_tokens=16,
            user_id="user-1",
        )

        self.assertEqual(fake.calls, 1)
        self.assertEqual(str(response.get("provider")), "openai")
        self.assertEqual(len(resolver.calls), 1)


if __name__ == "__main__":
    unittest.main()
