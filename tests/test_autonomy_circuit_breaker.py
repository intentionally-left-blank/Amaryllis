from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from runtime.autonomy_circuit_breaker import AutonomyCircuitBreaker, FAIL_SAFE_RECOVERY_REASON


class AutonomyCircuitBreakerTests(unittest.TestCase):
    def test_default_state_is_disarmed(self) -> None:
        breaker = AutonomyCircuitBreaker()
        snapshot = breaker.snapshot()
        self.assertFalse(bool(snapshot.get("armed")))
        self.assertEqual(str(snapshot.get("status")), "disarmed")
        self.assertEqual(int(snapshot.get("revision", -1)), 0)
        self.assertTrue(bool(str(snapshot.get("updated_at") or "").strip()))
        self.assertEqual(int(snapshot.get("active_scope_count", -1)), 0)

    def test_arm_and_disarm_update_state_and_revision(self) -> None:
        breaker = AutonomyCircuitBreaker()

        armed = breaker.arm(actor="svc-runtime", reason="phase5 rollout", request_id="req-1")
        self.assertTrue(bool(armed.get("armed")))
        self.assertEqual(str(armed.get("status")), "armed")
        self.assertEqual(int(armed.get("revision", -1)), 1)
        self.assertEqual(str(armed.get("armed_by")), "svc-runtime")
        self.assertEqual(str(armed.get("reason")), "phase5 rollout")
        self.assertEqual(str(armed.get("request_id")), "req-1")
        self.assertTrue(bool(str(armed.get("armed_at") or "").strip()))
        self.assertEqual(int(armed.get("active_scope_count", -1)), 1)

        disarmed = breaker.disarm(actor="svc-runtime", reason="incident mitigated", request_id="req-2")
        self.assertFalse(bool(disarmed.get("armed")))
        self.assertEqual(str(disarmed.get("status")), "disarmed")
        self.assertEqual(int(disarmed.get("revision", -1)), 2)
        self.assertEqual(str(disarmed.get("disarmed_by")), "svc-runtime")
        self.assertEqual(str(disarmed.get("reason")), "incident mitigated")
        self.assertEqual(str(disarmed.get("request_id")), "req-2")
        self.assertTrue(bool(str(disarmed.get("disarmed_at") or "").strip()))
        self.assertEqual(int(disarmed.get("active_scope_count", -1)), 0)

    def test_scoped_user_and_agent_blocking_decisions(self) -> None:
        breaker = AutonomyCircuitBreaker()
        breaker.arm(
            actor="svc-runtime",
            reason="user-scope",
            request_id="req-user",
            scope_type="user",
            scope_user_id="user-1",
        )
        breaker.arm(
            actor="svc-runtime",
            reason="agent-scope",
            request_id="req-agent",
            scope_type="agent",
            scope_agent_id="agent-9",
        )

        blocked_user = breaker.evaluate_run_creation(user_id="user-1", agent_id="agent-1")
        self.assertTrue(bool(blocked_user.get("blocked")))
        self.assertEqual(int(blocked_user.get("matched_scope_count", -1)), 1)

        blocked_agent = breaker.evaluate_run_creation(user_id="user-2", agent_id="agent-9")
        self.assertTrue(bool(blocked_agent.get("blocked")))
        self.assertEqual(int(blocked_agent.get("matched_scope_count", -1)), 1)

        allowed = breaker.evaluate_run_creation(user_id="user-2", agent_id="agent-2")
        self.assertFalse(bool(allowed.get("blocked")))
        self.assertEqual(int(allowed.get("matched_scope_count", -1)), 0)

        snapshot = breaker.snapshot()
        self.assertTrue(bool(snapshot.get("armed")))
        self.assertEqual(int(snapshot.get("active_scope_count", -1)), 2)

    def test_persists_and_restores_scopes_after_restart(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-circuit-breaker-state-") as tmp:
            state_path = Path(tmp) / "breaker-state.json"
            breaker = AutonomyCircuitBreaker(state_path=state_path)
            breaker.arm(
                actor="svc-runtime",
                reason="persist-user",
                request_id="req-user",
                scope_type="user",
                scope_user_id="user-1",
            )
            breaker.arm(
                actor="svc-runtime",
                reason="persist-agent",
                request_id="req-agent",
                scope_type="agent",
                scope_agent_id="agent-7",
            )

            restored = AutonomyCircuitBreaker(state_path=state_path)
            snapshot = restored.snapshot()
            self.assertTrue(bool(snapshot.get("armed")))
            self.assertEqual(int(snapshot.get("active_scope_count", -1)), 2)
            persistence = snapshot.get("persistence") if isinstance(snapshot, dict) else {}
            self.assertEqual(str((persistence or {}).get("restore_status")), "restored")

            user_decision = restored.evaluate_run_creation(user_id="user-1", agent_id="agent-x")
            self.assertTrue(bool(user_decision.get("blocked")))
            agent_decision = restored.evaluate_run_creation(user_id="user-z", agent_id="agent-7")
            self.assertTrue(bool(agent_decision.get("blocked")))

    def test_corrupted_state_file_arms_fail_safe_global_scope(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-circuit-breaker-corrupted-state-") as tmp:
            state_path = Path(tmp) / "breaker-state.json"
            state_path.write_text("{not-valid-json", encoding="utf-8")

            breaker = AutonomyCircuitBreaker(state_path=state_path)
            snapshot = breaker.snapshot()
            self.assertTrue(bool(snapshot.get("armed")))
            self.assertEqual(int(snapshot.get("active_scope_count", -1)), 1)
            scopes = snapshot.get("active_scopes") if isinstance(snapshot, dict) else []
            first_scope = scopes[0] if isinstance(scopes, list) and scopes else {}
            self.assertEqual(str(first_scope.get("scope_type")), "global")
            self.assertEqual(str(first_scope.get("reason")), FAIL_SAFE_RECOVERY_REASON)
            persistence = snapshot.get("persistence") if isinstance(snapshot, dict) else {}
            self.assertEqual(str((persistence or {}).get("restore_status")), "fail_safe_armed")
            self.assertTrue(bool(str((persistence or {}).get("restore_error") or "").strip()))


if __name__ == "__main__":
    unittest.main()
