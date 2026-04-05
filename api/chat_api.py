from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
import os
import re
import time
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from runtime.auth import auth_context_from_request, resolve_user_id
from runtime.errors import ProviderError, ValidationError
from tools.tool_executor import PermissionRequiredError

router = APIRouter(tags=["chat"])


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_AGENT_NAME_QUOTED_PATTERN = re.compile(r"[\"'«“](?P<name>[^\"'»”]{2,60})[\"'»”]")
_AGENT_FOCUS_PATTERN = re.compile(
    r"(?:для|по|for|about)\s+(?P<focus>[a-zA-Z0-9а-яА-ЯёЁ _/+#-]{2,120})",
    flags=re.IGNORECASE,
)
_SCHEDULE_TIME_PATTERN = re.compile(r"(?:\bв\b|\bat\b)\s*(?P<hour>\d{1,2})(?::(?P<minute>\d{1,2}))?")
_MINUTE_ONLY_PATTERN = re.compile(
    r"(?:\bв\b|\bat\b)\s*(?P<minute>\d{1,2})\s*(?:минут(?:а|ы)?|minute(?:s)?)",
    flags=re.IGNORECASE,
)
_HOURLY_INTERVAL_PATTERN = re.compile(
    r"(?:каждые|every)\s*(?P<hours>\d{1,2})\s*(?:час(?:а|ов)?|hours?)",
    flags=re.IGNORECASE,
)
_HOUR_ONLY_PATTERN = re.compile(r"(?<!\d)(?P<hour>\d{1,2})\s*(?:час(?:а|ов)?|hours?)(?!\w)", flags=re.IGNORECASE)

_DAILY_SCHEDULE_TOKENS: tuple[str, ...] = (
    "каждый день",
    "ежеднев",
    "daily",
    "every day",
)
_WEEKLY_SCHEDULE_TOKENS: tuple[str, ...] = (
    "еженед",
    "каждую неделю",
    "weekly",
    "every week",
)
_HOURLY_SCHEDULE_TOKENS: tuple[str, ...] = (
    "каждый час",
    "каждые",
    "hourly",
    "every hour",
)
_START_IMMEDIATELY_TOKENS: tuple[str, ...] = (
    "сразу",
    "прямо сейчас",
    "немедленно",
    "start now",
    "immediately",
    "run now",
)

_WEEKDAY_TOKENS: dict[str, str] = {
    "понедель": "MO",
    "пн": "MO",
    "monday": "MO",
    "mon": "MO",
    "вторник": "TU",
    "вт": "TU",
    "tuesday": "TU",
    "tue": "TU",
    "сред": "WE",
    "ср": "WE",
    "wednesday": "WE",
    "wed": "WE",
    "четверг": "TH",
    "чт": "TH",
    "thursday": "TH",
    "thu": "TH",
    "пятниц": "FR",
    "пт": "FR",
    "friday": "FR",
    "fri": "FR",
    "суббот": "SA",
    "сб": "SA",
    "saturday": "SA",
    "sat": "SA",
    "воскрес": "SU",
    "вс": "SU",
    "sunday": "SU",
    "sun": "SU",
}

_SOURCE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("reddit", ("reddit", "реддит")),
    ("twitter", ("twitter", "x.com", "tweet", "твиттер", "икс")),
    ("hackernews", ("hacker news", "hn", "news.ycombinator.com")),
    ("arxiv", ("arxiv", "arxiv.org")),
    ("github", ("github", "гитхаб")),
    ("web", ("web", "internet", "интернет", "сайт", "новост", "news")),
)
CHAT_QUICKSTART_IDEMPOTENCY_MEMORY_PREFIX = "chat.quickstart.idempotency.v1."


def _sign_action(
    request: Request,
    *,
    action: str,
    payload: dict[str, Any],
    actor: str | None = None,
    target_type: str | None = None,
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
            target_type=target_type,
            target_id=target_id,
            status=status,
            details=details,
        )
    except Exception:
        return {}


def _normalize_idempotency_key(raw_key: str | None) -> str:
    key = str(raw_key or "").strip()
    if not key:
        return ""
    return key[:200]


def _chat_quickstart_payload_fingerprint(
    *,
    user_id: str,
    query: str,
    session_id: str | None,
    model: str | None,
) -> str:
    canonical = {
        "user_id": str(user_id or "").strip(),
        "query": str(query or "").strip(),
        "session_id": str(session_id or "").strip(),
        "model": str(model or "").strip(),
    }
    serialized = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _chat_quickstart_auto_idempotency_key(
    *,
    user_id: str,
    query: str,
    session_id: str | None,
    model: str | None,
) -> str:
    fingerprint = _chat_quickstart_payload_fingerprint(
        user_id=user_id,
        query=query,
        session_id=session_id,
        model=model,
    )
    return f"chat-quickstart-{fingerprint[:24]}"


def _chat_quickstart_idempotency_memory_key(idempotency_key: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()
    return f"{CHAT_QUICKSTART_IDEMPOTENCY_MEMORY_PREFIX}{digest}"


def _load_quickstart_idempotency_record(raw_value: str) -> dict[str, Any]:
    try:
        payload = json.loads(str(raw_value or ""))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


class ChatMessage(BaseModel):
    role: str
    content: str | None = None
    name: str | None = None


class ToolFunction(BaseModel):
    name: str
    description: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class ToolDefinition(BaseModel):
    type: str = "function"
    function: ToolFunction


class ChatRoutingOptions(BaseModel):
    mode: str = Field(default="balanced")
    require_stream: bool = True
    require_tools: bool = False
    prefer_local: bool | None = None
    min_params_b: float | None = Field(default=None, ge=0.0)
    max_params_b: float | None = Field(default=None, ge=0.0)
    include_suggested: bool = False


class ChatCompletionsRequest(BaseModel):
    model: str | None = None
    provider: str | None = None
    user_id: str | None = None
    session_id: str | None = None
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=512, ge=1, le=8192)
    tools: list[ToolDefinition] | None = None
    permission_ids: list[str] = Field(default_factory=list)
    routing: ChatRoutingOptions | None = None


def _last_user_query(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        role = str(message.get("role") or "").strip().lower()
        if role != "user":
            continue
        content = str(message.get("content") or "").strip()
        if content:
            return content
    return ""


def _looks_like_agent_quickstart_request(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    if not normalized:
        return False
    if "как создать" in normalized or "how to create" in normalized:
        return False
    has_agent = "агент" in normalized or "agent" in normalized
    has_create = any(
        token in normalized
        for token in (
            "создай",
            "создать",
            "сделай",
            "сделать",
            "create",
            "build",
            "make",
        )
    )
    return has_agent and has_create


def _clean_focus_text(text: str) -> str:
    normalized = str(text or "").strip(" ,.;:!?")
    if not normalized:
        return ""
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized[:120]


def _strip_focus_tail(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    cut_at = len(raw)
    markers = (
        " каждый день",
        " ежедневно",
        " daily",
        " every day",
        " каждый час",
        " hourly",
        " every hour",
        " еженед",
        " weekly",
        " reddit",
        " twitter",
        " x.com",
    )
    for marker in markers:
        idx = lowered.find(marker)
        if idx > 0 and idx < cut_at:
            cut_at = idx
    return _clean_focus_text(raw[:cut_at])


def _infer_source_targets(lowered_text: str) -> list[str]:
    targets: list[str] = []
    lowered = str(lowered_text or "").strip().lower()
    if not lowered:
        return targets
    for source_id, variants in _SOURCE_PATTERNS:
        if any(variant in lowered for variant in variants):
            targets.append(source_id)
    if ("новост" in lowered or "news" in lowered) and "web" not in targets:
        targets.append("web")
    return targets


def _extract_time_hint(lowered_text: str, *, default_hour: int = 9, default_minute: int = 0) -> tuple[int, int]:
    text = str(lowered_text or "")
    match = _SCHEDULE_TIME_PATTERN.search(text)
    if match is None:
        minute_only = _MINUTE_ONLY_PATTERN.search(text)
        if minute_only is None:
            return default_hour, default_minute
        try:
            minute = int(minute_only.group("minute"))
        except Exception:
            minute = default_minute
        minute = max(0, min(59, minute))
        return default_hour, minute
    if match.group("minute") is None:
        tail = text[match.end() :]
        if re.match(r"\s*(?:минут(?:а|ы)?|minute(?:s)?)\b", tail, flags=re.IGNORECASE):
            try:
                minute = int(match.group("hour"))
            except Exception:
                minute = default_minute
            minute = max(0, min(59, minute))
            return default_hour, minute
    try:
        hour = int(match.group("hour"))
    except Exception:
        hour = default_hour
    try:
        minute = int(match.group("minute") or default_minute)
    except Exception:
        minute = default_minute
    hour = max(0, min(23, hour))
    minute = max(0, min(59, minute))
    return hour, minute


def _extract_weekday_codes(lowered_text: str) -> list[str]:
    lowered = str(lowered_text or "").lower()
    if not lowered:
        return []
    seen: list[str] = []
    for token, code in _WEEKDAY_TOKENS.items():
        if token in lowered and code not in seen:
            seen.append(code)
    return seen


def _infer_schedule_spec(lowered_text: str) -> dict[str, Any] | None:
    lowered = str(lowered_text or "").strip().lower()
    if not lowered:
        return None
    start_immediately = any(token in lowered for token in _START_IMMEDIATELY_TOKENS)

    if any(token in lowered for token in _HOURLY_SCHEDULE_TOKENS):
        interval_hours = 1
        interval_match = _HOURLY_INTERVAL_PATTERN.search(lowered)
        if interval_match is not None:
            try:
                interval_hours = int(interval_match.group("hours"))
            except Exception:
                interval_hours = 1
        else:
            direct_match = _HOUR_ONLY_PATTERN.search(lowered)
            if direct_match is not None:
                try:
                    interval_hours = int(direct_match.group("hour"))
                except Exception:
                    interval_hours = 1
        interval_hours = max(1, min(24, interval_hours))
        _, minute = _extract_time_hint(lowered, default_hour=0, default_minute=0)
        return {
            "schedule_type": "hourly",
            "schedule": {"interval_hours": interval_hours, "minute": minute},
            "interval_sec": interval_hours * 3600,
            "timezone": "UTC",
            "start_immediately": start_immediately,
        }

    weekday_codes = _extract_weekday_codes(lowered)
    is_daily = any(token in lowered for token in _DAILY_SCHEDULE_TOKENS)
    is_weekly = any(token in lowered for token in _WEEKLY_SCHEDULE_TOKENS) or bool(weekday_codes)
    if is_daily or is_weekly:
        hour, minute = _extract_time_hint(lowered, default_hour=9, default_minute=0)
        byday = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"] if is_daily else (weekday_codes or ["MO"])
        return {
            "schedule_type": "weekly",
            "schedule": {"byday": byday, "hour": hour, "minute": minute},
            "interval_sec": 7 * 24 * 3600,
            "timezone": "UTC",
            "start_immediately": start_immediately,
        }

    return None


def _automation_schedule_summary(automation: dict[str, Any]) -> str:
    schedule_type = str(automation.get("schedule_type") or "")
    schedule = automation.get("schedule")
    if not isinstance(schedule, dict):
        schedule = {}
    if schedule_type == "hourly":
        try:
            hours = int(schedule.get("interval_hours", 1))
        except Exception:
            hours = 1
        try:
            minute = int(schedule.get("minute", 0))
        except Exception:
            minute = 0
        return f"каждые {hours}ч в :{minute:02d} UTC"
    if schedule_type == "weekly":
        byday = schedule.get("byday")
        if isinstance(byday, list):
            days = ",".join(str(item) for item in byday if str(item).strip())
        else:
            days = "MO"
        try:
            hour = int(schedule.get("hour", 9))
        except Exception:
            hour = 9
        try:
            minute = int(schedule.get("minute", 0))
        except Exception:
            minute = 0
        return f"по расписанию {days} {hour:02d}:{minute:02d} UTC"
    if schedule_type:
        return f"schedule_type={schedule_type}"
    return "расписание активно"


def _build_automation_message(
    *,
    name: str,
    focus: str,
    source_targets: list[str],
    is_news: bool,
) -> str:
    focus_text = str(focus or "").strip() or "general domain"
    if is_news:
        channels = ", ".join(source_targets or ["web"])
        return (
            f"Run a news intelligence cycle for {focus_text}. "
            f"Collect updates from {channels}, deduplicate overlaps, and provide a concise daily digest with links."
        )
    return (
        f"Run maintenance cycle for agent '{name}' focused on {focus_text}. "
        "Review recent context, extract key updates, and produce an actionable summary."
    )


def _infer_agent_spec_from_request(request_text: str) -> dict[str, Any]:
    raw = str(request_text or "").strip()
    lowered = raw.lower()

    name_match = _AGENT_NAME_QUOTED_PATTERN.search(raw)
    requested_name = _clean_focus_text(name_match.group("name")) if name_match is not None else ""

    focus_match = _AGENT_FOCUS_PATTERN.search(raw)
    requested_focus = _clean_focus_text(focus_match.group("focus")) if focus_match is not None else ""
    requested_focus = _strip_focus_tail(requested_focus)
    source_targets = _infer_source_targets(lowered)

    if not requested_focus:
        if any(token in lowered for token in ("news", "новост", "twitter", "reddit", "x.com")):
            requested_focus = "AI news and internet updates"
        elif any(token in lowered for token in ("code", "код", "python", "typescript", "git", "program")):
            requested_focus = "software engineering tasks"
        else:
            requested_focus = "general productivity"

    is_news = any(token in lowered for token in ("news", "новост", "reddit", "twitter", "x.com")) or bool(source_targets)
    is_coding = any(token in lowered for token in ("code", "код", "python", "typescript", "git", "program"))

    if requested_name:
        name = requested_name
    elif is_news:
        name = "News Scout"
    elif is_coding:
        name = "Code Copilot"
    else:
        name = "Custom Assistant"

    source_hint = ""
    if source_targets:
        source_hint = f"Primary source channels: {', '.join(source_targets)}. "

    if is_news:
        system_prompt = (
            f"You are {name}. You are a specialized news agent for {requested_focus}. "
            f"{source_hint}"
            "Track updates, summarize key developments, deduplicate overlap, and always include source links."
        )
        tools = ["web_search"]
    elif is_coding:
        system_prompt = (
            f"You are {name}. You are a specialized coding assistant for {requested_focus}. "
            "Propose implementation plans, write concise code, and include practical verification steps."
        )
        tools = ["web_search"] if "web" in source_targets else []
    else:
        system_prompt = (
            f"You are {name}. You are a specialized assistant for {requested_focus}. "
            f"{source_hint}"
            "Provide actionable and structured help, asking clarifying questions only when necessary."
        )
        tools = ["web_search"] if "web" in source_targets else []

    schedule_spec = _infer_schedule_spec(lowered)
    automation_spec: dict[str, Any] | None = None
    if schedule_spec is not None:
        automation_spec = {
            **schedule_spec,
            "message": _build_automation_message(
                name=name,
                focus=requested_focus,
                source_targets=source_targets,
                is_news=is_news,
            ),
        }

    return {
        "name": name,
        "focus": requested_focus,
        "system_prompt": system_prompt,
        "tools": tools,
        "source_targets": source_targets,
        "kind": "news" if is_news else ("coding" if is_coding else "general"),
        "automation": automation_spec,
    }


def _build_quickstart_agent_created_content(
    *,
    agent_id: str,
    agent_name: str,
    focus: str,
    automation: dict[str, Any] | None,
    automation_error: str | None,
) -> str:
    content = f"Готово. Создал агента '{agent_name}' (id: {agent_id}). Фокус: {focus or 'general'}."
    if isinstance(automation, dict):
        content += f" Запустил автоматический режим ({_automation_schedule_summary(automation)})."
    elif automation_error:
        content += f" Агент создан, но расписание включить не удалось: {automation_error}."
    else:
        content += " Можешь сразу запускать его задачи."
    return content


def _build_quickstart_chat_completion(
    *,
    request: Request,
    payload: ChatCompletionsRequest,
    content: str,
    agent_record: dict[str, Any],
    action_receipt: dict[str, Any],
    automation_record: dict[str, Any] | None = None,
    quickstart_spec: dict[str, Any] | None = None,
    idempotency: dict[str, Any] | None = None,
) -> dict[str, Any]:
    completion_id = f"chatcmpl-{uuid4().hex}"
    created = int(time.time())
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": "amaryllis-system",
        "provider": "amaryllis",
        "request_id": _request_id(request),
        "routing": {"mode": "quickstart", "source": "chat_intent"},
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
        "provenance": {
            "version": "provenance_v1",
            "strategy": "quickstart_agent_create",
            "grounded": True,
            "query": _last_user_query(_normalize_messages(payload.messages)),
            "coverage_pct": 100.0,
            "sources": [],
        },
        "tool_events": [],
        "quick_action": {
            "type": "agent_created",
            "agent": agent_record,
            "automation": automation_record,
            "quickstart_spec": quickstart_spec,
            "idempotency": idempotency,
            "action_receipt": action_receipt,
        },
    }


def _build_quickstart_stream_response(
    *,
    request: Request,
    content: str,
) -> StreamingResponse:
    completion_id = f"chatcmpl-{uuid4().hex}"
    created = int(time.time())
    request_id = _request_id(request)

    def event_stream():
        first_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": "amaryllis-system",
            "provider": "amaryllis",
            "request_id": request_id,
            "choices": [
                {
                    "index": 0,
                    "delta": {"role": "assistant"},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(first_chunk, ensure_ascii=False)}\n\n"
        payload_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": "amaryllis-system",
            "provider": "amaryllis",
            "request_id": request_id,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": content},
                    "finish_reason": None,
                }
            ],
        }
        yield f"data: {json.dumps(payload_chunk, ensure_ascii=False)}\n\n"
        done_chunk = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": "amaryllis-system",
            "provider": "amaryllis",
            "request_id": request_id,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop",
                }
            ],
        }
        yield f"data: {json.dumps(done_chunk, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def _maybe_handle_chat_agent_quickstart(
    *,
    request: Request,
    payload: ChatCompletionsRequest,
    normalized_messages: list[dict[str, Any]],
    actor_user_id: str | None,
) -> dict[str, Any] | None:
    if not payload.user_id:
        return None
    query = _last_user_query(normalized_messages)
    if not _looks_like_agent_quickstart_request(query):
        return None

    services = request.app.state.services
    spec = _infer_agent_spec_from_request(query)
    request_fingerprint = _chat_quickstart_payload_fingerprint(
        user_id=str(payload.user_id),
        query=query,
        session_id=payload.session_id,
        model=payload.model,
    )
    idempotency_key = _normalize_idempotency_key(request.headers.get("Idempotency-Key"))
    if not idempotency_key:
        idempotency_key = _normalize_idempotency_key(request.headers.get("X-Idempotency-Key"))
    idempotency_derived = False
    if not idempotency_key and str(payload.session_id or "").strip():
        idempotency_key = _chat_quickstart_auto_idempotency_key(
            user_id=str(payload.user_id),
            query=query,
            session_id=payload.session_id,
            model=payload.model,
        )
        idempotency_derived = True

    automation_record: dict[str, Any] | None = None
    automation_error: str | None = None
    idempotency_payload: dict[str, Any] | None = None
    if idempotency_key:
        memory_key = _chat_quickstart_idempotency_memory_key(idempotency_key)
        cached_item = services.database.get_user_memory_item(
            user_id=str(payload.user_id),
            key=memory_key,
        )
        if isinstance(cached_item, dict):
            cached_record = _load_quickstart_idempotency_record(str(cached_item.get("value") or ""))
            cached_fingerprint = str(cached_record.get("fingerprint") or "").strip()
            if cached_fingerprint and cached_fingerprint != request_fingerprint:
                raise ValidationError("Idempotency key already used with a different quickstart payload.")
            cached_agent_id = str(cached_record.get("agent_id") or "").strip()
            if cached_agent_id:
                cached_agent = services.agent_manager.get_agent(cached_agent_id)
                if (
                    cached_agent is not None
                    and str(cached_agent.user_id or "").strip() == str(payload.user_id).strip()
                ):
                    cached_automation_id = str(cached_record.get("automation_id") or "").strip()
                    cached_automation = (
                        services.automation_scheduler.get_automation(cached_automation_id)
                        if cached_automation_id
                        else None
                    )
                    cached_spec = cached_record.get("quickstart_spec")
                    if not isinstance(cached_spec, dict):
                        cached_spec = spec
                    content = str(cached_record.get("assistant_content") or "").strip()
                    if not content:
                        content = _build_quickstart_agent_created_content(
                            agent_id=cached_agent.id,
                            agent_name=cached_agent.name,
                            focus=str(cached_spec.get("focus") or ""),
                            automation=cached_automation if isinstance(cached_automation, dict) else None,
                            automation_error=None,
                        )
                    replay_receipt = _sign_action(
                        request,
                        action="chat_agent_quickstart",
                        payload={
                            "request": query,
                            "user_id": payload.user_id,
                            "agent_name": cached_agent.name,
                            "focus": str(cached_spec.get("focus") or ""),
                            "kind": str(cached_spec.get("kind") or "general"),
                            "sources": (
                                cached_spec.get("source_targets")
                                if isinstance(cached_spec.get("source_targets"), list)
                                else []
                            ),
                            "automation_enabled": isinstance(cached_automation, dict),
                            "idempotency_key": idempotency_key,
                            "idempotency_replayed": True,
                        },
                        actor=actor_user_id,
                        target_type="agent",
                        target_id=cached_agent.id,
                    )
                    replay_idempotency = {
                        "key": idempotency_key,
                        "fingerprint": request_fingerprint,
                        "replayed": True,
                        "derived": idempotency_derived,
                    }
                    if payload.stream:
                        return {"stream_response": _build_quickstart_stream_response(request=request, content=content)}
                    return _build_quickstart_chat_completion(
                        request=request,
                        payload=payload,
                        content=content,
                        agent_record=cached_agent.to_record(),
                        action_receipt=replay_receipt,
                        automation_record=cached_automation if isinstance(cached_automation, dict) else None,
                        quickstart_spec=cached_spec,
                        idempotency=replay_idempotency,
                    )
        idempotency_payload = {
            "key": idempotency_key,
            "fingerprint": request_fingerprint,
            "replayed": False,
            "derived": idempotency_derived,
        }

    try:
        agent = services.agent_manager.create_agent(
            name=str(spec.get("name") or "Custom Assistant"),
            system_prompt=str(spec.get("system_prompt") or "You are a helpful assistant."),
            model=payload.model,
            tools=spec.get("tools") if isinstance(spec.get("tools"), list) else [],
            user_id=payload.user_id,
        )
        automation_spec = spec.get("automation")
        if isinstance(automation_spec, dict):
            try:
                automation_record = services.automation_scheduler.create_automation(
                    agent_id=agent.id,
                    user_id=str(payload.user_id),
                    session_id=payload.session_id,
                    message=str(automation_spec.get("message") or "Run agent cycle"),
                    interval_sec=automation_spec.get("interval_sec"),
                    schedule_type=automation_spec.get("schedule_type"),
                    schedule=automation_spec.get("schedule"),
                    timezone_name=str(automation_spec.get("timezone") or "UTC"),
                    start_immediately=bool(automation_spec.get("start_immediately", False)),
                    mission_policy=None,
                )
            except Exception as exc:
                automation_error = str(exc)
    except Exception as exc:
        _sign_action(
            request,
            action="chat_agent_quickstart",
            payload={
                "request": query,
                "user_id": payload.user_id,
                "idempotency_key": idempotency_key or None,
            },
            actor=actor_user_id,
            target_type="agent",
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc

    receipt = _sign_action(
        request,
        action="chat_agent_quickstart",
        payload={
            "request": query,
            "user_id": payload.user_id,
            "agent_name": agent.name,
            "focus": str(spec.get("focus") or ""),
            "kind": str(spec.get("kind") or "general"),
            "sources": spec.get("source_targets") if isinstance(spec.get("source_targets"), list) else [],
            "automation_enabled": automation_record is not None,
            "automation_error": automation_error,
            "idempotency_key": idempotency_key or None,
            "idempotency_replayed": False if idempotency_payload is not None else None,
        },
        actor=actor_user_id,
        target_type="agent",
        target_id=agent.id,
    )
    content = _build_quickstart_agent_created_content(
        agent_id=agent.id,
        agent_name=agent.name,
        focus=str(spec.get("focus") or ""),
        automation=automation_record if isinstance(automation_record, dict) else None,
        automation_error=automation_error,
    )
    if idempotency_payload is not None:
        try:
            services.database.set_user_memory(
                user_id=str(payload.user_id),
                key=_chat_quickstart_idempotency_memory_key(idempotency_key),
                value=json.dumps(
                    {
                        "fingerprint": request_fingerprint,
                        "agent_id": agent.id,
                        "automation_id": (
                            str(automation_record.get("id") or "").strip()
                            if isinstance(automation_record, dict)
                            else None
                        ),
                        "quickstart_spec": spec,
                        "assistant_content": content,
                        "recorded_at": time.time(),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                source="chat_api.quickstart.idempotency",
            )
        except Exception:
            pass
    agent_record = agent.to_record()
    if payload.stream:
        return {"stream_response": _build_quickstart_stream_response(request=request, content=content)}
    return _build_quickstart_chat_completion(
        request=request,
        payload=payload,
        content=content,
        agent_record=agent_record,
        action_receipt=receipt,
        automation_record=automation_record,
        quickstart_spec=spec,
        idempotency=idempotency_payload,
    )


def _build_provenance_payload(
    request: Request,
    payload: ChatCompletionsRequest,
    normalized_messages: list[dict[str, Any]],
    *,
    top_k: int = 3,
) -> dict[str, Any]:
    query = _last_user_query(normalized_messages)
    base = {
        "version": "provenance_v1",
        "generated_at": _utc_now_iso(),
        "strategy": "none",
        "grounded": False,
        "query": query,
        "coverage_pct": 0.0,
        "sources": [],
    }
    if not payload.user_id or not query:
        return base

    services = request.app.state.services
    try:
        rows = services.memory_manager.debug_retrieval(
            user_id=payload.user_id,
            query=query,
            top_k=max(1, min(int(top_k), 8)),
        )
    except Exception as exc:
        return {
            **base,
            "strategy": "memory_retrieval_debug_v1",
            "errors": [str(exc)],
        }

    sources: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        text = str(row.get("text") or "").strip()
        score = float(row.get("score") or 0.0)
        if not text:
            continue
        sources.append(
            {
                "layer": "semantic_memory",
                "source_id": row.get("semantic_id"),
                "rank": int(row.get("rank") or 0),
                "kind": str(row.get("kind") or "fact"),
                "score": round(score, 6),
                "excerpt": text[:220],
                "created_at": row.get("created_at"),
            }
        )

    grounded = len(sources) > 0
    return {
        **base,
        "strategy": "memory_retrieval_debug_v1",
        "grounded": grounded,
        "coverage_pct": 100.0 if grounded else 0.0,
        "sources": sources,
    }


def _routing_fallback_used(routing: dict[str, Any] | None) -> bool:
    if not isinstance(routing, dict):
        return False
    final = routing.get("final")
    if isinstance(final, dict) and bool(final.get("fallback_used", False)):
        return True
    return bool(routing.get("fallback_used", False))


def _effective_routing_payload(
    *,
    request: Request,
    payload: ChatCompletionsRequest,
    tool_names: list[str],
    stream: bool,
) -> tuple[dict[str, Any] | None, str]:
    services = request.app.state.services
    route_payload = payload.routing.model_dump(exclude_none=True) if payload.routing is not None else None
    if route_payload is None:
        try:
            snapshot = services.observability.sre.snapshot()
            qos_status = services.qos_governor.reconcile(snapshot=snapshot)
            route_mode = str(qos_status.get("route_mode") or "balanced").strip().lower() or "balanced"
            route_payload = {"mode": route_mode, "require_stream": bool(stream)}
        except Exception:
            route_payload = None

    if route_payload is not None and tool_names and not bool(route_payload.get("require_tools", False)):
        route_payload["require_tools"] = True

    effective_mode = str((route_payload or {}).get("mode") or "balanced").strip().lower() or "balanced"
    return route_payload, effective_mode


def _env_int(name: str, *, default: int) -> int:
    raw = str(os.getenv(name, str(default)) or "").strip()
    try:
        return int(raw)
    except Exception:
        return int(default)


def _kv_pressure_thresholds_tokens() -> tuple[int, int, int]:
    elevated = max(1, _env_int("AMARYLLIS_KV_PRESSURE_ELEVATED_TOKENS", default=1536))
    high = max(elevated + 1, _env_int("AMARYLLIS_KV_PRESSURE_HIGH_TOKENS", default=3072))
    critical = max(high + 1, _env_int("AMARYLLIS_KV_PRESSURE_CRITICAL_TOKENS", default=6144))
    return elevated, high, critical


def _estimate_kv_cache_payload(
    payload: ChatCompletionsRequest,
    *,
    output_chars: int,
    tool_rounds: int,
) -> dict[str, Any]:
    prompt_chars = 0
    for item in payload.messages:
        if item.content:
            prompt_chars += len(item.content)
    completion_chars = max(0, int(output_chars))

    prompt_tokens = max(1, int(math.ceil(float(prompt_chars) / 4.0))) if prompt_chars > 0 else 1
    completion_tokens = int(math.ceil(float(completion_chars) / 4.0)) if completion_chars > 0 else 0
    planned_decode_tokens = max(1, int(payload.max_tokens))
    tool_overhead_tokens = max(0, int(tool_rounds)) * 96
    estimated_tokens = max(
        1,
        prompt_tokens + max(completion_tokens, planned_decode_tokens) + tool_overhead_tokens,
    )

    kv_bytes_per_token = max(512, _env_int("AMARYLLIS_KV_BYTES_PER_TOKEN", default=2048))
    estimated_bytes = int(estimated_tokens * kv_bytes_per_token)

    elevated_tokens, high_tokens, critical_tokens = _kv_pressure_thresholds_tokens()
    if estimated_tokens >= critical_tokens:
        pressure_state = "critical"
    elif estimated_tokens >= high_tokens:
        pressure_state = "high"
    elif estimated_tokens >= elevated_tokens:
        pressure_state = "elevated"
    else:
        pressure_state = "low"

    eviction_count = 0
    if pressure_state == "high":
        high_step = max(1, high_tokens - elevated_tokens)
        eviction_count = max(1, int((estimated_tokens - high_tokens) / high_step) + 1)
    elif pressure_state == "critical":
        critical_step = max(1, critical_tokens - high_tokens)
        eviction_count = max(2, int((estimated_tokens - critical_tokens) / critical_step) + 2)

    return {
        "pressure_state": pressure_state,
        "estimated_tokens": int(estimated_tokens),
        "estimated_bytes": int(estimated_bytes),
        "eviction_count": int(eviction_count),
    }


def _emit_generation_loop_metrics(
    request: Request,
    *,
    payload: ChatCompletionsRequest,
    provider: str,
    model: str,
    routing: dict[str, Any] | None,
    effective_mode: str | None,
    stream: bool,
    ttft_ms: float | None,
    total_latency_ms: float | None,
    chunks: int,
    output_chars: int,
    tool_rounds: int,
    provenance: dict[str, Any] | None,
) -> None:
    services = request.app.state.services
    mode = str(effective_mode or "").strip().lower()
    if not mode:
        mode = str((payload.routing.model_dump(exclude_none=True) if payload.routing else {}).get("mode") or "balanced")
    qos_mode = "balanced"
    qos_thermal_state = "unknown"
    try:
        qos_mode = str(services.qos_governor.mode or "balanced")
        qos_thermal_state = str(services.qos_governor.thermal_state or "unknown")
    except Exception:
        qos_mode = "balanced"
        qos_thermal_state = "unknown"
    event = {
        "request_id": _request_id(request),
        "session_id": payload.session_id,
        "user_id": payload.user_id,
        "provider": provider,
        "model": model,
        "mode": mode,
        "qos_mode": qos_mode,
        "thermal_state": qos_thermal_state,
        "stream": bool(stream),
        "fallback_used": _routing_fallback_used(routing),
        "ttft_ms": round(float(ttft_ms), 3) if ttft_ms is not None else None,
        "total_latency_ms": round(float(total_latency_ms), 3) if total_latency_ms is not None else None,
        "chunks": int(max(0, chunks)),
        "output_chars": int(max(0, output_chars)),
        "tool_rounds": int(max(0, tool_rounds)),
        "provenance_grounded": bool((provenance or {}).get("grounded", False)),
        "provenance_sources_count": len((provenance or {}).get("sources", []))
        if isinstance((provenance or {}).get("sources", []), list)
        else 0,
        "kv_cache": _estimate_kv_cache_payload(
            payload,
            output_chars=int(max(0, output_chars)),
            tool_rounds=int(max(0, tool_rounds)),
        ),
    }
    try:
        services.telemetry.emit("generation_loop_metrics", event)
    except Exception:
        pass


def _validate_chat_request_limits(payload: ChatCompletionsRequest, request: Request) -> None:
    services = request.app.state.services
    max_messages = max(1, int(services.config.chat_max_messages))
    max_input_chars = max(1, int(services.config.chat_max_input_chars))
    max_tokens = max(1, int(services.config.chat_max_tokens))

    if len(payload.messages) > max_messages:
        raise ValidationError(
            f"messages limit exceeded ({len(payload.messages)} > {max_messages})"
        )

    input_chars = 0
    for item in payload.messages:
        if item.content:
            input_chars += len(item.content)
    if input_chars > max_input_chars:
        raise ValidationError(
            f"input size limit exceeded ({input_chars} chars > {max_input_chars})"
        )

    if payload.max_tokens > max_tokens:
        raise ValidationError(
            f"max_tokens exceeds server limit ({payload.max_tokens} > {max_tokens})"
        )


def _normalize_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for message in messages:
        normalized.append(message.model_dump(exclude_none=True))
    return normalized


def _tool_names_from_request(payload: ChatCompletionsRequest, request: Request) -> list[str]:
    if not payload.tools:
        return []

    services = request.app.state.services
    names: list[str] = []
    for tool in payload.tools:
        name = tool.function.name
        if services.tool_registry.get(name) is not None:
            names.append(name)
    return names


def _chat_once(
    request: Request,
    payload: ChatCompletionsRequest,
    messages: list[dict[str, Any]],
    provider: str | None,
    model: str | None,
    routing: dict[str, Any] | None,
) -> dict[str, Any]:
    services = request.app.state.services
    return services.model_manager.chat(
        messages=messages,
        model=model,
        provider=provider,
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
        routing=routing,
        session_id=payload.session_id,
        user_id=payload.user_id,
    )


def _chat_with_tool_loop(
    request: Request,
    payload: ChatCompletionsRequest,
    messages: list[dict[str, Any]],
    tool_names: list[str],
    provider: str | None,
    model: str | None,
    routing: dict[str, Any] | None,
    max_tool_rounds: int,
) -> tuple[str, str, str, list[dict[str, Any]], dict[str, Any] | None]:
    services = request.app.state.services
    reasoning_messages = list(messages)

    if tool_names:
        reasoning_messages.append(
            {
                "role": "system",
                "content": services.tool_executor.render_tool_instruction(tool_names),
            }
        )

    first = _chat_once(
        request=request,
        payload=payload,
        messages=reasoning_messages,
        provider=provider,
        model=model,
        routing=routing,
    )
    response_text = str(first.get("content", "")).strip()
    provider_used = str(first.get("provider", payload.provider or "unknown"))
    model_used = str(first.get("model", payload.model or "unknown"))
    routing_used = first.get("routing") if isinstance(first.get("routing"), dict) else None
    tool_events: list[dict[str, Any]] = []
    permission_ids = [item.strip() for item in payload.permission_ids if item and item.strip()]

    if not tool_names:
        return response_text, provider_used, model_used, tool_events, routing_used

    for attempt in range(1, max(1, max_tool_rounds) + 1):
        parsed = services.tool_executor.parse_tool_call(response_text)
        if not parsed:
            break

        tool_name = str(parsed["name"])
        arguments = parsed["arguments"]
        event: dict[str, Any] = {
            "attempt": attempt,
            "tool": tool_name,
            "arguments": arguments,
            "status": "started",
        }
        if tool_name not in tool_names:
            event["status"] = "blocked"
            event["error"] = "Tool is not allowed"
            tool_events.append(event)
            break

        started_at = time.perf_counter()
        try:
            tool_result = services.tool_executor.execute(
                tool_name,
                arguments,
                request_id=_request_id(request),
                user_id=payload.user_id,
                session_id=payload.session_id,
                permission_ids=permission_ids,
                action_class="autonomous_model",
            )
            event["status"] = "succeeded"
            event["result"] = tool_result.get("result")
            if "permission_prompt" in tool_result:
                event["permission_prompt"] = tool_result["permission_prompt"]
        except PermissionRequiredError as exc:
            tool_result = {
                "tool": tool_name,
                "error": str(exc),
                "permission_prompt_id": exc.prompt_id,
            }
            event["status"] = "permission_required"
            event["error"] = str(exc)
            event["permission_prompt_id"] = exc.prompt_id
        except Exception as exc:
            tool_result = {
                "tool": tool_name,
                "error": str(exc),
            }
            event["status"] = "failed"
            event["error"] = str(exc)

        event["duration_ms"] = round((time.perf_counter() - started_at) * 1000.0, 2)

        tool_events.append(event)

        reasoning_messages.append({"role": "assistant", "content": response_text})
        reasoning_messages.append(
            {
                "role": "tool",
                "name": tool_name,
                "content": json.dumps(tool_result, ensure_ascii=False),
            }
        )
        reasoning_messages.append(
            {
                "role": "system",
                "content": "Use tool output and provide final answer for the user.",
            }
        )

        followup = _chat_once(
            request=request,
            payload=payload,
            messages=reasoning_messages,
            provider=provider,
            model=model,
            routing=routing,
        )
        response_text = str(followup.get("content", "")).strip()
        provider_used = str(followup.get("provider", provider_used))
        model_used = str(followup.get("model", model_used))
        if routing_used is None and isinstance(followup.get("routing"), dict):
            routing_used = followup.get("routing")

    return response_text, provider_used, model_used, tool_events, routing_used


@router.post("/v1/chat/completions")
def chat_completions(payload: ChatCompletionsRequest, request: Request):
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=payload.user_id, auth=auth)
    payload = payload.model_copy(update={"user_id": effective_user_id})

    if not payload.messages:
        raise ValidationError("messages must not be empty")
    _validate_chat_request_limits(payload=payload, request=request)

    services = request.app.state.services
    normalized_messages = _normalize_messages(payload.messages)
    quickstart_payload = _maybe_handle_chat_agent_quickstart(
        request=request,
        payload=payload,
        normalized_messages=normalized_messages,
        actor_user_id=auth.user_id,
    )
    if quickstart_payload is not None:
        stream_response = quickstart_payload.get("stream_response")
        if isinstance(stream_response, StreamingResponse):
            return stream_response
        return quickstart_payload

    tool_names = _tool_names_from_request(payload=payload, request=request)
    request_id = _request_id(request)
    provenance = _build_provenance_payload(
        request=request,
        payload=payload,
        normalized_messages=normalized_messages,
    )
    route_payload, effective_route_mode = _effective_routing_payload(
        request=request,
        payload=payload,
        tool_names=tool_names,
        stream=bool(payload.stream),
    )

    if payload.stream:
        stream_messages = list(normalized_messages)
        if tool_names:
            stream_messages.append(
                {
                    "role": "system",
                    "content": services.tool_executor.render_tool_instruction(tool_names),
                }
            )

        stream_started = time.perf_counter()
        try:
            iterator, provider_used, model_used, routing_used = services.model_manager.stream_chat(
                messages=stream_messages,
                model=payload.model,
                provider=payload.provider,
                temperature=payload.temperature,
                max_tokens=payload.max_tokens,
                routing=route_payload,
                session_id=payload.session_id,
                user_id=payload.user_id,
            )
        except Exception as exc:
            raise ProviderError(str(exc)) from exc

        completion_id = f"chatcmpl-{uuid4().hex}"
        created = int(time.time())

        def event_stream():
            first_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model_used,
                "provider": provider_used,
                "request_id": request_id,
                "routing": routing_used,
                "provenance": provenance,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant"},
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(first_chunk, ensure_ascii=False)}\n\n"

            stream_error = False
            first_content_ts: float | None = None
            chunk_count = 0
            output_chars = 0
            try:
                for chunk in iterator:
                    chunk_count += 1
                    output_chars += len(str(chunk))
                    if first_content_ts is None:
                        first_content_ts = time.perf_counter()
                    payload_chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model_used,
                        "provider": provider_used,
                        "request_id": request_id,
                        "routing": routing_used,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": chunk},
                                "finish_reason": None,
                            }
                        ],
                    }
                    yield f"data: {json.dumps(payload_chunk, ensure_ascii=False)}\n\n"
            except Exception as exc:
                stream_error = True
                error_chunk = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model_used,
                    "provider": provider_used,
                    "request_id": request_id,
                    "routing": routing_used,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": f"Error: {exc}"},
                            "finish_reason": "error",
                        }
                    ],
                }
                yield f"data: {json.dumps(error_chunk, ensure_ascii=False)}\n\n"

            total_latency_ms = (time.perf_counter() - stream_started) * 1000.0
            ttft_ms = ((first_content_ts - stream_started) * 1000.0) if first_content_ts is not None else None
            _emit_generation_loop_metrics(
                request=request,
                payload=payload,
                provider=provider_used,
                model=model_used,
                routing=routing_used,
                effective_mode=effective_route_mode,
                stream=True,
                ttft_ms=ttft_ms,
                total_latency_ms=total_latency_ms,
                chunks=chunk_count,
                output_chars=output_chars,
                tool_rounds=0,
                provenance=provenance,
            )
            done_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model_used,
                "provider": provider_used,
                "request_id": request_id,
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "error" if stream_error else "stop",
                    }
                ],
            }
            yield f"data: {json.dumps(done_chunk, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    non_stream_started = time.perf_counter()
    try:
        content, provider_used, model_used, tool_events, routing_used = _chat_with_tool_loop(
            request=request,
            payload=payload,
            messages=normalized_messages,
            tool_names=tool_names,
            provider=payload.provider,
            model=payload.model,
            routing=route_payload,
            max_tool_rounds=services.config.task_max_tool_rounds,
        )
    except Exception as exc:
        raise ProviderError(str(exc)) from exc
    total_latency_ms = (time.perf_counter() - non_stream_started) * 1000.0
    _emit_generation_loop_metrics(
        request=request,
        payload=payload,
        provider=provider_used,
        model=model_used,
        routing=routing_used,
        effective_mode=effective_route_mode,
        stream=False,
        ttft_ms=total_latency_ms,
        total_latency_ms=total_latency_ms,
        chunks=1,
        output_chars=len(content),
        tool_rounds=len(tool_events),
        provenance=provenance,
    )

    completion_id = f"chatcmpl-{uuid4().hex}"
    created = int(time.time())

    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model_used,
        "provider": provider_used,
        "request_id": request_id,
        "routing": routing_used,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
        "provenance": provenance,
        "tool_events": tool_events,
    }
