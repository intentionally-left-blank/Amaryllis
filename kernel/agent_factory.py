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
_SCHEDULE_TIME_AMPM_PATTERN = re.compile(
    r"(?:\bв\b|\bat\b)\s*(?P<hour>\d{1,2})(?:[:.](?P<minute>\d{1,2}))?\s*(?P<ampm>a\.?m\.?|p\.?m\.?)\b",
    flags=re.IGNORECASE,
)
_SCHEDULE_TIME_DOT_PATTERN = re.compile(r"(?:\bв\b|\bat\b)\s*(?P<hour>\d{1,2})\.(?P<minute>\d{1,2})", flags=re.IGNORECASE)
_MINUTE_ONLY_PATTERN = re.compile(
    r"(?:\bв\b|\bat\b)\s*(?P<minute>\d{1,2})\s*(?:минут(?:а|ы)?|minute(?:s)?)",
    flags=re.IGNORECASE,
)
_HOURLY_INTERVAL_PATTERN = re.compile(
    r"(?:каждые|every|cada|a\s*cada|her)\s*(?P<hours>\d{1,2})\s*(?:час(?:а|ов)?|hours?|horas?|saat|h|ч)\b",
    flags=re.IGNORECASE,
)
_RELATIVE_HOURLY_INTERVAL_PATTERN = re.compile(
    r"(?:через|in|en|em)\s*(?P<hours>\d{1,2})\s*(?:час(?:а|ов)?|hours?|horas?|saat|h|ч)\b",
    flags=re.IGNORECASE,
)
_HOUR_ONLY_PATTERN = re.compile(r"(?<!\d)(?P<hour>\d{1,2})\s*(?:час(?:а|ов)?|hours?)(?!\w)", flags=re.IGNORECASE)
_URL_PATTERN = re.compile(r"https?://[^\s,;]+", flags=re.IGNORECASE)
_SITE_FILTER_PATTERN = re.compile(r"\bsite:(?P<domain>[a-z0-9.-]+\.[a-z]{2,24})\b", flags=re.IGNORECASE)
_DOMAIN_PATTERN = re.compile(
    r"\b(?P<domain>[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+)\b",
    flags=re.IGNORECASE,
)
_WORD_TOKEN_PATTERN = re.compile(r"[a-zа-яё]+", flags=re.IGNORECASE)
_TIMEZONE_OFFSET_PATTERN = re.compile(
    r"\b(?:UTC|GMT)\s*(?P<sign>[+-])\s*(?P<hours>\d{1,2})(?::?(?P<minutes>\d{2}))?\b",
    flags=re.IGNORECASE,
)
_TIMEZONE_BARE_OFFSET_PATTERN = re.compile(
    r"^(?P<sign>[+-])?(?P<hours>\d{1,2})(?::?(?P<minutes>\d{2}))?$",
    flags=re.IGNORECASE,
)
_TIMEZONE_IANA_PATTERN = re.compile(
    r"\b(?P<tz>[A-Za-z][A-Za-z0-9_+-]{1,32}/[A-Za-z][A-Za-z0-9_+-]{1,32}(?:/[A-Za-z][A-Za-z0-9_+-]{1,32})?)\b",
    flags=re.IGNORECASE,
)
_TIMEZONE_CONTEXT_PATTERN = re.compile(
    r"(?:\b(?:timezone|time\s*zone|tz|gmt|utc)\b|(?:по\s+времени)|(?:часов(?:ой|ому)\s+пояс[ауе]?))"
    r"\s*(?::|=|is)?\s*(?P<tz>[A-Za-z0-9_+\-/:]{1,64})",
    flags=re.IGNORECASE,
)

_DAILY_SCHEDULE_TOKENS: tuple[str, ...] = (
    "каждый день",
    "ежеднев",
    "daily",
    "every day",
    "cada dia",
    "todos los dias",
    "todo dia",
    "todos os dias",
    "her gun",
)
_WEEKLY_SCHEDULE_TOKENS: tuple[str, ...] = (
    "еженед",
    "каждую неделю",
    "weekly",
    "every week",
    "cada semana",
    "toda semana",
    "todas as semanas",
    "her hafta",
)
_HOURLY_SCHEDULE_TOKENS: tuple[str, ...] = (
    "каждый час",
    "каждые",
    "hourly",
    "every hour",
    "cada hora",
    "a cada",
    "her saat",
)
_START_IMMEDIATELY_TOKENS: tuple[str, ...] = (
    "сразу",
    "прямо сейчас",
    "немедленно",
    "start now",
    "immediately",
    "run now",
    "asap",
    "ahora",
)
_WEEKDAY_GROUP_TOKENS: tuple[str, ...] = (
    "weekdays",
    "weekday",
    "workdays",
    "on weekdays",
    "будни",
    "по будням",
    "рабочие дни",
    "entre semana",
    "laborables",
    "dias laborales",
    "dias uteis",
    "dias úteis",
    "hafta ici",
)
_WEEKEND_GROUP_TOKENS: tuple[str, ...] = (
    "weekends",
    "weekend",
    "on weekends",
    "выходные",
    "по выходным",
    "fin de semana",
    "fines de semana",
    "fim de semana",
    "fins de semana",
    "hafta sonu",
)
_TIME_OF_DAY_HINTS: tuple[tuple[str, int, int], ...] = (
    ("утром", 9, 0),
    ("morning", 9, 0),
    ("manana", 9, 0),
    ("manha", 9, 0),
    ("sabah", 9, 0),
    ("днем", 14, 0),
    ("днём", 14, 0),
    ("afternoon", 14, 0),
    ("tarde", 16, 0),
    ("oglen", 12, 0),
    ("полдень", 12, 0),
    ("noon", 12, 0),
    ("вечером", 19, 0),
    ("evening", 19, 0),
    ("aksam", 19, 0),
    ("ночью", 22, 0),
    ("night", 22, 0),
    ("noche", 22, 0),
    ("noite", 22, 0),
)

_WEEKDAY_EXACT_TOKENS: dict[str, str] = {
    "пн": "MO",
    "monday": "MO",
    "mon": "MO",
    "вторник": "TU",
    "вт": "TU",
    "tuesday": "TU",
    "tue": "TU",
    "ср": "WE",
    "wednesday": "WE",
    "wed": "WE",
    "чт": "TH",
    "thursday": "TH",
    "thu": "TH",
    "пт": "FR",
    "friday": "FR",
    "fri": "FR",
    "сб": "SA",
    "saturday": "SA",
    "sat": "SA",
    "вс": "SU",
    "sunday": "SU",
    "sun": "SU",
}
_WEEKDAY_PREFIX_TOKENS: tuple[tuple[str, str], ...] = (
    ("понедель", "MO"),
    ("вторник", "TU"),
    ("сред", "WE"),
    ("четверг", "TH"),
    ("пятниц", "FR"),
    ("суббот", "SA"),
    ("воскрес", "SU"),
)
_TIMEZONE_TOKEN_MAP: dict[str, str] = {
    "utc": "UTC",
    "gmt": "UTC",
    "almaty": "Asia/Almaty",
    "astana": "Asia/Almaty",
    "алматы": "Asia/Almaty",
    "астана": "Asia/Almaty",
    "kz": "Asia/Almaty",
    "msk": "Europe/Moscow",
    "moscow": "Europe/Moscow",
    "мск": "Europe/Moscow",
    "москва": "Europe/Moscow",
    "spb": "Europe/Moscow",
    "piter": "Europe/Moscow",
    "питер": "Europe/Moscow",
    "санктпетербург": "Europe/Moscow",
    "stpetersburg": "Europe/Moscow",
    "saintpetersburg": "Europe/Moscow",
    "berlin": "Europe/Berlin",
    "london": "Europe/London",
    "cet": "UTC+01:00",
    "cest": "UTC+02:00",
    "eet": "UTC+02:00",
    "eest": "UTC+03:00",
    "tokyo": "Asia/Tokyo",
    "jst": "UTC+09:00",
    "seoul": "Asia/Seoul",
    "kst": "UTC+09:00",
    "india": "Asia/Kolkata",
    "indian": "Asia/Kolkata",
    "delhi": "Asia/Kolkata",
    "mumbai": "Asia/Kolkata",
    "kolkata": "Asia/Kolkata",
    "ist": "UTC+05:30",
    "singapore": "Asia/Singapore",
    "sgt": "UTC+08:00",
    "dubai": "Asia/Dubai",
    "uae": "Asia/Dubai",
    "gst": "UTC+04:00",
    "riyadh": "Asia/Riyadh",
    "ksa": "Asia/Riyadh",
    "kyiv": "Europe/Kyiv",
    "kiev": "Europe/Kyiv",
    "turkiye": "Europe/Istanbul",
    "turkey": "Europe/Istanbul",
    "istanbul": "Europe/Istanbul",
    "trt": "UTC+03:00",
    "mexico": "America/Mexico_City",
    "mexicocity": "America/Mexico_City",
    "cdmx": "America/Mexico_City",
    "bogota": "America/Bogota",
    "buenosaires": "America/Argentina/Buenos_Aires",
    "argentina": "America/Argentina/Buenos_Aires",
    "santiago": "America/Santiago",
    "saopaulo": "America/Sao_Paulo",
    "brasilia": "America/Sao_Paulo",
    "jakarta": "Asia/Jakarta",
    "manila": "Asia/Manila",
    "sydney": "Australia/Sydney",
    "aest": "UTC+10:00",
    "aedt": "UTC+11:00",
    "newyork": "America/New_York",
    "new_york": "America/New_York",
    "nyc": "America/New_York",
    "ny": "America/New_York",
    "losangeles": "America/Los_Angeles",
    "los_angeles": "America/Los_Angeles",
    "la": "America/Los_Angeles",
    "pst": "UTC-08:00",
    "pdt": "UTC-07:00",
    "est": "UTC-05:00",
    "edt": "UTC-04:00",
    "cst": "UTC-06:00",
    "cdt": "UTC-05:00",
    "mst": "UTC-07:00",
    "mdt": "UTC-06:00",
}

_AMBIGUOUS_TIMEZONE_HINTS: dict[str, str] = {
    "ist": "IST can mean India (UTC+05:30), Israel (UTC+02:00), or Irish time; use city or UTC offset if needed.",
    "cst": "CST can mean US Central (UTC-06:00) or China Standard Time (UTC+08:00); use city or UTC offset if needed.",
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
_NEWS_KIND_KEYWORDS: tuple[str, ...] = (
    "news",
    "новост",
    "digest",
    "дайджест",
    "headline",
    "обзор",
    "trend",
    "trends",
    "релиз",
    "launch",
)
_CODING_KIND_KEYWORDS: tuple[str, ...] = (
    "code",
    "coding",
    "код",
    "разработ",
    "developer",
    "python",
    "typescript",
    "javascript",
    "java",
    "golang",
    "rust",
    "debug",
    "bug",
    "test",
    "ci",
    "repo",
    "repository",
    "commit",
    "sdk",
    "api",
    "automation script",
)
_NEWS_SOURCE_WEIGHTS: dict[str, float] = {
    "reddit": 2.0,
    "twitter": 2.0,
    "hackernews": 2.0,
    "arxiv": 1.0,
    "web": 0.25,
}
_CODING_SOURCE_WEIGHTS: dict[str, float] = {
    "github": 2.0,
    "arxiv": 0.25,
}
_NEWS_DOMAIN_SUFFIXES: tuple[str, ...] = (
    "reddit.com",
    "x.com",
    "twitter.com",
    "news.ycombinator.com",
    "techcrunch.com",
    "theverge.com",
    "wired.com",
    "reuters.com",
    "bloomberg.com",
)
_CODING_DOMAIN_SUFFIXES: tuple[str, ...] = (
    "github.com",
    "gitlab.com",
    "bitbucket.org",
    "pypi.org",
    "npmjs.com",
    "crates.io",
    "developer.mozilla.org",
    "docs.python.org",
)


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
    ampm_match = _SCHEDULE_TIME_AMPM_PATTERN.search(text)
    if ampm_match is not None:
        try:
            hour = int(ampm_match.group("hour"))
        except Exception:
            hour = default_hour
        try:
            minute = int(ampm_match.group("minute") or default_minute)
        except Exception:
            minute = default_minute
        marker = str(ampm_match.group("ampm") or "").lower().replace(".", "")
        if marker == "pm" and hour < 12:
            hour += 12
        if marker == "am" and hour == 12:
            hour = 0
        hour = max(0, min(23, hour))
        minute = max(0, min(59, minute))
        return hour, minute
    dot_match = _SCHEDULE_TIME_DOT_PATTERN.search(text)
    if dot_match is not None:
        try:
            hour = int(dot_match.group("hour"))
        except Exception:
            hour = default_hour
        try:
            minute = int(dot_match.group("minute") or default_minute)
        except Exception:
            minute = default_minute
        hour = max(0, min(23, hour))
        minute = max(0, min(59, minute))
        return hour, minute
    match = _SCHEDULE_TIME_PATTERN.search(text)
    if match is None:
        minute_only = _MINUTE_ONLY_PATTERN.search(text)
        if minute_only is None:
            for token, hinted_hour, hinted_minute in _TIME_OF_DAY_HINTS:
                if re.search(rf"(?<![a-zа-яё]){re.escape(token)}(?![a-zа-яё])", text, flags=re.IGNORECASE):
                    return hinted_hour, hinted_minute
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
    words = [match.group(0).lower() for match in _WORD_TOKEN_PATTERN.finditer(lowered)]
    if not words:
        return []
    seen: list[str] = []
    for word in words:
        code = _WEEKDAY_EXACT_TOKENS.get(word)
        if code is None:
            for prefix, prefix_code in _WEEKDAY_PREFIX_TOKENS:
                if word.startswith(prefix):
                    code = prefix_code
                    break
        if code is not None and code not in seen:
            seen.append(code)
    return seen


def _normalize_timezone_name(raw_value: Any, *, default: str = "UTC") -> str:
    value = str(raw_value or "").strip()
    if not value:
        return default
    bare_offset_match = _TIMEZONE_BARE_OFFSET_PATTERN.fullmatch(value)
    if bare_offset_match is not None:
        sign = "-" if str(bare_offset_match.group("sign") or "") == "-" else "+"
        try:
            hours = int(bare_offset_match.group("hours") or "0")
        except Exception:
            hours = 0
        try:
            minutes = int(bare_offset_match.group("minutes") or "0")
        except Exception:
            minutes = 0
        hours = max(0, min(14, hours))
        minutes = max(0, min(59, minutes))
        return f"UTC{sign}{hours:02d}:{minutes:02d}"
    offset_match = _TIMEZONE_OFFSET_PATTERN.search(value)
    if offset_match is not None:
        sign = "-" if str(offset_match.group("sign") or "") == "-" else "+"
        try:
            hours = int(offset_match.group("hours") or "0")
        except Exception:
            hours = 0
        try:
            minutes = int(offset_match.group("minutes") or "0")
        except Exception:
            minutes = 0
        hours = max(0, min(14, hours))
        minutes = max(0, min(59, minutes))
        return f"UTC{sign}{hours:02d}:{minutes:02d}"
    if value.upper() in {"UTC", "GMT"}:
        return "UTC"
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_+-]{1,32}/[A-Za-z][A-Za-z0-9_+-]{1,32}(?:/[A-Za-z][A-Za-z0-9_+-]{1,32})?", value):
        return value
    lowered = value.lower()
    mapped = _TIMEZONE_TOKEN_MAP.get(lowered)
    if mapped is not None:
        return mapped
    compact = re.sub(r"[^a-zа-яё0-9_]+", "", lowered)
    mapped = _TIMEZONE_TOKEN_MAP.get(compact)
    if mapped is not None:
        return mapped
    return default


def _extract_timezone_hint(raw_text: str) -> str:
    text = str(raw_text or "").strip()
    if not text:
        return "UTC"

    offset_match = _TIMEZONE_OFFSET_PATTERN.search(text)
    if offset_match is not None:
        return _normalize_timezone_name(offset_match.group(0))

    context_match = _TIMEZONE_CONTEXT_PATTERN.search(text)
    if context_match is not None:
        candidate = str(context_match.group("tz") or "").strip(" ,.;:!?)]}\"'")
        if candidate:
            normalized = _normalize_timezone_name(candidate, default="")
            if normalized:
                return normalized

    iana_match = _TIMEZONE_IANA_PATTERN.search(text)
    if iana_match is not None:
        return _normalize_timezone_name(iana_match.group("tz"))

    words = [match.group(0).lower() for match in _WORD_TOKEN_PATTERN.finditer(text)]
    for word in words:
        mapped = _TIMEZONE_TOKEN_MAP.get(word)
        if mapped is not None:
            return mapped
    compact = re.sub(r"[^a-zа-яё0-9_]+", "", text.lower())
    if compact:
        mapped = _TIMEZONE_TOKEN_MAP.get(compact)
        if mapped is not None:
            return mapped
    return "UTC"


def _extract_timezone_disambiguation_hints(raw_text: str) -> list[str]:
    text = str(raw_text or "").strip()
    if not text:
        return []
    hints: list[str] = []
    words = [match.group(0).lower() for match in _WORD_TOKEN_PATTERN.finditer(text)]
    for word in words:
        hint = _AMBIGUOUS_TIMEZONE_HINTS.get(word)
        if hint is not None and hint not in hints:
            hints.append(hint)
    compact = re.sub(r"[^a-zа-яё0-9_]+", "", text.lower())
    compact_hint = _AMBIGUOUS_TIMEZONE_HINTS.get(compact)
    if compact_hint is not None and compact_hint not in hints:
        hints.append(compact_hint)
    return hints


def _extract_group_byday(lowered_text: str) -> list[str] | None:
    lowered = str(lowered_text or "").lower()
    if not lowered:
        return None
    if any(token in lowered for token in _WEEKDAY_GROUP_TOKENS):
        return ["MO", "TU", "WE", "TH", "FR"]
    if any(token in lowered for token in _WEEKEND_GROUP_TOKENS):
        return ["SA", "SU"]
    return None


def _infer_schedule_spec(raw_text: str) -> dict[str, Any] | None:
    text = str(raw_text or "").strip()
    lowered = text.lower()
    if not lowered:
        return None
    relative_interval_match = _RELATIVE_HOURLY_INTERVAL_PATTERN.search(lowered)
    start_immediately = any(token in lowered for token in _START_IMMEDIATELY_TOKENS) or relative_interval_match is not None
    timezone_name = _extract_timezone_hint(text)
    interval_match = _HOURLY_INTERVAL_PATTERN.search(lowered)
    direct_match = _HOUR_ONLY_PATTERN.search(lowered)
    has_hourly_hint = (
        any(token in lowered for token in _HOURLY_SCHEDULE_TOKENS)
        or interval_match is not None
        or relative_interval_match is not None
        or direct_match is not None
    )
    if has_hourly_hint:
        interval_hours = 1
        if interval_match is not None:
            try:
                interval_hours = int(interval_match.group("hours"))
            except Exception:
                interval_hours = 1
        else:
            if relative_interval_match is not None:
                try:
                    interval_hours = int(relative_interval_match.group("hours"))
                except Exception:
                    interval_hours = 1
            elif direct_match is not None:
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
            "timezone": timezone_name,
            "start_immediately": start_immediately,
        }

    byday_group = _extract_group_byday(lowered)
    weekday_codes = _extract_weekday_codes(lowered)
    is_daily = bool(any(token in lowered for token in _DAILY_SCHEDULE_TOKENS) and byday_group is None)
    is_weekly = any(token in lowered for token in _WEEKLY_SCHEDULE_TOKENS) or bool(weekday_codes or byday_group)
    if is_daily or is_weekly:
        hour, minute = _extract_time_hint(lowered, default_hour=9, default_minute=0)
        byday = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"] if is_daily else (byday_group or weekday_codes or ["MO"])
        return {
            "schedule_type": "weekly",
            "schedule": {"byday": byday, "hour": hour, "minute": minute},
            "interval_sec": 7 * 24 * 3600,
            "timezone": timezone_name,
            "start_immediately": start_immediately,
        }

    return None


def automation_schedule_summary(automation: dict[str, Any]) -> str:
    schedule_type = str(automation.get("schedule_type") or "")
    schedule = automation.get("schedule")
    if not isinstance(schedule, dict):
        schedule = {}
    timezone_name = _normalize_timezone_name(automation.get("timezone"), default="UTC")
    if schedule_type == "hourly":
        try:
            hours = int(schedule.get("interval_hours", 1))
        except Exception:
            hours = 1
        try:
            minute = int(schedule.get("minute", 0))
        except Exception:
            minute = 0
        return f"каждые {hours}ч в :{minute:02d} {timezone_name}"
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
        return f"по расписанию {days} {hour:02d}:{minute:02d} {timezone_name}"
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
            "timezone": _normalize_timezone_name(schedule_spec.get("timezone"), default="UTC"),
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
        "timezone": _normalize_timezone_name(schedule_spec.get("timezone"), default="UTC"),
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
        resolved["timezone"] = _normalize_timezone_name(override.get("timezone"), default="UTC")
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


def _matched_keywords(lowered_text: str, keywords: tuple[str, ...]) -> list[str]:
    lowered = str(lowered_text or "").lower()
    matched: list[str] = []
    for token in keywords:
        normalized = str(token or "").strip().lower()
        if not normalized:
            continue
        if normalized in lowered and normalized not in matched:
            matched.append(normalized)
    return matched


def _suffix_matches(*, domains: list[str], suffixes: tuple[str, ...]) -> list[str]:
    matched: list[str] = []
    for domain in domains:
        normalized_domain = str(domain or "").strip().lower()
        if not normalized_domain:
            continue
        for suffix in suffixes:
            normalized_suffix = str(suffix or "").strip().lower()
            if not normalized_suffix:
                continue
            if normalized_domain == normalized_suffix or normalized_domain.endswith(f".{normalized_suffix}"):
                if normalized_domain not in matched:
                    matched.append(normalized_domain)
                break
    return matched


def _kind_score_from_sources(*, source_targets: list[str], weights: dict[str, float]) -> float:
    score = 0.0
    for target in source_targets:
        score += float(weights.get(str(target or "").strip().lower(), 0.0))
    return score


def _resolve_kind_from_signals(
    *,
    lowered_text: str,
    requested_focus: str,
    source_targets: list[str],
    source_domains: list[str],
) -> tuple[str, dict[str, Any]]:
    focus_lowered = str(requested_focus or "").strip().lower()
    text_news_keywords = _matched_keywords(lowered_text, _NEWS_KIND_KEYWORDS)
    text_coding_keywords = _matched_keywords(lowered_text, _CODING_KIND_KEYWORDS)
    focus_news_keywords = _matched_keywords(focus_lowered, _NEWS_KIND_KEYWORDS)
    focus_coding_keywords = _matched_keywords(focus_lowered, _CODING_KIND_KEYWORDS)

    news_domain_matches = _suffix_matches(domains=source_domains, suffixes=_NEWS_DOMAIN_SUFFIXES)
    coding_domain_matches = _suffix_matches(domains=source_domains, suffixes=_CODING_DOMAIN_SUFFIXES)

    news_score = (
        float(len(text_news_keywords))
        + float(len(focus_news_keywords))
        + _kind_score_from_sources(source_targets=source_targets, weights=_NEWS_SOURCE_WEIGHTS)
        + float(len(news_domain_matches)) * 1.5
    )
    coding_score = (
        float(len(text_coding_keywords))
        + float(len(focus_coding_keywords))
        + _kind_score_from_sources(source_targets=source_targets, weights=_CODING_SOURCE_WEIGHTS)
        + float(len(coding_domain_matches)) * 1.5
    )

    resolved_kind = "general"
    conflict_resolution = "none"
    if news_score > coding_score and news_score > 0:
        resolved_kind = "news"
    elif coding_score > news_score and coding_score > 0:
        resolved_kind = "coding"
    elif news_score > 0 and coding_score > 0:
        # Tie-breaker prefers explicit source-discovery channels.
        source_news_weight = _kind_score_from_sources(source_targets=source_targets, weights=_NEWS_SOURCE_WEIGHTS)
        source_coding_weight = _kind_score_from_sources(source_targets=source_targets, weights=_CODING_SOURCE_WEIGHTS)
        if source_news_weight > source_coding_weight:
            resolved_kind = "news"
            conflict_resolution = "tie_break_by_news_source_weight"
        elif source_coding_weight > source_news_weight:
            resolved_kind = "coding"
            conflict_resolution = "tie_break_by_coding_source_weight"
        elif len(news_domain_matches) > len(coding_domain_matches):
            resolved_kind = "news"
            conflict_resolution = "tie_break_by_news_domain_count"
        elif len(coding_domain_matches) > len(news_domain_matches):
            resolved_kind = "coding"
            conflict_resolution = "tie_break_by_coding_domain_count"
        else:
            resolved_kind = "news"
            conflict_resolution = "tie_break_default_news"

    mixed_intent = bool(news_score >= 1.0 and coding_score >= 1.0)

    reason = {
        "source": "natural_language_weighted_resolution_v2",
        "resolved_kind": resolved_kind,
        "scores": {
            "news": round(news_score, 3),
            "coding": round(coding_score, 3),
        },
        "signals": {
            "text_news_keywords": text_news_keywords,
            "text_coding_keywords": text_coding_keywords,
            "focus_news_keywords": focus_news_keywords,
            "focus_coding_keywords": focus_coding_keywords,
            "source_targets": source_targets,
            "domain_allowlist": source_domains,
            "news_domain_matches": news_domain_matches,
            "coding_domain_matches": coding_domain_matches,
        },
        "mixed_intent": mixed_intent,
        "conflict_resolution": conflict_resolution,
    }
    return resolved_kind, reason


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def build_inference_reason_view(reason_payload: dict[str, Any] | None) -> dict[str, Any]:
    reason = reason_payload if isinstance(reason_payload, dict) else {}
    base_reason = reason.get("base") if isinstance(reason.get("base"), dict) else {}
    analytics = reason if isinstance(reason.get("scores"), dict) else base_reason
    scores = analytics.get("scores") if isinstance(analytics.get("scores"), dict) else {}
    news_score = _safe_float(scores.get("news"), default=0.0)
    coding_score = _safe_float(scores.get("coding"), default=0.0)
    score_gap = abs(news_score - coding_score)
    top_score = max(news_score, coding_score)
    if top_score >= 2.0 and score_gap >= 1.5:
        confidence_level = "high"
    elif top_score >= 1.0 and score_gap >= 0.75:
        confidence_level = "medium"
    else:
        confidence_level = "low"

    resolved_kind = str(reason.get("resolved_kind") or base_reason.get("resolved_kind") or "general").strip().lower() or "general"
    mixed_intent = bool(reason.get("mixed_intent", base_reason.get("mixed_intent", False)))
    conflict_resolution = str(reason.get("conflict_resolution") or base_reason.get("conflict_resolution") or "none")
    overrides_applied = reason.get("overrides_applied") if isinstance(reason.get("overrides_applied"), list) else []
    normalized_overrides = [str(item).strip() for item in overrides_applied if str(item).strip()]

    signals = analytics.get("signals") if isinstance(analytics.get("signals"), dict) else {}
    timezone_hints_raw = signals.get("timezone_disambiguation_hints")
    timezone_hints = (
        [str(item).strip() for item in timezone_hints_raw if str(item).strip()]
        if isinstance(timezone_hints_raw, list)
        else []
    )
    highlights_raw: list[str] = []
    for key in (
        "text_news_keywords",
        "text_coding_keywords",
        "focus_news_keywords",
        "focus_coding_keywords",
        "source_targets",
        "news_domain_matches",
        "coding_domain_matches",
    ):
        values = signals.get(key)
        if not isinstance(values, list):
            continue
        for raw_item in values:
            token = str(raw_item or "").strip()
            if token and token not in highlights_raw:
                highlights_raw.append(token)
            if len(highlights_raw) >= 10:
                break
        if len(highlights_raw) >= 10:
            break

    summary = f"Resolved kind={resolved_kind}."
    if normalized_overrides:
        summary += f" Overrides applied: {', '.join(normalized_overrides)}."
    elif mixed_intent:
        summary += (
            f" Mixed intent detected (news={news_score:.2f}, coding={coding_score:.2f}); "
            f"resolution={conflict_resolution}."
        )
    elif top_score > 0:
        summary += f" Dominant signal score gap={score_gap:.2f}."
    else:
        summary += " Fallback heuristics used due to weak explicit signals."
    if timezone_hints:
        summary += " Timezone abbreviation may be ambiguous; review disambiguation hints."

    return {
        "version": "inference_reason_view_v1",
        "resolved_kind": resolved_kind,
        "confidence": {
            "level": confidence_level,
            "score_gap": round(score_gap, 3),
            "scores": {
                "news": round(news_score, 3),
                "coding": round(coding_score, 3),
            },
        },
        "mixed_intent": mixed_intent,
        "conflict_resolution": conflict_resolution,
        "overrides_applied": normalized_overrides,
        "highlights": highlights_raw[:8],
        "disambiguation_hints": timezone_hints[:3],
        "summary": summary,
    }


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
    resolved_kind, inference_reason = _resolve_kind_from_signals(
        lowered_text=lowered,
        requested_focus=requested_focus,
        source_targets=source_targets,
        source_domains=source_domains,
    )
    timezone_disambiguation_hints = _extract_timezone_disambiguation_hints(raw)
    if timezone_disambiguation_hints:
        signals = inference_reason.get("signals")
        if not isinstance(signals, dict):
            signals = {}
        signals["timezone_disambiguation_hints"] = timezone_disambiguation_hints
        inference_reason["signals"] = signals

    if not requested_focus:
        if resolved_kind == "news":
            requested_focus = "AI news and internet updates"
        elif resolved_kind == "coding":
            requested_focus = "software engineering tasks"
        else:
            requested_focus = "general productivity"

    if requested_name:
        name = requested_name
    elif resolved_kind == "news":
        name = "News Scout"
    elif resolved_kind == "coding":
        name = "Code Copilot"
    else:
        name = "Custom Assistant"

    schedule_spec = _infer_schedule_spec(raw)
    composed = _compose_agent_spec(
        kind=resolved_kind,
        name=name,
        focus=requested_focus,
        source_targets=source_targets,
        source_domains=source_domains,
        schedule_spec=schedule_spec,
    )
    composed["inference_reason"] = inference_reason
    composed["inference_reason_view"] = build_inference_reason_view(inference_reason)
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
        if policy_mode in _SUPPORTED_SOURCE_POLICY_MODES:
            if policy_mode == "open_web":
                source_channels = []
                source_domains = []
            elif policy_mode == "channels":
                source_domains = []

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
    composed["inference_reason_view"] = build_inference_reason_view(reason_payload)
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
