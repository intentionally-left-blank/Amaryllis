from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.config import AppConfig, AppConfigError


class RuntimeProfilesTests(unittest.TestCase):
    def test_default_profile_loads_dev_and_default_quality_budget(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-runtime-profile-") as tmp:
            support_dir = Path(tmp) / "support"
            with patch.dict(
                os.environ,
                {
                    "AMARYLLIS_SUPPORT_DIR": str(support_dir),
                    "AMARYLLIS_AUTH_TOKENS": "token-user:user-1:user",
                },
                clear=True,
            ):
                config = AppConfig.from_env()

        self.assertEqual(config.runtime_profile, "dev")
        self.assertEqual(config.slo_profile, "dev")
        self.assertEqual(config.runtime_profile_schema_version, 1)
        self.assertEqual(config.slo_profile_schema_version, 1)
        self.assertAlmostEqual(config.perf_budget_max_p95_latency_ms, 350.0)
        self.assertAlmostEqual(config.perf_budget_max_error_rate_pct, 0.0)

    def test_ci_profile_requires_auth_tokens(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-runtime-profile-") as tmp:
            support_dir = Path(tmp) / "support"
            with patch.dict(
                os.environ,
                {
                    "AMARYLLIS_SUPPORT_DIR": str(support_dir),
                    "AMARYLLIS_RUNTIME_PROFILE": "ci",
                    "AMARYLLIS_AUTH_TOKENS": "",
                    "AMARYLLIS_API_TOKEN": "",
                },
                clear=True,
            ):
                with self.assertRaisesRegex(AppConfigError, "requires non-empty env vars"):
                    AppConfig.from_env()

    def test_ci_profile_applies_ci_slo_targets(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-runtime-profile-") as tmp:
            support_dir = Path(tmp) / "support"
            with patch.dict(
                os.environ,
                {
                    "AMARYLLIS_SUPPORT_DIR": str(support_dir),
                    "AMARYLLIS_RUNTIME_PROFILE": "ci",
                    "AMARYLLIS_AUTH_TOKENS": "token-user:user-1:user",
                },
                clear=True,
            ):
                config = AppConfig.from_env()

        self.assertEqual(config.runtime_profile, "ci")
        self.assertEqual(config.slo_profile, "ci")
        self.assertEqual(config.api_release_channel, "beta")
        self.assertAlmostEqual(config.observability_request_availability_target, 0.996)
        self.assertAlmostEqual(config.observability_request_latency_p95_ms_target, 1000.0)
        self.assertAlmostEqual(config.observability_run_success_target, 0.985)

    def test_unknown_runtime_profile_fails_fast(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-runtime-profile-") as tmp:
            support_dir = Path(tmp) / "support"
            with patch.dict(
                os.environ,
                {
                    "AMARYLLIS_SUPPORT_DIR": str(support_dir),
                    "AMARYLLIS_RUNTIME_PROFILE": "unknown-profile",
                    "AMARYLLIS_AUTH_TOKENS": "token-user:user-1:user",
                },
                clear=True,
            ):
                with self.assertRaisesRegex(AppConfigError, "Profile manifest not found"):
                    AppConfig.from_env()

    def test_slo_profile_can_be_overridden_explicitly(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-runtime-profile-") as tmp:
            support_dir = Path(tmp) / "support"
            with patch.dict(
                os.environ,
                {
                    "AMARYLLIS_SUPPORT_DIR": str(support_dir),
                    "AMARYLLIS_RUNTIME_PROFILE": "release",
                    "AMARYLLIS_SLO_PROFILE": "dev",
                    "AMARYLLIS_AUTH_TOKENS": "token-user:user-1:user",
                },
                clear=True,
            ):
                config = AppConfig.from_env()

        self.assertEqual(config.runtime_profile, "release")
        self.assertEqual(config.slo_profile, "dev")
        self.assertAlmostEqual(config.perf_budget_max_p95_latency_ms, 350.0)


if __name__ == "__main__":
    unittest.main()
