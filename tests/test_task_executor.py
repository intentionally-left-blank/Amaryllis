from __future__ import annotations

import unittest
from typing import Any

from agents.agent import Agent
from controller.meta_controller import MetaController
from planner.planner import Planner
from tasks.task_executor import TaskExecutor
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


class TaskExecutorTests(unittest.TestCase):
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
