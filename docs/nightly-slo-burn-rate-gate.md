# Nightly SLO Burn-Rate Gate

## Purpose

`scripts/release/nightly_slo_burn_rate_gate.py` is a blocking nightly gate that detects sustained error-budget burn anomalies.

It reads `nightly_reliability_run.py` output and fails if burn-rate stays above budget for too many consecutive samples.

## Input

- Source report: `artifacts/nightly-reliability-report.json`
- Required section: `burn_rate.samples` and `burn_rate.summary`

## Local Run

```bash
python3 scripts/release/nightly_slo_burn_rate_gate.py \
  --report artifacts/nightly-reliability-report.json \
  --max-consecutive-request-breach-samples 2 \
  --max-consecutive-run-breach-samples 2 \
  --output artifacts/nightly-burn-rate-gate-report.json
```

Threshold rules:
- burn-rate thresholds default to the active report budget (`request.budget` / `runs.budget`);
- optional overrides:
  - `--max-request-burn-rate`
  - `--max-run-burn-rate`

## Pass / Fail

Gate fails when either condition is true:
- request burn-rate max consecutive breaches > allowed threshold;
- run burn-rate max consecutive breaches > allowed threshold.

Single transient spikes are allowed; sustained streaks are not.

## Output

```text
artifacts/nightly-burn-rate-gate-report.json
```

Report fields:
- `thresholds`: effective budgets + allowed consecutive breach limits.
- `summary.request|runs`: sample count, avg/p95/max burn-rate, breach rounds, max consecutive breach streak.
- `passed` and `failures`.
