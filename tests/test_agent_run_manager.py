from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

from agents.agent import Agent
from agents.agent_run_manager import AgentRunManager
from storage.database import Database
from storage.vector_store import VectorStore


class _FakeTaskExecutor:
    def __init__(
        self,
        fail_first: bool = False,
        always_fail: bool = False,
        fail_once_after_prepare: bool = False,
    ) -> None:
        self.fail_first = fail_first
        self.always_fail = always_fail
        self.fail_once_after_prepare = fail_once_after_prepare
        self.call_count = 0
        self.last_resume_state: dict[str, Any] | None = None

    def execute(
        self,
        agent: Agent,
        user_id: str,
        session_id: str | None,
        user_message: str,
        checkpoint: Any = None,
        run_deadline_monotonic: float | None = None,  # noqa: ARG002
        resume_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.call_count += 1
        self.last_resume_state = resume_state

        completed_steps = set()
        if isinstance(resume_state, dict):
            raw = resume_state.get("completed_steps")
            if isinstance(raw, list):
                completed_steps = {str(item) for item in raw}

        if "prepare_context" not in completed_steps and callable(checkpoint):
            checkpoint(
                {
                    "stage": "step_completed",
                    "step": "prepare_context",
                    "resume_state": {
                        "completed_steps": ["prepare_context"],
                        "strategy": "simple",
                        "plan": [{"id": 1, "description": "prepare"}],
                    },
                }
            )
            completed_steps.add("prepare_context")

        if self.fail_once_after_prepare and self.call_count == 1:
            raise RuntimeError("fail after prepare")

        if self.always_fail:
            raise RuntimeError("forced failure")
        if self.fail_first and self.call_count == 1:
            raise RuntimeError("fail first attempt")

        if "reasoning" not in completed_steps and callable(checkpoint):
            checkpoint(
                {
                    "stage": "step_completed",
                    "step": "reasoning",
                    "resume_state": {
                        "completed_steps": ["prepare_context", "reasoning"],
                        "strategy": "simple",
                        "plan": [{"id": 1, "description": "prepare"}],
                        "response_text": f"ok:{user_message}",
                        "provider": "fake",
                        "model": "fake-model",
                        "tool_events": [],
                        "model_calls": 1,
                        "tool_rounds": 0,
                    },
                }
            )

        if callable(checkpoint):
            checkpoint(
                {
                    "stage": "fake_executor",
                    "message": "Fake executor produced response.",
                }
            )
        return {
            "agent_id": agent.id,
            "user_id": user_id,
            "session_id": session_id,
            "response": f"ok:{user_message}",
        }


class AgentRunManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-runs-")
        self.base = Path(self._tmp.name)
        self.database = Database(self.base / "state.db")
        self.vector = VectorStore(self.base / "vectors.faiss")
        self.executor = _FakeTaskExecutor()
        self.manager = AgentRunManager(
            database=self.database,
            task_executor=self.executor,  # type: ignore[arg-type]
            worker_count=1,
            default_max_attempts=2,
        )

        self.agent = Agent.create(
            name="Run Test Agent",
            system_prompt="Test prompt",
            model=None,
            tools=[],
            user_id="user-1",
        )
        self.database.upsert_agent(self.agent.to_record())

    def tearDown(self) -> None:
        self.manager.stop()
        self.database.close()
        self._tmp.cleanup()

    def test_run_succeeds_and_records_checkpoints(self) -> None:
        self.manager.start()
        run = self.manager.create_run(
            agent=self.agent,
            user_id="user-1",
            session_id="session-1",
            user_message="hello",
            max_attempts=2,
        )

        final = self._wait_for_status(run["id"], {"succeeded"})
        self.assertIsNotNone(final)
        assert final is not None
        self.assertEqual(final["status"], "succeeded")
        self.assertEqual(final["attempts"], 1)
        self.assertIsInstance(final["result"], dict)
        stages = {item.get("stage") for item in final["checkpoints"]}
        self.assertIn("queued", stages)
        self.assertIn("running", stages)
        self.assertIn("fake_executor", stages)
        self.assertIn("succeeded", stages)

    def test_run_retries_then_succeeds(self) -> None:
        self.executor.fail_first = True
        self.manager.start()
        run = self.manager.create_run(
            agent=self.agent,
            user_id="user-1",
            session_id="session-1",
            user_message="retry me",
            max_attempts=2,
        )

        final = self._wait_for_status(run["id"], {"succeeded"})
        self.assertIsNotNone(final)
        assert final is not None
        self.assertEqual(final["status"], "succeeded")
        self.assertEqual(final["attempts"], 2)
        stages = [item.get("stage") for item in final["checkpoints"]]
        self.assertIn("retry_scheduled", stages)

    def test_cancel_queued_run_without_workers(self) -> None:
        run = self.manager.create_run(
            agent=self.agent,
            user_id="user-1",
            session_id=None,
            user_message="cancel me",
            max_attempts=1,
        )

        canceled = self.manager.cancel_run(run["id"])
        self.assertEqual(canceled["status"], "canceled")
        self.assertEqual(canceled["cancel_requested"], 1)

    def test_resume_failed_run(self) -> None:
        self.executor.always_fail = True
        self.manager.start()
        run = self.manager.create_run(
            agent=self.agent,
            user_id="user-1",
            session_id=None,
            user_message="fail then resume",
            max_attempts=1,
        )

        failed = self._wait_for_status(run["id"], {"failed"})
        self.assertIsNotNone(failed)
        assert failed is not None
        self.assertEqual(failed["status"], "failed")

        self.executor.always_fail = False
        resumed = self.manager.resume_run(run["id"])
        self.assertEqual(resumed["status"], "queued")

        final = self._wait_for_status(run["id"], {"succeeded"})
        self.assertIsNotNone(final)
        assert final is not None
        self.assertEqual(final["status"], "succeeded")

    def test_resume_uses_checkpoint_resume_state(self) -> None:
        self.executor.fail_once_after_prepare = True
        self.manager.start()
        run = self.manager.create_run(
            agent=self.agent,
            user_id="user-1",
            session_id="session-1",
            user_message="resume from step",
            max_attempts=1,
        )

        failed = self._wait_for_status(run["id"], {"failed"})
        self.assertIsNotNone(failed)
        assert failed is not None
        self.assertEqual(failed["status"], "failed")

        resumed = self.manager.resume_run(run["id"])
        self.assertEqual(resumed["status"], "queued")

        final = self._wait_for_status(run["id"], {"succeeded"})
        self.assertIsNotNone(final)
        assert final is not None
        self.assertEqual(final["status"], "succeeded")
        self.assertGreaterEqual(self.executor.call_count, 2)
        self.assertIsNotNone(self.executor.last_resume_state)
        assert isinstance(self.executor.last_resume_state, dict)
        completed = self.executor.last_resume_state.get("completed_steps", [])
        self.assertIn("prepare_context", completed)

    def test_replay_returns_timeline_and_attempt_summary(self) -> None:
        self.manager.start()
        run = self.manager.create_run(
            agent=self.agent,
            user_id="user-1",
            session_id="session-1",
            user_message="replay me",
            max_attempts=2,
        )
        final = self._wait_for_status(run["id"], {"succeeded"})
        self.assertIsNotNone(final)

        replay = self.manager.replay_run(run["id"])
        self.assertEqual(replay["run_id"], run["id"])
        self.assertEqual(replay["status"], "succeeded")
        self.assertGreaterEqual(int(replay["checkpoint_count"]), 4)

        timeline = replay["timeline"]
        self.assertIsInstance(timeline, list)
        stages = [str(item.get("stage")) for item in timeline]
        self.assertIn("queued", stages)
        self.assertIn("running", stages)
        self.assertIn("succeeded", stages)

        summary = replay["attempt_summary"]
        self.assertIsInstance(summary, list)
        self.assertGreaterEqual(len(summary), 1)
        first = summary[0]
        self.assertEqual(first.get("attempt"), 1)
        stage_counts = first.get("stage_counts")
        self.assertIsInstance(stage_counts, dict)
        assert isinstance(stage_counts, dict)
        self.assertGreaterEqual(int(stage_counts.get("running", 0)), 1)
        self.assertGreaterEqual(int(stage_counts.get("succeeded", 0)), 1)

        latest_resume_state = replay.get("latest_resume_state")
        self.assertIsInstance(latest_resume_state, dict)
        assert isinstance(latest_resume_state, dict)
        completed_steps = latest_resume_state.get("completed_steps", [])
        self.assertIn("prepare_context", completed_steps)
        self.assertIn("reasoning", completed_steps)

    def test_replay_missing_run_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.manager.replay_run("missing-run-id")

    def _wait_for_status(
        self,
        run_id: str,
        statuses: set[str],
        timeout_sec: float = 4.0,
    ) -> dict[str, Any] | None:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            run = self.manager.get_run(run_id)
            if run and str(run.get("status")) in statuses:
                return run
            time.sleep(0.05)
        return self.manager.get_run(run_id)


if __name__ == "__main__":
    unittest.main()
