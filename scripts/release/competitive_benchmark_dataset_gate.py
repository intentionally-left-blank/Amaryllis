#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import traceback
from typing import Any


SUITE = "competitive_benchmark_dataset_gate_v1"
DEFAULT_DATASET = "eval/datasets/quality/competitive_benchmark_scenarios_v1.json"
EXPECTED_DATASET_SUITE = "competitive_benchmark_scenarios_v1"
EXPECTED_SCHEMA_VERSION = 1
ALLOWED_LANES = ("create", "schedule", "quality", "recovery")
DEFAULT_REQUIRED_LANES = ("create", "schedule", "quality", "recovery")
BANNED_VENDOR_MARKERS = (
    "openai",
    "chatgpt",
    "anthropic",
    "claude",
    "gemini",
    "grok",
    "cohere",
    "mistral",
    "azure openai",
    "/v1/chat/completions",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate competitor-neutral benchmark scenario dataset contract "
            "(create/schedule/quality/recovery coverage + reproducibility + audit metadata)."
        )
    )
    parser.add_argument(
        "--dataset",
        default=DEFAULT_DATASET,
        help="Path to benchmark scenario dataset JSON.",
    )
    parser.add_argument(
        "--expected-suite",
        default=EXPECTED_DATASET_SUITE,
        help="Expected dataset suite identifier.",
    )
    parser.add_argument(
        "--expected-schema-version",
        type=int,
        default=EXPECTED_SCHEMA_VERSION,
        help="Expected dataset schema_version.",
    )
    parser.add_argument(
        "--require-lane",
        action="append",
        default=list(DEFAULT_REQUIRED_LANES),
        help="Lane that must be present at least once (repeatable).",
    )
    parser.add_argument(
        "--min-scenarios-per-lane",
        type=int,
        default=1,
        help="Minimum number of scenarios required per required lane.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional JSON report output path.",
    )
    return parser.parse_args()


def _resolve_path(repo_root: Path, raw: str) -> Path:
    path = Path(str(raw or "").strip()).expanduser()
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _collect_text_nodes(value: Any, *, path: str = "$") -> list[tuple[str, str]]:
    nodes: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            child_path = f"{path}.{key}"
            nodes.extend(_collect_text_nodes(nested, path=child_path))
        return nodes
    if isinstance(value, list):
        for index, nested in enumerate(value):
            child_path = f"{path}[{index}]"
            nodes.extend(_collect_text_nodes(nested, path=child_path))
        return nodes
    if isinstance(value, str):
        text = value.strip()
        if text:
            nodes.append((path, text))
    return nodes


def _validate_required_string(
    scenario: dict[str, Any],
    *,
    key: str,
    scenario_id: str,
    errors: list[str],
) -> str:
    value = str(scenario.get(key) or "").strip()
    if not value:
        errors.append(f"{scenario_id}:{key}_missing")
    return value


def _safe_int(value: Any, default: int = -1) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _validate_scenario(
    *,
    scenario: Any,
    index: int,
    seen_ids: set[str],
) -> tuple[dict[str, Any], list[str], str | None, int]:
    scenario_report: dict[str, Any] = {
        "index": index,
        "id": f"scenario_{index}",
        "status": "fail",
        "errors": [],
        "vendor_neutrality_violations": [],
    }
    errors: list[str] = []

    if not isinstance(scenario, dict):
        errors.append("scenario_not_object")
        scenario_report["errors"] = errors
        return scenario_report, errors, None, 0

    scenario_id = _validate_required_string(scenario, key="id", scenario_id=f"scenario_{index}", errors=errors)
    if not scenario_id:
        scenario_id = f"scenario_{index}"
    scenario_report["id"] = scenario_id

    lane = _validate_required_string(scenario, key="lane", scenario_id=scenario_id, errors=errors).lower()
    if lane and lane not in ALLOWED_LANES:
        errors.append(f"{scenario_id}:lane_unsupported:{lane}")
    if scenario_id in seen_ids:
        errors.append(f"{scenario_id}:duplicate_id")
    seen_ids.add(scenario_id)

    _validate_required_string(scenario, key="title", scenario_id=scenario_id, errors=errors)
    _validate_required_string(scenario, key="objective", scenario_id=scenario_id, errors=errors)
    _validate_required_string(scenario, key="prompt", scenario_id=scenario_id, errors=errors)

    expected = scenario.get("expected_outcomes")
    if not isinstance(expected, dict):
        errors.append(f"{scenario_id}:expected_outcomes_missing")
    else:
        checks = expected.get("checks")
        if not isinstance(checks, list) or not [str(item).strip() for item in checks if str(item).strip()]:
            errors.append(f"{scenario_id}:expected_outcomes.checks_missing")
        kpi_targets = expected.get("kpi_targets")
        if not isinstance(kpi_targets, dict) or not kpi_targets:
            errors.append(f"{scenario_id}:expected_outcomes.kpi_targets_missing")

    evidence = scenario.get("evidence")
    if not isinstance(evidence, dict):
        errors.append(f"{scenario_id}:evidence_missing")
    else:
        must_capture = evidence.get("must_capture")
        if not isinstance(must_capture, list) or not [str(item).strip() for item in must_capture if str(item).strip()]:
            errors.append(f"{scenario_id}:evidence.must_capture_missing")

    reproducibility = scenario.get("reproducibility")
    if not isinstance(reproducibility, dict):
        errors.append(f"{scenario_id}:reproducibility_missing")
    else:
        seed = str(reproducibility.get("seed") or "").strip()
        max_retries = _safe_int(reproducibility.get("max_retries"), -1)
        replay_window_sec = _safe_int(reproducibility.get("replay_window_sec"), -1)
        if not seed:
            errors.append(f"{scenario_id}:reproducibility.seed_missing")
        if max_retries < 0:
            errors.append(f"{scenario_id}:reproducibility.max_retries_invalid")
        if replay_window_sec <= 0:
            errors.append(f"{scenario_id}:reproducibility.replay_window_sec_invalid")

    vendor_violations: list[dict[str, Any]] = []
    for field_path, text in _collect_text_nodes(scenario):
        lowered = text.lower()
        for marker in BANNED_VENDOR_MARKERS:
            if marker in lowered:
                vendor_violations.append(
                    {
                        "marker": marker,
                        "field": field_path,
                        "excerpt": text[:160],
                    }
                )
    if vendor_violations:
        errors.append(f"{scenario_id}:vendor_specific_markers_detected")
    scenario_report["vendor_neutrality_violations"] = vendor_violations
    scenario_report["errors"] = errors
    scenario_report["status"] = "pass" if not errors else "fail"
    return scenario_report, errors, lane if lane in ALLOWED_LANES else None, len(vendor_violations)


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    dataset_path = _resolve_path(repo_root, str(args.dataset))

    if not dataset_path.exists():
        print(f"[competitive-benchmark-dataset-gate] dataset not found: {dataset_path}")
        return 2

    if int(args.min_scenarios_per_lane) < 1:
        print("[competitive-benchmark-dataset-gate] --min-scenarios-per-lane must be >= 1")
        return 2

    required_lanes = []
    for lane in args.require_lane:
        normalized = str(lane or "").strip().lower()
        if normalized and normalized in ALLOWED_LANES and normalized not in required_lanes:
            required_lanes.append(normalized)
    if not required_lanes:
        required_lanes = list(DEFAULT_REQUIRED_LANES)

    try:
        payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"[competitive-benchmark-dataset-gate] failed to read dataset: {exc}")
        return 2

    root_errors: list[str] = []
    scenario_reports: list[dict[str, Any]] = []
    lane_counts = {lane: 0 for lane in ALLOWED_LANES}
    seen_ids: set[str] = set()
    vendor_violation_count = 0

    suite_value = ""
    schema_version = None
    root_reproducibility: dict[str, Any] = {}
    root_audit: dict[str, Any] = {}

    if not isinstance(payload, dict):
        root_errors.append("dataset_root_not_object")
    else:
        suite_value = str(payload.get("suite") or "").strip()
        if suite_value != str(args.expected_suite).strip():
            root_errors.append(f"suite_mismatch:{suite_value or '<empty>'}")

        schema_version = payload.get("schema_version")
        if _safe_int(schema_version, -1) != int(args.expected_schema_version):
            root_errors.append(
                "schema_version_mismatch:"
                f"{_safe_int(schema_version, -1)}!={int(args.expected_schema_version)}"
            )

        reproducibility = payload.get("reproducibility")
        if not isinstance(reproducibility, dict):
            root_errors.append("reproducibility_missing")
        else:
            root_reproducibility = reproducibility
            if str(reproducibility.get("runtime_backend") or "").strip().lower() != "deterministic":
                root_errors.append("reproducibility.runtime_backend_must_be_deterministic")
            if not bool(reproducibility.get("idempotency_required", False)):
                root_errors.append("reproducibility.idempotency_required_must_be_true")

        audit = payload.get("audit")
        if not isinstance(audit, dict):
            root_errors.append("audit_missing")
        else:
            root_audit = audit
            required_trace_fields = audit.get("required_trace_fields")
            required_summary_metrics = audit.get("required_summary_metrics")
            if not isinstance(required_trace_fields, list) or not required_trace_fields:
                root_errors.append("audit.required_trace_fields_missing")
            if not isinstance(required_summary_metrics, list) or not required_summary_metrics:
                root_errors.append("audit.required_summary_metrics_missing")

        raw_scenarios = payload.get("scenarios")
        if not isinstance(raw_scenarios, list) or not raw_scenarios:
            root_errors.append("scenarios_missing")
            raw_scenarios = []
        for index, scenario in enumerate(raw_scenarios, start=1):
            scenario_report, errors, lane, violation_count = _validate_scenario(
                scenario=scenario,
                index=index,
                seen_ids=seen_ids,
            )
            scenario_reports.append(scenario_report)
            vendor_violation_count += violation_count
            if lane:
                lane_counts[lane] += 1
            if errors:
                root_errors.extend(errors)

    missing_required_lanes = [
        lane for lane in required_lanes if lane_counts.get(lane, 0) < int(args.min_scenarios_per_lane)
    ]
    if missing_required_lanes:
        root_errors.append(
            "required_lane_coverage_failed:"
            + ",".join(f"{lane}<{int(args.min_scenarios_per_lane)}" for lane in missing_required_lanes)
        )

    scenarios_total = len(scenario_reports)
    scenarios_failed = sum(1 for item in scenario_reports if str(item.get("status")) != "pass")
    scenarios_passed = scenarios_total - scenarios_failed
    status = "pass" if not root_errors else "fail"

    report = {
        "generated_at": _utc_now_iso(),
        "suite": SUITE,
        "dataset": {
            "path": str(dataset_path),
            "expected_suite": str(args.expected_suite),
            "expected_schema_version": int(args.expected_schema_version),
            "required_lanes": required_lanes,
            "min_scenarios_per_lane": int(args.min_scenarios_per_lane),
        },
        "summary": {
            "status": status,
            "scenarios_total": scenarios_total,
            "scenarios_passed": scenarios_passed,
            "scenarios_failed": scenarios_failed,
            "vendor_neutrality_violations": vendor_violation_count,
            "missing_required_lanes": missing_required_lanes,
            "errors": root_errors,
        },
        "lane_counts": lane_counts,
        "root_contract": {
            "suite": suite_value,
            "schema_version": _safe_int(schema_version, -1),
            "reproducibility": root_reproducibility,
            "audit": root_audit,
        },
        "scenarios": scenario_reports,
    }

    output_raw = str(args.output or "").strip()
    if output_raw:
        output_path = _resolve_path(repo_root, output_raw)
        _write_json(output_path, report)
        print(f"[competitive-benchmark-dataset-gate] report={output_path}")

    print(
        "[competitive-benchmark-dataset-gate] "
        f"{'OK' if status == 'pass' else 'FAILED'} "
        f"scenarios={scenarios_total} failed={scenarios_failed} "
        f"vendor_violations={vendor_violation_count}"
    )
    if status != "pass":
        return 1
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # pragma: no cover - defensive
        traceback.print_exc()
        raise SystemExit(1)
