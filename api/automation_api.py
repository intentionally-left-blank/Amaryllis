from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, Field

from runtime.errors import NotFoundError, ProviderError, ValidationError

router = APIRouter(tags=["automations"])


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


def _sign_action(
    request: Request,
    *,
    action: str,
    payload: dict[str, Any],
    actor: str | None = None,
    target_id: str | None = None,
    status: str = "succeeded",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    services = request.app.state.services
    try:
        return services.security_manager.signed_action(
            action=action,
            payload=payload,
            request_id=_request_id(request),
            actor=actor,
            target_type="automation",
            target_id=target_id,
            status=status,
            details=details,
        )
    except Exception:
        return {}


class CreateAutomationRequest(BaseModel):
    agent_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    session_id: str | None = None
    interval_sec: int | None = Field(default=None, ge=10, le=86400)
    schedule_type: str | None = Field(default=None)
    schedule: dict[str, Any] = Field(default_factory=dict)
    timezone: str = Field(default="UTC", min_length=1)
    start_immediately: bool = False


@router.post("/automations/create")
def create_automation(payload: CreateAutomationRequest, request: Request) -> dict[str, Any]:
    services = request.app.state.services
    try:
        automation = services.automation_scheduler.create_automation(
            agent_id=payload.agent_id,
            user_id=payload.user_id,
            session_id=payload.session_id,
            message=payload.message,
            interval_sec=payload.interval_sec,
            schedule_type=payload.schedule_type,
            schedule=payload.schedule,
            timezone_name=payload.timezone,
            start_immediately=payload.start_immediately,
        )
        receipt = _sign_action(
            request,
            action="automation_create",
            payload=payload.model_dump(),
            actor=payload.user_id,
            target_id=str(automation.get("id")),
        )
        return {
            "automation": automation,
            "action_receipt": receipt,
            "request_id": _request_id(request),
        }
    except ValueError as exc:
        _sign_action(
            request,
            action="automation_create",
            payload=payload.model_dump(),
            actor=payload.user_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ValidationError(str(exc)) from exc
    except Exception as exc:
        _sign_action(
            request,
            action="automation_create",
            payload=payload.model_dump(),
            actor=payload.user_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc


class UpdateAutomationRequest(BaseModel):
    message: str | None = None
    session_id: str | None = None
    interval_sec: int | None = Field(default=None, ge=10, le=86400)
    schedule_type: str | None = None
    schedule: dict[str, Any] | None = None
    timezone: str | None = None


@router.post("/automations/{automation_id}/update")
def update_automation(
    payload: UpdateAutomationRequest,
    request: Request,
    automation_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    try:
        automation = services.automation_scheduler.update_automation(
            automation_id=automation_id,
            message=payload.message,
            session_id=payload.session_id,
            interval_sec=payload.interval_sec,
            schedule_type=payload.schedule_type,
            schedule=payload.schedule,
            timezone_name=payload.timezone,
        )
        receipt = _sign_action(
            request,
            action="automation_update",
            payload=payload.model_dump(exclude_none=True),
            actor=str(automation.get("user_id") or ""),
            target_id=automation_id,
        )
        return {
            "automation": automation,
            "action_receipt": receipt,
            "request_id": _request_id(request),
        }
    except ValueError as exc:
        _sign_action(
            request,
            action="automation_update",
            payload=payload.model_dump(exclude_none=True),
            target_id=automation_id,
            status="failed",
            details={"error": str(exc)},
        )
        if "not found" in str(exc).lower():
            raise NotFoundError(str(exc)) from exc
        raise ValidationError(str(exc)) from exc
    except Exception as exc:
        _sign_action(
            request,
            action="automation_update",
            payload=payload.model_dump(exclude_none=True),
            target_id=automation_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc


@router.get("/automations")
def list_automations(
    request: Request,
    user_id: str | None = Query(default=None),
    agent_id: str | None = Query(default=None),
    enabled: bool | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    services = request.app.state.services
    try:
        items = services.automation_scheduler.list_automations(
            user_id=user_id,
            agent_id=agent_id,
            enabled=enabled,
            limit=limit,
        )
        return {
            "items": items,
            "count": len(items),
            "request_id": _request_id(request),
        }
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.get("/debug/automations/health")
def debug_automations_health(
    request: Request,
    user_id: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict[str, Any]:
    services = request.app.state.services
    try:
        snapshot = services.automation_scheduler.health_snapshot(
            user_id=user_id,
            limit=limit,
        )
        return {
            "health": snapshot,
            "request_id": _request_id(request),
        }
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.get("/automations/{automation_id}")
def get_automation(
    request: Request,
    automation_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    automation = services.automation_scheduler.get_automation(automation_id)
    if automation is None:
        raise NotFoundError(f"Automation not found: {automation_id}")
    return {
        "automation": automation,
        "request_id": _request_id(request),
    }


@router.post("/automations/{automation_id}/pause")
def pause_automation(
    request: Request,
    automation_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    try:
        automation = services.automation_scheduler.pause_automation(automation_id)
        receipt = _sign_action(
            request,
            action="automation_pause",
            payload={"automation_id": automation_id},
            actor=str(automation.get("user_id") or ""),
            target_id=automation_id,
        )
        return {
            "automation": automation,
            "action_receipt": receipt,
            "request_id": _request_id(request),
        }
    except ValueError as exc:
        _sign_action(
            request,
            action="automation_pause",
            payload={"automation_id": automation_id},
            target_id=automation_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise NotFoundError(str(exc)) from exc
    except Exception as exc:
        _sign_action(
            request,
            action="automation_pause",
            payload={"automation_id": automation_id},
            target_id=automation_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc


@router.post("/automations/{automation_id}/resume")
def resume_automation(
    request: Request,
    automation_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    try:
        automation = services.automation_scheduler.resume_automation(automation_id)
        receipt = _sign_action(
            request,
            action="automation_resume",
            payload={"automation_id": automation_id},
            actor=str(automation.get("user_id") or ""),
            target_id=automation_id,
        )
        return {
            "automation": automation,
            "action_receipt": receipt,
            "request_id": _request_id(request),
        }
    except ValueError as exc:
        _sign_action(
            request,
            action="automation_resume",
            payload={"automation_id": automation_id},
            target_id=automation_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise NotFoundError(str(exc)) from exc
    except Exception as exc:
        _sign_action(
            request,
            action="automation_resume",
            payload={"automation_id": automation_id},
            target_id=automation_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc


@router.post("/automations/{automation_id}/run")
def run_automation_now(
    request: Request,
    automation_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    try:
        automation = services.automation_scheduler.run_now(automation_id)
        receipt = _sign_action(
            request,
            action="automation_run_now",
            payload={"automation_id": automation_id},
            actor=str(automation.get("user_id") or ""),
            target_id=automation_id,
        )
        return {
            "automation": automation,
            "action_receipt": receipt,
            "request_id": _request_id(request),
        }
    except ValueError as exc:
        _sign_action(
            request,
            action="automation_run_now",
            payload={"automation_id": automation_id},
            target_id=automation_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise NotFoundError(str(exc)) from exc
    except Exception as exc:
        _sign_action(
            request,
            action="automation_run_now",
            payload={"automation_id": automation_id},
            target_id=automation_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc


@router.delete("/automations/{automation_id}")
def delete_automation(
    request: Request,
    automation_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    try:
        deleted = services.automation_scheduler.delete_automation(automation_id)
        if not deleted:
            raise NotFoundError(f"Automation not found: {automation_id}")
        receipt = _sign_action(
            request,
            action="automation_delete",
            payload={"automation_id": automation_id},
            target_id=automation_id,
        )
        return {
            "status": "deleted",
            "automation_id": automation_id,
            "action_receipt": receipt,
            "request_id": _request_id(request),
        }
    except NotFoundError:
        _sign_action(
            request,
            action="automation_delete",
            payload={"automation_id": automation_id},
            target_id=automation_id,
            status="failed",
            details={"error": f"Automation not found: {automation_id}"},
        )
        raise
    except Exception as exc:
        _sign_action(
            request,
            action="automation_delete",
            payload={"automation_id": automation_id},
            target_id=automation_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc


@router.get("/automations/{automation_id}/events")
def list_automation_events(
    request: Request,
    automation_id: str = Path(..., min_length=1),
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    services = request.app.state.services
    automation = services.automation_scheduler.get_automation(automation_id)
    if automation is None:
        raise NotFoundError(f"Automation not found: {automation_id}")
    try:
        items = services.automation_scheduler.list_events(automation_id=automation_id, limit=limit)
        return {
            "items": items,
            "count": len(items),
            "request_id": _request_id(request),
        }
    except Exception as exc:
        raise ProviderError(str(exc)) from exc
