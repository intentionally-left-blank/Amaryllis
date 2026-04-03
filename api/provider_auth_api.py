from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, Field

from runtime.auth import assert_owner, auth_context_from_request, resolve_user_id
from runtime.errors import AmaryllisError, ProviderError, ValidationError
from runtime.provider_sessions import SUPPORTED_PROVIDER_SESSION_PROVIDERS

router = APIRouter(tags=["provider-auth"])


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
        ],
        "notes": [
            "credential_ref stores external secret reference, not raw provider token",
            "entitlements are evaluated from server key availability and active provider sessions",
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
