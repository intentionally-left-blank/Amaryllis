# Adoption KPI Schema Gate

## Purpose

`P4-H08` requires a strict, machine-verifiable contract for adoption funnel KPIs.

`scripts/release/adoption_kpi_schema_gate.py` validates:
- user adoption funnel metrics from `user_journey_benchmark`,
- developer adoption quality from `api_quickstart_compatibility_gate`,
- channel readiness coverage from `distribution_channel_manifest_gate`,
- optional signal-surface parity in `release_quality_dashboard`.

## Inputs

Required:
- `--user-journey-report`
- `--api-quickstart-report`
- `--distribution-channel-manifest-report`

Optional:
- `--quality-dashboard-report` (checks that adoption metrics are surfaced and passing in dashboard signals)

Threshold flags:
- `--min-api-quickstart-pass-rate-pct` (default `100`)
- `--min-distribution-channel-coverage-pct` (default `100`)

## Output

Suite id:
- `adoption_kpi_schema_gate_v1`

Report payload includes:
- `sources`,
- normalized `checks` (`gte` / `lte` / boolean),
- extracted `kpis`,
- `summary` (`checks_total`, `checks_failed`, `status`).

Companion publication artifact:
- `docs/adoption-kpi-snapshot.md`
- `scripts/release/build_adoption_kpi_snapshot.py`
- `scripts/release/adoption_kpi_trend_gate.py`
- `scripts/release/publish_adoption_kpi_snapshot.py`

## CI Integration

- `release-gate.yml` (`Release KPI Pack` job):
  - runs blocking adoption KPI schema gate against release artifacts,
  - uploads `artifacts/adoption-kpi-schema-gate-report.json`,
  - then builds and publishes adoption KPI snapshot artifacts.

- `nightly-reliability.yml`:
  - runs blocking adoption KPI schema gate against nightly artifacts,
  - uploads `artifacts/nightly-adoption-kpi-schema-gate-report.json`,
  - then builds and publishes nightly adoption KPI snapshot artifacts.
