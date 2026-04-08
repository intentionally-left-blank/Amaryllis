from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, Field

from runtime.auth import assert_owner, auth_context_from_request, resolve_user_id
from runtime.entitlements import ENTITLEMENT_ROUTE_POLICY_VERSION
from runtime.errors import AmaryllisError, ProviderError, ValidationError
from runtime.provider_sessions import SUPPORTED_PROVIDER_SESSION_PROVIDERS

router = APIRouter(tags=["provider-auth"])
PROVIDER_AUTH_ONBOARDING_VERSION = "provider_auth_onboarding_v1"
PROVIDER_AUTH_ROUTE_POLICY_VERSION = ENTITLEMENT_ROUTE_POLICY_VERSION


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


def _raise_provider_auth_error(exc: Exception) -> None:
    if isinstance(exc, AmaryllisError):
        raise exc
    if isinstance(exc, ValueError):
        raise ValidationError(str(exc)) from exc
    raise ProviderError(str(exc)) from exc


def _sign_action(
    request: Request,
    *,
    action: str,
    payload: dict[str, Any],
    actor: str | None,
    status: str = "succeeded",
    details: dict[str, Any] | None = None,
    target_id: str | None = None,
) -> dict[str, Any]:
    services = request.app.state.services
    try:
        return services.security_manager.signed_action(
            action=action,
            payload=payload,
            request_id=_request_id(request),
            actor=actor,
            target_type="provider_session",
            target_id=target_id,
            status=status,
            details=details,
        )
    except Exception:
        return {}


class ProviderSessionCreateRequest(BaseModel):
    user_id: str | None = None
    provider: str = Field(min_length=1, max_length=64)
    credential_ref: str = Field(min_length=1, max_length=400)
    display_name: str | None = Field(default=None, max_length=255)
    scopes: list[str] = Field(default_factory=list)
    expires_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProviderSessionRevokeRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=2000)


def _build_provider_onboarding_card(
    *,
    services: Any,
    user_id: str,
    provider: str,
) -> dict[str, Any]:
    entitlement = services.entitlement_resolver.resolve_provider(
        user_id=user_id,
        provider=provider,
    )
    onboarding = entitlement.get("onboarding")
    if not isinstance(onboarding, dict):
        onboarding = {}
    normalized_provider = str(provider or "").strip().lower()
    normalized_user = str(user_id or "").strip()
    credential_ref_example = f"secret://vault/{normalized_provider}/{normalized_user or 'user-id'}"
    return {
        "provider": normalized_provider,
        "user_id": normalized_user,
        "status": str(onboarding.get("status") or ("ready" if entitlement.get("available") else "setup_required")),
        "reason_codes": list(onboarding.get("reason_codes") or []),
        "next_actions": list(onboarding.get("next_actions") or []),
        "route_policy": entitlement.get("route_policy", {}),
        "error_contract": entitlement.get("error_contract", {}),
        "entitlement": entitlement,
        "security_hints": [
            "Store only credential_ref (never raw provider tokens) in session payloads.",
            "Use minimal scopes required for your automation or chat workflow.",
            "Revoke sessions immediately on credential rotation or suspected compromise.",
        ],
        "examples": {
            "create_session": {
                "endpoint": "/auth/providers/sessions",
                "method": "POST",
                "payload": {
                    "user_id": normalized_user,
                    "provider": normalized_provider,
                    "credential_ref": credential_ref_example,
                    "scopes": ["chat"],
                },
            },
            "verify_entitlements": {
                "endpoint": "/auth/providers/entitlements",
                "method": "GET",
                "query": {"user_id": normalized_user, "provider": normalized_provider},
            },
        },
    }


def _build_provider_route_policy_card(
    *,
    services: Any,
    user_id: str,
    provider: str,
) -> dict[str, Any]:
    entitlement = services.entitlement_resolver.resolve_provider(
        user_id=user_id,
        provider=provider,
    )
    route_policy = entitlement.get("route_policy")
    if not isinstance(route_policy, dict):
        route_policy = {}
    error_contract = entitlement.get("error_contract")
    if not isinstance(error_contract, dict):
        error_contract = {}
    return {
        "provider": str(provider or "").strip().lower(),
        "user_id": str(user_id or "").strip(),
        "available": bool(entitlement.get("available")),
        "access_mode": str(entitlement.get("access_mode") or "none"),
        "route_policy": route_policy,
        "error_contract": error_contract,
        "checked_at": entitlement.get("checked_at"),
    }


@router.get("/auth/providers/contract")
def provider_auth_contract(request: Request) -> dict[str, Any]:
    auth_context_from_request(request)
    return {
        "contract_version": "provider_auth_v1",
        "request_id": _request_id(request),
        "providers": list(SUPPORTED_PROVIDER_SESSION_PROVIDERS),
        "session_endpoints": [
            {"method": "POST", "path": "/auth/providers/sessions"},
            {"method": "GET", "path": "/auth/providers/sessions"},
            {"method": "POST", "path": "/auth/providers/sessions/{session_id}/revoke"},
            {"method": "GET", "path": "/auth/providers/entitlements"},
            {"method": "GET", "path": "/auth/providers/onboarding"},
            {"method": "GET", "path": "/auth/providers/routing-policy"},
        ],
        "notes": [
            "credential_ref stores external secret reference, not raw provider token",
            "entitlements are evaluated from server key availability and active provider sessions",
            "use onboarding endpoint for step-by-step provider setup and entitlement feedback",
            "routing-policy endpoint exposes deterministic session-vs-server-key route selection",
        ],
    }


@router.post("/auth/providers/sessions")
def create_provider_session(payload: ProviderSessionCreateRequest, request: Request) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=payload.user_id, auth=auth)
    try:
        created = services.provider_session_manager.create_session(
            user_id=effective_user_id,
            provider=payload.provider,
            credential_ref=payload.credential_ref,
            scopes=payload.scopes,
            display_name=payload.display_name,
            expires_at=payload.expires_at,
            metadata=payload.metadata,
        )
    except Exception as exc:
        _sign_action(
            request,
            action="provider_session_create",
            payload={**payload.model_dump(), "user_id": effective_user_id},
            actor=auth.user_id,
            status="failed",
            details={"error": str(exc)},
        )
        _raise_provider_auth_error(exc)
    receipt = _sign_action(
        request,
        action="provider_session_create",
        payload={**payload.model_dump(), "user_id": effective_user_id},
        actor=auth.user_id,
        target_id=str(created.get("id") or ""),
    )
    return {
        "session": created,
        "action_receipt": receipt,
        "request_id": _request_id(request),
    }


@router.get("/auth/providers/sessions")
def list_provider_sessions(
    request: Request,
    user_id: str | None = Query(default=None),
    provider: str | None = Query(default=None),
    include_revoked: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=user_id, auth=auth)
    try:
        items = services.provider_session_manager.list_sessions(
            user_id=effective_user_id,
            provider=provider,
            include_revoked=include_revoked,
            limit=limit,
        )
    except Exception as exc:
        _raise_provider_auth_error(exc)
    return {
        "items": items,
        "count": len(items),
        "request_id": _request_id(request),
    }


@router.post("/auth/providers/sessions/{session_id}/revoke")
def revoke_provider_session(
    payload: ProviderSessionRevokeRequest,
    request: Request,
    session_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    try:
        current = services.provider_session_manager.get_session(session_id)
        assert_owner(
            owner_user_id=str(current.get("user_id") or ""),
            auth=auth,
            resource_name="provider_session",
            resource_id=session_id,
        )
        updated = services.provider_session_manager.revoke_session(
            session_id=session_id,
            actor_user_id=auth.user_id,
            is_admin=auth.is_admin,
            reason=payload.reason,
        )
    except Exception as exc:
        _sign_action(
            request,
            action="provider_session_revoke",
            payload={"session_id": session_id, "reason": payload.reason},
            actor=auth.user_id,
            status="failed",
            details={"error": str(exc)},
            target_id=session_id,
        )
        _raise_provider_auth_error(exc)
    receipt = _sign_action(
        request,
        action="provider_session_revoke",
        payload={"session_id": session_id, "reason": payload.reason},
        actor=auth.user_id,
        target_id=session_id,
    )
    return {
        "session": updated,
        "action_receipt": receipt,
        "request_id": _request_id(request),
    }


@router.get("/auth/providers/entitlements")
def provider_entitlements(
    request: Request,
    user_id: str | None = Query(default=None),
    provider: str | None = Query(default=None),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=user_id, auth=auth)
    try:
        if provider:
            payload = services.entitlement_resolver.resolve_provider(
                user_id=effective_user_id,
                provider=provider,
            )
        else:
            payload = services.entitlement_resolver.resolve_all(
                user_id=effective_user_id,
            )
    except Exception as exc:
        _raise_provider_auth_error(exc)
    return {
        **payload,
        "request_id": _request_id(request),
    }


@router.get("/auth/providers/onboarding")
def provider_onboarding(
    request: Request,
    user_id: str | None = Query(default=None),
    provider: str | None = Query(default=None),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=user_id, auth=auth)
    normalized_provider = str(provider or "").strip().lower() or None
    try:
        if normalized_provider:
            if normalized_provider not in set(SUPPORTED_PROVIDER_SESSION_PROVIDERS):
                raise ValidationError(
                    "Unsupported provider. Allowed: " + ", ".join(SUPPORTED_PROVIDER_SESSION_PROVIDERS)
                )
            card = _build_provider_onboarding_card(
                services=services,
                user_id=effective_user_id,
                provider=normalized_provider,
            )
            return {
                "contract_version": PROVIDER_AUTH_ONBOARDING_VERSION,
                "user_id": effective_user_id,
                "provider": normalized_provider,
                "card": card,
                "request_id": _request_id(request),
            }

        items = [
            _build_provider_onboarding_card(
                services=services,
                user_id=effective_user_id,
                provider=item,
            )
            for item in SUPPORTED_PROVIDER_SESSION_PROVIDERS
        ]
        ready_count = sum(1 for item in items if str(item.get("status") or "") == "ready")
        return {
            "contract_version": PROVIDER_AUTH_ONBOARDING_VERSION,
            "user_id": effective_user_id,
            "items": items,
            "count": len(items),
            "ready_count": ready_count,
            "request_id": _request_id(request),
        }
    except Exception as exc:
        _raise_provider_auth_error(exc)


@router.get("/auth/providers/routing-policy")
def provider_routing_policy(
    request: Request,
    user_id: str | None = Query(default=None),
    provider: str | None = Query(default=None),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=user_id, auth=auth)
    normalized_provider = str(provider or "").strip().lower() or None
    try:
        if normalized_provider:
            if normalized_provider not in set(SUPPORTED_PROVIDER_SESSION_PROVIDERS):
                raise ValidationError(
                    "Unsupported provider. Allowed: " + ", ".join(SUPPORTED_PROVIDER_SESSION_PROVIDERS)
                )
            card = _build_provider_route_policy_card(
                services=services,
                user_id=effective_user_id,
                provider=normalized_provider,
            )
            return {
                "contract_version": PROVIDER_AUTH_ROUTE_POLICY_VERSION,
                "user_id": effective_user_id,
                "provider": normalized_provider,
                "card": card,
                "request_id": _request_id(request),
            }

        items = [
            _build_provider_route_policy_card(
                services=services,
                user_id=effective_user_id,
                provider=item,
            )
            for item in SUPPORTED_PROVIDER_SESSION_PROVIDERS
        ]
        ready_count = sum(1 for item in items if bool(item.get("available")))
        return {
            "contract_version": PROVIDER_AUTH_ROUTE_POLICY_VERSION,
            "user_id": effective_user_id,
            "items": items,
            "count": len(items),
            "ready_count": ready_count,
            "request_id": _request_id(request),
        }
    except Exception as exc:
        _raise_provider_auth_error(exc)
