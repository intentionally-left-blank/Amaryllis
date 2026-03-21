from __future__ import annotations

from datetime import datetime, timezone
import unittest

from automation.mission_planner import build_mission_plan, resolve_mission_schedule


class MissionPlannerTests(unittest.TestCase):
    def test_resolve_workday_profile(self) -> None:
        schedule_type, schedule, interval = resolve_mission_schedule(
            cadence_profile="workday",
            schedule_type=None,
            schedule=None,
            interval_sec=None,
        )
        self.assertEqual(schedule_type, "weekly")
        self.assertEqual(schedule.get("byday"), ["MO", "TU", "WE", "TH", "FR"])
        self.assertEqual(int(schedule.get("hour", -1)), 9)
        self.assertEqual(int(schedule.get("minute", -1)), 0)
        self.assertEqual(interval, 7 * 24 * 3600)

    def test_resolve_hourly_profile(self) -> None:
        schedule_type, schedule, interval = resolve_mission_schedule(
            cadence_profile="hourly",
            schedule_type=None,
            schedule=None,
            interval_sec=None,
        )
        self.assertEqual(schedule_type, "hourly")
        self.assertEqual(int(schedule.get("interval_hours", -1)), 1)
        self.assertEqual(int(schedule.get("minute", -1)), 0)
        self.assertEqual(interval, 3600)

    def test_watch_profile_requires_explicit_schedule(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires explicit schedule"):
            resolve_mission_schedule(
                cadence_profile="watch_fs",
                schedule_type=None,
                schedule=None,
                interval_sec=None,
            )

    def test_build_plan_forces_review_gate_on_high_risk(self) -> None:
        plan = build_mission_plan(
            agent_id="agent-1",
            user_id="user-1",
            message="Run autonomous weekly code health mission",
            session_id="session-1",
            timezone_name="UTC",
            cadence_profile="weekly",
            start_immediately=True,
            schedule_type=None,
            schedule=None,
            interval_sec=None,
            simulation={"risk_summary": {"overall_risk_level": "high"}},
            now_utc=datetime(2026, 3, 21, 10, 0, tzinfo=timezone.utc),
        )
        recommendation = plan.get("recommendation", {})
        self.assertEqual(bool(recommendation.get("requested_start_immediately")), True)
        self.assertEqual(bool(recommendation.get("effective_start_immediately")), False)
        risk = plan.get("risk", {})
        self.assertEqual(str(risk.get("overall")), "high")
        self.assertEqual(bool(risk.get("requires_review")), True)

    def test_build_plan_keeps_requested_start_for_medium_risk(self) -> None:
        plan = build_mission_plan(
            agent_id="agent-1",
            user_id="user-1",
            message="Run daily sync mission",
            session_id=None,
            timezone_name="UTC",
            cadence_profile="daily",
            start_immediately=True,
            schedule_type=None,
            schedule=None,
            interval_sec=None,
            simulation={"risk_summary": {"overall_risk_level": "medium"}},
            now_utc=datetime(2026, 3, 21, 10, 0, tzinfo=timezone.utc),
        )
        recommendation = plan.get("recommendation", {})
        self.assertEqual(bool(recommendation.get("effective_start_immediately")), True)
        apply_payload = plan.get("apply_payload", {})
        self.assertEqual(str(apply_payload.get("schedule_type")), "weekly")
        self.assertEqual(bool(apply_payload.get("start_immediately")), True)


if __name__ == "__main__":
    unittest.main()
