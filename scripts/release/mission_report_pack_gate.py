#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate mission success/recovery report pack contract "
            "(suite/schema/scope/class-breakdown/KPI completeness)."
        )
    )
    parser.add_argument(
        "--report",
        default="artifacts/mission-success-recovery-report.json",
        help="Path to mission success/recovery report JSON.",
    )
    parser.add_argument(
        "--expected-scope",
        default="auto",
        choices=("auto", "release", "nightly"),
        help="Expected scope; `auto` accepts report-provided scope.",
    )
    parser.add_argument(
        "--min-checks-total",
        type=int,
        default=1,
        help="Minimum required summary.checks_total.",
    )
    parser.add_argument(
        "--allow-failed-status",
        action="store_true",
        help="Do not fail when report summary/class statuses are `fail`.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional JSON report output path.",
    )
    return parser.parse_args()


def _resolve_path(project_root: Path, raw: str) -> Path:
    path = Path(str(raw).strip())
    if not path.is_absolute():
        path = project_root / path
    return path


def _load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("JSON payload must be object")
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _required_classes(scope: str) -> set[str]:
    if scope == "nightly":
        return {"nightly_reliability", "user_flow", "adoption_growth"}
    return {
        "mission_execution",
        "recovery",
        "quality",
        "runtime_qos",
        "distribution",
        "user_flow",
        "adoption_growth",
    }


def _required_kpis(scope: str) -> set[str]:
    if scope == "nightly":
        return {
            "nightly_success_rate_pct",
            "nightly_burn_rate_gate_passed",
            "nightly_breaker_soak_gate_passed",
            "nightly_autonomy_breaker_gate_passed",
            "nightly_autonomy_breaker_domains_contract_passed",
            "nightly_adoption_trend_gate_passed",
            "journey_success_rate_pct",
            "journey_plan_to_execute_conversion_rate_pct",
            "news_citation_coverage_rate",
            "news_mission_success_rate_pct",
        }
    return {
        "mission_success_rate_pct",
        "recovery_pass_rate_pct",
        "release_quality_score_pct",
        "distribution_score_pct",
        "journey_success_rate_pct",
        "journey_plan_to_execute_conversion_rate_pct",
        "autonomy_breaker_gate_passed",
        "autonomy_breaker_domains_contract_passed",
        "adoption_trend_gate_passed",
        "news_citation_coverage_rate",
        "news_mission_success_rate_pct",
    }


def main() -> int:
    args = _parse_args()
    project_root = Path(__file__).resolve().parents[2]
    report_path = _resolve_path(project_root, str(args.report))

    checks: list[dict[str, Any]] = []

    def add_check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    if not report_path.exists():
        add_check("report_exists", False, f"missing: {report_path}")
        payload: dict[str, Any] = {}
    else:
        add_check("report_exists", True, str(report_path))
        try:
            payload = _load_json_object(report_path)
        except Exception as exc:
            payload = {}
            add_check("report_json_valid", False, f"{type(exc).__name__}: {exc}")

    suite = str(payload.get("suite") or "").strip()
    add_check("suite_id", suite == "mission_success_recovery_report_pack_v2", f"suite={suite}")

    schema_version = int(payload.get("schema_version") or 0)
    add_check("schema_version", schema_version == 2, f"schema_version={schema_version}")

    scope = str(payload.get("scope") or "").strip().lower()
    add_check("scope_present", scope in {"release", "nightly"}, f"scope={scope}")
    expected_scope = str(args.expected_scope or "auto").strip().lower()
    if expected_scope != "auto":
        add_check("scope_expected", scope == expected_scope, f"scope={scope} expected={expected_scope}")

    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    checks_total = int(summary.get("checks_total") or 0)
    checks_failed = int(summary.get("checks_failed") or 0)
    summary_status = str(summary.get("status") or "").strip().lower()
    add_check(
        "summary_checks_total",
        checks_total >= max(1, int(args.min_checks_total)),
        f"checks_total={checks_total}",
    )
    add_check(
        "summary_status_shape",
        summary_status in {"pass", "fail"},
        f"summary_status={summary_status}",
    )
    if not bool(args.allow_failed_status):
        add_check("summary_status_pass", summary_status == "pass", f"summary_status={summary_status}")
        add_check("summary_checks_failed_zero", checks_failed == 0, f"checks_failed={checks_failed}")

    class_order = payload.get("class_order") if isinstance(payload.get("class_order"), list) else []
    class_order_set = {str(item).strip() for item in class_order if str(item).strip()}
    required_classes = _required_classes(scope if scope in {"release", "nightly"} else "release")
    add_check(
        "class_order_required",
        required_classes.issubset(class_order_set),
        f"required={sorted(required_classes)} present={sorted(class_order_set)}",
    )

    class_breakdown = payload.get("class_breakdown") if isinstance(payload.get("class_breakdown"), dict) else {}
    add_check("class_breakdown_present", bool(class_breakdown), f"classes={sorted(class_breakdown.keys())}")

    for class_name in sorted(required_classes):
        row = class_breakdown.get(class_name) if isinstance(class_breakdown.get(class_name), dict) else {}
        row_status = str((row or {}).get("status") or "").strip().lower()
        row_checks_total = int((row or {}).get("checks_total") or 0)
        row_checks_failed = int((row or {}).get("checks_failed") or 0)
        row_kpis = (row or {}).get("kpis") if isinstance((row or {}).get("kpis"), dict) else {}
        add_check(
            f"class_{class_name}_exists",
            bool(row),
            f"class={class_name}",
        )
        add_check(
            f"class_{class_name}_checks_total",
            row_checks_total >= 1,
            f"checks_total={row_checks_total}",
        )
        add_check(
            f"class_{class_name}_has_kpis",
            bool(row_kpis),
            f"kpi_count={len(row_kpis)}",
        )
        if not bool(args.allow_failed_status):
            add_check(
                f"class_{class_name}_status_pass",
                row_status == "pass",
                f"status={row_status}",
            )
            add_check(
                f"class_{class_name}_checks_failed_zero",
                row_checks_failed == 0,
                f"checks_failed={row_checks_failed}",
            )

    kpis = payload.get("kpis") if isinstance(payload.get("kpis"), dict) else {}
    add_check("kpis_present", bool(kpis), f"kpi_count={len(kpis)}")

    required_kpis = _required_kpis(scope if scope in {"release", "nightly"} else "release")
    missing_kpis = sorted(key for key in required_kpis if key not in kpis)
    add_check(
        "required_kpis_present",
        not missing_kpis,
        f"missing={missing_kpis}",
    )

    failed = [item for item in checks if not bool(item.get("ok"))]
    report = {
        "generated_at": _utc_now_iso(),
        "suite": "mission_report_pack_gate_v1",
        "summary": {
            "status": "pass" if not failed else "fail",
            "checks_total": len(checks),
            "checks_failed": len(failed),
            "report": str(report_path),
            "scope": scope,
        },
        "checks": checks,
    }

    output_raw = str(args.output or "").strip()
    if output_raw:
        output_path = _resolve_path(project_root, output_raw)
        _write_json(output_path, report)

    if failed:
        print("[mission-report-pack-gate] FAILED")
        for item in failed:
            print(f"- {item.get('name')}: {item.get('detail')}")
        return 1

    print(f"[mission-report-pack-gate] OK checks={len(checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
