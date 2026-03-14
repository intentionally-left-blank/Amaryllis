from __future__ import annotations

import unittest

from controller.meta_controller import MetaController


class MetaControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.controller = MetaController()

    def test_simple_question_uses_simple_strategy(self) -> None:
        strategy = self.controller.choose_strategy(
            user_message="What is the difference between RAM and SSD?",
            tools_available=True,
        )
        self.assertEqual(strategy, "simple")

    def test_file_and_url_requests_use_tool_strategy_when_available(self) -> None:
        strategy = self.controller.choose_strategy(
            user_message="Read /tmp/log.txt and fetch https://example.com/latest updates",
            tools_available=True,
        )
        self.assertEqual(strategy, "tool")

    def test_multi_clause_request_promotes_complex_strategy(self) -> None:
        strategy = self.controller.choose_strategy(
            user_message=(
                "Plan a migration roadmap, compare two rollout strategies, "
                "and provide a risk matrix with mitigation phases."
            ),
            tools_available=True,
        )
        self.assertEqual(strategy, "complex")


if __name__ == "__main__":
    unittest.main()
