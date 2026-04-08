from __future__ import annotations

from datetime import datetime, timezone
import unittest

from automation.mission_planner import (
    apply_mission_template,
    build_mission_plan,
    list_mission_templates,
    mission_template_catalog,
    resolve_mission_schedule,
)


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

    def test_template_catalog_contains_phase9_defaults(self) -> None:
        templates = list_mission_templates()
        template_ids = {str(item.get("id")) for item in templates}
        self.assertEqual(
            template_ids,
            {
                "code_health",
                "security_audit",
                "release_guard",
                "runtime_watchdog",
                "ai_news_daily",
                "ai_research_brief_daily",
                "ai_monitoring_watch_hourly",
            },
        )
        catalog = mission_template_catalog()
        self.assertEqual(str(catalog.get("version")), "mission_template_catalog_v1")
        self.assertGreaterEqual(int(catalog.get("template_count", 0)), len(template_ids))
        lanes = catalog.get("lanes", [])
        self.assertIsInstance(lanes, list)
        self.assertIn("news", lanes)
        self.assertIn("research", lanes)
        self.assertIn("monitoring", lanes)

    def test_apply_template_uses_defaults_when_message_missing(self) -> None:
        resolved = apply_mission_template(
            template_id="release_guard",
            message=None,
            cadence_profile=None,
            start_immediately=None,
            schedule_type=None,
            schedule=None,
            interval_sec=None,
            max_attempts=None,
            budget=None,
        )
        self.assertEqual(str(resolved.get("cadence_profile")), "daily")
        self.assertEqual(bool(resolved.get("start_immediately")), False)
        self.assertEqual(int(resolved.get("max_attempts") or 0), 3)
        message = str(resolved.get("message") or "")
        self.assertIn("release guard mission", message.lower())
        mission_policy = resolved.get("mission_policy")
        self.assertIsInstance(mission_policy, dict)
        assert isinstance(mission_policy, dict)
        self.assertEqual(str(mission_policy.get("profile")), "release")
        slo = mission_policy.get("slo")
        self.assertIsInstance(slo, dict)
        assert isinstance(slo, dict)
        self.assertEqual(int(slo.get("disable_failures", 0)), 3)
        template = resolved.get("template")
        self.assertIsInstance(template, dict)
        assert isinstance(template, dict)
        self.assertEqual(str(template.get("id")), "release_guard")

    def test_apply_ai_news_daily_template(self) -> None:
        resolved = apply_mission_template(
            template_id="ai_news_daily",
            message=None,
            cadence_profile=None,
            start_immediately=None,
            schedule_type=None,
            schedule=None,
            interval_sec=None,
            max_attempts=None,
            budget=None,
        )
        self.assertEqual(str(resolved.get("cadence_profile")), "daily")
        self.assertEqual(str(resolved.get("schedule_type")), "weekly")
        schedule = resolved.get("schedule")
        self.assertIsInstance(schedule, dict)
        assert isinstance(schedule, dict)
        self.assertEqual(schedule.get("byday"), ["MO", "TU", "WE", "TH", "FR", "SA", "SU"])
        message = str(resolved.get("message") or "").lower()
        self.assertIn("ai news mission", message)
        self.assertIn("confidence markers", message)

    def test_apply_template_rejects_unknown_template(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported mission template"):
            apply_mission_template(
                template_id="does-not-exist",
                message="test",
                cadence_profile=None,
                start_immediately=None,
                schedule_type=None,
                schedule=None,
                interval_sec=None,
                max_attempts=None,
                budget=None,
            )

    def test_apply_ai_research_brief_daily_template(self) -> None:
        resolved = apply_mission_template(
            template_id="ai_research_brief_daily",
            message=None,
            cadence_profile=None,
            start_immediately=None,
            schedule_type=None,
            schedule=None,
            interval_sec=None,
            max_attempts=None,
            budget=None,
        )
        self.assertEqual(str(resolved.get("cadence_profile")), "daily")
        self.assertEqual(str(resolved.get("schedule_type")), "weekly")
        schedule = resolved.get("schedule")
        self.assertIsInstance(schedule, dict)
        assert isinstance(schedule, dict)
        self.assertEqual(int(schedule.get("hour", -1)), 10)
        self.assertEqual(int(schedule.get("minute", -1)), 0)
        template = resolved.get("template", {})
        self.assertEqual(str(template.get("lane")), "research")

    def test_apply_ai_monitoring_watch_hourly_template(self) -> None:
        resolved = apply_mission_template(
            template_id="ai_monitoring_watch_hourly",
            message=None,
            cadence_profile=None,
            start_immediately=None,
            schedule_type=None,
            schedule=None,
            interval_sec=None,
            max_attempts=None,
            budget=None,
        )
        self.assertEqual(str(resolved.get("cadence_profile")), "hourly")
        self.assertEqual(str(resolved.get("schedule_type")), "hourly")
        schedule = resolved.get("schedule")
        self.assertIsInstance(schedule, dict)
        assert isinstance(schedule, dict)
        self.assertEqual(int(schedule.get("interval_hours", -1)), 1)
        self.assertEqual(int(schedule.get("minute", -1)), 5)
        self.assertEqual(bool(resolved.get("start_immediately")), True)
        template = resolved.get("template", {})
        self.assertEqual(str(template.get("lane")), "monitoring")


if __name__ == "__main__":
    unittest.main()
