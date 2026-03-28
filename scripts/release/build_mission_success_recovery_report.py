from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build mission success/recovery public report pack from release and nightly "
            "reliability artifacts."
        )
    )
    parser.add_argument(
        "--mission-queue-report",
        default="",
        help="Optional mission queue load report JSON path.",
    )
    parser.add_argument(
        "--fault-injection-report",
        default="",
        help="Optional fault injection reliability report JSON path.",
    )
    parser.add_argument(
        "--quality-dashboard-report",
        default="",
        help="Optional release quality dashboard report JSON path.",
    )
    parser.add_argument(
        "--user-journey-report",
        default="",
        help="Optional user journey benchmark report JSON path.",
    )
    parser.add_argument(
        "--distribution-resilience-report",
        default="",
        help="Optional distribution resilience report JSON path.",
    )
    parser.add_argument(
        "--macos-desktop-parity-report",
        default="",
        help="Optional macOS desktop parity smoke report JSON path.",
    )
    parser.add_argument(
        "--nightly-reliability-report",
        default="",
        help="Optional nightly reliability report JSON path.",
    )
    parser.add_argument(
        "--nightly-burn-rate-report",
        default="",
        help="Optional nightly burn-rate gate report JSON path.",
    )
    parser.add_argument(
        "--breaker-soak-report",
        default="",
        help="Optional autonomy circuit-breaker stability soak gate report JSON path.",
    )
    parser.add_argument(
        "--adoption-kpi-trend-report",
        default="",
        help="Optional adoption KPI trend gate report JSON path.",
    )
    parser.add_argument(
        "--scope",
        default="auto",
        choices=("auto", "release", "nightly"),
        help="Report scope label.",
    )
    parser.add_argument(
        "--output",
        default="artifacts/mission-success-recovery-report.json",
        help="Output report path.",
    )
    return parser.parse_args()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_optional_path(project_root: Path, raw: str) -> Path | None:
    candidate = str(raw or "").strip()
    if not candidate:
        return None
    path = Path(candidate)
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


def _signal_value(payload: dict[str, Any], metric_id: str) -> float | None:
    signals = payload.get("signals")
    if not isinstance(signals, list):
        return None
    for item in signals:
        if not isinstance(item, dict):
            continue
        if str(item.get("metric_id") or "").strip() != str(metric_id).strip():
            continue
        try:
            return float(item.get("value"))
        except Exception:
            return None
    return None


def _check(
    *,
    check_id: str,
    source: str,
    value: float,
    threshold: float,
    comparator: str,
    unit: str,
) -> dict[str, Any]:
    normalized = str(comparator).strip().lower()
    if normalized not in {"lte", "gte"}:
        raise ValueError(f"Unsupported comparator: {comparator}")
    passed = value <= threshold if normalized == "lte" else value >= threshold
    return {
        "id": check_id,
        "source": source,
        "value": round(float(value), 6),
        "threshold": round(float(threshold), 6),
        "comparator": normalized,
        "unit": unit,
        "passed": bool(passed),
    }


def _scope_from_sources(*, requested: str, nightly_present: bool) -> str:
    if requested != "auto":
        return requested
    return "nightly" if nightly_present else "release"


def _source_class(source: str) -> str:
    normalized = str(source or "").strip().lower()
    mapping = {
        "mission_queue": "mission_execution",
        "fault_injection": "recovery",
        "quality_dashboard": "quality",
        "qos_governor": "runtime_qos",
        "distribution_resilience": "distribution",
        "macos_desktop_parity": "desktop_staging",
        "user_journey": "user_flow",
        "adoption_kpi_trend": "adoption_growth",
        "nightly_reliability": "nightly_reliability",
        "nightly_burn_rate": "nightly_reliability",
        "breaker_soak": "nightly_reliability",
    }
    return mapping.get(normalized, "other")


def _kpi_class(kpi_key: str) -> str:
    normalized = str(kpi_key or "").strip().lower()
    if normalized.startswith("mission_"):
        return "mission_execution"
    if normalized.startswith("recovery_"):
        return "recovery"
    if normalized.startswith("release_quality_"):
        return "quality"
    if normalized.startswith("qos_"):
        return "runtime_qos"
    if normalized.startswith("distribution_"):
        return "distribution"
    if normalized.startswith("desktop_staging_") or normalized.startswith("macos_desktop_"):
        return "desktop_staging"
    if normalized.startswith("journey_"):
        return "user_flow"
    if normalized.startswith("adoption_trend_") or normalized.startswith("nightly_adoption_trend_"):
        return "adoption_growth"
    if normalized.startswith("nightly_"):
        return "nightly_reliability"
    return "other"


def _class_order() -> list[str]:
    return [
        "mission_execution",
        "recovery",
        "quality",
        "runtime_qos",
        "distribution",
        "desktop_staging",
        "user_flow",
        "adoption_growth",
        "nightly_reliability",
        "other",
    ]


def _build_class_breakdown(
    *,
    checks: list[dict[str, Any]],
    kpis: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    breakdown: dict[str, dict[str, Any]] = {}
    for class_name in _class_order():
        breakdown[class_name] = {
            "checks_total": 0,
            "checks_passed": 0,
            "checks_failed": 0,
            "score_pct": 0.0,
            "status": "pass",
            "kpis": {},
        }

    for check in checks:
        if not isinstance(check, dict):
            continue
        class_name = _source_class(str(check.get("source") or ""))
        row = breakdown.setdefault(
            class_name,
            {
                "checks_total": 0,
                "checks_passed": 0,
                "checks_failed": 0,
                "score_pct": 0.0,
                "status": "pass",
                "kpis": {},
            },
        )
        row["checks_total"] = int(row.get("checks_total", 0)) + 1
        if bool(check.get("passed")):
            row["checks_passed"] = int(row.get("checks_passed", 0)) + 1
        else:
            row["checks_failed"] = int(row.get("checks_failed", 0)) + 1

    for key, value in kpis.items():
        class_name = _kpi_class(str(key))
        row = breakdown.setdefault(
            class_name,
            {
                "checks_total": 0,
                "checks_passed": 0,
                "checks_failed": 0,
                "score_pct": 0.0,
                "status": "pass",
                "kpis": {},
            },
        )
        row_kpis = row.get("kpis")
        if not isinstance(row_kpis, dict):
            row_kpis = {}
            row["kpis"] = row_kpis
        row_kpis[str(key)] = value

    for row in breakdown.values():
        total = int(row.get("checks_total", 0))
        passed = int(row.get("checks_passed", 0))
        failed = int(row.get("checks_failed", 0))
        score = (float(passed) / float(total) * 100.0) if total > 0 else 0.0
        row["score_pct"] = round(score, 4)
        row["status"] = "pass" if failed == 0 else "fail"

    return breakdown


def main() -> int:
    args = _parse_args()
    project_root = Path(__file__).resolve().parents[2]

    source_paths = {
        "mission_queue": _resolve_optional_path(project_root, str(args.mission_queue_report)),
        "fault_injection": _resolve_optional_path(project_root, str(args.fault_injection_report)),
        "quality_dashboard": _resolve_optional_path(project_root, str(args.quality_dashboard_report)),
        "distribution_resilience": _resolve_optional_path(project_root, str(args.distribution_resilience_report)),
        "macos_desktop_parity": _resolve_optional_path(project_root, str(args.macos_desktop_parity_report)),
        "user_journey": _resolve_optional_path(project_root, str(args.user_journey_report)),
        "adoption_kpi_trend": _resolve_optional_path(project_root, str(args.adoption_kpi_trend_report)),
        "nightly_reliability": _resolve_optional_path(project_root, str(args.nightly_reliability_report)),
        "nightly_burn_rate": _resolve_optional_path(project_root, str(args.nightly_burn_rate_report)),
        "breaker_soak": _resolve_optional_path(project_root, str(args.breaker_soak_report)),
    }

    reports: dict[str, dict[str, Any]] = {}
    for key, path in source_paths.items():
        if path is None:
            continue
        if not path.exists():
            print(f"[mission-report-pack] missing source report: {key} path={path}", file=sys.stderr)
            return 2
        try:
            reports[key] = _load_json_object(path)
        except Exception as exc:
            print(f"[mission-report-pack] invalid source report: {key} error={exc}", file=sys.stderr)
            return 2

    if not reports:
        print("[mission-report-pack] no source reports provided", file=sys.stderr)
        return 2

    checks: list[dict[str, Any]] = []
    kpis: dict[str, Any] = {}
    sources_meta: dict[str, dict[str, Any]] = {}
    scope = _scope_from_sources(
        requested=str(args.scope),
        nightly_present=("nightly_reliability" in reports or "nightly_burn_rate" in reports),
    )

    for key, payload in reports.items():
        suite = str(payload.get("suite") or "").strip()
        generated_at = str(payload.get("generated_at") or "").strip()
        path = source_paths.get(key)
        sources_meta[key] = {
            "suite": suite,
            "generated_at": generated_at,
            "path": str(path) if path is not None else "",
        }

    mission = reports.get("mission_queue")
    if isinstance(mission, dict):
        summary = mission.get("summary") if isinstance(mission.get("summary"), dict) else {}
        config = mission.get("config") if isinstance(mission.get("config"), dict) else {}
        success_rate = _safe_float(summary.get("success_rate_pct"))
        failed_or_canceled = _safe_float(summary.get("failed_or_canceled"))
        p95_queue_wait = _safe_float(summary.get("p95_queue_wait_ms"))
        p95_end_to_end = _safe_float(summary.get("p95_end_to_end_ms"))
        checks.extend(
            [
                _check(
                    check_id="mission.success_rate_pct",
                    source="mission_queue",
                    value=success_rate,
                    threshold=_safe_float(config.get("min_success_rate_pct")),
                    comparator="gte",
                    unit="pct",
                ),
                _check(
                    check_id="mission.failed_or_canceled",
                    source="mission_queue",
                    value=failed_or_canceled,
                    threshold=_safe_float(config.get("max_failed_runs")),
                    comparator="lte",
                    unit="count",
                ),
                _check(
                    check_id="mission.p95_queue_wait_ms",
                    source="mission_queue",
                    value=p95_queue_wait,
                    threshold=_safe_float(config.get("max_p95_queue_wait_ms")),
                    comparator="lte",
                    unit="ms",
                ),
                _check(
                    check_id="mission.p95_end_to_end_ms",
                    source="mission_queue",
                    value=p95_end_to_end,
                    threshold=_safe_float(config.get("max_p95_end_to_end_ms")),
                    comparator="lte",
                    unit="ms",
                ),
            ]
        )
        kpis["mission_success_rate_pct"] = round(success_rate, 4)
        kpis["mission_failed_or_canceled"] = int(failed_or_canceled)
        kpis["mission_p95_queue_wait_ms"] = round(p95_queue_wait, 2)
        kpis["mission_p95_end_to_end_ms"] = round(p95_end_to_end, 2)

    fault = reports.get("fault_injection")
    if isinstance(fault, dict):
        summary = fault.get("summary") if isinstance(fault.get("summary"), dict) else {}
        pass_rate = _safe_float(summary.get("pass_rate_pct"))
        checks.append(
            _check(
                check_id="recovery.pass_rate_pct",
                source="fault_injection",
                value=pass_rate,
                threshold=_safe_float(summary.get("min_pass_rate_pct")),
                comparator="gte",
                unit="pct",
            )
        )
        kpis["recovery_pass_rate_pct"] = round(pass_rate, 4)

    quality = reports.get("quality_dashboard")
    if isinstance(quality, dict):
        summary = quality.get("summary") if isinstance(quality.get("summary"), dict) else {}
        score = _safe_float(summary.get("quality_score_pct"))
        status = str(summary.get("status") or "").strip().lower()
        checks.append(
            _check(
                check_id="release.quality_dashboard_status",
                source="quality_dashboard",
                value=1.0 if status == "pass" else 0.0,
                threshold=1.0,
                comparator="gte",
                unit="bool",
            )
        )
        kpis["release_quality_score_pct"] = round(score, 4)
        qos_status = _signal_value(quality, "qos_governor.status")
        qos_checks_failed = _signal_value(quality, "qos_governor.checks_failed")
        if qos_status is not None:
            checks.append(
                _check(
                    check_id="qos_governor.status",
                    source="qos_governor",
                    value=float(qos_status),
                    threshold=1.0,
                    comparator="gte",
                    unit="bool",
                )
            )
            kpis["qos_gate_status"] = 1.0 if float(qos_status) >= 1.0 else 0.0
        if qos_checks_failed is not None:
            checks.append(
                _check(
                    check_id="qos_governor.checks_failed",
                    source="qos_governor",
                    value=float(qos_checks_failed),
                    threshold=0.0,
                    comparator="lte",
                    unit="count",
                )
            )
            kpis["qos_gate_checks_failed"] = int(max(0.0, float(qos_checks_failed)))

    distribution = reports.get("distribution_resilience")
    if isinstance(distribution, dict):
        summary = distribution.get("summary") if isinstance(distribution.get("summary"), dict) else {}
        status = str(summary.get("status") or "").strip().lower()
        checks_failed = _safe_float(summary.get("checks_failed"))
        score_pct = _safe_float(summary.get("score_pct"))
        checks.extend(
            [
                _check(
                    check_id="distribution.status",
                    source="distribution_resilience",
                    value=1.0 if status == "pass" else 0.0,
                    threshold=1.0,
                    comparator="gte",
                    unit="bool",
                ),
                _check(
                    check_id="distribution.checks_failed",
                    source="distribution_resilience",
                    value=checks_failed,
                    threshold=0.0,
                    comparator="lte",
                    unit="count",
                ),
            ]
        )
        kpis["distribution_score_pct"] = round(score_pct, 4)
        kpis["distribution_checks_failed"] = int(checks_failed)

    desktop_staging = reports.get("macos_desktop_parity")
    if isinstance(desktop_staging, dict):
        summary = desktop_staging.get("summary") if isinstance(desktop_staging.get("summary"), dict) else {}
        latency_ms = summary.get("latency_ms") if isinstance(summary.get("latency_ms"), dict) else {}
        status = str(summary.get("status") or "").strip().lower()
        checks_failed = _safe_float(summary.get("checks_failed"))
        error_rate_pct = _safe_float(summary.get("error_rate_pct"))
        p95_latency = _safe_float(latency_ms.get("p95"), default=0.0)
        checks.extend(
            [
                _check(
                    check_id="desktop_staging.status",
                    source="macos_desktop_parity",
                    value=1.0 if status == "pass" else 0.0,
                    threshold=1.0,
                    comparator="gte",
                    unit="bool",
                ),
                _check(
                    check_id="desktop_staging.checks_failed",
                    source="macos_desktop_parity",
                    value=checks_failed,
                    threshold=0.0,
                    comparator="lte",
                    unit="count",
                ),
                _check(
                    check_id="desktop_staging.error_rate_pct",
                    source="macos_desktop_parity",
                    value=error_rate_pct,
                    threshold=0.0,
                    comparator="lte",
                    unit="pct",
                ),
            ]
        )
        kpis["desktop_staging_checks_failed"] = int(checks_failed)
        kpis["desktop_staging_error_rate_pct"] = round(error_rate_pct, 4)
        kpis["desktop_staging_p95_latency_ms"] = round(p95_latency, 2)

    journey = reports.get("user_journey")
    if isinstance(journey, dict):
        summary = journey.get("summary") if isinstance(journey.get("summary"), dict) else {}
        thresholds = (
            journey.get("config", {}).get("thresholds")
            if isinstance(journey.get("config"), dict)
            and isinstance(journey.get("config", {}).get("thresholds"), dict)
            else {}
        )
        success_rate = _safe_float(summary.get("journey_success_rate_pct"))
        p95_journey = _safe_float(summary.get("p95_journey_latency_ms"))
        p95_plan = _safe_float(summary.get("p95_plan_dispatch_latency_ms"))
        p95_execute = _safe_float(summary.get("p95_execute_dispatch_latency_ms"))
        conversion = _safe_float(summary.get("plan_to_execute_conversion_rate_pct"))
        activation_success = _safe_float(summary.get("activation_success_rate_pct"))
        activation_blocked = _safe_float(summary.get("activation_blocked_rate_pct"))
        p95_activation = _safe_float(summary.get("p95_activation_latency_ms"))
        install_success = _safe_float(summary.get("install_success_rate_pct"))
        retention_proxy = _safe_float(summary.get("retention_proxy_success_rate_pct"))
        feature_adoption = _safe_float(summary.get("feature_adoption_rate_pct"))
        checks.extend(
            [
                _check(
                    check_id="journey.success_rate_pct",
                    source="user_journey",
                    value=success_rate,
                    threshold=_safe_float(thresholds.get("min_success_rate_pct"), default=success_rate),
                    comparator="gte",
                    unit="pct",
                ),
                _check(
                    check_id="journey.p95_end_to_end_ms",
                    source="user_journey",
                    value=p95_journey,
                    threshold=_safe_float(
                        thresholds.get("max_p95_journey_latency_ms"),
                        default=p95_journey,
                    ),
                    comparator="lte",
                    unit="ms",
                ),
                _check(
                    check_id="journey.p95_plan_dispatch_ms",
                    source="user_journey",
                    value=p95_plan,
                    threshold=_safe_float(
                        thresholds.get("max_p95_plan_dispatch_latency_ms"),
                        default=p95_plan,
                    ),
                    comparator="lte",
                    unit="ms",
                ),
                _check(
                    check_id="journey.p95_execute_dispatch_ms",
                    source="user_journey",
                    value=p95_execute,
                    threshold=_safe_float(
                        thresholds.get("max_p95_execute_dispatch_latency_ms"),
                        default=p95_execute,
                    ),
                    comparator="lte",
                    unit="ms",
                ),
                _check(
                    check_id="journey.plan_to_execute_conversion_rate_pct",
                    source="user_journey",
                    value=conversion,
                    threshold=_safe_float(
                        thresholds.get("min_plan_to_execute_conversion_rate_pct"),
                        default=conversion,
                    ),
                    comparator="gte",
                    unit="pct",
                ),
                _check(
                    check_id="journey.activation_success_rate_pct",
                    source="user_journey",
                    value=activation_success,
                    threshold=_safe_float(
                        thresholds.get("min_activation_success_rate_pct"),
                        default=activation_success,
                    ),
                    comparator="gte",
                    unit="pct",
                ),
                _check(
                    check_id="journey.activation_blocked_rate_pct",
                    source="user_journey",
                    value=activation_blocked,
                    threshold=_safe_float(
                        thresholds.get("max_blocked_activation_rate_pct"),
                        default=activation_blocked,
                    ),
                    comparator="lte",
                    unit="pct",
                ),
                _check(
                    check_id="journey.p95_activation_latency_ms",
                    source="user_journey",
                    value=p95_activation,
                    threshold=_safe_float(
                        thresholds.get("max_p95_activation_latency_ms"),
                        default=p95_activation,
                    ),
                    comparator="lte",
                    unit="ms",
                ),
                _check(
                    check_id="journey.install_success_rate_pct",
                    source="user_journey",
                    value=install_success,
                    threshold=_safe_float(
                        thresholds.get("min_install_success_rate_pct"),
                        default=install_success,
                    ),
                    comparator="gte",
                    unit="pct",
                ),
                _check(
                    check_id="journey.retention_proxy_success_rate_pct",
                    source="user_journey",
                    value=retention_proxy,
                    threshold=_safe_float(
                        thresholds.get("min_retention_proxy_success_rate_pct"),
                        default=retention_proxy,
                    ),
                    comparator="gte",
                    unit="pct",
                ),
                _check(
                    check_id="journey.feature_adoption_rate_pct",
                    source="user_journey",
                    value=feature_adoption,
                    threshold=_safe_float(
                        thresholds.get("min_feature_adoption_rate_pct"),
                        default=feature_adoption,
                    ),
                    comparator="gte",
                    unit="pct",
                ),
            ]
        )
        kpis["journey_success_rate_pct"] = round(success_rate, 4)
        kpis["journey_p95_end_to_end_ms"] = round(p95_journey, 2)
        kpis["journey_p95_plan_dispatch_ms"] = round(p95_plan, 2)
        kpis["journey_p95_execute_dispatch_ms"] = round(p95_execute, 2)
        kpis["journey_plan_to_execute_conversion_rate_pct"] = round(conversion, 4)
        kpis["journey_activation_success_rate_pct"] = round(activation_success, 4)
        kpis["journey_activation_blocked_rate_pct"] = round(activation_blocked, 4)
        kpis["journey_p95_activation_latency_ms"] = round(p95_activation, 2)
        kpis["journey_install_success_rate_pct"] = round(install_success, 4)
        kpis["journey_retention_proxy_success_rate_pct"] = round(retention_proxy, 4)
        kpis["journey_feature_adoption_rate_pct"] = round(feature_adoption, 4)

    adoption_trend = reports.get("adoption_kpi_trend")
    if isinstance(adoption_trend, dict):
        summary = adoption_trend.get("summary") if isinstance(adoption_trend.get("summary"), dict) else {}
        status = str(summary.get("status") or "").strip().lower()
        checks_failed = _safe_float(summary.get("checks_failed"))
        compared_metrics = _safe_float(summary.get("compared_metrics"))
        regressed_metrics = _safe_float(summary.get("regressed"))
        trend_passed = status == "pass" and checks_failed <= 0.0
        checks.extend(
            [
                _check(
                    check_id="adoption_trend.status",
                    source="adoption_kpi_trend",
                    value=1.0 if trend_passed else 0.0,
                    threshold=1.0,
                    comparator="gte",
                    unit="bool",
                ),
                _check(
                    check_id="adoption_trend.checks_failed",
                    source="adoption_kpi_trend",
                    value=checks_failed,
                    threshold=0.0,
                    comparator="lte",
                    unit="count",
                ),
                _check(
                    check_id="adoption_trend.compared_metrics",
                    source="adoption_kpi_trend",
                    value=compared_metrics,
                    threshold=1.0,
                    comparator="gte",
                    unit="count",
                ),
            ]
        )
        kpis["adoption_trend_gate_passed"] = bool(trend_passed)
        kpis["adoption_trend_checks_failed"] = int(max(0.0, checks_failed))
        kpis["adoption_trend_compared_metrics"] = int(max(0.0, compared_metrics))
        kpis["adoption_trend_regressed_metrics"] = int(max(0.0, regressed_metrics))
        if scope == "nightly":
            kpis["nightly_adoption_trend_gate_passed"] = bool(trend_passed)
            kpis["nightly_adoption_trend_regressed_metrics"] = int(max(0.0, regressed_metrics))

    nightly = reports.get("nightly_reliability")
    if isinstance(nightly, dict):
        summary = nightly.get("summary") if isinstance(nightly.get("summary"), dict) else {}
        thresholds = nightly.get("thresholds") if isinstance(nightly.get("thresholds"), dict) else {}
        success_rate = _safe_float(summary.get("success_rate_pct"))
        p95_latency = _safe_float(summary.get("p95_latency_ms"))
        jitter = _safe_float(summary.get("latency_jitter_ms"))
        checks.extend(
            [
                _check(
                    check_id="nightly.success_rate_pct",
                    source="nightly_reliability",
                    value=success_rate,
                    threshold=_safe_float(thresholds.get("min_success_rate_pct")),
                    comparator="gte",
                    unit="pct",
                ),
                _check(
                    check_id="nightly.p95_latency_ms",
                    source="nightly_reliability",
                    value=p95_latency,
                    threshold=_safe_float(thresholds.get("max_p95_latency_ms")),
                    comparator="lte",
                    unit="ms",
                ),
                _check(
                    check_id="nightly.latency_jitter_ms",
                    source="nightly_reliability",
                    value=jitter,
                    threshold=_safe_float(thresholds.get("max_latency_jitter_ms")),
                    comparator="lte",
                    unit="ms",
                ),
            ]
        )
        kpis["nightly_success_rate_pct"] = round(success_rate, 4)
        kpis["nightly_p95_latency_ms"] = round(p95_latency, 2)
        kpis["nightly_latency_jitter_ms"] = round(jitter, 2)

    burn = reports.get("nightly_burn_rate")
    if isinstance(burn, dict):
        passed = bool(burn.get("passed"))
        checks.append(
            _check(
                check_id="nightly.burn_rate_gate_passed",
                source="nightly_burn_rate",
                value=1.0 if passed else 0.0,
                threshold=1.0,
                comparator="gte",
                unit="bool",
            )
        )
        summary = burn.get("summary") if isinstance(burn.get("summary"), dict) else {}
        request = summary.get("request") if isinstance(summary.get("request"), dict) else {}
        runs = summary.get("runs") if isinstance(summary.get("runs"), dict) else {}
        kpis["nightly_burn_rate_gate_passed"] = passed
        kpis["nightly_request_max_consecutive_breach_samples"] = int(
            _safe_float(request.get("max_consecutive_breach_samples"))
        )
        kpis["nightly_run_max_consecutive_breach_samples"] = int(
            _safe_float(runs.get("max_consecutive_breach_samples"))
        )

    breaker_soak = reports.get("breaker_soak")
    if isinstance(breaker_soak, dict):
        summary = breaker_soak.get("summary") if isinstance(breaker_soak.get("summary"), dict) else {}
        config = breaker_soak.get("config") if isinstance(breaker_soak.get("config"), dict) else {}
        status = str(summary.get("status") or "").strip().lower()
        success_rate = _safe_float(summary.get("success_rate_pct"))
        cycles_failed = _safe_float(summary.get("cycles_failed"))
        p95_cycle_latency = _safe_float(summary.get("p95_cycle_latency_ms"))
        checks.extend(
            [
                _check(
                    check_id="nightly.breaker_soak_gate_passed",
                    source="breaker_soak",
                    value=1.0 if status == "pass" else 0.0,
                    threshold=1.0,
                    comparator="gte",
                    unit="bool",
                ),
                _check(
                    check_id="nightly.breaker_soak_success_rate_pct",
                    source="breaker_soak",
                    value=success_rate,
                    threshold=_safe_float(config.get("min_success_rate_pct"), default=success_rate),
                    comparator="gte",
                    unit="pct",
                ),
                _check(
                    check_id="nightly.breaker_soak_failed_cycles",
                    source="breaker_soak",
                    value=cycles_failed,
                    threshold=_safe_float(config.get("max_failed_cycles"), default=cycles_failed),
                    comparator="lte",
                    unit="count",
                ),
                _check(
                    check_id="nightly.breaker_soak_p95_cycle_latency_ms",
                    source="breaker_soak",
                    value=p95_cycle_latency,
                    threshold=_safe_float(config.get("max_p95_cycle_latency_ms"), default=p95_cycle_latency),
                    comparator="lte",
                    unit="ms",
                ),
            ]
        )
        soak_passed = status == "pass"
        kpis["nightly_breaker_soak_gate_passed"] = soak_passed
        kpis["nightly_breaker_soak_success_rate_pct"] = round(success_rate, 4)
        kpis["nightly_breaker_soak_cycles_failed"] = int(max(0.0, cycles_failed))
        kpis["nightly_breaker_soak_p95_cycle_latency_ms"] = round(p95_cycle_latency, 4)

    passed_checks = sum(1 for item in checks if bool(item.get("passed")))
    failed_checks = len(checks) - passed_checks
    class_breakdown = _build_class_breakdown(checks=checks, kpis=kpis)

    payload = {
        "generated_at": _utc_now_iso(),
        "suite": "mission_success_recovery_report_pack_v2",
        "schema_version": 2,
        "scope": scope,
        "sources": sources_meta,
        "kpis": kpis,
        "checks": checks,
        "class_order": _class_order(),
        "class_breakdown": class_breakdown,
        "summary": {
            "checks_total": len(checks),
            "checks_passed": passed_checks,
            "checks_failed": failed_checks,
            "status": "pass" if failed_checks == 0 else "fail",
        },
    }

    output_path = _resolve_optional_path(project_root, str(args.output))
    assert output_path is not None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[mission-report-pack] report={output_path}")
    print(json.dumps(payload["summary"], ensure_ascii=False))
    print("[mission-report-pack] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
