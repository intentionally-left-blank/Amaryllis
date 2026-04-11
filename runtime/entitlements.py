from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from runtime.config import AppConfig
from runtime.provider_sessions import SUPPORTED_PROVIDER_SESSION_PROVIDERS
from storage.database import Database

ENTITLEMENT_ROUTE_POLICY_VERSION = "provider_route_policy_v1"
ENTITLEMENT_ERROR_CONTRACT_VERSION = "provider_entitlement_error_v1"
ENTITLEMENT_DIAGNOSTICS_VERSION = "provider_entitlement_diagnostics_v1"
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


def _build_failure_signatures(
    *,
    provider: str,
) -> list[dict[str, Any]]:
    normalized_provider = str(provider or "").strip().lower()
    return [
        {
            "id": "provider_access_not_configured",
            "error_class": "entitlement",
            "provider": normalized_provider,
            "http_status": 403,
            "matchers": [
                "Provider entitlement denied",
                "error_code=provider_access_not_configured",
                "selected_route=none",
            ],
            "hint": "Create provider session or configure server API key.",
        },
        {
            "id": "provider_chat_disabled",
            "error_class": "entitlement",
            "provider": normalized_provider,
            "http_status": 403,
            "matchers": [
                "Provider entitlement denied",
                "error_code=provider_chat_disabled",
            ],
            "hint": "Enable chat feature for selected provider entitlement path.",
        },
    ]


def _build_session_diagnostics_summary(
    *,
    sessions: list[dict[str, Any]],
    selected_route: str,
) -> dict[str, Any]:
    normalized_sessions = [item for item in sessions if isinstance(item, dict)]
    active = [item for item in normalized_sessions if str(item.get("status") or "").strip().lower() != "revoked"]
    revoked = [item for item in normalized_sessions if str(item.get("status") or "").strip().lower() == "revoked"]

    def _latest_timestamp(rows: list[dict[str, Any]], *keys: str) -> str | None:
        candidates: list[str] = []
        for row in rows:
            for key in keys:
                value = str(row.get(key) or "").strip()
                if value:
                    candidates.append(value)
        if not candidates:
            return None
        return max(candidates)

    revoked_reason_counts: dict[str, int] = {}
    for item in revoked:
        reason = str(item.get("revoked_reason") or "").strip()
        if not reason:
            reason = "unspecified"
        revoked_reason_counts[reason] = int(revoked_reason_counts.get(reason, 0)) + 1
    revoked_reasons = [
        {"reason": key, "count": int(value)}
        for key, value in sorted(revoked_reason_counts.items(), key=lambda pair: (-pair[1], pair[0]))[:10]
    ]

    scopes_union = sorted(
        {
            str(scope or "").strip().lower()
            for item in active
            for scope in (item.get("scopes") if isinstance(item.get("scopes"), list) else [])
            if str(scope or "").strip()
        }
    )

    sample = []
    for item in normalized_sessions[:5]:
        sample.append(
            {
                "id": str(item.get("id") or ""),
                "status": str(item.get("status") or ""),
                "display_name": str(item.get("display_name") or "") or None,
                "credential_ref_hint": str(item.get("credential_ref_hint") or "") or None,
                "scopes": list(item.get("scopes") or []),
                "created_at": str(item.get("created_at") or "") or None,
                "updated_at": str(item.get("updated_at") or "") or None,
                "last_used_at": str(item.get("last_used_at") or "") or None,
                "revoked_at": str(item.get("revoked_at") or "") or None,
            }
        )

    return {
        "total_count": len(normalized_sessions),
        "active_count": len(active),
        "revoked_count": len(revoked),
        "selected_route": str(selected_route or "none"),
        "last_active_at": _latest_timestamp(active, "last_used_at", "updated_at"),
        "last_revoked_at": _latest_timestamp(revoked, "revoked_at", "updated_at"),
        "active_scopes_union": scopes_union,
        "revoked_reasons": revoked_reasons,
        "sample": sample,
    }


def _build_diagnostic_checks(
    *,
    provider_payload: dict[str, Any],
    session_summary: dict[str, Any],
) -> list[dict[str, Any]]:
    route_policy = provider_payload.get("route_policy")
    route_policy = route_policy if isinstance(route_policy, dict) else {}
    error_contract = provider_payload.get("error_contract")
    error_contract = error_contract if isinstance(error_contract, dict) else {}
    feature_flags = provider_payload.get("feature_flags")
    feature_flags = feature_flags if isinstance(feature_flags, dict) else {}
    selected_route = str(route_policy.get("selected_route") or "none").strip().lower() or "none"
    available = bool(provider_payload.get("available"))
    server_key_available = bool(provider_payload.get("server_key_available"))
    session_count = int(provider_payload.get("session_count") or 0)
    allowed_models = list(provider_payload.get("allowed_models") or [])
    active_count = int(session_summary.get("active_count") or 0)

    checks: list[dict[str, Any]] = []

    route_ok = selected_route != "none"
    checks.append(
        {
            "id": "route_selected",
            "status": "pass" if route_ok else "fail",
            "severity": "info" if route_ok else "error",
            "code": str(error_contract.get("error_code") or "provider_access_not_configured") if not route_ok else "route_selected",
            "message": (
                "Provider route selected successfully."
                if route_ok
                else "Provider route is not configured."
            ),
            "evidence": {
                "selected_route": selected_route,
                "available_routes": list(route_policy.get("available_routes") or []),
                "decision_reason": str(route_policy.get("decision_reason") or ""),
            },
        }
    )

    if selected_route == "user_session":
        route_consistent = session_count > 0 and active_count > 0
    elif selected_route == "server_api_key":
        route_consistent = server_key_available
    else:
        route_consistent = (session_count <= 0) and (not server_key_available)
    checks.append(
        {
            "id": "route_consistency",
            "status": "pass" if route_consistent else "fail",
            "severity": "warning" if route_consistent else "error",
            "code": "route_state_mismatch" if not route_consistent else "route_state_consistent",
            "message": (
                "Route state matches entitlement evidence."
                if route_consistent
                else "Route state is inconsistent with entitlement evidence."
            ),
            "evidence": {
                "selected_route": selected_route,
                "session_count": session_count,
                "active_session_count": active_count,
                "server_key_available": server_key_available,
            },
        }
    )

    chat_enabled = bool(feature_flags.get("chat"))
    checks.append(
        {
            "id": "chat_feature_enabled",
            "status": "pass" if chat_enabled else "fail",
            "severity": "info" if chat_enabled else ("error" if available else "warning"),
            "code": "chat_enabled" if chat_enabled else "provider_chat_disabled",
            "message": (
                "Chat feature is enabled for selected entitlement route."
                if chat_enabled
                else "Chat feature is disabled or blocked for this entitlement state."
            ),
            "evidence": {
                "available": available,
                "chat_feature": chat_enabled,
                "selected_route": selected_route,
            },
        }
    )

    models_ok = (not available) or bool(allowed_models)
    checks.append(
        {
            "id": "allowed_models_present",
            "status": "pass" if models_ok else "fail",
            "severity": "info" if models_ok else "warning",
            "code": "allowed_models_present" if models_ok else "allowed_models_missing",
            "message": (
                "Allowed model list is available."
                if models_ok
                else "No allowed models found for available entitlement route."
            ),
            "evidence": {
                "available": available,
                "allowed_models_count": len(allowed_models),
            },
        }
    )

    return checks


def _resolve_diagnostics_status(
    *,
    available: bool,
    checks: list[dict[str, Any]],
) -> str:
    if not available:
        return "blocked"
    has_fail = any(str(item.get("status") or "") == "fail" for item in checks if isinstance(item, dict))
    return "degraded" if has_fail else "ready"


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

    def resolve_provider_diagnostics(
        self,
        *,
        user_id: str,
        provider: str,
        session_limit: int = 50,
    ) -> dict[str, Any]:
        payload = self.resolve_provider(user_id=user_id, provider=provider)
        normalized_user = str(payload.get("user_id") or "").strip()
        normalized_provider = str(payload.get("provider") or "").strip().lower()
        normalized_limit = max(1, min(int(session_limit), 500))

        session_rows = self.database.list_provider_sessions(
            user_id=normalized_user,
            provider=normalized_provider,
            include_revoked=True,
            limit=normalized_limit,
        )
        route_policy = payload.get("route_policy")
        route_policy = route_policy if isinstance(route_policy, dict) else {}
        selected_route = str(route_policy.get("selected_route") or "none").strip().lower() or "none"

        session_summary = _build_session_diagnostics_summary(
            sessions=session_rows,
            selected_route=selected_route,
        )
        checks = _build_diagnostic_checks(
            provider_payload=payload,
            session_summary=session_summary,
        )
        diagnostics_status = _resolve_diagnostics_status(
            available=bool(payload.get("available")),
            checks=checks,
        )
        failing_checks = [
            item
            for item in checks
            if isinstance(item, dict) and str(item.get("status") or "") == "fail"
        ]

        return {
            "version": ENTITLEMENT_DIAGNOSTICS_VERSION,
            "provider": normalized_provider,
            "user_id": normalized_user,
            "status": diagnostics_status,
            "summary": {
                "available": bool(payload.get("available")),
                "access_mode": str(payload.get("access_mode") or "none"),
                "selected_route": selected_route,
                "error_code": str((payload.get("error_contract") or {}).get("error_code") or "") or None,
                "checks_total": len(checks),
                "checks_failed": len(failing_checks),
            },
            "entitlement": {
                "available": bool(payload.get("available")),
                "access_mode": str(payload.get("access_mode") or "none"),
                "session_count": int(payload.get("session_count") or 0),
                "server_key_available": bool(payload.get("server_key_available")),
                "allowed_models": list(payload.get("allowed_models") or []),
                "rate_tier": str(payload.get("rate_tier") or "none"),
                "feature_flags": dict(payload.get("feature_flags") or {}),
            },
            "route_policy": route_policy,
            "error_contract": dict(payload.get("error_contract") or {}),
            "session_summary": session_summary,
            "checks": checks,
            "next_actions": list(((payload.get("onboarding") or {}).get("next_actions") or [])),
            "failure_signatures": _build_failure_signatures(provider=normalized_provider),
            "checked_at": str(payload.get("checked_at") or _utc_now_iso()),
        }

    def resolve_all_diagnostics(
        self,
        *,
        user_id: str,
        session_limit: int = 50,
    ) -> dict[str, Any]:
        items = [
            self.resolve_provider_diagnostics(
                user_id=user_id,
                provider=provider,
                session_limit=session_limit,
            )
            for provider in SUPPORTED_PROVIDER_SESSION_PROVIDERS
        ]
        status_counts: dict[str, int] = {"ready": 0, "degraded": 0, "blocked": 0}
        for item in items:
            key = str(item.get("status") or "blocked").strip().lower()
            if key not in status_counts:
                status_counts[key] = 0
            status_counts[key] = int(status_counts.get(key, 0)) + 1
        return {
            "version": ENTITLEMENT_DIAGNOSTICS_VERSION,
            "user_id": str(user_id or "").strip(),
            "items": items,
            "count": len(items),
            "status_counts": status_counts,
            "checked_at": _utc_now_iso(),
        }
