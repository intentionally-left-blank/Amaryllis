# Agent Factory Plan Perf Gate

`scripts/release/agent_factory_plan_perf_gate.py` is a blocking latency/reliability gate for:

- `POST /v1/agents/quickstart/plan`
- concurrent quickstart plan generation under deterministic runtime backend.

## What It Validates

- p95 latency for plan requests (`p95_latency_ms`) stays within configured budget.
- request error rate (`error_rate_pct`) stays within configured budget.
- every successful response includes a valid `quickstart_plan` with `inference_reason`.
- mixed multilingual/timezone-heavy prompt set remains stable under concurrent load.

## Run Locally

```bash
python scripts/release/agent_factory_plan_perf_gate.py \
  --requests-total 30 \
  --concurrency 6 \
  --max-p95-latency-ms 2000 \
  --max-error-rate-pct 0 \
  --output artifacts/agent-factory-plan-perf-gate-report.json
```

## Report Contract

- `suite`: `agent_factory_plan_perf_gate_v1`
- `summary`:
  - `status`: `pass|fail`
  - `requests_total`, `requests_succeeded`, `requests_failed`
  - `error_rate_pct`
  - `p50_latency_ms`, `p95_latency_ms`, `max_latency_ms`
  - `total_duration_ms`
- `thresholds`:
  - `max_p95_latency_ms`
  - `max_error_rate_pct`
- `breaches[]`
- `failure_samples[]`

## CI Wiring

- Release workflow: `.github/workflows/release-gate.yml`
  - blocking step writes `artifacts/agent-factory-plan-perf-gate-report.json`
- Nightly workflow: `.github/workflows/nightly-reliability.yml`
  - blocking step writes `artifacts/nightly-agent-factory-plan-perf-gate-report.json`
