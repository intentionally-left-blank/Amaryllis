from __future__ import annotations

import importlib
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover - dependency may be unavailable
    TestClient = None  # type: ignore[assignment]


class _BudgetGuardrailStubExecutor:
    def execute(
        self,
        *,
        agent: Any,
        user_id: str,
        session_id: str | None,
        user_message: str,
        checkpoint: Any,
        run_deadline_monotonic: float | None = None,
        resume_state: dict[str, Any] | None = None,
        run_budget: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _ = (agent, user_id, session_id, run_deadline_monotonic, resume_state, run_budget)
        text = str(user_message or "").strip().lower()

        if "sibling-long" in text:
            deadline = time.time() + 5.0
            while time.time() < deadline:
                checkpoint(
                    {
                        "stage": "reasoning",
                        "message": "sibling still running",
                    }
                )
                time.sleep(0.05)
            return {"response": "sibling-finished"}

        checkpoint(
            {
                "stage": "tool_call_finished",
                "message": "stub tool call #1",
                "tool_name": "stub_budget_tool",
                "status": "succeeded",
                "cached": False,
                "executed": True,
            }
        )
        checkpoint(
            {
                "stage": "tool_call_finished",
                "message": "stub tool call #2",
                "tool_name": "stub_budget_tool",
                "status": "succeeded",
                "cached": False,
                "executed": True,
            }
        )
        return {"response": "primary-finished"}


@unittest.skipIf(TestClient is None, "fastapi dependency is not available")
class AgentRunBudgetGuardrailAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-run-budget-api-")
        support_dir = Path(cls._tmp.name) / "support"
        auth_tokens = {
            "admin-token": {"user_id": "admin", "scopes": ["admin", "user"]},
            "user-token": {"user_id": "user-1", "scopes": ["user"]},
            "user2-token": {"user_id": "user-2", "scopes": ["user"]},
            "service-token": {"user_id": "svc-runtime", "scopes": ["service"]},
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
                "AMARYLLIS_COGNITION_BACKEND": "deterministic",
            },
            clear=False,
        )
        cls._env_patch.start()

        import runtime.server as server_module

        cls.server_module = importlib.reload(server_module)
        cls._client_cm = TestClient(cls.server_module.app)
        cls.client = cls._client_cm.__enter__()

        services = cls.server_module.app.state.services
        stub = _BudgetGuardrailStubExecutor()
        services.agent_run_manager.task_executor = stub
        services.agent_manager.task_executor = stub

    @classmethod
    def tearDownClass(cls) -> None:
        cls._client_cm.__exit__(None, None, None)
        cls._env_patch.stop()
        cls._tmp.cleanup()

    @staticmethod
    def _auth(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _create_agent(self, *, name: str) -> str:
        created = self.client.post(
            "/agents/create",
            headers=self._auth("user-token"),
            json={
                "name": name,
                "system_prompt": "budget guardrail api test",
                "user_id": "user-1",
                "tools": ["web_search"],
            },
        )
        self.assertEqual(created.status_code, 200)
        return str(created.json().get("id"))

    def _create_run(
        self,
        *,
        agent_id: str,
        message: str,
        max_attempts: int,
        budget: dict[str, Any],
    ) -> str:
        created = self.client.post(
            f"/agents/{agent_id}/runs",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "session_id": "budget-guardrail-session",
                "message": message,
                "max_attempts": max_attempts,
                "budget": budget,
            },
        )
        self.assertEqual(created.status_code, 200)
        run_id = str(created.json().get("run", {}).get("id") or "")
        self.assertTrue(bool(run_id))
        return run_id

    def _get_run(self, run_id: str) -> dict[str, Any]:
        response = self.client.get(f"/agents/runs/{run_id}", headers=self._auth("user-token"))
        self.assertEqual(response.status_code, 200)
        payload = response.json().get("run", {})
        self.assertTrue(isinstance(payload, dict))
        return payload

    def _wait_run_terminal(self, *, run_id: str, timeout_sec: float = 8.0) -> dict[str, Any]:
        terminal = {"succeeded", "failed", "canceled"}
        deadline = time.time() + timeout_sec
        last_payload: dict[str, Any] = {}
        while time.time() < deadline:
            payload = self._get_run(run_id)
            last_payload = payload
            status = str(payload.get("status") or "").strip().lower()
            if status in terminal:
                return payload
            time.sleep(0.05)
        self.fail(f"Run {run_id} did not reach terminal status. last_payload={last_payload}")

    def _wait_run_status(self, *, run_id: str, wanted: str, timeout_sec: float = 6.0) -> dict[str, Any]:
        deadline = time.time() + timeout_sec
        wanted_norm = str(wanted).strip().lower()
        last_payload: dict[str, Any] = {}
        while time.time() < deadline:
            payload = self._get_run(run_id)
            last_payload = payload
            status = str(payload.get("status") or "").strip().lower()
            if status == wanted_norm:
                return payload
            time.sleep(0.05)
        self.fail(
            f"Run {run_id} did not reach status={wanted_norm}. last_payload={last_payload}"
        )

    def test_single_budget_breach_pauses_without_retry(self) -> None:
        agent_id = self._create_agent(name="Budget Pause Agent")
        run_id = self._create_run(
            agent_id=agent_id,
            message="primary-budget-breach",
            max_attempts=3,
            budget={
                "max_tokens": 24_000,
                "max_duration_sec": 120,
                "max_tool_calls": 1,
                "max_tool_errors": 3,
            },
        )

        terminal = self._wait_run_terminal(run_id=run_id)
        self.assertEqual(str(terminal.get("status")), "failed")
        self.assertEqual(str(terminal.get("stop_reason")), "budget_guardrail_paused")
        self.assertEqual(str(terminal.get("failure_class")), "budget_exceeded")
        self.assertEqual(int(terminal.get("attempts") or 0), 1)

        diagnostics = self.client.get(
            f"/agents/runs/{run_id}/diagnostics",
            headers=self._auth("user-token"),
        )
        self.assertEqual(diagnostics.status_code, 200)
        payload = diagnostics.json().get("diagnostics", {})
        warnings = list(payload.get("diagnostics", {}).get("warnings", []))
        self.assertIn("budget_exceeded", warnings)
        self.assertIn("budget_guardrail_paused", warnings)

    def test_repeated_budget_breach_escalates_to_agent_scope_kill_switch(self) -> None:
        agent_id = self._create_agent(name="Budget Escalation Agent")
        guarded_budget = {
            "max_tokens": 24_000,
            "max_duration_sec": 120,
            "max_tool_calls": 1,
            "max_tool_errors": 3,
        }
        primary_run_id = self._create_run(
            agent_id=agent_id,
            message="primary-budget-breach",
            max_attempts=3,
            budget=guarded_budget,
        )
        first_terminal = self._wait_run_terminal(run_id=primary_run_id)
        self.assertEqual(str(first_terminal.get("stop_reason")), "budget_guardrail_paused")

        sibling_run_id = self._create_run(
            agent_id=agent_id,
            message="sibling-long-running",
            max_attempts=1,
            budget={
                "max_tokens": 24_000,
                "max_duration_sec": 120,
                "max_tool_calls": 8,
                "max_tool_errors": 3,
            },
        )
        self._wait_run_status(run_id=sibling_run_id, wanted="running")

        resumed = self.client.post(
            f"/agents/runs/{primary_run_id}/resume",
            headers=self._auth("user-token"),
        )
        self.assertEqual(resumed.status_code, 200)

        final_primary = self._wait_run_terminal(run_id=primary_run_id, timeout_sec=10.0)
        self.assertEqual(str(final_primary.get("status")), "canceled")
        self.assertEqual(str(final_primary.get("stop_reason")), "budget_guardrail_kill_switch")

        final_sibling = self._wait_run_terminal(run_id=sibling_run_id, timeout_sec=10.0)
        self.assertEqual(str(final_sibling.get("status")), "canceled")
        self.assertEqual(str(final_sibling.get("stop_reason")), "kill_switch_triggered")

        replay = self.client.get(
            f"/agents/runs/{primary_run_id}/replay",
            headers=self._auth("user-token"),
        )
        self.assertEqual(replay.status_code, 200)
        timeline = list(replay.json().get("replay", {}).get("timeline", []))
        stages = [str(item.get("stage") or "") for item in timeline if isinstance(item, dict)]
        self.assertIn("budget_guardrail_escalated", stages)
        self.assertIn("budget_guardrail_kill_switch_scope", stages)


if __name__ == "__main__":
    unittest.main()
