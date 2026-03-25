from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import unittest
from typing import Any

from runtime.privacy_transparency import (
    PRIVACY_TRANSPARENCY_CONTRACT_VERSION,
    build_privacy_transparency_contract,
)


@dataclass(frozen=True)
class _FakeConfig:
    observability_otel_enabled: bool
    observability_otlp_endpoint: str | None
    telemetry_path: Path
    mcp_endpoints: tuple[str, ...]
    openai_base_url: str
    openrouter_base_url: str
    anthropic_base_url: str


class _FakeModelManager:
    def __init__(
        self,
        *,
        active_provider: str,
        active_model: str,
        capabilities: dict[str, Any],
    ) -> None:
        self.active_provider = active_provider
        self.active_model = active_model
        self._capabilities = capabilities

    def provider_capabilities(self) -> dict[str, Any]:
        return self._capabilities


class _FakeObservability:
    def __init__(self, *, otel_available: bool) -> None:
        self._otel_available = otel_available


def _intent_by_id(payload: dict[str, Any], intent_id: str) -> dict[str, Any]:
    intents = payload.get("network_intents")
    if not isinstance(intents, list):
        return {}
    for item in intents:
        if isinstance(item, dict) and str(item.get("id")) == intent_id:
            return item
    return {}


class PrivacyTransparencyContractTests(unittest.TestCase):
    def test_local_provider_reports_offline_ready_with_local_only_telemetry(self) -> None:
        config = _FakeConfig(
            observability_otel_enabled=False,
            observability_otlp_endpoint=None,
            telemetry_path=Path("/tmp/amaryllis/events.jsonl"),
            mcp_endpoints=(),
            openai_base_url="https://api.openai.com/v1",
            openrouter_base_url="https://openrouter.ai/api/v1",
            anthropic_base_url="https://api.anthropic.com/v1",
        )
        model_manager = _FakeModelManager(
            active_provider="mlx",
            active_model="mlx-community/model-4bit",
            capabilities={
                "mlx": {"local": True, "supports_download": True},
                "openai": {"local": False, "supports_download": False},
            },
        )

        payload = build_privacy_transparency_contract(config=config, model_manager=model_manager, observability=None)
        self.assertEqual(payload["contract_version"], PRIVACY_TRANSPARENCY_CONTRACT_VERSION)
        offline = payload["offline"]
        self.assertTrue(bool(offline["offline_possible"]))
        self.assertTrue(bool(offline["offline_ready_now"]))
        self.assertFalse(bool(offline["network_required_now"]))
        self.assertEqual(offline["local_providers"], ["mlx"])
        self.assertEqual(offline["cloud_providers"], ["openai"])
        telemetry = payload["telemetry"]
        self.assertEqual(str(telemetry["mode"]), "local_only")
        self.assertFalse(bool(telemetry["export_enabled"]))
        otel_intent = _intent_by_id(payload, "observability.otel_export")
        self.assertFalse(bool(otel_intent.get("requires_network", True)))

    def test_cloud_active_provider_marks_network_required_and_lists_mcp_intent(self) -> None:
        config = _FakeConfig(
            observability_otel_enabled=False,
            observability_otlp_endpoint=None,
            telemetry_path=Path("/tmp/amaryllis/events.jsonl"),
            mcp_endpoints=("https://tools.example.org/mcp",),
            openai_base_url="https://api.openai.com/v1",
            openrouter_base_url="https://openrouter.ai/api/v1",
            anthropic_base_url="https://api.anthropic.com/v1",
        )
        model_manager = _FakeModelManager(
            active_provider="openai",
            active_model="gpt-4o-mini",
            capabilities={
                "openai": {"local": False, "supports_download": False},
                "ollama": {"local": True, "supports_download": True},
            },
        )

        payload = build_privacy_transparency_contract(config=config, model_manager=model_manager, observability=None)
        offline = payload["offline"]
        self.assertTrue(bool(offline["network_required_now"]))
        cloud_intent = _intent_by_id(payload, "chat.cloud_inference")
        self.assertTrue(bool(cloud_intent.get("requires_network", False)))
        self.assertIn("https://api.openai.com/v1", cloud_intent.get("destinations", []))
        mcp_intent = _intent_by_id(payload, "tools.mcp_remote")
        self.assertTrue(bool(mcp_intent.get("requires_network", False)))
        self.assertIn("https://tools.example.org/mcp", mcp_intent.get("destinations", []))

    def test_otel_intent_is_networked_only_when_export_enabled_and_endpoint_set(self) -> None:
        config = _FakeConfig(
            observability_otel_enabled=True,
            observability_otlp_endpoint="http://otel-collector:4318/v1/traces",
            telemetry_path=Path("/tmp/amaryllis/events.jsonl"),
            mcp_endpoints=(),
            openai_base_url="https://api.openai.com/v1",
            openrouter_base_url="https://openrouter.ai/api/v1",
            anthropic_base_url="https://api.anthropic.com/v1",
        )
        model_manager = _FakeModelManager(
            active_provider="mlx",
            active_model="model",
            capabilities={
                "mlx": {"local": True, "supports_download": True},
            },
        )
        observability = _FakeObservability(otel_available=True)

        payload = build_privacy_transparency_contract(
            config=config,
            model_manager=model_manager,
            observability=observability,
        )
        telemetry = payload["telemetry"]
        self.assertTrue(bool(telemetry["export_enabled"]))
        self.assertTrue(bool(telemetry["export_active"]))
        self.assertEqual(str(telemetry["export_endpoint"]), "http://otel-collector:4318/v1/traces")
        otel_intent = _intent_by_id(payload, "observability.otel_export")
        self.assertTrue(bool(otel_intent.get("requires_network", False)))
        self.assertIn("http://otel-collector:4318/v1/traces", otel_intent.get("destinations", []))


if __name__ == "__main__":
    unittest.main()
