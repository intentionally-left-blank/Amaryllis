from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, Field

from runtime.auth import assert_owner, auth_context_from_request, resolve_user_id
from runtime.errors import AmaryllisError, NotFoundError, ProviderError, ValidationError
from voice.session_manager import VOICE_SESSION_MODES, VOICE_SESSION_STATES

router = APIRouter(tags=["voice"])


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
            target_type="voice_session",
            target_id=target_id,
            status=status,
            details=details,
        )
    except Exception:
        return {}


class VoiceSessionStartRequest(BaseModel):
    user_id: str | None = None
    mode: str = Field(default="ptt")
    input_device: str | None = Field(default=None, max_length=256)
    sample_rate_hz: int = Field(default=16_000, ge=8_000, le=96_000)
    language: str | None = Field(default=None, max_length=64)
    metadata: dict[str, Any] = Field(default_factory=dict)


class VoiceSessionStopRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=256)


@router.post("/voice/sessions/start")
def start_voice_session(payload: VoiceSessionStartRequest, request: Request) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=payload.user_id, auth=auth)
    sign_payload = {**payload.model_dump(), "user_id": effective_user_id}

    try:
        session = services.voice_session_manager.start_session(
            user_id=effective_user_id,
            mode=payload.mode,
            input_device=payload.input_device,
            sample_rate_hz=payload.sample_rate_hz,
            language=payload.language,
            metadata=payload.metadata,
            request_id=_request_id(request),
        )
        receipt = _sign_action(
            request,
            action="voice_session_start",
            payload=sign_payload,
            actor=auth.user_id,
            target_id=str(session.get("id") or ""),
        )
        return {
            "voice_session": session,
            "action_receipt": receipt,
            "request_id": _request_id(request),
            "supported_modes": sorted(VOICE_SESSION_MODES),
            "supported_states": sorted(VOICE_SESSION_STATES),
        }
    except ValueError as exc:
        _sign_action(
            request,
            action="voice_session_start",
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
            action="voice_session_start",
            payload=sign_payload,
            actor=auth.user_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc


@router.get("/voice/sessions")
def list_voice_sessions(
    request: Request,
    user_id: str | None = Query(default=None),
    state: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=user_id, auth=auth)
    try:
        items = services.voice_session_manager.list_sessions(
            user_id=effective_user_id,
            state=state,
            limit=limit,
        )
        return {
            "items": items,
            "count": len(items),
            "request_id": _request_id(request),
            "supported_modes": sorted(VOICE_SESSION_MODES),
            "supported_states": sorted(VOICE_SESSION_STATES),
        }
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.get("/voice/sessions/{session_id}")
def get_voice_session(
    request: Request,
    session_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    try:
        session = services.voice_session_manager.get_session(session_id=session_id)
        assert_owner(
            owner_user_id=str(session.get("user_id") or ""),
            auth=auth,
            resource_name="voice_session",
            resource_id=session_id,
        )
        return {
            "voice_session": session,
            "request_id": _request_id(request),
            "supported_modes": sorted(VOICE_SESSION_MODES),
            "supported_states": sorted(VOICE_SESSION_STATES),
        }
    except ValueError as exc:
        raise NotFoundError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.post("/voice/sessions/{session_id}/stop")
def stop_voice_session(
    payload: VoiceSessionStopRequest,
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
        existing = services.voice_session_manager.get_session(session_id=session_id)
        assert_owner(
            owner_user_id=str(existing.get("user_id") or ""),
            auth=auth,
            resource_name="voice_session",
            resource_id=session_id,
        )
        session = services.voice_session_manager.stop_session(
            session_id=session_id,
            reason=payload.reason,
            actor=auth.user_id,
            request_id=_request_id(request),
        )
        receipt = _sign_action(
            request,
            action="voice_session_stop",
            payload=sign_payload,
            actor=auth.user_id,
            target_id=session_id,
        )
        return {
            "voice_session": session,
            "action_receipt": receipt,
            "request_id": _request_id(request),
            "supported_modes": sorted(VOICE_SESSION_MODES),
            "supported_states": sorted(VOICE_SESSION_STATES),
        }
    except ValueError as exc:
        _sign_action(
            request,
            action="voice_session_stop",
            payload=sign_payload,
            actor=auth.user_id,
            target_id=session_id,
            status="failed",
            details={"error": str(exc)},
        )
        if "not found" in str(exc).lower():
            raise NotFoundError(str(exc)) from exc
        raise ValidationError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        _sign_action(
            request,
            action="voice_session_stop",
            payload=sign_payload,
            actor=auth.user_id,
            target_id=session_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc
