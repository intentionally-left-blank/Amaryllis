from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from storage.database import Database


class DatabasePersistenceHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-db-hardening-")
        self.base = Path(self._tmp.name)
        self.db_path = self.base / "state.db"
        self.database = Database(self.db_path)

    def tearDown(self) -> None:
        self.database.close()
        self._tmp.cleanup()

    def test_write_transaction_defers_commit_until_context_exit(self) -> None:
        with self.database.write_transaction():
            self.database.set_setting("runtime.mode", "staging")
            external = sqlite3.connect(self.db_path)
            row = external.execute(
                "SELECT value FROM settings WHERE key = ?",
                ("runtime.mode",),
            ).fetchone()
            external.close()
            self.assertIsNone(row)

        external_after = sqlite3.connect(self.db_path)
        row_after = external_after.execute(
            "SELECT value FROM settings WHERE key = ?",
            ("runtime.mode",),
        ).fetchone()
        external_after.close()
        self.assertIsNotNone(row_after)
        assert row_after is not None
        self.assertEqual(row_after[0], "staging")

    def test_write_transaction_rolls_back_on_error(self) -> None:
        self.database.set_setting("runtime.mode", "baseline")

        with self.assertRaisesRegex(RuntimeError, "forced failure"):
            with self.database.write_transaction():
                self.database.set_setting("runtime.mode", "broken")
                raise RuntimeError("forced failure")

        self.assertEqual(self.database.get_setting("runtime.mode"), "baseline")

    def test_agent_run_child_tables_cascade_on_run_delete(self) -> None:
        run_id = "run-fk-1"
        self.database.create_agent_run(
            run_id=run_id,
            agent_id="agent-1",
            user_id="user-1",
            session_id="session-1",
            input_message="hello",
            status="queued",
            max_attempts=2,
            budget={"max_attempts": 2},
        )
        self.database.upsert_agent_run_issue(
            run_id=run_id,
            issue_id="prepare_context",
            issue_order=0,
            title="Prepare Context",
            status="planned",
            depends_on=[],
            attempt_count=0,
            last_error=None,
            payload={},
            started_at=None,
            finished_at=None,
        )
        self.database.upsert_agent_run_issue_artifact(
            run_id=run_id,
            issue_id="prepare_context",
            artifact_key="result",
            artifact={"ok": True},
        )
        self.database.upsert_agent_run_tool_call(
            run_id=run_id,
            idempotency_key="tool-1",
            tool_name="filesystem.read",
            arguments={"path": "/tmp/a"},
            status="succeeded",
            result={"content": "x"},
            error_message=None,
            attempt=1,
        )

        with self.database._lock:  # noqa: SLF001
            self.database._conn.execute("DELETE FROM agent_runs WHERE id = ?", (run_id,))  # noqa: SLF001
            self.database._conn.commit()  # noqa: SLF001

        self.assertEqual(self.database.list_agent_run_issues(run_id=run_id, limit=10), [])
        self.assertEqual(
            self.database.list_agent_run_issue_artifacts(run_id=run_id, limit=10),
            [],
        )
        self.assertEqual(self.database.list_agent_run_tool_calls(run_id=run_id, limit=10), [])

    def test_automation_child_tables_cascade_on_automation_delete(self) -> None:
        automation_id = "auto-fk-1"
        self.database.create_automation(
            automation_id=automation_id,
            agent_id="agent-1",
            user_id="user-1",
            session_id="session-1",
            message="ping",
            interval_sec=60,
            next_run_at="2026-01-01T00:00:00+00:00",
            schedule_type="interval",
            schedule={"interval_sec": 60},
            timezone_name="UTC",
        )
        self.database.add_automation_event(
            automation_id=automation_id,
            event_type="created",
            message="created",
        )
        inserted = self.database.register_automation_dispatch(
            automation_id=automation_id,
            dispatch_key="manual:interval:1",
            source="manual",
            run_id=None,
        )
        self.assertTrue(inserted)

        with self.database._lock:  # noqa: SLF001
            self.database._conn.execute("DELETE FROM automations WHERE id = ?", (automation_id,))  # noqa: SLF001
            self.database._conn.commit()  # noqa: SLF001
            dispatch_rows = self.database._conn.execute(  # noqa: SLF001
                "SELECT COUNT(*) FROM automation_dispatches WHERE automation_id = ?",
                (automation_id,),
            ).fetchone()

        self.assertEqual(self.database.list_automation_events(automation_id=automation_id, limit=10), [])
        assert dispatch_rows is not None
        self.assertEqual(int(dispatch_rows[0]), 0)

    def test_agent_run_lease_claim_is_cas_and_release_requires_token(self) -> None:
        run_id = "run-lease-1"
        self.database.create_agent_run(
            run_id=run_id,
            agent_id="agent-1",
            user_id="user-1",
            session_id="session-1",
            input_message="hello",
            status="queued",
            max_attempts=2,
            budget={"max_attempts": 2},
        )
        now = datetime.now(timezone.utc)
        lease_1 = self.database.claim_agent_run_lease(
            run_id=run_id,
            lease_owner="worker-a",
            lease_token="token-a",
            lease_expires_at=(now + timedelta(seconds=60)).isoformat(),
            now_iso=now.isoformat(),
        )
        self.assertIsNotNone(lease_1)
        assert lease_1 is not None
        self.assertEqual(str(lease_1.get("lease_owner")), "worker-a")
        self.assertEqual(str(lease_1.get("lease_token")), "token-a")

        lease_2 = self.database.claim_agent_run_lease(
            run_id=run_id,
            lease_owner="worker-b",
            lease_token="token-b",
            lease_expires_at=(now + timedelta(seconds=60)).isoformat(),
            now_iso=now.isoformat(),
        )
        self.assertIsNone(lease_2)

        released_wrong = self.database.release_agent_run_lease(
            run_id=run_id,
            lease_owner="worker-a",
            lease_token="token-wrong",
        )
        self.assertFalse(released_wrong)

        released_ok = self.database.release_agent_run_lease(
            run_id=run_id,
            lease_owner="worker-a",
            lease_token="token-a",
        )
        self.assertTrue(released_ok)

        lease_3 = self.database.claim_agent_run_lease(
            run_id=run_id,
            lease_owner="worker-b",
            lease_token="token-b",
            lease_expires_at=(now + timedelta(seconds=120)).isoformat(),
            now_iso=now.isoformat(),
        )
        self.assertIsNotNone(lease_3)
        assert lease_3 is not None
        self.assertEqual(str(lease_3.get("lease_owner")), "worker-b")

    def test_agent_run_lease_can_be_reclaimed_after_expiry(self) -> None:
        run_id = "run-lease-2"
        self.database.create_agent_run(
            run_id=run_id,
            agent_id="agent-1",
            user_id="user-1",
            session_id="session-1",
            input_message="hello",
            status="queued",
            max_attempts=2,
            budget={"max_attempts": 2},
        )
        now = datetime.now(timezone.utc)
        lease_1 = self.database.claim_agent_run_lease(
            run_id=run_id,
            lease_owner="worker-a",
            lease_token="token-a",
            lease_expires_at=(now + timedelta(seconds=2)).isoformat(),
            now_iso=now.isoformat(),
        )
        self.assertIsNotNone(lease_1)

        lease_2 = self.database.claim_agent_run_lease(
            run_id=run_id,
            lease_owner="worker-b",
            lease_token="token-b",
            lease_expires_at=(now + timedelta(seconds=30)).isoformat(),
            now_iso=(now + timedelta(seconds=3)).isoformat(),
        )
        self.assertIsNotNone(lease_2)
        assert lease_2 is not None
        self.assertEqual(str(lease_2.get("lease_owner")), "worker-b")


if __name__ == "__main__":
    unittest.main()
