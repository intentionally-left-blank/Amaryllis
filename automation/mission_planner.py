from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from automation.mission_policy import resolve_mission_policy_overlay
from automation.schedule import compute_next_run_at, normalize_schedule, validate_timezone

_SUPPORTED_CADENCE_PROFILES: tuple[str, ...] = (
    "hourly",
    "daily",
    "workday",
    "weekly",
    "watch_fs",
)

_MISSION_TEMPLATE_REGISTRY: dict[str, dict[str, Any]] = {
    "code_health": {
        "id": "code_health",
        "name": "Code Health Sweep",
        "description": "Run lint/test/dependency checks and return remediation backlog.",
        "default_message": (
            "Run code health sweep: execute lint/tests, detect flaky or failing areas, "
            "summarize root causes, and propose prioritized fixes."
        ),
        "cadence_profile": "workday",
        "start_immediately": False,
        "max_attempts": 3,
        "budget": {"max_steps": 24, "max_tool_calls": 40, "timeout_sec": 900},
        "mission_policy_profile": "balanced",
        "risk_tags": ["quality", "maintenance"],
    },
    "security_audit": {
        "id": "security_audit",
        "name": "Security Audit",
        "description": "Review dependency, secrets, and policy posture with explicit risk findings.",
        "default_message": (
            "Run security audit: scan dependencies/secrets/config, classify risks, "
            "and prepare mitigation plan with urgency levels."
        ),
        "cadence_profile": "weekly",
        "start_immediately": False,
        "max_attempts": 4,
        "budget": {"max_steps": 28, "max_tool_calls": 48, "timeout_sec": 1200},
        "mission_policy_profile": "strict",
        "risk_tags": ["security", "compliance"],
    },
    "release_guard": {
        "id": "release_guard",
        "name": "Release Guard",
        "description": "Validate release readiness gates and summarize go/no-go blockers.",
        "default_message": (
            "Run release guard mission: validate quality gates, highlight blockers, "
            "and provide go/no-go recommendation with rollback notes."
        ),
        "cadence_profile": "daily",
        "start_immediately": False,
        "max_attempts": 3,
        "budget": {"max_steps": 22, "max_tool_calls": 36, "timeout_sec": 900},
        "mission_policy_profile": "release",
        "risk_tags": ["release", "reliability"],
    },
    "runtime_watchdog": {
        "id": "runtime_watchdog",
        "name": "Runtime Watchdog",
        "description": "Continuously watch runtime health and auto-surface incident hints.",
        "default_message": (
            "Run runtime watchdog mission: inspect health/slo/incidents, detect regressions, "
            "and generate actionable recovery checklist."
        ),
        "cadence_profile": "hourly",
        "start_immediately": True,
        "max_attempts": 2,
        "budget": {"max_steps": 16, "max_tool_calls": 24, "timeout_sec": 600},
        "mission_policy_profile": "watchdog",
        "risk_tags": ["ops", "watchdog"],
    },
    "ai_news_daily": {
        "id": "ai_news_daily",
        "name": "AI News Daily Digest",
        "description": "Collect, deduplicate, and summarize daily AI news with source-grounded citations.",
        "default_message": (
            "Run daily AI news mission: collect updates from web, reddit, and x for the last 24 hours; "
            "deduplicate overlapping stories by canonical URL; produce a concise digest with source links "
            "and confidence markers for each section."
        ),
        "cadence_profile": "daily",
        "start_immediately": False,
        "max_attempts": 3,
        "budget": {"max_steps": 20, "max_tool_calls": 36, "timeout_sec": 900},
        "mission_policy_profile": "balanced",
        "risk_tags": ["news", "research"],
        "schedule_type": "weekly",
        "schedule": {
            "byday": ["MO", "TU", "WE", "TH", "FR", "SA", "SU"],
            "hour": 9,
            "minute": 0,
        },
    },
}


def list_mission_templates() -> list[dict[str, Any]]:
    return [deepcopy(_MISSION_TEMPLATE_REGISTRY[key]) for key in sorted(_MISSION_TEMPLATE_REGISTRY)]


def apply_mission_template(
    *,
    template_id: str | None,
    message: str | None,
    cadence_profile: str | None,
    start_immediately: bool | None,
    schedule_type: str | None,
    schedule: dict[str, Any] | None,
    interval_sec: int | None,
    max_attempts: int | None,
    budget: dict[str, Any] | None,
    mission_policy_profile: str | None = None,
    mission_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    template = _resolve_template(template_id)

    resolved_message = str(message or "").strip()
    if not resolved_message and template is not None:
        resolved_message = str(template.get("default_message") or "").strip()
    if not resolved_message:
        raise ValueError("message must be non-empty")

    resolved_cadence = str(cadence_profile or "").strip().lower()
    if not resolved_cadence and template is not None:
        resolved_cadence = str(template.get("cadence_profile") or "").strip().lower()
    if not resolved_cadence:
        resolved_cadence = "workday"

    resolved_policy_profile = str(mission_policy_profile or "").strip().lower()
    if not resolved_policy_profile and template is not None:
        resolved_policy_profile = str(template.get("mission_policy_profile") or "").strip().lower()
    resolved_policy = resolve_mission_policy_overlay(
        policy=mission_policy if isinstance(mission_policy, dict) else {},
        profile=resolved_policy_profile or None,
    )

    if start_immediately is None:
        resolved_start_immediately = bool(template.get("start_immediately")) if template is not None else False
    else:
        resolved_start_immediately = bool(start_immediately)

    resolved_max_attempts = max_attempts
    if resolved_max_attempts is None and template is not None:
        template_max_attempts = template.get("max_attempts")
        if template_max_attempts is not None:
            resolved_max_attempts = int(template_max_attempts)

    resolved_budget: dict[str, Any] = {}
    if template is not None and isinstance(template.get("budget"), dict):
        resolved_budget.update(deepcopy(template.get("budget", {})))
    if isinstance(budget, dict):
        resolved_budget.update(deepcopy(budget))

    explicit_schedule = bool(schedule_type) or bool(schedule) or (interval_sec is not None)
    resolved_schedule_type = schedule_type
    resolved_schedule = deepcopy(schedule) if isinstance(schedule, dict) else schedule
    resolved_interval = interval_sec
    if not explicit_schedule and template is not None:
        template_schedule_type = template.get("schedule_type")
        template_schedule = template.get("schedule")
        template_interval = template.get("interval_sec")
        if template_schedule_type is not None or template_schedule is not None or template_interval is not None:
            resolved_schedule_type = (
                str(template_schedule_type).strip() if template_schedule_type is not None else None
            )
            resolved_schedule = deepcopy(template_schedule) if isinstance(template_schedule, dict) else template_schedule
            resolved_interval = int(template_interval) if template_interval is not None else None

    return {
        "template": _template_view(template),
        "message": resolved_message,
        "cadence_profile": resolved_cadence,
        "start_immediately": resolved_start_immediately,
        "schedule_type": resolved_schedule_type,
        "schedule": resolved_schedule,
        "interval_sec": resolved_interval,
        "max_attempts": resolved_max_attempts,
        "budget": resolved_budget,
        "mission_policy": resolved_policy,
    }


def build_mission_plan(
    *,
    agent_id: str,
    user_id: str,
    message: str,
    session_id: str | None,
    timezone_name: str,
    cadence_profile: str | None,
    start_immediately: bool,
    schedule_type: str | None,
    schedule: dict[str, Any] | None,
    interval_sec: int | None,
    simulation: dict[str, Any],
    now_utc: datetime | None = None,
) -> dict[str, Any]:
    normalized_timezone = validate_timezone(timezone_name)
    normalized_message = str(message or "").strip()
    if not normalized_message:
        raise ValueError("message must be non-empty")

    resolved_type, resolved_schedule, resolved_interval = resolve_mission_schedule(
        cadence_profile=cadence_profile,
        schedule_type=schedule_type,
        schedule=schedule,
        interval_sec=interval_sec,
    )

    current = now_utc or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    else:
        current = current.astimezone(timezone.utc)

    next_run_at = compute_next_run_at(
        schedule_type=resolved_type,
        schedule=resolved_schedule,
        timezone_name=normalized_timezone,
        now_utc=current,
    )

    risk_summary = simulation.get("risk_summary")
    if not isinstance(risk_summary, dict):
        risk_summary = {}
    overall_risk = str(risk_summary.get("overall_risk_level") or "unknown").strip().lower() or "unknown"
    requires_review = overall_risk in {"high", "critical", "unknown"}

    recommended_start_immediately = bool(start_immediately and not requires_review)
    review_checklist = _build_review_checklist(
        overall_risk=overall_risk,
        schedule_type=resolved_type,
        requires_review=requires_review,
    )

    return {
        "agent_id": agent_id,
        "user_id": user_id,
        "session_id": session_id,
        "message": normalized_message,
        "cadence_profile": _normalize_profile(cadence_profile),
        "timezone": normalized_timezone,
        "schedule_type": resolved_type,
        "schedule": resolved_schedule,
        "interval_sec": resolved_interval,
        "next_run_at": next_run_at,
        "risk": {
            "overall": overall_risk,
            "requires_review": requires_review,
        },
        "recommendation": {
            "requested_start_immediately": bool(start_immediately),
            "effective_start_immediately": recommended_start_immediately,
            "review_checklist": review_checklist,
        },
        "apply_payload": {
            "agent_id": agent_id,
            "user_id": user_id,
            "message": normalized_message,
            "session_id": session_id,
            "interval_sec": resolved_interval,
            "schedule_type": resolved_type,
            "schedule": resolved_schedule,
            "timezone": normalized_timezone,
            "start_immediately": recommended_start_immediately,
        },
    }


def resolve_mission_schedule(
    *,
    cadence_profile: str | None,
    schedule_type: str | None,
    schedule: dict[str, Any] | None,
    interval_sec: int | None,
) -> tuple[str, dict[str, Any], int]:
    explicit_schedule = bool(schedule_type) or bool(schedule) or (interval_sec is not None)
    if explicit_schedule:
        return normalize_schedule(
            schedule_type=schedule_type,
            schedule=schedule,
            interval_sec=interval_sec,
        )

    profile = _normalize_profile(cadence_profile)
    if profile == "hourly":
        return normalize_schedule(
            schedule_type="hourly",
            schedule={"interval_hours": 1, "minute": 0},
            interval_sec=None,
        )

    if profile == "daily":
        return normalize_schedule(
            schedule_type="weekly",
            schedule={
                "byday": ["MO", "TU", "WE", "TH", "FR", "SA", "SU"],
                "hour": 9,
                "minute": 0,
            },
            interval_sec=None,
        )

    if profile == "weekly":
        return normalize_schedule(
            schedule_type="weekly",
            schedule={"byday": ["MO"], "hour": 9, "minute": 0},
            interval_sec=None,
        )

    if profile == "watch_fs":
        raise ValueError(
            "watch_fs cadence requires explicit schedule payload with path and poll settings"
        )

    return normalize_schedule(
        schedule_type="weekly",
        schedule={"byday": ["MO", "TU", "WE", "TH", "FR"], "hour": 9, "minute": 0},
        interval_sec=None,
    )


def _resolve_template(template_id: str | None) -> dict[str, Any] | None:
    normalized = str(template_id or "").strip().lower()
    if not normalized:
        return None
    normalized = normalized.replace("-", "_")
    selected = _MISSION_TEMPLATE_REGISTRY.get(normalized)
    if selected is None:
        raise ValueError(f"unsupported mission template: {template_id}")
    return deepcopy(selected)


def _template_view(template: dict[str, Any] | None) -> dict[str, Any] | None:
    if template is None:
        return None
    return {
        "id": str(template.get("id") or ""),
        "name": str(template.get("name") or ""),
        "description": str(template.get("description") or ""),
        "risk_tags": list(template.get("risk_tags") or []),
    }


def _normalize_profile(cadence_profile: str | None) -> str:
    normalized = str(cadence_profile or "workday").strip().lower() or "workday"
    if normalized not in _SUPPORTED_CADENCE_PROFILES:
        return "workday"
    return normalized


def _build_review_checklist(
    *,
    overall_risk: str,
    schedule_type: str,
    requires_review: bool,
) -> list[str]:
    checks: list[str] = [
        "Verify mission prompt is specific and bounded.",
        "Confirm target agent tools match mission intent.",
    ]
    if schedule_type == "watch_fs":
        checks.append("Validate watch_fs path/glob to avoid broad filesystem scans.")
    if requires_review:
        checks.append(
            f"Risk level is '{overall_risk}': run dry-run output review before enabling immediate scheduling."
        )
        checks.append("Start paused or delayed, then promote after first successful run.")
    else:
        checks.append("Risk level is acceptable for standard scheduler rollout.")
    return checks
