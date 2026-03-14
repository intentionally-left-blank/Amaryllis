from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from runtime.config import AppConfig, AppConfigError


class ConfigSecurityDefaultsTests(unittest.TestCase):
    def test_defaults_use_strict_security_modes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-config-tests-") as tmp:
            support_dir = Path(tmp) / "support"
            with patch.dict(
                os.environ,
                {
                    "AMARYLLIS_SUPPORT_DIR": str(support_dir),
                    "AMARYLLIS_AUTH_TOKENS": "token-1:user-1:user",
                },
                clear=True,
            ):
                config = AppConfig.from_env()

        self.assertEqual(config.tool_approval_enforcement, "strict")
        self.assertEqual(config.plugin_signing_mode, "strict")
        self.assertTrue(config.tool_sandbox_enabled)
        self.assertEqual(config.plugin_runtime_mode, "sandboxed")
        self.assertTrue(config.auth_enabled)
        self.assertGreaterEqual(config.run_lease_ttl_sec, config.run_attempt_timeout_sec + 5.0)

    def test_invalid_modes_fallback_to_strict(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-config-tests-") as tmp:
            support_dir = Path(tmp) / "support"
            with patch.dict(
                os.environ,
                {
                    "AMARYLLIS_SUPPORT_DIR": str(support_dir),
                    "AMARYLLIS_AUTH_TOKENS": "token-1:user-1:user",
                    "AMARYLLIS_TOOL_APPROVAL_ENFORCEMENT": "invalid",
                    "AMARYLLIS_PLUGIN_SIGNING_MODE": "invalid",
                    "AMARYLLIS_PLUGIN_RUNTIME_MODE": "invalid",
                },
                clear=True,
            ):
                config = AppConfig.from_env()

        self.assertEqual(config.tool_approval_enforcement, "strict")
        self.assertEqual(config.plugin_signing_mode, "strict")
        self.assertEqual(config.plugin_runtime_mode, "sandboxed")

    def test_production_profile_rejects_non_strict_security_modes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-config-tests-") as tmp:
            support_dir = Path(tmp) / "support"
            with patch.dict(
                os.environ,
                {
                    "AMARYLLIS_SUPPORT_DIR": str(support_dir),
                    "AMARYLLIS_AUTH_TOKENS": "token-1:user-1:user",
                    "AMARYLLIS_SECURITY_PROFILE": "production",
                    "AMARYLLIS_TOOL_APPROVAL_ENFORCEMENT": "prompt_and_allow",
                    "AMARYLLIS_PLUGIN_SIGNING_MODE": "warn",
                },
                clear=True,
            ):
                with self.assertRaisesRegex(AppConfigError, "production security configuration"):
                    AppConfig.from_env()

    def test_production_profile_rejects_disabled_auth(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-config-tests-") as tmp:
            support_dir = Path(tmp) / "support"
            with patch.dict(
                os.environ,
                {
                    "AMARYLLIS_SUPPORT_DIR": str(support_dir),
                    "AMARYLLIS_SECURITY_PROFILE": "production",
                    "AMARYLLIS_AUTH_ENABLED": "false",
                    "AMARYLLIS_AUTH_TOKENS": "token-1:user-1:user",
                },
                clear=True,
            ):
                with self.assertRaisesRegex(AppConfigError, "AMARYLLIS_AUTH_ENABLED must be true"):
                    AppConfig.from_env()

    def test_production_profile_rejects_empty_auth_tokens(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-config-tests-") as tmp:
            support_dir = Path(tmp) / "support"
            with patch.dict(
                os.environ,
                {
                    "AMARYLLIS_SUPPORT_DIR": str(support_dir),
                    "AMARYLLIS_SECURITY_PROFILE": "production",
                    "AMARYLLIS_AUTH_ENABLED": "true",
                    "AMARYLLIS_AUTH_TOKENS": "",
                    "AMARYLLIS_API_TOKEN": "",
                },
                clear=True,
            ):
                with self.assertRaisesRegex(AppConfigError, "At least one auth token"):
                    AppConfig.from_env()

    def test_production_profile_rejects_allow_insecure_modes_flag(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-config-tests-") as tmp:
            support_dir = Path(tmp) / "support"
            with patch.dict(
                os.environ,
                {
                    "AMARYLLIS_SUPPORT_DIR": str(support_dir),
                    "AMARYLLIS_SECURITY_PROFILE": "production",
                    "AMARYLLIS_ALLOW_INSECURE_SECURITY_MODES": "true",
                    "AMARYLLIS_AUTH_TOKENS": "token-1:user-1:user",
                },
                clear=True,
            ):
                with self.assertRaisesRegex(
                    AppConfigError,
                    "AMARYLLIS_ALLOW_INSECURE_SECURITY_MODES must be false",
                ):
                    AppConfig.from_env()

    def test_production_profile_rejects_disabled_tool_sandbox(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-config-tests-") as tmp:
            support_dir = Path(tmp) / "support"
            with patch.dict(
                os.environ,
                {
                    "AMARYLLIS_SUPPORT_DIR": str(support_dir),
                    "AMARYLLIS_SECURITY_PROFILE": "production",
                    "AMARYLLIS_AUTH_TOKENS": "token-1:user-1:user",
                    "AMARYLLIS_TOOL_SANDBOX_ENABLED": "false",
                },
                clear=True,
            ):
                with self.assertRaisesRegex(
                    AppConfigError,
                    "AMARYLLIS_TOOL_SANDBOX_ENABLED must be true",
                ):
                    AppConfig.from_env()

    def test_production_profile_rejects_legacy_plugin_runtime(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-config-tests-") as tmp:
            support_dir = Path(tmp) / "support"
            with patch.dict(
                os.environ,
                {
                    "AMARYLLIS_SUPPORT_DIR": str(support_dir),
                    "AMARYLLIS_SECURITY_PROFILE": "production",
                    "AMARYLLIS_AUTH_TOKENS": "token-1:user-1:user",
                    "AMARYLLIS_PLUGIN_RUNTIME_MODE": "legacy",
                },
                clear=True,
            ):
                with self.assertRaisesRegex(
                    AppConfigError,
                    "AMARYLLIS_PLUGIN_RUNTIME_MODE must be sandboxed",
                ):
                    AppConfig.from_env()

    def test_development_profile_can_keep_non_strict_modes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-config-tests-") as tmp:
            support_dir = Path(tmp) / "support"
            with patch.dict(
                os.environ,
                {
                    "AMARYLLIS_SUPPORT_DIR": str(support_dir),
                    "AMARYLLIS_AUTH_TOKENS": "token-1:user-1:user",
                    "AMARYLLIS_SECURITY_PROFILE": "development",
                    "AMARYLLIS_TOOL_APPROVAL_ENFORCEMENT": "prompt_and_allow",
                    "AMARYLLIS_PLUGIN_SIGNING_MODE": "warn",
                },
                clear=True,
            ):
                config = AppConfig.from_env()

        self.assertEqual(config.security_profile, "development")
        self.assertEqual(config.tool_approval_enforcement, "prompt_and_allow")
        self.assertEqual(config.plugin_signing_mode, "warn")

    def test_parse_auth_tokens_from_csv(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-config-tests-") as tmp:
            support_dir = Path(tmp) / "support"
            with patch.dict(
                os.environ,
                {
                    "AMARYLLIS_SUPPORT_DIR": str(support_dir),
                    "AMARYLLIS_AUTH_TOKENS": (
                        "token-admin:admin:admin|user,"
                        "token-user:user-1:user,"
                        "token-service:svc-runner:service"
                    ),
                },
                clear=True,
            ):
                config = AppConfig.from_env()

        self.assertEqual(len(config.auth_tokens), 3)
        by_token = {item.token: item for item in config.auth_tokens}
        self.assertEqual(set(by_token["token-admin"].scopes), {"admin", "user"})
        self.assertEqual(by_token["token-user"].user_id, "user-1")
        self.assertEqual(set(by_token["token-service"].scopes), {"service"})


if __name__ == "__main__":
    unittest.main()
