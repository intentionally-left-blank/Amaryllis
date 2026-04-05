from __future__ import annotations

import unittest

from news.outbound import NewsDigestOutboundDispatcher, normalize_outbound_policy_rows


class NewsOutboundTests(unittest.TestCase):
    def test_dispatch_dry_run_respects_limits_and_emits_events(self) -> None:
        dispatcher = NewsDigestOutboundDispatcher()
        digest = {
            "summary": "Daily AI digest summary",
            "sections": [
                {
                    "headline": "Model release",
                    "confidence": "high",
                    "source_refs": [{"url": "https://example.com/model"}],
                }
            ],
            "metrics": {"section_count": 1, "citation_coverage_rate": 1.0},
            "top_links": ["https://example.com/model"],
        }
        policy_rows = normalize_outbound_policy_rows(
            [
                {
                    "channel": "webhook",
                    "is_enabled": True,
                    "max_targets": 1,
                    "targets": [
                        "https://example.com/hook/main",
                        "https://example.com/hook/secondary",
                    ],
                },
                {
                    "channel": "email",
                    "is_enabled": True,
                    "max_targets": 2,
                    "targets": ["digest@example.com"],
                },
                {
                    "channel": "telegram",
                    "is_enabled": False,
                    "max_targets": 1,
                    "targets": ["123456"],
                },
            ]
        )

        report = dispatcher.dispatch(
            topic="AI",
            digest=digest,
            policy_rows=policy_rows,
            channels=None,
            dry_run=True,
        )
        summary = report.get("summary", {})
        self.assertEqual(int(summary.get("channels_considered", -1)), 3)
        self.assertEqual(int(summary.get("channels_sent", -1)), 2)
        self.assertEqual(int(summary.get("attempted_targets", -1)), 2)
        self.assertEqual(int(summary.get("delivered_targets", -1)), 2)
        self.assertEqual(int(summary.get("failed_targets", -1)), 0)
        channels = report.get("channels", [])
        self.assertEqual(len(channels), 3)
        webhook_report = next(item for item in channels if str(item.get("channel")) == "webhook")
        self.assertEqual(int(webhook_report.get("dropped_targets", -1)), 1)
        self.assertEqual(str(webhook_report.get("status")), "delivered")
        events = report.get("events", [])
        self.assertEqual(len(events), 2)
        self.assertTrue(all(str(item.get("status")).startswith("delivered") for item in events))

    def test_dispatch_real_mode_reports_skips_and_invalid_targets(self) -> None:
        dispatcher = NewsDigestOutboundDispatcher(
            smtp_host=None,
            smtp_from=None,
            telegram_bot_token=None,
        )
        digest = {
            "summary": "Digest",
            "sections": [{"headline": "One", "confidence": "medium", "source_refs": []}],
            "metrics": {"section_count": 1, "citation_coverage_rate": 0.0},
            "top_links": [],
        }
        report = dispatcher.dispatch(
            topic="AI",
            digest=digest,
            policy_rows=[
                {"channel": "webhook", "is_enabled": True, "max_targets": 1, "targets": ["not-a-url"]},
                {"channel": "email", "is_enabled": True, "max_targets": 1, "targets": ["digest@example.com"]},
                {"channel": "telegram", "is_enabled": True, "max_targets": 1, "targets": ["123456"]},
            ],
            dry_run=False,
        )
        channels = {str(item.get("channel")): item for item in report.get("channels", []) if isinstance(item, dict)}
        self.assertEqual(str(channels["webhook"].get("status")), "failed")
        self.assertEqual(str((channels["webhook"].get("results") or [])[0].get("status")), "failed_invalid_target")
        self.assertEqual(str(channels["email"].get("status")), "skipped")
        self.assertEqual(str((channels["email"].get("results") or [])[0].get("status")), "skipped_config_missing")
        self.assertEqual(str(channels["telegram"].get("status")), "skipped")
        self.assertEqual(str((channels["telegram"].get("results") or [])[0].get("status")), "skipped_config_missing")


if __name__ == "__main__":
    unittest.main()
