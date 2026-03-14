#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import subprocess
import sys


def main() -> int:
    project_root = Path(__file__).resolve().parents[2]
    command = [
        sys.executable,
        str(project_root / "scripts" / "security" / "compliance_check.py"),
    ]
    completed = subprocess.run(command, check=False)
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
