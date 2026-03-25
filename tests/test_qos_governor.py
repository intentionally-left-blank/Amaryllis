from __future__ import annotations

import unittest

from runtime.qos_governor import QoSGovernor, QoSThresholds


class QoSGovernorTests(unittest.TestCase):
    def _build_governor(self, *, mode: str = "balanced", auto_enabled: bool = True) -> QoSGovernor:
        return QoSGovernor(
            initial_mode=mode,
            auto_enabled=auto_enabled,
            thresholds=QoSThresholds(
                ttft_target_ms=1200.0,
                ttft_critical_ms=2200.0,
                request_latency_target_ms=1200.0,
                request_latency_critical_ms=2200.0,
                kv_pressure_target_events=0,
                kv_pressure_critical_events=2,
            ),
        )

    @staticmethod
    def _snapshot(
        *,
        ttft_p95_ms: float,
        request_latency_p95_ms: float,
        kv_pressure_events: int,
        fallback_rate: float = 0.0,
        thermal_hot_events: int = 0,
    ) -> dict:
        return {
            "sli": {
                "requests": {"latency_p95_ms": request_latency_p95_ms},
                "generation": {
                    "ttft_p95_ms": ttft_p95_ms,
                    "kv_pressure_events": kv_pressure_events,
                    "fallback_rate": fallback_rate,
                    "thermal_hot_events": thermal_hot_events,
                },
            }
        }

    def test_critical_pressure_demotes_to_power_save(self) -> None:
        governor = self._build_governor(mode="quality", auto_enabled=True)
        status = governor.reconcile(
            snapshot=self._snapshot(
                ttft_p95_ms=3000.0,
                request_latency_p95_ms=2600.0,
                kv_pressure_events=3,
                fallback_rate=0.7,
            )
        )
        self.assertEqual(str(status.get("active_mode")), "power_save")
        self.assertTrue(bool(status.get("changed")))
        self.assertEqual(str(status.get("reason")), "pressure_critical")
        self.assertEqual(str(status.get("route_mode")), "local_first")

    def test_recovery_promotes_back_towards_quality(self) -> None:
        governor = self._build_governor(mode="power_save", auto_enabled=True)
        recovered = self._snapshot(
            ttft_p95_ms=300.0,
            request_latency_p95_ms=350.0,
            kv_pressure_events=0,
            fallback_rate=0.0,
        )
        first = governor.reconcile(snapshot=recovered)
        second = governor.reconcile(snapshot=recovered)
        self.assertEqual(str(first.get("active_mode")), "balanced")
        self.assertEqual(str(second.get("active_mode")), "quality")
        self.assertIn(str(second.get("reason")), {"healthy_promote_quality", "healthy_hold_quality"})

    def test_manual_mode_lock_disables_auto_switch(self) -> None:
        governor = self._build_governor(mode="balanced", auto_enabled=True)
        governor.set_mode(mode="quality", auto_enabled=False)
        status = governor.reconcile(
            snapshot=self._snapshot(
                ttft_p95_ms=9999.0,
                request_latency_p95_ms=9999.0,
                kv_pressure_events=10,
                fallback_rate=1.0,
            )
        )
        self.assertEqual(str(status.get("active_mode")), "quality")
        self.assertFalse(bool(status.get("changed")))
        self.assertEqual(str(status.get("reason")), "manual_mode_locked")

    def test_manual_mode_rejects_invalid_value(self) -> None:
        governor = self._build_governor()
        with self.assertRaisesRegex(ValueError, "qos mode must be one of"):
            governor.set_mode(mode="ultra")

    def test_manual_thermal_update_rejects_invalid_value(self) -> None:
        governor = self._build_governor()
        with self.assertRaisesRegex(ValueError, "thermal_state must be one of"):
            governor.set_thermal_state(thermal_state="lava")

    def test_thermal_critical_forces_power_save(self) -> None:
        governor = self._build_governor(mode="quality", auto_enabled=True)
        status = governor.reconcile(
            snapshot=self._snapshot(
                ttft_p95_ms=200.0,
                request_latency_p95_ms=180.0,
                kv_pressure_events=0,
            ),
            thermal_state="critical",
        )
        self.assertEqual(str(status.get("active_mode")), "power_save")
        self.assertEqual(str(status.get("reason")), "thermal_critical")
        self.assertEqual(str(status.get("thermal_state")), "critical")

    def test_thermal_warm_demotes_quality(self) -> None:
        governor = self._build_governor(mode="quality", auto_enabled=True)
        status = governor.reconcile(
            snapshot=self._snapshot(
                ttft_p95_ms=250.0,
                request_latency_p95_ms=210.0,
                kv_pressure_events=0,
            ),
            thermal_state="warm",
        )
        self.assertEqual(str(status.get("active_mode")), "balanced")
        self.assertEqual(str(status.get("reason")), "thermal_warm_demote_quality")


if __name__ == "__main__":
    unittest.main()
