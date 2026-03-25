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

Nightly user journey benchmark companion gate:

```bash
python3 scripts/release/user_journey_benchmark.py \
  --iterations 8 \
  --min-success-rate-pct 100 \
  --max-p95-journey-latency-ms 3500 \
  --max-p95-plan-dispatch-latency-ms 1500 \
  --max-p95-execute-dispatch-latency-ms 1500 \
  --min-plan-to-execute-conversion-rate-pct 100 \
  --min-activation-success-rate-pct 100 \
  --max-blocked-activation-rate-pct 0 \
  --max-p95-activation-latency-ms 600000 \
  --min-install-success-rate-pct 100 \
  --min-retention-proxy-success-rate-pct 100 \
  --min-feature-adoption-rate-pct 100 \
  --baseline eval/baselines/quality/user_journey_benchmark_baseline.json \
  --output artifacts/nightly-user-journey-benchmark-report.json \
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

Nightly user journey benchmark artifact:

```text
artifacts/nightly-user-journey-benchmark-report.json
```

Mission success/recovery report pack artifact:

```text
artifacts/nightly-mission-success-recovery-report.json
```

Nightly runtime-export companion artifact:

```text
artifacts/nightly-mission-success-recovery-runtime-export.json
```

Nightly macOS desktop parity smoke artifact (staging, non-blocking):

```text
artifacts/nightly-macos-desktop-parity-smoke-report.json
```

Report includes:
- `summary`: total/failed requests, success/error rate, avg/p95 latency, jitter, stability score.
- `trend_deltas`: deltas vs baseline metrics.
- `burn_rate.samples`: per-round request/run burn-rate samples + active burn-rate budgets.
- `burn_rate.summary`: p95/max/breach streaks for request/run burn-rate.
- `failures`: per-request mismatch details (expected vs actual status, round, latency).

Companion staging report:
- `macos_desktop_parity_smoke_v1` for desktop-action parity on macOS contract surface.

Runtime export publisher:

```bash
python3 scripts/release/publish_mission_success_recovery_snapshot.py \
  --report artifacts/nightly-mission-success-recovery-report.json \
  --channel nightly \
  --expect-scope nightly \
  --install-root ~/.local/share/amaryllis
```

## Baseline

Baseline file:

```text
eval/baselines/reliability/nightly_smoke_baseline.json
```

Used for trend deltas only. Strict pass/fail is governed by explicit threshold flags/env vars.
