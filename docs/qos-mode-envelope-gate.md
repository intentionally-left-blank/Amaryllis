# QoS Mode Envelope Gate

## Purpose
`P4-E03` blocking gate for user-visible QoS behavior.

It validates that all public QoS modes (`quality`, `balanced`, `power_save`) keep journey KPIs within envelope and expose consistent runtime QoS contract fields.

## Script
- `scripts/release/qos_mode_envelope_gate.py`

## What It Validates
1. Runs `user_journey_benchmark.py` per mode with strict KPI thresholds.
2. Verifies per-mode benchmark report status is `pass`.
3. Verifies runtime QoS contract per mode:
   - `active_mode` matches requested mode,
   - `auto_enabled=false` (manual mode lock for deterministic envelope check),
   - `route_mode` matches mode mapping.

## Local Run

```bash
python scripts/release/qos_mode_envelope_gate.py \
  --journey-iterations 2 \
  --max-p95-journey-latency-ms 3500 \
  --max-p95-plan-dispatch-latency-ms 1500 \
  --max-p95-execute-dispatch-latency-ms 1500 \
  --max-p95-activation-latency-ms 600000 \
  --max-failed-modes 0 \
  --output artifacts/qos-mode-envelope-gate-report.json
```

## CI Artifacts
- release: `artifacts/qos-mode-envelope-gate-report.json`
- nightly: `artifacts/nightly-qos-mode-envelope-gate-report.json`

## Report Contract
- `suite`: `qos_mode_envelope_gate_v1`
- `summary.status`: `pass|fail`
- `summary.failed_modes`: list of failed QoS modes
- `modes`: per-mode benchmark run info, summary, and runtime QoS payload
