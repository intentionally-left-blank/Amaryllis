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
    except Exception as exc:  # pragma: no cover
        print(f"[dr-gate] fastapi testclient unavailable: {exc}")
        return 2

    with tempfile.TemporaryDirectory(prefix="amaryllis-dr-gate-") as tmp:
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
        os.environ["AMARYLLIS_BACKUP_ENABLED"] = "true"
        os.environ["AMARYLLIS_BACKUP_INTERVAL_SEC"] = "36000"
        os.environ["AMARYLLIS_BACKUP_RESTORE_DRILL_ENABLED"] = "false"

        import runtime.server as server_module

        server_module = importlib.reload(server_module)
        with TestClient(server_module.app) as client:
            created = client.post(
                "/service/backup/run",
                headers=_auth("service-token"),
                json={"trigger": "ci-dr-gate", "verify": True},
            )
            if created.status_code != 200:
                print(f"[dr-gate] FAILED create_backup status={created.status_code}")
                print(created.text)
                return 1
            created_payload = created.json()
            backup_id = str(created_payload.get("backup_id", "")).strip()
            if not backup_id:
                print("[dr-gate] FAILED missing backup_id")
                return 1
            if not bool(created_payload.get("verification", {}).get("ok")):
                print("[dr-gate] FAILED backup verification failed")
                print(json.dumps(created_payload, ensure_ascii=False, indent=2))
                return 1
            drill = client.post(
                "/service/backup/restore-drill",
                headers=_auth("service-token"),
                json={"backup_id": backup_id},
            )
            if drill.status_code != 200:
                print(f"[dr-gate] FAILED restore_drill status={drill.status_code}")
                print(drill.text)
                return 1
            drill_payload = drill.json()
            if not bool(drill_payload.get("ok")):
                print("[dr-gate] FAILED restore drill returned not ok")
                print(json.dumps(drill_payload, ensure_ascii=False, indent=2))
                return 1

    print("[dr-gate] OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # pragma: no cover
        traceback.print_exc()
        raise SystemExit(1)
