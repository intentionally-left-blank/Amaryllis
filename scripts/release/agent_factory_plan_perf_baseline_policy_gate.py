#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any


EXPECTED_BASELINE_SUITE = "agent_factory_plan_perf_envelope_v1"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate policy constraints for Agent Factory plan perf baseline updates "
            "(drift limits + required manual-approval metadata)."
        )
    )
    parser.add_argument(
        "--current-baseline",
        default="eval/baselines/quality/agent_factory_plan_perf_envelope.json",
        help="Path to proposed/current baseline envelope JSON.",
    )
    parser.add_argument(
        "--reference-baseline",
        default="",
        help="Path to reference baseline envelope JSON (usually target branch baseline).",
    )
    parser.add_argument(
        "--allow-missing-reference",
        action="store_true",
        help="Allow missing --reference-baseline (treat as bootstrap baseline).",
    )
    parser.add_argument(
        "--max-auto-increase-pct",
        type=float,
        default=15.0,
        help="Maximum automatic increase percent per profile without manual approval metadata.",
    )
    parser.add_argument(
        "--max-auto-decrease-pct",
        type=float,
        default=20.0,
        help="Maximum automatic decrease percent per profile without manual approval metadata.",
    )
    parser.add_argument(
        "--output",
        default="artifacts/agent-factory-plan-perf-baseline-policy-gate-report.json",
        help="Output policy gate report JSON path.",
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


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_json_object(path: Path, *, error_prefix: str) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"{error_prefix}: missing file: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{error_prefix}: JSON root must be object: {path}")
    return payload


def _extract_profiles(payload: dict[str, Any]) -> dict[str, tuple[str, dict[str, Any]]]:
    raw_profiles = payload.get("profiles")
    if not isinstance(raw_profiles, dict):
        return {}
    index: dict[str, tuple[str, dict[str, Any]]] = {}
    for raw_name, raw_profile in raw_profiles.items():
        if not isinstance(raw_profile, dict):
            continue
        normalized = str(raw_name or "").strip().lower()
        if not normalized:
            continue
        index[normalized] = (str(raw_name), raw_profile)
    return index


def _required_text_check(
    checks: list[dict[str, Any]],
    *,
    field: str,
    value: Any,
) -> None:
    normalized = str(value or "").strip()
    checks.append(
        {
            "name": f"change_control.{field}",
            "passed": bool(normalized),
            "detail": normalized if normalized else "missing",
        }
    )


def _validate_change_control(
    *,
    change_control: dict[str, Any] | None,
    baseline_changed: bool,
    requires_manual_profiles: list[str],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if not baseline_changed:
        checks.append(
            {
                "name": "baseline_changed",
                "passed": True,
                "detail": "no profile threshold drift detected",
            }
        )
        return checks

    if not isinstance(change_control, dict):
        return [
            {
                "name": "change_control",
                "passed": False,
                "detail": "missing object for changed baseline",
            }
        ]

    _required_text_check(checks, field="change_id", value=change_control.get("change_id"))
    _required_text_check(checks, field="reason", value=change_control.get("reason"))
    _required_text_check(checks, field="ticket", value=change_control.get("ticket"))
    _required_text_check(checks, field="requested_by", value=change_control.get("requested_by"))

    requires_manual = bool(requires_manual_profiles)
    manual_approval = bool(change_control.get("manual_approval"))
    checks.append(
        {
            "name": "change_control.manual_approval",
            "passed": (manual_approval if requires_manual else True),
            "detail": f"value={manual_approval} required={requires_manual}",
        }
    )

    approved_by_raw = change_control.get("approved_by")
    approved_by: list[str] = []
    if isinstance(approved_by_raw, list):
        for item in approved_by_raw:
            normalized = str(item or "").strip()
            if normalized:
                approved_by.append(normalized)
    checks.append(
        {
            "name": "change_control.approved_by",
            "passed": (len(approved_by) > 0 if requires_manual else True),
            "detail": ",".join(approved_by) if approved_by else "missing",
        }
    )

    approved_at = str(change_control.get("approved_at") or "").strip()
    checks.append(
        {
            "name": "change_control.approved_at",
            "passed": bool(approved_at) if requires_manual else True,
            "detail": approved_at if approved_at else "missing",
        }
    )

    scope_raw = change_control.get("approval_scope")
    scope: list[str] = []
    if isinstance(scope_raw, list):
        for item in scope_raw:
            normalized = str(item or "").strip().lower()
            if normalized:
                scope.append(normalized)
    if requires_manual and scope:
        missing_scope = sorted({profile.lower() for profile in requires_manual_profiles} - set(scope))
        checks.append(
            {
                "name": "change_control.approval_scope",
                "passed": len(missing_scope) == 0,
                "detail": "ok" if not missing_scope else f"missing_profiles={','.join(missing_scope)}",
            }
        )
    else:
        checks.append(
            {
                "name": "change_control.approval_scope",
                "passed": True,
                "detail": "not_enforced",
            }
        )

    return checks


def main() -> int:
    args = _parse_args()
    project_root = Path(__file__).resolve().parents[2]
    current_baseline_path = _resolve_path(project_root, str(args.current_baseline))
    output_path = _resolve_path(project_root, str(args.output))

    if float(args.max_auto_increase_pct) < 0:
        print("[agent-factory-plan-perf-baseline-policy-gate] --max-auto-increase-pct must be >= 0", file=sys.stderr)
        return 2
    if float(args.max_auto_decrease_pct) < 0:
        print("[agent-factory-plan-perf-baseline-policy-gate] --max-auto-decrease-pct must be >= 0", file=sys.stderr)
        return 2

    reference_baseline_raw = str(args.reference_baseline or "").strip()
    reference_baseline_path: Path | None = None
    if reference_baseline_raw:
        reference_baseline_path = _resolve_path(project_root, reference_baseline_raw)

    try:
        current_baseline = _load_json_object(current_baseline_path, error_prefix="current_baseline")
        if reference_baseline_path is None:
            if args.allow_missing_reference:
                reference_baseline: dict[str, Any] = {}
            else:
                raise ValueError("--reference-baseline is required unless --allow-missing-reference is set")
        else:
            reference_baseline = _load_json_object(reference_baseline_path, error_prefix="reference_baseline")
    except Exception as exc:
        print(f"[agent-factory-plan-perf-baseline-policy-gate] input_error={exc}", file=sys.stderr)
        return 2

    failures: list[str] = []
    current_suite = str(current_baseline.get("suite") or "").strip()
    reference_suite = str(reference_baseline.get("suite") or "").strip()
    if current_suite and current_suite != EXPECTED_BASELINE_SUITE:
        failures.append(f"unexpected_current_suite={current_suite}")
    if reference_suite and reference_suite != EXPECTED_BASELINE_SUITE:
        failures.append(f"unexpected_reference_suite={reference_suite}")

    current_profiles = _extract_profiles(current_baseline)
    reference_profiles = _extract_profiles(reference_baseline)
    if not current_profiles:
        failures.append("current_baseline_profiles_missing")

    profile_results: list[dict[str, Any]] = []
    requires_manual_profiles: list[str] = []
    for normalized_name in sorted(set(current_profiles.keys()) | set(reference_profiles.keys())):
        current_entry = current_profiles.get(normalized_name)
        reference_entry = reference_profiles.get(normalized_name)
        display_name = (
            current_entry[0]
            if current_entry is not None
            else reference_entry[0]
            if reference_entry is not None
            else normalized_name
        )

        result: dict[str, Any] = {
            "profile": display_name,
            "status": "pass",
            "requires_manual_approval": False,
            "approval_reasons": [],
            "drift": {},
        }
        approval_reasons: list[str] = []

        if reference_entry is None:
            approval_reasons.append("profile_added")
        elif current_entry is None:
            approval_reasons.append("profile_removed")
        else:
            _, current_profile = current_entry
            _, reference_profile = reference_entry
            current_p95 = _safe_float(current_profile.get("max_p95_latency_ms"), default=-1.0)
            reference_p95 = _safe_float(reference_profile.get("max_p95_latency_ms"), default=-1.0)
            current_error = _safe_float(current_profile.get("max_error_rate_pct"), default=-1.0)
            reference_error = _safe_float(reference_profile.get("max_error_rate_pct"), default=-1.0)

            result["thresholds"] = {
                "reference": {
                    "max_p95_latency_ms": round(reference_p95, 4),
                    "max_error_rate_pct": round(reference_error, 4),
                },
                "current": {
                    "max_p95_latency_ms": round(current_p95, 4),
                    "max_error_rate_pct": round(current_error, 4),
                },
            }

            if reference_p95 <= 0 or current_p95 <= 0:
                result["status"] = "fail"
                failures.append(f"profile={display_name} invalid_p95_thresholds")
            else:
                delta_ms = current_p95 - reference_p95
                delta_pct = (delta_ms / reference_p95) * 100.0
                result["drift"] = {
                    "p95_threshold_delta_ms": round(delta_ms, 4),
                    "p95_threshold_delta_pct": round(delta_pct, 4),
                    "error_rate_delta_pct": round(current_error - reference_error, 4),
                }
                if delta_pct > float(args.max_auto_increase_pct):
                    approval_reasons.append(
                        "p95_increase_exceeds_auto_limit"
                        f"(delta_pct={delta_pct:.3f},limit={float(args.max_auto_increase_pct):.3f})"
                    )
                if delta_pct < -float(args.max_auto_decrease_pct):
                    approval_reasons.append(
                        "p95_decrease_exceeds_auto_limit"
                        f"(delta_pct={delta_pct:.3f},limit={float(args.max_auto_decrease_pct):.3f})"
                    )

            if current_error < 0 or reference_error < 0:
                result["status"] = "fail"
                failures.append(f"profile={display_name} invalid_error_rate_thresholds")
            elif abs(current_error - reference_error) > 1e-9:
                approval_reasons.append(
                    "error_rate_threshold_changed"
                    f"(delta_pct={(current_error - reference_error):.4f})"
                )

        if approval_reasons:
            result["status"] = "manual_approval_required"
            result["requires_manual_approval"] = True
            result["approval_reasons"] = approval_reasons
            requires_manual_profiles.append(display_name)
        profile_results.append(result)

    baseline_changed = current_baseline != reference_baseline
    metadata_checks = _validate_change_control(
        change_control=current_baseline.get("change_control")
        if isinstance(current_baseline.get("change_control"), dict)
        else None,
        baseline_changed=baseline_changed,
        requires_manual_profiles=requires_manual_profiles,
    )
    for check in metadata_checks:
        if not bool(check.get("passed")):
            failures.append(str(check.get("name") or "metadata_check_failed"))

    status = "pass" if not failures else "fail"
    report = {
        "generated_at": _utc_now_iso(),
        "suite": "agent_factory_plan_perf_baseline_policy_gate_v1",
        "inputs": {
            "current_baseline_path": str(current_baseline_path),
            "reference_baseline_path": str(reference_baseline_path) if reference_baseline_path is not None else "",
            "reference_loaded": bool(reference_baseline),
        },
        "policy": {
            "max_auto_increase_pct": float(args.max_auto_increase_pct),
            "max_auto_decrease_pct": float(args.max_auto_decrease_pct),
            "expected_baseline_suite": EXPECTED_BASELINE_SUITE,
        },
        "summary": {
            "status": status,
            "baseline_changed": baseline_changed,
            "profiles_total": len(profile_results),
            "profiles_requiring_manual_approval": len(requires_manual_profiles),
            "checks_failed": len(failures),
        },
        "profiles": profile_results,
        "metadata_checks": metadata_checks,
        "failures": sorted(set(failures)),
    }
    _write_json(output_path, report)

    if status != "pass":
        print(
            "[agent-factory-plan-perf-baseline-policy-gate] FAILED "
            f"checks_failed={len(set(failures))} report={output_path}"
        )
        return 1

    print(
        "[agent-factory-plan-perf-baseline-policy-gate] OK "
        f"profiles={len(profile_results)} manual_required={len(requires_manual_profiles)} report={output_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
