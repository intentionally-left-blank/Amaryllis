from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from runtime.auth import assert_owner, auth_context_from_request, resolve_user_id
from runtime.errors import AmaryllisError, NotFoundError, ProviderError, ValidationError
from runtime.news_missions import build_news_mission_plan
from sources.base import SUPPORTED_NEWS_SOURCES

router = APIRouter(tags=["news"])


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


def _raise_news_error(exc: Exception) -> None:
    if isinstance(exc, AmaryllisError):
        raise exc
    if isinstance(exc, ValueError):
        text = str(exc)
        if "not found" in text.lower():
            raise NotFoundError(text) from exc
        raise ValidationError(text) from exc
    raise ProviderError(str(exc)) from exc


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
            target_type="news_mission",
            target_id=target_id,
            status=status,
            details=details,
        )
    except Exception:
        return {}


class NewsMissionPlanRequest(BaseModel):
    agent_id: str = Field(min_length=1)
    user_id: str | None = None
    topic: str = Field(min_length=1, max_length=300)
    sources: list[str] = Field(default_factory=lambda: ["web"])
    window_hours: int = Field(default=24, ge=1, le=168)
    max_items_per_source: int = Field(default=20, ge=1, le=100)
    timezone: str = Field(default="UTC", min_length=1)
    schedule_type: str | None = None
    schedule: dict[str, Any] = Field(default_factory=dict)
    interval_sec: int | None = Field(default=None, ge=10, le=86400)
    start_immediately: bool = False


@router.get("/news/contract")
def news_contract(request: Request) -> dict[str, Any]:
    auth_context_from_request(request)
    services = request.app.state.services
    return {
        "contract_version": "news_mission_v1",
        "contract_path": "contracts/news_mission_v1.json",
        "supported_sources": list(SUPPORTED_NEWS_SOURCES),
        "source_health": services.source_connectors.health(),
        "planner_endpoints": [
            {"method": "POST", "path": "/news/missions/plan"},
            {"method": "POST", "path": "/automations/create"},
        ],
        "request_id": _request_id(request),
    }


@router.post("/news/missions/plan")
def plan_news_mission(payload: NewsMissionPlanRequest, request: Request) -> dict[str, Any]:
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
        plan = build_news_mission_plan(
            agent_id=payload.agent_id,
            user_id=effective_user_id,
            topic=payload.topic,
            timezone_name=payload.timezone,
            sources=payload.sources,
            window_hours=payload.window_hours,
            max_items_per_source=payload.max_items_per_source,
            schedule_type=payload.schedule_type,
            schedule=payload.schedule,
            interval_sec=payload.interval_sec,
            start_immediately=payload.start_immediately,
        )
    except Exception as exc:
        _sign_action(
            request,
            action="news_mission_plan",
            payload={**payload.model_dump(), "user_id": effective_user_id},
            actor=auth.user_id,
            target_id=payload.agent_id,
            status="failed",
            details={"error": str(exc)},
        )
        _raise_news_error(exc)

    receipt = _sign_action(
        request,
        action="news_mission_plan",
        payload={**payload.model_dump(), "user_id": effective_user_id},
        actor=auth.user_id,
        target_id=payload.agent_id,
    )
    return {
        "mission_plan": plan,
        "apply_hint": {
            "endpoint": "/automations/create",
            "payload": plan.get("apply_payload", {}),
        },
        "action_receipt": receipt,
        "request_id": _request_id(request),
    }

