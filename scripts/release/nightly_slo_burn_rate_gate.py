#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import statistics
import sys
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate nightly SLO burn-rate trend and fail on sustained error-budget "
            "burn anomalies."
        )
    )
    parser.add_argument(
        "--report",
        default="artifacts/nightly-reliability-report.json",
        help="Path to nightly reliability report JSON.",
    )
    parser.add_argument(
        "--max-request-burn-rate",
        type=float,
        default=-1.0,
        help="Override request burn-rate threshold. Default: use report quality budget.",
    )
    parser.add_argument(
        "--max-run-burn-rate",
        type=float,
        default=-1.0,
        help="Override run burn-rate threshold. Default: use report quality budget.",
    )
    parser.add_argument(
        "--max-consecutive-request-breach-samples",
        type=int,
        default=2,
        help="Maximum allowed consecutive request burn-rate breach samples.",
    )
    parser.add_argument(
        "--max-consecutive-run-breach-samples",
        type=int,
        default=2,
        help="Maximum allowed consecutive run burn-rate breach samples.",
    )
    parser.add_argument(
        "--output",
        default="artifacts/nightly-burn-rate-gate-report.json",
        help="Output report path.",
    )
    return parser.parse_args()


def _as_float(value: Any) -> float | None:
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _percentile(values: list[float], p: int) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    rank = max(0, min(len(sorted_values) - 1, int(round((p / 100.0) * (len(sorted_values) - 1)))))
    return float(sorted_values[rank])


def _max_consecutive_breaches(values: list[float], *, threshold: float) -> int:
    max_streak = 0
    streak = 0
    for value in values:
        if float(value) > float(threshold):
            streak += 1
            max_streak = max(max_streak, streak)
            continue
        streak = 0
    return max_streak


def _analyze_scope(
    *,
    samples: list[dict[str, Any]],
    value_key: str,
    threshold: float,
) -> dict[str, Any]:
    values: list[float] = []
    rounds: list[int] = []
    for item in samples:
        value = _as_float(item.get(value_key))
        if value is None:
            continue
        values.append(value)
        rounds.append(int(item.get("round", 0)))

    breach_rounds = [rounds[index] for index, value in enumerate(values) if float(value) > float(threshold)]
    return {
        "sample_count": len(values),
        "threshold": round(float(threshold), 6),
        "avg": round(statistics.mean(values), 6) if values else 0.0,
        "p95": round(_percentile(values, 95), 6),
        "max": round(max(values), 6) if values else 0.0,
        "breach_samples": len(breach_rounds),
        "max_consecutive_breach_samples": _max_consecutive_breaches(values, threshold=threshold),
        "breach_rounds": breach_rounds,
    }


def _resolve_threshold(*, cli_value: float, report_summary: dict[str, Any], key: str) -> float | None:
    if float(cli_value) > 0:
        return float(cli_value)
    scope = report_summary.get(key)
    if not isinstance(scope, dict):
        return None
    return _as_float(scope.get("budget"))


def main() -> int:
    args = _parse_args()
    if args.max_request_burn_rate != -1.0 and args.max_request_burn_rate <= 0:
        print("[nightly-burn-rate] --max-request-burn-rate must be > 0 or omitted", file=sys.stderr)
        return 2
    if args.max_run_burn_rate != -1.0 and args.max_run_burn_rate <= 0:
        print("[nightly-burn-rate] --max-run-burn-rate must be > 0 or omitted", file=sys.stderr)
        return 2
    if args.max_consecutive_request_breach_samples < 0:
        print(
            "[nightly-burn-rate] --max-consecutive-request-breach-samples must be >= 0",
            file=sys.stderr,
        )
        return 2
    if args.max_consecutive_run_breach_samples < 0:
        print(
            "[nightly-burn-rate] --max-consecutive-run-breach-samples must be >= 0",
            file=sys.stderr,
        )
        return 2

    project_root = Path(__file__).resolve().parents[2]
    report_path = Path(str(args.report).strip())
    if not report_path.is_absolute():
        report_path = project_root / report_path
    if not report_path.exists():
        print(f"[nightly-burn-rate] report not found: {report_path}", file=sys.stderr)
        return 2

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        print("[nightly-burn-rate] report payload must be a JSON object", file=sys.stderr)
        return 2

    burn_rate = payload.get("burn_rate")
    if not isinstance(burn_rate, dict):
        print("[nightly-burn-rate] report missing burn_rate section", file=sys.stderr)
        return 2

    samples_raw = burn_rate.get("samples")
    summary_raw = burn_rate.get("summary")
    if not isinstance(samples_raw, list) or not isinstance(summary_raw, dict):
        print("[nightly-burn-rate] report burn_rate section is malformed", file=sys.stderr)
        return 2

    samples: list[dict[str, Any]] = [item for item in samples_raw if isinstance(item, dict)]
    if not samples:
        print("[nightly-burn-rate] report contains no burn-rate samples", file=sys.stderr)
        return 2

    request_threshold = _resolve_threshold(
        cli_value=float(args.max_request_burn_rate),
        report_summary=summary_raw,
        key="request",
    )
    run_threshold = _resolve_threshold(
        cli_value=float(args.max_run_burn_rate),
        report_summary=summary_raw,
        key="runs",
    )
    if request_threshold is None or request_threshold <= 0:
        print("[nightly-burn-rate] request burn-rate threshold unavailable/invalid", file=sys.stderr)
        return 2
    if run_threshold is None or run_threshold <= 0:
        print("[nightly-burn-rate] run burn-rate threshold unavailable/invalid", file=sys.stderr)
        return 2

    request_analysis = _analyze_scope(
        samples=samples,
        value_key="request_burn_rate",
        threshold=float(request_threshold),
    )
    run_analysis = _analyze_scope(
        samples=samples,
        value_key="run_burn_rate",
        threshold=float(run_threshold),
    )

    failures: list[str] = []
    if int(request_analysis["max_consecutive_breach_samples"]) > int(args.max_consecutive_request_breach_samples):
        failures.append(
            "request burn-rate sustained breach: "
            f"max_consecutive={request_analysis['max_consecutive_breach_samples']} "
            f"allowed={args.max_consecutive_request_breach_samples}"
        )
    if int(run_analysis["max_consecutive_breach_samples"]) > int(args.max_consecutive_run_breach_samples):
        failures.append(
            "run burn-rate sustained breach: "
            f"max_consecutive={run_analysis['max_consecutive_breach_samples']} "
            f"allowed={args.max_consecutive_run_breach_samples}"
        )

    output = {
        "generated_at": _utc_now_iso(),
        "suite": "nightly_slo_burn_rate_gate_v1",
        "source_report": str(report_path),
        "thresholds": {
            "request_burn_rate": round(float(request_threshold), 6),
            "run_burn_rate": round(float(run_threshold), 6),
            "max_consecutive_request_breach_samples": int(args.max_consecutive_request_breach_samples),
            "max_consecutive_run_breach_samples": int(args.max_consecutive_run_breach_samples),
        },
        "summary": {
            "sample_count": len(samples),
            "request": request_analysis,
            "runs": run_analysis,
        },
        "passed": len(failures) == 0,
        "failures": failures,
    }

    output_path = Path(str(args.output).strip())
    if not output_path.is_absolute():
        output_path = project_root / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"[nightly-burn-rate] report={output_path}")
    print(json.dumps(output["summary"], ensure_ascii=False))

    if failures:
        print("[nightly-burn-rate] FAILED")
        for reason in failures:
            print(f"- {reason}")
        return 1

    print("[nightly-burn-rate] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
