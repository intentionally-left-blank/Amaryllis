from __future__ import annotations

import json
import logging
import math
import os
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Protocol
from uuid import uuid4

try:
    from opentelemetry import context as otel_context
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    from opentelemetry.trace import SpanKind
    from opentelemetry.trace.status import Status, StatusCode
except Exception:  # pragma: no cover - optional dependency
    otel_context = None  # type: ignore[assignment]
    otel_trace = None  # type: ignore[assignment]
    Resource = None  # type: ignore[assignment]
    TracerProvider = None  # type: ignore[assignment]
    BatchSpanProcessor = None  # type: ignore[assignment]
    ConsoleSpanExporter = None  # type: ignore[assignment]
    SpanKind = None  # type: ignore[assignment]
    Status = None  # type: ignore[assignment]
    StatusCode = None  # type: ignore[assignment]

try:
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
except Exception:  # pragma: no cover - optional dependency
    OTLPSpanExporter = None  # type: ignore[assignment]


class TelemetrySink(Protocol):
    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        ...


@dataclass(frozen=True)
class SLOTargets:
    window_sec: float
    request_availability_target: float
    request_latency_p95_ms_target: float
    run_success_target: float
    min_request_samples: int
    min_run_samples: int
    incident_cooldown_sec: float


@dataclass
class RequestSpanContext:
    span: Any | None
    token: Any | None
    trace_id: str
    path: str
    method: str
    request_id: str
    started_at: float


class SREMonitor:
    def __init__(self, *, targets: SLOTargets, logger: logging.Logger) -> None:
        self.targets = targets
        self.logger = logger
        self._release_quality_dashboard_path = (
            str(os.getenv("AMARYLLIS_RELEASE_QUALITY_DASHBOARD_PATH") or "").strip() or None
        )
        self._nightly_mission_report_path = (
            str(os.getenv("AMARYLLIS_NIGHTLY_MISSION_REPORT_PATH") or "").strip() or None
        )
        self._lock = Lock()
        self._http_events: deque[dict[str, Any]] = deque(maxlen=20000)
        self._run_events: deque[dict[str, Any]] = deque(maxlen=20000)
        self._generation_events: deque[dict[str, Any]] = deque(maxlen=20000)
        self._incidents: deque[dict[str, Any]] = deque(maxlen=2000)
        self._active_incidents: dict[str, float] = {}
        self._recent_snapshots: deque[dict[str, Any]] = deque(maxlen=32)

    @staticmethod
    def _now_epoch() -> float:
        return time.time()

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    def record_http(
        self,
        *,
        method: str,
        path: str,
        status_code: int,
        duration_ms: float,
        request_id: str | None = None,
        error_type: str | None = None,
    ) -> None:
        row = {
            "ts": self._now_epoch(),
            "method": str(method or "").upper(),
            "path": str(path or ""),
            "status_code": int(status_code),
            "duration_ms": float(max(0.0, duration_ms)),
            "request_id": str(request_id or ""),
            "error_type": str(error_type or ""),
        }
        with self._lock:
            self._http_events.append(row)
            self._prune_unlocked()
            self._evaluate_incidents_unlocked()

    def record_run_terminal(
        self,
        *,
        status: str,
        run_id: str | None = None,
        failure_class: str | None = None,
        stop_reason: str | None = None,
        duration_ms: float | None = None,
    ) -> None:
        normalized_status = str(status or "").strip().lower() or "unknown"
        row = {
            "ts": self._now_epoch(),
            "status": normalized_status,
            "run_id": str(run_id or ""),
            "failure_class": str(failure_class or ""),
            "stop_reason": str(stop_reason or ""),
            "duration_ms": float(max(0.0, duration_ms or 0.0)),
        }
        with self._lock:
            self._run_events.append(row)
            self._prune_unlocked()
            self._evaluate_incidents_unlocked()

    def record_generation_loop(
        self,
        *,
        request_id: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        stream: bool = False,
        fallback_used: bool = False,
        ttft_ms: float | None = None,
        total_latency_ms: float | None = None,
        kv_pressure_state: str | None = None,
        thermal_state: str | None = None,
    ) -> None:
        row = {
            "ts": self._now_epoch(),
            "request_id": str(request_id or ""),
            "provider": str(provider or ""),
            "model": str(model or ""),
            "stream": bool(stream),
            "fallback_used": bool(fallback_used),
            "ttft_ms": float(max(0.0, ttft_ms or 0.0)),
            "total_latency_ms": float(max(0.0, total_latency_ms or 0.0)),
            "kv_pressure_state": str(kv_pressure_state or "unknown").strip().lower() or "unknown",
            "thermal_state": str(thermal_state or "unknown").strip().lower() or "unknown",
        }
        with self._lock:
            self._generation_events.append(row)
            self._prune_unlocked()
            self._evaluate_incidents_unlocked()

    def ingest_event(self, event_type: str, payload: dict[str, Any]) -> None:
        normalized_type = str(event_type or "").strip().lower()
        data = dict(payload or {})
        if normalized_type == "request_done":
            self.record_http(
                method=str(data.get("method") or ""),
                path=str(data.get("path") or ""),
                status_code=int(data.get("status_code") or 0),
                duration_ms=float(data.get("duration_ms") or 0.0),
                request_id=str(data.get("request_id") or ""),
            )
            return
        if normalized_type == "request_error":
            self.record_http(
                method=str(data.get("method") or ""),
                path=str(data.get("path") or ""),
                status_code=int(data.get("status_code") or 0),
                duration_ms=float(data.get("duration_ms") or 0.0),
                request_id=str(data.get("request_id") or ""),
                error_type=str(data.get("error_type") or ""),
            )
            return
        if normalized_type in {"agent_run_succeeded", "agent_run_failed", "agent_run_canceled"}:
            status = normalized_type.removeprefix("agent_run_")
            self.record_run_terminal(
                status=status,
                run_id=str(data.get("run_id") or ""),
                failure_class=str(data.get("failure_class") or ""),
                stop_reason=str(data.get("stop_reason") or ""),
                duration_ms=float(data.get("duration_ms") or 0.0),
            )
            return
        if normalized_type == "generation_loop_metrics":
            kv_cache = data.get("kv_cache") if isinstance(data.get("kv_cache"), dict) else {}
            self.record_generation_loop(
                request_id=str(data.get("request_id") or ""),
                provider=str(data.get("provider") or ""),
                model=str(data.get("model") or ""),
                stream=bool(data.get("stream", False)),
                fallback_used=bool(data.get("fallback_used", False)),
                ttft_ms=float(data.get("ttft_ms") or 0.0),
                total_latency_ms=float(data.get("total_latency_ms") or 0.0),
                kv_pressure_state=str(kv_cache.get("pressure_state") or "unknown"),
                thermal_state=str(data.get("thermal_state") or "unknown"),
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._prune_unlocked()
            snapshot = self._build_snapshot_unlocked()
            self._recent_snapshots.append(snapshot)
            return snapshot

    def list_incidents(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._incidents)[-max(1, min(int(limit), 1000)) :]
        return list(reversed(items))

    def render_prometheus_metrics(self) -> str:
        snapshot = self.snapshot()
        request_sli = snapshot["sli"]["requests"]
        run_sli = snapshot["sli"]["runs"]
        generation_sli = snapshot["sli"]["generation"]
        budgets = snapshot["error_budget"]
        release_quality = self._release_quality_snapshot_metrics()
        nightly_mission = self._nightly_mission_snapshot_metrics()
        lines = [
            "# HELP amaryllis_requests_total HTTP requests observed in SLO window.",
            "# TYPE amaryllis_requests_total gauge",
            f"amaryllis_requests_total {float(request_sli['total']):.0f}",
            "# HELP amaryllis_request_availability_ratio Availability ratio (non-5xx).",
            "# TYPE amaryllis_request_availability_ratio gauge",
            f"amaryllis_request_availability_ratio {float(request_sli['availability']):.6f}",
            "# HELP amaryllis_request_latency_p95_ms HTTP latency p95 in milliseconds.",
            "# TYPE amaryllis_request_latency_p95_ms gauge",
            f"amaryllis_request_latency_p95_ms {float(request_sli['latency_p95_ms']):.3f}",
            "# HELP amaryllis_runs_total Terminal runs observed in SLO window.",
            "# TYPE amaryllis_runs_total gauge",
            f"amaryllis_runs_total {float(run_sli['total']):.0f}",
            "# HELP amaryllis_run_success_ratio Agent run success ratio.",
            "# TYPE amaryllis_run_success_ratio gauge",
            f"amaryllis_run_success_ratio {float(run_sli['success_rate']):.6f}",
            "# HELP amaryllis_generation_events_total Generation loop events observed in SLO window.",
            "# TYPE amaryllis_generation_events_total gauge",
            f"amaryllis_generation_events_total {float(generation_sli['total']):.0f}",
            "# HELP amaryllis_generation_ttft_p95_ms Generation TTFT p95 in milliseconds.",
            "# TYPE amaryllis_generation_ttft_p95_ms gauge",
            f"amaryllis_generation_ttft_p95_ms {float(generation_sli['ttft_p95_ms']):.6f}",
            "# HELP amaryllis_generation_total_latency_p95_ms Generation total latency p95 in milliseconds.",
            "# TYPE amaryllis_generation_total_latency_p95_ms gauge",
            f"amaryllis_generation_total_latency_p95_ms {float(generation_sli['total_latency_p95_ms']):.6f}",
            "# HELP amaryllis_generation_fallback_rate Generation fallback usage ratio.",
            "# TYPE amaryllis_generation_fallback_rate gauge",
            f"amaryllis_generation_fallback_rate {float(generation_sli['fallback_rate']):.6f}",
            "# HELP amaryllis_generation_kv_pressure_events_total Generation events with high/critical KV pressure.",
            "# TYPE amaryllis_generation_kv_pressure_events_total gauge",
            f"amaryllis_generation_kv_pressure_events_total {float(generation_sli['kv_pressure_events']):.0f}",
            "# HELP amaryllis_generation_thermal_hot_events_total Generation events observed in hot/critical thermal states.",
            "# TYPE amaryllis_generation_thermal_hot_events_total gauge",
            f"amaryllis_generation_thermal_hot_events_total {float(generation_sli['thermal_hot_events']):.0f}",
            "# HELP amaryllis_error_budget_remaining_ratio Remaining request error budget ratio.",
            "# TYPE amaryllis_error_budget_remaining_ratio gauge",
            f"amaryllis_error_budget_remaining_ratio{{scope=\"requests\"}} "
            f"{float(budgets['requests']['remaining_ratio']):.6f}",
            f"amaryllis_error_budget_remaining_ratio{{scope=\"runs\"}} "
            f"{float(budgets['runs']['remaining_ratio']):.6f}",
            "# HELP amaryllis_error_budget_burn_rate Error budget burn rate.",
            "# TYPE amaryllis_error_budget_burn_rate gauge",
            f"amaryllis_error_budget_burn_rate{{scope=\"requests\"}} {float(budgets['requests']['burn_rate']):.6f}",
            f"amaryllis_error_budget_burn_rate{{scope=\"runs\"}} {float(budgets['runs']['burn_rate']):.6f}",
            "# HELP amaryllis_open_incidents_total Open incidents count.",
            "# TYPE amaryllis_open_incidents_total gauge",
            f"amaryllis_open_incidents_total {float(snapshot['incidents']['open_count']):.0f}",
            "# HELP amaryllis_release_quality_snapshot_loaded Release quality dashboard snapshot availability (0/1).",
            "# TYPE amaryllis_release_quality_snapshot_loaded gauge",
            f"amaryllis_release_quality_snapshot_loaded {float(release_quality['snapshot_loaded']):.0f}",
            "# HELP amaryllis_release_quality_score_pct Release quality score percent from latest snapshot.",
            "# TYPE amaryllis_release_quality_score_pct gauge",
            f"amaryllis_release_quality_score_pct {float(release_quality['quality_score_pct']):.6f}",
            "# HELP amaryllis_release_quality_signals_failed Failed release quality signals count.",
            "# TYPE amaryllis_release_quality_signals_failed gauge",
            f"amaryllis_release_quality_signals_failed {float(release_quality['signals_failed']):.6f}",
            "# HELP amaryllis_release_quality_status Release quality status (1=pass, 0=fail).",
            "# TYPE amaryllis_release_quality_status gauge",
            f"amaryllis_release_quality_status {float(release_quality['status']):.6f}",
            "# HELP amaryllis_release_desktop_staging_signal_present Desktop staging parity signal presence (0/1).",
            "# TYPE amaryllis_release_desktop_staging_signal_present gauge",
            f"amaryllis_release_desktop_staging_signal_present {float(release_quality['desktop_staging_signal_present']):.0f}",
            "# HELP amaryllis_release_desktop_staging_status Desktop staging parity status (1=pass, 0=fail).",
            "# TYPE amaryllis_release_desktop_staging_status gauge",
            f"amaryllis_release_desktop_staging_status {float(release_quality['desktop_staging_status']):.6f}",
            "# HELP amaryllis_release_desktop_staging_error_rate_pct Desktop staging parity error-rate percent.",
            "# TYPE amaryllis_release_desktop_staging_error_rate_pct gauge",
            f"amaryllis_release_desktop_staging_error_rate_pct {float(release_quality['desktop_staging_error_rate_pct']):.6f}",
            "# HELP amaryllis_release_desktop_staging_checks_failed Desktop staging parity failed checks count.",
            "# TYPE amaryllis_release_desktop_staging_checks_failed gauge",
            f"amaryllis_release_desktop_staging_checks_failed {float(release_quality['desktop_staging_checks_failed']):.6f}",
            "# HELP amaryllis_release_qos_signal_present QoS governor signal presence in release quality snapshot (0/1).",
            "# TYPE amaryllis_release_qos_signal_present gauge",
            f"amaryllis_release_qos_signal_present {float(release_quality['qos_signal_present']):.0f}",
            "# HELP amaryllis_release_qos_status QoS governor gate status in release quality snapshot (1=pass, 0=fail).",
            "# TYPE amaryllis_release_qos_status gauge",
            f"amaryllis_release_qos_status {float(release_quality['qos_status']):.6f}",
            "# HELP amaryllis_release_qos_checks_failed QoS governor gate failed checks in release quality snapshot.",
            "# TYPE amaryllis_release_qos_checks_failed gauge",
            f"amaryllis_release_qos_checks_failed {float(release_quality['qos_checks_failed']):.6f}",
            "# HELP amaryllis_nightly_mission_snapshot_loaded Nightly mission report snapshot availability (0/1).",
            "# TYPE amaryllis_nightly_mission_snapshot_loaded gauge",
            f"amaryllis_nightly_mission_snapshot_loaded {float(nightly_mission['snapshot_loaded']):.0f}",
            "# HELP amaryllis_nightly_mission_status Nightly mission status (1=pass, 0=fail).",
            "# TYPE amaryllis_nightly_mission_status gauge",
            f"amaryllis_nightly_mission_status {float(nightly_mission['status']):.6f}",
            "# HELP amaryllis_nightly_success_rate_pct Nightly mission success-rate percent.",
            "# TYPE amaryllis_nightly_success_rate_pct gauge",
            f"amaryllis_nightly_success_rate_pct {float(nightly_mission['success_rate_pct']):.6f}",
            "# HELP amaryllis_nightly_p95_latency_ms Nightly mission p95 latency in milliseconds.",
            "# TYPE amaryllis_nightly_p95_latency_ms gauge",
            f"amaryllis_nightly_p95_latency_ms {float(nightly_mission['p95_latency_ms']):.6f}",
            "# HELP amaryllis_nightly_latency_jitter_ms Nightly mission latency jitter in milliseconds.",
            "# TYPE amaryllis_nightly_latency_jitter_ms gauge",
            f"amaryllis_nightly_latency_jitter_ms {float(nightly_mission['latency_jitter_ms']):.6f}",
            "# HELP amaryllis_nightly_burn_rate_gate_passed Nightly burn-rate gate status (1=pass, 0=fail).",
            "# TYPE amaryllis_nightly_burn_rate_gate_passed gauge",
            f"amaryllis_nightly_burn_rate_gate_passed {float(nightly_mission['burn_rate_gate_passed']):.6f}",
        ]
        return "\n".join(lines) + "\n"

    @staticmethod
    def _safe_float(value: Any, *, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return float(default)

    @staticmethod
    def _signal_value(payload: dict[str, Any], metric_id: str) -> float | None:
        signals = payload.get("signals")
        if not isinstance(signals, list):
            return None
        for item in signals:
            if not isinstance(item, dict):
                continue
            if str(item.get("metric_id") or "").strip() != metric_id:
                continue
            try:
                return float(item.get("value"))
            except Exception:
                return None
        return None

    def _release_quality_snapshot_metrics(self) -> dict[str, float]:
        metrics = {
            "snapshot_loaded": 0.0,
            "quality_score_pct": 0.0,
            "signals_failed": 0.0,
            "status": 0.0,
            "desktop_staging_signal_present": 0.0,
            "desktop_staging_status": 0.0,
            "desktop_staging_error_rate_pct": 0.0,
            "desktop_staging_checks_failed": 0.0,
            "qos_signal_present": 0.0,
            "qos_status": 0.0,
            "qos_checks_failed": 0.0,
        }
        path_raw = str(self._release_quality_dashboard_path or "").strip()
        if not path_raw:
            return metrics

        path = Path(path_raw)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return metrics
        if not isinstance(payload, dict):
            return metrics

        metrics["snapshot_loaded"] = 1.0
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        metrics["quality_score_pct"] = self._safe_float(summary.get("quality_score_pct"), default=0.0)
        metrics["signals_failed"] = max(0.0, self._safe_float(summary.get("signals_failed"), default=0.0))
        metrics["status"] = 1.0 if str(summary.get("status") or "").strip().lower() == "pass" else 0.0

        desktop_status = self._signal_value(payload, "macos_desktop_parity.status")
        desktop_error_rate = self._signal_value(payload, "macos_desktop_parity.error_rate_pct")
        desktop_checks_failed = self._signal_value(payload, "macos_desktop_parity.checks_failed")
        if desktop_status is not None or desktop_error_rate is not None or desktop_checks_failed is not None:
            metrics["desktop_staging_signal_present"] = 1.0
        if desktop_status is not None:
            metrics["desktop_staging_status"] = float(desktop_status)
        if desktop_error_rate is not None:
            metrics["desktop_staging_error_rate_pct"] = max(0.0, float(desktop_error_rate))
        if desktop_checks_failed is not None:
            metrics["desktop_staging_checks_failed"] = max(0.0, float(desktop_checks_failed))

        qos_status = self._signal_value(payload, "qos_governor.status")
        qos_checks_failed = self._signal_value(payload, "qos_governor.checks_failed")
        if qos_status is not None or qos_checks_failed is not None:
            metrics["qos_signal_present"] = 1.0
        if qos_status is not None:
            metrics["qos_status"] = 1.0 if float(qos_status) >= 1.0 else 0.0
        if qos_checks_failed is not None:
            metrics["qos_checks_failed"] = max(0.0, float(qos_checks_failed))
        return metrics

    def _nightly_mission_snapshot_metrics(self) -> dict[str, float]:
        metrics = {
            "snapshot_loaded": 0.0,
            "status": 0.0,
            "success_rate_pct": 0.0,
            "p95_latency_ms": 0.0,
            "latency_jitter_ms": 0.0,
            "burn_rate_gate_passed": 0.0,
        }
        path_raw = str(self._nightly_mission_report_path or "").strip()
        if not path_raw:
            return metrics

        path = Path(path_raw)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return metrics
        if not isinstance(payload, dict):
            return metrics
        if str(payload.get("suite") or "").strip() != "mission_success_recovery_report_pack_v2":
            return metrics

        metrics["snapshot_loaded"] = 1.0
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
        metrics["status"] = 1.0 if str(summary.get("status") or "").strip().lower() == "pass" else 0.0

        kpis = payload.get("kpis") if isinstance(payload.get("kpis"), dict) else {}
        metrics["success_rate_pct"] = max(
            0.0,
            self._safe_float(kpis.get("nightly_success_rate_pct"), default=0.0),
        )
        metrics["p95_latency_ms"] = max(
            0.0,
            self._safe_float(kpis.get("nightly_p95_latency_ms"), default=0.0),
        )
        metrics["latency_jitter_ms"] = max(
            0.0,
            self._safe_float(kpis.get("nightly_latency_jitter_ms"), default=0.0),
        )
        burn_gate = kpis.get("nightly_burn_rate_gate_passed")
        if isinstance(burn_gate, bool):
            metrics["burn_rate_gate_passed"] = 1.0 if burn_gate else 0.0
        else:
            metrics["burn_rate_gate_passed"] = 1.0 if self._safe_float(burn_gate, default=0.0) >= 1.0 else 0.0
        return metrics

    def _prune_unlocked(self) -> None:
        now = self._now_epoch()
        cutoff = now - max(1.0, float(self.targets.window_sec))
        while self._http_events and float(self._http_events[0].get("ts", 0.0)) < cutoff:
            self._http_events.popleft()
        while self._run_events and float(self._run_events[0].get("ts", 0.0)) < cutoff:
            self._run_events.popleft()
        while self._generation_events and float(self._generation_events[0].get("ts", 0.0)) < cutoff:
            self._generation_events.popleft()

    def _build_snapshot_unlocked(self) -> dict[str, Any]:
        request_events = list(self._http_events)
        run_events = list(self._run_events)
        generation_events = list(self._generation_events)

        request_total = len(request_events)
        request_ok = sum(1 for item in request_events if int(item.get("status_code", 0)) < 500)
        availability = (float(request_ok) / float(request_total)) if request_total else 1.0
        request_latencies = [float(item.get("duration_ms", 0.0)) for item in request_events]
        p95_ms = self._quantile(request_latencies, 0.95)

        terminal_runs = [item for item in run_events if str(item.get("status") or "") in {"succeeded", "failed", "canceled"}]
        run_total = len(terminal_runs)
        run_success = sum(1 for item in terminal_runs if str(item.get("status") or "") == "succeeded")
        run_success_rate = (float(run_success) / float(run_total)) if run_total else 1.0

        generation_total = len(generation_events)
        generation_stream_total = sum(1 for item in generation_events if bool(item.get("stream", False)))
        generation_fallback_total = sum(1 for item in generation_events if bool(item.get("fallback_used", False)))
        generation_ttft_values = [float(item.get("ttft_ms", 0.0)) for item in generation_events]
        generation_total_latency_values = [float(item.get("total_latency_ms", 0.0)) for item in generation_events]
        generation_kv_pressure_events = sum(
            1
            for item in generation_events
            if str(item.get("kv_pressure_state") or "").strip().lower() in {"high", "critical"}
        )
        generation_thermal_hot_events = sum(
            1
            for item in generation_events
            if str(item.get("thermal_state") or "").strip().lower() in {"hot", "critical"}
        )
        generation_fallback_rate = (
            float(generation_fallback_total) / float(generation_total)
            if generation_total
            else 0.0
        )

        request_budget = self._budget_ratio(observed=availability, target=self.targets.request_availability_target)
        run_budget = self._budget_ratio(observed=run_success_rate, target=self.targets.run_success_target)

        return {
            "timestamp": self._now_iso(),
            "window_sec": float(self.targets.window_sec),
            "slo": {
                "request_availability_target": float(self.targets.request_availability_target),
                "request_latency_p95_ms_target": float(self.targets.request_latency_p95_ms_target),
                "run_success_target": float(self.targets.run_success_target),
                "min_request_samples": int(self.targets.min_request_samples),
                "min_run_samples": int(self.targets.min_run_samples),
            },
            "sli": {
                "requests": {
                    "total": request_total,
                    "successful": request_ok,
                    "availability": round(availability, 6),
                    "latency_p95_ms": round(p95_ms, 3),
                },
                "runs": {
                    "total": run_total,
                    "successful": run_success,
                    "success_rate": round(run_success_rate, 6),
                },
                "generation": {
                    "total": generation_total,
                    "stream_total": generation_stream_total,
                    "fallback_total": generation_fallback_total,
                    "fallback_rate": round(generation_fallback_rate, 6),
                    "ttft_p95_ms": round(self._quantile(generation_ttft_values, 0.95), 3),
                    "total_latency_p95_ms": round(self._quantile(generation_total_latency_values, 0.95), 3),
                    "kv_pressure_events": generation_kv_pressure_events,
                    "thermal_hot_events": generation_thermal_hot_events,
                },
            },
            "error_budget": {
                "requests": request_budget,
                "runs": run_budget,
            },
            "incidents": {
                "open_count": len(self._active_incidents),
                "recent": list(self._incidents)[-10:],
            },
        }

    def _evaluate_incidents_unlocked(self) -> None:
        snapshot = self._build_snapshot_unlocked()
        now = self._now_epoch()
        breaches: dict[str, dict[str, Any]] = {}

        request_sli = snapshot["sli"]["requests"]
        run_sli = snapshot["sli"]["runs"]
        targets = snapshot["slo"]

        request_total = int(request_sli["total"])
        if request_total >= int(targets["min_request_samples"]):
            if float(request_sli["availability"]) < float(targets["request_availability_target"]):
                breaches["request_availability"] = {
                    "severity": "high",
                    "message": "HTTP availability is below SLO target.",
                    "value": float(request_sli["availability"]),
                    "target": float(targets["request_availability_target"]),
                }
            if float(request_sli["latency_p95_ms"]) > float(targets["request_latency_p95_ms_target"]):
                breaches["request_latency_p95"] = {
                    "severity": "medium",
                    "message": "HTTP latency p95 exceeded SLO threshold.",
                    "value": float(request_sli["latency_p95_ms"]),
                    "target": float(targets["request_latency_p95_ms_target"]),
                }

        run_total = int(run_sli["total"])
        if run_total >= int(targets["min_run_samples"]):
            if float(run_sli["success_rate"]) < float(targets["run_success_target"]):
                breaches["run_success_rate"] = {
                    "severity": "high",
                    "message": "Run success rate is below SLO target.",
                    "value": float(run_sli["success_rate"]),
                    "target": float(targets["run_success_target"]),
                }

        cooldown = max(1.0, float(self.targets.incident_cooldown_sec))
        for key, breach in breaches.items():
            last = float(self._active_incidents.get(key, 0.0))
            if now - last < cooldown:
                continue
            incident = {
                "id": str(uuid4()),
                "opened_at": self._now_iso(),
                "state": "open",
                "type": key,
                "severity": str(breach["severity"]),
                "message": str(breach["message"]),
                "value": float(breach["value"]),
                "target": float(breach["target"]),
            }
            self._incidents.append(incident)
            self._active_incidents[key] = now
            self.logger.error(
                "sre_incident_open type=%s severity=%s value=%.6f target=%.6f message=%s",
                incident["type"],
                incident["severity"],
                incident["value"],
                incident["target"],
                incident["message"],
            )

        resolved_keys = [key for key in self._active_incidents if key not in breaches]
        for key in resolved_keys:
            self._active_incidents.pop(key, None)
            self._incidents.append(
                {
                    "id": str(uuid4()),
                    "opened_at": self._now_iso(),
                    "state": "resolved",
                    "type": key,
                    "severity": "info",
                    "message": "Incident condition recovered.",
                    "value": 0.0,
                    "target": 0.0,
                }
            )

    @staticmethod
    def _quantile(values: list[float], q: float) -> float:
        if not values:
            return 0.0
        sorted_values = sorted(float(item) for item in values)
        if len(sorted_values) == 1:
            return sorted_values[0]
        rank = min(max(q, 0.0), 1.0) * (len(sorted_values) - 1)
        low = math.floor(rank)
        high = math.ceil(rank)
        if low == high:
            return sorted_values[low]
        weight = rank - low
        return sorted_values[low] * (1.0 - weight) + sorted_values[high] * weight

    @staticmethod
    def _budget_ratio(*, observed: float, target: float) -> dict[str, float]:
        normalized_target = min(max(float(target), 0.0), 1.0)
        allowed_error = max(0.000001, 1.0 - normalized_target)
        observed_error = max(0.0, 1.0 - min(max(float(observed), 0.0), 1.0))
        remaining_abs = max(0.0, allowed_error - observed_error)
        remaining_ratio = min(1.0, remaining_abs / allowed_error) if allowed_error > 0 else 0.0
        burn_rate = observed_error / allowed_error if allowed_error > 0 else float("inf")
        return {
            "target": round(normalized_target, 6),
            "observed": round(min(max(float(observed), 0.0), 1.0), 6),
            "allowed_error_ratio": round(allowed_error, 6),
            "observed_error_ratio": round(observed_error, 6),
            "remaining_ratio": round(remaining_ratio, 6),
            "burn_rate": round(burn_rate, 6),
        }


class ObservabilityTelemetry:
    def __init__(self, *, base: TelemetrySink, monitor: SREMonitor) -> None:
        self.base = base
        self.monitor = monitor

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.base.emit(event_type, payload)
        self.monitor.ingest_event(event_type, payload)


class ObservabilityManager:
    def __init__(
        self,
        *,
        logger: logging.Logger,
        service_name: str,
        service_version: str,
        environment: str,
        otel_enabled: bool,
        otlp_endpoint: str | None,
        slo_targets: SLOTargets,
    ) -> None:
        self.logger = logger
        self.service_name = service_name
        self.service_version = service_version
        self.environment = environment
        self.otel_enabled = bool(otel_enabled)
        self.otlp_endpoint = str(otlp_endpoint or "").strip() or None
        self.sre = SREMonitor(targets=slo_targets, logger=logger)
        self._tracer = None
        self._otel_available = False
        self._configure_otel()

    def _configure_otel(self) -> None:
        if not self.otel_enabled:
            self.logger.info("otel_disabled")
            return
        if TracerProvider is None or BatchSpanProcessor is None or Resource is None or otel_trace is None:
            self.logger.warning("otel_unavailable dependencies_missing=true")
            return
        resource = Resource.create(
            {
                "service.name": self.service_name,
                "service.version": self.service_version,
                "deployment.environment": self.environment,
            }
        )
        provider = TracerProvider(resource=resource)
        exporter = None
        if self.otlp_endpoint and OTLPSpanExporter is not None:
            try:
                exporter = OTLPSpanExporter(endpoint=self.otlp_endpoint)
            except Exception as exc:  # pragma: no cover - defensive
                self.logger.warning("otel_exporter_init_failed endpoint=%s error=%s", self.otlp_endpoint, exc)
        if exporter is None and ConsoleSpanExporter is not None:
            exporter = ConsoleSpanExporter()
        if exporter is None:
            self.logger.warning("otel_exporter_unavailable")
            return
        provider.add_span_processor(BatchSpanProcessor(exporter))
        self._tracer = provider.get_tracer("amaryllis.runtime")
        self._otel_available = True
        self.logger.info("otel_enabled exporter=%s", exporter.__class__.__name__)

    def start_request_span(self, *, request_id: str, method: str, path: str) -> RequestSpanContext:
        started_at = time.perf_counter()
        if not self._otel_available or self._tracer is None or otel_trace is None:
            return RequestSpanContext(
                span=None,
                token=None,
                trace_id=request_id,
                path=path,
                method=method.upper(),
                request_id=request_id,
                started_at=started_at,
            )
        span = self._tracer.start_span(
            name=f"{method.upper()} {path}",
            kind=SpanKind.SERVER if SpanKind is not None else None,
        )
        span.set_attribute("http.method", method.upper())
        span.set_attribute("http.route", path)
        span.set_attribute("amaryllis.request_id", request_id)
        token = None
        if otel_context is not None and otel_trace is not None:
            token = otel_context.attach(otel_trace.set_span_in_context(span))
        trace_id = f"{span.get_span_context().trace_id:032x}"
        return RequestSpanContext(
            span=span,
            token=token,
            trace_id=trace_id,
            path=path,
            method=method.upper(),
            request_id=request_id,
            started_at=started_at,
        )

    def finish_request_span(
        self,
        *,
        context: RequestSpanContext,
        status_code: int,
        duration_ms: float,
        error_type: str | None = None,
    ) -> None:
        self.sre.record_http(
            method=context.method,
            path=context.path,
            status_code=int(status_code),
            duration_ms=float(duration_ms),
            request_id=context.request_id,
            error_type=error_type,
        )
        if context.span is None:
            return
        span = context.span
        span.set_attribute("http.status_code", int(status_code))
        span.set_attribute("amaryllis.duration_ms", float(duration_ms))
        if error_type:
            span.set_attribute("amaryllis.error_type", str(error_type))
        if status_code >= 500 and Status is not None and StatusCode is not None:
            span.set_status(Status(StatusCode.ERROR))
        elif Status is not None and StatusCode is not None:
            span.set_status(Status(StatusCode.OK))
        span.end()
        if context.token is not None and otel_context is not None:
            otel_context.detach(context.token)
