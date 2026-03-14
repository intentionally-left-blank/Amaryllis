from __future__ import annotations

import unittest

from planner.planner import Planner


class PlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = Planner()

    def test_simple_strategy_returns_direct_answer_step(self) -> None:
        steps = self.planner.create_plan(
            task="Hello",
            strategy="simple",
        )

        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].kind, "answer_direct")
        self.assertFalse(steps[0].requires_tools)

    def test_url_summary_task_builds_structured_pipeline(self) -> None:
        steps = self.planner.create_plan(
            task="Summarize https://example.com/docs/changelog and keep it concise",
            strategy="tool",
        )

        self.assertGreaterEqual(len(steps), 4)
        self.assertEqual(steps[0].kind, "analyze_request")
        self.assertEqual(steps[1].kind, "fetch_source")
        self.assertTrue(steps[1].requires_tools)
        self.assertEqual(steps[-1].kind, "summarize")
        self.assertIn(3, steps[-1].depends_on)

    def test_complex_multi_clause_task_has_parallel_branches_and_merge(self) -> None:
        steps = self.planner.create_plan(
            task="Draft API policy and map edge cases and define rollout checklist",
            strategy="complex",
        )

        merge_steps = [step for step in steps if step.kind == "merge_results"]
        self.assertEqual(len(merge_steps), 1)
        self.assertGreaterEqual(len(merge_steps[0].depends_on), 2)
        verify_steps = [step for step in steps if step.kind == "verify"]
        self.assertGreaterEqual(len(verify_steps), 1)


if __name__ == "__main__":
    unittest.main()
