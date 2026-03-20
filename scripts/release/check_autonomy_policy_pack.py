#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from tools.autonomy_policy_pack import AutonomyPolicyPackError, load_autonomy_policy_pack


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate autonomy policy-pack schema/contract for L0-L5 risk rules "
            "and approval semantics."
        )
    )
    parser.add_argument(
        "--policy-pack",
        default="policies/autonomy/default.json",
        help="Path to autonomy policy-pack JSON.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    policy_path = Path(str(args.policy_pack).strip())
    if not policy_path.is_absolute():
        policy_path = (ROOT_DIR / policy_path).resolve()

    try:
        pack = load_autonomy_policy_pack(policy_path)
    except AutonomyPolicyPackError as exc:
        print(f"[autonomy-policy-pack] FAILED: {exc}", file=sys.stderr)
        return 1

    print(
        "[autonomy-policy-pack] OK "
        f"pack={pack.pack} schema_version={pack.schema_version} path={pack.source_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
