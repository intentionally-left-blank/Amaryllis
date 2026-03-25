# Adoption KPI Snapshot

## Purpose

`P4-H08` needs a stable, publishable artifact for adoption metrics, separate from the schema gate result.

Scripts:
- `scripts/release/build_adoption_kpi_snapshot.py`
- `scripts/release/adoption_kpi_trend_gate.py`
- `scripts/release/publish_adoption_kpi_snapshot.py`

## Inputs

Required:
- `--schema-gate-report` (`adoption_kpi_schema_gate_v1`)
- `--user-journey-report` (`user_journey_benchmark_v1`)
- `--api-quickstart-report` (`api_quickstart_compatibility_gate_v1`)
- `--distribution-channel-manifest-report` (`distribution_channel_manifest_gate_v1`)

Optional:
- `--quality-dashboard-report` (`release_quality_dashboard_v1`) for adoption-signal surface parity checks.

## Output Contract

`adoption-kpi-snapshot.json`:
- `suite`: `adoption_kpi_snapshot_v1`
- `release`: `release_id`, `release_channel`, `commit_sha`
- `sources`: source suite/path metadata
- `signals`: normalized adoption signals (`metric_id`, `value`, `threshold`, `comparator`, `passed`)
- `kpis`: consolidated adoption KPIs
- `summary`: `signals_total`, `signals_failed`, `adoption_score_pct`, `status`

Builder returns non-zero when `summary.status != pass` (blocking behavior).

Trend gate contract:
- `suite`: `adoption_kpi_trend_gate_v1`
- validates regression budgets against baseline:
  - activation success
  - activation blocked rate (increase)
  - install success
  - retention proxy
  - feature adoption
  - API quickstart pass rate
  - distribution channel coverage

Baseline:
- `eval/baselines/quality/adoption_kpi_snapshot_baseline.json`

## CI Integration

Release workflow (`release-gate.yml`, `Release KPI Pack`):
- runs schema gate (blocking),
- builds `artifacts/adoption-kpi-snapshot-final.json` (blocking),
- runs `artifacts/adoption-kpi-trend-gate-report.json` gate (blocking),
- publishes runtime-export copy `artifacts/adoption-kpi-snapshot-runtime-export.json`,
- uploads all artifacts.

Nightly workflow (`nightly-reliability.yml`):
- builds `artifacts/nightly-adoption-kpi-snapshot-report.json` (blocking),
- runs `artifacts/nightly-adoption-kpi-trend-gate-report.json` gate (blocking),
- publishes runtime-export copy `artifacts/nightly-adoption-kpi-snapshot-runtime-export.json`,
- uploads all artifacts.

## Runtime Export

Default runtime path:
- `~/.local/share/amaryllis/observability/adoption-kpi-snapshot-latest.json`
  (override with `AMARYLLIS_ADOPTION_KPI_SNAPSHOT_PATH`).

Publisher example:

```bash
python scripts/release/adoption_kpi_trend_gate.py \
  --snapshot-report artifacts/adoption-kpi-snapshot-final.json \
  --baseline eval/baselines/quality/adoption_kpi_snapshot_baseline.json \
  --output artifacts/adoption-kpi-trend-gate-report.json

python scripts/release/publish_adoption_kpi_snapshot.py \
  --snapshot-report artifacts/adoption-kpi-snapshot-final.json \
  --channel release \
  --install-root ~/.local/share/amaryllis
```
