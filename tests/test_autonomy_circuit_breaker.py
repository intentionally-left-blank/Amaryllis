from __future__ import annotations

import unittest

from runtime.autonomy_circuit_breaker import AutonomyCircuitBreaker


class AutonomyCircuitBreakerTests(unittest.TestCase):
    def test_default_state_is_disarmed(self) -> None:
        breaker = AutonomyCircuitBreaker()
        snapshot = breaker.snapshot()
        self.assertFalse(bool(snapshot.get("armed")))
        self.assertEqual(str(snapshot.get("status")), "disarmed")
        self.assertEqual(int(snapshot.get("revision", -1)), 0)
        self.assertTrue(bool(str(snapshot.get("updated_at") or "").strip()))

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

        disarmed = breaker.disarm(actor="svc-runtime", reason="incident mitigated", request_id="req-2")
        self.assertFalse(bool(disarmed.get("armed")))
        self.assertEqual(str(disarmed.get("status")), "disarmed")
        self.assertEqual(int(disarmed.get("revision", -1)), 2)
        self.assertEqual(str(disarmed.get("disarmed_by")), "svc-runtime")
        self.assertEqual(str(disarmed.get("reason")), "incident mitigated")
        self.assertEqual(str(disarmed.get("request_id")), "req-2")
        self.assertTrue(bool(str(disarmed.get("disarmed_at") or "").strip()))


if __name__ == "__main__":
    unittest.main()
