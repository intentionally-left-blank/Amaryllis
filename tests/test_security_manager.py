from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from runtime.security import LocalIdentityManager, SecurityManager
from storage.database import Database


class SecurityManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-security-")
        self.base = Path(self._tmp.name)
        self.database = Database(self.base / "state.db")
        self.identity = LocalIdentityManager(self.base / "identity.json")
        self.security = SecurityManager(
            identity_manager=self.identity,
            database=self.database,
            telemetry=None,
        )

    def tearDown(self) -> None:
        self.database.close()
        self._tmp.cleanup()

    def test_signed_action_creates_auditable_receipt(self) -> None:
        payload = {
            "automation_id": "auto-1",
            "operation": "pause",
        }
        receipt = self.security.signed_action(
            action="automation_pause",
            payload=payload,
            request_id="req-123",
            actor="user-1",
            target_type="automation",
            target_id="auto-1",
        )

        self.assertEqual(receipt["request_id"], "req-123")
        self.assertEqual(receipt["action"], "automation_pause")
        self.assertTrue(receipt.get("signature"))
        self.assertTrue(self.identity.verify(receipt, payload))

        events = self.security.list_audit_events(limit=10)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action"], "automation_pause")
        self.assertEqual(events[0]["actor"], "user-1")
        self.assertEqual(events[0]["status"], "succeeded")

    def test_identity_is_persistent_between_manager_instances(self) -> None:
        first_info = self.identity.info()
        reloaded = LocalIdentityManager(self.base / "identity.json")
        second_info = reloaded.info()

        self.assertEqual(first_info["key_id"], second_info["key_id"])
        self.assertEqual(first_info["fingerprint"], second_info["fingerprint"])

    def test_identity_rotation_changes_active_key_and_keeps_old_receipt_verifiable(self) -> None:
        payload = {"operation": "before_rotation"}
        receipt_before = self.security.signed_action(
            action="security_check_before_rotation",
            payload=payload,
            request_id="req-before",
            actor="admin",
            target_type="security",
            target_id="identity",
        )
        self.assertTrue(self.identity.verify(receipt_before, payload))

        rotated = self.security.rotate_identity(
            actor="admin",
            request_id="req-rotate",
            reason="scheduled_rotation",
        )
        rotation = rotated["rotation"]
        before_key = str(rotation["previous"]["key_id"])
        after_key = str(rotation["current"]["key_id"])
        self.assertNotEqual(before_key, after_key)
        self.assertGreaterEqual(int(rotation.get("history_count", 0)), 1)
        self.assertTrue(bool(rotated["action_receipt"].get("signature")))

        self.assertTrue(self.identity.verify(receipt_before, payload))

        events = self.security.list_audit_events(limit=20)
        event_types = [str(item.get("event_type")) for item in events]
        self.assertIn("identity_rotation", event_types)

    def test_access_denied_audit_event_is_written(self) -> None:
        self.security.audit_access_denied(
            denial_type="permission_denied",
            request_id="req-deny",
            actor="user-1",
            path="/debug/models/failover",
            method="GET",
            message="Admin scope is required",
            status_code=403,
            scopes=["user"],
        )
        events = self.security.list_audit_events(limit=10)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event_type"], "authz_deny")
        self.assertEqual(events[0]["status"], "failed")
        self.assertEqual(events[0]["target_id"], "/debug/models/failover")


if __name__ == "__main__":
    unittest.main()
