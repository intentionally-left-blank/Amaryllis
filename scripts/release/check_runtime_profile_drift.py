#!/usr/bin/env python3
from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from runtime.config import AppConfig
from runtime.profile_loader import ProfileLoadError, load_runtime_profile, load_slo_profile


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate runtime/SLO profile manifests and ensure runtime config "
            "loads deterministic values without profile drift."
        )
    )
    parser.add_argument(
        "--profiles",
        default="dev,ci,release",
        help="Comma-separated runtime profiles to validate.",
    )
    parser.add_argument(
        "--runtime-profiles-dir",
        default="runtime/profiles",
        help="Path to runtime profile manifests.",
    )
    parser.add_argument(
        "--slo-profiles-dir",
        default="slo_profiles",
        help="Path to SLO profile manifests.",
    )
    return parser.parse_args()


def _resolve_path(raw: str) -> Path:
    candidate = Path(str(raw or "")).expanduser()
    if not candidate.is_absolute():
        candidate = ROOT_DIR / candidate
    return candidate.resolve()


def _close_enough(left: float, right: float) -> bool:
    return math.isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-9)


def main() -> int:
    args = _parse_args()
    runtime_profiles_dir = _resolve_path(args.runtime_profiles_dir)
    slo_profiles_dir = _resolve_path(args.slo_profiles_dir)
    profiles = [item.strip().lower() for item in str(args.profiles).split(",") if item.strip()]

    failures: list[str] = []

    if not profiles:
        print("[profile-drift] no profiles requested", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="amaryllis-profile-drift-") as tmp:
        support_root = Path(tmp) / "support"

        for profile in profiles:
            try:
                runtime_manifest = load_runtime_profile(profile, profiles_dir=runtime_profiles_dir)
                slo_manifest = load_slo_profile(runtime_manifest.slo_profile, profiles_dir=slo_profiles_dir)
            except ProfileLoadError as exc:
                failures.append(f"{profile}: manifest load failed: {exc}")
                continue

            env = {
                "AMARYLLIS_RUNTIME_PROFILE": runtime_manifest.profile,
                "AMARYLLIS_RUNTIME_PROFILE_DIR": str(runtime_profiles_dir),
                "AMARYLLIS_SLO_PROFILE_DIR": str(slo_profiles_dir),
                "AMARYLLIS_SUPPORT_DIR": str(support_root / runtime_manifest.profile),
                "AMARYLLIS_AUTH_ENABLED": "true",
                "AMARYLLIS_AUTH_TOKENS": "token-admin:admin:admin|user",
            }

            try:
                with patch.dict(os.environ, env, clear=True):
                    config = AppConfig.from_env()
            except Exception as exc:
                failures.append(f"{profile}: AppConfig.from_env failed: {exc}")
                continue

            if config.runtime_profile != runtime_manifest.profile:
                failures.append(
                    f"{profile}: runtime_profile={config.runtime_profile} expected={runtime_manifest.profile}"
                )
            if config.slo_profile != slo_manifest.profile:
                failures.append(f"{profile}: slo_profile={config.slo_profile} expected={slo_manifest.profile}")

            if not _close_enough(
                config.observability_request_availability_target,
                float(slo_manifest.targets["request_availability_target"]),
            ):
                failures.append(
                    f"{profile}: observability_request_availability_target drift "
                    f"config={config.observability_request_availability_target} "
                    f"profile={slo_manifest.targets['request_availability_target']}"
                )

            if not _close_enough(
                config.observability_request_latency_p95_ms_target,
                float(slo_manifest.targets["request_latency_p95_ms_target"]),
            ):
                failures.append(
                    f"{profile}: observability_request_latency_p95_ms_target drift "
                    f"config={config.observability_request_latency_p95_ms_target} "
                    f"profile={slo_manifest.targets['request_latency_p95_ms_target']}"
                )

            if not _close_enough(
                config.observability_run_success_target,
                float(slo_manifest.targets["run_success_target"]),
            ):
                failures.append(
                    f"{profile}: observability_run_success_target drift "
                    f"config={config.observability_run_success_target} "
                    f"profile={slo_manifest.targets['run_success_target']}"
                )

            if not _close_enough(
                config.perf_budget_max_p95_latency_ms,
                float(slo_manifest.quality_budget["perf_max_p95_latency_ms"]),
            ):
                failures.append(
                    f"{profile}: perf_budget_max_p95_latency_ms drift "
                    f"config={config.perf_budget_max_p95_latency_ms} "
                    f"profile={slo_manifest.quality_budget['perf_max_p95_latency_ms']}"
                )

            if not _close_enough(
                config.perf_budget_max_error_rate_pct,
                float(slo_manifest.quality_budget["perf_max_error_rate_pct"]),
            ):
                failures.append(
                    f"{profile}: perf_budget_max_error_rate_pct drift "
                    f"config={config.perf_budget_max_error_rate_pct} "
                    f"profile={slo_manifest.quality_budget['perf_max_error_rate_pct']}"
                )

    if failures:
        print("[profile-drift] FAILED", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    joined = ", ".join(profiles)
    print(f"[profile-drift] OK profiles={joined}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
