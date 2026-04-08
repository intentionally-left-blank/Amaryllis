from __future__ import annotations

from typing import Any


def list_mission_templates() -> list[dict[str, Any]]:
    from automation.mission_planner import list_mission_templates as _list_mission_templates

    return _list_mission_templates()


def mission_template_catalog() -> dict[str, Any]:
    from automation.mission_planner import mission_template_catalog as _mission_template_catalog

    return _mission_template_catalog()


def list_mission_policy_profiles() -> list[dict[str, Any]]:
    from automation.mission_policy import list_mission_policy_profiles as _list_mission_policy_profiles

    return _list_mission_policy_profiles()


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
    mission_policy_profile: str | None,
    mission_policy: dict[str, Any] | None,
) -> dict[str, Any]:
    from automation.mission_planner import apply_mission_template as _apply_mission_template

    return _apply_mission_template(
        template_id=template_id,
        message=message,
        cadence_profile=cadence_profile,
        start_immediately=start_immediately,
        schedule_type=schedule_type,
        schedule=schedule,
        interval_sec=interval_sec,
        max_attempts=max_attempts,
        budget=budget,
        mission_policy_profile=mission_policy_profile,
        mission_policy=mission_policy,
    )


def build_mission_plan(
    *,
    agent_id: str,
    user_id: str,
    message: str,
    session_id: str | None,
    timezone_name: str,
    cadence_profile: str,
    start_immediately: bool,
    schedule_type: str | None,
    schedule: dict[str, Any] | None,
    interval_sec: int | None,
    simulation: dict[str, Any],
) -> dict[str, Any]:
    from automation.mission_planner import build_mission_plan as _build_mission_plan

    return _build_mission_plan(
        agent_id=agent_id,
        user_id=user_id,
        message=message,
        session_id=session_id,
        timezone_name=timezone_name,
        cadence_profile=cadence_profile,
        start_immediately=start_immediately,
        schedule_type=schedule_type,
        schedule=schedule,
        interval_sec=interval_sec,
        simulation=simulation,
    )
