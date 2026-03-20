#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
from typing import Iterable

_ALLOWED_CHANNELS = {"stable", "canary"}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Rollback Linux runtime channel symlink to a previous release "
            "recorded by scripts/install_linux.sh history."
        )
    )
    parser.add_argument(
        "--install-root",
        default=os.getenv("AMARYLLIS_LINUX_INSTALL_ROOT", str(Path.home() / ".local/share/amaryllis")),
        help="Linux install root (default: ~/.local/share/amaryllis).",
    )
    parser.add_argument(
        "--channel",
        default=os.getenv("AMARYLLIS_LINUX_RELEASE_CHANNEL", "stable"),
        help="Release channel to rollback (stable|canary).",
    )
    parser.add_argument(
        "--steps",
        type=int,
        default=1,
        help="How many history transitions to rollback (default: 1).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned rollback actions without mutating filesystem.",
    )
    return parser.parse_args()


def _normalize_timeline(items: Iterable[str], releases_dir: Path) -> list[str]:
    timeline: list[str] = []
    for raw in items:
        release_id = str(raw or "").strip()
        if not release_id:
            continue
        if not (releases_dir / release_id).is_dir():
            continue
        if timeline and timeline[-1] == release_id:
            continue
        timeline.append(release_id)
    return timeline


def _read_history(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]


def _append_history(path: Path, release_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
        if lines and lines[-1] == release_id:
            return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{release_id}\n")


def _release_id_from_link(link: Path) -> str:
    target = link.resolve()
    return target.name


def main() -> int:
    args = _parse_args()
    channel = str(args.channel or "").strip().lower()
    steps = int(args.steps)

    if channel not in _ALLOWED_CHANNELS:
        print(
            f"[linux-channel-rollback] invalid --channel={channel!r}; expected one of: stable|canary",
            file=sys.stderr,
        )
        return 2

    if steps < 1:
        print("[linux-channel-rollback] --steps must be >= 1", file=sys.stderr)
        return 2

    install_root = Path(str(args.install_root)).expanduser().resolve()
    releases_dir = install_root / "releases"
    channels_dir = install_root / "channels"
    channel_link = channels_dir / channel
    history_path = channels_dir / f"{channel}.history"
    current_link = install_root / "current"

    if not channel_link.is_symlink():
        print(f"[linux-channel-rollback] channel link missing: {channel_link}", file=sys.stderr)
        return 1

    history_lines = _read_history(history_path)
    timeline = _normalize_timeline(history_lines, releases_dir)

    current_release = _release_id_from_link(channel_link)
    if not timeline:
        print(
            f"[linux-channel-rollback] no rollback history available for channel '{channel}'",
            file=sys.stderr,
        )
        return 1

    current_idx = -1
    for idx in range(len(timeline) - 1, -1, -1):
        if timeline[idx] == current_release:
            current_idx = idx
            break

    if current_idx < 0:
        print(
            "[linux-channel-rollback] current channel target is not present in rollback history; "
            "refuse unsafe rollback",
            file=sys.stderr,
        )
        return 1

    target_idx = current_idx - steps
    if target_idx < 0:
        print(
            f"[linux-channel-rollback] cannot rollback {steps} step(s); "
            f"history depth before current={current_idx}",
            file=sys.stderr,
        )
        return 1

    target_release = timeline[target_idx]
    target_path = releases_dir / target_release
    if not target_path.is_dir():
        print(
            f"[linux-channel-rollback] target release directory missing: {target_path}",
            file=sys.stderr,
        )
        return 1

    print(f"[linux-channel-rollback] install_root={install_root}")
    print(f"[linux-channel-rollback] channel={channel}")
    print(f"[linux-channel-rollback] current={current_release}")
    print(f"[linux-channel-rollback] target={target_release}")
    print(f"[linux-channel-rollback] steps={steps}")

    if args.dry_run:
        print(f"[linux-channel-rollback] dry-run: ln -sfn {target_path} {channel_link}")
        if channel == "stable":
            print(f"[linux-channel-rollback] dry-run: ln -sfn {target_path} {current_link}")
        print(f"[linux-channel-rollback] dry-run: append {target_release} to {history_path}")
        print("[linux-channel-rollback] OK (dry-run)")
        return 0

    channels_dir.mkdir(parents=True, exist_ok=True)
    # Use symlink replacement for deterministic rollback.
    channel_link.unlink(missing_ok=True)
    channel_link.symlink_to(target_path)

    if channel == "stable":
        if current_link.exists() and not current_link.is_symlink():
            print(
                f"[linux-channel-rollback] refuse to replace non-symlink current pointer: {current_link}",
                file=sys.stderr,
            )
            return 1
        current_link.unlink(missing_ok=True)
        current_link.symlink_to(target_path)

    _append_history(history_path, target_release)

    print("[linux-channel-rollback] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
