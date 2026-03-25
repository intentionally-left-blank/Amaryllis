from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build consolidated release quality dashboard snapshot from gate reports "
            "(perf/fault/injection-containment/model-artifact-admission/environment-passport/mission-queue/runtime-lifecycle/user-journey "
            "with optional license admission, distribution resilience, distribution channel manifest, "
            "API quickstart compatibility, QoS governor gate, and macOS desktop parity staging)."
        )
    )
    parser.add_argument(
        "--perf-report",
        default="artifacts/perf-smoke-report.json",
        help="Path to perf smoke report JSON.",
    )
    parser.add_argument(
        "--fault-injection-report",
        default="artifacts/fault-injection-reliability-report.json",
        help="Path to fault-injection reliability report JSON.",
    )
    parser.add_argument(
        "--injection-containment-report",
        default="",
        help="Optional path to injection containment gate report JSON.",
    )
    parser.add_argument(
        "--model-artifact-admission-report",
        default="",
        help="Optional path to model artifact admission gate report JSON.",
    )
    parser.add_argument(
        "--environment-passport-report",
        default="",
        help="Optional path to environment passport gate report JSON.",
    )
    parser.add_argument(
        "--license-admission-report",
        default="",
        help="Optional path to license admission gate report JSON.",
    )
    parser.add_argument(
        "--mission-queue-report",
        default="artifacts/mission-queue-load-report.json",
        help="Path to mission queue load report JSON.",
    )
    parser.add_argument(
        "--runtime-lifecycle-report",
        default="artifacts/runtime-lifecycle-smoke-report.json",
        help="Path to runtime lifecycle smoke report JSON.",
    )
    parser.add_argument(
        "--user-journey-report",
        default="artifacts/user-journey-benchmark-report.json",
        help="Path to user journey benchmark report JSON.",
    )
    parser.add_argument(
        "--distribution-resilience-report",
        default="",
        help="Optional path to distribution resilience report JSON.",
    )
    parser.add_argument(
        "--distribution-channel-manifest-report",
        default="",
        help="Optional path to distribution channel manifest gate report JSON.",
    )
    parser.add_argument(
        "--api-quickstart-report",
        default="",
        help="Optional path to API quickstart compatibility gate report JSON.",
    )
    parser.add_argument(
        "--qos-governor-report",
        default="",
        help="Optional path to QoS governor gate report JSON.",
    )
    parser.add_argument(
        "--long-context-report",
        default="",
        help="Optional path to long-context reliability gate report JSON.",
    )
    parser.add_argument(
        "--macos-desktop-parity-report",
        default="",
        help="Optional path to macOS desktop parity smoke report JSON.",
    )
    parser.add_argument(
        "--output",
        default="artifacts/release-quality-dashboard.json",
        help="Output snapshot path.",
    )
    parser.add_argument(
        "--baseline",
        default="eval/baselines/quality/release_quality_dashboard_baseline.json",
        help="Optional baseline snapshot path for trend deltas.",
    )
    parser.add_argument(
        "--trend-output",
        default="artifacts/release-quality-dashboard-trend.json",
        help="Optional trend report output path. Empty disables trend output.",
    )
    parser.add_argument(
        "--release-id",
        default="",
        help="Release identifier. Defaults to GITHUB_REF_NAME/GITHUB_SHA when available.",
    )
    parser.add_argument(
        "--release-channel",
        default="",
        help="Release channel label. Defaults to value inferred from GitHub ref.",
    )
    parser.add_argument(
        "--commit-sha",
        default="",
        help="Commit SHA. Defaults to GITHUB_SHA when available.",
    )
    return parser.parse_args()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_path(project_root: Path, raw_path: str) -> Path:
    path = Path(str(raw_path).strip())
    if not path.is_absolute():
        path = project_root / path
    return path


def _load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON payload must be object: {path}")
    return payload


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _metric_signal(
    *,
    metric_id: str,
    source: str,
    category: str,
    value: float,
    threshold: float,
    comparator: str,
    unit: str,
) -> dict[str, Any]:
    normalized_comparator = str(comparator).strip().lower()
    if normalized_comparator not in {"lte", "gte"}:
        raise ValueError(f"Unsupported comparator: {comparator}")
    passed = value <= threshold if normalized_comparator == "lte" else value >= threshold
    return {
        "metric_id": metric_id,
        "source": source,
        "category": category,
        "value": round(float(value), 6),
        "threshold": round(float(threshold), 6),
        "comparator": normalized_comparator,
        "unit": unit,
        "passed": bool(passed),
    }


def _infer_release_context(args: argparse.Namespace) -> dict[str, str]:
    github_ref = str(os.getenv("GITHUB_REF") or "").strip()
    github_ref_name = str(os.getenv("GITHUB_REF_NAME") or "").strip()
    github_sha = str(os.getenv("GITHUB_SHA") or "").strip()

    release_id = str(args.release_id or "").strip()
    if not release_id:
        release_id = github_ref_name or github_sha or "local-dev"

    release_channel = str(args.release_channel or "").strip().lower()
    if not release_channel:
        if github_ref.startswith("refs/tags/v"):
            release_channel = "stable"
        elif github_ref.startswith("refs/pull/"):
            release_channel = "pr"
        elif github_ref.startswith("refs/heads/"):
            release_channel = "branch"
        else:
            release_channel = "local"

    commit_sha = str(args.commit_sha or "").strip() or github_sha or "unknown"
    return {
        "release_id": release_id,
        "release_channel": release_channel,
        "commit_sha": commit_sha,
    }


def _build_snapshot(
    *,
    args: argparse.Namespace,
    reports: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    perf = reports["perf_smoke"]
    fault = reports["fault_injection"]
    mission = reports["mission_queue"]
    runtime = reports["runtime_lifecycle"]
    user_journey = reports["user_journey"]

    perf_summary = perf.get("summary") if isinstance(perf.get("summary"), dict) else {}
    perf_thresholds = perf.get("thresholds") if isinstance(perf.get("thresholds"), dict) else {}

    fault_summary = fault.get("summary") if isinstance(fault.get("summary"), dict) else {}
    mission_summary = mission.get("summary") if isinstance(mission.get("summary"), dict) else {}
    mission_config = mission.get("config") if isinstance(mission.get("config"), dict) else {}
    runtime_summary = runtime.get("summary") if isinstance(runtime.get("summary"), dict) else {}
    user_journey_summary = (
        user_journey.get("summary") if isinstance(user_journey.get("summary"), dict) else {}
    )
    user_journey_thresholds = (
        user_journey.get("config", {}).get("thresholds")
        if isinstance(user_journey.get("config"), dict)
        and isinstance(user_journey.get("config", {}).get("thresholds"), dict)
        else {}
    )
    distribution = reports.get("distribution_resilience")
    distribution_summary = (
        distribution.get("summary")
        if isinstance(distribution, dict) and isinstance(distribution.get("summary"), dict)
        else {}
    )
    distribution_channel_manifest = reports.get("distribution_channel_manifest")
    distribution_channel_manifest_summary = (
        distribution_channel_manifest.get("summary")
        if isinstance(distribution_channel_manifest, dict)
        and isinstance(distribution_channel_manifest.get("summary"), dict)
        else {}
    )
    api_quickstart = reports.get("api_quickstart_compat")
    api_quickstart_summary = (
        api_quickstart.get("summary")
        if isinstance(api_quickstart, dict) and isinstance(api_quickstart.get("summary"), dict)
        else {}
    )
    qos_governor = reports.get("qos_governor")
    qos_governor_summary = (
        qos_governor.get("summary")
        if isinstance(qos_governor, dict) and isinstance(qos_governor.get("summary"), dict)
        else {}
    )
    long_context = reports.get("long_context")
    long_context_summary = (
        long_context.get("summary")
        if isinstance(long_context, dict) and isinstance(long_context.get("summary"), dict)
        else {}
    )
    macos_parity = reports.get("macos_desktop_parity")
    macos_parity_summary = (
        macos_parity.get("summary")
        if isinstance(macos_parity, dict) and isinstance(macos_parity.get("summary"), dict)
        else {}
    )
    injection = reports.get("injection_containment")
    injection_summary = (
        injection.get("summary")
        if isinstance(injection, dict) and isinstance(injection.get("summary"), dict)
        else {}
    )
    model_admission = reports.get("model_artifact_admission")
    model_admission_summary = (
        model_admission.get("summary")
        if isinstance(model_admission, dict) and isinstance(model_admission.get("summary"), dict)
        else {}
    )
    environment_passport = reports.get("environment_passport")
    environment_passport_summary = (
        environment_passport.get("summary")
        if isinstance(environment_passport, dict)
        and isinstance(environment_passport.get("summary"), dict)
        else {}
    )
    license_admission = reports.get("license_admission")
    license_admission_summary = (
        license_admission.get("summary")
        if isinstance(license_admission, dict)
        and isinstance(license_admission.get("summary"), dict)
        else {}
    )

    signals: list[dict[str, Any]] = [
        _metric_signal(
            metric_id="perf.p95_latency_ms",
            source="perf_smoke",
            category="performance",
            value=_safe_float(perf_summary.get("p95_latency_ms")),
            threshold=_safe_float(perf_thresholds.get("max_p95_latency_ms")),
            comparator="lte",
            unit="ms",
        ),
        _metric_signal(
            metric_id="perf.error_rate_pct",
            source="perf_smoke",
            category="reliability",
            value=_safe_float(perf_summary.get("error_rate_pct")),
            threshold=_safe_float(perf_thresholds.get("max_error_rate_pct")),
            comparator="lte",
            unit="pct",
        ),
        _metric_signal(
            metric_id="fault_injection.pass_rate_pct",
            source="fault_injection",
            category="resilience",
            value=_safe_float(fault_summary.get("pass_rate_pct")),
            threshold=_safe_float(fault_summary.get("min_pass_rate_pct")),
            comparator="gte",
            unit="pct",
        ),
        _metric_signal(
            metric_id="mission_queue.success_rate_pct",
            source="mission_queue",
            category="queue",
            value=_safe_float(mission_summary.get("success_rate_pct")),
            threshold=_safe_float(mission_config.get("min_success_rate_pct")),
            comparator="gte",
            unit="pct",
        ),
        _metric_signal(
            metric_id="mission_queue.p95_queue_wait_ms",
            source="mission_queue",
            category="queue",
            value=_safe_float(mission_summary.get("p95_queue_wait_ms")),
            threshold=_safe_float(mission_config.get("max_p95_queue_wait_ms")),
            comparator="lte",
            unit="ms",
        ),
        _metric_signal(
            metric_id="mission_queue.p95_end_to_end_ms",
            source="mission_queue",
            category="queue",
            value=_safe_float(mission_summary.get("p95_end_to_end_ms")),
            threshold=_safe_float(mission_config.get("max_p95_end_to_end_ms")),
            comparator="lte",
            unit="ms",
        ),
        _metric_signal(
            metric_id="mission_queue.failed_or_canceled",
            source="mission_queue",
            category="queue",
            value=_safe_float(mission_summary.get("failed_or_canceled")),
            threshold=_safe_float(mission_config.get("max_failed_runs")),
            comparator="lte",
            unit="count",
        ),
        _metric_signal(
            metric_id="runtime_lifecycle.targets_ok",
            source="runtime_lifecycle",
            category="runtime",
            value=1.0 if bool(runtime_summary.get("targets_ok")) else 0.0,
            threshold=1.0,
            comparator="gte",
            unit="bool",
        ),
        _metric_signal(
            metric_id="runtime_lifecycle.startup_ok",
            source="runtime_lifecycle",
            category="runtime",
            value=1.0 if bool(runtime_summary.get("startup_ok")) else 0.0,
            threshold=1.0,
            comparator="gte",
            unit="bool",
        ),
        _metric_signal(
            metric_id="runtime_lifecycle.checks_failed",
            source="runtime_lifecycle",
            category="runtime",
            value=_safe_float(runtime_summary.get("checks_failed")),
            threshold=0.0,
            comparator="lte",
            unit="count",
        ),
        _metric_signal(
            metric_id="user_journey.success_rate_pct",
            source="user_journey",
            category="user_flow",
            value=_safe_float(user_journey_summary.get("journey_success_rate_pct")),
            threshold=_safe_float(
                user_journey_thresholds.get("min_success_rate_pct"),
                default=_safe_float(user_journey_summary.get("journey_success_rate_pct")),
            ),
            comparator="gte",
            unit="pct",
        ),
        _metric_signal(
            metric_id="user_journey.p95_journey_latency_ms",
            source="user_journey",
            category="user_flow",
            value=_safe_float(user_journey_summary.get("p95_journey_latency_ms")),
            threshold=_safe_float(
                user_journey_thresholds.get("max_p95_journey_latency_ms"),
                default=_safe_float(user_journey_summary.get("p95_journey_latency_ms")),
            ),
            comparator="lte",
            unit="ms",
        ),
        _metric_signal(
            metric_id="user_journey.p95_plan_dispatch_latency_ms",
            source="user_journey",
            category="user_flow",
            value=_safe_float(user_journey_summary.get("p95_plan_dispatch_latency_ms")),
            threshold=_safe_float(
                user_journey_thresholds.get("max_p95_plan_dispatch_latency_ms"),
                default=_safe_float(user_journey_summary.get("p95_plan_dispatch_latency_ms")),
            ),
            comparator="lte",
            unit="ms",
        ),
        _metric_signal(
            metric_id="user_journey.p95_execute_dispatch_latency_ms",
            source="user_journey",
            category="user_flow",
            value=_safe_float(user_journey_summary.get("p95_execute_dispatch_latency_ms")),
            threshold=_safe_float(
                user_journey_thresholds.get("max_p95_execute_dispatch_latency_ms"),
                default=_safe_float(user_journey_summary.get("p95_execute_dispatch_latency_ms")),
            ),
            comparator="lte",
            unit="ms",
        ),
        _metric_signal(
            metric_id="user_journey.plan_to_execute_conversion_rate_pct",
            source="user_journey",
            category="user_flow",
            value=_safe_float(user_journey_summary.get("plan_to_execute_conversion_rate_pct")),
            threshold=_safe_float(
                user_journey_thresholds.get("min_plan_to_execute_conversion_rate_pct"),
                default=_safe_float(user_journey_summary.get("plan_to_execute_conversion_rate_pct")),
            ),
            comparator="gte",
            unit="pct",
        ),
        _metric_signal(
            metric_id="user_journey.activation_success_rate_pct",
            source="user_journey",
            category="user_flow",
            value=_safe_float(user_journey_summary.get("activation_success_rate_pct")),
            threshold=_safe_float(
                user_journey_thresholds.get("min_activation_success_rate_pct"),
                default=_safe_float(user_journey_summary.get("activation_success_rate_pct")),
            ),
            comparator="gte",
            unit="pct",
        ),
        _metric_signal(
            metric_id="user_journey.activation_blocked_rate_pct",
            source="user_journey",
            category="user_flow",
            value=_safe_float(user_journey_summary.get("activation_blocked_rate_pct")),
            threshold=_safe_float(
                user_journey_thresholds.get("max_blocked_activation_rate_pct"),
                default=_safe_float(user_journey_summary.get("activation_blocked_rate_pct")),
            ),
            comparator="lte",
            unit="pct",
        ),
        _metric_signal(
            metric_id="user_journey.p95_activation_latency_ms",
            source="user_journey",
            category="user_flow",
            value=_safe_float(user_journey_summary.get("p95_activation_latency_ms")),
            threshold=_safe_float(
                user_journey_thresholds.get("max_p95_activation_latency_ms"),
                default=_safe_float(user_journey_summary.get("p95_activation_latency_ms")),
            ),
            comparator="lte",
            unit="ms",
        ),
        _metric_signal(
            metric_id="user_journey.install_success_rate_pct",
            source="user_journey",
            category="user_flow",
            value=_safe_float(user_journey_summary.get("install_success_rate_pct")),
            threshold=_safe_float(
                user_journey_thresholds.get("min_install_success_rate_pct"),
                default=_safe_float(user_journey_summary.get("install_success_rate_pct")),
            ),
            comparator="gte",
            unit="pct",
        ),
        _metric_signal(
            metric_id="user_journey.retention_proxy_success_rate_pct",
            source="user_journey",
            category="user_flow",
            value=_safe_float(user_journey_summary.get("retention_proxy_success_rate_pct")),
            threshold=_safe_float(
                user_journey_thresholds.get("min_retention_proxy_success_rate_pct"),
                default=_safe_float(user_journey_summary.get("retention_proxy_success_rate_pct")),
            ),
            comparator="gte",
            unit="pct",
        ),
        _metric_signal(
            metric_id="user_journey.feature_adoption_rate_pct",
            source="user_journey",
            category="user_flow",
            value=_safe_float(user_journey_summary.get("feature_adoption_rate_pct")),
            threshold=_safe_float(
                user_journey_thresholds.get("min_feature_adoption_rate_pct"),
                default=_safe_float(user_journey_summary.get("feature_adoption_rate_pct")),
            ),
            comparator="gte",
            unit="pct",
        ),
    ]
    if distribution_summary:
        distribution_status = str(distribution_summary.get("status") or "").strip().lower()
        signals.extend(
            [
                _metric_signal(
                    metric_id="distribution_resilience.status",
                    source="distribution_resilience",
                    category="distribution",
                    value=1.0 if distribution_status == "pass" else 0.0,
                    threshold=1.0,
                    comparator="gte",
                    unit="bool",
                ),
                _metric_signal(
                    metric_id="distribution_resilience.checks_failed",
                    source="distribution_resilience",
                    category="distribution",
                    value=_safe_float(distribution_summary.get("checks_failed")),
                    threshold=0.0,
                    comparator="lte",
                    unit="count",
                ),
                _metric_signal(
                    metric_id="distribution_resilience.score_pct",
                    source="distribution_resilience",
                    category="distribution",
                    value=_safe_float(distribution_summary.get("score_pct")),
                    threshold=100.0,
                    comparator="gte",
                    unit="pct",
                ),
            ]
        )
    if distribution_channel_manifest_summary:
        manifest_status = str(distribution_channel_manifest_summary.get("status") or "").strip().lower()
        manifest_checks_total = max(0.0, _safe_float(distribution_channel_manifest_summary.get("checks_total")))
        manifest_checks_failed = max(0.0, _safe_float(distribution_channel_manifest_summary.get("checks_failed")))
        manifest_checks_passed = max(0.0, manifest_checks_total - manifest_checks_failed)
        manifest_coverage_pct = (
            (manifest_checks_passed / manifest_checks_total) * 100.0
            if manifest_checks_total > 0
            else (100.0 if manifest_status == "pass" else 0.0)
        )
        signals.extend(
            [
                _metric_signal(
                    metric_id="distribution_channel_manifest.status",
                    source="distribution_channel_manifest",
                    category="distribution",
                    value=1.0 if manifest_status == "pass" else 0.0,
                    threshold=1.0,
                    comparator="gte",
                    unit="bool",
                ),
                _metric_signal(
                    metric_id="distribution_channel_manifest.checks_failed",
                    source="distribution_channel_manifest",
                    category="distribution",
                    value=manifest_checks_failed,
                    threshold=0.0,
                    comparator="lte",
                    unit="count",
                ),
                _metric_signal(
                    metric_id="distribution_channel_manifest.coverage_pct",
                    source="distribution_channel_manifest",
                    category="distribution",
                    value=manifest_coverage_pct,
                    threshold=100.0,
                    comparator="gte",
                    unit="pct",
                ),
            ]
        )
    if api_quickstart_summary:
        quickstart_status = str(api_quickstart_summary.get("status") or "").strip().lower()
        quickstart_checks_total = max(0.0, _safe_float(api_quickstart_summary.get("checks_total")))
        quickstart_checks_failed = max(0.0, _safe_float(api_quickstart_summary.get("checks_failed")))
        quickstart_checks_passed = max(0.0, quickstart_checks_total - quickstart_checks_failed)
        quickstart_pass_rate = (
            (quickstart_checks_passed / quickstart_checks_total) * 100.0
            if quickstart_checks_total > 0
            else (100.0 if quickstart_status == "pass" else 0.0)
        )
        signals.extend(
            [
                _metric_signal(
                    metric_id="api_quickstart_compat.status",
                    source="api_quickstart_compat",
                    category="developer_adoption",
                    value=1.0 if quickstart_status == "pass" else 0.0,
                    threshold=1.0,
                    comparator="gte",
                    unit="bool",
                ),
                _metric_signal(
                    metric_id="api_quickstart_compat.checks_failed",
                    source="api_quickstart_compat",
                    category="developer_adoption",
                    value=quickstart_checks_failed,
                    threshold=0.0,
                    comparator="lte",
                    unit="count",
                ),
                _metric_signal(
                    metric_id="api_quickstart_compat.pass_rate_pct",
                    source="api_quickstart_compat",
                    category="developer_adoption",
                    value=quickstart_pass_rate,
                    threshold=100.0,
                    comparator="gte",
                    unit="pct",
                ),
            ]
        )
    if qos_governor_summary:
        qos_status = str(qos_governor_summary.get("status") or "").strip().lower()
        signals.extend(
            [
                _metric_signal(
                    metric_id="qos_governor.status",
                    source="qos_governor",
                    category="runtime_qos",
                    value=1.0 if qos_status == "pass" else 0.0,
                    threshold=1.0,
                    comparator="gte",
                    unit="bool",
                ),
                _metric_signal(
                    metric_id="qos_governor.checks_failed",
                    source="qos_governor",
                    category="runtime_qos",
                    value=_safe_float(qos_governor_summary.get("checks_failed")),
                    threshold=0.0,
                    comparator="lte",
                    unit="count",
                ),
            ]
        )
    if long_context_summary:
        long_context_status = str(long_context_summary.get("status") or "").strip().lower()
        signals.extend(
            [
                _metric_signal(
                    metric_id="long_context.status",
                    source="long_context",
                    category="long_context",
                    value=1.0 if long_context_status == "pass" else 0.0,
                    threshold=1.0,
                    comparator="gte",
                    unit="bool",
                ),
                _metric_signal(
                    metric_id="long_context.checks_failed",
                    source="long_context",
                    category="long_context",
                    value=_safe_float(long_context_summary.get("checks_failed")),
                    threshold=0.0,
                    comparator="lte",
                    unit="count",
                ),
            ]
        )
    if macos_parity_summary:
        macos_status = str(macos_parity_summary.get("status") or "").strip().lower()
        signals.extend(
            [
                _metric_signal(
                    metric_id="macos_desktop_parity.status",
                    source="macos_desktop_parity",
                    category="desktop_staging",
                    value=1.0 if macos_status == "pass" else 0.0,
                    threshold=1.0,
                    comparator="gte",
                    unit="bool",
                ),
                _metric_signal(
                    metric_id="macos_desktop_parity.checks_failed",
                    source="macos_desktop_parity",
                    category="desktop_staging",
                    value=_safe_float(macos_parity_summary.get("checks_failed")),
                    threshold=0.0,
                    comparator="lte",
                    unit="count",
                ),
                _metric_signal(
                    metric_id="macos_desktop_parity.error_rate_pct",
                    source="macos_desktop_parity",
                    category="desktop_staging",
                    value=_safe_float(macos_parity_summary.get("error_rate_pct")),
                    threshold=0.0,
                    comparator="lte",
                    unit="pct",
                ),
            ]
        )
    if injection_summary:
        signals.extend(
            [
                _metric_signal(
                    metric_id="injection_containment.containment_score_pct",
                    source="injection_containment",
                    category="security",
                    value=_safe_float(injection_summary.get("containment_score_pct")),
                    threshold=_safe_float(injection_summary.get("min_containment_score_pct"), default=100.0),
                    comparator="gte",
                    unit="pct",
                ),
                _metric_signal(
                    metric_id="injection_containment.failed_scenarios",
                    source="injection_containment",
                    category="security",
                    value=_safe_float(injection_summary.get("failed_scenarios")),
                    threshold=_safe_float(injection_summary.get("max_failed_scenarios"), default=0.0),
                    comparator="lte",
                    unit="count",
                ),
            ]
        )
    if model_admission_summary:
        signals.extend(
            [
                _metric_signal(
                    metric_id="model_artifact_admission.admission_score_pct",
                    source="model_artifact_admission",
                    category="supply_chain",
                    value=_safe_float(model_admission_summary.get("admission_score_pct")),
                    threshold=_safe_float(model_admission_summary.get("min_admission_score_pct"), default=100.0),
                    comparator="gte",
                    unit="pct",
                ),
                _metric_signal(
                    metric_id="model_artifact_admission.failed_scenarios",
                    source="model_artifact_admission",
                    category="supply_chain",
                    value=_safe_float(model_admission_summary.get("failed_scenarios")),
                    threshold=_safe_float(model_admission_summary.get("max_failed_scenarios"), default=0.0),
                    comparator="lte",
                    unit="count",
                ),
            ]
        )
    if environment_passport_summary:
        signals.extend(
            [
                _metric_signal(
                    metric_id="environment_passport.completeness_score_pct",
                    source="environment_passport",
                    category="reproducibility",
                    value=_safe_float(environment_passport_summary.get("completeness_score_pct")),
                    threshold=_safe_float(
                        environment_passport_summary.get("min_completeness_score_pct"),
                        default=100.0,
                    ),
                    comparator="gte",
                    unit="pct",
                ),
                _metric_signal(
                    metric_id="environment_passport.missing_required_fields",
                    source="environment_passport",
                    category="reproducibility",
                    value=_safe_float(environment_passport_summary.get("missing_required_fields_count")),
                    threshold=_safe_float(
                        environment_passport_summary.get("max_missing_required"),
                        default=0.0,
                    ),
                    comparator="lte",
                    unit="count",
                ),
            ]
        )
    if license_admission_summary:
        signals.extend(
            [
                _metric_signal(
                    metric_id="license_admission.admission_score_pct",
                    source="license_admission",
                    category="compliance",
                    value=_safe_float(license_admission_summary.get("admission_score_pct")),
                    threshold=_safe_float(
                        license_admission_summary.get("min_admission_score_pct"),
                        default=100.0,
                    ),
                    comparator="gte",
                    unit="pct",
                ),
                _metric_signal(
                    metric_id="license_admission.failed_scenarios",
                    source="license_admission",
                    category="compliance",
                    value=_safe_float(license_admission_summary.get("failed_scenarios")),
                    threshold=_safe_float(
                        license_admission_summary.get("max_failed_scenarios"),
                        default=0.0,
                    ),
                    comparator="lte",
                    unit="count",
                ),
            ]
        )

    total = len(signals)
    passed = sum(1 for item in signals if bool(item.get("passed")))
    failed = total - passed
    quality_score_pct = (float(passed) / float(total) * 100.0) if total > 0 else 0.0

    sources: dict[str, dict[str, Any]] = {}
    for key, payload in reports.items():
        suite = str(payload.get("suite") or "").strip()
        generated_at = str(payload.get("generated_at") or "").strip()
        sources[key] = {
            "suite": suite,
            "generated_at": generated_at,
        }

    return {
        "generated_at": _utc_now_iso(),
        "suite": "release_quality_dashboard_v1",
        "release": _infer_release_context(args),
        "sources": sources,
        "signals": signals,
        "summary": {
            "signals_total": total,
            "signals_passed": passed,
            "signals_failed": failed,
            "quality_score_pct": round(quality_score_pct, 4),
            "status": "pass" if failed == 0 else "fail",
        },
    }


def _metric_map_from_snapshot(snapshot: dict[str, Any]) -> dict[str, float]:
    signals = snapshot.get("signals")
    if not isinstance(signals, list):
        return {}
    output: dict[str, float] = {}
    for item in signals:
        if not isinstance(item, dict):
            continue
        metric_id = str(item.get("metric_id") or "").strip()
        if not metric_id:
            continue
        output[metric_id] = _safe_float(item.get("value"), default=0.0)
    return output


def _build_trend_report(*, snapshot: dict[str, Any], baseline: dict[str, Any], baseline_path: Path) -> dict[str, Any]:
    current_signals = snapshot.get("signals")
    if not isinstance(current_signals, list):
        raise ValueError("snapshot must include signals list")

    baseline_values = _metric_map_from_snapshot(baseline)
    comparisons: list[dict[str, Any]] = []
    for item in current_signals:
        if not isinstance(item, dict):
            continue
        metric_id = str(item.get("metric_id") or "").strip()
        comparator = str(item.get("comparator") or "").strip().lower()
        if not metric_id or comparator not in {"lte", "gte"}:
            continue
        if metric_id not in baseline_values:
            continue
        current_value = _safe_float(item.get("value"))
        baseline_value = _safe_float(baseline_values.get(metric_id))
        delta = current_value - baseline_value
        directional_delta = baseline_value - current_value if comparator == "lte" else current_value - baseline_value
        if directional_delta > 0:
            direction = "improved"
        elif directional_delta < 0:
            direction = "regressed"
        else:
            direction = "unchanged"
        comparisons.append(
            {
                "metric_id": metric_id,
                "comparator": comparator,
                "unit": str(item.get("unit") or ""),
                "current": round(current_value, 6),
                "baseline": round(baseline_value, 6),
                "delta": round(delta, 6),
                "directional_delta": round(directional_delta, 6),
                "direction": direction,
            }
        )

    improved = sum(1 for item in comparisons if item.get("direction") == "improved")
    regressed = sum(1 for item in comparisons if item.get("direction") == "regressed")
    unchanged = sum(1 for item in comparisons if item.get("direction") == "unchanged")
    return {
        "generated_at": _utc_now_iso(),
        "suite": "release_quality_dashboard_trend_v1",
        "baseline_path": str(baseline_path),
        "baseline_suite": str(baseline.get("suite") or ""),
        "snapshot_suite": str(snapshot.get("suite") or ""),
        "comparisons": comparisons,
        "summary": {
            "compared_metrics": len(comparisons),
            "improved": improved,
            "regressed": regressed,
            "unchanged": unchanged,
        },
    }


def main() -> int:
    args = _parse_args()
    project_root = Path(__file__).resolve().parents[2]

    report_paths = {
        "perf_smoke": _resolve_path(project_root, str(args.perf_report)),
        "fault_injection": _resolve_path(project_root, str(args.fault_injection_report)),
        "mission_queue": _resolve_path(project_root, str(args.mission_queue_report)),
        "runtime_lifecycle": _resolve_path(project_root, str(args.runtime_lifecycle_report)),
        "user_journey": _resolve_path(project_root, str(args.user_journey_report)),
    }
    distribution_raw = str(args.distribution_resilience_report or "").strip()
    distribution_path = _resolve_path(project_root, distribution_raw) if distribution_raw else None
    distribution_manifest_raw = str(args.distribution_channel_manifest_report or "").strip()
    distribution_manifest_path = (
        _resolve_path(project_root, distribution_manifest_raw) if distribution_manifest_raw else None
    )
    api_quickstart_raw = str(args.api_quickstart_report or "").strip()
    api_quickstart_path = _resolve_path(project_root, api_quickstart_raw) if api_quickstart_raw else None
    qos_governor_raw = str(args.qos_governor_report or "").strip()
    qos_governor_path = _resolve_path(project_root, qos_governor_raw) if qos_governor_raw else None
    long_context_raw = str(args.long_context_report or "").strip()
    long_context_path = _resolve_path(project_root, long_context_raw) if long_context_raw else None
    macos_parity_raw = str(args.macos_desktop_parity_report or "").strip()
    macos_parity_path = _resolve_path(project_root, macos_parity_raw) if macos_parity_raw else None
    injection_raw = str(args.injection_containment_report or "").strip()
    injection_path = _resolve_path(project_root, injection_raw) if injection_raw else None
    model_admission_raw = str(args.model_artifact_admission_report or "").strip()
    model_admission_path = _resolve_path(project_root, model_admission_raw) if model_admission_raw else None
    environment_passport_raw = str(args.environment_passport_report or "").strip()
    environment_passport_path = (
        _resolve_path(project_root, environment_passport_raw) if environment_passport_raw else None
    )
    license_admission_raw = str(args.license_admission_report or "").strip()
    license_admission_path = (
        _resolve_path(project_root, license_admission_raw) if license_admission_raw else None
    )

    reports: dict[str, dict[str, Any]] = {}
    for key, path in report_paths.items():
        if not path.exists():
            print(f"[quality-dashboard] missing report for {key}: {path}", file=sys.stderr)
            return 2
        try:
            reports[key] = _load_json_object(path)
        except Exception as exc:
            print(f"[quality-dashboard] invalid report for {key}: {path} error={exc}", file=sys.stderr)
            return 2
    if distribution_path is not None:
        if not distribution_path.exists():
            print(f"[quality-dashboard] missing report for distribution_resilience: {distribution_path}", file=sys.stderr)
            return 2
        try:
            reports["distribution_resilience"] = _load_json_object(distribution_path)
        except Exception as exc:
            print(
                f"[quality-dashboard] invalid report for distribution_resilience: {distribution_path} error={exc}",
                file=sys.stderr,
            )
            return 2
    if distribution_manifest_path is not None:
        if not distribution_manifest_path.exists():
            print(
                (
                    "[quality-dashboard] missing report for distribution_channel_manifest: "
                    f"{distribution_manifest_path}"
                ),
                file=sys.stderr,
            )
            return 2
        try:
            reports["distribution_channel_manifest"] = _load_json_object(distribution_manifest_path)
        except Exception as exc:
            print(
                (
                    "[quality-dashboard] invalid report for distribution_channel_manifest: "
                    f"{distribution_manifest_path} error={exc}"
                ),
                file=sys.stderr,
            )
            return 2
    if api_quickstart_path is not None:
        if not api_quickstart_path.exists():
            print(
                f"[quality-dashboard] missing report for api_quickstart_compat: {api_quickstart_path}",
                file=sys.stderr,
            )
            return 2
        try:
            reports["api_quickstart_compat"] = _load_json_object(api_quickstart_path)
        except Exception as exc:
            print(
                f"[quality-dashboard] invalid report for api_quickstart_compat: {api_quickstart_path} error={exc}",
                file=sys.stderr,
            )
            return 2
    if qos_governor_path is not None:
        if not qos_governor_path.exists():
            print(f"[quality-dashboard] missing report for qos_governor: {qos_governor_path}", file=sys.stderr)
            return 2
        try:
            reports["qos_governor"] = _load_json_object(qos_governor_path)
        except Exception as exc:
            print(
                f"[quality-dashboard] invalid report for qos_governor: {qos_governor_path} error={exc}",
                file=sys.stderr,
            )
            return 2
    if long_context_path is not None:
        if not long_context_path.exists():
            print(f"[quality-dashboard] missing report for long_context: {long_context_path}", file=sys.stderr)
            return 2
        try:
            reports["long_context"] = _load_json_object(long_context_path)
        except Exception as exc:
            print(
                f"[quality-dashboard] invalid report for long_context: {long_context_path} error={exc}",
                file=sys.stderr,
            )
            return 2
    if macos_parity_path is not None:
        if not macos_parity_path.exists():
            print(f"[quality-dashboard] missing report for macos_desktop_parity: {macos_parity_path}", file=sys.stderr)
            return 2
        try:
            reports["macos_desktop_parity"] = _load_json_object(macos_parity_path)
        except Exception as exc:
            print(
                f"[quality-dashboard] invalid report for macos_desktop_parity: {macos_parity_path} error={exc}",
                file=sys.stderr,
            )
            return 2
    if injection_path is not None:
        if not injection_path.exists():
            print(f"[quality-dashboard] missing report for injection_containment: {injection_path}", file=sys.stderr)
            return 2
        try:
            reports["injection_containment"] = _load_json_object(injection_path)
        except Exception as exc:
            print(
                f"[quality-dashboard] invalid report for injection_containment: {injection_path} error={exc}",
                file=sys.stderr,
            )
            return 2
    if model_admission_path is not None:
        if not model_admission_path.exists():
            print(
                f"[quality-dashboard] missing report for model_artifact_admission: {model_admission_path}",
                file=sys.stderr,
            )
            return 2
        try:
            reports["model_artifact_admission"] = _load_json_object(model_admission_path)
        except Exception as exc:
            print(
                f"[quality-dashboard] invalid report for model_artifact_admission: {model_admission_path} error={exc}",
                file=sys.stderr,
            )
            return 2
    if environment_passport_path is not None:
        if not environment_passport_path.exists():
            print(
                f"[quality-dashboard] missing report for environment_passport: {environment_passport_path}",
                file=sys.stderr,
            )
            return 2
        try:
            reports["environment_passport"] = _load_json_object(environment_passport_path)
        except Exception as exc:
            print(
                f"[quality-dashboard] invalid report for environment_passport: {environment_passport_path} error={exc}",
                file=sys.stderr,
            )
            return 2
    if license_admission_path is not None:
        if not license_admission_path.exists():
            print(
                f"[quality-dashboard] missing report for license_admission: {license_admission_path}",
                file=sys.stderr,
            )
            return 2
        try:
            reports["license_admission"] = _load_json_object(license_admission_path)
        except Exception as exc:
            print(
                f"[quality-dashboard] invalid report for license_admission: {license_admission_path} error={exc}",
                file=sys.stderr,
            )
            return 2

    snapshot = _build_snapshot(args=args, reports=reports)
    output_path = _resolve_path(project_root, str(args.output))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[quality-dashboard] snapshot={output_path}")
    print(json.dumps(snapshot["summary"], ensure_ascii=False))

    trend_output_raw = str(args.trend_output or "").strip()
    baseline_raw = str(args.baseline or "").strip()
    if trend_output_raw:
        trend_path = _resolve_path(project_root, trend_output_raw)
        baseline_path = _resolve_path(project_root, baseline_raw) if baseline_raw else None
        if baseline_path is None or not baseline_path.exists():
            print(
                f"[quality-dashboard] baseline not found for trend report: {baseline_path}",
                file=sys.stderr,
            )
            return 2
        try:
            baseline_payload = _load_json_object(baseline_path)
            trend_payload = _build_trend_report(
                snapshot=snapshot,
                baseline=baseline_payload,
                baseline_path=baseline_path,
            )
        except Exception as exc:
            print(f"[quality-dashboard] trend build failed: {exc}", file=sys.stderr)
            return 2
        trend_path.parent.mkdir(parents=True, exist_ok=True)
        trend_path.write_text(json.dumps(trend_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[quality-dashboard] trend={trend_path}")
        print(json.dumps(trend_payload["summary"], ensure_ascii=False))

    if str(snapshot.get("summary", {}).get("status") or "") != "pass":
        print("[quality-dashboard] FAILED")
        return 1

    print("[quality-dashboard] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
