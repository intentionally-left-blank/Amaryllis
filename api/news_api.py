from __future__ import annotations

from typing import Any, Protocol

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from runtime.auth import assert_owner, auth_context_from_request, resolve_user_id
from runtime.errors import AmaryllisError, NotFoundError, ProviderError, ValidationError
from runtime.news_missions import build_news_mission_plan
from sources.base import SUPPORTED_NEWS_SOURCES

router = APIRouter(tags=["news"])

NEWS_AGENT_MARKER = "[[amaryllis.news.agent]]"
NEWS_DEFAULT_AGENT_MEMORY_KEY = "news.default_agent_id"
NEWS_DEFAULT_AGENT_NAME = "News Scout"


class _AgentLike(Protocol):
    id: str
    name: str
    user_id: str
    created_at: str
    system_prompt: str


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
    target_type: str = "news_mission",
    target_id: str | None = None,
) -> dict[str, Any]:
    services = request.app.state.services
    try:
        return services.security_manager.signed_action(
            action=action,
            payload=payload,
            request_id=_request_id(request),
            actor=actor,
            target_type=target_type,
            target_id=target_id,
            status=status,
            details=details,
        )
    except Exception:
        return {}


def _build_news_agent_prompt(focus: str | None = None) -> str:
    focus_text = str(focus or "").strip()
    guidance = (
        "You are a specialized autonomous news research agent. "
        "Gather relevant updates, deduplicate overlapping stories, and cite source URLs. "
        "Prefer factual summaries and confidence notes over speculation."
    )
    if focus_text:
        return f"{NEWS_AGENT_MARKER}\nFocus domain: {focus_text}.\n{guidance}"
    return f"{NEWS_AGENT_MARKER}\nFocus domain: general technology and AI news.\n{guidance}"


def _is_news_agent(agent: _AgentLike) -> bool:
    return NEWS_AGENT_MARKER in str(agent.system_prompt or "")


def _news_agent_payload(agent: _AgentLike, *, is_default: bool = False) -> dict[str, Any]:
    return {
        "news_agent_id": agent.id,
        "name": agent.name,
        "user_id": agent.user_id,
        "created_at": agent.created_at,
        "managed": _is_news_agent(agent),
        "is_default": bool(is_default),
    }


def _remember_default_news_agent(*, services: Any, user_id: str, agent_id: str) -> None:
    if not user_id or not agent_id:
        return
    try:
        services.database.set_user_memory(
            user_id=user_id,
            key=NEWS_DEFAULT_AGENT_MEMORY_KEY,
            value=agent_id,
            source="news_api",
        )
    except Exception:
        return


def _default_news_agent_id(*, services: Any, user_id: str) -> str | None:
    if not user_id:
        return None
    try:
        item = services.database.get_user_memory_item(user_id=user_id, key=NEWS_DEFAULT_AGENT_MEMORY_KEY)
    except Exception:
        return None
    if not isinstance(item, dict):
        return None
    value = str(item.get("value") or "").strip()
    return value or None


def _resolve_news_agent(
    *,
    request: Request,
    auth: Any,
    user_id: str,
    agent_id: str | None,
    news_agent_id: str | None,
    create_if_missing: bool,
) -> _AgentLike:
    services = request.app.state.services
    requested = str(news_agent_id or agent_id or "").strip()
    if requested:
        resolved = services.agent_manager.get_agent(requested)
        if resolved is None:
            raise NotFoundError(f"News agent not found: {requested}")
        assert_owner(
            owner_user_id=resolved.user_id,
            auth=auth,
            resource_name="agent",
            resource_id=resolved.id,
        )
        return resolved

    remembered = _default_news_agent_id(services=services, user_id=user_id)
    if remembered:
        candidate = services.agent_manager.get_agent(remembered)
        if candidate is not None and str(candidate.user_id or "").strip() == user_id:
            assert_owner(
                owner_user_id=candidate.user_id,
                auth=auth,
                resource_name="agent",
                resource_id=candidate.id,
            )
            return candidate

    for candidate in services.agent_manager.list_agents(user_id=user_id):
        if not _is_news_agent(candidate):
            continue
        _remember_default_news_agent(services=services, user_id=user_id, agent_id=candidate.id)
        return candidate

    if not create_if_missing:
        raise NotFoundError("News agent not found")

    created = services.agent_manager.create_agent(
        name=NEWS_DEFAULT_AGENT_NAME,
        system_prompt=_build_news_agent_prompt(),
        model=None,
        tools=["web_search"],
        user_id=user_id,
    )
    _remember_default_news_agent(services=services, user_id=user_id, agent_id=created.id)
    return created


class InternetScopeRequest(BaseModel):
    queries: list[str] = Field(default_factory=list)
    include_domains: list[str] = Field(default_factory=list)
    exclude_domains: list[str] = Field(default_factory=list)
    seed_urls: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    max_depth: int = Field(default=1, ge=1, le=5)


class NewsAgentCreateRequest(BaseModel):
    user_id: str | None = None
    name: str | None = None
    focus: str | None = None
    model: str | None = None
    tools: list[str] = Field(default_factory=lambda: ["web_search"])
    set_default: bool = True


class NewsMissionPlanRequest(BaseModel):
    news_agent_id: str | None = None
    agent_id: str | None = None
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
    internet_scope: InternetScopeRequest = Field(default_factory=InternetScopeRequest)
    source_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)


class NewsIngestPreviewRequest(BaseModel):
    user_id: str | None = None
    topic: str = Field(min_length=1, max_length=300)
    sources: list[str] = Field(default_factory=lambda: ["web"])
    window_hours: int = Field(default=24, ge=1, le=168)
    max_items_per_source: int = Field(default=20, ge=1, le=100)
    internet_scope: InternetScopeRequest = Field(default_factory=InternetScopeRequest)
    persist: bool = True


class NewsMissionCreateRequest(NewsMissionPlanRequest):
    session_id: str | None = None


class NewsAgentQuickstartRequest(BaseModel):
    user_id: str | None = None
    request: str = Field(default="сделай пожалуйста такого агента", min_length=1, max_length=1000)
    topic: str | None = Field(default=None, max_length=300)
    focus: str | None = Field(default=None, max_length=300)
    sources: list[str] = Field(default_factory=lambda: ["web"])
    window_hours: int = Field(default=24, ge=1, le=168)
    max_items_per_source: int = Field(default=20, ge=1, le=100)
    timezone: str = Field(default="UTC", min_length=1)
    schedule_type: str | None = None
    schedule: dict[str, Any] = Field(default_factory=dict)
    interval_sec: int | None = Field(default=None, ge=10, le=86400)
    start_immediately: bool = False
    internet_scope: InternetScopeRequest = Field(default_factory=InternetScopeRequest)
    source_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
    session_id: str | None = None


def _infer_topic_from_quickstart(request_text: str, explicit_topic: str | None) -> str:
    manual = str(explicit_topic or "").strip()
    if manual:
        return manual
    text = str(request_text or "").strip().lower()
    if "crypto" in text or "крипт" in text or "bitcoin" in text:
        return "Crypto"
    if "fintech" in text or "finance" in text or "финанс" in text:
        return "Fintech"
    if "security" in text or "кибер" in text or "security" in text:
        return "Cybersecurity"
    if "robot" in text or "робот" in text:
        return "Robotics"
    if "startup" in text or "стартап" in text:
        return "Startups"
    return "AI"


@router.get("/news/contract")
def news_contract(request: Request) -> dict[str, Any]:
    auth_context_from_request(request)
    services = request.app.state.services
    return {
        "contract_version": "news_mission_v1",
        "contract_path": "contracts/news_mission_v1.json",
        "supported_sources": list(SUPPORTED_NEWS_SOURCES),
        "source_health": services.source_connectors.health(),
        "endpoints": [
            {"method": "POST", "path": "/news/agents/create"},
            {"method": "GET", "path": "/news/agents"},
            {"method": "POST", "path": "/news/agents/quickstart"},
            {"method": "POST", "path": "/news/missions/plan"},
            {"method": "POST", "path": "/news/missions/create"},
            {"method": "POST", "path": "/news/ingest/preview"},
            {"method": "GET", "path": "/news/items"},
        ],
        "request_id": _request_id(request),
    }


@router.post("/news/agents/create")
def create_news_agent(payload: NewsAgentCreateRequest, request: Request) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=payload.user_id, auth=auth)
    try:
        name = str(payload.name or "").strip() or NEWS_DEFAULT_AGENT_NAME
        tools = [str(item).strip() for item in payload.tools if str(item).strip()]
        if not tools:
            tools = ["web_search"]
        agent = services.agent_manager.create_agent(
            name=name,
            system_prompt=_build_news_agent_prompt(payload.focus),
            model=payload.model,
            tools=tools,
            user_id=effective_user_id,
        )
        if bool(payload.set_default):
            _remember_default_news_agent(services=services, user_id=effective_user_id, agent_id=agent.id)
    except Exception as exc:
        _sign_action(
            request,
            action="news_agent_create",
            payload={**payload.model_dump(exclude_none=True), "user_id": effective_user_id},
            actor=auth.user_id,
            target_type="news_agent",
            status="failed",
            details={"error": str(exc)},
        )
        _raise_news_error(exc)

    receipt = _sign_action(
        request,
        action="news_agent_create",
        payload={**payload.model_dump(exclude_none=True), "user_id": effective_user_id},
        actor=auth.user_id,
        target_type="news_agent",
        target_id=agent.id,
    )
    return {
        "news_agent": _news_agent_payload(agent, is_default=bool(payload.set_default)),
        "action_receipt": receipt,
        "request_id": _request_id(request),
    }


@router.get("/news/agents")
def list_news_agents(request: Request, user_id: str | None = Query(default=None)) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=user_id, auth=auth)
    default_id = _default_news_agent_id(services=services, user_id=effective_user_id)
    agents: list[dict[str, Any]] = []
    for agent in services.agent_manager.list_agents(user_id=effective_user_id):
        if not _is_news_agent(agent):
            continue
        agents.append(_news_agent_payload(agent, is_default=(agent.id == default_id)))
    return {
        "items": agents,
        "count": len(agents),
        "request_id": _request_id(request),
    }


@router.post("/news/agents/quickstart")
def quickstart_news_agent(payload: NewsAgentQuickstartRequest, request: Request) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=payload.user_id, auth=auth)
    resolved_topic = _infer_topic_from_quickstart(payload.request, payload.topic)
    try:
        agent = _resolve_news_agent(
            request=request,
            auth=auth,
            user_id=effective_user_id,
            agent_id=None,
            news_agent_id=None,
            create_if_missing=True,
        )
        plan = build_news_mission_plan(
            agent_id=agent.id,
            user_id=effective_user_id,
            topic=resolved_topic,
            timezone_name=payload.timezone,
            sources=payload.sources,
            window_hours=payload.window_hours,
            max_items_per_source=payload.max_items_per_source,
            schedule_type=payload.schedule_type,
            schedule=payload.schedule,
            interval_sec=payload.interval_sec,
            start_immediately=payload.start_immediately,
            internet_scope=payload.internet_scope.model_dump(exclude_none=True),
            source_overrides=payload.source_overrides,
        )
        apply_payload = plan.get("apply_payload") if isinstance(plan.get("apply_payload"), dict) else {}
        apply_payload["session_id"] = payload.session_id
        automation = services.automation_scheduler.create_automation(
            agent_id=str(apply_payload.get("agent_id") or agent.id),
            user_id=str(apply_payload.get("user_id") or effective_user_id),
            session_id=payload.session_id,
            message=str(apply_payload.get("message") or ""),
            interval_sec=apply_payload.get("interval_sec"),
            schedule_type=apply_payload.get("schedule_type"),
            schedule=apply_payload.get("schedule"),
            timezone_name=str(apply_payload.get("timezone") or payload.timezone),
            start_immediately=bool(apply_payload.get("start_immediately", payload.start_immediately)),
            mission_policy=apply_payload.get("mission_policy"),
        )
        _remember_default_news_agent(services=services, user_id=effective_user_id, agent_id=agent.id)
    except Exception as exc:
        _sign_action(
            request,
            action="news_agent_quickstart",
            payload={**payload.model_dump(), "user_id": effective_user_id, "resolved_topic": resolved_topic},
            actor=auth.user_id,
            target_type="news_agent",
            status="failed",
            details={"error": str(exc)},
        )
        _raise_news_error(exc)

    receipt = _sign_action(
        request,
        action="news_agent_quickstart",
        payload={**payload.model_dump(), "user_id": effective_user_id, "resolved_topic": resolved_topic},
        actor=auth.user_id,
        target_type="news_agent",
        target_id=agent.id,
    )
    return {
        "news_agent": _news_agent_payload(agent, is_default=True),
        "resolved_topic": resolved_topic,
        "mission_plan": plan,
        "automation": automation,
        "assistant_reply": (
            f"Готово. Создал новостного агента по теме '{resolved_topic}' "
            "и включил ежедневный авто-дайджест."
        ),
        "action_receipt": receipt,
        "request_id": _request_id(request),
    }


@router.post("/news/missions/plan")
def plan_news_mission(payload: NewsMissionPlanRequest, request: Request) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=payload.user_id, auth=auth)
    try:
        agent = _resolve_news_agent(
            request=request,
            auth=auth,
            user_id=effective_user_id,
            agent_id=payload.agent_id,
            news_agent_id=payload.news_agent_id,
            create_if_missing=True,
        )
        plan = build_news_mission_plan(
            agent_id=agent.id,
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
            internet_scope=payload.internet_scope.model_dump(exclude_none=True),
            source_overrides=payload.source_overrides,
        )
        _remember_default_news_agent(services=services, user_id=effective_user_id, agent_id=agent.id)
    except Exception as exc:
        _sign_action(
            request,
            action="news_mission_plan",
            payload={**payload.model_dump(), "user_id": effective_user_id},
            actor=auth.user_id,
            target_id=str(payload.news_agent_id or payload.agent_id or ""),
            status="failed",
            details={"error": str(exc)},
        )
        _raise_news_error(exc)

    receipt = _sign_action(
        request,
        action="news_mission_plan",
        payload={**payload.model_dump(), "user_id": effective_user_id, "resolved_agent_id": agent.id},
        actor=auth.user_id,
        target_id=agent.id,
    )
    return {
        "news_agent": _news_agent_payload(agent, is_default=True),
        "mission_plan": plan,
        "apply_hint": {
            "endpoint": "/news/missions/create",
            "payload": {
                **payload.model_dump(exclude_none=True),
                "user_id": effective_user_id,
                "news_agent_id": agent.id,
                "agent_id": agent.id,
            },
        },
        "automation_apply_hint": {
            "endpoint": "/automations/create",
            "payload": plan.get("apply_payload", {}),
        },
        "action_receipt": receipt,
        "request_id": _request_id(request),
    }


@router.post("/news/missions/create")
def create_news_mission(payload: NewsMissionCreateRequest, request: Request) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=payload.user_id, auth=auth)
    try:
        agent = _resolve_news_agent(
            request=request,
            auth=auth,
            user_id=effective_user_id,
            agent_id=payload.agent_id,
            news_agent_id=payload.news_agent_id,
            create_if_missing=True,
        )
        plan = build_news_mission_plan(
            agent_id=agent.id,
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
            internet_scope=payload.internet_scope.model_dump(exclude_none=True),
            source_overrides=payload.source_overrides,
        )
        apply_payload = plan.get("apply_payload") if isinstance(plan.get("apply_payload"), dict) else {}
        apply_payload["session_id"] = payload.session_id
        automation = services.automation_scheduler.create_automation(
            agent_id=str(apply_payload.get("agent_id") or agent.id),
            user_id=str(apply_payload.get("user_id") or effective_user_id),
            session_id=payload.session_id,
            message=str(apply_payload.get("message") or ""),
            interval_sec=apply_payload.get("interval_sec"),
            schedule_type=apply_payload.get("schedule_type"),
            schedule=apply_payload.get("schedule"),
            timezone_name=str(apply_payload.get("timezone") or payload.timezone),
            start_immediately=bool(apply_payload.get("start_immediately", payload.start_immediately)),
            mission_policy=apply_payload.get("mission_policy"),
        )
        _remember_default_news_agent(services=services, user_id=effective_user_id, agent_id=agent.id)
    except Exception as exc:
        _sign_action(
            request,
            action="news_mission_create",
            payload={**payload.model_dump(), "user_id": effective_user_id},
            actor=auth.user_id,
            target_id=str(payload.news_agent_id or payload.agent_id or ""),
            status="failed",
            details={"error": str(exc)},
        )
        _raise_news_error(exc)

    receipt = _sign_action(
        request,
        action="news_mission_create",
        payload={**payload.model_dump(), "user_id": effective_user_id, "resolved_agent_id": agent.id},
        actor=auth.user_id,
        target_id=str(automation.get("id") or ""),
    )
    return {
        "news_agent": _news_agent_payload(agent, is_default=True),
        "mission_plan": plan,
        "automation": automation,
        "action_receipt": receipt,
        "request_id": _request_id(request),
    }


@router.post("/news/ingest/preview")
def ingest_news_preview(payload: NewsIngestPreviewRequest, request: Request) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=payload.user_id, auth=auth)
    try:
        report = services.news_pipeline.ingest_preview(
            topic=payload.topic,
            sources=payload.sources,
            window_hours=payload.window_hours,
            max_items_per_source=payload.max_items_per_source,
            internet_scope=payload.internet_scope.model_dump(exclude_none=True),
        )
        persisted_count = 0
        if bool(payload.persist):
            persisted_count = services.database.upsert_news_items(
                user_id=effective_user_id,
                topic=payload.topic,
                items=report.get("items") if isinstance(report.get("items"), list) else [],
            )
    except Exception as exc:
        _sign_action(
            request,
            action="news_ingest_preview",
            payload={**payload.model_dump(), "user_id": effective_user_id},
            actor=auth.user_id,
            status="failed",
            details={"error": str(exc)},
        )
        _raise_news_error(exc)

    receipt = _sign_action(
        request,
        action="news_ingest_preview",
        payload={**payload.model_dump(), "user_id": effective_user_id},
        actor=auth.user_id,
    )
    return {
        "report": report,
        "persisted_count": persisted_count,
        "action_receipt": receipt,
        "request_id": _request_id(request),
    }


@router.get("/news/items")
def list_news_items(
    request: Request,
    user_id: str | None = Query(default=None),
    topic: str | None = Query(default=None),
    source: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=user_id, auth=auth)
    try:
        items = services.database.list_news_items(
            user_id=effective_user_id,
            topic=topic,
            source=source,
            limit=limit,
        )
    except Exception as exc:
        _raise_news_error(exc)
    return {
        "items": items,
        "count": len(items),
        "request_id": _request_id(request),
    }
