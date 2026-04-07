from __future__ import annotations

import unittest

from agents.factory import (
    apply_agent_spec_overrides,
    automation_schedule_summary,
    build_quickstart_agent_created_content,
    infer_agent_spec_from_request,
    looks_like_agent_quickstart_request,
)


class AgentFactoryTests(unittest.TestCase):
    def test_looks_like_quickstart_intent(self) -> None:
        self.assertTrue(looks_like_agent_quickstart_request("создай агента для AI новостей"))
        self.assertTrue(looks_like_agent_quickstart_request("create an agent for daily ai news"))
        self.assertFalse(looks_like_agent_quickstart_request("как создать агента"))
        self.assertFalse(looks_like_agent_quickstart_request("tell me about agents"))

    def test_infer_spec_with_domain_allowlist(self) -> None:
        spec = infer_agent_spec_from_request(
            "создай новостного агента для AI с сайтов https://openai.com/blog и huggingface.co каждый день в 07:45"
        )
        self.assertEqual(str(spec.get("kind")), "news")
        self.assertIn("web_search", spec.get("tools", []))
        source_policy = spec.get("source_policy", {})
        self.assertIsInstance(source_policy, dict)
        self.assertEqual(str(source_policy.get("mode")), "allowlist")
        domains = source_policy.get("domains", [])
        self.assertIn("openai.com", domains)
        self.assertIn("huggingface.co", domains)
        automation = spec.get("automation", {})
        self.assertIsInstance(automation, dict)
        self.assertEqual(str(automation.get("schedule_type")), "weekly")
        schedule = automation.get("schedule", {})
        self.assertEqual(int(schedule.get("hour", -1)), 7)
        self.assertEqual(int(schedule.get("minute", -1)), 45)

    def test_automation_schedule_summary_and_created_content(self) -> None:
        summary = automation_schedule_summary(
            {
                "schedule_type": "hourly",
                "schedule": {"interval_hours": 3, "minute": 5},
            }
        )
        self.assertIn("каждые 3ч", summary)
        content = build_quickstart_agent_created_content(
            agent_id="agent-123",
            agent_name="News Scout",
            focus="AI news",
            automation={"schedule_type": "hourly", "schedule": {"interval_hours": 3, "minute": 5}},
            automation_error=None,
        )
        self.assertIn("Создал агента", content)
        self.assertIn("автоматический режим", content)

    def test_apply_overrides_can_switch_profile_and_schedule(self) -> None:
        base = infer_agent_spec_from_request("создай агента для AI новостей каждый день в 08:15 из reddit")
        overridden = apply_agent_spec_overrides(
            spec=base,
            overrides={
                "kind": "coding",
                "name": "Build Pilot",
                "focus": "python tooling",
                "source_policy": {
                    "mode": "allowlist",
                    "domains": ["pypi.org", "github.com"],
                },
                "automation": {
                    "enabled": True,
                    "schedule_type": "hourly",
                    "schedule": {"interval_hours": 6, "minute": 10},
                },
            },
        )
        self.assertEqual(str(overridden.get("kind")), "coding")
        self.assertEqual(str(overridden.get("name")), "Build Pilot")
        self.assertEqual(str(overridden.get("focus")), "python tooling")
        source_policy = overridden.get("source_policy", {})
        self.assertEqual(str(source_policy.get("mode")), "allowlist")
        self.assertIn("pypi.org", source_policy.get("domains", []))
        self.assertIn("github.com", source_policy.get("domains", []))
        self.assertIn("web_search", overridden.get("tools", []))
        automation = overridden.get("automation", {})
        self.assertEqual(str(automation.get("schedule_type")), "hourly")
        schedule = automation.get("schedule", {})
        self.assertEqual(int(schedule.get("interval_hours", -1)), 6)
        self.assertEqual(int(schedule.get("minute", -1)), 10)

    def test_apply_overrides_can_disable_automation(self) -> None:
        base = infer_agent_spec_from_request("создай агента для AI новостей каждый день в 08:15")
        overridden = apply_agent_spec_overrides(
            spec=base,
            overrides={"automation": {"enabled": False}},
        )
        self.assertIsNone(overridden.get("automation"))

    def test_inference_reason_marks_mixed_intent_conflict(self) -> None:
        spec = infer_agent_spec_from_request("создай агента для AI новостей и python разработки из reddit")
        reason = spec.get("inference_reason", {})
        self.assertIsInstance(reason, dict)
        self.assertTrue(bool(reason.get("mixed_intent", False)))
        self.assertEqual(str(reason.get("resolved_kind")), "news")

    def test_hourly_schedule_does_not_false_match_weekday_from_python(self) -> None:
        spec = infer_agent_spec_from_request(
            "create an agent for python package maintenance from pypi.org and github.com every 6 hours at 10 minute"
        )
        automation = spec.get("automation", {})
        self.assertIsInstance(automation, dict)
        self.assertEqual(str(automation.get("schedule_type")), "hourly")
        schedule = automation.get("schedule", {})
        self.assertIsInstance(schedule, dict)
        self.assertEqual(int(schedule.get("interval_hours", -1)), 6)
        self.assertEqual(int(schedule.get("minute", -1)), 10)

    def test_weekday_schedule_infers_iana_timezone(self) -> None:
        spec = infer_agent_spec_from_request(
            "создай агента для AI новостей по будням в 09:30 timezone Asia/Almaty"
        )
        automation = spec.get("automation", {})
        self.assertIsInstance(automation, dict)
        self.assertEqual(str(automation.get("schedule_type")), "weekly")
        self.assertEqual(str(automation.get("timezone")), "Asia/Almaty")
        schedule = automation.get("schedule", {})
        self.assertIsInstance(schedule, dict)
        self.assertEqual(schedule.get("byday"), ["MO", "TU", "WE", "TH", "FR"])
        self.assertEqual(int(schedule.get("hour", -1)), 9)
        self.assertEqual(int(schedule.get("minute", -1)), 30)

    def test_weekend_schedule_infers_utc_offset_timezone(self) -> None:
        spec = infer_agent_spec_from_request(
            "create an agent for AI digest on weekends at 11:45 UTC+5"
        )
        automation = spec.get("automation", {})
        self.assertIsInstance(automation, dict)
        self.assertEqual(str(automation.get("schedule_type")), "weekly")
        self.assertEqual(str(automation.get("timezone")), "UTC+05:00")
        schedule = automation.get("schedule", {})
        self.assertIsInstance(schedule, dict)
        self.assertEqual(schedule.get("byday"), ["SA", "SU"])
        self.assertEqual(int(schedule.get("hour", -1)), 11)
        self.assertEqual(int(schedule.get("minute", -1)), 45)

    def test_weekday_schedule_infers_daypart_and_cyrillic_timezone_alias(self) -> None:
        spec = infer_agent_spec_from_request(
            "создай агента для AI новостей по будням утром по времени мск"
        )
        automation = spec.get("automation", {})
        self.assertIsInstance(automation, dict)
        self.assertEqual(str(automation.get("schedule_type")), "weekly")
        self.assertEqual(str(automation.get("timezone")), "Europe/Moscow")
        schedule = automation.get("schedule", {})
        self.assertIsInstance(schedule, dict)
        self.assertEqual(schedule.get("byday"), ["MO", "TU", "WE", "TH", "FR"])
        self.assertEqual(int(schedule.get("hour", -1)), 9)
        self.assertEqual(int(schedule.get("minute", -1)), 0)

    def test_daily_schedule_supports_ampm_and_timezone_abbreviation(self) -> None:
        spec = infer_agent_spec_from_request(
            "create an agent for AI digest every day at 8:30pm PST"
        )
        automation = spec.get("automation", {})
        self.assertIsInstance(automation, dict)
        self.assertEqual(str(automation.get("schedule_type")), "weekly")
        self.assertEqual(str(automation.get("timezone")), "UTC-08:00")
        schedule = automation.get("schedule", {})
        self.assertIsInstance(schedule, dict)
        self.assertEqual(schedule.get("byday"), ["MO", "TU", "WE", "TH", "FR", "SA", "SU"])
        self.assertEqual(int(schedule.get("hour", -1)), 20)
        self.assertEqual(int(schedule.get("minute", -1)), 30)

    def test_relative_hourly_schedule_starts_immediately(self) -> None:
        spec = infer_agent_spec_from_request(
            "create an agent for AI digest in 3 hours CET"
        )
        automation = spec.get("automation", {})
        self.assertIsInstance(automation, dict)
        self.assertEqual(str(automation.get("schedule_type")), "hourly")
        self.assertEqual(str(automation.get("timezone")), "UTC+01:00")
        self.assertTrue(bool(automation.get("start_immediately")))
        schedule = automation.get("schedule", {})
        self.assertIsInstance(schedule, dict)
        self.assertEqual(int(schedule.get("interval_hours", -1)), 3)
        self.assertEqual(int(schedule.get("minute", -1)), 0)

    def test_spanish_weekday_daypart_with_tokyo_timezone(self) -> None:
        spec = infer_agent_spec_from_request(
            "create an agent for AI digest entre semana por la manana timezone Tokyo"
        )
        automation = spec.get("automation", {})
        self.assertIsInstance(automation, dict)
        self.assertEqual(str(automation.get("schedule_type")), "weekly")
        self.assertEqual(str(automation.get("timezone")), "Asia/Tokyo")
        schedule = automation.get("schedule", {})
        self.assertIsInstance(schedule, dict)
        self.assertEqual(schedule.get("byday"), ["MO", "TU", "WE", "TH", "FR"])
        self.assertEqual(int(schedule.get("hour", -1)), 9)
        self.assertEqual(int(schedule.get("minute", -1)), 0)

    def test_spanish_weekend_dot_time_with_ist_timezone(self) -> None:
        spec = infer_agent_spec_from_request(
            "create an agent for AI digest fin de semana at 7.15 IST"
        )
        automation = spec.get("automation", {})
        self.assertIsInstance(automation, dict)
        self.assertEqual(str(automation.get("schedule_type")), "weekly")
        self.assertEqual(str(automation.get("timezone")), "UTC+05:30")
        schedule = automation.get("schedule", {})
        self.assertIsInstance(schedule, dict)
        self.assertEqual(schedule.get("byday"), ["SA", "SU"])
        self.assertEqual(int(schedule.get("hour", -1)), 7)
        self.assertEqual(int(schedule.get("minute", -1)), 15)

    def test_turkish_hourly_interval_with_kst_timezone(self) -> None:
        spec = infer_agent_spec_from_request(
            "create an agent for AI digest her 4 saat KST"
        )
        automation = spec.get("automation", {})
        self.assertIsInstance(automation, dict)
        self.assertEqual(str(automation.get("schedule_type")), "hourly")
        self.assertEqual(str(automation.get("timezone")), "UTC+09:00")
        schedule = automation.get("schedule", {})
        self.assertIsInstance(schedule, dict)
        self.assertEqual(int(schedule.get("interval_hours", -1)), 4)
        self.assertEqual(int(schedule.get("minute", -1)), 0)

    def test_portuguese_daily_hint_with_latam_timezone_alias(self) -> None:
        spec = infer_agent_spec_from_request(
            "create an agent for AI digest todo dia at 6:45 CDMX"
        )
        automation = spec.get("automation", {})
        self.assertIsInstance(automation, dict)
        self.assertEqual(str(automation.get("schedule_type")), "weekly")
        self.assertEqual(str(automation.get("timezone")), "America/Mexico_City")
        schedule = automation.get("schedule", {})
        self.assertIsInstance(schedule, dict)
        self.assertEqual(schedule.get("byday"), ["MO", "TU", "WE", "TH", "FR", "SA", "SU"])
        self.assertEqual(int(schedule.get("hour", -1)), 6)
        self.assertEqual(int(schedule.get("minute", -1)), 45)

    def test_inference_reason_view_contains_ui_ready_summary(self) -> None:
        spec = infer_agent_spec_from_request("создай агента для AI новостей и python разработки из reddit")
        view = spec.get("inference_reason_view", {})
        self.assertIsInstance(view, dict)
        self.assertEqual(str(view.get("version")), "inference_reason_view_v1")
        self.assertEqual(str(view.get("resolved_kind")), "news")
        confidence = view.get("confidence", {})
        self.assertIsInstance(confidence, dict)
        self.assertIn(str(confidence.get("level")), {"high", "medium", "low"})
        self.assertTrue(str(view.get("summary") or "").strip())

    def test_inference_reason_view_contains_timezone_disambiguation_hint_for_ist(self) -> None:
        spec = infer_agent_spec_from_request("create an agent for AI digest every day at 8:00 IST")
        automation = spec.get("automation", {})
        self.assertEqual(str(automation.get("timezone")), "UTC+05:30")
        view = spec.get("inference_reason_view", {})
        self.assertIsInstance(view, dict)
        hints = view.get("disambiguation_hints", [])
        self.assertIsInstance(hints, list)
        self.assertTrue(any("IST can mean" in str(item) for item in hints))

    def test_inference_reason_view_contains_locale_fallback_hint_for_spanish_cst(self) -> None:
        spec = infer_agent_spec_from_request(
            "create an agent for AI digest fin de semana at 7.15 CST"
        )
        automation = spec.get("automation", {})
        self.assertEqual(str(automation.get("timezone")), "UTC-06:00")
        view = spec.get("inference_reason_view", {})
        self.assertIsInstance(view, dict)
        hints = view.get("disambiguation_hints", [])
        self.assertIsInstance(hints, list)
        self.assertTrue(any("Locale fallback (es)" in str(item) for item in hints))
        self.assertTrue(any("America/Mexico_City" in str(item) for item in hints))


if __name__ == "__main__":
    unittest.main()
