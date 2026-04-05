#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from eval.replay_snapshot import canonicalize_replay_snapshot
from eval.news_digest_snapshot import canonicalize_news_digest_snapshot
from news.digest import compose_grounded_digest


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate deterministic eval/replay chain via seeded golden-task fixtures "
            "and canonical replay snapshot fixtures."
        )
    )
    parser.add_argument("--repo-root", default=".", help="Repository root path.")
    parser.add_argument(
        "--golden-tasks",
        default="eval/golden_tasks/deterministic_smoke.json",
        help="Golden tasks file for deterministic fixture mode.",
    )
    parser.add_argument(
        "--golden-fixture-responses",
        default="eval/fixtures/golden_tasks/deterministic_smoke_responses.json",
        help="Fixture responses for deterministic golden eval mode.",
    )
    parser.add_argument(
        "--golden-snapshot",
        default="eval/fixtures/golden_tasks/deterministic_smoke_snapshot.json",
        help="Expected canonical snapshot path for deterministic golden eval.",
    )
    parser.add_argument(
        "--replay-input",
        default="eval/fixtures/replay/sample_replay_input.json",
        help="Replay payload fixture path.",
    )
    parser.add_argument(
        "--replay-snapshot",
        default="eval/fixtures/replay/sample_replay_snapshot.json",
        help="Expected canonical replay snapshot fixture path.",
    )
    parser.add_argument(
        "--news-digest-input",
        default="eval/fixtures/replay/news/news_digest_input.json",
        help="News digest replay input fixture path.",
    )
    parser.add_argument(
        "--news-digest-snapshot",
        default="eval/fixtures/replay/news/news_digest_snapshot.json",
        help="Expected canonical snapshot for news digest replay fixture.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=1337,
        help="Deterministic seed value forwarded to golden eval runner.",
    )
    parser.add_argument(
        "--update-fixtures",
        action="store_true",
        help="Rewrite expected snapshot fixtures instead of failing on drift.",
    )
    return parser.parse_args()


def _resolve_path(repo_root: Path, raw_path: str) -> Path:
    candidate = Path(str(raw_path or "")).expanduser()
    if not candidate.is_absolute():
        candidate = repo_root / candidate
    return candidate.resolve()


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_json_object(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be object: {path}")
    return payload


def _run_golden_determinism_check(
    *,
    repo_root: Path,
    golden_tasks: Path,
    golden_fixture_responses: Path,
    golden_snapshot: Path,
    seed: int,
    update_fixtures: bool,
) -> list[str]:
    failures: list[str] = []
    runner = repo_root / "scripts" / "eval" / "run_golden_tasks.py"
    if not runner.exists():
        return [f"golden runner not found: {runner}"]

    with tempfile.TemporaryDirectory(prefix="amaryllis-eval-replay-determinism-") as tmp:
        report_path = Path(tmp) / "golden-report.json"
        command = [
            sys.executable,
            str(runner),
            "--tasks-file",
            str(golden_tasks),
            "--fixture-responses",
            str(golden_fixture_responses),
            "--seed",
            str(int(seed)),
            "--output",
            str(report_path),
            "--snapshot-expected",
            str(golden_snapshot),
        ]
        if update_fixtures:
            command.append("--update-snapshot")

        proc = subprocess.run(
            command,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )

        if proc.returncode != 0:
            failures.append("golden deterministic snapshot drift")
            failures.append(f"golden stdout: {proc.stdout.strip()}")
            failures.append(f"golden stderr: {proc.stderr.strip()}")
        elif not report_path.exists():
            failures.append(f"golden deterministic report not produced: {report_path}")

    return failures


def _run_replay_snapshot_check(
    *,
    replay_input: Path,
    replay_snapshot: Path,
    update_fixtures: bool,
) -> list[str]:
    failures: list[str] = []
    if not replay_input.exists():
        return [f"replay input fixture not found: {replay_input}"]

    try:
        input_payload = _load_json_object(replay_input)
    except Exception as exc:
        return [f"replay input fixture invalid: {exc}"]

    canonical = canonicalize_replay_snapshot(input_payload)
    if update_fixtures:
        _write_json(replay_snapshot, canonical)
        return []

    if not replay_snapshot.exists():
        return [f"replay expected snapshot not found: {replay_snapshot}"]

    try:
        expected = _load_json_object(replay_snapshot)
    except Exception as exc:
        return [f"replay expected snapshot invalid: {exc}"]

    if expected != canonical:
        failures.append("replay deterministic snapshot drift")
        failures.append(f"replay expected: {replay_snapshot}")
        failures.append(
            "replay summary expected="
            f"{json.dumps(expected.get('issue_summary', {}), ensure_ascii=False)} "
            f"actual={json.dumps(canonical.get('issue_summary', {}), ensure_ascii=False)}"
        )
    return failures


def _run_news_digest_snapshot_check(
    *,
    news_digest_input: Path,
    news_digest_snapshot: Path,
    update_fixtures: bool,
) -> list[str]:
    failures: list[str] = []
    if not news_digest_input.exists():
        return [f"news digest replay input fixture not found: {news_digest_input}"]

    try:
        payload = _load_json_object(news_digest_input)
    except Exception as exc:
        return [f"news digest replay input fixture invalid: {exc}"]

    digest = compose_grounded_digest(
        topic=str(payload.get("topic") or ""),
        items=list(payload.get("items") or []),
        max_sections=int(payload.get("max_sections") or 5),
    )
    canonical = canonicalize_news_digest_snapshot(digest)

    if update_fixtures:
        _write_json(news_digest_snapshot, canonical)
        return []

    if not news_digest_snapshot.exists():
        return [f"news digest expected snapshot not found: {news_digest_snapshot}"]

    try:
        expected = _load_json_object(news_digest_snapshot)
    except Exception as exc:
        return [f"news digest expected snapshot invalid: {exc}"]

    if expected != canonical:
        failures.append("news digest deterministic snapshot drift")
        failures.append(f"news digest expected: {news_digest_snapshot}")
        failures.append(
            "news digest metrics expected="
            f"{json.dumps(expected.get('metrics', {}), ensure_ascii=False)} "
            f"actual={json.dumps(canonical.get('metrics', {}), ensure_ascii=False)}"
        )
    return failures


def main() -> int:
    args = _parse_args()
    repo_root = _resolve_path(ROOT_DIR, args.repo_root)
    if not repo_root.exists():
        print(f"repo root not found: {repo_root}", file=sys.stderr)
        return 2

    golden_tasks = _resolve_path(repo_root, args.golden_tasks)
    golden_fixture_responses = _resolve_path(repo_root, args.golden_fixture_responses)
    golden_snapshot = _resolve_path(repo_root, args.golden_snapshot)
    replay_input = _resolve_path(repo_root, args.replay_input)
    replay_snapshot = _resolve_path(repo_root, args.replay_snapshot)
    news_digest_input = _resolve_path(repo_root, args.news_digest_input)
    news_digest_snapshot = _resolve_path(repo_root, args.news_digest_snapshot)

    failures: list[str] = []

    for path_label, path_value in (
        ("golden tasks", golden_tasks),
        ("golden fixture responses", golden_fixture_responses),
    ):
        if not path_value.exists():
            failures.append(f"{path_label} not found: {path_value}")

    if failures:
        print("[eval-replay-determinism] FAILED", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    failures.extend(
        _run_golden_determinism_check(
            repo_root=repo_root,
            golden_tasks=golden_tasks,
            golden_fixture_responses=golden_fixture_responses,
            golden_snapshot=golden_snapshot,
            seed=int(args.seed),
            update_fixtures=bool(args.update_fixtures),
        )
    )

    failures.extend(
        _run_replay_snapshot_check(
            replay_input=replay_input,
            replay_snapshot=replay_snapshot,
            update_fixtures=bool(args.update_fixtures),
        )
    )
    failures.extend(
        _run_news_digest_snapshot_check(
            news_digest_input=news_digest_input,
            news_digest_snapshot=news_digest_snapshot,
            update_fixtures=bool(args.update_fixtures),
        )
    )

    if failures:
        print("[eval-replay-determinism] FAILED", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print(
        "[eval-replay-determinism] OK "
        f"seed={int(args.seed)} golden_snapshot={golden_snapshot} replay_snapshot={replay_snapshot} "
        f"news_digest_snapshot={news_digest_snapshot}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
