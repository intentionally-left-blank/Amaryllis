from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any


SUPPORTED_QOS_MODES: tuple[str, ...] = ("quality", "balanced", "power_save")
SUPPORTED_THERMAL_STATES: tuple[str, ...] = ("unknown", "cool", "warm", "hot", "critical")

_QOS_ROUTE_MODE_MAP: dict[str, str] = {
    "quality": "quality_first",
    "balanced": "balanced",
    "power_save": "local_first",
}


def is_supported_qos_mode(value: str | None) -> bool:
    return str(value or "").strip().lower() in SUPPORTED_QOS_MODES


def is_supported_thermal_state(value: str | None) -> bool:
    return str(value or "").strip().lower() in SUPPORTED_THERMAL_STATES


def normalize_qos_mode(value: str | None, *, fallback: str = "balanced") -> str:
    normalized = str(value or "").strip().lower()
    if normalized in SUPPORTED_QOS_MODES:
        return normalized
    if str(fallback or "").strip().lower() in SUPPORTED_QOS_MODES:
        return str(fallback).strip().lower()
    return "balanced"


def qos_mode_to_route_mode(mode: str | None) -> str:
    normalized = normalize_qos_mode(mode)
    return _QOS_ROUTE_MODE_MAP.get(normalized, "balanced")


def normalize_thermal_state(value: str | None, *, fallback: str = "unknown") -> str:
    normalized = str(value or "").strip().lower()
    if normalized in SUPPORTED_THERMAL_STATES:
        return normalized
    fallback_normalized = str(fallback or "").strip().lower()
    if fallback_normalized in SUPPORTED_THERMAL_STATES:
        return fallback_normalized
    return "unknown"


@dataclass(frozen=True)
class QoSThresholds:
    ttft_target_ms: float
    ttft_critical_ms: float
    request_latency_target_ms: float
    request_latency_critical_ms: float
    kv_pressure_target_events: int
    kv_pressure_critical_events: int


class QoSGovernor:
    def __init__(
        self,
        *,
        initial_mode: str = "balanced",
        initial_thermal_state: str = "unknown",
        auto_enabled: bool = True,
        thresholds: QoSThresholds,
    ) -> None:
        self._mode = normalize_qos_mode(initial_mode)
        self._thermal_state = normalize_thermal_state(initial_thermal_state)
        self._auto_enabled = bool(auto_enabled)
        self._thresholds = thresholds
        self._last_reason = "startup"
        self._lock = Lock()

    @property
    def mode(self) -> str:
        with self._lock:
            return self._mode

    @property
    def auto_enabled(self) -> bool:
        with self._lock:
            return self._auto_enabled

    @property
    def thermal_state(self) -> str:
        with self._lock:
            return self._thermal_state

    def reconcile(
        self,
        *,
        snapshot: dict[str, Any] | None = None,
        thermal_state: str | None = None,
    ) -> dict[str, Any]:
        metrics = _extract_metrics(snapshot)
        with self._lock:
            if thermal_state is not None:
                self._thermal_state = normalize_thermal_state(thermal_state, fallback=self._thermal_state)
            active_thermal_state = self._thermal_state
            current = self._mode
            if not self._auto_enabled:
                target = current
                reason = "manual_mode_locked"
            else:
                target, reason = self._target_mode(
                    current_mode=current,
                    metrics=metrics,
                    thermal_state=active_thermal_state,
                )
            changed = target != current
            if changed:
                self._mode = target
            self._last_reason = reason
            active_mode = self._mode
            auto_enabled = self._auto_enabled

        return {
            "active_mode": active_mode,
            "route_mode": qos_mode_to_route_mode(active_mode),
            "auto_enabled": auto_enabled,
            "thermal_state": active_thermal_state,
            "changed": bool(changed),
            "reason": reason,
            "thresholds": {
                "ttft_target_ms": float(self._thresholds.ttft_target_ms),
                "ttft_critical_ms": float(self._thresholds.ttft_critical_ms),
                "request_latency_target_ms": float(self._thresholds.request_latency_target_ms),
                "request_latency_critical_ms": float(self._thresholds.request_latency_critical_ms),
                "kv_pressure_target_events": int(self._thresholds.kv_pressure_target_events),
                "kv_pressure_critical_events": int(self._thresholds.kv_pressure_critical_events),
            },
            "metrics": metrics,
        }

    def set_mode(
        self,
        *,
        mode: str | None = None,
        auto_enabled: bool | None = None,
        thermal_state: str | None = None,
        snapshot: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if mode is not None:
                requested = str(mode or "").strip().lower()
                if requested not in SUPPORTED_QOS_MODES:
                    raise ValueError(
                        "qos mode must be one of: " + ", ".join(SUPPORTED_QOS_MODES)
                    )
                self._mode = requested
                self._last_reason = "manual_mode_update"
            if auto_enabled is not None:
                self._auto_enabled = bool(auto_enabled)
                self._last_reason = (
                    "manual_auto_enable" if self._auto_enabled else "manual_auto_disable"
                )
            if thermal_state is not None:
                requested_thermal_state = str(thermal_state or "").strip().lower()
                if not is_supported_thermal_state(requested_thermal_state):
                    raise ValueError(
                        "thermal_state must be one of: " + ", ".join(SUPPORTED_THERMAL_STATES)
                    )
                self._thermal_state = requested_thermal_state
                self._last_reason = "manual_thermal_update"
        return self.reconcile(snapshot=snapshot)

    def set_thermal_state(self, *, thermal_state: str, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            requested_thermal_state = str(thermal_state or "").strip().lower()
            if not is_supported_thermal_state(requested_thermal_state):
                raise ValueError(
                    "thermal_state must be one of: " + ", ".join(SUPPORTED_THERMAL_STATES)
                )
            self._thermal_state = requested_thermal_state
            self._last_reason = "manual_thermal_update"
        return self.reconcile(snapshot=snapshot)

    def _target_mode(
        self,
        *,
        current_mode: str,
        metrics: dict[str, float],
        thermal_state: str,
    ) -> tuple[str, str]:
        ttft_p95 = float(metrics.get("ttft_p95_ms") or 0.0)
        request_p95 = float(metrics.get("request_latency_p95_ms") or 0.0)
        kv_pressure_events = int(round(float(metrics.get("kv_pressure_events") or 0.0)))
        fallback_rate = float(metrics.get("fallback_rate") or 0.0)
        thermal_hot_events = int(round(float(metrics.get("thermal_hot_events") or 0.0)))

        normalized_thermal_state = normalize_thermal_state(thermal_state)
        if normalized_thermal_state == "critical":
            return "power_save", "thermal_critical"
        if normalized_thermal_state == "hot":
            if current_mode == "quality":
                return "balanced", "thermal_hot_demote_quality"
            strong_hot_pressure = (
                ttft_p95 > (float(self._thresholds.ttft_target_ms) * 1.15)
                or request_p95 > (float(self._thresholds.request_latency_target_ms) * 1.15)
                or fallback_rate >= 0.2
                or thermal_hot_events >= 1
            )
            if strong_hot_pressure:
                return "power_save", "thermal_hot_demote_power_save"
            if current_mode == "power_save":
                return "power_save", "thermal_hot_hold_power_save"
            return "balanced", "thermal_hot_hold_balanced"
        if normalized_thermal_state == "warm" and current_mode == "quality":
            return "balanced", "thermal_warm_demote_quality"

        critical = (
            ttft_p95 > float(self._thresholds.ttft_critical_ms)
            or request_p95 > float(self._thresholds.request_latency_critical_ms)
            or kv_pressure_events >= int(self._thresholds.kv_pressure_critical_events)
            or fallback_rate >= 0.55
            or thermal_hot_events >= max(1, int(self._thresholds.kv_pressure_critical_events))
        )
        elevated = (
            ttft_p95 > float(self._thresholds.ttft_target_ms)
            or request_p95 > float(self._thresholds.request_latency_target_ms)
            or kv_pressure_events > int(self._thresholds.kv_pressure_target_events)
            or fallback_rate >= 0.2
            or thermal_hot_events > 0
        )
        healthy = (
            ttft_p95 <= (float(self._thresholds.ttft_target_ms) * 0.75)
            and request_p95 <= (float(self._thresholds.request_latency_target_ms) * 0.8)
            and kv_pressure_events <= int(self._thresholds.kv_pressure_target_events)
            and fallback_rate < 0.05
            and thermal_hot_events <= 0
        )

        if critical:
            return "power_save", "pressure_critical"

        if elevated:
            if current_mode == "quality":
                return "balanced", "pressure_elevated_demote_quality"
            if current_mode == "balanced":
                strong_pressure = (
                    ttft_p95 > (float(self._thresholds.ttft_target_ms) * 1.4)
                    or request_p95 > (float(self._thresholds.request_latency_target_ms) * 1.4)
                    or kv_pressure_events >= max(1, int(self._thresholds.kv_pressure_critical_events) - 1)
                    or fallback_rate >= 0.35
                )
                if strong_pressure:
                    return "power_save", "pressure_elevated_demote_power_save"
                return "balanced", "pressure_elevated_hold_balanced"
            return "power_save", "pressure_elevated_hold_power_save"

        if healthy:
            if current_mode == "power_save":
                return "balanced", "recovered_promote_balanced"
            if normalize_thermal_state(thermal_state) == "warm":
                return "balanced", "thermal_warm_hold_balanced"
            if current_mode == "balanced":
                return "quality", "healthy_promote_quality"
            return "quality", "healthy_hold_quality"

        return current_mode, "stable_hold"


def _extract_metrics(snapshot: dict[str, Any] | None) -> dict[str, float]:
    if not isinstance(snapshot, dict):
        return {
            "ttft_p95_ms": 0.0,
            "request_latency_p95_ms": 0.0,
            "kv_pressure_events": 0.0,
            "fallback_rate": 0.0,
            "thermal_hot_events": 0.0,
        }

    sli = snapshot.get("sli")
    if not isinstance(sli, dict):
        sli = {}
    generation = sli.get("generation")
    if not isinstance(generation, dict):
        generation = {}
    requests = sli.get("requests")
    if not isinstance(requests, dict):
        requests = {}

    try:
        kv_pressure = float(generation.get("kv_pressure_events") or 0.0)
    except Exception:
        kv_pressure = 0.0
    try:
        thermal_hot_events = float(generation.get("thermal_hot_events") or 0.0)
    except Exception:
        thermal_hot_events = 0.0

    return {
        "ttft_p95_ms": _safe_float(generation.get("ttft_p95_ms")),
        "request_latency_p95_ms": _safe_float(requests.get("latency_p95_ms")),
        "kv_pressure_events": max(0.0, kv_pressure),
        "fallback_rate": _safe_float(generation.get("fallback_rate")),
        "thermal_hot_events": max(0.0, thermal_hot_events),
    }


def _safe_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)
