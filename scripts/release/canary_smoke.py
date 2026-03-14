#!/usr/bin/env python3
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def main() -> int:
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    try:
        from fastapi.testclient import TestClient
    except Exception as exc:  # pragma: no cover - environment-dependent
        print(f"[canary] fastapi testclient unavailable: {exc}")
        return 2

    with tempfile.TemporaryDirectory(prefix="amaryllis-canary-") as tmp:
        support_dir = Path(tmp) / "support"
        auth_tokens = {
            "admin-token": {"user_id": "admin", "scopes": ["admin", "user"]},
            "user-token": {"user_id": "user-1", "scopes": ["user"]},
            "service-token": {"user_id": "svc-runtime", "scopes": ["service"]},
        }
        os.environ["AMARYLLIS_SUPPORT_DIR"] = str(support_dir)
        os.environ["AMARYLLIS_AUTH_ENABLED"] = "true"
        os.environ["AMARYLLIS_AUTH_TOKENS"] = json.dumps(auth_tokens, ensure_ascii=False)
        os.environ["AMARYLLIS_MEMORY_CONSOLIDATION_ENABLED"] = "false"
        os.environ["AMARYLLIS_MCP_ENDPOINTS"] = ""
        os.environ["AMARYLLIS_SECURITY_PROFILE"] = "production"

        import runtime.server as server_module

        server_module = importlib.reload(server_module)
        with TestClient(server_module.app) as client:
            checks: list[tuple[str, str, int]] = [
                ("GET", "/health", 200),
                ("GET", "/service/health", 200),
                ("GET", "/v1/models", 200),
                ("GET", "/models", 200),
                ("GET", "/service/observability/slo", 200),
                ("GET", "/service/api/lifecycle", 200),
                ("GET", "/service/backup/status", 200),
                ("POST", "/v1/models/route", 200),
            ]
            failed: list[str] = []
            for method, path, expected in checks:
                headers: dict[str, str] = {}
                payload: dict[str, object] | None = None
                if path.startswith("/service/"):
                    headers = _auth("service-token")
                elif path.startswith("/health"):
                    headers = {}
                else:
                    headers = _auth("user-token")
                if method == "POST":
                    payload = {"mode": "balanced"}
                response = client.request(method, path, headers=headers, json=payload)
                if response.status_code != expected:
                    failed.append(f"{method} {path} expected={expected} got={response.status_code}")
                    continue
                if not str(response.headers.get("X-Amaryllis-API-Version", "")).strip():
                    failed.append(f"{method} {path} missing X-Amaryllis-API-Version header")
                if path == "/models":
                    if response.headers.get("Deprecation") != "true":
                        failed.append("GET /models missing Deprecation=true header")
            if failed:
                print("[canary] FAILED")
                for item in failed:
                    print(f"- {item}")
                print(
                    "[canary] rollback: see docs/release-playbook.md section "
                    "'Rollback Procedure' and run scripts/release/rollback_local.sh <tag>."
                )
                return 1

    print("[canary] OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # pragma: no cover - defensive
        traceback.print_exc()
        raise SystemExit(1)
