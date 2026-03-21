from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from automation.schedule import compute_next_run_at, normalize_schedule, validate_timezone

_SUPPORTED_CADENCE_PROFILES: tuple[str, ...] = (
    "hourly",
    "daily",
    "workday",
    "weekly",
    "watch_fs",
)


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
