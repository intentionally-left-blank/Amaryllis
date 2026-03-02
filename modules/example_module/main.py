from __future__ import annotations

import json
import sys
from typing import Any


def run(context: dict[str, Any]) -> dict[str, Any]:
    input_payload = context.get("input", {})
    user_id = context.get("user_id", "")

    return {
        "output": {
            "echo": input_payload,
            "received_user_id": user_id,
        },
        "memory_write": {
            "last_input": input_payload,
        },
    }


def _main() -> int:
    try:
        raw_context = sys.stdin.read()
        context = json.loads(raw_context)
        if not isinstance(context, dict):
            raise ValueError("Context must be a JSON object.")

        result = run(context)
        sys.stdout.write(json.dumps(result))
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(_main())
