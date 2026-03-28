from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from agents.agent_run_manager import AutonomyCircuitBreakerBlockedError
from storage.database import Database
from supervisor.task_graph_manager import SupervisorTaskGraphManager


class _FakeAgentManager:
    def __init__(self) -> None:
        self._runs: dict[str, dict[str, Any]] = {}
        self._run_seq = 0

    def create_run(
        self,
        *,
        agent_id: str,
        user_message: str,
        user_id: str,
        session_id: str | None,
        max_attempts: int | None = None,
        budget: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._run_seq += 1
        run_id = f"run-{self._run_seq}"
        run = {
            "id": run_id,
            "agent_id": agent_id,
            "user_id": user_id,
            "session_id": session_id,
            "message": user_message,
            "status": "queued",
            "max_attempts": max_attempts,
            "budget": budget,
            "result": {},
        }
        self._runs[run_id] = dict(run)
        return dict(run)

    def get_run(self, run_id: str) -> dict[str, Any]:
        run = self._runs.get(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        return dict(run)

    def set_run_status(self, run_id: str, status: str, *, error: str | None = None) -> None:
        run = self._runs.get(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        run["status"] = status
        if error is not None:
            run["result"] = {"error": error}

    def set_run_response(self, run_id: str, response: str) -> None:
        run = self._runs.get(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        result = run.get("result")
        if not isinstance(result, dict):
            result = {}
            run["result"] = result
        result["response"] = str(response)


class _BreakerAwareFakeAgentManager(_FakeAgentManager):
    def __init__(
        self,
        *,
        block_global: bool = False,
        blocked_user_id: str | None = None,
        blocked_agent_id: str | None = None,
    ) -> None:
        super().__init__()
        self.block_global = bool(block_global)
        self.blocked_user_id = str(blocked_user_id or "").strip() or None
        self.blocked_agent_id = str(blocked_agent_id or "").strip() or None
        self.revision = 1

    def create_run(
        self,
        *,
        agent_id: str,
        user_message: str,
        user_id: str,
        session_id: str | None,
        max_attempts: int | None = None,
        budget: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        matched_scopes: list[dict[str, Any]] = []
        if self.block_global:
            matched_scopes.append({"scope_type": "global", "reason": "test-global"})
        if self.blocked_user_id is not None and self.blocked_user_id == str(user_id):
            matched_scopes.append(
                {
                    "scope_type": "user",
                    "scope_user_id": self.blocked_user_id,
                    "reason": "test-user-scope",
                }
            )
        if self.blocked_agent_id is not None and self.blocked_agent_id == str(agent_id):
            matched_scopes.append(
                {
                    "scope_type": "agent",
                    "scope_agent_id": self.blocked_agent_id,
                    "reason": "test-agent-scope",
                }
            )
        if matched_scopes:
            revision = int(self.revision)
            self.revision += 1
            raise AutonomyCircuitBreakerBlockedError(
                "blocked by breaker",
                decision={"blocked": True, "revision": revision},
                matched_scopes=matched_scopes,
                snapshot={
                    "armed": True,
                    "revision": revision,
                    "active_scope_count": len(matched_scopes),
                    "active_scopes": matched_scopes,
                },
            )
        return super().create_run(
            agent_id=agent_id,
            user_message=user_message,
            user_id=user_id,
            session_id=session_id,
            max_attempts=max_attempts,
            budget=budget,
        )


def _node_by_id(graph: dict[str, Any], node_id: str) -> dict[str, Any]:
    for node in graph.get("nodes", []):
        if str(node.get("node_id") or "") == node_id:
            return node
    raise AssertionError(f"node not found: {node_id}")


class SupervisorTaskGraphManagerTests(unittest.TestCase):
    def test_create_graph_validates_cycle_and_dependencies(self) -> None:
        manager = SupervisorTaskGraphManager(agent_manager=_FakeAgentManager())
        created = manager.create_graph(
            user_id="user-1",
            objective="resolve incident",
            nodes=[
                {
                    "node_id": "analyze",
                    "agent_id": "agent-a",
                    "message": "Inspect incident logs",
                },
                {
                    "node_id": "remediate",
                    "agent_id": "agent-b",
                    "message": "Apply remediation",
                    "depends_on": ["analyze"],
                },
            ],
        )
        self.assertTrue(str(created.get("id") or "").startswith("sup-"))
        self.assertEqual(str(created.get("status")), "planned")
        self.assertEqual(len(created.get("nodes", [])), 2)

        with self.assertRaises(ValueError):
            manager.create_graph(
                user_id="user-1",
                objective="invalid",
                nodes=[
                    {
                        "node_id": "a",
                        "agent_id": "agent-a",
                        "message": "A",
                        "depends_on": ["b"],
                    },
                    {
                        "node_id": "b",
                        "agent_id": "agent-b",
                        "message": "B",
                        "depends_on": ["a"],
                    },
                ],
            )

    def test_launch_and_tick_promotes_dependencies(self) -> None:
        fake_agent_manager = _FakeAgentManager()
        manager = SupervisorTaskGraphManager(agent_manager=fake_agent_manager)

        created = manager.create_graph(
            user_id="user-1",
            objective="incident response",
            nodes=[
                {
                    "node_id": "triage",
                    "agent_id": "agent-triage",
                    "message": "Run triage",
                },
                {
                    "node_id": "fix",
                    "agent_id": "agent-fix",
                    "message": "Run fix",
                    "depends_on": ["triage"],
                },
            ],
        )
        graph_id = str(created.get("id") or "")
        launched = manager.launch_graph(
            graph_id=graph_id,
            user_id="user-1",
            session_id="sup-session-1",
        )
        self.assertEqual(str(launched.get("status")), "running")
        triage = _node_by_id(launched, "triage")
        self.assertIn(str(triage.get("status")), {"queued", "running"})
        triage_run_id = str(triage.get("run_id") or "")
        self.assertTrue(triage_run_id)

        fake_agent_manager.set_run_status(triage_run_id, "succeeded")
        after_triage = manager.tick_graph(graph_id=graph_id, user_id="user-1")
        triage_after = _node_by_id(after_triage, "triage")
        fix_after = _node_by_id(after_triage, "fix")
        self.assertEqual(str(triage_after.get("status")), "succeeded")
        self.assertIn(str(fix_after.get("status")), {"queued", "running"})
        fix_run_id = str(fix_after.get("run_id") or "")
        self.assertTrue(fix_run_id)

        fake_agent_manager.set_run_status(fix_run_id, "succeeded")
        completed = manager.tick_graph(graph_id=graph_id, user_id="user-1")
        self.assertEqual(str(completed.get("status")), "succeeded")
        self.assertEqual(str(_node_by_id(completed, "fix").get("status")), "succeeded")

    def test_failed_dependency_blocks_downstream(self) -> None:
        fake_agent_manager = _FakeAgentManager()
        manager = SupervisorTaskGraphManager(agent_manager=fake_agent_manager)
        created = manager.create_graph(
            user_id="user-1",
            objective="incident response",
            nodes=[
                {
                    "node_id": "step-1",
                    "agent_id": "agent-a",
                    "message": "Step 1",
                },
                {
                    "node_id": "step-2",
                    "agent_id": "agent-b",
                    "message": "Step 2",
                    "depends_on": ["step-1"],
                },
            ],
        )
        graph_id = str(created.get("id") or "")
        launched = manager.launch_graph(graph_id=graph_id, user_id="user-1")
        step1 = _node_by_id(launched, "step-1")
        run_id = str(step1.get("run_id") or "")
        self.assertTrue(run_id)

        fake_agent_manager.set_run_status(run_id, "failed", error="tool timeout")
        failed = manager.tick_graph(graph_id=graph_id, user_id="user-1")
        self.assertEqual(str(failed.get("status")), "failed")
        self.assertEqual(str(_node_by_id(failed, "step-1").get("status")), "failed")
        self.assertEqual(str(_node_by_id(failed, "step-2").get("status")), "blocked")

    def test_owner_guard_blocks_foreign_access(self) -> None:
        manager = SupervisorTaskGraphManager(agent_manager=_FakeAgentManager())
        created = manager.create_graph(
            user_id="user-1",
            objective="owner-test",
            nodes=[
                {
                    "node_id": "n1",
                    "agent_id": "agent-a",
                    "message": "owner",
                }
            ],
        )
        graph_id = str(created.get("id") or "")
        with self.assertRaises(ValueError):
            manager.launch_graph(graph_id=graph_id, user_id="user-2")

    def test_graph_state_recovers_from_database_checkpoint_store(self) -> None:
        fake_agent_manager = _FakeAgentManager()
        with tempfile.TemporaryDirectory(prefix="amaryllis-tests-supervisor-persistence-") as tmp_dir:
            database = Database(Path(tmp_dir) / "amaryllis.db")
            manager = SupervisorTaskGraphManager(
                agent_manager=fake_agent_manager,
                database=database,
            )
            created = manager.create_graph(
                user_id="user-1",
                objective="resume-graph",
                nodes=[
                    {
                        "node_id": "triage",
                        "agent_id": "agent-a",
                        "message": "triage",
                    },
                    {
                        "node_id": "fix",
                        "agent_id": "agent-b",
                        "message": "fix",
                        "depends_on": ["triage"],
                    },
                ],
            )
            graph_id = str(created.get("id") or "")
            launched = manager.launch_graph(
                graph_id=graph_id,
                user_id="user-1",
                session_id="sup-persist-session-1",
            )
            triage_run_id = str(_node_by_id(launched, "triage").get("run_id") or "")
            self.assertTrue(triage_run_id)

            fake_agent_manager.set_run_status(triage_run_id, "succeeded")
            progressed = manager.tick_graph(graph_id=graph_id, user_id="user-1")
            fix_run_id = str(_node_by_id(progressed, "fix").get("run_id") or "")
            self.assertTrue(fix_run_id)

            recovered_manager = SupervisorTaskGraphManager(
                agent_manager=fake_agent_manager,
                database=database,
            )
            recovered_graph = recovered_manager.get_graph(graph_id=graph_id)
            self.assertEqual(str(recovered_graph.get("status")), "running")
            self.assertEqual(str(_node_by_id(recovered_graph, "triage").get("status")), "succeeded")
            self.assertEqual(str(_node_by_id(recovered_graph, "fix").get("run_id")), fix_run_id)

            fake_agent_manager.set_run_status(fix_run_id, "succeeded")
            completed = recovered_manager.tick_graph(graph_id=graph_id, user_id="user-1")
            self.assertEqual(str(completed.get("status")), "succeeded")
            database.close()

    def test_objective_verification_manual_mode_requires_explicit_verify(self) -> None:
        fake_agent_manager = _FakeAgentManager()
        manager = SupervisorTaskGraphManager(agent_manager=fake_agent_manager)
        created = manager.create_graph(
            user_id="user-1",
            objective="Deliver mitigation report",
            nodes=[
                {
                    "node_id": "report",
                    "agent_id": "agent-report",
                    "message": "Generate mitigation report",
                }
            ],
            objective_verification={
                "mode": "manual",
            },
        )
        graph_id = str(created.get("id") or "")
        launched = manager.launch_graph(graph_id=graph_id, user_id="user-1")
        run_id = str(_node_by_id(launched, "report").get("run_id") or "")
        self.assertTrue(run_id)
        fake_agent_manager.set_run_response(run_id, "Mitigation report completed.")
        fake_agent_manager.set_run_status(run_id, "succeeded")

        review_required = manager.tick_graph(graph_id=graph_id, user_id="user-1")
        self.assertEqual(str(review_required.get("status")), "review_required")
        verification = review_required.get("objective_verification", {})
        self.assertEqual(str(verification.get("status")), "review_required")

        approved = manager.verify_graph_objective(
            graph_id=graph_id,
            user_id="user-1",
            override_pass=True,
            note="Reviewed by operator",
        )
        self.assertEqual(str(approved.get("status")), "succeeded")
        self.assertEqual(
            str(approved.get("objective_verification", {}).get("status")),
            "passed",
        )

    def test_objective_verification_keyword_gate_can_fail_graph(self) -> None:
        fake_agent_manager = _FakeAgentManager()
        manager = SupervisorTaskGraphManager(agent_manager=fake_agent_manager)
        created = manager.create_graph(
            user_id="user-1",
            objective="Find root cause and remediation",
            nodes=[
                {
                    "node_id": "analysis",
                    "agent_id": "agent-analysis",
                    "message": "Analyze incident",
                }
            ],
            objective_verification={
                "mode": "auto",
                "required_keywords": ["root cause"],
                "min_response_chars": 10,
                "on_failure": "failed",
            },
        )
        graph_id = str(created.get("id") or "")
        launched = manager.launch_graph(graph_id=graph_id, user_id="user-1")
        run_id = str(_node_by_id(launched, "analysis").get("run_id") or "")
        self.assertTrue(run_id)

        fake_agent_manager.set_run_response(run_id, "Short incident note.")
        fake_agent_manager.set_run_status(run_id, "succeeded")
        failed = manager.tick_graph(graph_id=graph_id, user_id="user-1")
        self.assertEqual(str(failed.get("status")), "failed")
        verification = failed.get("objective_verification", {})
        self.assertEqual(str(verification.get("status")), "failed")

    def test_breaker_blocked_dispatch_keeps_node_planned_until_disarm(self) -> None:
        fake_agent_manager = _BreakerAwareFakeAgentManager(block_global=True)
        manager = SupervisorTaskGraphManager(agent_manager=fake_agent_manager)
        created = manager.create_graph(
            user_id="user-1",
            objective="breaker dispatch parity",
            nodes=[
                {
                    "node_id": "node-1",
                    "agent_id": "agent-1",
                    "message": "step",
                }
            ],
        )
        graph_id = str(created.get("id") or "")

        launched = manager.launch_graph(graph_id=graph_id, user_id="user-1")
        node = _node_by_id(launched, "node-1")
        self.assertEqual(str(node.get("status") or ""), "planned")
        self.assertFalse(bool(str(node.get("run_id") or "").strip()))
        self.assertEqual(str(launched.get("status") or ""), "running")
        timeline = launched.get("timeline", [])
        self.assertTrue(
            any(
                str(item.get("event") or "") == "node_run_blocked_autonomy_circuit_breaker"
                for item in timeline
            )
        )

        fake_agent_manager.block_global = False
        resumed = manager.tick_graph(graph_id=graph_id, user_id="user-1")
        resumed_node = _node_by_id(resumed, "node-1")
        self.assertIn(str(resumed_node.get("status") or ""), {"queued", "running", "succeeded"})
        self.assertTrue(bool(str(resumed_node.get("run_id") or "").strip()))


if __name__ == "__main__":
    unittest.main()
