from __future__ import annotations

import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
