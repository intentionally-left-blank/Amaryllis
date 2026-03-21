# Release Quality Dashboard Snapshot

## Purpose
`P3-D01` publishes a unified release-quality dashboard artifact built from blocking gate reports.

Script:
- `scripts/release/build_quality_dashboard_snapshot.py`

Primary artifacts:
- `artifacts/release-quality-dashboard.json`
- `artifacts/release-quality-dashboard-trend.json`
- `artifacts/release-quality-dashboard-final.json` (post-Linux distribution path)
- `artifacts/release-quality-dashboard-trend-final.json` (post-Linux distribution trend)

Baseline:
- `eval/baselines/quality/release_quality_dashboard_baseline.json`

## Inputs

The snapshot builder consumes these gate reports:
- `artifacts/perf-smoke-report.json`
- `artifacts/fault-injection-reliability-report.json`
- `artifacts/mission-queue-load-report.json`
- `artifacts/runtime-lifecycle-smoke-report.json`
- `artifacts/user-journey-benchmark-report.json`

Optional:
- `artifacts/distribution-resilience-report.json`
- `artifacts/macos-desktop-parity-smoke-report.json`

## Output Contract

`release-quality-dashboard.json`:
- `suite`: `release_quality_dashboard_v1`
- `release`: release id/channel/commit metadata
- `sources`: source suite timestamps for each gate
- `signals`: normalized metric signals with:
  - `metric_id`
  - `value`
  - `threshold`
  - `comparator` (`lte` or `gte`)
  - `category` (performance/reliability/resilience/queue/runtime/user_flow/distribution/desktop_staging)
  - `passed`
- `summary`: total/passed/failed signals + `quality_score_pct` + `status`

`release-quality-dashboard-trend.json`:
- `suite`: `release_quality_dashboard_trend_v1`
- baseline reference metadata
- per-metric delta/comparison against baseline snapshot
- summary counts for improved/regressed/unchanged metrics

## CI Integration

`release-gate.yml` now:
- persists perf smoke report as artifact,
- builds dashboard snapshot after canary benchmark gates (`release-quality-dashboard`),
- optionally enriches both canary/final dashboard snapshots with macOS desktop parity staging report when present,
- rebuilds final dashboard in `Release KPI Pack` with `distribution-resilience-report` included (`release-quality-dashboard-final`),
- uploads both snapshot + trend artifacts.

This gives a stable, machine-readable quality surface for release-over-release comparability.
