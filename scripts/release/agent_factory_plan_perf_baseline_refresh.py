#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Build Agent Factory plan perf baseline refresh report from profile gate reports "
            "and optionally emit a suggested updated baseline envelope."
        )
    )
    parser.add_argument(
        "--baseline",
        default="eval/baselines/quality/agent_factory_plan_perf_envelope.json",
        help="Path to baseline envelope JSON.",
    )
    parser.add_argument(
        "--report",
        action="append",
        default=[],
        help="Profile report mapping in format '<profile>=<path>'. Can be repeated.",
    )
    parser.add_argument(
        "--headroom-pct",
        type=float,
        default=35.0,
        help="Headroom percent applied to observed p95 latency for suggested threshold.",
    )
    parser.add_argument(
        "--min-headroom-ms",
        type=float,
        default=50.0,
        help="Minimum absolute headroom in milliseconds for suggested threshold.",
    )
    parser.add_argument(
        "--max-increase-pct",
        type=float,
        default=30.0,
        help="Maximum threshold increase percent per refresh cycle.",
    )
    parser.add_argument(
        "--max-decrease-pct",
        type=float,
        default=20.0,
        help="Maximum threshold decrease percent per refresh cycle.",
    )
    parser.add_argument(
        "--write-updated-baseline",
        default="",
        help="Optional output path for suggested updated baseline JSON.",
    )
    parser.add_argument(
        "--output",
        default="artifacts/agent-factory-plan-perf-baseline-refresh-report.json",
        help="Output refresh report path.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero when warnings/failures are present.",
    )
    return parser.parse_args()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_path(project_root: Path, raw_path: str) -> Path:
    candidate = Path(str(raw_path or "").strip()).expanduser()
    if not candidate.is_absolute():
        candidate = project_root / candidate
    return candidate.resolve()


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _parse_report_mapping(project_root: Path, items: list[str]) -> dict[str, Path]:
    mapping: dict[str, Path] = {}
    for raw_item in items:
        raw = str(raw_item or "").strip()
        if not raw:
            continue
        if "=" not in raw:
            raise ValueError(f"invalid --report mapping: {raw!r} (expected '<profile>=<path>')")
        profile_raw, path_raw = raw.split("=", 1)
        profile = str(profile_raw or "").strip().lower()
        if not profile:
            raise ValueError(f"invalid --report mapping: {raw!r} (empty profile)")
        path = _resolve_path(project_root, path_raw)
        mapping[profile] = path
    if not mapping:
        raise ValueError("at least one --report <profile>=<path> mapping is required")
    return mapping


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _compute_suggested_p95(
    *,
    observed_p95_ms: float,
    current_threshold_ms: float,
    headroom_pct: float,
    min_headroom_ms: float,
    max_increase_pct: float,
    max_decrease_pct: float,
) -> float:
    headroom_target = max(
        observed_p95_ms * (1.0 + (headroom_pct / 100.0)),
        observed_p95_ms + max(0.0, min_headroom_ms),
    )
    if current_threshold_ms <= 0:
        return round(max(observed_p95_ms, headroom_target), 3)
    upper = current_threshold_ms * (1.0 + max(0.0, max_increase_pct) / 100.0)
    lower = current_threshold_ms * (1.0 - max(0.0, max_decrease_pct) / 100.0)
    bounded = min(max(headroom_target, lower), upper)
    return round(max(observed_p95_ms, bounded), 3)


def _load_json_object(path: Path, *, error_prefix: str) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"{error_prefix}: missing file: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{error_prefix}: JSON root must be object: {path}")
    return payload


def main() -> int:
    args = _parse_args()
    project_root = Path(__file__).resolve().parents[2]
    baseline_path = _resolve_path(project_root, str(args.baseline))
    output_path = _resolve_path(project_root, str(args.output))
    updated_baseline_path = _resolve_path(project_root, str(args.write_updated_baseline)) if str(args.write_updated_baseline).strip() else None

    try:
        report_mapping = _parse_report_mapping(project_root, list(args.report or []))
        baseline_payload = _load_json_object(baseline_path, error_prefix="baseline")
    except Exception as exc:
        print(f"[agent-factory-plan-perf-baseline-refresh] input_error={exc}", file=sys.stderr)
        return 2

    raw_profiles = baseline_payload.get("profiles")
    if not isinstance(raw_profiles, dict) or not raw_profiles:
        print(
            "[agent-factory-plan-perf-baseline-refresh] input_error=baseline.profiles must be non-empty object",
            file=sys.stderr,
        )
        return 2

    baseline_profiles_index: dict[str, tuple[str, dict[str, Any]]] = {}
    for raw_name, raw_payload in raw_profiles.items():
        if not isinstance(raw_payload, dict):
            continue
        normalized_name = str(raw_name or "").strip().lower()
        if not normalized_name:
            continue
        baseline_profiles_index[normalized_name] = (str(raw_name), raw_payload)
    if not baseline_profiles_index:
        print(
            "[agent-factory-plan-perf-baseline-refresh] input_error=no valid profiles in baseline",
            file=sys.stderr,
        )
        return 2

    profile_results: list[dict[str, Any]] = []
    fail_count = 0
    warn_count = 0
    updated_baseline = json.loads(json.dumps(baseline_payload))
    updated_profiles = updated_baseline.get("profiles")
    if not isinstance(updated_profiles, dict):
        updated_profiles = {}
        updated_baseline["profiles"] = updated_profiles

    for normalized_profile, report_path in sorted(report_mapping.items()):
        if normalized_profile not in baseline_profiles_index:
            fail_count += 1
            profile_results.append(
                {
                    "profile": normalized_profile,
                    "status": "fail",
                    "notes": [f"profile_missing_in_baseline:{normalized_profile}"],
                    "report_path": str(report_path),
                }
            )
            continue
        baseline_profile_name, baseline_profile_payload = baseline_profiles_index[normalized_profile]
        notes: list[str] = []
        status = "pass"
        try:
            report_payload = _load_json_object(report_path, error_prefix=f"report[{normalized_profile}]")
        except Exception as exc:
            fail_count += 1
            profile_results.append(
                {
                    "profile": baseline_profile_name,
                    "status": "fail",
                    "notes": [f"report_load_error:{exc}"],
                    "report_path": str(report_path),
                }
            )
            continue

        summary = report_payload.get("summary") if isinstance(report_payload.get("summary"), dict) else {}
        report_thresholds = report_payload.get("thresholds") if isinstance(report_payload.get("thresholds"), dict) else {}
        report_status = str(summary.get("status") or "").strip().lower()
        if report_status and report_status != "pass":
            status = "warn"
            notes.append(f"report_status={report_status}")

        observed_p95_ms = _safe_float(summary.get("p95_latency_ms"), default=0.0)
        observed_error_rate_pct = _safe_float(summary.get("error_rate_pct"), default=100.0)
        baseline_p95_ms = _safe_float(
            baseline_profile_payload.get("max_p95_latency_ms"),
            default=_safe_float(report_thresholds.get("max_p95_latency_ms"), default=0.0),
        )
        baseline_error_rate_pct = _safe_float(
            baseline_profile_payload.get("max_error_rate_pct"),
            default=_safe_float(report_thresholds.get("max_error_rate_pct"), default=0.0),
        )
        suggested_p95_ms = _compute_suggested_p95(
            observed_p95_ms=observed_p95_ms,
            current_threshold_ms=baseline_p95_ms,
            headroom_pct=float(args.headroom_pct),
            min_headroom_ms=float(args.min_headroom_ms),
            max_increase_pct=float(args.max_increase_pct),
            max_decrease_pct=float(args.max_decrease_pct),
        )
        suggested_error_rate_pct = baseline_error_rate_pct

        if observed_error_rate_pct > baseline_error_rate_pct:
            status = "warn"
            notes.append(
                f"observed_error_rate_pct={observed_error_rate_pct:.4f} exceeds baseline={baseline_error_rate_pct:.4f}"
            )

        p95_delta_ms = suggested_p95_ms - baseline_p95_ms
        p95_delta_pct = ((p95_delta_ms / baseline_p95_ms) * 100.0) if baseline_p95_ms > 0 else 0.0
        if abs(p95_delta_pct) >= 15.0:
            notes.append(f"p95_threshold_shift_pct={p95_delta_pct:.3f}")

        if status == "warn":
            warn_count += 1
        profile_results.append(
            {
                "profile": baseline_profile_name,
                "status": status,
                "report_path": str(report_path),
                "observed": {
                    "p95_latency_ms": round(observed_p95_ms, 4),
                    "error_rate_pct": round(observed_error_rate_pct, 4),
                },
                "baseline_thresholds": {
                    "max_p95_latency_ms": round(baseline_p95_ms, 4),
                    "max_error_rate_pct": round(baseline_error_rate_pct, 4),
                },
                "suggested_thresholds": {
                    "max_p95_latency_ms": round(suggested_p95_ms, 4),
                    "max_error_rate_pct": round(suggested_error_rate_pct, 4),
                },
                "drift": {
                    "p95_threshold_delta_ms": round(p95_delta_ms, 4),
                    "p95_threshold_delta_pct": round(p95_delta_pct, 4),
                },
                "notes": notes,
            }
        )
        updated_profile_payload = updated_profiles.get(baseline_profile_name)
        if isinstance(updated_profile_payload, dict):
            updated_profile_payload["max_p95_latency_ms"] = round(suggested_p95_ms, 4)
            updated_profile_payload["max_error_rate_pct"] = round(suggested_error_rate_pct, 4)

    summary_status = "pass"
    if fail_count > 0:
        summary_status = "fail"
    elif warn_count > 0:
        summary_status = "warn"
    refresh_report = {
        "generated_at": _utc_now_iso(),
        "suite": "agent_factory_plan_perf_baseline_refresh_v1",
        "baseline": {
            "path": str(baseline_path),
            "suite": str(baseline_payload.get("suite") or ""),
            "generated_at": str(baseline_payload.get("generated_at") or ""),
        },
        "policy": {
            "headroom_pct": float(args.headroom_pct),
            "min_headroom_ms": float(args.min_headroom_ms),
            "max_increase_pct": float(args.max_increase_pct),
            "max_decrease_pct": float(args.max_decrease_pct),
        },
        "summary": {
            "status": summary_status,
            "profiles_total": len(profile_results),
            "profiles_warn": int(warn_count),
            "profiles_fail": int(fail_count),
        },
        "profiles": profile_results,
    }
    _write_json(output_path, refresh_report)

    if updated_baseline_path is not None:
        updated_baseline["generated_at"] = _utc_now_iso()
        updated_baseline["derived_from"] = {
            "source_baseline_path": str(baseline_path),
            "refresh_report_path": str(output_path),
        }
        _write_json(updated_baseline_path, updated_baseline)

    strict_failed = bool(args.strict and summary_status in {"warn", "fail"})
    if strict_failed:
        print(
            "[agent-factory-plan-perf-baseline-refresh] FAILED "
            f"status={summary_status} warn={warn_count} fail={fail_count} report={output_path}"
        )
        return 1

    print(
        "[agent-factory-plan-perf-baseline-refresh] OK "
        f"status={summary_status} profiles={len(profile_results)} report={output_path}"
    )
    if updated_baseline_path is not None:
        print(f"[agent-factory-plan-perf-baseline-refresh] suggested_baseline={updated_baseline_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
