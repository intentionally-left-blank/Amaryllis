from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from runtime.config import AppConfig

PRIVACY_TRANSPARENCY_CONTRACT_VERSION = "privacy_offline_transparency_v1"


def build_privacy_transparency_contract(
    *,
    config: AppConfig,
    model_manager: Any,
    observability: Any | None = None,
) -> dict[str, Any]:
    active_provider = str(getattr(model_manager, "active_provider", "") or "").strip() or "unknown"
    active_model = str(getattr(model_manager, "active_model", "") or "").strip() or "unknown"
    provider_capabilities = _provider_capabilities(model_manager)

    local_providers: list[str] = []
    cloud_providers: list[str] = []
    for provider_name, payload in provider_capabilities.items():
        cap = payload if isinstance(payload, dict) else {}
        if bool(cap.get("local", False)):
            local_providers.append(str(provider_name))
        else:
            cloud_providers.append(str(provider_name))
    local_providers = sorted(set(local_providers))
    cloud_providers = sorted(set(cloud_providers))

    active_cap = provider_capabilities.get(active_provider, {})
    active_cap_dict = active_cap if isinstance(active_cap, dict) else {}
    active_provider_local = bool(active_cap_dict.get("local", False))
    offline_possible = bool(local_providers)
    network_required_now = bool(active_provider not in {"", "unknown"} and not active_provider_local)
    offline_ready_now = bool(offline_possible and active_provider_local)

    otel_enabled = bool(config.observability_otel_enabled)
    otlp_endpoint = str(config.observability_otlp_endpoint or "").strip() or None
    export_active = bool(getattr(observability, "_otel_available", False)) if observability is not None else False

    network_intents = _network_intents(
        config=config,
        cloud_providers=cloud_providers,
        provider_capabilities=provider_capabilities,
        otel_enabled=otel_enabled,
        otlp_endpoint=otlp_endpoint,
    )

    return {
        "contract_version": PRIVACY_TRANSPARENCY_CONTRACT_VERSION,
        "generated_at": _utc_now_iso(),
        "active": {
            "provider": active_provider,
            "model": active_model,
        },
        "offline": {
            "preferred_mode": "local_first",
            "offline_possible": offline_possible,
            "offline_ready_now": offline_ready_now,
            "network_required_now": network_required_now,
            "active_provider_local": active_provider_local,
            "local_providers": local_providers,
            "cloud_providers": cloud_providers,
        },
        "telemetry": {
            "mode": "local_plus_export" if otel_enabled else "local_only",
            "local_events_enabled": True,
            "local_events_path": str(config.telemetry_path),
            "export_opt_in_default": True,
            "export_enabled": otel_enabled,
            "export_active": export_active,
            "export_endpoint": otlp_endpoint,
        },
        "network_intents": network_intents,
        "policy_docs": [
            {
                "id": "privacy_offline_transparency",
                "path": "/docs/privacy-offline-transparency",
            },
            {
                "id": "observability_sre",
                "path": "/docs/observability-sre",
            },
        ],
    }


def _provider_capabilities(model_manager: Any) -> dict[str, Any]:
    getter = getattr(model_manager, "provider_capabilities", None)
    if not callable(getter):
        return {}
    try:
        payload = getter()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _network_intents(
    *,
    config: AppConfig,
    cloud_providers: list[str],
    provider_capabilities: dict[str, Any],
    otel_enabled: bool,
    otlp_endpoint: str | None,
) -> list[dict[str, Any]]:
    intents: list[dict[str, Any]] = [
        {
            "id": "chat.local_inference",
            "label": "Local chat inference",
            "requires_network": False,
            "when": "Active provider is local (mlx/ollama or equivalent).",
            "destinations": [],
            "controls": [
                "Use local provider in Models.",
            ],
        }
    ]

    cloud_destinations = _cloud_destinations(config=config, cloud_providers=cloud_providers)
    intents.append(
        {
            "id": "chat.cloud_inference",
            "label": "Cloud provider inference",
            "requires_network": bool(cloud_providers),
            "when": "Active provider is cloud (openai/openrouter/anthropic).",
            "destinations": cloud_destinations,
            "controls": [
                "Switch active model/provider to local.",
                "Remove cloud API keys from runtime settings.",
            ],
        }
    )

    download_intents: list[dict[str, Any]] = []
    for provider_name, payload in provider_capabilities.items():
        cap = payload if isinstance(payload, dict) else {}
        if not bool(cap.get("supports_download", False)):
            continue
        download_intents.append(
            {
                "id": f"model.download.{provider_name}",
                "label": f"Model download via {provider_name}",
                "requires_network": True,
                "when": "Triggered by model install/package install.",
                "destinations": [],
                "controls": [
                    "Skip install/download actions while offline.",
                ],
            }
        )
    intents.extend(download_intents)

    mcp_destinations = [str(item) for item in config.mcp_endpoints]
    intents.append(
        {
            "id": "tools.mcp_remote",
            "label": "Remote MCP tool endpoints",
            "requires_network": bool(mcp_destinations),
            "when": "MCP endpoints are configured.",
            "destinations": mcp_destinations,
            "controls": [
                "Clear AMARYLLIS_MCP_ENDPOINTS to force local-only tool path.",
            ],
        }
    )

    intents.append(
        {
            "id": "observability.otel_export",
            "label": "OpenTelemetry export",
            "requires_network": bool(otel_enabled and otlp_endpoint),
            "when": "Enabled only when AMARYLLIS_OTEL_ENABLED=true and endpoint is configured.",
            "destinations": [otlp_endpoint] if otlp_endpoint else [],
            "controls": [
                "Set AMARYLLIS_OTEL_ENABLED=false to keep telemetry local only.",
            ],
        }
    )

    return intents


def _cloud_destinations(*, config: AppConfig, cloud_providers: list[str]) -> list[str]:
    targets: list[str] = []
    for provider_name in cloud_providers:
        if provider_name == "openai":
            targets.append(str(config.openai_base_url))
        elif provider_name == "openrouter":
            targets.append(str(config.openrouter_base_url))
        elif provider_name == "anthropic":
            targets.append(str(config.anthropic_base_url))
    normalized = [item.strip() for item in targets if item and item.strip()]
    return sorted(set(normalized))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
