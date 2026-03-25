# Release Quality Dashboard Snapshot

## Purpose
`P3-D01` publishes a unified release-quality dashboard artifact built from blocking gate reports.

Script:
- `scripts/release/build_quality_dashboard_snapshot.py`
- `scripts/release/publish_release_quality_snapshot.py`

Primary artifacts:
- `artifacts/release-quality-dashboard.json`
- `artifacts/release-quality-dashboard-trend.json`
- `artifacts/release-quality-dashboard-final.json` (post-Linux distribution path)
- `artifacts/release-quality-dashboard-trend-final.json` (post-Linux distribution trend)
- `artifacts/release-quality-dashboard-runtime-export.json` (stable runtime import path payload)
- `artifacts/release-quality-dashboard-trend-runtime-export.json`

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
- `artifacts/injection-containment-report.json`
- `artifacts/model-artifact-admission-report.json`
- `artifacts/license-admission-report.json`
- `artifacts/environment-passport-report.json`
- `artifacts/qos-governor-gate-report.json`
- `artifacts/long-context-reliability-report.json`
- `artifacts/distribution-resilience-report.json`
- `artifacts/distribution-channel-manifest-report.json`
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
  - `category` (performance/reliability/resilience/queue/runtime/user_flow/security/supply_chain/compliance/reproducibility/runtime_qos/long_context/distribution/desktop_staging/developer_adoption)
  - `passed`

Adoption funnel signals are included via `user_journey.*` (install/retention/feature), `api_quickstart_compat.*`,
and optional `distribution_channel_manifest.*` metrics.
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
- enriches dashboard with injection containment score when `injection-containment-report` artifact is present,
- enriches dashboard with model package admission score when `model-artifact-admission-report` artifact is present,
- enriches dashboard with license admission score when `license-admission-report` artifact is present,
- enriches dashboard with environment passport completeness score when `environment-passport-report` artifact is present,
- enriches dashboard with QoS governor contract status when `qos-governor-gate-report` artifact is present,
- enriches dashboard with long-context reliability status when `long-context-reliability-report` artifact is present,
- enriches dashboard with distribution channel manifest readiness when `distribution-channel-manifest-report` artifact is present,
- optionally enriches both canary/final dashboard snapshots with macOS desktop parity staging report when present,
- rebuilds final dashboard in `Release KPI Pack` with `distribution-resilience-report` included (`release-quality-dashboard-final`),
- publishes runtime-export snapshot/trend copies via `publish_release_quality_snapshot.py`,
- uploads snapshot + trend artifacts (including runtime export copies).

This gives a stable, machine-readable quality surface for release-over-release comparability.

## Runtime Export (Optional)

To surface latest release quality in runtime Prometheus metrics and Grafana:

- run publisher:
  - `python scripts/release/publish_release_quality_snapshot.py --snapshot-report artifacts/release-quality-dashboard-final.json --trend-report artifacts/release-quality-dashboard-trend-final.json --install-root ~/.local/share/amaryllis`,
- runtime then reads default path
  - `~/.local/share/amaryllis/observability/release-quality-dashboard-latest.json`
  via `AMARYLLIS_RELEASE_QUALITY_DASHBOARD_PATH` (overridable),
- runtime metrics endpoint then publishes release/desktop-staging/QoS-gate gauges consumed by
  `observability/grafana/dashboard-amaryllis.json`.
