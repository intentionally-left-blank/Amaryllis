#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate adoption KPI funnel contract across user-journey, developer quickstart, "
            "and distribution channel readiness artifacts."
        )
    )
    parser.add_argument(
        "--user-journey-report",
        default=str(os.getenv("AMARYLLIS_ADOPTION_USER_JOURNEY_REPORT", "artifacts/user-journey-benchmark-report.json")).strip(),
        help="Path to user journey benchmark report JSON.",
    )
    parser.add_argument(
        "--api-quickstart-report",
        default=str(os.getenv("AMARYLLIS_ADOPTION_API_QUICKSTART_REPORT", "artifacts/api-quickstart-compat-report.json")).strip(),
        help="Path to API quickstart compatibility gate report JSON.",
    )
    parser.add_argument(
        "--distribution-channel-manifest-report",
        default=str(
            os.getenv(
                "AMARYLLIS_ADOPTION_DISTRIBUTION_MANIFEST_REPORT",
                "artifacts/distribution-channel-manifest-report.json",
            )
        ).strip(),
        help="Path to distribution channel manifest gate report JSON.",
    )
    parser.add_argument(
        "--quality-dashboard-report",
        default=str(os.getenv("AMARYLLIS_ADOPTION_QUALITY_DASHBOARD_REPORT", "")).strip(),
        help="Optional path to release quality dashboard report JSON for signal-surface validation.",
    )
    parser.add_argument(
        "--min-api-quickstart-pass-rate-pct",
        type=float,
        default=float(os.getenv("AMARYLLIS_ADOPTION_MIN_API_QUICKSTART_PASS_RATE_PCT", "100")),
        help="Minimum required API quickstart pass-rate in percent (0..100).",
    )
    parser.add_argument(
        "--min-distribution-channel-coverage-pct",
        type=float,
        default=float(os.getenv("AMARYLLIS_ADOPTION_MIN_DISTRIBUTION_CHANNEL_COVERAGE_PCT", "100")),
        help="Minimum required distribution channel manifest coverage in percent (0..100).",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional JSON report output path.",
    )
    return parser.parse_args()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_path(project_root: Path, raw_path: str) -> Path:
    path = Path(str(raw_path).strip()).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def _check_bool(*, check_id: str, source: str, passed: bool) -> dict[str, Any]:
    return _check(
        check_id=check_id,
        source=source,
        value=1.0 if bool(passed) else 0.0,
        threshold=1.0,
        comparator="gte",
        unit="bool",
    )


def _signal_map(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    signals = payload.get("signals")
    if not isinstance(signals, list):
        return output
    for item in signals:
        if not isinstance(item, dict):
            continue
        metric_id = str(item.get("metric_id") or "").strip()
        if not metric_id:
            continue
        output[metric_id] = item
    return output


def main() -> int:
    args = _parse_args()
    for name in (
        "min_api_quickstart_pass_rate_pct",
        "min_distribution_channel_coverage_pct",
    ):
        value = _safe_float(getattr(args, name))
        if value < 0 or value > 100:
            print(
                f"[adoption-kpi-schema-gate] --{name.replace('_', '-')} must be in range 0..100",
                file=sys.stderr,
            )
            return 2

    project_root = Path(__file__).resolve().parents[2]

    journey_path = _resolve_path(project_root, str(args.user_journey_report))
    api_quickstart_path = _resolve_path(project_root, str(args.api_quickstart_report))
    distribution_manifest_path = _resolve_path(project_root, str(args.distribution_channel_manifest_report))
    quality_dashboard_raw = str(args.quality_dashboard_report or "").strip()
    quality_dashboard_path = _resolve_path(project_root, quality_dashboard_raw) if quality_dashboard_raw else None

    for label, path in (
        ("user_journey", journey_path),
        ("api_quickstart", api_quickstart_path),
        ("distribution_channel_manifest", distribution_manifest_path),
    ):
        if not path.exists():
            print(f"[adoption-kpi-schema-gate] missing source report for {label}: {path}", file=sys.stderr)
            return 2
    if quality_dashboard_path is not None and not quality_dashboard_path.exists():
        print(
            f"[adoption-kpi-schema-gate] missing source report for quality_dashboard: {quality_dashboard_path}",
            file=sys.stderr,
        )
        return 2

    try:
        journey = _load_json_object(journey_path)
        api_quickstart = _load_json_object(api_quickstart_path)
        distribution_manifest = _load_json_object(distribution_manifest_path)
        quality_dashboard = (
            _load_json_object(quality_dashboard_path) if quality_dashboard_path is not None else None
        )
    except Exception as exc:
        print(f"[adoption-kpi-schema-gate] failed to load reports: {exc}", file=sys.stderr)
        return 2

    if str(journey.get("suite") or "").strip() != "user_journey_benchmark_v1":
        print("[adoption-kpi-schema-gate] unexpected user journey suite", file=sys.stderr)
        return 2
    if str(api_quickstart.get("suite") or "").strip() != "api_quickstart_compatibility_gate_v1":
        print("[adoption-kpi-schema-gate] unexpected API quickstart suite", file=sys.stderr)
        return 2
    if str(distribution_manifest.get("suite") or "").strip() != "distribution_channel_manifest_gate_v1":
        print("[adoption-kpi-schema-gate] unexpected distribution channel manifest suite", file=sys.stderr)
        return 2
    if quality_dashboard is not None and str(quality_dashboard.get("suite") or "").strip() != "release_quality_dashboard_v1":
        print("[adoption-kpi-schema-gate] unexpected quality dashboard suite", file=sys.stderr)
        return 2

    checks: list[dict[str, Any]] = []

    journey_summary = journey.get("summary") if isinstance(journey.get("summary"), dict) else {}
    journey_thresholds = (
        journey.get("config", {}).get("thresholds")
        if isinstance(journey.get("config"), dict)
        and isinstance(journey.get("config", {}).get("thresholds"), dict)
        else {}
    )
    required_journey_metrics = {
        "activation_success_rate_pct",
        "activation_blocked_rate_pct",
        "install_success_rate_pct",
        "retention_proxy_success_rate_pct",
        "feature_adoption_rate_pct",
    }
    checks.append(
        _check_bool(
            check_id="journey.required_metrics_present",
            source="user_journey",
            passed=all(metric in journey_summary for metric in required_journey_metrics),
        )
    )
    checks.extend(
        [
            _check(
                check_id="journey.activation_success_rate_pct",
                source="user_journey",
                value=_safe_float(journey_summary.get("activation_success_rate_pct")),
                threshold=_safe_float(
                    journey_thresholds.get("min_activation_success_rate_pct"),
                    default=_safe_float(journey_summary.get("activation_success_rate_pct")),
                ),
                comparator="gte",
                unit="pct",
            ),
            _check(
                check_id="journey.activation_blocked_rate_pct",
                source="user_journey",
                value=_safe_float(journey_summary.get("activation_blocked_rate_pct")),
                threshold=_safe_float(
                    journey_thresholds.get("max_blocked_activation_rate_pct"),
                    default=_safe_float(journey_summary.get("activation_blocked_rate_pct")),
                ),
                comparator="lte",
                unit="pct",
            ),
            _check(
                check_id="journey.install_success_rate_pct",
                source="user_journey",
                value=_safe_float(journey_summary.get("install_success_rate_pct")),
                threshold=_safe_float(
                    journey_thresholds.get("min_install_success_rate_pct"),
                    default=_safe_float(journey_summary.get("install_success_rate_pct")),
                ),
                comparator="gte",
                unit="pct",
            ),
            _check(
                check_id="journey.retention_proxy_success_rate_pct",
                source="user_journey",
                value=_safe_float(journey_summary.get("retention_proxy_success_rate_pct")),
                threshold=_safe_float(
                    journey_thresholds.get("min_retention_proxy_success_rate_pct"),
                    default=_safe_float(journey_summary.get("retention_proxy_success_rate_pct")),
                ),
                comparator="gte",
                unit="pct",
            ),
            _check(
                check_id="journey.feature_adoption_rate_pct",
                source="user_journey",
                value=_safe_float(journey_summary.get("feature_adoption_rate_pct")),
                threshold=_safe_float(
                    journey_thresholds.get("min_feature_adoption_rate_pct"),
                    default=_safe_float(journey_summary.get("feature_adoption_rate_pct")),
                ),
                comparator="gte",
                unit="pct",
            ),
        ]
    )

    api_summary = api_quickstart.get("summary") if isinstance(api_quickstart.get("summary"), dict) else {}
    api_checks_total = max(0.0, _safe_float(api_summary.get("checks_total")))
    api_checks_failed = max(0.0, _safe_float(api_summary.get("checks_failed")))
    api_checks_passed = max(0.0, api_checks_total - api_checks_failed)
    api_pass_rate = (
        (api_checks_passed / api_checks_total) * 100.0
        if api_checks_total > 0
        else (100.0 if str(api_summary.get("status") or "").strip().lower() == "pass" else 0.0)
    )
    checks.extend(
        [
            _check_bool(
                check_id="api_quickstart.required_summary_fields_present",
                source="api_quickstart_compat",
                passed="checks_total" in api_summary and "checks_failed" in api_summary,
            ),
            _check(
                check_id="api_quickstart.pass_rate_pct",
                source="api_quickstart_compat",
                value=api_pass_rate,
                threshold=float(args.min_api_quickstart_pass_rate_pct),
                comparator="gte",
                unit="pct",
            ),
        ]
    )

    manifest_summary = (
        distribution_manifest.get("summary")
        if isinstance(distribution_manifest.get("summary"), dict)
        else {}
    )
    manifest_checks_total = max(0.0, _safe_float(manifest_summary.get("checks_total")))
    manifest_checks_failed = max(0.0, _safe_float(manifest_summary.get("checks_failed")))
    manifest_checks_passed = max(0.0, manifest_checks_total - manifest_checks_failed)
    manifest_coverage = (
        (manifest_checks_passed / manifest_checks_total) * 100.0
        if manifest_checks_total > 0
        else (100.0 if str(manifest_summary.get("status") or "").strip().lower() == "pass" else 0.0)
    )
    checks.extend(
        [
            _check_bool(
                check_id="distribution_channel_manifest.required_summary_fields_present",
                source="distribution_channel_manifest",
                passed="checks_total" in manifest_summary and "checks_failed" in manifest_summary,
            ),
            _check(
                check_id="distribution_channel_manifest.coverage_pct",
                source="distribution_channel_manifest",
                value=manifest_coverage,
                threshold=float(args.min_distribution_channel_coverage_pct),
                comparator="gte",
                unit="pct",
            ),
            _check(
                check_id="distribution_channel_manifest.checks_failed",
                source="distribution_channel_manifest",
                value=manifest_checks_failed,
                threshold=0.0,
                comparator="lte",
                unit="count",
            ),
        ]
    )

    if isinstance(quality_dashboard, dict):
        signal_map = _signal_map(quality_dashboard)
        expected_dashboard_signals = (
            "user_journey.activation_success_rate_pct",
            "user_journey.install_success_rate_pct",
            "user_journey.retention_proxy_success_rate_pct",
            "user_journey.feature_adoption_rate_pct",
            "api_quickstart_compat.pass_rate_pct",
            "distribution_channel_manifest.coverage_pct",
        )
        checks.append(
            _check_bool(
                check_id="quality_dashboard.required_adoption_signals_present",
                source="quality_dashboard",
                passed=all(metric_id in signal_map for metric_id in expected_dashboard_signals),
            )
        )
        checks.append(
            _check_bool(
                check_id="quality_dashboard.required_adoption_signals_passed",
                source="quality_dashboard",
                passed=all(bool(signal_map.get(metric_id, {}).get("passed")) for metric_id in expected_dashboard_signals),
            )
        )

    checks_total = len(checks)
    checks_passed = sum(1 for item in checks if bool(item.get("passed")))
    checks_failed = checks_total - checks_passed
    summary = {
        "status": "pass" if checks_failed == 0 else "fail",
        "checks_total": checks_total,
        "checks_passed": checks_passed,
        "checks_failed": checks_failed,
    }
    report = {
        "generated_at": _utc_now_iso(),
        "suite": "adoption_kpi_schema_gate_v1",
        "sources": {
            "user_journey": str(journey_path),
            "api_quickstart": str(api_quickstart_path),
            "distribution_channel_manifest": str(distribution_manifest_path),
            "quality_dashboard": str(quality_dashboard_path) if quality_dashboard_path is not None else "",
        },
        "kpis": {
            "journey_install_success_rate_pct": round(
                _safe_float(journey_summary.get("install_success_rate_pct")),
                4,
            ),
            "journey_retention_proxy_success_rate_pct": round(
                _safe_float(journey_summary.get("retention_proxy_success_rate_pct")),
                4,
            ),
            "journey_feature_adoption_rate_pct": round(
                _safe_float(journey_summary.get("feature_adoption_rate_pct")),
                4,
            ),
            "api_quickstart_pass_rate_pct": round(api_pass_rate, 4),
            "distribution_channel_manifest_coverage_pct": round(manifest_coverage, 4),
        },
        "checks": checks,
        "summary": summary,
    }

    if args.output:
        output_path = _resolve_path(project_root, str(args.output))
        _write_json(output_path, report)

    if checks_failed > 0:
        print("[adoption-kpi-schema-gate] FAILED")
        for item in checks:
            if not bool(item.get("passed")):
                print(f"- {item.get('id')}")
        return 1

    print("[adoption-kpi-schema-gate] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
