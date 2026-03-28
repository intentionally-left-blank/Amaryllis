# KV Pressure Policy Gate

## Purpose
`P4-E02` guardrail for runtime portability/QoS envelope:

- validate that generation loop emits non-empty KV pressure telemetry (`generation_loop_metrics.kv_cache.*`);
- validate that pressure states include real high/critical signals (not permanent `unknown`);
- validate QoS policy transition under pressure (`quality` -> `power_save`) without silent degradation.

## Script
- `/Users/bogdan/Amaryllis/scripts/release/kv_pressure_policy_gate.py`

## What It Checks
1. QoS starts from expected mode (`quality` by default).
2. Low-pressure chat keeps mode stable.
3. High-pressure chat produces pressure telemetry (`high`/`critical`).
4. QoS reconciles to pressure mode (`power_save` by default) with `reason` containing `pressure`.
5. Telemetry includes:
   - `kv_cache.pressure_state`,
   - `kv_cache.estimated_tokens`,
   - `kv_cache.estimated_bytes`,
   - `kv_cache.eviction_count`.

## Local Run

```bash
python scripts/release/kv_pressure_policy_gate.py \
  --min-pressure-events 1 \
  --min-critical-events 1 \
  --output artifacts/kv-pressure-policy-gate-report.json
```

## CI Wiring
- Release artifact:
  - `artifacts/kv-pressure-policy-gate-report.json`
- Nightly artifact:
  - `artifacts/nightly-kv-pressure-policy-gate-report.json`

## Report Contract
- `suite`: `kv_pressure_policy_gate_v1`
- `summary.status`: `pass|fail`
- `summary.errors`: list of failed checks
- `steps`: gate request/response trace
- `telemetry`: filtered generation-loop KV pressure counters and maxima
