from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from automation.schedule import compute_next_run_at, normalize_schedule, validate_timezone
from news.pipeline import build_query_bundle
from news.outbound import SUPPORTED_NEWS_OUTBOUND_CHANNELS
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


def normalize_internet_scope(scope: dict[str, Any] | None) -> dict[str, Any]:
    raw = scope if isinstance(scope, dict) else {}
    normalized: dict[str, Any] = {}
    queries: list[str] = []
    for item in raw.get("queries", []):
        text = str(item or "").strip()
        if text and text not in queries:
            queries.append(text)
    include_domains: list[str] = []
    for item in raw.get("include_domains", []):
        text = str(item or "").strip().lower().lstrip(".")
        if text and text not in include_domains:
            include_domains.append(text)
    exclude_domains: list[str] = []
    for item in raw.get("exclude_domains", []):
        text = str(item or "").strip().lower().lstrip(".")
        if text and text not in exclude_domains:
            exclude_domains.append(text)
    seed_urls: list[str] = []
    for item in raw.get("seed_urls", []):
        text = str(item or "").strip()
        if text and text not in seed_urls:
            seed_urls.append(text)
    languages: list[str] = []
    for item in raw.get("languages", []):
        text = str(item or "").strip().lower()
        if text and text not in languages:
            languages.append(text)
    regions: list[str] = []
    for item in raw.get("regions", []):
        text = str(item or "").strip().lower()
        if text and text not in regions:
            regions.append(text)
    max_depth_raw = raw.get("max_depth", 1)
    try:
        max_depth = max(1, min(int(max_depth_raw), 5))
    except Exception:
        max_depth = 1

    normalized["queries"] = queries
    normalized["include_domains"] = include_domains
    normalized["exclude_domains"] = exclude_domains
    normalized["seed_urls"] = seed_urls
    normalized["languages"] = languages
    normalized["regions"] = regions
    normalized["max_depth"] = max_depth
    return normalized


def build_news_mission_instruction(
    *,
    topic: str,
    sources: list[str],
    window_hours: int,
    max_items_per_source: int,
    internet_scope: dict[str, Any] | None = None,
) -> str:
    source_text = ", ".join(sources)
    normalized_scope = normalize_internet_scope(internet_scope)
    scope_parts: list[str] = []
    if normalized_scope.get("include_domains"):
        scope_parts.append("include domains: " + ", ".join(normalized_scope["include_domains"][:8]))
    if normalized_scope.get("exclude_domains"):
        scope_parts.append("exclude domains: " + ", ".join(normalized_scope["exclude_domains"][:8]))
    if normalized_scope.get("queries"):
        scope_parts.append("focus queries: " + ", ".join(normalized_scope["queries"][:8]))
    if normalized_scope.get("seed_urls"):
        scope_parts.append("seed urls: " + ", ".join(normalized_scope["seed_urls"][:5]))
    scope_text = "; ".join(scope_parts) if scope_parts else "scope: global web"
    return (
        "Run autonomous news digest mission. "
        f"Topic: {topic}. "
        f"Sources: {source_text}. "
        f"Time window: last {window_hours} hours. "
        f"{scope_text}. "
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
    internet_scope: dict[str, Any] | None = None,
    source_overrides: dict[str, Any] | None = None,
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    normalized_topic = str(topic or "").strip()
    if not normalized_topic:
        raise ValueError("topic is required")
    normalized_sources = normalize_news_sources(sources)
    normalized_timezone = validate_timezone(timezone_name)
    normalized_window_hours = max(1, min(int(window_hours), 168))
    normalized_max_items = max(1, min(int(max_items_per_source), 100))
    normalized_scope = normalize_internet_scope(internet_scope)
    normalized_source_overrides = source_overrides if isinstance(source_overrides, dict) else {}
    query_bundle = build_query_bundle(
        topic=normalized_topic,
        queries=normalized_scope.get("queries"),
        include_domains=normalized_scope.get("include_domains"),
        seed_urls=normalized_scope.get("seed_urls"),
        max_depth=int(normalized_scope.get("max_depth", 1)),
    )
    if not query_bundle:
        query_bundle = [normalized_topic]

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
        internet_scope=normalized_scope,
    )
    metadata = {
        "contract": "news_mission_v1",
        "contract_version": "0.1.0-draft",
        "topic": normalized_topic,
        "sources": list(normalized_sources),
        "supported_outbound_channels": list(SUPPORTED_NEWS_OUTBOUND_CHANNELS),
        "window_hours": normalized_window_hours,
        "max_items_per_source": normalized_max_items,
        "internet_scope": deepcopy(normalized_scope),
        "query_bundle": list(query_bundle),
        "source_overrides": deepcopy(normalized_source_overrides),
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
            "internet_scope": deepcopy(normalized_scope),
            "query_bundle": list(query_bundle),
            "source_overrides": deepcopy(normalized_source_overrides),
            "delivery": {
                "deliver_to_inbox": True,
                "deliver_to_outbound": False,
                "supported_outbound_channels": list(SUPPORTED_NEWS_OUTBOUND_CHANNELS),
            },
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
        "internet_scope": deepcopy(normalized_scope),
        "query_bundle": list(query_bundle),
        "source_overrides": deepcopy(normalized_source_overrides),
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
