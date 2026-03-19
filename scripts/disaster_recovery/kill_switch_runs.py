#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys

import httpx


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Trigger Amaryllis run kill switch via service API.")
    parser.add_argument(
        "--base-url",
        default=os.getenv("AMARYLLIS_BASE_URL", "http://localhost:8000"),
        help="Runtime base URL.",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("AMARYLLIS_SERVICE_TOKEN", ""),
        help="Service/admin bearer token (or set AMARYLLIS_SERVICE_TOKEN).",
    )
    parser.add_argument(
        "--reason",
        default="manual-kill-switch-cli",
        help="Kill-switch reason stored in audit/checkpoints.",
    )
    parser.add_argument(
        "--include-running",
        default="true",
        choices=("true", "false"),
        help="Whether to interrupt running runs.",
    )
    parser.add_argument(
        "--include-queued",
        default="true",
        choices=("true", "false"),
        help="Whether to cancel queued runs.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5000,
        help="Max number of runs scanned per target status.",
    )
    parser.add_argument(
        "--timeout-sec",
        type=float,
        default=15.0,
        help="HTTP timeout in seconds.",
    )
    return parser.parse_args()


def _parse_bool(value: str) -> bool:
    return str(value or "").strip().lower() == "true"


def main() -> int:
    args = _parse_args()
    token = str(args.token or "").strip()
    if not token:
        print("error: service token is required (use --token or AMARYLLIS_SERVICE_TOKEN)", file=sys.stderr)
        return 2

    include_running = _parse_bool(args.include_running)
    include_queued = _parse_bool(args.include_queued)
    payload = {
        "reason": str(args.reason or "").strip() or None,
        "include_running": include_running,
        "include_queued": include_queued,
        "limit": max(1, int(args.limit)),
    }
    url = f"{str(args.base_url).rstrip('/')}/service/runs/kill-switch"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    try:
        with httpx.Client(timeout=max(1.0, float(args.timeout_sec))) as client:
            response = client.post(url, headers=headers, json=payload)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": f"request_failed: {exc}",
                    "url": url,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    try:
        parsed = response.json()
    except Exception:
        parsed = {
            "status_code": int(response.status_code),
            "body": response.text,
        }
    if not isinstance(parsed, dict):
        parsed = {
            "status_code": int(response.status_code),
            "body": str(parsed),
        }

    if response.status_code >= 400:
        print(
            json.dumps(
                {
                    "ok": False,
                    "status_code": int(response.status_code),
                    "error": parsed,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    print(json.dumps(parsed, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
