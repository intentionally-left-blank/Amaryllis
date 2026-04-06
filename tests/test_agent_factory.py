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


if __name__ == "__main__":
    unittest.main()
