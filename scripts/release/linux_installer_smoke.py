#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run Linux installer smoke checks against scripts/install_linux.sh "
            "and verify deterministic install/upgrade behavior."
        )
    )
    parser.add_argument(
        "--output",
        default=os.getenv("AMARYLLIS_LINUX_INSTALLER_SMOKE_OUTPUT", ""),
        help="Optional JSON report output path.",
    )
    parser.add_argument(
        "--require-linux",
        action="store_true",
        help="Fail if current platform is not Linux.",
    )
    parser.add_argument(
        "--bootstrap-python",
        default=os.getenv("AMARYLLIS_BOOTSTRAP_PYTHON", sys.executable),
        help="Python executable passed to installer/bootstrap path.",
    )
    parser.add_argument(
        "--keep-releases",
        type=int,
        default=int(os.getenv("AMARYLLIS_KEEP_RELEASES", "2")),
        help="Release retention count used by installer during smoke checks.",
    )
    parser.add_argument(
        "--command-timeout-sec",
        type=int,
        default=int(os.getenv("AMARYLLIS_LINUX_INSTALLER_SMOKE_TIMEOUT_SEC", "1800")),
        help="Timeout for each installer/launcher command.",
    )
    return parser.parse_args()


def _write_report(path: str, payload: dict[str, Any]) -> None:
    target = Path(path).expanduser().resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _mark_check(report: dict[str, Any], *, name: str, ok: bool, detail: str) -> None:
    checks = report.setdefault("checks", [])
    assert isinstance(checks, list)
    checks.append(
        {
            "name": name,
            "ok": bool(ok),
            "detail": detail,
        }
    )


def _run_cmd(
    report: dict[str, Any],
    *,
    label: str,
    cmd: list[str],
    cwd: Path,
    env: dict[str, str],
    timeout_sec: int,
) -> bool:
    started = time.perf_counter()
    completed = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=max(5, int(timeout_sec)),
        check=False,
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 2)
    commands = report.setdefault("commands", [])
    assert isinstance(commands, list)
    commands.append(
        {
            "label": label,
            "cmd": cmd,
            "returncode": int(completed.returncode),
            "duration_ms": elapsed_ms,
            "stdout_tail": (completed.stdout or "")[-1500:],
            "stderr_tail": (completed.stderr or "")[-1500:],
        }
    )
    return completed.returncode == 0


def _verify_release_state(
    report: dict[str, Any],
    *,
    check_prefix: str,
    install_root: Path,
    bin_dir: Path,
    expected_release_id: str,
    keep_releases: int,
    channel: str,
    expected_current_release_id: str | None,
) -> bool:
    ok = True
    current_link = install_root / "current"
    channel_link = install_root / "channels" / channel
    history_path = install_root / "channels" / f"{channel}.history"
    releases_dir = install_root / "releases"
    expected_release = releases_dir / expected_release_id
    launcher = bin_dir / "amaryllis-runtime"

    if not channel_link.is_symlink():
        _mark_check(
            report,
            name=f"{check_prefix}_channel_symlink",
            ok=False,
            detail=f"missing channel symlink: {channel_link}",
        )
        ok = False
    else:
        channel_target = channel_link.resolve()
        if channel_target != expected_release.resolve():
            _mark_check(
                report,
                name=f"{check_prefix}_channel_target",
                ok=False,
                detail=f"channel '{channel}' points to {channel_target}, expected {expected_release}",
            )
            ok = False
        else:
            _mark_check(
                report,
                name=f"{check_prefix}_channel_target",
                ok=True,
                detail=f"{channel_link} -> {channel_target}",
            )

    if expected_current_release_id is None:
        _mark_check(
            report,
            name=f"{check_prefix}_current_target",
            ok=True,
            detail="current target check skipped",
        )
    else:
        expected_current_release = releases_dir / expected_current_release_id
        if not current_link.is_symlink():
            _mark_check(
                report,
                name=f"{check_prefix}_current_symlink",
                ok=False,
                detail=f"missing current symlink: {current_link}",
            )
            ok = False
        else:
            current_target = current_link.resolve()
            if current_target != expected_current_release.resolve():
                _mark_check(
                    report,
                    name=f"{check_prefix}_current_target",
                    ok=False,
                    detail=f"current points to {current_target}, expected {expected_current_release}",
                )
                ok = False
            else:
                _mark_check(
                    report,
                    name=f"{check_prefix}_current_target",
                    ok=True,
                    detail=f"{current_link} -> {current_target}",
                )

    history_ok = False
    if history_path.exists():
        lines = [line.strip() for line in history_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if lines and lines[-1] == expected_release_id:
            history_ok = True
    _mark_check(
        report,
        name=f"{check_prefix}_channel_history",
        ok=history_ok,
        detail=f"history={history_path} latest={expected_release_id}",
    )
    if not history_ok:
        ok = False

    expected_runtime = expected_release / "src" / "runtime" / "server.py"
    if not expected_runtime.exists():
        _mark_check(
            report,
            name=f"{check_prefix}_runtime_source",
            ok=False,
            detail=f"runtime source missing: {expected_runtime}",
        )
        ok = False
    else:
        _mark_check(
            report,
            name=f"{check_prefix}_runtime_source",
            ok=True,
            detail=f"runtime source exists: {expected_runtime}",
        )

    expected_uvicorn = expected_release / "venv" / "bin" / "uvicorn"
    if not expected_uvicorn.exists():
        _mark_check(
            report,
            name=f"{check_prefix}_venv_uvicorn",
            ok=False,
            detail=f"uvicorn missing in release venv: {expected_uvicorn}",
        )
        ok = False
    else:
        _mark_check(
            report,
            name=f"{check_prefix}_venv_uvicorn",
            ok=True,
            detail=f"uvicorn exists: {expected_uvicorn}",
        )

    if not launcher.exists():
        _mark_check(
            report,
            name=f"{check_prefix}_launcher_exists",
            ok=False,
            detail=f"launcher missing: {launcher}",
        )
        ok = False
    elif not os.access(launcher, os.X_OK):
        _mark_check(
            report,
            name=f"{check_prefix}_launcher_executable",
            ok=False,
            detail=f"launcher is not executable: {launcher}",
        )
        ok = False
    else:
        _mark_check(
            report,
            name=f"{check_prefix}_launcher_executable",
            ok=True,
            detail=f"launcher is executable: {launcher}",
        )

    releases = sorted([item for item in releases_dir.glob("*") if item.is_dir()])
    max_allowed = max(1, int(keep_releases)) + 2
    if len(releases) > max_allowed:
        _mark_check(
            report,
            name=f"{check_prefix}_release_retention",
            ok=False,
            detail=f"release count={len(releases)} exceeds keep+channels={max_allowed}",
        )
        ok = False
    else:
        _mark_check(
            report,
            name=f"{check_prefix}_release_retention",
            ok=True,
            detail=f"release count={len(releases)} keep={keep_releases} max_allowed={max_allowed}",
        )
    return ok


def main() -> int:
    args = _parse_args()
    is_linux = sys.platform.startswith("linux")

    report: dict[str, Any] = {
        "suite": "linux_installer_smoke_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "sys_platform": sys.platform,
        },
        "require_linux": bool(args.require_linux),
        "keep_releases": max(1, int(args.keep_releases)),
        "bootstrap_python": str(args.bootstrap_python or ""),
        "commands": [],
        "checks": [],
    }

    if not is_linux:
        message = f"current platform is '{sys.platform}'"
        if args.require_linux:
            _mark_check(report, name="platform_check", ok=False, detail=message)
            if args.output:
                _write_report(args.output, report)
            print(f"[linux-installer-smoke] FAILED: {message}")
            return 1
        _mark_check(report, name="platform_check", ok=True, detail=f"skipped: {message}")
        if args.output:
            _write_report(args.output, report)
            print(f"[linux-installer-smoke] report={args.output}")
        print(f"[linux-installer-smoke] SKIPPED: {message}")
        return 0

    project_root = Path(__file__).resolve().parents[2]
    installer = project_root / "scripts" / "install_linux.sh"
    rollback = project_root / "scripts" / "release" / "linux_channel_rollback.py"
    if not installer.exists():
        _mark_check(report, name="installer_exists", ok=False, detail=f"missing installer: {installer}")
        if args.output:
            _write_report(args.output, report)
            print(f"[linux-installer-smoke] report={args.output}")
        print(f"[linux-installer-smoke] FAILED: missing installer {installer}")
        return 1
    _mark_check(report, name="installer_exists", ok=True, detail=str(installer))
    if not rollback.exists():
        _mark_check(report, name="rollback_script_exists", ok=False, detail=f"missing rollback script: {rollback}")
        if args.output:
            _write_report(args.output, report)
            print(f"[linux-installer-smoke] report={args.output}")
        print(f"[linux-installer-smoke] FAILED: missing rollback script {rollback}")
        return 1
    _mark_check(report, name="rollback_script_exists", ok=True, detail=str(rollback))

    failed = False
    with tempfile.TemporaryDirectory(prefix="amaryllis-linux-installer-smoke-") as tmp:
        temp_root = Path(tmp)
        install_root = temp_root / "runtime-install"
        bin_dir = temp_root / "bin"
        env = dict(os.environ)
        env["AMARYLLIS_LINUX_INSTALL_ROOT"] = str(install_root)
        env["AMARYLLIS_LINUX_BIN_DIR"] = str(bin_dir)
        env["AMARYLLIS_KEEP_RELEASES"] = str(max(1, int(args.keep_releases)))
        env["AMARYLLIS_BOOTSTRAP_PYTHON"] = str(args.bootstrap_python or sys.executable)

        release_one = "installer-smoke-r1"
        release_two = "installer-smoke-r2"
        canary_one = "installer-smoke-c1"
        canary_two = "installer-smoke-c2"
        launcher = bin_dir / "amaryllis-runtime"

        if not _run_cmd(
            report,
            label="install_release_r1",
            cmd=[str(installer), "--release-id", release_one, "--channel", "stable"],
            cwd=project_root,
            env=env,
            timeout_sec=args.command_timeout_sec,
        ):
            failed = True
        if not failed:
            failed = not _verify_release_state(
                report,
                check_prefix="release_r1",
                install_root=install_root,
                bin_dir=bin_dir,
                expected_release_id=release_one,
                keep_releases=max(1, int(args.keep_releases)),
                channel="stable",
                expected_current_release_id=release_one,
            )

        if not failed:
            if not _run_cmd(
                report,
                label="launcher_help_after_r1",
                cmd=[str(launcher), "--help"],
                cwd=project_root,
                env=env,
                timeout_sec=max(30, int(args.command_timeout_sec)),
            ):
                failed = True

        if not failed:
            if not _run_cmd(
                report,
                label="install_release_r2_upgrade",
                cmd=[str(installer), "--release-id", release_two, "--channel", "stable"],
                cwd=project_root,
                env=env,
                timeout_sec=args.command_timeout_sec,
            ):
                failed = True

        if not failed:
            failed = not _verify_release_state(
                report,
                check_prefix="release_r2",
                install_root=install_root,
                bin_dir=bin_dir,
                expected_release_id=release_two,
                keep_releases=max(1, int(args.keep_releases)),
                channel="stable",
                expected_current_release_id=release_two,
            )

        release_one_path = install_root / "releases" / release_one
        release_two_path = install_root / "releases" / release_two
        both_exist = release_one_path.exists() and release_two_path.exists()
        _mark_check(
            report,
            name="upgrade_keeps_prior_release",
            ok=both_exist,
            detail=f"release_one={release_one_path.exists()} release_two={release_two_path.exists()}",
        )
        if not both_exist:
            failed = True

        if not failed:
            if not _run_cmd(
                report,
                label="launcher_help_after_r2",
                cmd=[str(launcher), "--help"],
                cwd=project_root,
                env=env,
                timeout_sec=max(30, int(args.command_timeout_sec)),
            ):
                failed = True

        if not failed:
            if not _run_cmd(
                report,
                label="install_canary_c1",
                cmd=[str(installer), "--release-id", canary_one, "--channel", "canary"],
                cwd=project_root,
                env=env,
                timeout_sec=args.command_timeout_sec,
            ):
                failed = True

        if not failed:
            failed = not _verify_release_state(
                report,
                check_prefix="canary_c1",
                install_root=install_root,
                bin_dir=bin_dir,
                expected_release_id=canary_one,
                keep_releases=max(1, int(args.keep_releases)),
                channel="canary",
                expected_current_release_id=release_two,
            )

        if not failed:
            if not _run_cmd(
                report,
                label="install_canary_c2_upgrade",
                cmd=[str(installer), "--release-id", canary_two, "--channel", "canary"],
                cwd=project_root,
                env=env,
                timeout_sec=args.command_timeout_sec,
            ):
                failed = True

        if not failed:
            failed = not _verify_release_state(
                report,
                check_prefix="canary_c2",
                install_root=install_root,
                bin_dir=bin_dir,
                expected_release_id=canary_two,
                keep_releases=max(1, int(args.keep_releases)),
                channel="canary",
                expected_current_release_id=release_two,
            )

        if not failed:
            if not _run_cmd(
                report,
                label="rollback_canary_to_c1",
                cmd=[
                    str(sys.executable),
                    str(rollback),
                    "--install-root",
                    str(install_root),
                    "--channel",
                    "canary",
                    "--steps",
                    "1",
                ],
                cwd=project_root,
                env=env,
                timeout_sec=max(30, int(args.command_timeout_sec)),
            ):
                failed = True

        if not failed:
            failed = not _verify_release_state(
                report,
                check_prefix="canary_rollback",
                install_root=install_root,
                bin_dir=bin_dir,
                expected_release_id=canary_one,
                keep_releases=max(1, int(args.keep_releases)),
                channel="canary",
                expected_current_release_id=release_two,
            )

        if not failed:
            canary_env = dict(env)
            canary_env["AMARYLLIS_LINUX_RELEASE_CHANNEL"] = "canary"
            if not _run_cmd(
                report,
                label="launcher_help_canary_after_rollback",
                cmd=[str(launcher), "--help"],
                cwd=project_root,
                env=canary_env,
                timeout_sec=max(30, int(args.command_timeout_sec)),
            ):
                failed = True

        canary_one_path = install_root / "releases" / canary_one
        canary_two_path = install_root / "releases" / canary_two
        canary_both_exist = canary_one_path.exists() and canary_two_path.exists()
        _mark_check(
            report,
            name="rollback_keeps_canary_release_history",
            ok=canary_both_exist,
            detail=f"canary_one={canary_one_path.exists()} canary_two={canary_two_path.exists()}",
        )
        if not canary_both_exist:
            failed = True

        report["install_root"] = str(install_root)
        report["bin_dir"] = str(bin_dir)
        report["current_release"] = str((install_root / "current").resolve()) if (install_root / "current").exists() else ""
        report["stable_release"] = (
            str((install_root / "channels" / "stable").resolve())
            if (install_root / "channels" / "stable").exists()
            else ""
        )
        report["canary_release"] = (
            str((install_root / "channels" / "canary").resolve())
            if (install_root / "channels" / "canary").exists()
            else ""
        )

    if args.output:
        _write_report(args.output, report)
        print(f"[linux-installer-smoke] report={args.output}")

    if failed:
        print("[linux-installer-smoke] FAILED")
        return 1
    print("[linux-installer-smoke] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
