from __future__ import annotations

import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agents.agent import Agent
from agents.agent_run_manager import AgentRunManager
from automation.automation_scheduler import AutomationScheduler
from storage.database import Database
from storage.vector_store import VectorStore


class _FakeTaskExecutor:
    def execute(
        self,
        agent: Agent,
        user_id: str,
        session_id: str | None,
        user_message: str,
        checkpoint: dict | None = None,
    ) -> dict:
        return {
            "agent_id": agent.id,
            "user_id": user_id,
            "session_id": session_id,
            "response": f"ok:{user_message}",
        }


class _AlwaysBlockedCircuitBreaker:
    def snapshot(self) -> dict:
        return {
            "status": "armed",
            "armed": True,
            "reason": "incident-response",
            "active_scope_count": 1,
            "active_scopes": [
                {
                    "scope_type": "global",
                    "reason": "incident-response",
                }
            ],
        }

    def evaluate_run_creation(
        self,
        *,
        user_id: str | None,
        agent_id: str | None,
    ) -> dict:
        return {
            "blocked": True,
            "matched_scope_count": 1,
            "matched_scopes": [
                {
                    "scope_type": "global",
                    "scope_user_id": None,
                    "scope_agent_id": None,
                    "reason": "incident-response",
                }
            ],
            "active_scope_count": 1,
            "revision": 1,
        }


class AutomationSchedulerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-automation-")
        self.base = Path(self._tmp.name)
        self.database = Database(self.base / "state.db")
        self.vector = VectorStore(self.base / "vectors.faiss")

        self.run_manager = AgentRunManager(
            database=self.database,
            task_executor=_FakeTaskExecutor(),  # type: ignore[arg-type]
            worker_count=1,
            default_max_attempts=2,
        )
        self.scheduler = AutomationScheduler(
            database=self.database,
            run_manager=self.run_manager,
            poll_interval_sec=0.5,
            batch_size=10,
            escalation_warning_threshold=1,
            escalation_critical_threshold=2,
            escalation_disable_threshold=3,
        )

        self.agent = Agent.create(
            name="Automation Agent",
            system_prompt="automation",
            model=None,
            tools=[],
            user_id="user-1",
        )
        self.database.upsert_agent(self.agent.to_record())

    def tearDown(self) -> None:
        self.scheduler.stop()
        self.run_manager.stop()
        self.database.close()
        self._tmp.cleanup()

    def test_run_now_queues_agent_run(self) -> None:
        automation = self.scheduler.create_automation(
            agent_id=self.agent.id,
            user_id="user-1",
            session_id="session-1",
            message="daily check",
            interval_sec=60,
            start_immediately=False,
        )

        updated = self.scheduler.run_now(automation["id"])
        self.assertEqual(updated["id"], automation["id"])
        self.assertIsNotNone(updated.get("last_run_at"))
        self.assertIsNone(updated.get("last_error"))

        runs = self.database.list_agent_runs(user_id="user-1", limit=10)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "queued")
        self.assertEqual(runs[0]["input_message"], "daily check")

        events = self.scheduler.list_events(automation["id"], limit=20)
        self.assertTrue(any(item["event_type"] == "run_queued" for item in events))

    def test_create_automation_rejects_cross_user_agent_access(self) -> None:
        with self.assertRaisesRegex(ValueError, "ownership mismatch"):
            self.scheduler.create_automation(
                agent_id=self.agent.id,
                user_id="user-2",
                session_id=None,
                message="cross-user automation",
                interval_sec=60,
                start_immediately=False,
            )

    def test_tick_processes_due_automation(self) -> None:
        automation = self.scheduler.create_automation(
            agent_id=self.agent.id,
            user_id="user-1",
            session_id=None,
            message="heartbeat",
            interval_sec=30,
            start_immediately=True,
        )

        # Trigger one scheduler cycle without waiting for thread loop.
        self.scheduler._tick()  # noqa: SLF001

        runs = self.database.list_agent_runs(user_id="user-1", limit=10)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["input_message"], "heartbeat")

        row = self.database.get_automation(automation["id"])
        self.assertIsNotNone(row)
        assert row is not None
        self.assertIsNotNone(row.get("last_run_at"))
        self.assertIsNone(row.get("last_error"))

    def test_watch_fs_triggers_only_on_file_change(self) -> None:
        watch_dir = self.base / "watch"
        watch_dir.mkdir(parents=True, exist_ok=True)
        watched_file = watch_dir / "notes.txt"
        watched_file.write_text("v1", encoding="utf-8")

        automation = self.scheduler.create_automation(
            agent_id=self.agent.id,
            user_id="user-1",
            session_id="session-watch",
            message="watch changes",
            schedule_type="watch_fs",
            schedule={
                "path": str(watch_dir),
                "poll_sec": 2,
                "recursive": False,
                "glob": "*.txt",
                "max_changed_files": 10,
            },
            start_immediately=True,
        )

        self.scheduler._tick()  # noqa: SLF001
        runs = self.database.list_agent_runs(user_id="user-1", limit=20)
        self.assertEqual(len(runs), 0)

        time.sleep(0.01)
        watched_file.write_text("v2", encoding="utf-8")
        self.database.update_automation_fields(automation["id"], next_run_at="1970-01-01T00:00:00+00:00")

        self.scheduler._tick()  # noqa: SLF001
        runs_after_change = self.database.list_agent_runs(user_id="user-1", limit=20)
        self.assertEqual(len(runs_after_change), 1)
        self.assertIn("Watcher detected file changes", runs_after_change[0]["input_message"])
        self.assertIn("- notes.txt", runs_after_change[0]["input_message"])

        inbox = self.scheduler.list_inbox_items(user_id="user-1", limit=20)
        self.assertTrue(any(item["title"] == "Automation watcher triggered" for item in inbox))

    def test_escalation_policy_creates_inbox_and_disables(self) -> None:
        automation = self.scheduler.create_automation(
            agent_id=self.agent.id,
            user_id="user-1",
            session_id=None,
            message="will fail",
            interval_sec=60,
            start_immediately=False,
        )
        self.database.delete_agent(self.agent.id)

        for _ in range(3):
            with self.assertRaises(ValueError):
                self.scheduler.run_now(automation["id"])

        row = self.database.get_automation(automation["id"])
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["consecutive_failures"], 3)
        self.assertEqual(row["escalation_level"], "critical")
        self.assertFalse(row["is_enabled"])
        self.assertIsNotNone(row["last_error"])

        inbox = self.scheduler.list_inbox_items(user_id="user-1", limit=20)
        self.assertGreaterEqual(len(inbox), 3)
        titles = [item["title"] for item in inbox]
        self.assertIn("Automation warning", titles)
        self.assertIn("Automation in critical failure state", titles)
        self.assertIn("Automation disabled after failures", titles)

    def test_mission_policy_overlay_enforces_custom_failure_thresholds(self) -> None:
        automation = self.scheduler.create_automation(
            agent_id=self.agent.id,
            user_id="user-1",
            session_id=None,
            message="policy controlled",
            interval_sec=60,
            start_immediately=False,
            mission_policy={
                "profile": "balanced",
                "slo": {
                    "warning_failures": 2,
                    "critical_failures": 3,
                    "disable_failures": 4,
                    "backoff_base_sec": 1,
                    "backoff_max_sec": 1,
                    "circuit_failure_threshold": 4,
                    "circuit_open_sec": 1,
                },
            },
        )
        self.database.delete_agent(self.agent.id)

        for index in range(1, 5):
            with self.assertRaises(ValueError):
                self.scheduler.run_now(automation["id"])
            row = self.database.get_automation(automation["id"])
            self.assertIsNotNone(row)
            assert row is not None
            self.assertEqual(int(row["consecutive_failures"]), index)
            if index == 1:
                self.assertEqual(str(row["escalation_level"]), "none")
                self.assertTrue(bool(row["is_enabled"]))
            if index == 2:
                self.assertEqual(str(row["escalation_level"]), "warning")
                self.assertTrue(bool(row["is_enabled"]))
            if index == 3:
                self.assertEqual(str(row["escalation_level"]), "critical")
                self.assertTrue(bool(row["is_enabled"]))
            if index == 4:
                self.assertEqual(str(row["escalation_level"]), "critical")
                self.assertFalse(bool(row["is_enabled"]))

    def test_health_snapshot_reports_mission_policy_profiles(self) -> None:
        strict = self.scheduler.create_automation(
            agent_id=self.agent.id,
            user_id="user-1",
            session_id="strict",
            message="strict mission",
            interval_sec=60,
            start_immediately=False,
            mission_policy={"profile": "strict"},
        )
        balanced = self.scheduler.create_automation(
            agent_id=self.agent.id,
            user_id="user-1",
            session_id="balanced",
            message="balanced mission",
            interval_sec=60,
            start_immediately=False,
        )
        self.assertIsNotNone(strict.get("mission_policy"))
        self.assertIsNotNone(balanced.get("mission_policy"))

        health = self.scheduler.health_snapshot(user_id="user-1", limit=50)
        scheduler_block = health.get("scheduler", {})
        self.assertIsInstance(scheduler_block, dict)
        assert isinstance(scheduler_block, dict)
        profiles = scheduler_block.get("mission_policy_profiles", {})
        self.assertIsInstance(profiles, dict)
        assert isinstance(profiles, dict)
        self.assertGreaterEqual(int(profiles.get("strict", 0)), 1)

    def test_mark_inbox_item_read_and_unread(self) -> None:
        item = self.database.add_inbox_item(
            user_id="user-1",
            category="automation",
            severity="info",
            title="Test item",
            body="Body",
            source_type="automation",
            source_id="automation-1",
            metadata={"x": 1},
            requires_action=False,
        )
        self.assertFalse(item["is_read"])

        read_item = self.scheduler.set_inbox_item_read(item["id"], is_read=True)
        self.assertTrue(read_item["is_read"])

        unread_item = self.scheduler.set_inbox_item_read(item["id"], is_read=False)
        self.assertFalse(unread_item["is_read"])

    def test_dispatch_dedup_skips_duplicate_slot(self) -> None:
        automation = self.scheduler.create_automation(
            agent_id=self.agent.id,
            user_id="user-1",
            session_id=None,
            message="dedup check",
            interval_sec=60,
            start_immediately=False,
        )
        self.database.update_automation_fields(
            automation["id"],
            next_run_at="1970-01-01T00:00:00+00:00",
        )

        row = self.database.get_automation(automation["id"])
        self.assertIsNotNone(row)
        assert row is not None

        self.scheduler._trigger(row, source="scheduled")  # noqa: SLF001
        self.scheduler._trigger(row, source="scheduled")  # noqa: SLF001

        runs = self.database.list_agent_runs(user_id="user-1", limit=20)
        self.assertEqual(len(runs), 1)

        events = self.scheduler.list_events(automation["id"], limit=50)
        event_types = [item["event_type"] for item in events]
        self.assertIn("run_queued", event_types)
        self.assertIn("run_deduplicated", event_types)

    def test_tick_recovers_after_stale_lease(self) -> None:
        automation = self.scheduler.create_automation(
            agent_id=self.agent.id,
            user_id="user-1",
            session_id=None,
            message="lease recovery",
            interval_sec=30,
            start_immediately=True,
        )

        now = datetime.now(timezone.utc)
        self.database.update_automation_fields(
            automation["id"],
            next_run_at="1970-01-01T00:00:00+00:00",
            lease_owner="dead-scheduler",
            lease_expires_at=(now + timedelta(minutes=5)).isoformat(),
        )
        self.scheduler._tick()  # noqa: SLF001
        runs_initial = self.database.list_agent_runs(user_id="user-1", limit=20)
        self.assertEqual(len(runs_initial), 0)

        self.database.update_automation_fields(
            automation["id"],
            next_run_at="1970-01-01T00:00:00+00:00",
            lease_expires_at=(now - timedelta(minutes=5)).isoformat(),
        )
        self.scheduler._tick()  # noqa: SLF001
        runs_after_recovery = self.database.list_agent_runs(user_id="user-1", limit=20)
        self.assertEqual(len(runs_after_recovery), 1)

    def test_backoff_and_circuit_open_pause_scheduled_runs(self) -> None:
        self.scheduler.circuit_failure_threshold = 1
        self.scheduler.backoff_base_sec = 60.0
        self.scheduler.backoff_max_sec = 60.0
        self.scheduler.circuit_open_sec = 120.0

        automation = self.scheduler.create_automation(
            agent_id=self.agent.id,
            user_id="user-1",
            session_id=None,
            message="broken",
            interval_sec=30,
            start_immediately=True,
        )
        self.database.delete_agent(self.agent.id)
        self.database.update_automation_fields(
            automation["id"],
            next_run_at="1970-01-01T00:00:00+00:00",
        )

        self.scheduler._tick()  # noqa: SLF001
        row = self.database.get_automation(automation["id"])
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(int(row["consecutive_failures"]), 1)
        self.assertIsNotNone(row.get("backoff_until"))
        self.assertIsNotNone(row.get("circuit_open_until"))

        self.database.update_automation_fields(
            automation["id"],
            next_run_at="1970-01-01T00:00:00+00:00",
            backoff_until="1970-01-01T00:00:00+00:00",
        )
        self.scheduler._tick()  # noqa: SLF001
        row_during_circuit = self.database.get_automation(automation["id"])
        self.assertIsNotNone(row_during_circuit)
        assert row_during_circuit is not None
        self.assertEqual(int(row_during_circuit["consecutive_failures"]), 1)

        self.database.update_automation_fields(
            automation["id"],
            next_run_at="1970-01-01T00:00:00+00:00",
            backoff_until="1970-01-01T00:00:00+00:00",
            circuit_open_until="1970-01-01T00:00:00+00:00",
        )
        self.scheduler._tick()  # noqa: SLF001
        row_after_open = self.database.get_automation(automation["id"])
        self.assertIsNotNone(row_after_open)
        assert row_after_open is not None
        self.assertEqual(int(row_after_open["consecutive_failures"]), 2)

    def test_health_snapshot_contains_reliability_fields(self) -> None:
        ok_automation = self.scheduler.create_automation(
            agent_id=self.agent.id,
            user_id="user-1",
            session_id="session-ok",
            message="ok run",
            interval_sec=30,
            start_immediately=True,
        )
        fail_agent = Agent.create(
            name="Automation Fail Agent",
            system_prompt="automation fail",
            model=None,
            tools=[],
            user_id="user-1",
        )
        self.database.upsert_agent(fail_agent.to_record())
        failed_automation = self.scheduler.create_automation(
            agent_id=fail_agent.id,
            user_id="user-1",
            session_id="session-fail",
            message="fail run",
            interval_sec=30,
            start_immediately=True,
        )
        self.database.delete_agent(fail_agent.id)
        self.database.update_automation_fields(
            ok_automation["id"],
            next_run_at="1970-01-01T00:00:00+00:00",
        )
        self.database.update_automation_fields(
            failed_automation["id"],
            next_run_at="1970-01-01T00:00:00+00:00",
        )

        self.scheduler._tick()  # noqa: SLF001

        health = self.scheduler.health_snapshot(user_id="user-1", limit=200)
        self.assertEqual(int(health["total_automations"]), 2)
        self.assertIn("runtime_state", health)
        self.assertIn("slo", health)
        self.assertIn("recent_events", health)
        self.assertIn("scheduler", health)
        self.assertGreaterEqual(int(health["recent_events"]["count"]), 2)
        self.assertGreaterEqual(int(health["slo"]["sample_size"]), 1)
        self.assertIsInstance(health.get("top_failures"), list)

    def test_breaker_blocked_run_now_does_not_increment_failures(self) -> None:
        self.run_manager.autonomy_circuit_breaker = _AlwaysBlockedCircuitBreaker()
        automation = self.scheduler.create_automation(
            agent_id=self.agent.id,
            user_id="user-1",
            session_id="session-breaker",
            message="blocked by breaker",
            interval_sec=60,
            start_immediately=False,
        )

        updated = self.scheduler.run_now(automation["id"])
        self.assertEqual(str(updated["id"]), str(automation["id"]))
        self.assertEqual(int(updated.get("consecutive_failures", 0)), 0)
        self.assertEqual(str(updated.get("escalation_level") or "none"), "none")
        self.assertTrue(bool(updated.get("is_enabled", False)))
        self.assertIsNone(updated.get("last_error"))

        runs = self.database.list_agent_runs(user_id="user-1", limit=10)
        self.assertEqual(len(runs), 0)

        events = self.scheduler.list_events(automation["id"], limit=50)
        event_types = [str(item.get("event_type") or "") for item in events]
        self.assertIn("run_blocked_autonomy_circuit_breaker", event_types)

        health = self.scheduler.health_snapshot(user_id="user-1", limit=50)
        self.assertEqual(
            int(health.get("slo", {}).get("run_blocked_by_autonomy_circuit_breaker", 0)),
            1,
        )


if __name__ == "__main__":
    unittest.main()
