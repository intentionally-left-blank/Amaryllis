from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, Field

from runtime.automation_missions import (
    apply_mission_template,
    build_mission_plan,
    list_mission_policy_profiles,
    list_mission_templates,
)
from runtime.auth import assert_owner, auth_context_from_request, resolve_user_id
from runtime.errors import AmaryllisError, NotFoundError, ProviderError, ValidationError

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
    mission_policy: dict[str, Any] = Field(default_factory=dict)


class PlanMissionRequest(BaseModel):
    agent_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    message: str | None = None
    session_id: str | None = None
    timezone: str = Field(default="UTC", min_length=1)
    cadence_profile: str | None = None
    start_immediately: bool | None = None
    template_id: str | None = Field(default=None, min_length=1)
    schedule_type: str | None = Field(default=None)
    schedule: dict[str, Any] = Field(default_factory=dict)
    interval_sec: int | None = Field(default=None, ge=10, le=86400)
    max_attempts: int | None = Field(default=None, ge=1, le=10)
    budget: dict[str, Any] = Field(default_factory=dict)
    mission_policy_profile: str | None = None
    mission_policy: dict[str, Any] = Field(default_factory=dict)


@router.get("/automations/mission/templates")
def mission_templates(request: Request) -> dict[str, Any]:
    auth_context_from_request(request)
    templates = list_mission_templates()
    return {
        "items": templates,
        "count": len(templates),
        "request_id": _request_id(request),
    }


@router.get("/automations/mission/policies")
def mission_policy_profiles(request: Request) -> dict[str, Any]:
    auth_context_from_request(request)
    policies = list_mission_policy_profiles()
    return {
        "items": policies,
        "count": len(policies),
        "request_id": _request_id(request),
    }


@router.post("/automations/mission/plan")
def plan_mission(payload: PlanMissionRequest, request: Request) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=payload.user_id, auth=auth)
    try:
        agent = services.agent_manager.get_agent(payload.agent_id)
        if agent is None:
            raise NotFoundError(f"Agent not found: {payload.agent_id}")
        assert_owner(
            owner_user_id=agent.user_id,
            auth=auth,
            resource_name="agent",
            resource_id=payload.agent_id,
        )

        resolved = apply_mission_template(
            template_id=payload.template_id,
            message=payload.message,
            cadence_profile=payload.cadence_profile,
            start_immediately=payload.start_immediately,
            schedule_type=payload.schedule_type,
            schedule=payload.schedule,
            interval_sec=payload.interval_sec,
            max_attempts=payload.max_attempts,
            budget=payload.budget,
            mission_policy_profile=payload.mission_policy_profile,
            mission_policy=payload.mission_policy,
        )

        simulation = services.agent_manager.simulate_run(
            agent_id=payload.agent_id,
            user_id=effective_user_id,
            session_id=payload.session_id,
            user_message=str(resolved.get("message") or ""),
            max_attempts=resolved.get("max_attempts"),
            budget=resolved.get("budget", {}),
        )
        mission_plan = build_mission_plan(
            agent_id=payload.agent_id,
            user_id=effective_user_id,
            message=str(resolved.get("message") or ""),
            session_id=payload.session_id,
            timezone_name=payload.timezone,
            cadence_profile=str(resolved.get("cadence_profile") or "workday"),
            start_immediately=bool(resolved.get("start_immediately")),
            schedule_type=resolved.get("schedule_type"),
            schedule=resolved.get("schedule"),
            interval_sec=resolved.get("interval_sec"),
            simulation=simulation,
        )
        selected_template = resolved.get("template")
        if isinstance(selected_template, dict):
            mission_plan["template"] = selected_template
        resolved_mission_policy = resolved.get("mission_policy")
        if isinstance(resolved_mission_policy, dict):
            mission_plan["mission_policy"] = resolved_mission_policy
            apply_payload = mission_plan.get("apply_payload")
            if isinstance(apply_payload, dict):
                apply_payload["mission_policy"] = dict(resolved_mission_policy)
        receipt = _sign_action(
            request,
            action="automation_plan_mission",
            payload={**payload.model_dump(), "user_id": effective_user_id},
            actor=auth.user_id,
            target_id=payload.agent_id,
        )
        return {
            "mission_plan": mission_plan,
            "simulation": simulation,
            "template": selected_template,
            "mission_policy": resolved_mission_policy,
            "apply_hint": {
                "endpoint": "/automations/create",
                "payload": mission_plan.get("apply_payload", {}),
            },
            "action_receipt": receipt,
            "request_id": _request_id(request),
        }
    except ValueError as exc:
        _sign_action(
            request,
            action="automation_plan_mission",
            payload={**payload.model_dump(), "user_id": effective_user_id},
            actor=auth.user_id,
            target_id=payload.agent_id,
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
            action="automation_plan_mission",
            payload={**payload.model_dump(), "user_id": effective_user_id},
            actor=auth.user_id,
            target_id=payload.agent_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc


@router.post("/automations/create")
def create_automation(payload: CreateAutomationRequest, request: Request) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=payload.user_id, auth=auth)
    try:
        agent = services.agent_manager.get_agent(payload.agent_id)
        if agent is None:
            raise NotFoundError(f"Agent not found: {payload.agent_id}")
        assert_owner(
            owner_user_id=agent.user_id,
            auth=auth,
            resource_name="agent",
            resource_id=payload.agent_id,
        )
        automation = services.automation_scheduler.create_automation(
            agent_id=payload.agent_id,
            user_id=effective_user_id,
            session_id=payload.session_id,
            message=payload.message,
            interval_sec=payload.interval_sec,
            schedule_type=payload.schedule_type,
            schedule=payload.schedule,
            timezone_name=payload.timezone,
            start_immediately=payload.start_immediately,
            mission_policy=payload.mission_policy,
        )
        receipt = _sign_action(
            request,
            action="automation_create",
            payload={**payload.model_dump(), "user_id": effective_user_id},
            actor=auth.user_id,
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
            payload={**payload.model_dump(), "user_id": effective_user_id},
            actor=auth.user_id,
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
            action="automation_create",
            payload={**payload.model_dump(), "user_id": effective_user_id},
            actor=auth.user_id,
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
    mission_policy: dict[str, Any] | None = None


@router.post("/automations/{automation_id}/update")
def update_automation(
    payload: UpdateAutomationRequest,
    request: Request,
    automation_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    try:
        existing = services.automation_scheduler.get_automation(automation_id)
        if existing is None:
            raise NotFoundError(f"Automation not found: {automation_id}")
        assert_owner(
            owner_user_id=str(existing.get("user_id") or ""),
            auth=auth,
            resource_name="automation",
            resource_id=automation_id,
        )
        automation = services.automation_scheduler.update_automation(
            automation_id=automation_id,
            message=payload.message,
            session_id=payload.session_id,
            interval_sec=payload.interval_sec,
            schedule_type=payload.schedule_type,
            schedule=payload.schedule,
            timezone_name=payload.timezone,
            mission_policy=payload.mission_policy,
        )
        receipt = _sign_action(
            request,
            action="automation_update",
            payload=payload.model_dump(exclude_none=True),
            actor=auth.user_id,
            target_id=automation_id,
        )
        return {
            "automation": automation,
            "action_receipt": receipt,
            "request_id": _request_id(request),
        }
    except NotFoundError:
        _sign_action(
            request,
            action="automation_update",
            payload=payload.model_dump(exclude_none=True),
            target_id=automation_id,
            status="failed",
            details={"error": f"Automation not found: {automation_id}"},
        )
        raise
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
    except AmaryllisError:
        raise
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
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=user_id, auth=auth)
    try:
        items = services.automation_scheduler.list_automations(
            user_id=effective_user_id,
            agent_id=agent_id,
            enabled=enabled,
            limit=limit,
        )
        return {
            "items": items,
            "count": len(items),
            "request_id": _request_id(request),
        }
    except AmaryllisError:
        raise
    except Exception as exc:
        raise ProviderError(str(exc)) from exc


@router.get("/debug/automations/health")
def debug_automations_health(
    request: Request,
    user_id: str | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=user_id, auth=auth)
    try:
        snapshot = services.automation_scheduler.health_snapshot(
            user_id=effective_user_id,
            limit=limit,
        )
        return {
            "health": snapshot,
            "request_id": _request_id(request),
        }
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    except AmaryllisError:
        raise
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
    auth = auth_context_from_request(request)
    assert_owner(
        owner_user_id=str(automation.get("user_id") or ""),
        auth=auth,
        resource_name="automation",
        resource_id=automation_id,
    )
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
    auth = auth_context_from_request(request)
    try:
        existing = services.automation_scheduler.get_automation(automation_id)
        if existing is None:
            raise NotFoundError(f"Automation not found: {automation_id}")
        assert_owner(
            owner_user_id=str(existing.get("user_id") or ""),
            auth=auth,
            resource_name="automation",
            resource_id=automation_id,
        )
        automation = services.automation_scheduler.pause_automation(automation_id)
        receipt = _sign_action(
            request,
            action="automation_pause",
            payload={"automation_id": automation_id},
            actor=auth.user_id,
            target_id=automation_id,
        )
        return {
            "automation": automation,
            "action_receipt": receipt,
            "request_id": _request_id(request),
        }
    except NotFoundError:
        _sign_action(
            request,
            action="automation_pause",
            payload={"automation_id": automation_id},
            target_id=automation_id,
            status="failed",
            details={"error": f"Automation not found: {automation_id}"},
        )
        raise
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
    except AmaryllisError:
        raise
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
    auth = auth_context_from_request(request)
    try:
        existing = services.automation_scheduler.get_automation(automation_id)
        if existing is None:
            raise NotFoundError(f"Automation not found: {automation_id}")
        assert_owner(
            owner_user_id=str(existing.get("user_id") or ""),
            auth=auth,
            resource_name="automation",
            resource_id=automation_id,
        )
        automation = services.automation_scheduler.resume_automation(automation_id)
        receipt = _sign_action(
            request,
            action="automation_resume",
            payload={"automation_id": automation_id},
            actor=auth.user_id,
            target_id=automation_id,
        )
        return {
            "automation": automation,
            "action_receipt": receipt,
            "request_id": _request_id(request),
        }
    except NotFoundError:
        _sign_action(
            request,
            action="automation_resume",
            payload={"automation_id": automation_id},
            target_id=automation_id,
            status="failed",
            details={"error": f"Automation not found: {automation_id}"},
        )
        raise
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
    except AmaryllisError:
        raise
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
    auth = auth_context_from_request(request)
    try:
        existing = services.automation_scheduler.get_automation(automation_id)
        if existing is None:
            raise NotFoundError(f"Automation not found: {automation_id}")
        assert_owner(
            owner_user_id=str(existing.get("user_id") or ""),
            auth=auth,
            resource_name="automation",
            resource_id=automation_id,
        )
        automation = services.automation_scheduler.run_now(automation_id)
        receipt = _sign_action(
            request,
            action="automation_run_now",
            payload={"automation_id": automation_id},
            actor=auth.user_id,
            target_id=automation_id,
        )
        return {
            "automation": automation,
            "action_receipt": receipt,
            "request_id": _request_id(request),
        }
    except NotFoundError:
        _sign_action(
            request,
            action="automation_run_now",
            payload={"automation_id": automation_id},
            target_id=automation_id,
            status="failed",
            details={"error": f"Automation not found: {automation_id}"},
        )
        raise
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
    except AmaryllisError:
        raise
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
    auth = auth_context_from_request(request)
    try:
        existing = services.automation_scheduler.get_automation(automation_id)
        if existing is None:
            raise NotFoundError(f"Automation not found: {automation_id}")
        assert_owner(
            owner_user_id=str(existing.get("user_id") or ""),
            auth=auth,
            resource_name="automation",
            resource_id=automation_id,
        )
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
    except AmaryllisError:
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
    auth = auth_context_from_request(request)
    assert_owner(
        owner_user_id=str(automation.get("user_id") or ""),
        auth=auth,
        resource_name="automation",
        resource_id=automation_id,
    )
    try:
        items = services.automation_scheduler.list_events(automation_id=automation_id, limit=limit)
        return {
            "items": items,
            "count": len(items),
            "request_id": _request_id(request),
        }
    except AmaryllisError:
        raise
    except Exception as exc:
        raise ProviderError(str(exc)) from exc
