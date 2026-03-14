from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover - dependency may be unavailable
    TestClient = None  # type: ignore[assignment]


@unittest.skipIf(TestClient is None, "fastapi dependency is not available")
class SecurityComplianceAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-security-compliance-")
        support_dir = Path(cls._tmp.name) / "support"
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
        cls._env_patch = patch.dict(
            os.environ,
            {
                "AMARYLLIS_SUPPORT_DIR": str(support_dir),
                "AMARYLLIS_AUTH_ENABLED": "true",
                "AMARYLLIS_AUTH_TOKENS": json.dumps(auth_tokens, ensure_ascii=False),
                "AMARYLLIS_MEMORY_CONSOLIDATION_ENABLED": "false",
                "AMARYLLIS_MCP_ENDPOINTS": "",
                "AMARYLLIS_SECURITY_PROFILE": "production",
                "AMARYLLIS_TOOL_APPROVAL_ENFORCEMENT": "strict",
                "AMARYLLIS_TOOL_SANDBOX_ENABLED": "true",
                "AMARYLLIS_PLUGIN_SIGNING_MODE": "strict",
                "AMARYLLIS_PLUGIN_RUNTIME_MODE": "sandboxed",
            },
            clear=False,
        )
        cls._env_patch.start()

        import runtime.server as server_module

        cls.server_module = importlib.reload(server_module)
        cls._client_cm = TestClient(cls.server_module.app)
        cls.client = cls._client_cm.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._client_cm.__exit__(None, None, None)
        cls._env_patch.stop()
        cls._tmp.cleanup()

    @staticmethod
    def _auth(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def test_user_cannot_access_compliance_admin_endpoints(self) -> None:
        protected_paths = [
            "/security/secrets",
            "/security/access-reviews",
            "/security/incidents",
            "/security/compliance/snapshot",
        ]
        for path in protected_paths:
            response = self.client.get(path, headers=self._auth("user-token"))
            self.assertEqual(response.status_code, 403, msg=path)
            payload = response.json()
            self.assertEqual(payload["error"]["type"], "permission_denied")

    def test_admin_compliance_workflow(self) -> None:
        models = self.client.get("/models", headers=self._auth("user-token"))
        self.assertEqual(models.status_code, 200)

        sync = self.client.post("/security/secrets/sync", headers=self._auth("admin-token"))
        self.assertEqual(sync.status_code, 200)
        sync_payload = sync.json()
        self.assertGreaterEqual(int(sync_payload.get("count", 0)), 1)
        self.assertTrue(bool(sync_payload["action_receipt"].get("signature")))

        secrets = self.client.get(
            "/security/secrets",
            headers=self._auth("admin-token"),
            params={"sync_first": "true", "limit": 100},
        )
        self.assertEqual(secrets.status_code, 200)
        secrets_payload = secrets.json()
        self.assertGreaterEqual(int(secrets_payload.get("count", 0)), 1)
        self.assertIn("summary", secrets_payload)

        activity = self.client.get(
            "/security/auth/tokens/activity",
            headers=self._auth("admin-token"),
            params={"limit": 500},
        )
        self.assertEqual(activity.status_code, 200)
        activity_payload = activity.json()
        self.assertGreaterEqual(int(activity_payload.get("count", 0)), 2)
        user_items = [
            item for item in activity_payload.get("items", []) if str(item.get("user_id")) == "user-1"
        ]
        self.assertTrue(user_items)

        start_review = self.client.post(
            "/security/access-reviews/start",
            headers=self._auth("admin-token"),
            json={"summary": "ops weekly review", "stale_days": 7},
        )
        self.assertEqual(start_review.status_code, 200)
        review_id = str(start_review.json().get("review", {}).get("id") or "")
        self.assertTrue(review_id)

        complete_review = self.client.post(
            f"/security/access-reviews/{review_id}/complete",
            headers=self._auth("admin-token"),
            json={
                "summary": "review done",
                "decisions": {"remove_stale_tokens": []},
                "findings": [{"severity": "low", "message": "no blockers"}],
            },
        )
        self.assertEqual(complete_review.status_code, 200)
        completed_review = complete_review.json().get("review", {})
        self.assertEqual(str(completed_review.get("status")), "completed")

        open_incident = self.client.post(
            "/security/incidents/open",
            headers=self._auth("admin-token"),
            json={
                "category": "security",
                "severity": "medium",
                "title": "test-incident",
                "description": "incident lifecycle test",
            },
        )
        self.assertEqual(open_incident.status_code, 200)
        incident_id = str(open_incident.json().get("incident", {}).get("id") or "")
        self.assertTrue(incident_id)

        ack = self.client.post(
            f"/security/incidents/{incident_id}/ack",
            headers=self._auth("admin-token"),
            json={"owner": "secops", "note": "ack"},
        )
        self.assertEqual(ack.status_code, 200)
        self.assertEqual(str(ack.json().get("incident", {}).get("status")), "acknowledged")

        note = self.client.post(
            f"/security/incidents/{incident_id}/notes",
            headers=self._auth("admin-token"),
            json={"message": "working on it", "details": {"ticket": "SEC-1"}},
        )
        self.assertEqual(note.status_code, 200)

        resolve = self.client.post(
            f"/security/incidents/{incident_id}/resolve",
            headers=self._auth("admin-token"),
            json={
                "resolution_summary": "resolved",
                "impact": "none",
                "containment": "n/a",
                "root_cause": "test",
                "recovery_actions": "none",
            },
        )
        self.assertEqual(resolve.status_code, 200)
        self.assertEqual(str(resolve.json().get("incident", {}).get("status")), "resolved")

        snapshot = self.client.get("/security/compliance/snapshot", headers=self._auth("admin-token"))
        self.assertEqual(snapshot.status_code, 200)
        snapshot_payload = snapshot.json().get("snapshot", {})
        controls = snapshot_payload.get("controls", {})
        self.assertEqual(str(snapshot_payload.get("control_framework")), "SOC2/ISO27001 baseline")
        self.assertIn("audit_ready", controls)
        self.assertIsInstance(snapshot_payload.get("checklist", []), list)
        self.assertGreaterEqual(len(snapshot_payload.get("checklist", [])), 5)

        export = self.client.post(
            "/security/compliance/evidence/export",
            headers=self._auth("admin-token"),
            json={"output_name": "compliance-evidence-test.json", "window_days": 30, "event_limit": 500},
        )
        self.assertEqual(export.status_code, 200)
        export_result = export.json().get("result", {})
        self.assertTrue(bool(export_result.get("action_receipt", {}).get("signature")))
        evidence_path = Path(str(export_result.get("path") or "")).expanduser()
        self.assertTrue(evidence_path.exists())
        with evidence_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
        self.assertIn("snapshot", payload)
        self.assertIn("audit_events", payload)
        self.assertIn("incidents", payload)

    def test_missing_objects_return_not_found(self) -> None:
        missing_review = self.client.get(
            "/security/access-reviews/does-not-exist",
            headers=self._auth("admin-token"),
        )
        self.assertEqual(missing_review.status_code, 404)
        self.assertEqual(missing_review.json()["error"]["type"], "not_found")

        missing_incident = self.client.get(
            "/security/incidents/does-not-exist",
            headers=self._auth("admin-token"),
        )
        self.assertEqual(missing_incident.status_code, 404)
        self.assertEqual(missing_incident.json()["error"]["type"], "not_found")


if __name__ == "__main__":
    unittest.main()
