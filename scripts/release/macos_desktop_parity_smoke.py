#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import sys
import time
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tools.desktop_action_adapter import DesktopActionRequest, MacOSDesktopActionAdapter


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run macOS desktop-action parity smoke in staging mode. "
            "The checks are host-agnostic and validate the macOS adapter contract."
        )
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=int(os.getenv("AMARYLLIS_MACOS_DESKTOP_PARITY_ITERATIONS", "2")),
        help="Number of parity rounds.",
    )
    parser.add_argument(
        "--output",
        default=os.getenv("AMARYLLIS_MACOS_DESKTOP_PARITY_OUTPUT", ""),
        help="Optional JSON report output path.",
    )
    return parser.parse_args()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _percentile(values: list[float], p: int) -> float:
    if not values:
        return 0.0
    sorted_values = sorted(values)
    rank = max(0, min(len(sorted_values) - 1, int(round((p / 100.0) * (len(sorted_values) - 1)))))
    return float(sorted_values[rank])


class _Completed:
    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = int(returncode)
        self.stdout = str(stdout)
        self.stderr = str(stderr)


class _PopenResult:
    def __init__(self, pid: int) -> None:
        self.pid = int(pid)


class _MacOSParityHarness:
    def __init__(self) -> None:
        self.clipboard_value = ""
        self._next_pid = 4000

    def which(self, name: str) -> str | None:
        mapping = {
            "osascript": "/usr/bin/osascript",
            "pbcopy": "/usr/bin/pbcopy",
            "pbpaste": "/usr/bin/pbpaste",
            "open": "/usr/bin/open",
        }
        return mapping.get(str(name))

    def run(self, command: list[str], **kwargs: Any) -> _Completed:
        cmd = list(command)
        if not cmd:
            return _Completed(returncode=1, stderr="empty command")
        binary = str(cmd[0])
        if binary.endswith("pbcopy"):
            self.clipboard_value = str(kwargs.get("input") or "")
            return _Completed(returncode=0)
        if binary.endswith("pbpaste"):
            return _Completed(returncode=0, stdout=self.clipboard_value)
        if binary.endswith("osascript"):
            script = str(cmd[2] if len(cmd) > 2 else "")
            if "display notification" in script:
                return _Completed(returncode=0)
            if "get name of every application process" in script:
                return _Completed(returncode=0, stdout="Finder, Terminal, Safari\n")
            if " to activate" in script:
                return _Completed(returncode=0)
            if "close front window" in script:
                return _Completed(returncode=0)
            return _Completed(returncode=1, stderr="unsupported osascript smoke command")
        return _Completed(returncode=1, stderr=f"unsupported command: {binary}")

    def popen(self, command: list[str], **kwargs: Any) -> _PopenResult:
        _ = (command, kwargs)
        self._next_pid += 1
        return _PopenResult(pid=self._next_pid)


def _record_check(
    checks: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    latencies: list[float],
    *,
    round_number: int,
    check_id: str,
    detail: str,
    started: float,
    ok: bool,
) -> None:
    latency_ms = (time.perf_counter() - started) * 1000.0
    latencies.append(latency_ms)
    row = {
        "round": int(round_number),
        "check_id": str(check_id),
        "ok": bool(ok),
        "detail": str(detail),
        "latency_ms": round(latency_ms, 2),
    }
    checks.append(row)
    if not ok:
        failures.append(row)


def _run_smoke_round(
    *,
    round_number: int,
    checks: list[dict[str, Any]],
    failures: list[dict[str, Any]],
    latencies: list[float],
) -> None:
    harness = _MacOSParityHarness()
    adapter = MacOSDesktopActionAdapter(
        which_resolver=harness.which,
        run_command=harness.run,
        popen_command=harness.popen,
    )

    started = time.perf_counter()
    describe = adapter.describe()
    ok = str(describe.get("kind")) == "macos" and bool(describe.get("supports_real_desktop"))
    _record_check(
        checks,
        failures,
        latencies,
        round_number=round_number,
        check_id="describe.kind",
        detail=f"kind={describe.get('kind')}",
        started=started,
        ok=ok,
    )

    notify_started = time.perf_counter()
    notify_result = adapter.execute(
        DesktopActionRequest.from_arguments(
            {"action": "notify", "title": "Amaryllis", "message": f"smoke-{round_number}"}
        )
    )
    _record_check(
        checks,
        failures,
        latencies,
        round_number=round_number,
        check_id="notify",
        detail=str(notify_result.message or notify_result.status),
        started=notify_started,
        ok=bool(notify_result.ok and notify_result.status == "succeeded"),
    )

    clipboard_write_started = time.perf_counter()
    text = f"parity-round-{round_number}"
    clipboard_write = adapter.execute(
        DesktopActionRequest.from_arguments({"action": "clipboard_write", "text": text})
    )
    _record_check(
        checks,
        failures,
        latencies,
        round_number=round_number,
        check_id="clipboard_write",
        detail=str(clipboard_write.status),
        started=clipboard_write_started,
        ok=bool(clipboard_write.ok and clipboard_write.status == "succeeded"),
    )

    clipboard_read_started = time.perf_counter()
    clipboard_read = adapter.execute(
        DesktopActionRequest.from_arguments({"action": "clipboard_read"})
    )
    content = str(clipboard_read.data.get("content") or "")
    _record_check(
        checks,
        failures,
        latencies,
        round_number=round_number,
        check_id="clipboard_read",
        detail=f"content_len={len(content)}",
        started=clipboard_read_started,
        ok=bool(clipboard_read.ok and clipboard_read.status == "succeeded" and content == text),
    )

    app_launch_started = time.perf_counter()
    app_launch = adapter.execute(
        DesktopActionRequest.from_arguments({"action": "app_launch", "target": "com.apple.Safari"})
    )
    launch_command = app_launch.data.get("command")
    _record_check(
        checks,
        failures,
        latencies,
        round_number=round_number,
        check_id="app_launch",
        detail=str(launch_command),
        started=app_launch_started,
        ok=bool(
            app_launch.ok
            and app_launch.status == "succeeded"
            and isinstance(launch_command, list)
            and launch_command[:3] == ["/usr/bin/open", "-b", "com.apple.Safari"]
        ),
    )

    window_list_started = time.perf_counter()
    window_list = adapter.execute(DesktopActionRequest.from_arguments({"action": "window_list"}))
    windows = window_list.data.get("windows")
    _record_check(
        checks,
        failures,
        latencies,
        round_number=round_number,
        check_id="window_list",
        detail=f"count={window_list.data.get('count')}",
        started=window_list_started,
        ok=bool(
            window_list.ok
            and window_list.status == "succeeded"
            and isinstance(windows, list)
            and len(windows) >= 1
        ),
    )

    window_focus_started = time.perf_counter()
    window_focus = adapter.execute(
        DesktopActionRequest.from_arguments({"action": "window_focus", "target": "Safari"})
    )
    _record_check(
        checks,
        failures,
        latencies,
        round_number=round_number,
        check_id="window_focus",
        detail=str(window_focus.status),
        started=window_focus_started,
        ok=bool(window_focus.ok and window_focus.status == "succeeded"),
    )

    window_close_started = time.perf_counter()
    window_close = adapter.execute(
        DesktopActionRequest.from_arguments({"action": "window_close", "target": "Safari"})
    )
    _record_check(
        checks,
        failures,
        latencies,
        round_number=round_number,
        check_id="window_close",
        detail=str(window_close.status),
        started=window_close_started,
        ok=bool(window_close.ok and window_close.status == "succeeded"),
    )


def main() -> int:
    args = _parse_args()
    if int(args.iterations) <= 0:
        print("[macos-desktop-parity] --iterations must be >= 1", file=sys.stderr)
        return 2

    checks: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    latencies: list[float] = []
    started_at = _utc_now_iso()

    for round_number in range(1, int(args.iterations) + 1):
        _run_smoke_round(
            round_number=round_number,
            checks=checks,
            failures=failures,
            latencies=latencies,
        )

    failed_checks = len(failures)
    total_checks = len(checks)
    passed_checks = total_checks - failed_checks
    error_rate_pct = (float(failed_checks) / float(total_checks) * 100.0) if total_checks > 0 else 0.0
    summary = {
        "checks_total": total_checks,
        "checks_passed": passed_checks,
        "checks_failed": failed_checks,
        "error_rate_pct": round(error_rate_pct, 4),
        "status": "pass" if failed_checks == 0 else "fail",
        "latency_ms": {
            "p50": round(_percentile(latencies, 50), 2),
            "p95": round(_percentile(latencies, 95), 2),
            "max": round(max(latencies) if latencies else 0.0, 2),
        },
    }

    payload = {
        "generated_at": _utc_now_iso(),
        "started_at": started_at,
        "suite": "macos_desktop_parity_smoke_v1",
        "staging_target_platform": "darwin",
        "iterations": int(args.iterations),
        "platform": {
            "python": platform.python_version(),
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "sys_platform": sys.platform,
        },
        "checks": checks,
        "failures": failures,
        "summary": summary,
    }

    output_raw = str(args.output or "").strip()
    if output_raw:
        output_path = Path(output_raw)
        if not output_path.is_absolute():
            output_path = Path.cwd() / output_path
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[macos-desktop-parity] report={output_path}")

    print(json.dumps(summary, ensure_ascii=False))
    if failed_checks > 0:
        print("[macos-desktop-parity] FAILED")
        return 1

    print("[macos-desktop-parity] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
