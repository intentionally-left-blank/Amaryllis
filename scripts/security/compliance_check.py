#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback
from typing import Any


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _expect(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def _shutdown_app(app: Any) -> None:
    services = getattr(getattr(app, "state", None), "services", None)
    if services is None:
        return
    try:
        services.automation_scheduler.stop()
        if services.memory_consolidation_worker is not None:
            services.memory_consolidation_worker.stop()
        if services.backup_scheduler is not None:
            services.backup_scheduler.stop()
        services.agent_run_manager.stop()
        services.database.close()
        services.vector_store.persist()
    except Exception:
        pass


def _json_or_empty(response: Any) -> dict[str, Any]:
    try:
        payload = response.json()
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def main() -> int:
    root = _project_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    try:
        from fastapi.testclient import TestClient
    except Exception as exc:  # pragma: no cover - environment dependent
        print(f"[compliance-check] fastapi test client unavailable: {exc}")
        return 2

    failures: list[str] = []

    with tempfile.TemporaryDirectory(prefix="amaryllis-compliance-check-") as tmp:
        support_dir = Path(tmp) / "support"
        auth_tokens = {
            "admin-token": {
                "user_id": "admin",
                "scopes": ["admin", "user"],
            },
            "user-token": {
                "user_id": "user-1",
                "scopes": ["user"],
            },
            "service-token": {
                "user_id": "svc-runtime",
                "scopes": ["service"],
            },
        }
        os.environ["AMARYLLIS_SUPPORT_DIR"] = str(support_dir)
        os.environ["AMARYLLIS_AUTH_ENABLED"] = "true"
        os.environ["AMARYLLIS_AUTH_TOKENS"] = json.dumps(auth_tokens, ensure_ascii=False)
        os.environ["AMARYLLIS_SECURITY_PROFILE"] = "production"
        os.environ["AMARYLLIS_MEMORY_CONSOLIDATION_ENABLED"] = "false"
        os.environ["AMARYLLIS_MCP_ENDPOINTS"] = ""
        os.environ["AMARYLLIS_TOOL_APPROVAL_ENFORCEMENT"] = "strict"
        os.environ["AMARYLLIS_PLUGIN_SIGNING_MODE"] = "strict"
        os.environ["AMARYLLIS_PLUGIN_RUNTIME_MODE"] = "sandboxed"
        os.environ["AMARYLLIS_TOOL_SANDBOX_ENABLED"] = "true"
        os.environ["AMARYLLIS_ALLOW_INSECURE_SECURITY_MODES"] = "false"

        import runtime.server as server_module

        server_module = importlib.reload(server_module)
        with TestClient(server_module.app) as client:
            blocked = client.get("/security/compliance/snapshot", headers=_auth("user-token"))
            _expect(blocked.status_code == 403, "non-admin must be blocked from /security/*", failures)

            sync = client.post("/security/secrets/sync", headers=_auth("admin-token"))
            _expect(sync.status_code == 200, "sync secrets failed", failures)
            sync_payload = _json_or_empty(sync)
            sync_items = sync_payload.get("items") if isinstance(sync_payload.get("items"), list) else []
            _expect(len(sync_items) >= 1, "secret inventory must contain at least one item", failures)
            _expect(
                bool((sync_payload.get("action_receipt") or {}).get("signature")),
                "secret sync must emit signed receipt",
                failures,
            )

            user_touch = client.get(
                "/agents",
                headers=_auth("user-token"),
                params={"user_id": "user-1", "limit": 5},
            )
            _expect(user_touch.status_code == 200, "user token baseline request failed", failures)

            start_review = client.post(
                "/security/access-reviews/start",
                headers=_auth("admin-token"),
                json={"summary": "weekly access review", "stale_days": 7},
            )
            _expect(start_review.status_code == 200, "start access review failed", failures)
            start_payload = _json_or_empty(start_review)
            review = start_payload.get("review") if isinstance(start_payload.get("review"), dict) else {}
            review_id = str(review.get("id") or "").strip()
            _expect(bool(review_id), "access review id missing", failures)

            complete_review = client.post(
                f"/security/access-reviews/{review_id}/complete",
                headers=_auth("admin-token"),
                json={
                    "summary": "review completed",
                    "decisions": {"rotate_service_tokens": False},
                    "findings": [{"severity": "low", "message": "no critical findings"}],
                },
            )
            _expect(complete_review.status_code == 200, "complete access review failed", failures)
            completed = _json_or_empty(complete_review).get("review")
            if not isinstance(completed, dict):
                completed = {}
            _expect(str(completed.get("status") or "") == "completed", "access review status must be completed", failures)

            open_incident = client.post(
                "/security/incidents/open",
                headers=_auth("admin-token"),
                json={
                    "category": "security",
                    "severity": "high",
                    "title": "test incident",
                    "description": "compliance gate incident lifecycle",
                },
            )
            _expect(open_incident.status_code == 200, "open incident failed", failures)
            incident = _json_or_empty(open_incident).get("incident")
            if not isinstance(incident, dict):
                incident = {}
            incident_id = str(incident.get("id") or "").strip()
            _expect(bool(incident_id), "incident id missing", failures)

            ack_incident = client.post(
                f"/security/incidents/{incident_id}/ack",
                headers=_auth("admin-token"),
                json={"owner": "secops", "note": "acknowledged by secops"},
            )
            _expect(ack_incident.status_code == 200, "ack incident failed", failures)

            note_incident = client.post(
                f"/security/incidents/{incident_id}/notes",
                headers=_auth("admin-token"),
                json={"message": "containment in progress", "details": {"channel": "oncall"}},
            )
            _expect(note_incident.status_code == 200, "incident note failed", failures)

            resolve_incident = client.post(
                f"/security/incidents/{incident_id}/resolve",
                headers=_auth("admin-token"),
                json={
                    "resolution_summary": "resolved by compliance check",
                    "impact": "none",
                    "containment": "n/a",
                    "root_cause": "test",
                    "recovery_actions": "none",
                },
            )
            _expect(resolve_incident.status_code == 200, "resolve incident failed", failures)

            auth_activity = client.get(
                "/security/auth/tokens/activity",
                headers=_auth("admin-token"),
                params={"limit": 500},
            )
            _expect(auth_activity.status_code == 200, "auth token activity endpoint failed", failures)
            activity_items = _json_or_empty(auth_activity).get("items")
            if not isinstance(activity_items, list):
                activity_items = []
            _expect(len(activity_items) >= 2, "expected auth activity for multiple tokens", failures)

            snapshot = client.get("/security/compliance/snapshot", headers=_auth("admin-token"))
            _expect(snapshot.status_code == 200, "compliance snapshot endpoint failed", failures)
            snapshot_payload = _json_or_empty(snapshot).get("snapshot")
            if not isinstance(snapshot_payload, dict):
                snapshot_payload = {}
            controls = snapshot_payload.get("controls") if isinstance(snapshot_payload.get("controls"), dict) else {}
            checklist = snapshot_payload.get("checklist") if isinstance(snapshot_payload.get("checklist"), list) else []
            _expect(
                str(snapshot_payload.get("control_framework") or "") == "SOC2/ISO27001 baseline",
                "snapshot must report SOC2/ISO27001 baseline",
                failures,
            )
            _expect("audit_ready" in controls, "snapshot controls must include audit_ready", failures)
            _expect(len(checklist) >= 5, "snapshot checklist must contain mapped controls", failures)

            export = client.post(
                "/security/compliance/evidence/export",
                headers=_auth("admin-token"),
                json={"window_days": 30, "event_limit": 1000},
            )
            _expect(export.status_code == 200, "evidence export failed", failures)
            export_result = _json_or_empty(export).get("result")
            if not isinstance(export_result, dict):
                export_result = {}
            evidence_path = Path(str(export_result.get("path") or ""))
            _expect(evidence_path.exists(), "evidence file was not created", failures)
            if evidence_path.exists():
                digest = hashlib.sha256(evidence_path.read_bytes()).hexdigest()
                _expect(
                    digest == str(export_result.get("sha256") or ""),
                    "evidence digest mismatch",
                    failures,
                )
                try:
                    evidence_payload = json.loads(evidence_path.read_text(encoding="utf-8"))
                except Exception:
                    evidence_payload = {}
                _expect("snapshot" in evidence_payload, "evidence payload must include snapshot", failures)
                _expect("audit_events" in evidence_payload, "evidence payload must include audit_events", failures)
                _expect("incidents" in evidence_payload, "evidence payload must include incidents", failures)

            audit = client.get(
                "/security/audit",
                headers=_auth("admin-token"),
                params={"action": "security_evidence_export", "limit": 50},
            )
            _expect(audit.status_code == 200, "security audit endpoint failed", failures)
            audit_items = _json_or_empty(audit).get("items")
            if not isinstance(audit_items, list):
                audit_items = []
            _expect(any(str(item.get("action")) == "security_evidence_export" for item in audit_items), "audit trail missing evidence export action", failures)

        _shutdown_app(server_module.app)

    if failures:
        print("[compliance-check] FAILED")
        for item in failures:
            print(f"- {item}")
        return 1

    print("[compliance-check] OK")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:  # pragma: no cover - defensive
        traceback.print_exc()
        raise SystemExit(1)
