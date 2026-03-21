from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, Field

from flow.session_manager import UNIFIED_SESSION_CHANNELS, UNIFIED_SESSION_STATES
from runtime.auth import assert_owner, auth_context_from_request, resolve_user_id
from runtime.errors import AmaryllisError, NotFoundError, ProviderError, ValidationError

router = APIRouter(tags=["flow"])


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


def _sign_action(
    request: Request,
    *,
    action: str,
    payload: dict[str, Any],
    actor: str | None = None,
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
            target_type="flow_session",
            target_id=target_id,
            status=status,
            details=details,
        )
    except Exception:
        return {}


class FlowSessionStartRequest(BaseModel):
    user_id: str | None = None
    channels: list[str] = Field(default_factory=lambda: ["text"])
    initial_state: str = Field(default="created")
    metadata: dict[str, Any] = Field(default_factory=dict)


class FlowSessionTransitionRequest(BaseModel):
    to_state: str = Field(min_length=1)
    reason: str = Field(default="state_transition", min_length=1, max_length=512)
    metadata: dict[str, Any] = Field(default_factory=dict)


class FlowSessionActivityRequest(BaseModel):
    channel: str = Field(min_length=1)
    event: str = Field(min_length=1, max_length=512)
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.get("/flow/sessions/contract")
def flow_sessions_contract(request: Request) -> dict[str, Any]:
    _ = auth_context_from_request(request)
    return {
        "states": sorted(UNIFIED_SESSION_STATES),
        "channels": sorted(UNIFIED_SESSION_CHANNELS),
        "request_id": _request_id(request),
    }


@router.post("/flow/sessions/start")
def start_flow_session(payload: FlowSessionStartRequest, request: Request) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=payload.user_id, auth=auth)
    sign_payload = {
        **payload.model_dump(),
        "user_id": effective_user_id,
    }
    try:
        session = services.flow_session_manager.start_session(
            user_id=effective_user_id,
            channels=list(payload.channels),
            metadata=payload.metadata,
            initial_state=payload.initial_state,
            request_id=_request_id(request),
            actor=auth.user_id,
        )
        receipt = _sign_action(
            request,
            action="flow_session_start",
            payload=sign_payload,
            actor=auth.user_id,
            target_id=str(session.get("id") or ""),
        )
        return {
            "flow_session": session,
            "action_receipt": receipt,
            "request_id": _request_id(request),
            "supported_states": sorted(UNIFIED_SESSION_STATES),
            "supported_channels": sorted(UNIFIED_SESSION_CHANNELS),
        }
    except ValueError as exc:
        _sign_action(
            request,
            action="flow_session_start",
            payload=sign_payload,
            actor=auth.user_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ValidationError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        _sign_action(
            request,
            action="flow_session_start",
            payload=sign_payload,
            actor=auth.user_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc


@router.get("/flow/sessions")
def list_flow_sessions(
    request: Request,
    user_id: str | None = Query(default=None),
    state: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=2000),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=user_id, auth=auth)
    try:
        items = services.flow_session_manager.list_sessions(
            user_id=effective_user_id,
            state=state,
            limit=limit,
        )
        return {
            "items": items,
            "count": len(items),
            "request_id": _request_id(request),
            "supported_states": sorted(UNIFIED_SESSION_STATES),
            "supported_channels": sorted(UNIFIED_SESSION_CHANNELS),
        }
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.get("/flow/sessions/{session_id}")
def get_flow_session(
    request: Request,
    session_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    try:
        session = services.flow_session_manager.get_session(session_id=session_id)
        assert_owner(
            owner_user_id=str(session.get("user_id") or ""),
            auth=auth,
            resource_name="flow_session",
            resource_id=session_id,
        )
        return {
            "flow_session": session,
            "request_id": _request_id(request),
            "supported_states": sorted(UNIFIED_SESSION_STATES),
            "supported_channels": sorted(UNIFIED_SESSION_CHANNELS),
        }
    except ValueError as exc:
        if "not found" in str(exc).lower():
            raise NotFoundError(str(exc)) from exc
        raise ValidationError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.post("/flow/sessions/{session_id}/transition")
def transition_flow_session(
    payload: FlowSessionTransitionRequest,
    request: Request,
    session_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    sign_payload = {
        "session_id": session_id,
        **payload.model_dump(exclude_none=True),
    }
    try:
        existing = services.flow_session_manager.get_session(session_id=session_id)
        assert_owner(
            owner_user_id=str(existing.get("user_id") or ""),
            auth=auth,
            resource_name="flow_session",
            resource_id=session_id,
        )
        session = services.flow_session_manager.transition_session(
            session_id=session_id,
            to_state=payload.to_state,
            reason=payload.reason,
            actor=auth.user_id,
            metadata=payload.metadata,
            request_id=_request_id(request),
        )
        receipt = _sign_action(
            request,
            action="flow_session_transition",
            payload=sign_payload,
            actor=auth.user_id,
            target_id=session_id,
        )
        return {
            "flow_session": session,
            "action_receipt": receipt,
            "request_id": _request_id(request),
            "supported_states": sorted(UNIFIED_SESSION_STATES),
            "supported_channels": sorted(UNIFIED_SESSION_CHANNELS),
        }
    except ValueError as exc:
        if "not found" in str(exc).lower():
            raise NotFoundError(str(exc)) from exc
        _sign_action(
            request,
            action="flow_session_transition",
            payload=sign_payload,
            actor=auth.user_id,
            target_id=session_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ValidationError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        _sign_action(
            request,
            action="flow_session_transition",
            payload=sign_payload,
            actor=auth.user_id,
            target_id=session_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc


@router.post("/flow/sessions/{session_id}/activity")
def record_flow_session_activity(
    payload: FlowSessionActivityRequest,
    request: Request,
    session_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    sign_payload = {
        "session_id": session_id,
        **payload.model_dump(exclude_none=True),
    }
    try:
        existing = services.flow_session_manager.get_session(session_id=session_id)
        assert_owner(
            owner_user_id=str(existing.get("user_id") or ""),
            auth=auth,
            resource_name="flow_session",
            resource_id=session_id,
        )
        session = services.flow_session_manager.record_activity(
            session_id=session_id,
            channel=payload.channel,
            event=payload.event,
            actor=auth.user_id,
            metadata=payload.metadata,
            request_id=_request_id(request),
        )
        receipt = _sign_action(
            request,
            action="flow_session_activity",
            payload=sign_payload,
            actor=auth.user_id,
            target_id=session_id,
        )
        return {
            "flow_session": session,
            "action_receipt": receipt,
            "request_id": _request_id(request),
            "supported_states": sorted(UNIFIED_SESSION_STATES),
            "supported_channels": sorted(UNIFIED_SESSION_CHANNELS),
        }
    except ValueError as exc:
        if "not found" in str(exc).lower():
            raise NotFoundError(str(exc)) from exc
        _sign_action(
            request,
            action="flow_session_activity",
            payload=sign_payload,
            actor=auth.user_id,
            target_id=session_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ValidationError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        _sign_action(
            request,
            action="flow_session_activity",
            payload=sign_payload,
            actor=auth.user_id,
            target_id=session_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc
