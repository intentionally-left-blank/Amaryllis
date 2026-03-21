#!/usr/bin/env python3
from __future__ import annotations

import argparse
from collections import OrderedDict
from pathlib import Path
import plistlib
import sys
from typing import Iterable


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Render deterministic runtime service manifests for Linux systemd user service "
            "or macOS launchd agent."
        )
    )
    parser.add_argument(
        "--target",
        required=True,
        choices=("linux-systemd", "macos-launchd"),
        help="Manifest target kind.",
    )
    parser.add_argument(
        "--service-name",
        default="amaryllis-runtime",
        help="Service/agent name (default: amaryllis-runtime).",
    )
    parser.add_argument(
        "--install-root",
        default=str(Path.home() / ".local" / "share" / "amaryllis"),
        help="Runtime install root.",
    )
    parser.add_argument(
        "--bin-dir",
        default=str(Path.home() / ".local" / "bin"),
        help="Directory containing runtime launcher binary.",
    )
    parser.add_argument(
        "--channel",
        default="stable",
        choices=("stable", "canary"),
        help="Release channel for runtime launcher.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Runtime bind host.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Runtime bind port.",
    )
    parser.add_argument(
        "--working-directory",
        default="",
        help="Optional working directory override.",
    )
    parser.add_argument(
        "--label-prefix",
        default="org.amaryllis",
        help="launchd label prefix (macOS target only).",
    )
    parser.add_argument(
        "--environment",
        action="append",
        default=[],
        help="Extra environment value in KEY=VALUE format. Repeatable.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional output file. Prints to stdout when omitted.",
    )
    return parser.parse_args()


def _parse_environment_items(items: Iterable[str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for raw in items:
        token = str(raw or "").strip()
        if not token:
            continue
        if "=" not in token:
            raise ValueError(f"Invalid --environment entry: {token!r} (expected KEY=VALUE)")
        key, value = token.split("=", 1)
        normalized_key = key.strip()
        if not normalized_key:
            raise ValueError(f"Invalid --environment entry: {token!r} (empty key)")
        env[normalized_key] = value
    return env


def _base_env(*, install_root: str, channel: str, host: str, port: int) -> dict[str, str]:
    root = str(install_root)
    return {
        "AMARYLLIS_LINUX_INSTALL_ROOT": root,
        "AMARYLLIS_LINUX_RELEASE_CHANNEL": str(channel),
        "AMARYLLIS_HOST": str(host),
        "AMARYLLIS_PORT": str(int(port)),
        "AMARYLLIS_RELEASE_QUALITY_DASHBOARD_PATH": str(
            Path(root) / "observability" / "release-quality-dashboard-latest.json"
        ),
        "AMARYLLIS_NIGHTLY_MISSION_REPORT_PATH": str(
            Path(root) / "observability" / "nightly-mission-success-recovery-latest.json"
        ),
    }


def _render_linux_systemd(
    *,
    service_name: str,
    install_root: str,
    launcher_path: str,
    channel: str,
    host: str,
    port: int,
    working_directory: str,
    extra_environment: dict[str, str],
) -> str:
    env = OrderedDict()
    for key, value in _base_env(
        install_root=install_root,
        channel=channel,
        host=host,
        port=port,
    ).items():
        env[key] = value
    for key in sorted(extra_environment):
        env[key] = str(extra_environment[key])

    lines: list[str] = [
        "[Unit]",
        f"Description=Amaryllis Runtime Service ({channel})",
        "Wants=network-online.target",
        "After=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        f"WorkingDirectory={working_directory or install_root}",
        f"ExecStart={launcher_path}",
    ]
    for key, value in env.items():
        safe_value = str(value).replace('"', '\\"')
        lines.append(f'Environment="{key}={safe_value}"')

    lines.extend(
        [
            "Restart=on-failure",
            "RestartSec=5",
            "NoNewPrivileges=true",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        ]
    )
    return "\n".join(lines)


def _render_macos_launchd(
    *,
    service_name: str,
    install_root: str,
    launcher_path: str,
    channel: str,
    host: str,
    port: int,
    working_directory: str,
    label_prefix: str,
    extra_environment: dict[str, str],
) -> str:
    env = _base_env(
        install_root=install_root,
        channel=channel,
        host=host,
        port=port,
    )
    for key in sorted(extra_environment):
        env[key] = str(extra_environment[key])

    normalized_prefix = str(label_prefix or "org.amaryllis").strip().strip(".") or "org.amaryllis"
    label = f"{normalized_prefix}.{service_name}".replace(" ", "-")

    payload: OrderedDict[str, object] = OrderedDict()
    payload["Label"] = label
    payload["ProgramArguments"] = [launcher_path]
    payload["WorkingDirectory"] = working_directory or install_root
    payload["RunAtLoad"] = True
    payload["KeepAlive"] = True
    payload["EnvironmentVariables"] = env

    return plistlib.dumps(payload, fmt=plistlib.FMT_XML).decode("utf-8")


def main() -> int:
    args = _parse_args()

    service_name = str(args.service_name or "").strip()
    if not service_name:
        print("[runtime-manifest] --service-name must be non-empty", file=sys.stderr)
        return 2

    if int(args.port) <= 0 or int(args.port) > 65535:
        print("[runtime-manifest] --port must be in [1, 65535]", file=sys.stderr)
        return 2

    install_root = str(Path(args.install_root).expanduser())
    launcher_path = str(Path(args.bin_dir).expanduser() / service_name)
    try:
        extra_environment = _parse_environment_items(args.environment)
    except ValueError as exc:
        print(f"[runtime-manifest] {exc}", file=sys.stderr)
        return 2

    if args.target == "linux-systemd":
        rendered = _render_linux_systemd(
            service_name=service_name,
            install_root=install_root,
            launcher_path=launcher_path,
            channel=str(args.channel),
            host=str(args.host),
            port=int(args.port),
            working_directory=str(args.working_directory or "").strip(),
            extra_environment=extra_environment,
        )
    else:
        rendered = _render_macos_launchd(
            service_name=service_name,
            install_root=install_root,
            launcher_path=launcher_path,
            channel=str(args.channel),
            host=str(args.host),
            port=int(args.port),
            working_directory=str(args.working_directory or "").strip(),
            label_prefix=str(args.label_prefix),
            extra_environment=extra_environment,
        )

    output = str(args.output or "").strip()
    if output:
        target = Path(output).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
        print(f"[runtime-manifest] wrote {target}")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
