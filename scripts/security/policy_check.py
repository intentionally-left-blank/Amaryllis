#!/usr/bin/env python3
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from runtime.config import AppConfig, AppConfigError


def _base_env(*, support_dir: Path) -> dict[str, str]:
    return {
        "AMARYLLIS_SUPPORT_DIR": str(support_dir),
        "AMARYLLIS_SECURITY_PROFILE": "production",
        "AMARYLLIS_AUTH_ENABLED": "true",
        "AMARYLLIS_AUTH_TOKENS": "token-admin:admin:admin|user",
        "AMARYLLIS_TOOL_APPROVAL_ENFORCEMENT": "strict",
        "AMARYLLIS_TOOL_SANDBOX_ENABLED": "true",
        "AMARYLLIS_PLUGIN_SIGNING_MODE": "strict",
        "AMARYLLIS_PLUGIN_RUNTIME_MODE": "sandboxed",
        "AMARYLLIS_ALLOW_INSECURE_SECURITY_MODES": "false",
    }


def _expect_valid(*, name: str, env: dict[str, str], failures: list[str]) -> None:
    try:
        with patch.dict(os.environ, env, clear=True):
            config = AppConfig.from_env()
    except Exception as exc:
        failures.append(f"{name}: expected valid configuration, got error: {exc}")
        return
    if config.security_profile != "production":
        failures.append(f"{name}: expected production profile")


def _expect_invalid(
    *,
    name: str,
    env: dict[str, str],
    expected_substring: str,
    failures: list[str],
) -> None:
    try:
        with patch.dict(os.environ, env, clear=True):
            AppConfig.from_env()
    except AppConfigError as exc:
        message = str(exc)
        if expected_substring not in message:
            failures.append(
                f"{name}: expected error containing '{expected_substring}', got: {message}"
            )
        return
    except Exception as exc:
        failures.append(f"{name}: expected AppConfigError, got: {type(exc).__name__}: {exc}")
        return
    failures.append(f"{name}: expected failure but configuration was accepted")


def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="amaryllis-security-policy-") as tmp:
        support_dir = Path(tmp) / "support"
        base = _base_env(support_dir=support_dir)

        _expect_valid(name="strict_production", env=base, failures=failures)

        env = dict(base)
        env["AMARYLLIS_AUTH_ENABLED"] = "false"
        _expect_invalid(
            name="auth_disabled",
            env=env,
            expected_substring="AMARYLLIS_AUTH_ENABLED must be true",
            failures=failures,
        )

        env = dict(base)
        env["AMARYLLIS_AUTH_TOKENS"] = ""
        env["AMARYLLIS_API_TOKEN"] = ""
        _expect_invalid(
            name="empty_auth_tokens",
            env=env,
            expected_substring="At least one auth token",
            failures=failures,
        )

        env = dict(base)
        env["AMARYLLIS_TOOL_APPROVAL_ENFORCEMENT"] = "prompt_and_allow"
        _expect_invalid(
            name="advisory_tool_mode",
            env=env,
            expected_substring="AMARYLLIS_TOOL_APPROVAL_ENFORCEMENT must be strict",
            failures=failures,
        )

        env = dict(base)
        env["AMARYLLIS_PLUGIN_SIGNING_MODE"] = "warn"
        _expect_invalid(
            name="non_strict_plugin_signing",
            env=env,
            expected_substring="AMARYLLIS_PLUGIN_SIGNING_MODE must be strict",
            failures=failures,
        )

        env = dict(base)
        env["AMARYLLIS_TOOL_SANDBOX_ENABLED"] = "false"
        _expect_invalid(
            name="disabled_tool_sandbox",
            env=env,
            expected_substring="AMARYLLIS_TOOL_SANDBOX_ENABLED must be true",
            failures=failures,
        )

        env = dict(base)
        env["AMARYLLIS_PLUGIN_RUNTIME_MODE"] = "legacy"
        _expect_invalid(
            name="legacy_plugin_runtime",
            env=env,
            expected_substring="AMARYLLIS_PLUGIN_RUNTIME_MODE must be sandboxed",
            failures=failures,
        )

        env = dict(base)
        env["AMARYLLIS_ALLOW_INSECURE_SECURITY_MODES"] = "true"
        _expect_invalid(
            name="allow_insecure_flag",
            env=env,
            expected_substring="AMARYLLIS_ALLOW_INSECURE_SECURITY_MODES must be false",
            failures=failures,
        )

    if failures:
        print("Security policy checks failed:", file=sys.stderr)
        for item in failures:
            print(f"- {item}", file=sys.stderr)
        return 1
    print("Security policy checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
