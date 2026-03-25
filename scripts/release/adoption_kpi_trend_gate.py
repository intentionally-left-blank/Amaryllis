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
            "Validate adoption KPI regressions against baseline snapshot and fail when "
            "regression budget is exceeded."
        )
    )
    parser.add_argument(
        "--snapshot-report",
        default=str(
            os.getenv(
                "AMARYLLIS_ADOPTION_KPI_SNAPSHOT_REPORT",
                "artifacts/adoption-kpi-snapshot-final.json",
            )
        ).strip(),
        help="Path to current adoption KPI snapshot JSON.",
    )
    parser.add_argument(
        "--baseline",
        default=str(
            os.getenv(
                "AMARYLLIS_ADOPTION_KPI_BASELINE",
                "eval/baselines/quality/adoption_kpi_snapshot_baseline.json",
            )
        ).strip(),
        help="Path to baseline adoption KPI snapshot JSON.",
    )
    parser.add_argument(
        "--max-activation-success-regression-pct",
        type=float,
        default=float(os.getenv("AMARYLLIS_ADOPTION_MAX_ACTIVATION_SUCCESS_REGRESSION_PCT", "1")),
        help="Maximum allowed activation-success regression in percentage points.",
    )
    parser.add_argument(
        "--max-activation-blocked-rate-increase-pct",
        type=float,
        default=float(os.getenv("AMARYLLIS_ADOPTION_MAX_ACTIVATION_BLOCKED_RATE_INCREASE_PCT", "0")),
        help="Maximum allowed activation-blocked-rate increase in percentage points.",
    )
    parser.add_argument(
        "--max-install-success-regression-pct",
        type=float,
        default=float(os.getenv("AMARYLLIS_ADOPTION_MAX_INSTALL_SUCCESS_REGRESSION_PCT", "1")),
        help="Maximum allowed install-success regression in percentage points.",
    )
    parser.add_argument(
        "--max-retention-proxy-regression-pct",
        type=float,
        default=float(os.getenv("AMARYLLIS_ADOPTION_MAX_RETENTION_PROXY_REGRESSION_PCT", "1")),
        help="Maximum allowed retention-proxy regression in percentage points.",
    )
    parser.add_argument(
        "--max-feature-adoption-regression-pct",
        type=float,
        default=float(os.getenv("AMARYLLIS_ADOPTION_MAX_FEATURE_ADOPTION_REGRESSION_PCT", "2")),
        help="Maximum allowed feature-adoption regression in percentage points.",
    )
    parser.add_argument(
        "--max-api-quickstart-pass-rate-regression-pct",
        type=float,
        default=float(os.getenv("AMARYLLIS_ADOPTION_MAX_API_QUICKSTART_PASS_RATE_REGRESSION_PCT", "1")),
        help="Maximum allowed API quickstart pass-rate regression in percentage points.",
    )
    parser.add_argument(
        "--max-channel-coverage-regression-pct",
        type=float,
        default=float(os.getenv("AMARYLLIS_ADOPTION_MAX_CHANNEL_COVERAGE_REGRESSION_PCT", "1")),
        help="Maximum allowed distribution-channel coverage regression in percentage points.",
    )
    parser.add_argument(
        "--output",
        default="artifacts/adoption-kpi-trend-gate-report.json",
        help="Output report path.",
    )
    return parser.parse_args()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_path(project_root: Path, raw_path: str) -> Path:
    path = Path(str(raw_path).strip()).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve()


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
    for field in (
        "max_activation_success_regression_pct",
        "max_activation_blocked_rate_increase_pct",
        "max_install_success_regression_pct",
        "max_retention_proxy_regression_pct",
        "max_feature_adoption_regression_pct",
        "max_api_quickstart_pass_rate_regression_pct",
        "max_channel_coverage_regression_pct",
    ):
        value = _safe_float(getattr(args, field))
        if value < 0:
            print(f"[adoption-kpi-trend-gate] --{field.replace('_', '-')} must be >= 0", file=sys.stderr)
            return 2

    project_root = Path(__file__).resolve().parents[2]
    snapshot_path = _resolve_path(project_root, str(args.snapshot_report))
    baseline_path = _resolve_path(project_root, str(args.baseline))
    if not snapshot_path.exists():
        print(f"[adoption-kpi-trend-gate] snapshot report not found: {snapshot_path}", file=sys.stderr)
        return 2
    if not baseline_path.exists():
        print(f"[adoption-kpi-trend-gate] baseline report not found: {baseline_path}", file=sys.stderr)
        return 2

    try:
        snapshot = _load_json_object(snapshot_path)
        baseline = _load_json_object(baseline_path)
    except Exception as exc:
        print(f"[adoption-kpi-trend-gate] invalid report payload: {exc}", file=sys.stderr)
        return 2

    if str(snapshot.get("suite") or "").strip() != "adoption_kpi_snapshot_v1":
        print("[adoption-kpi-trend-gate] unexpected snapshot suite", file=sys.stderr)
        return 2
    baseline_suite = str(baseline.get("suite") or "").strip()
    if baseline_suite not in {"adoption_kpi_snapshot_baseline_v1", "adoption_kpi_snapshot_v1"}:
        print("[adoption-kpi-trend-gate] unexpected baseline suite", file=sys.stderr)
        return 2

    snapshot_signals = _signal_map(snapshot)
    baseline_signals = _signal_map(baseline)

    tracked: list[dict[str, Any]] = [
        {
            "metric_id": "user_journey.activation_success_rate_pct",
            "comparator": "gte",
            "threshold": float(args.max_activation_success_regression_pct),
            "unit": "pct",
        },
        {
            "metric_id": "user_journey.activation_blocked_rate_pct",
            "comparator": "lte",
            "threshold": float(args.max_activation_blocked_rate_increase_pct),
            "unit": "pct",
        },
        {
            "metric_id": "user_journey.install_success_rate_pct",
            "comparator": "gte",
            "threshold": float(args.max_install_success_regression_pct),
            "unit": "pct",
        },
        {
            "metric_id": "user_journey.retention_proxy_success_rate_pct",
            "comparator": "gte",
            "threshold": float(args.max_retention_proxy_regression_pct),
            "unit": "pct",
        },
        {
            "metric_id": "user_journey.feature_adoption_rate_pct",
            "comparator": "gte",
            "threshold": float(args.max_feature_adoption_regression_pct),
            "unit": "pct",
        },
        {
            "metric_id": "api_quickstart_compat.pass_rate_pct",
            "comparator": "gte",
            "threshold": float(args.max_api_quickstart_pass_rate_regression_pct),
            "unit": "pct",
        },
        {
            "metric_id": "distribution_channel_manifest.coverage_pct",
            "comparator": "gte",
            "threshold": float(args.max_channel_coverage_regression_pct),
            "unit": "pct",
        },
    ]

    tracked_ids = [str(item["metric_id"]) for item in tracked]
    checks: list[dict[str, Any]] = [
        _check_bool(
            check_id="snapshot.required_metrics_present",
            source="snapshot",
            passed=all(metric_id in snapshot_signals for metric_id in tracked_ids),
        ),
        _check_bool(
            check_id="baseline.required_metrics_present",
            source="baseline",
            passed=all(metric_id in baseline_signals for metric_id in tracked_ids),
        ),
    ]
    comparisons: list[dict[str, Any]] = []
    for row in tracked:
        metric_id = str(row["metric_id"])
        if metric_id not in snapshot_signals or metric_id not in baseline_signals:
            continue
        comparator = str(row["comparator"])
        threshold = _safe_float(row["threshold"])
        current_value = _safe_float(snapshot_signals.get(metric_id, {}).get("value"))
        baseline_value = _safe_float(baseline_signals.get(metric_id, {}).get("value"))
        delta = current_value - baseline_value
        directional_delta = baseline_value - current_value if comparator == "lte" else current_value - baseline_value
        regression_pct = max(0.0, baseline_value - current_value) if comparator == "gte" else max(
            0.0, current_value - baseline_value
        )
        direction = "unchanged"
        if directional_delta > 0:
            direction = "improved"
        elif directional_delta < 0:
            direction = "regressed"

        checks.append(
            _check(
                check_id=f"trend.{metric_id}.regression_pct",
                source="adoption_kpi_trend",
                value=regression_pct,
                threshold=threshold,
                comparator="lte",
                unit="pct",
            )
        )
        comparisons.append(
            {
                "metric_id": metric_id,
                "comparator": comparator,
                "unit": str(row.get("unit") or ""),
                "current": round(current_value, 6),
                "baseline": round(baseline_value, 6),
                "delta": round(delta, 6),
                "directional_delta": round(directional_delta, 6),
                "regression_pct": round(regression_pct, 6),
                "direction": direction,
            }
        )

    checks_total = len(checks)
    checks_failed = sum(1 for item in checks if not bool(item.get("passed")))
    checks_passed = checks_total - checks_failed
    summary = {
        "status": "pass" if checks_failed == 0 else "fail",
        "checks_total": checks_total,
        "checks_passed": checks_passed,
        "checks_failed": checks_failed,
        "compared_metrics": len(comparisons),
        "improved": sum(1 for item in comparisons if str(item.get("direction")) == "improved"),
        "regressed": sum(1 for item in comparisons if str(item.get("direction")) == "regressed"),
        "unchanged": sum(1 for item in comparisons if str(item.get("direction")) == "unchanged"),
    }

    report = {
        "generated_at": _utc_now_iso(),
        "suite": "adoption_kpi_trend_gate_v1",
        "snapshot": {
            "path": str(snapshot_path),
            "suite": str(snapshot.get("suite") or ""),
            "generated_at": str(snapshot.get("generated_at") or ""),
        },
        "baseline": {
            "path": str(baseline_path),
            "suite": baseline_suite,
            "generated_at": str(baseline.get("generated_at") or ""),
        },
        "config": {
            "thresholds": {
                "max_activation_success_regression_pct": float(args.max_activation_success_regression_pct),
                "max_activation_blocked_rate_increase_pct": float(args.max_activation_blocked_rate_increase_pct),
                "max_install_success_regression_pct": float(args.max_install_success_regression_pct),
                "max_retention_proxy_regression_pct": float(args.max_retention_proxy_regression_pct),
                "max_feature_adoption_regression_pct": float(args.max_feature_adoption_regression_pct),
                "max_api_quickstart_pass_rate_regression_pct": float(
                    args.max_api_quickstart_pass_rate_regression_pct
                ),
                "max_channel_coverage_regression_pct": float(args.max_channel_coverage_regression_pct),
            }
        },
        "comparisons": comparisons,
        "checks": checks,
        "summary": summary,
    }

    output_path = _resolve_path(project_root, str(args.output))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[adoption-kpi-trend-gate] report={output_path}")
    print(json.dumps(summary, ensure_ascii=False))

    if checks_failed > 0:
        print("[adoption-kpi-trend-gate] FAILED")
        return 1

    print("[adoption-kpi-trend-gate] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
