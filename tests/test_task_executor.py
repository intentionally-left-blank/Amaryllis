from __future__ import annotations

import time
import unittest
from typing import Any

from agents.agent import Agent
from controller.meta_controller import MetaController
from planner.planner import PlanStep, Planner
from tasks.task_executor import TaskExecutor, TaskTimeoutError
from tools.tool_executor import ToolExecutor
from tools.tool_registry import ToolRegistry


class _FakeModelManager:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def chat(
        self,
        *,
        messages: list[dict[str, Any]],
        model: str | None,
        session_id: str | None,
    ) -> dict[str, Any]:
        self.calls.append(
            {
                "messages": list(messages),
                "model": model,
                "session_id": session_id,
            }
        )
        if self._responses:
            return dict(self._responses.pop(0))
        return {
            "content": "fallback response",
            "provider": "fake",
            "model": "fake-model",
        }


class _FakeMemoryManager:
    def __init__(self) -> None:
        self.interactions: list[dict[str, Any]] = []
        self.facts: list[dict[str, Any]] = []

    def add_interaction(
        self,
        *,
        user_id: str,
        agent_id: str,
        role: str,
        content: str,
        session_id: str | None,
    ) -> None:
        self.interactions.append(
            {
                "user_id": user_id,
                "agent_id": agent_id,
                "role": role,
                "content": content,
                "session_id": session_id,
            }
        )

    def remember_fact(self, *, user_id: str, text: str, metadata: dict[str, Any]) -> None:
        self.facts.append(
            {
                "user_id": user_id,
                "text": text,
                "metadata": metadata,
            }
        )

    @staticmethod
    def get_context(
        *,
        user_id: str,
        agent_id: str,
        query: str,
        session_id: str | None,
    ) -> dict[str, Any]:
        return {
            "user": {
                "id": user_id,
                "agent_id": agent_id,
                "session_id": session_id,
            },
            "working": [],
            "episodic": [],
            "semantic": [],
            "profile": [],
        }


class _ParallelPlanner:
    @staticmethod
    def create_plan(task: str, strategy: str) -> list[PlanStep]:  # noqa: ARG004
        return [
            PlanStep(id=1, description="Resolve subtask A", depends_on=[]),
            PlanStep(id=2, description="Resolve subtask B", depends_on=[]),
            PlanStep(id=3, description="Merge subtasks", depends_on=[1, 2]),
        ]


class _SlowIssueTaskExecutor(TaskExecutor):
    def _evaluate_plan_issue(  # type: ignore[override]
        self,
        *,
        issue_id: str,
        step_payload: dict[str, Any],
        tools_available: bool,
        issue_deadline_monotonic: float,
    ) -> dict[str, Any]:
        time.sleep(0.03)
        return super()._evaluate_plan_issue(
            issue_id=issue_id,
            step_payload=step_payload,
            tools_available=tools_available,
            issue_deadline_monotonic=issue_deadline_monotonic,
        )


class TaskExecutorTests(unittest.TestCase):
    def test_issue_state_machine_checkpoints_and_resume_snapshot(self) -> None:
        model_manager = _FakeModelManager(
            responses=[
                {
                    "content": "Final stable answer.",
                    "provider": "fake",
                    "model": "fake-model",
                }
            ]
        )
        memory_manager = _FakeMemoryManager()
        registry = ToolRegistry()
        executor = TaskExecutor(
            model_manager=model_manager,  # type: ignore[arg-type]
            memory_manager=memory_manager,  # type: ignore[arg-type]
            tool_registry=registry,
            tool_executor=ToolExecutor(registry),
            meta_controller=MetaController(),
            planner=Planner(),
            max_model_calls=4,
            verifier_enabled=False,
        )
        agent = Agent.create(
            name="Issue Agent",
            system_prompt="Respond directly.",
            model="fake-model",
            tools=[],
            user_id="user-1",
        )

        checkpoints: list[dict[str, Any]] = []

        def _checkpoint(payload: dict[str, Any]) -> None:
            checkpoints.append(dict(payload))

        result = executor.execute(
            agent=agent,
            user_id="user-1",
            session_id="session-issue",
            user_message="Hello",
            checkpoint=_checkpoint,
        )

        self.assertEqual(result["response"], "Final stable answer.")
        issue_events = [item for item in checkpoints if item.get("stage") == "issue_state"]
        self.assertGreaterEqual(len(issue_events), 4)
        issue_ids = {str(item.get("issue", {}).get("id")) for item in issue_events if isinstance(item.get("issue"), dict)}
        self.assertIn("prepare_context", issue_ids)
        self.assertIn("reasoning", issue_ids)
        self.assertIn("persist", issue_ids)
        self.assertIn("plan_step:1", issue_ids)

        artifact_events = [item for item in checkpoints if item.get("stage") == "issue_artifact"]
        self.assertGreaterEqual(len(artifact_events), 1)
        self.assertEqual(str(artifact_events[0].get("issue_id")), "plan_step:1")
        self.assertEqual(str(artifact_events[0].get("artifact_key")), "result")

        step_completed = [item for item in checkpoints if item.get("stage") == "step_completed"]
        self.assertGreaterEqual(len(step_completed), 3)
        latest_resume = step_completed[-1].get("resume_state")
        self.assertIsInstance(latest_resume, dict)
        assert isinstance(latest_resume, dict)
        issues = latest_resume.get("issues")
        self.assertIsInstance(issues, dict)
        assert isinstance(issues, dict)
        self.assertEqual(str(issues.get("prepare_context", {}).get("status")), "done")
        self.assertEqual(str(issues.get("reasoning", {}).get("status")), "done")
        self.assertEqual(str(issues.get("persist", {}).get("status")), "done")
        issue_artifacts = latest_resume.get("issue_artifacts")
        self.assertIsInstance(issue_artifacts, dict)
        assert isinstance(issue_artifacts, dict)
        self.assertIn("plan_step:1", issue_artifacts)

        first_call_messages = model_manager.calls[0]["messages"]
        artifact_context_present = any(
            isinstance(item, dict)
            and str(item.get("role")) == "system"
            and "Issue artifacts context:" in str(item.get("content", ""))
            for item in first_call_messages
        )
        self.assertTrue(artifact_context_present)

    def test_invalid_tool_arguments_are_rejected_before_execution(self) -> None:
        call_counter = {"count": 0}

        def _handler(args: dict[str, Any]) -> dict[str, Any]:
            call_counter["count"] += 1
            return {"ok": True, "args": args}

        registry = ToolRegistry()
        registry.register(
            name="calc_sum",
            description="Calculate sum",
            input_schema={
                "type": "object",
                "properties": {
                    "count": {"type": "integer"},
                },
                "required": ["count"],
                "additionalProperties": False,
            },
            handler=_handler,
        )

        model_manager = _FakeModelManager(
            responses=[
                {
                    "content": '<tool_call>{"name":"calc_sum","arguments":{"count":"invalid"}}</tool_call>',
                    "provider": "fake",
                    "model": "fake-model",
                },
                {
                    "content": "Final answer without tool execution.",
                    "provider": "fake",
                    "model": "fake-model",
                },
            ]
        )
        memory_manager = _FakeMemoryManager()
        executor = TaskExecutor(
            model_manager=model_manager,  # type: ignore[arg-type]
            memory_manager=memory_manager,  # type: ignore[arg-type]
            tool_registry=registry,
            tool_executor=ToolExecutor(registry),
            meta_controller=MetaController(),
            planner=Planner(),
            max_model_calls=4,
            verifier_enabled=False,
        )

        agent = Agent.create(
            name="Tool Agent",
            system_prompt="Use tools when needed.",
            model="fake-model",
            tools=["calc_sum"],
            user_id="user-1",
        )

        result = executor.execute(
            agent=agent,
            user_id="user-1",
            session_id="session-1",
            user_message="Use a tool",
        )

        self.assertEqual(call_counter["count"], 0)
        self.assertEqual(result["response"], "Final answer without tool execution.")
        self.assertEqual(result["metrics"]["model_calls"], 2)
        self.assertEqual(result["metrics"]["tool_rounds"], 1)

        tools = result["tools"]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["status"], "invalid_arguments")
        self.assertIn("count", str(tools[0].get("error", "")))

    def test_plan_issues_execute_in_parallel_when_independent(self) -> None:
        model_manager = _FakeModelManager(
            responses=[
                {
                    "content": "Final answer.",
                    "provider": "fake",
                    "model": "fake-model",
                }
            ]
        )
        memory_manager = _FakeMemoryManager()
        registry = ToolRegistry()
        executor = TaskExecutor(
            model_manager=model_manager,  # type: ignore[arg-type]
            memory_manager=memory_manager,  # type: ignore[arg-type]
            tool_registry=registry,
            tool_executor=ToolExecutor(registry),
            meta_controller=MetaController(),
            planner=_ParallelPlanner(),  # type: ignore[arg-type]
            max_model_calls=4,
            verifier_enabled=False,
            issue_parallel_workers=2,
            issue_timeout_sec=5.0,
        )
        agent = Agent.create(
            name="Parallel Agent",
            system_prompt="Solve tasks.",
            model="fake-model",
            tools=[],
            user_id="user-1",
        )
        checkpoints: list[dict[str, Any]] = []

        def _checkpoint(payload: dict[str, Any]) -> None:
            checkpoints.append(dict(payload))

        result = executor.execute(
            agent=agent,
            user_id="user-1",
            session_id="s1",
            user_message="Do A and B",
            checkpoint=_checkpoint,
        )
        self.assertEqual(result["response"], "Final answer.")

        tracked = []
        for idx, item in enumerate(checkpoints):
            if item.get("stage") != "issue_state":
                continue
            issue = item.get("issue")
            if not isinstance(issue, dict):
                continue
            issue_id = str(issue.get("id"))
            status = str(issue.get("status"))
            if issue_id in {"plan_step:1", "plan_step:2"} and status in {"running", "done"}:
                tracked.append((idx, issue_id, status))

        running_indices = [idx for idx, _, status in tracked if status == "running"]
        done_indices = [idx for idx, _, status in tracked if status == "done"]
        self.assertGreaterEqual(len(running_indices), 2)
        self.assertGreaterEqual(len(done_indices), 2)
        self.assertLess(max(running_indices[:2]), min(done_indices))

    def test_plan_issue_deadline_timeout(self) -> None:
        model_manager = _FakeModelManager(
            responses=[
                {
                    "content": "Final answer.",
                    "provider": "fake",
                    "model": "fake-model",
                }
            ]
        )
        memory_manager = _FakeMemoryManager()
        registry = ToolRegistry()
        executor = _SlowIssueTaskExecutor(
            model_manager=model_manager,  # type: ignore[arg-type]
            memory_manager=memory_manager,  # type: ignore[arg-type]
            tool_registry=registry,
            tool_executor=ToolExecutor(registry),
            meta_controller=MetaController(),
            planner=_ParallelPlanner(),  # type: ignore[arg-type]
            max_model_calls=4,
            verifier_enabled=False,
            issue_parallel_workers=1,
            issue_timeout_sec=0.01,
        )
        agent = Agent.create(
            name="Timeout Agent",
            system_prompt="Solve tasks.",
            model="fake-model",
            tools=[],
            user_id="user-1",
        )

        with self.assertRaises(TaskTimeoutError):
            executor.execute(
                agent=agent,
                user_id="user-1",
                session_id="s2",
                user_message="Do A and B",
            )

    def test_verifier_repairs_empty_response(self) -> None:
        model_manager = _FakeModelManager(
            responses=[
                {
                    "content": "   ",
                    "provider": "fake",
                    "model": "fake-model",
                },
                {
                    "content": "Repaired final answer with enough details.",
                    "provider": "fake",
                    "model": "fake-model",
                },
            ]
        )
        memory_manager = _FakeMemoryManager()
        registry = ToolRegistry()
        executor = TaskExecutor(
            model_manager=model_manager,  # type: ignore[arg-type]
            memory_manager=memory_manager,  # type: ignore[arg-type]
            tool_registry=registry,
            tool_executor=ToolExecutor(registry),
            meta_controller=MetaController(),
            planner=Planner(),
            max_model_calls=4,
            verifier_enabled=True,
            verifier_max_repair_attempts=1,
            verifier_min_response_chars=8,
        )

        agent = Agent.create(
            name="Verifier Agent",
            system_prompt="Be concise.",
            model="fake-model",
            tools=[],
            user_id="user-1",
        )

        result = executor.execute(
            agent=agent,
            user_id="user-1",
            session_id="session-1",
            user_message="Hello",
        )

        self.assertEqual(result["response"], "Repaired final answer with enough details.")
        self.assertEqual(result["metrics"]["model_calls"], 2)
        self.assertEqual(result["provider"], "fake")
        self.assertGreaterEqual(len(memory_manager.interactions), 2)
        self.assertEqual(memory_manager.interactions[-1]["role"], "assistant")
        self.assertEqual(
            memory_manager.interactions[-1]["content"],
            "Repaired final answer with enough details.",
        )


if __name__ == "__main__":
    unittest.main()
