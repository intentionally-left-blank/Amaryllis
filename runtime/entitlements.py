from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from runtime.config import AppConfig
from runtime.provider_sessions import SUPPORTED_PROVIDER_SESSION_PROVIDERS
from storage.database import Database

ENTITLEMENT_ROUTE_POLICY_VERSION = "provider_route_policy_v1"
ENTITLEMENT_ERROR_CONTRACT_VERSION = "provider_entitlement_error_v1"
_SERVER_KEY_ROUTE_PROVIDERS = {"openai", "anthropic", "openrouter"}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _build_route_policy(
    *,
    provider: str,
    session_count: int,
    server_key_available: bool,
) -> dict[str, Any]:
    normalized_provider = str(provider or "").strip().lower()
    supports_server_key = normalized_provider in _SERVER_KEY_ROUTE_PROVIDERS
    has_user_session = int(session_count) > 0
    has_server_key = bool(server_key_available and supports_server_key)

    preferred_order: list[str] = ["user_session"]
    if supports_server_key:
        preferred_order.append("server_api_key")

    route_table = {
        "user_session": {
            "configured": has_user_session,
            "available": has_user_session,
        },
        "server_api_key": {
            "configured": has_server_key,
            "available": has_server_key,
            "supported": supports_server_key,
        },
    }
    available_routes = [route for route in preferred_order if bool(route_table.get(route, {}).get("available"))]
    selected_route = available_routes[0] if available_routes else "none"
    fallback_routes = available_routes[1:] if len(available_routes) > 1 else []

    decision_reason = "provider_access_missing"
    if selected_route == "user_session":
        if has_server_key:
            decision_reason = "user_session_preferred_over_server_key"
        else:
            decision_reason = "user_session_only"
    elif selected_route == "server_api_key":
        decision_reason = "server_api_key_selected"

    return {
        "version": ENTITLEMENT_ROUTE_POLICY_VERSION,
        "provider": normalized_provider,
        "preferred_order": preferred_order,
        "available_routes": available_routes,
        "selected_route": selected_route,
        "fallback_routes": fallback_routes,
        "decision_reason": decision_reason,
        "routes": route_table,
    }


def _build_entitlement_error_contract(
    *,
    provider: str,
    route_policy: dict[str, Any],
) -> dict[str, Any]:
    selected_route = str(route_policy.get("selected_route") or "none").strip().lower() or "none"
    available_routes = list(route_policy.get("available_routes") or [])
    if selected_route != "none":
        return {
            "version": ENTITLEMENT_ERROR_CONTRACT_VERSION,
            "status": "ok",
            "error_type": None,
            "error_code": None,
            "http_status": None,
            "message": None,
            "provider": str(provider or "").strip().lower(),
            "available_routes": available_routes,
            "next_actions": [],
        }

    return {
        "version": ENTITLEMENT_ERROR_CONTRACT_VERSION,
        "status": "error",
        "error_type": "entitlement_setup_required",
        "error_code": "provider_access_not_configured",
        "http_status": 403,
        "message": (
            "Provider access is not configured. "
            "Create a provider session or configure a server API key."
        ),
        "provider": str(provider or "").strip().lower(),
        "available_routes": available_routes,
        "next_actions": [
            {
                "id": "create_provider_session",
                "title": "Create provider session",
                "description": "Connect per-user provider access via credential_ref.",
                "endpoint": "/auth/providers/sessions",
                "method": "POST",
            },
            {
                "id": "configure_server_key",
                "title": "Configure server API key",
                "description": "Set provider API key on server runtime if shared cloud route is required.",
                "endpoint": "/auth/providers/entitlements",
                "method": "GET",
            },
        ],
    }


def _build_onboarding_feedback(
    *,
    provider: str,
    available: bool,
    access_mode: str,
    session_count: int,
    server_key_available: bool,
    route_policy: dict[str, Any],
    error_contract: dict[str, Any],
) -> dict[str, Any]:
    reason_codes: list[str] = []
    if available:
        if access_mode == "user_session":
            reason_codes.append("user_session_active")
        elif access_mode == "server_api_key":
            reason_codes.append("server_api_key_available")
        else:
            reason_codes.append("provider_access_available")
    else:
        reason_codes.append("missing_provider_access")
        if str(error_contract.get("error_code") or "").strip():
            reason_codes.append(str(error_contract.get("error_code") or "").strip())

    next_actions: list[dict[str, Any]] = []
    if not available:
        recovery_actions = error_contract.get("next_actions")
        if isinstance(recovery_actions, list):
            next_actions.extend(item for item in recovery_actions if isinstance(item, dict))
        next_actions.append(
            {
                "id": "verify_entitlements",
                "title": "Verify entitlement",
                "description": "Re-check provider entitlement after session/key setup.",
                "endpoint": "/auth/providers/entitlements",
                "method": "GET",
            }
        )
    elif session_count > 0:
        next_actions.append(
            {
                "id": "session_hygiene",
                "title": "Maintain session hygiene",
                "description": "Revoke stale sessions and rotate credential_ref when needed.",
                "endpoint": "/auth/providers/sessions/{session_id}/revoke",
                "method": "POST",
            }
        )
    elif server_key_available:
        next_actions.append(
            {
                "id": "optional_user_passthrough",
                "title": "Optional user passthrough",
                "description": "Create a user provider session if per-user passthrough or isolation is required.",
                "endpoint": "/auth/providers/sessions",
                "method": "POST",
            }
        )

    return {
        "status": "ready" if available else "setup_required",
        "reason_codes": reason_codes,
        "next_actions": next_actions,
        "provider": provider,
        "route_policy": {
            "version": str(route_policy.get("version") or ""),
            "selected_route": str(route_policy.get("selected_route") or "none"),
            "fallback_routes": list(route_policy.get("fallback_routes") or []),
            "decision_reason": str(route_policy.get("decision_reason") or ""),
        },
    }


@dataclass
class EntitlementResolver:
    config: AppConfig
    database: Database

    def _server_key_available(self, provider: str) -> bool:
        if provider == "openai":
            return bool(self.config.openai_api_key)
        if provider == "anthropic":
            return bool(self.config.anthropic_api_key)
        if provider == "openrouter":
            return bool(self.config.openrouter_api_key)
        return False

    def resolve_provider(self, *, user_id: str, provider: str) -> dict[str, Any]:
        normalized_user = str(user_id or "").strip()
        normalized_provider = str(provider or "").strip().lower()
        if normalized_provider not in set(SUPPORTED_PROVIDER_SESSION_PROVIDERS):
            raise ValueError("Unsupported provider")

        sessions = self.database.list_provider_sessions(
            user_id=normalized_user,
            provider=normalized_provider,
            include_revoked=False,
            limit=50,
        )
        session_count = len(sessions)
        has_server_key = self._server_key_available(normalized_provider)
        route_policy = _build_route_policy(
            provider=normalized_provider,
            session_count=session_count,
            server_key_available=has_server_key,
        )
        selected_route = str(route_policy.get("selected_route") or "none").strip().lower() or "none"
        available = selected_route != "none"
        access_mode = selected_route if selected_route in {"user_session", "server_api_key"} else "none"
        error_contract = _build_entitlement_error_contract(
            provider=normalized_provider,
            route_policy=route_policy,
        )

        allowed_models: list[str] = []
        if normalized_provider == "openai":
            allowed_models = [str(self.database.get_setting("openai_default_model", "gpt-4o-mini") or "gpt-4o-mini")]
        elif normalized_provider == "anthropic":
            allowed_models = [
                str(
                    self.database.get_setting("anthropic_default_model", "claude-3-5-sonnet-latest")
                    or "claude-3-5-sonnet-latest"
                )
            ]
        elif normalized_provider == "openrouter":
            allowed_models = [
                str(
                    self.database.get_setting("openrouter_default_model", "openai/gpt-4o-mini")
                    or "openai/gpt-4o-mini"
                )
            ]

        return {
            "provider": normalized_provider,
            "user_id": normalized_user,
            "available": available,
            "access_mode": access_mode,
            "session_count": session_count,
            "server_key_available": has_server_key,
            "allowed_models": allowed_models,
            "rate_tier": "standard" if available else "none",
            "feature_flags": {
                "chat": available,
                "automation_news": available,
                "passthrough": session_count > 0,
            },
            "route_policy": route_policy,
            "error_contract": error_contract,
            "onboarding": _build_onboarding_feedback(
                provider=normalized_provider,
                available=available,
                access_mode=access_mode,
                session_count=session_count,
                server_key_available=has_server_key,
                route_policy=route_policy,
                error_contract=error_contract,
            ),
            "checked_at": _utc_now_iso(),
        }

    def resolve_all(self, *, user_id: str) -> dict[str, Any]:
        items = [
            self.resolve_provider(user_id=user_id, provider=provider)
            for provider in SUPPORTED_PROVIDER_SESSION_PROVIDERS
        ]
        available = [item["provider"] for item in items if bool(item.get("available"))]
        return {
            "user_id": str(user_id or "").strip(),
            "providers": items,
            "available_providers": available,
            "count": len(items),
            "checked_at": _utc_now_iso(),
        }
