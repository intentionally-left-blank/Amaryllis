from __future__ import annotations

import tempfile
import time
from threading import Lock
import unittest
from pathlib import Path
from typing import Any

from agents.agent import Agent
from agents.agent_run_manager import AgentRunManager
from models.provider_errors import ProviderErrorInfo, ProviderOperationError
from storage.database import Database
from storage.vector_store import VectorStore


class _FakeTaskExecutor:
    def __init__(
        self,
        fail_first: bool = False,
        always_fail: bool = False,
        fail_once_after_prepare: bool = False,
        fail_once_after_tool_record: bool = False,
        error_sequence: list[Exception] | None = None,
        emit_tool_finished_count: int = 0,
        emit_tool_error_count: int = 0,
        emit_tool_call_record: bool = False,
    ) -> None:
        self.fail_first = fail_first
        self.always_fail = always_fail
        self.fail_once_after_prepare = fail_once_after_prepare
        self.fail_once_after_tool_record = fail_once_after_tool_record
        self.error_sequence = list(error_sequence or [])
        self.emit_tool_finished_count = max(0, int(emit_tool_finished_count))
        self.emit_tool_error_count = max(0, int(emit_tool_error_count))
        self.emit_tool_call_record = bool(emit_tool_call_record)
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
            checkpoint(
                {
                    "stage": "issue_artifact",
                    "issue_id": "prepare_context",
                    "artifact_key": "result",
                    "artifact": {
                        "prepared": True,
                        "user_message": user_message,
                    },
                }
            )
            completed_steps.add("prepare_context")

        if self.call_count <= len(self.error_sequence):
            raise self.error_sequence[self.call_count - 1]

        if self.fail_once_after_prepare and self.call_count == 1:
            raise RuntimeError("fail after prepare")

        if self.always_fail:
            raise RuntimeError("forced failure")
        if self.fail_first and self.call_count == 1:
            raise RuntimeError("timeout on first attempt")

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

        if callable(checkpoint) and self.emit_tool_call_record:
            checkpoint(
                {
                    "stage": "tool_call_recorded",
                    "tool": "demo_tool",
                    "idempotency_key": "demo-key-1",
                    "status": "succeeded",
                    "arguments": {"query": user_message},
                    "result": {
                        "tool": "demo_tool",
                        "result": {"ok": True, "query": user_message},
                    },
                    "cached": False,
                    "executed": True,
                }
            )
        if self.fail_once_after_tool_record and self.call_count == 1:
            raise RuntimeError("fail after tool record")

        if callable(checkpoint):
            for idx in range(self.emit_tool_finished_count):
                status = "failed" if idx < self.emit_tool_error_count else "succeeded"
                checkpoint(
                    {
                        "stage": "tool_call_finished",
                        "status": status,
                        "duration_ms": 12.0,
                    }
                )

        if callable(checkpoint):
            checkpoint(
                {
                    "stage": "fake_executor",
                    "message": "Fake executor produced response.",
                    "estimated_tokens_total": 120,
                }
            )
        return {
            "agent_id": agent.id,
            "user_id": user_id,
            "session_id": session_id,
            "response": f"ok:{user_message}",
            "metrics": {
                "model_calls": 1,
                "tool_calls": self.emit_tool_finished_count,
                "tool_errors": self.emit_tool_error_count,
                "estimated_tokens": 120,
                "attempt_count": 1,
                "duration_ms": 20.0,
                "total_attempt_duration_ms": 20.0,
            },
        }


class _SlowSideEffectTaskExecutor:
    def __init__(self, sleep_sec: float = 0.12) -> None:
        self.sleep_sec = max(0.01, float(sleep_sec))
        self.call_count = 0
        self.side_effect_count = 0
        self._lock = Lock()

    def execute(
        self,
        agent: Agent,
        user_id: str,
        session_id: str | None,
        user_message: str,
        checkpoint: Any = None,
        run_deadline_monotonic: float | None = None,  # noqa: ARG002
        resume_state: dict[str, Any] | None = None,  # noqa: ARG002
    ) -> dict[str, Any]:
        with self._lock:
            self.call_count += 1
            self.side_effect_count += 1
        if callable(checkpoint):
            checkpoint(
                {
                    "stage": "tool_call_recorded",
                    "tool": "demo_tool",
                    "idempotency_key": "side-effect:1",
                    "status": "succeeded",
                    "arguments": {"message": user_message},
                    "result": {"ok": True},
                    "cached": False,
                    "executed": True,
                }
            )
        time.sleep(self.sleep_sec)
        return {
            "agent_id": agent.id,
            "user_id": user_id,
            "session_id": session_id,
            "response": f"ok:{user_message}",
            "metrics": {
                "model_calls": 1,
                "tool_calls": 1,
                "tool_errors": 0,
                "estimated_tokens": 64,
                "attempt_count": 1,
                "duration_ms": 50.0,
                "total_attempt_duration_ms": 50.0,
            },
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
        self.assertEqual(final.get("stop_reason"), "completed")
        self.assertEqual(final["attempts"], 1)
        self.assertIsInstance(final["result"], dict)
        stages = {item.get("stage") for item in final["checkpoints"]}
        self.assertIn("queued", stages)
        self.assertIn("running", stages)
        self.assertIn("fake_executor", stages)
        self.assertIn("succeeded", stages)
        issues = final.get("issues", [])
        self.assertIsInstance(issues, list)
        self.assertGreaterEqual(len(issues), 3)
        issue_statuses = {str(item.get("status")) for item in issues}
        self.assertEqual(issue_statuses, {"done"})

    def test_create_run_rejects_cross_user_agent_access(self) -> None:
        with self.assertRaisesRegex(ValueError, "ownership mismatch"):
            self.manager.create_run(
                agent=self.agent,
                user_id="user-2",
                session_id=None,
                user_message="cross-user run",
            )

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
        self.assertEqual(canceled.get("stop_reason"), "canceled_by_user")
        self.assertEqual(canceled.get("failure_class"), "canceled")

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
        issues = final.get("issues", [])
        self.assertTrue(any(item.get("issue_id") == "prepare_context" for item in issues))
        self.assertTrue(any(item.get("issue_id") == "reasoning" for item in issues))

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
        issue_summary = replay.get("issue_summary", {})
        self.assertIsInstance(issue_summary, dict)
        status_breakdown = issue_summary.get("status_breakdown", {})
        self.assertIsInstance(status_breakdown, dict)
        self.assertGreaterEqual(int(status_breakdown.get("done", 0)), 1)

    def test_list_run_issues_returns_persisted_states(self) -> None:
        self.manager.start()
        run = self.manager.create_run(
            agent=self.agent,
            user_id="user-1",
            session_id="session-issues",
            user_message="track issues",
            max_attempts=1,
        )
        final = self._wait_for_status(run["id"], {"succeeded"})
        self.assertIsNotNone(final)

        items = self.manager.list_run_issues(run["id"], limit=200)
        self.assertGreaterEqual(len(items), 3)
        ids = {str(item.get("issue_id")) for item in items}
        self.assertIn("prepare_context", ids)
        self.assertIn("reasoning", ids)
        self.assertIn("persist", ids)
        self.assertTrue(all(str(item.get("status")) == "done" for item in items))

    def test_list_run_artifacts_returns_persisted_issue_outputs(self) -> None:
        self.manager.start()
        run = self.manager.create_run(
            agent=self.agent,
            user_id="user-1",
            session_id="session-artifacts",
            user_message="track artifacts",
            max_attempts=1,
        )
        final = self._wait_for_status(run["id"], {"succeeded"})
        self.assertIsNotNone(final)

        items = self.manager.list_run_artifacts(run["id"], limit=100)
        self.assertGreaterEqual(len(items), 1)
        first = items[0]
        self.assertEqual(str(first.get("issue_id")), "prepare_context")
        self.assertEqual(str(first.get("artifact_key")), "result")
        artifact = first.get("artifact", {})
        self.assertIsInstance(artifact, dict)
        assert isinstance(artifact, dict)
        self.assertEqual(bool(artifact.get("prepared")), True)

    def test_resume_uses_persisted_issue_artifacts_when_checkpoints_missing(self) -> None:
        self.executor.fail_once_after_prepare = True
        self.manager.start()
        run = self.manager.create_run(
            agent=self.agent,
            user_id="user-1",
            session_id="session-resume-artifacts",
            user_message="resume artifact state",
            max_attempts=1,
        )
        failed = self._wait_for_status(run["id"], {"failed"})
        self.assertIsNotNone(failed)

        artifacts = self.manager.list_run_artifacts(run["id"], limit=100)
        self.assertGreaterEqual(len(artifacts), 1)
        self.database.update_agent_run_fields(run["id"], checkpoints_json=[])

        self.executor.fail_once_after_prepare = False
        resumed = self.manager.resume_run(run["id"])
        self.assertEqual(resumed["status"], "queued")
        final = self._wait_for_status(run["id"], {"succeeded"})
        self.assertIsNotNone(final)
        self.assertIsNotNone(self.executor.last_resume_state)
        assert isinstance(self.executor.last_resume_state, dict)
        issue_artifacts = self.executor.last_resume_state.get("issue_artifacts")
        self.assertIsInstance(issue_artifacts, dict)
        assert isinstance(issue_artifacts, dict)
        prepare_artifacts = issue_artifacts.get("prepare_context", {})
        self.assertIsInstance(prepare_artifacts, dict)
        assert isinstance(prepare_artifacts, dict)
        self.assertIn("result", prepare_artifacts)

    def test_resume_uses_persisted_tool_call_cache_when_checkpoints_missing(self) -> None:
        self.executor = _FakeTaskExecutor(
            emit_tool_call_record=True,
            fail_once_after_tool_record=True,
        )
        self.manager = AgentRunManager(
            database=self.database,
            task_executor=self.executor,  # type: ignore[arg-type]
            worker_count=1,
            default_max_attempts=1,
            retry_backoff_sec=0.0,
            retry_max_backoff_sec=0.0,
            retry_jitter_sec=0.0,
        )
        self.manager.start()
        run = self.manager.create_run(
            agent=self.agent,
            user_id="user-1",
            session_id="session-resume-tool-cache",
            user_message="resume tool call cache",
            max_attempts=1,
        )
        failed = self._wait_for_status(run["id"], {"failed"})
        self.assertIsNotNone(failed)

        rows = self.database.list_agent_run_tool_calls(run_id=run["id"], limit=100)
        self.assertGreaterEqual(len(rows), 1)
        self.assertEqual(str(rows[0].get("idempotency_key")), "demo-key-1")
        self.assertEqual(str(rows[0].get("status")), "succeeded")
        self.database.update_agent_run_fields(run["id"], checkpoints_json=[])

        self.executor.fail_once_after_tool_record = False
        resumed = self.manager.resume_run(run["id"])
        self.assertEqual(resumed["status"], "queued")
        final = self._wait_for_status(run["id"], {"succeeded"})
        self.assertIsNotNone(final)

        self.assertIsNotNone(self.executor.last_resume_state)
        assert isinstance(self.executor.last_resume_state, dict)
        tool_call_cache = self.executor.last_resume_state.get("tool_call_cache")
        self.assertIsInstance(tool_call_cache, dict)
        assert isinstance(tool_call_cache, dict)
        self.assertIn("demo-key-1", tool_call_cache)
        cached_entry = tool_call_cache.get("demo-key-1")
        self.assertIsInstance(cached_entry, dict)
        assert isinstance(cached_entry, dict)
        self.assertEqual(str(cached_entry.get("status")), "succeeded")

    def test_start_recovers_running_runs_after_restart(self) -> None:
        run = self.manager.create_run(
            agent=self.agent,
            user_id="user-1",
            session_id="session-recovery",
            user_message="recover me",
            max_attempts=3,
        )
        self.database.update_agent_run_fields(
            run["id"],
            status="running",
            attempts=1,
            started_at=self.manager._utc_now(),  # noqa: SLF001
            lease_owner="crashed-worker",
            lease_token="crashed-token",
            lease_expires_at=self.manager._utc_now(),  # noqa: SLF001
        )
        self.database.append_agent_run_checkpoint(
            run_id=run["id"],
            checkpoint={
                "stage": "running",
                "attempt": 1,
                "message": "Simulated in-flight run before crash.",
            },
        )

        self.manager.start()
        final = self._wait_for_status(run["id"], {"succeeded"})
        self.assertIsNotNone(final)
        assert final is not None
        self.assertEqual(final["status"], "succeeded")
        self.assertGreaterEqual(int(final.get("attempts", 0)), 2)
        self.assertIsNone(final.get("lease_owner"))
        self.assertIsNone(final.get("lease_token"))
        self.assertIsNone(final.get("lease_expires_at"))
        stages = [str(item.get("stage")) for item in final.get("checkpoints", [])]
        self.assertIn("recovered_after_crash", stages)

    def test_replay_missing_run_raises(self) -> None:
        with self.assertRaises(ValueError):
            self.manager.replay_run("missing-run-id")

    def test_rate_limit_failure_retries_then_succeeds_with_failure_class(self) -> None:
        self.executor = _FakeTaskExecutor(
            error_sequence=[
                self._provider_error(
                    error_class="rate_limit",
                    message="429 Too Many Requests",
                    retryable=True,
                )
            ]
        )
        self.manager = AgentRunManager(
            database=self.database,
            task_executor=self.executor,  # type: ignore[arg-type]
            worker_count=1,
            default_max_attempts=2,
            retry_backoff_sec=0.0,
            retry_max_backoff_sec=0.0,
            retry_jitter_sec=0.0,
        )
        self.manager.start()
        run = self.manager.create_run(
            agent=self.agent,
            user_id="user-1",
            session_id="session-1",
            user_message="rate limited",
            max_attempts=2,
        )
        final = self._wait_for_status(run["id"], {"succeeded"})
        self.assertIsNotNone(final)
        assert final is not None
        self.assertEqual(final["status"], "succeeded")
        self.assertEqual(final["attempts"], 2)
        checkpoints = final.get("checkpoints", [])
        self.assertTrue(any(item.get("stage") == "retry_scheduled" for item in checkpoints))
        errors = [item for item in checkpoints if item.get("stage") == "error"]
        self.assertGreaterEqual(len(errors), 1)
        self.assertEqual(errors[0].get("failure_class"), "rate_limit")
        self.assertEqual(errors[0].get("stop_reason"), "provider_rate_limit")
        self.assertEqual(errors[0].get("retryable"), True)

    def test_quota_failure_is_non_retryable(self) -> None:
        self.executor = _FakeTaskExecutor(
            error_sequence=[
                self._provider_error(
                    error_class="quota",
                    message="quota exceeded",
                    retryable=False,
                )
            ]
        )
        self.manager = AgentRunManager(
            database=self.database,
            task_executor=self.executor,  # type: ignore[arg-type]
            worker_count=1,
            default_max_attempts=3,
            retry_backoff_sec=0.0,
            retry_max_backoff_sec=0.0,
            retry_jitter_sec=0.0,
        )
        self.manager.start()
        run = self.manager.create_run(
            agent=self.agent,
            user_id="user-1",
            session_id="session-1",
            user_message="quota fail",
            max_attempts=3,
        )
        final = self._wait_for_status(run["id"], {"failed"})
        self.assertIsNotNone(final)
        assert final is not None
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["attempts"], 1)
        self.assertEqual(final.get("failure_class"), "quota")
        self.assertEqual(final.get("stop_reason"), "provider_quota")
        stages = [str(item.get("stage")) for item in final.get("checkpoints", [])]
        self.assertNotIn("retry_scheduled", stages)

    def test_run_budget_tool_calls_exceeded_fails_fast(self) -> None:
        self.executor = _FakeTaskExecutor(
            emit_tool_finished_count=2,
            emit_tool_error_count=0,
        )
        self.manager = AgentRunManager(
            database=self.database,
            task_executor=self.executor,  # type: ignore[arg-type]
            worker_count=1,
            default_max_attempts=1,
            retry_backoff_sec=0.0,
            retry_max_backoff_sec=0.0,
            retry_jitter_sec=0.0,
        )
        self.manager.start()
        run = self.manager.create_run(
            agent=self.agent,
            user_id="user-1",
            session_id="session-1",
            user_message="tool budget",
            max_attempts=1,
            budget={
                "max_tokens": 10_000,
                "max_duration_sec": 60,
                "max_tool_calls": 1,
                "max_tool_errors": 2,
            },
        )
        final = self._wait_for_status(run["id"], {"failed"})
        self.assertIsNotNone(final)
        assert final is not None
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final.get("failure_class"), "budget_exceeded")
        self.assertEqual(final.get("stop_reason"), "budget_exceeded")

    def test_run_health_snapshot_contains_slo_metrics(self) -> None:
        self.manager.start()
        run_ok = self.manager.create_run(
            agent=self.agent,
            user_id="user-1",
            session_id="session-ok",
            user_message="ok",
            max_attempts=2,
        )
        self._wait_for_status(run_ok["id"], {"succeeded"})

        self.executor.error_sequence = [RuntimeError("timeout")]
        self.executor.call_count = 0
        run_fail = self.manager.create_run(
            agent=self.agent,
            user_id="user-1",
            session_id="session-fail",
            user_message="fail",
            max_attempts=1,
        )
        self._wait_for_status(run_fail["id"], {"failed"})

        health = self.manager.get_run_health(user_id="user-1", limit=20)
        self.assertGreaterEqual(int(health.get("sample_size", 0)), 2)
        status_breakdown = health.get("status_breakdown", {})
        self.assertIsInstance(status_breakdown, dict)
        self.assertGreaterEqual(int(status_breakdown.get("succeeded", 0)), 1)
        self.assertGreaterEqual(int(status_breakdown.get("failed", 0)), 1)
        slo = health.get("slo", {})
        self.assertIsInstance(slo, dict)
        run_slo = slo.get("run", {})
        self.assertIn("duration_ms", run_slo)
        attempt_slo = slo.get("run_attempt", {})
        self.assertIn("success_rate", attempt_slo)
        tool_slo = slo.get("tool_call", {})
        self.assertIn("duration_ms", tool_slo)

    def test_duplicate_queue_entries_do_not_duplicate_side_effects(self) -> None:
        self.executor = _SlowSideEffectTaskExecutor(sleep_sec=0.2)
        self.manager = AgentRunManager(
            database=self.database,
            task_executor=self.executor,  # type: ignore[arg-type]
            worker_count=3,
            default_max_attempts=1,
            retry_backoff_sec=0.0,
            retry_max_backoff_sec=0.0,
            retry_jitter_sec=0.0,
            run_lease_ttl_sec=30.0,
        )
        self.manager.start()
        run = self.manager.create_run(
            agent=self.agent,
            user_id="user-1",
            session_id="session-chaos",
            user_message="single effect",
            max_attempts=1,
        )
        for _ in range(8):
            self.manager._queue.put(run["id"])  # noqa: SLF001

        final = self._wait_for_status(run["id"], {"succeeded"})
        self.assertIsNotNone(final)
        assert final is not None
        self.assertEqual(final["status"], "succeeded")
        self.assertEqual(int(final.get("attempts", 0)), 1)
        self.assertEqual(self.executor.call_count, 1)
        self.assertEqual(self.executor.side_effect_count, 1)

        tool_calls = self.manager.list_run_tool_calls(run["id"], limit=20)
        self.assertEqual(len(tool_calls), 1)
        self.assertEqual(str(tool_calls[0].get("idempotency_key")), "side-effect:1")

    def test_run_lease_is_released_after_terminal_failure(self) -> None:
        self.executor.always_fail = True
        self.manager = AgentRunManager(
            database=self.database,
            task_executor=self.executor,  # type: ignore[arg-type]
            worker_count=1,
            default_max_attempts=1,
            retry_backoff_sec=0.0,
            retry_max_backoff_sec=0.0,
            retry_jitter_sec=0.0,
            run_lease_ttl_sec=60.0,
        )
        self.manager.start()
        run = self.manager.create_run(
            agent=self.agent,
            user_id="user-1",
            session_id="session-lease-release",
            user_message="fail and release lease",
            max_attempts=1,
        )
        final = self._wait_for_status(run["id"], {"failed"})
        self.assertIsNotNone(final)
        assert final is not None
        self.assertEqual(final["status"], "failed")
        self.assertIsNone(final.get("lease_owner"))
        self.assertIsNone(final.get("lease_token"))
        self.assertIsNone(final.get("lease_expires_at"))

    @staticmethod
    def _provider_error(
        *,
        error_class: str,
        message: str,
        retryable: bool,
    ) -> ProviderOperationError:
        info = ProviderErrorInfo(
            provider="openai",
            operation="chat",
            error_class=error_class,  # type: ignore[arg-type]
            message=message,
            raw_message=message,
            retryable=retryable,
            status_code=429 if error_class == "rate_limit" else 400,
        )
        return ProviderOperationError(info)

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
