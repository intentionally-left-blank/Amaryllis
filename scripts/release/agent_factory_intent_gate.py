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
            "Validate Agent Factory intent inference against fixture cases "
            "(kind/source policy/schedule and explainability hints)."
        )
    )
    parser.add_argument(
        "--fixture",
        default="eval/fixtures/agent_factory/intent_inference_cases.json",
        help="Path to intent inference fixture suite.",
    )
    parser.add_argument(
        "--min-pass-rate",
        type=float,
        default=1.0,
        help="Minimum pass rate required in [0, 1].",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional JSON report path.",
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


def _safe_int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _evaluate_case(
    *,
    infer_fn: Any,
    raw_case: dict[str, Any],
) -> dict[str, Any]:
    case_id = str(raw_case.get("id") or "case")
    request = str(raw_case.get("request") or "")
    expected = raw_case.get("expected", {})
    if not isinstance(expected, dict):
        expected = {}

    mismatches: list[dict[str, Any]] = []
    if not request.strip():
        return {
            "id": case_id,
            "status": "fail",
            "mismatches": [
                {
                    "field": "request",
                    "expected": "non-empty",
                    "actual": "",
                }
            ],
        }

    spec = infer_fn(request)
    if not isinstance(spec, dict):
        return {
            "id": case_id,
            "status": "fail",
            "mismatches": [
                {
                    "field": "spec",
                    "expected": "dict",
                    "actual": str(type(spec)),
                }
            ],
        }

    def assert_equal(field: str, actual: Any, expected_value: Any) -> None:
        if actual != expected_value:
            mismatches.append(
                {
                    "field": field,
                    "expected": expected_value,
                    "actual": actual,
                }
            )

    expected_kind = str(expected.get("kind") or "")
    if expected_kind:
        assert_equal("kind", str(spec.get("kind") or ""), expected_kind)

    source_policy = spec.get("source_policy")
    if not isinstance(source_policy, dict):
        source_policy = {}
    expected_source_policy_mode = str(expected.get("source_policy_mode") or "")
    if expected_source_policy_mode:
        assert_equal("source_policy.mode", str(source_policy.get("mode") or ""), expected_source_policy_mode)

    reason = spec.get("inference_reason")
    if not isinstance(reason, dict):
        reason = {}
    if expected_kind:
        assert_equal("inference_reason.resolved_kind", str(reason.get("resolved_kind") or ""), expected_kind)
    if "mixed_intent" in expected:
        assert_equal(
            "inference_reason.mixed_intent",
            bool(reason.get("mixed_intent", False)),
            bool(expected.get("mixed_intent")),
        )

    expected_schedule_type = str(expected.get("schedule_type") or "")
    if expected_schedule_type:
        automation = spec.get("automation")
        if not isinstance(automation, dict):
            automation = {}
        assert_equal("automation.schedule_type", str(automation.get("schedule_type") or ""), expected_schedule_type)
        schedule = automation.get("schedule")
        if not isinstance(schedule, dict):
            schedule = {}
        if "interval_hours" in expected:
            assert_equal(
                "automation.schedule.interval_hours",
                _safe_int(schedule.get("interval_hours")),
                _safe_int(expected.get("interval_hours")),
            )
        if "hour" in expected:
            assert_equal(
                "automation.schedule.hour",
                _safe_int(schedule.get("hour")),
                _safe_int(expected.get("hour")),
            )
        if "minute" in expected:
            assert_equal(
                "automation.schedule.minute",
                _safe_int(schedule.get("minute")),
                _safe_int(expected.get("minute")),
            )

    status = "pass" if not mismatches else "fail"
    return {
        "id": case_id,
        "status": status,
        "mismatches": mismatches,
    }


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    fixture_path = _resolve_path(repo_root, args.fixture)
    min_pass_rate = max(0.0, min(float(args.min_pass_rate), 1.0))

    try:
        from agents.factory import infer_agent_spec_from_request  # noqa: PLC0415
    except Exception as exc:
        print(f"[agent-factory-intent-gate] FAILED import_error={exc}")
        return 2

    if not fixture_path.exists():
        print(f"[agent-factory-intent-gate] FAILED missing_fixture={fixture_path}")
        return 2

    try:
        payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[agent-factory-intent-gate] FAILED fixture_read_error={exc}")
        return 2

    raw_cases = payload.get("cases", []) if isinstance(payload, dict) else []
    if not isinstance(raw_cases, list) or not raw_cases:
        print("[agent-factory-intent-gate] FAILED empty_or_invalid_cases")
        return 2

    case_results: list[dict[str, Any]] = []
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
        case_results.append(
            _evaluate_case(
                infer_fn=infer_agent_spec_from_request,
                raw_case=raw_case,
            )
        )

    total = len(case_results)
    failed_cases = [item for item in case_results if str(item.get("status")) != "pass"]
    passed = total - len(failed_cases)
    pass_rate = (float(passed) / float(total)) if total > 0 else 0.0
    status = "pass" if pass_rate >= min_pass_rate and not failed_cases else "fail"

    report = {
        "generated_at": _utc_now_iso(),
        "suite": "agent_factory_intent_gate_v1",
        "fixture": str(fixture_path),
        "summary": {
            "status": status,
            "cases_total": total,
            "cases_passed": passed,
            "cases_failed": len(failed_cases),
            "pass_rate": round(pass_rate, 4),
            "min_pass_rate": round(min_pass_rate, 4),
        },
        "cases": case_results,
    }

    output_raw = str(args.output or "").strip()
    if output_raw:
        output_path = _resolve_path(repo_root, output_raw)
        _write_json(output_path, report)

    if status == "pass":
        print(
            "[agent-factory-intent-gate] OK "
            f"cases={total} passed={passed} pass_rate={pass_rate:.3f}"
        )
        return 0

    failed_ids = ",".join(str(item.get("id") or "case") for item in failed_cases[:20])
    print(
        "[agent-factory-intent-gate] FAILED "
        f"cases={total} passed={passed} pass_rate={pass_rate:.3f} "
        f"failed_cases={failed_ids}"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
