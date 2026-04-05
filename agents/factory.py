from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

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
_URL_PATTERN = re.compile(r"https?://[^\s,;]+", flags=re.IGNORECASE)
_SITE_FILTER_PATTERN = re.compile(r"\bsite:(?P<domain>[a-z0-9.-]+\.[a-z]{2,24})\b", flags=re.IGNORECASE)
_DOMAIN_PATTERN = re.compile(
    r"\b(?P<domain>[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+)\b",
    flags=re.IGNORECASE,
)

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
_SOURCE_DOMAIN_PREFIXES: tuple[tuple[str, str], ...] = (
    ("reddit.com", "reddit"),
    ("x.com", "twitter"),
    ("twitter.com", "twitter"),
    ("news.ycombinator.com", "hackernews"),
    ("arxiv.org", "arxiv"),
    ("github.com", "github"),
)
_SUPPORTED_AGENT_KINDS: tuple[str, ...] = ("news", "coding", "general")
_SUPPORTED_SOURCE_POLICY_MODES: tuple[str, ...] = ("open_web", "channels", "allowlist")


def looks_like_agent_quickstart_request(text: str) -> bool:
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


def automation_schedule_summary(automation: dict[str, Any]) -> str:
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


def _sanitize_source_channels(values: list[Any] | None) -> list[str]:
    if not isinstance(values, list):
        return []
    channels: list[str] = []
    for raw in values:
        token = str(raw or "").strip().lower()
        if not token:
            continue
        if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,40}", token) is None:
            continue
        if token in channels:
            continue
        channels.append(token)
        if len(channels) >= 12:
            break
    return channels


def _sanitize_domains(values: list[Any] | None) -> list[str]:
    if not isinstance(values, list):
        return []
    domains: list[str] = []
    for raw in values:
        normalized = _normalize_domain(str(raw or ""))
        if not normalized or normalized in domains:
            continue
        domains.append(normalized)
        if len(domains) >= 12:
            break
    return domains


def _sanitize_tools(values: list[Any] | None) -> list[str]:
    if not isinstance(values, list):
        return []
    tools: list[str] = []
    for raw in values:
        token = str(raw or "").strip()
        if not token:
            continue
        if token in tools:
            continue
        tools.append(token)
        if len(tools) >= 32:
            break
    return tools


def _default_tools_for_kind(kind: str, *, source_targets: list[str], source_domains: list[str]) -> list[str]:
    normalized_kind = str(kind or "").strip().lower()
    if normalized_kind == "news":
        return ["web_search"]
    if source_targets or source_domains:
        return ["web_search"]
    return []


def _build_system_prompt(
    *,
    kind: str,
    name: str,
    focus: str,
    source_targets: list[str],
    source_domains: list[str],
) -> str:
    normalized_kind = str(kind or "").strip().lower()
    channel_hint = ""
    if source_targets:
        channel_hint = f"Primary source channels: {', '.join(source_targets)}. "
    domain_hint = ""
    if source_domains:
        domain_hint = f"Prefer these domains when possible: {', '.join(source_domains)}. "

    if normalized_kind == "news":
        return (
            f"You are {name}. You are a specialized news agent for {focus}. "
            f"{channel_hint}{domain_hint}"
            "Track updates, summarize key developments, deduplicate overlap, and always include source links."
        )
    if normalized_kind == "coding":
        return (
            f"You are {name}. You are a specialized coding assistant for {focus}. "
            f"{domain_hint}"
            "Propose implementation plans, write concise code, and include practical verification steps."
        )
    return (
        f"You are {name}. You are a specialized assistant for {focus}. "
        f"{channel_hint}{domain_hint}"
        "Provide actionable and structured help, asking clarifying questions only when necessary."
    )


def _normalize_schedule_spec(schedule_spec: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(schedule_spec, dict):
        return None
    schedule_type = str(schedule_spec.get("schedule_type") or "").strip().lower()
    if schedule_type not in {"hourly", "weekly"}:
        return None
    schedule = schedule_spec.get("schedule")
    if not isinstance(schedule, dict):
        schedule = {}

    if schedule_type == "hourly":
        try:
            interval_hours = int(schedule.get("interval_hours", 1))
        except Exception:
            interval_hours = 1
        try:
            minute = int(schedule.get("minute", 0))
        except Exception:
            minute = 0
        interval_hours = max(1, min(24, interval_hours))
        minute = max(0, min(59, minute))
        interval_sec = int(schedule_spec.get("interval_sec") or interval_hours * 3600)
        interval_sec = max(3600, min(24 * 3600, interval_sec))
        return {
            "schedule_type": "hourly",
            "schedule": {
                "interval_hours": interval_hours,
                "minute": minute,
            },
            "interval_sec": interval_sec,
            "timezone": str(schedule_spec.get("timezone") or "UTC"),
            "start_immediately": bool(schedule_spec.get("start_immediately", False)),
        }

    raw_byday = schedule.get("byday")
    byday: list[str] = []
    if isinstance(raw_byday, list):
        for raw_code in raw_byday:
            code = str(raw_code or "").strip().upper()
            if code not in {"MO", "TU", "WE", "TH", "FR", "SA", "SU"}:
                continue
            if code not in byday:
                byday.append(code)
    if not byday:
        byday = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
    try:
        hour = int(schedule.get("hour", 9))
    except Exception:
        hour = 9
    try:
        minute = int(schedule.get("minute", 0))
    except Exception:
        minute = 0
    hour = max(0, min(23, hour))
    minute = max(0, min(59, minute))
    interval_sec = int(schedule_spec.get("interval_sec") or 7 * 24 * 3600)
    interval_sec = max(3600, min(31 * 24 * 3600, interval_sec))
    return {
        "schedule_type": "weekly",
        "schedule": {
            "byday": byday,
            "hour": hour,
            "minute": minute,
        },
        "interval_sec": interval_sec,
        "timezone": str(schedule_spec.get("timezone") or "UTC"),
        "start_immediately": bool(schedule_spec.get("start_immediately", False)),
    }


def _schedule_spec_from_automation(automation: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(automation, dict):
        return None
    return _normalize_schedule_spec(
        {
            "schedule_type": automation.get("schedule_type"),
            "schedule": automation.get("schedule"),
            "interval_sec": automation.get("interval_sec"),
            "timezone": automation.get("timezone"),
            "start_immediately": automation.get("start_immediately"),
        }
    )


def _default_schedule_spec() -> dict[str, Any]:
    return {
        "schedule_type": "weekly",
        "schedule": {
            "byday": ["MO", "TU", "WE", "TH", "FR", "SA", "SU"],
            "hour": 9,
            "minute": 0,
        },
        "interval_sec": 7 * 24 * 3600,
        "timezone": "UTC",
        "start_immediately": False,
    }


def _apply_automation_override(
    *,
    base_schedule_spec: dict[str, Any] | None,
    override: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(override, dict):
        return _normalize_schedule_spec(base_schedule_spec)

    enabled = override.get("enabled")
    if enabled is False:
        return None

    resolved = _normalize_schedule_spec(base_schedule_spec)
    if resolved is None and (enabled is True or any(key in override for key in ("schedule_type", "schedule", "interval_sec"))):
        resolved = _default_schedule_spec()
    if resolved is None:
        return None

    if override.get("schedule_type") is not None:
        resolved["schedule_type"] = str(override.get("schedule_type") or "").strip().lower()
    if isinstance(override.get("schedule"), dict):
        resolved["schedule"] = dict(override.get("schedule"))
    if override.get("interval_sec") is not None:
        try:
            resolved["interval_sec"] = int(override.get("interval_sec"))
        except Exception:
            pass
    if override.get("timezone") is not None:
        resolved["timezone"] = str(override.get("timezone") or "UTC")
    if override.get("start_immediately") is not None:
        resolved["start_immediately"] = bool(override.get("start_immediately"))
    return _normalize_schedule_spec(resolved)


def _compose_agent_spec(
    *,
    kind: str,
    name: str,
    focus: str,
    source_targets: list[str],
    source_domains: list[str],
    schedule_spec: dict[str, Any] | None,
    tools_override: list[str] | None = None,
) -> dict[str, Any]:
    normalized_kind = str(kind or "general").strip().lower()
    if normalized_kind not in _SUPPORTED_AGENT_KINDS:
        normalized_kind = "general"
    resolved_name = _clean_focus_text(name) or "Custom Assistant"
    resolved_focus = _clean_focus_text(focus) or "general productivity"
    resolved_targets = _sanitize_source_channels(source_targets)
    resolved_domains = _sanitize_domains(source_domains)
    if resolved_domains and not resolved_targets:
        for domain in resolved_domains:
            source_target = _source_target_from_domain(domain)
            if source_target not in resolved_targets:
                resolved_targets.append(source_target)
        if "web" not in resolved_targets:
            resolved_targets.append("web")

    if tools_override is None:
        tools = _default_tools_for_kind(
            normalized_kind,
            source_targets=resolved_targets,
            source_domains=resolved_domains,
        )
    else:
        tools = _sanitize_tools(tools_override)

    automation_spec = _normalize_schedule_spec(schedule_spec)
    if automation_spec is not None:
        automation_spec["message"] = _build_automation_message(
            name=resolved_name,
            focus=resolved_focus,
            source_targets=resolved_targets,
            source_domains=resolved_domains,
            is_news=(normalized_kind == "news"),
        )

    return {
        "name": resolved_name,
        "focus": resolved_focus,
        "system_prompt": _build_system_prompt(
            kind=normalized_kind,
            name=resolved_name,
            focus=resolved_focus,
            source_targets=resolved_targets,
            source_domains=resolved_domains,
        ),
        "tools": tools,
        "source_targets": resolved_targets,
        "source_policy": _build_source_policy(
            source_targets=resolved_targets,
            domains=resolved_domains,
        ),
        "kind": normalized_kind,
        "automation": automation_spec,
    }


def _normalize_domain(raw: str) -> str:
    candidate = str(raw or "").strip().lower().strip(" ,.;:!?)]}\"'")
    if candidate.startswith("www."):
        candidate = candidate[4:]
    if not candidate or "." not in candidate:
        return ""
    parts = [part for part in candidate.split(".") if part]
    if len(parts) < 2:
        return ""
    tld = parts[-1]
    if re.fullmatch(r"[a-z]{2,24}", tld) is None:
        return ""
    if not any(any(ch.isalpha() for ch in part) for part in parts[:-1]):
        return ""
    if any(re.fullmatch(r"\d+", part) for part in parts):
        # Drop pure IPv4-like fragments and numeric pseudo domains.
        return ""
    return ".".join(parts)


def _extract_domain_allowlist(request_text: str) -> list[str]:
    text = str(request_text or "")
    candidates: list[str] = []
    for match in _URL_PATTERN.finditer(text):
        try:
            hostname = urlparse(match.group(0)).hostname
        except Exception:
            hostname = None
        normalized = _normalize_domain(str(hostname or ""))
        if normalized:
            candidates.append(normalized)
    for match in _SITE_FILTER_PATTERN.finditer(text):
        normalized = _normalize_domain(match.group("domain"))
        if normalized:
            candidates.append(normalized)
    for match in _DOMAIN_PATTERN.finditer(text):
        normalized = _normalize_domain(match.group("domain"))
        if normalized:
            candidates.append(normalized)
    deduped: list[str] = []
    for domain in candidates:
        if domain in deduped:
            continue
        deduped.append(domain)
        if len(deduped) >= 12:
            break
    return deduped


def _source_target_from_domain(domain: str) -> str:
    normalized = str(domain or "").strip().lower()
    if not normalized:
        return "web"
    for suffix, source in _SOURCE_DOMAIN_PREFIXES:
        if normalized == suffix or normalized.endswith(f".{suffix}"):
            return source
    return "web"


def _infer_source_targets(lowered_text: str, *, domains: list[str]) -> list[str]:
    targets: list[str] = []
    lowered = str(lowered_text or "").strip().lower()
    if lowered:
        for source_id, variants in _SOURCE_PATTERNS:
            if any(variant in lowered for variant in variants):
                targets.append(source_id)
        if ("новост" in lowered or "news" in lowered) and "web" not in targets:
            targets.append("web")
    for domain in domains:
        source_target = _source_target_from_domain(domain)
        if source_target not in targets:
            targets.append(source_target)
    if domains and "web" not in targets:
        targets.append("web")
    return targets


def _build_source_policy(*, source_targets: list[str], domains: list[str]) -> dict[str, Any]:
    if domains:
        mode = "allowlist"
    elif source_targets:
        mode = "channels"
    else:
        mode = "open_web"
    return {
        "mode": mode,
        "channels": source_targets,
        "domains": domains,
    }


def _build_automation_message(
    *,
    name: str,
    focus: str,
    source_targets: list[str],
    source_domains: list[str],
    is_news: bool,
) -> str:
    focus_text = str(focus or "").strip() or "general domain"
    source_hint = ""
    if source_targets:
        source_hint = f"Collect updates from {', '.join(source_targets)}"
    else:
        source_hint = "Collect updates from trusted web sources"
    if source_domains:
        source_hint += f" (focus on: {', '.join(source_domains)})."
    else:
        source_hint += "."
    if is_news:
        return (
            f"Run a news intelligence cycle for {focus_text}. "
            f"{source_hint} Deduplicate overlaps, and provide a concise digest with links."
        )
    return (
        f"Run maintenance cycle for agent '{name}' focused on {focus_text}. "
        "Review recent context, extract key updates, and produce an actionable summary."
    )


def infer_agent_spec_from_request(request_text: str) -> dict[str, Any]:
    raw = str(request_text or "").strip()
    lowered = raw.lower()
    source_domains = _extract_domain_allowlist(raw)

    name_match = _AGENT_NAME_QUOTED_PATTERN.search(raw)
    requested_name = _clean_focus_text(name_match.group("name")) if name_match is not None else ""

    focus_match = _AGENT_FOCUS_PATTERN.search(raw)
    requested_focus = _clean_focus_text(focus_match.group("focus")) if focus_match is not None else ""
    requested_focus = _strip_focus_tail(requested_focus)

    source_targets = _infer_source_targets(lowered, domains=source_domains)

    if not requested_focus:
        if any(token in lowered for token in ("news", "новост", "twitter", "reddit", "x.com")):
            requested_focus = "AI news and internet updates"
        elif any(token in lowered for token in ("code", "код", "python", "typescript", "git", "program")):
            requested_focus = "software engineering tasks"
        else:
            requested_focus = "general productivity"

    is_news = any(token in lowered for token in ("news", "новост", "reddit", "twitter", "x.com")) or bool(
        source_targets
    )
    is_coding = any(token in lowered for token in ("code", "код", "python", "typescript", "git", "program"))

    if requested_name:
        name = requested_name
    elif is_news:
        name = "News Scout"
    elif is_coding:
        name = "Code Copilot"
    else:
        name = "Custom Assistant"

    schedule_spec = _infer_schedule_spec(lowered)
    resolved_kind = "news" if is_news else ("coding" if is_coding else "general")
    composed = _compose_agent_spec(
        kind="news" if is_news else ("coding" if is_coding else "general"),
        name=name,
        focus=requested_focus,
        source_targets=source_targets,
        source_domains=source_domains,
        schedule_spec=schedule_spec,
    )
    composed["inference_reason"] = {
        "source": "natural_language_heuristics_v1",
        "resolved_kind": resolved_kind,
        "signals": {
            "news_signal": bool(is_news),
            "coding_signal": bool(is_coding),
            "source_targets_count": len(source_targets),
            "domain_allowlist_count": len(source_domains),
        },
        "mixed_intent": bool(is_news and is_coding),
        "conflict_resolution": (
            "news_priority_due_to_source_scope"
            if (is_news and is_coding)
            else "none"
        ),
    }
    return composed


def apply_agent_spec_overrides(*, spec: dict[str, Any], overrides: dict[str, Any] | None) -> dict[str, Any]:
    base = dict(spec or {})
    if not isinstance(overrides, dict) or not overrides:
        return base

    base_kind = str(base.get("kind") or "general").strip().lower()
    base_name = str(base.get("name") or "Custom Assistant")
    base_focus = str(base.get("focus") or "general productivity")
    base_source_policy = base.get("source_policy")
    if isinstance(base_source_policy, dict):
        base_channels = _sanitize_source_channels(base_source_policy.get("channels"))
        base_domains = _sanitize_domains(base_source_policy.get("domains"))
    else:
        base_channels = _sanitize_source_channels(base.get("source_targets"))
        base_domains = []

    override_kind = str(overrides.get("kind") or "").strip().lower()
    kind = override_kind if override_kind in _SUPPORTED_AGENT_KINDS else base_kind
    name = _clean_focus_text(str(overrides.get("name") or "")) or base_name
    focus = _clean_focus_text(str(overrides.get("focus") or "")) or base_focus

    source_channels = list(base_channels)
    source_domains = list(base_domains)
    source_policy_override = overrides.get("source_policy")
    if isinstance(source_policy_override, dict):
        if isinstance(source_policy_override.get("channels"), list):
            source_channels = _sanitize_source_channels(source_policy_override.get("channels"))
        if isinstance(source_policy_override.get("domains"), list):
            source_domains = _sanitize_domains(source_policy_override.get("domains"))
        policy_mode = str(source_policy_override.get("mode") or "").strip().lower()
        if policy_mode == "open_web":
            source_channels = []
            source_domains = []
        elif policy_mode == "channels":
            source_domains = []
        elif policy_mode == "allowlist":
            pass

    if source_domains and not source_channels:
        for domain in source_domains:
            source_target = _source_target_from_domain(domain)
            if source_target not in source_channels:
                source_channels.append(source_target)
        if "web" not in source_channels:
            source_channels.append("web")

    tools_override = _sanitize_tools(overrides.get("tools")) if isinstance(overrides.get("tools"), list) else None
    base_schedule_spec = _schedule_spec_from_automation(base.get("automation") if isinstance(base.get("automation"), dict) else None)
    schedule_spec = _apply_automation_override(
        base_schedule_spec=base_schedule_spec,
        override=overrides.get("automation") if isinstance(overrides.get("automation"), dict) else None,
    )

    composed = _compose_agent_spec(
        kind=kind,
        name=name,
        focus=focus,
        source_targets=source_channels,
        source_domains=source_domains,
        schedule_spec=schedule_spec,
        tools_override=tools_override,
    )
    base_reason = base.get("inference_reason")
    reason_payload: dict[str, Any] = {
        "source": "natural_language_heuristics_v1+overrides_v1",
        "resolved_kind": kind,
        "overrides_applied": sorted(str(key) for key in overrides.keys()),
    }
    if isinstance(base_reason, dict):
        reason_payload["base"] = base_reason
    composed["inference_reason"] = reason_payload
    return composed


def build_quickstart_agent_created_content(
    *,
    agent_id: str,
    agent_name: str,
    focus: str,
    automation: dict[str, Any] | None,
    automation_error: str | None,
) -> str:
    content = f"Готово. Создал агента '{agent_name}' (id: {agent_id}). Фокус: {focus or 'general'}."
    if isinstance(automation, dict):
        content += f" Запустил автоматический режим ({automation_schedule_summary(automation)})."
    elif automation_error:
        content += f" Агент создан, но расписание включить не удалось: {automation_error}."
    else:
        content += " Можешь сразу запускать его задачи."
    return content
