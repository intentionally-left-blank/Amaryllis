from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from automation.schedule import compute_next_run_at, normalize_schedule, validate_timezone
from sources.base import SUPPORTED_NEWS_SOURCES, normalize_source_name


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_news_sources(sources: list[str] | None) -> list[str]:
    if not sources:
        return ["web"]
    normalized: list[str] = []
    seen: set[str] = set()
    for item in sources:
        source = normalize_source_name(str(item))
        if source in seen:
            continue
        seen.add(source)
        normalized.append(source)
    if not normalized:
        return ["web"]
    return normalized


def build_news_mission_instruction(
    *,
    topic: str,
    sources: list[str],
    window_hours: int,
    max_items_per_source: int,
) -> str:
    source_text = ", ".join(sources)
    return (
        "Run autonomous news digest mission. "
        f"Topic: {topic}. "
        f"Sources: {source_text}. "
        f"Time window: last {window_hours} hours. "
        f"Collect up to {max_items_per_source} relevant items per source, deduplicate similar stories, "
        "and produce a concise digest with citation links and confidence notes."
    )


def build_news_mission_plan(
    *,
    agent_id: str,
    user_id: str,
    topic: str,
    timezone_name: str,
    sources: list[str] | None = None,
    window_hours: int = 24,
    max_items_per_source: int = 20,
    schedule_type: str | None = None,
    schedule: dict[str, Any] | None = None,
    interval_sec: int | None = None,
    start_immediately: bool = False,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    normalized_topic = str(topic or "").strip()
    if not normalized_topic:
        raise ValueError("topic is required")
    normalized_sources = normalize_news_sources(sources)
    normalized_timezone = validate_timezone(timezone_name)
    normalized_window_hours = max(1, min(int(window_hours), 168))
    normalized_max_items = max(1, min(int(max_items_per_source), 100))

    if schedule_type or schedule or interval_sec is not None:
        resolved_type, resolved_schedule, resolved_interval = normalize_schedule(
            schedule_type=schedule_type,
            schedule=schedule,
            interval_sec=interval_sec,
        )
    else:
        resolved_type, resolved_schedule, resolved_interval = normalize_schedule(
            schedule_type="weekly",
            schedule={
                "byday": ["MO", "TU", "WE", "TH", "FR", "SA", "SU"],
                "hour": 9,
                "minute": 0,
            },
            interval_sec=None,
        )

    current = now_utc or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)

    next_run_at = (
        current.isoformat()
        if bool(start_immediately)
        else compute_next_run_at(
            schedule_type=resolved_type,
            schedule=resolved_schedule,
            timezone_name=normalized_timezone,
            now_utc=current,
        )
    )

    message = build_news_mission_instruction(
        topic=normalized_topic,
        sources=normalized_sources,
        window_hours=normalized_window_hours,
        max_items_per_source=normalized_max_items,
    )
    metadata = {
        "contract": "news_mission_v1",
        "contract_version": "0.1.0-draft",
        "topic": normalized_topic,
        "sources": list(normalized_sources),
        "window_hours": normalized_window_hours,
        "max_items_per_source": normalized_max_items,
        "generated_at": _utc_now_iso(),
    }
    apply_payload = {
        "agent_id": str(agent_id or "").strip(),
        "user_id": str(user_id or "").strip(),
        "message": message,
        "session_id": None,
        "interval_sec": resolved_interval,
        "schedule_type": resolved_type,
        "schedule": deepcopy(resolved_schedule),
        "timezone": normalized_timezone,
        "start_immediately": bool(start_immediately),
        "mission_policy": {
            "profile": "balanced",
            "max_items_per_source": normalized_max_items,
            "topic": normalized_topic,
            "sources": list(normalized_sources),
            "window_hours": normalized_window_hours,
        },
    }
    return {
        "agent_id": str(agent_id or "").strip(),
        "user_id": str(user_id or "").strip(),
        "topic": normalized_topic,
        "sources": list(normalized_sources),
        "supported_sources": list(SUPPORTED_NEWS_SOURCES),
        "window_hours": normalized_window_hours,
        "max_items_per_source": normalized_max_items,
        "schedule_type": resolved_type,
        "schedule": deepcopy(resolved_schedule),
        "interval_sec": resolved_interval,
        "timezone": normalized_timezone,
        "start_immediately": bool(start_immediately),
        "next_run_at": next_run_at,
        "message": message,
        "metadata": metadata,
        "apply_payload": apply_payload,
    }

