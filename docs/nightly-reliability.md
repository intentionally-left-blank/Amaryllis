# Nightly Extended Reliability Run

## Purpose

Nightly run validates non-functional reliability regressions and publishes a machine-readable report with trend deltas for:
- success rate,
- latency (p95),
- stability (latency jitter + stability score),
- SLO burn-rate samples (requests/runs) for downstream regression gate.

## Workflow

- GitHub Actions: `.github/workflows/nightly-reliability.yml`
- Triggers:
  - nightly schedule (`cron: 0 2 * * *`, UTC),
  - manual dispatch.
- Includes blocking follow-up gate:
  - `scripts/release/nightly_slo_burn_rate_gate.py`

## Local Run

```bash
python3 scripts/release/nightly_reliability_run.py \
  --iterations 12 \
  --min-success-rate-pct 99 \
  --max-p95-latency-ms 600 \
  --max-latency-jitter-ms 120 \
  --baseline eval/baselines/reliability/nightly_smoke_baseline.json \
  --strict
```

## Report

Default output path:

```text
eval/reports/reliability/nightly_<timestamp>.json
```

Workflow output artifact:

```text
artifacts/nightly-reliability-report.json
```

Burn-rate gate output artifact:

```text
artifacts/nightly-burn-rate-gate-report.json
```

Report includes:
- `summary`: total/failed requests, success/error rate, avg/p95 latency, jitter, stability score.
- `trend_deltas`: deltas vs baseline metrics.
- `burn_rate.samples`: per-round request/run burn-rate samples + active burn-rate budgets.
- `burn_rate.summary`: p95/max/breach streaks for request/run burn-rate.
- `failures`: per-request mismatch details (expected vs actual status, round, latency).

## Baseline

Baseline file:

```text
eval/baselines/reliability/nightly_smoke_baseline.json
```

Used for trend deltas only. Strict pass/fail is governed by explicit threshold flags/env vars.
