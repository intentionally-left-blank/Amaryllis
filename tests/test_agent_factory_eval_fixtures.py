from __future__ import annotations

import json
from pathlib import Path
import unittest

from agents.factory import infer_agent_spec_from_request


class AgentFactoryEvalFixturesTests(unittest.TestCase):
    def test_intent_inference_cases(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        fixture_path = repo_root / "eval" / "fixtures" / "agent_factory" / "intent_inference_cases.json"
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        cases = payload.get("cases", [])
        self.assertIsInstance(cases, list)

        for raw_case in cases:
            self.assertIsInstance(raw_case, dict)
            request = str(raw_case.get("request") or "")
            expected = raw_case.get("expected", {})
            self.assertTrue(request)
            self.assertIsInstance(expected, dict)
            spec = infer_agent_spec_from_request(request)

            self.assertEqual(str(spec.get("kind") or ""), str(expected.get("kind") or ""))
            source_policy = spec.get("source_policy", {})
            self.assertIsInstance(source_policy, dict)
            self.assertEqual(
                str(source_policy.get("mode") or ""),
                str(expected.get("source_policy_mode") or ""),
            )

            reason = spec.get("inference_reason", {})
            self.assertIsInstance(reason, dict)
            self.assertEqual(str(reason.get("resolved_kind") or ""), str(expected.get("kind") or ""))
            self.assertEqual(bool(reason.get("mixed_intent", False)), bool(expected.get("mixed_intent", False)))

            if "schedule_type" in expected:
                automation = spec.get("automation", {})
                self.assertIsInstance(automation, dict)
                self.assertEqual(str(automation.get("schedule_type") or ""), str(expected.get("schedule_type") or ""))
                schedule = automation.get("schedule", {})
                self.assertIsInstance(schedule, dict)
                if "interval_hours" in expected:
                    self.assertEqual(int(schedule.get("interval_hours", -1)), int(expected.get("interval_hours")))
                if "hour" in expected:
                    self.assertEqual(int(schedule.get("hour", -1)), int(expected.get("hour")))
                if "minute" in expected:
                    self.assertEqual(int(schedule.get("minute", -1)), int(expected.get("minute")))
                if "timezone" in expected:
                    self.assertEqual(str(automation.get("timezone") or ""), str(expected.get("timezone") or ""))
                if "byday" in expected:
                    self.assertEqual(list(schedule.get("byday") or []), list(expected.get("byday") or []))

    def test_quickstart_flow_cases(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        fixture_path = repo_root / "eval" / "fixtures" / "agent_factory" / "quickstart_flow_cases.json"
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        cases = payload.get("cases", [])
        self.assertIsInstance(cases, list)

        for raw_case in cases:
            self.assertIsInstance(raw_case, dict)
            request = str(raw_case.get("request") or "")
            expected = raw_case.get("expected", {})
            self.assertTrue(request)
            self.assertIsInstance(expected, dict)
            self.assertTrue(str(raw_case.get("id") or ""))
            self.assertIn("kind", expected)
            self.assertIn("source_policy_mode", expected)
            self.assertIn("schedule_type", expected)

            spec = infer_agent_spec_from_request(request)
            self.assertEqual(str(spec.get("kind") or ""), str(expected.get("kind") or ""))
            source_policy = spec.get("source_policy", {})
            self.assertIsInstance(source_policy, dict)
            self.assertEqual(
                str(source_policy.get("mode") or ""),
                str(expected.get("source_policy_mode") or ""),
            )

            automation = spec.get("automation")
            if isinstance(automation, dict):
                actual_schedule_type = str(automation.get("schedule_type") or "")
            else:
                actual_schedule_type = ""
            self.assertEqual(actual_schedule_type, str(expected.get("schedule_type") or ""))


if __name__ == "__main__":
    unittest.main()
