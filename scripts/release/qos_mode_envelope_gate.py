#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run QoS mode envelope benchmark across quality/balanced/power_save "
            "and fail on journey KPI or runtime QoS-contract regressions."
        )
    )
    parser.add_argument(
        "--journey-iterations",
        type=int,
        default=int(os.getenv("AMARYLLIS_QOS_ENVELOPE_ITERATIONS", "2")),
        help="User journey iterations per QoS mode.",
    )
    parser.add_argument(
        "--min-success-rate-pct",
        type=float,
        default=float(os.getenv("AMARYLLIS_QOS_ENVELOPE_MIN_SUCCESS_RATE_PCT", "100")),
        help="Minimum required journey success rate percent per mode.",
    )
    parser.add_argument(
        "--max-p95-journey-latency-ms",
        type=float,
        default=float(os.getenv("AMARYLLIS_QOS_ENVELOPE_MAX_P95_JOURNEY_MS", "3500")),
        help="Maximum allowed p95 journey latency per mode.",
    )
    parser.add_argument(
        "--max-p95-plan-dispatch-latency-ms",
        type=float,
        default=float(os.getenv("AMARYLLIS_QOS_ENVELOPE_MAX_P95_PLAN_MS", "1500")),
        help="Maximum allowed p95 plan dispatch latency per mode.",
    )
    parser.add_argument(
        "--max-p95-execute-dispatch-latency-ms",
        type=float,
        default=float(os.getenv("AMARYLLIS_QOS_ENVELOPE_MAX_P95_EXECUTE_MS", "1500")),
        help="Maximum allowed p95 execute dispatch latency per mode.",
    )
    parser.add_argument(
        "--max-p95-activation-latency-ms",
        type=float,
        default=float(os.getenv("AMARYLLIS_QOS_ENVELOPE_MAX_P95_ACTIVATION_MS", "600000")),
        help="Maximum allowed p95 activation latency per mode.",
    )
    parser.add_argument(
        "--max-failed-modes",
        type=int,
        default=int(os.getenv("AMARYLLIS_QOS_ENVELOPE_MAX_FAILED_MODES", "0")),
        help="Maximum allowed failed QoS modes in envelope run.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional JSON report output path.",
    )
    return parser.parse_args()


def _route_mode_for_qos(mode: str) -> str:
    mapping = {
        "quality": "quality_first",
        "balanced": "balanced",
        "power_save": "local_first",
    }
    return mapping.get(str(mode or "").strip().lower(), "balanced")


def _resolve_output(project_root: Path, raw: str) -> Path:
    path = Path(str(raw).strip())
    if not path.is_absolute():
        path = project_root / path
    return path


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("report_json_must_be_object")
    return payload


def _run_mode(
    *,
    mode: str,
    args: argparse.Namespace,
    project_root: Path,
    report_path: Path,
) -> tuple[dict[str, Any], list[str]]:
    script = project_root / "scripts" / "release" / "user_journey_benchmark.py"
    command = [
        sys.executable,
        str(script),
        "--iterations",
        str(max(1, int(args.journey_iterations))),
        "--min-success-rate-pct",
        str(float(args.min_success_rate_pct)),
        "--max-p95-journey-latency-ms",
        str(float(args.max_p95_journey_latency_ms)),
        "--max-p95-plan-dispatch-latency-ms",
        str(float(args.max_p95_plan_dispatch_latency_ms)),
        "--max-p95-execute-dispatch-latency-ms",
        str(float(args.max_p95_execute_dispatch_latency_ms)),
        "--max-p95-activation-latency-ms",
        str(float(args.max_p95_activation_latency_ms)),
        "--min-plan-to-execute-conversion-rate-pct",
        "100",
        "--min-activation-success-rate-pct",
        "100",
        "--max-blocked-activation-rate-pct",
        "0",
        "--min-install-success-rate-pct",
        "100",
        "--min-retention-proxy-success-rate-pct",
        "100",
        "--min-feature-adoption-rate-pct",
        "100",
        "--qos-mode",
        mode,
        "--cognition-backend",
        "deterministic",
        "--strict",
        "--output",
        str(report_path),
    ]

    env = os.environ.copy()
    env["AMARYLLIS_QOS_AUTO_ENABLED"] = "false"

    proc = subprocess.run(
        command,
        cwd=str(project_root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    errors: list[str] = []
    payload: dict[str, Any] = {}
    if report_path.exists():
        try:
            payload = _load_json(report_path)
        except Exception as exc:
            errors.append(f"{mode}:invalid_report:{exc}")
    else:
        errors.append(f"{mode}:missing_report")

    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    qos = config.get("qos") if isinstance(config.get("qos"), dict) else {}
    qos_runtime = qos.get("runtime") if isinstance(qos.get("runtime"), dict) else {}

    if int(proc.returncode) != 0:
        errors.append(f"{mode}:benchmark_exit={proc.returncode}")
    if str(summary.get("status") or "").strip().lower() != "pass":
        errors.append(f"{mode}:summary_status={summary.get('status')}")
    if int(summary.get("checks_failed") or 0) > 0:
        errors.append(f"{mode}:checks_failed={summary.get('checks_failed')}")

    active_mode = str(qos_runtime.get("active_mode") or "").strip().lower()
    auto_enabled = bool(qos_runtime.get("auto_enabled"))
    route_mode = str(qos_runtime.get("route_mode") or "").strip().lower()
    expected_route_mode = _route_mode_for_qos(mode)
    if active_mode != mode:
        errors.append(f"{mode}:runtime_active_mode={active_mode}:expected={mode}")
    if auto_enabled:
        errors.append(f"{mode}:runtime_auto_enabled_expected_false")
    if route_mode != expected_route_mode:
        errors.append(
            f"{mode}:runtime_route_mode={route_mode}:expected={expected_route_mode}"
        )

    return {
        "mode": mode,
        "command": command,
        "exit_code": int(proc.returncode),
        "stdout_tail": proc.stdout[-4000:],
        "stderr_tail": proc.stderr[-4000:],
        "report_path": str(report_path),
        "summary": summary,
        "runtime_qos": qos_runtime,
    }, errors


def main() -> int:
    args = _parse_args()
    project_root = Path(__file__).resolve().parents[2]

    if int(args.journey_iterations) <= 0:
        print("[qos-envelope-gate] --journey-iterations must be > 0", file=sys.stderr)
        return 2
    if int(args.max_failed_modes) < 0:
        print("[qos-envelope-gate] --max-failed-modes must be >= 0", file=sys.stderr)
        return 2

    modes = ("quality", "balanced", "power_save")
    mode_results: list[dict[str, Any]] = []
    errors: list[str] = []

    with tempfile.TemporaryDirectory(prefix="amaryllis-qos-envelope-gate-") as tmp:
        base = Path(tmp)
        for mode in modes:
            mode_report = base / f"user-journey-{mode}.json"
            result, mode_errors = _run_mode(
                mode=mode,
                args=args,
                project_root=project_root,
                report_path=mode_report,
            )
            mode_results.append(result)
            errors.extend(mode_errors)

    failed_modes = sorted({str(item).split(":", 1)[0] for item in errors if ":" in str(item)})
    if len(failed_modes) > max(0, int(args.max_failed_modes)):
        errors.append(
            f"failed_modes_exceeded:{len(failed_modes)}>{max(0, int(args.max_failed_modes))}"
        )

    report = {
        "suite": "qos_mode_envelope_gate_v1",
        "summary": {
            "status": "pass" if not errors else "fail",
            "modes_total": len(modes),
            "modes_failed": len(failed_modes),
            "failed_modes": failed_modes,
            "checks_failed": len(errors),
            "errors": errors,
        },
        "modes": mode_results,
    }

    if args.output:
        output_path = _resolve_output(project_root, str(args.output))
        _write_json(output_path, report)
        print(f"[qos-envelope-gate] report={output_path}")

    if errors:
        print("[qos-envelope-gate] FAILED")
        for item in errors:
            print(f"- {item}")
        return 1

    print("[qos-envelope-gate] OK modes=quality,balanced,power_save")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
