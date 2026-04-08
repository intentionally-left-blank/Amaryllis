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
            "Validate mission template contract quality: schema consistency and "
            "deterministic replay snapshots for template apply -> mission plan flow."
        )
    )
    parser.add_argument(
        "--fixture",
        default="eval/fixtures/agent_templates/template_contract_cases.json",
        help="Path to template contract fixture cases.",
    )
    parser.add_argument(
        "--snapshot",
        default="eval/fixtures/agent_templates/template_contract_snapshot.json",
        help="Path to canonical expected template replay snapshot.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional report output path.",
    )
    parser.add_argument(
        "--update-snapshot",
        action="store_true",
        help="Update expected snapshot file with current canonical payload.",
    )
    return parser.parse_args()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_path(repo_root: Path, raw: str) -> Path:
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_json_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON payload must be object: {path}")
    return payload


def _as_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _parse_now_utc(value: Any) -> datetime:
    raw = str(value or "").strip()
    if not raw:
        return datetime(2026, 1, 5, 8, 0, tzinfo=timezone.utc)
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _default_simulation() -> dict[str, Any]:
    return {"risk_summary": {"overall_risk_level": "medium"}}


def _normalize_snapshot_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _normalize_snapshot_value(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_normalize_snapshot_value(item) for item in value]
    return value


def _validate_contract_fields(
    *,
    case_id: str,
    template: dict[str, Any],
    resolved: dict[str, Any],
    mission_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    mismatches: list[dict[str, Any]] = []

    def require_non_empty(path: str, value: Any) -> None:
        if str(value or "").strip() == "":
            mismatches.append(
                {
                    "field": path,
                    "expected": "non-empty",
                    "actual": value,
                }
            )

    for field in ("id", "catalog_version", "lane", "name", "description"):
        require_non_empty(f"template.{field}", template.get(field))
    risk_tags = template.get("risk_tags")
    if not isinstance(risk_tags, list):
        mismatches.append(
            {
                "field": "template.risk_tags",
                "expected": "list",
                "actual": str(type(risk_tags)),
            }
        )

    require_non_empty("resolved.message", resolved.get("message"))
    require_non_empty("resolved.cadence_profile", resolved.get("cadence_profile"))

    mission_policy = resolved.get("mission_policy")
    if not isinstance(mission_policy, dict):
        mismatches.append(
            {
                "field": "resolved.mission_policy",
                "expected": "dict",
                "actual": str(type(mission_policy)),
            }
        )
    else:
        require_non_empty("resolved.mission_policy.profile", mission_policy.get("profile"))
        slo = mission_policy.get("slo")
        required_slo_fields = {
            "warning_failures",
            "critical_failures",
            "disable_failures",
            "backoff_base_sec",
            "backoff_max_sec",
            "circuit_failure_threshold",
            "circuit_open_sec",
        }
        if not isinstance(slo, dict):
            mismatches.append(
                {
                    "field": "resolved.mission_policy.slo",
                    "expected": "dict",
                    "actual": str(type(slo)),
                }
            )
        else:
            missing = sorted(item for item in required_slo_fields if item not in slo)
            if missing:
                mismatches.append(
                    {
                        "field": "resolved.mission_policy.slo",
                        "expected": "all required fields",
                        "actual": {"missing": missing},
                    }
                )

    apply_payload = mission_plan.get("apply_payload")
    if not isinstance(apply_payload, dict):
        mismatches.append(
            {
                "field": "mission_plan.apply_payload",
                "expected": "dict",
                "actual": str(type(apply_payload)),
            }
        )
    else:
        require_non_empty("mission_plan.apply_payload.message", apply_payload.get("message"))
        require_non_empty("mission_plan.apply_payload.schedule_type", apply_payload.get("schedule_type"))
        require_non_empty("mission_plan.apply_payload.timezone", apply_payload.get("timezone"))

    risk = mission_plan.get("risk")
    if not isinstance(risk, dict):
        mismatches.append(
            {
                "field": "mission_plan.risk",
                "expected": "dict",
                "actual": str(type(risk)),
            }
        )
    else:
        require_non_empty("mission_plan.risk.overall", risk.get("overall"))

    recommendation = mission_plan.get("recommendation")
    if not isinstance(recommendation, dict):
        mismatches.append(
            {
                "field": "mission_plan.recommendation",
                "expected": "dict",
                "actual": str(type(recommendation)),
            }
        )
    else:
        checklist = recommendation.get("review_checklist")
        if not isinstance(checklist, list) or not checklist:
            mismatches.append(
                {
                    "field": "mission_plan.recommendation.review_checklist",
                    "expected": "non-empty list",
                    "actual": checklist,
                }
            )

    if mismatches:
        return [
            {
                "case_id": case_id,
                **item,
            }
            for item in mismatches
        ]
    return []


def _evaluate_case(
    *,
    apply_template_fn: Any,
    build_plan_fn: Any,
    raw_case: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    case_id = str(raw_case.get("id") or "case")
    template_id = str(raw_case.get("template_id") or "").strip()
    if not template_id:
        return (
            {
                "id": case_id,
                "status": "fail",
                "mismatches": [
                    {
                        "field": "template_id",
                        "expected": "non-empty",
                        "actual": template_id,
                    }
                ],
            },
            {},
        )

    overrides = raw_case.get("overrides")
    overrides = overrides if isinstance(overrides, dict) else {}
    simulation = raw_case.get("simulation")
    simulation = simulation if isinstance(simulation, dict) else _default_simulation()
    now_utc = _parse_now_utc(raw_case.get("now_utc"))
    timezone_name = str(raw_case.get("timezone") or "UTC")
    user_id = str(raw_case.get("user_id") or "fixture-user")
    agent_id = str(raw_case.get("agent_id") or f"fixture-{template_id}")
    session_id = raw_case.get("session_id")
    normalized_session_id = str(session_id or "").strip() or None

    resolved = apply_template_fn(
        template_id=template_id,
        message=overrides.get("message"),
        cadence_profile=overrides.get("cadence_profile"),
        start_immediately=overrides.get("start_immediately"),
        schedule_type=overrides.get("schedule_type"),
        schedule=overrides.get("schedule"),
        interval_sec=overrides.get("interval_sec"),
        max_attempts=overrides.get("max_attempts"),
        budget=overrides.get("budget"),
        mission_policy_profile=overrides.get("mission_policy_profile"),
        mission_policy=overrides.get("mission_policy"),
    )
    template = resolved.get("template")
    template = template if isinstance(template, dict) else {}

    mission_plan = build_plan_fn(
        agent_id=agent_id,
        user_id=user_id,
        message=str(resolved.get("message") or ""),
        session_id=normalized_session_id,
        timezone_name=timezone_name,
        cadence_profile=str(resolved.get("cadence_profile") or ""),
        start_immediately=bool(resolved.get("start_immediately")),
        schedule_type=resolved.get("schedule_type"),
        schedule=resolved.get("schedule"),
        interval_sec=_as_int(resolved.get("interval_sec"), default=0) if resolved.get("interval_sec") is not None else None,
        simulation=simulation,
        now_utc=now_utc,
    )

    mismatches = _validate_contract_fields(
        case_id=case_id,
        template=template,
        resolved=resolved,
        mission_plan=mission_plan,
    )

    expected = raw_case.get("expected")
    expected = expected if isinstance(expected, dict) else {}
    if expected:
        expected_lane = str(expected.get("lane") or "").strip()
        if expected_lane and str(template.get("lane") or "").strip() != expected_lane:
            mismatches.append(
                {
                    "case_id": case_id,
                    "field": "template.lane",
                    "expected": expected_lane,
                    "actual": str(template.get("lane") or "").strip(),
                }
            )
        expected_schedule_type = str(expected.get("schedule_type") or "").strip()
        if expected_schedule_type and str(mission_plan.get("schedule_type") or "").strip() != expected_schedule_type:
            mismatches.append(
                {
                    "case_id": case_id,
                    "field": "mission_plan.schedule_type",
                    "expected": expected_schedule_type,
                    "actual": str(mission_plan.get("schedule_type") or "").strip(),
                }
            )

    canonical = {
        "template": _normalize_snapshot_value(template),
        "resolved": _normalize_snapshot_value(
            {
                "message": str(resolved.get("message") or ""),
                "cadence_profile": str(resolved.get("cadence_profile") or ""),
                "start_immediately": bool(resolved.get("start_immediately")),
                "schedule_type": str(resolved.get("schedule_type") or ""),
                "schedule": resolved.get("schedule"),
                "interval_sec": resolved.get("interval_sec"),
                "max_attempts": resolved.get("max_attempts"),
                "budget": resolved.get("budget"),
                "mission_policy": resolved.get("mission_policy"),
            }
        ),
        "mission_plan": _normalize_snapshot_value(
            {
                "schedule_type": str(mission_plan.get("schedule_type") or ""),
                "schedule": mission_plan.get("schedule"),
                "interval_sec": mission_plan.get("interval_sec"),
                "next_run_at": mission_plan.get("next_run_at"),
                "risk": mission_plan.get("risk"),
                "recommendation": mission_plan.get("recommendation"),
                "apply_payload": mission_plan.get("apply_payload"),
            }
        ),
    }
    return (
        {
            "id": case_id,
            "status": "pass" if not mismatches else "fail",
            "mismatches": mismatches,
        },
        canonical,
    )


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    fixture_path = _resolve_path(repo_root, str(args.fixture))
    snapshot_path = _resolve_path(repo_root, str(args.snapshot))

    try:
        from automation.mission_planner import (  # noqa: PLC0415
            apply_mission_template,
            build_mission_plan,
            mission_template_catalog,
        )
    except Exception as exc:
        print(f"[agent-template-contract-gate] FAILED import_error={exc}")
        return 2

    if not fixture_path.exists():
        print(f"[agent-template-contract-gate] FAILED missing_fixture={fixture_path}")
        return 2

    try:
        fixture_payload = _load_json_object(fixture_path)
    except Exception as exc:
        print(f"[agent-template-contract-gate] FAILED fixture_read_error={exc}")
        return 2

    raw_cases = fixture_payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        print("[agent-template-contract-gate] FAILED empty_or_invalid_cases")
        return 2

    catalog = mission_template_catalog()
    catalog_version = str(catalog.get("version") or "")
    template_count = int(catalog.get("template_count") or 0)

    case_results: list[dict[str, Any]] = []
    canonical_cases: dict[str, Any] = {}
    for raw_case in raw_cases:
        if not isinstance(raw_case, dict):
            case_results.append(
                {
                    "id": "case",
                    "status": "fail",
                    "mismatches": [
                        {
                            "field": "case",
                            "expected": "dict",
                            "actual": str(type(raw_case)),
                        }
                    ],
                }
            )
            continue
        try:
            result, canonical = _evaluate_case(
                apply_template_fn=apply_mission_template,
                build_plan_fn=build_mission_plan,
                raw_case=raw_case,
            )
        except Exception as exc:
            result = {
                "id": str(raw_case.get("id") or "case"),
                "status": "fail",
                "mismatches": [
                    {
                        "field": "case_execution",
                        "expected": "no exception",
                        "actual": str(exc),
                    }
                ],
            }
            canonical = {}
        case_results.append(result)
        case_id = str(result.get("id") or "case")
        if canonical:
            canonical_cases[case_id] = canonical

    expected_snapshot: dict[str, Any] = {}
    if args.update_snapshot:
        _write_json(
            snapshot_path,
            {
                "version": "agent_template_snapshot_v1",
                "generated_at": _utc_now_iso(),
                "catalog_version": catalog_version,
                "cases": canonical_cases,
            },
        )
    else:
        if not snapshot_path.exists():
            print(f"[agent-template-contract-gate] FAILED missing_snapshot={snapshot_path}")
            return 2
        try:
            expected_snapshot = _load_json_object(snapshot_path)
        except Exception as exc:
            print(f"[agent-template-contract-gate] FAILED snapshot_read_error={exc}")
            return 2

    snapshot_drift_cases: list[str] = []
    if not args.update_snapshot:
        expected_cases = expected_snapshot.get("cases")
        expected_cases = expected_cases if isinstance(expected_cases, dict) else {}
        for case_id, canonical in canonical_cases.items():
            if case_id not in expected_cases:
                snapshot_drift_cases.append(case_id)
                continue
            expected_case = _normalize_snapshot_value(expected_cases.get(case_id))
            if _normalize_snapshot_value(canonical) != expected_case:
                snapshot_drift_cases.append(case_id)

    failed_cases = [item for item in case_results if str(item.get("status") or "") != "pass"]
    summary_status = "pass"
    if failed_cases or snapshot_drift_cases:
        summary_status = "fail"

    catalog_failures: list[str] = []
    if catalog_version != "mission_template_catalog_v1":
        catalog_failures.append(f"catalog_version={catalog_version}")
    if template_count < len(raw_cases):
        catalog_failures.append(
            f"template_count={template_count}<fixture_cases={len(raw_cases)}"
        )
    if catalog_failures:
        summary_status = "fail"

    report = {
        "generated_at": _utc_now_iso(),
        "suite": "agent_template_contract_gate_v1",
        "fixture": str(fixture_path),
        "snapshot": str(snapshot_path),
        "summary": {
            "status": summary_status,
            "cases_total": len(case_results),
            "cases_failed": len(failed_cases),
            "snapshot_drift_cases": snapshot_drift_cases,
            "catalog_version": catalog_version,
            "template_count": template_count,
            "catalog_failures": catalog_failures,
            "snapshot_updated": bool(args.update_snapshot),
        },
        "cases": case_results,
    }

    output_raw = str(args.output or "").strip()
    if output_raw:
        output_path = _resolve_path(repo_root, output_raw)
        _write_json(output_path, report)

    if summary_status == "pass":
        print(
            "[agent-template-contract-gate] OK "
            f"cases={len(case_results)} snapshot_updated={bool(args.update_snapshot)}"
        )
        return 0

    failed_case_ids = ",".join(str(item.get("id") or "case") for item in failed_cases[:20])
    drift_case_ids = ",".join(snapshot_drift_cases[:20])
    print(
        "[agent-template-contract-gate] FAILED "
        f"failed_cases={failed_case_ids or 'none'} snapshot_drift={drift_case_ids or 'none'} "
        f"catalog_failures={','.join(catalog_failures) or 'none'}"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
