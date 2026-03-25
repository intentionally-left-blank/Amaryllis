# Observability and SRE

## Signals

Amaryllis exposes:

- Logs: structured runtime logs with `request_id` and `trace_id`
- Traces: OpenTelemetry spans (if OTel dependencies are installed and enabled)
- Metrics: Prometheus text format at `/service/observability/metrics`
- SLO snapshot: `/service/observability/slo`
- Incident feed: `/service/observability/incidents`

Optional release-quality metric export:

- set `AMARYLLIS_RELEASE_QUALITY_DASHBOARD_PATH=/abs/path/release-quality-dashboard-latest.json`,
- Linux installer/service manifest default path is
  `~/.local/share/amaryllis/observability/release-quality-dashboard-latest.json`,
- `/service/observability/metrics` additionally exports latest release gauges:
  - `amaryllis_release_quality_snapshot_loaded` (`0|1`)
  - `amaryllis_release_quality_score_pct`
  - `amaryllis_release_quality_signals_failed`
  - `amaryllis_release_quality_status` (`1=pass`, `0=fail`)
  - `amaryllis_release_desktop_staging_signal_present` (`0|1`)
  - `amaryllis_release_desktop_staging_status` (`1=pass`, `0=fail`)
  - `amaryllis_release_desktop_staging_error_rate_pct`
  - `amaryllis_release_desktop_staging_checks_failed`
  - `amaryllis_release_qos_signal_present` (`0|1`)
  - `amaryllis_release_qos_status` (`1=pass`, `0=fail`)
  - `amaryllis_release_qos_checks_failed`
  - `amaryllis_release_adoption_install_success_rate_pct`
  - `amaryllis_release_adoption_retention_proxy_success_rate_pct`
  - `amaryllis_release_adoption_feature_adoption_rate_pct`
  - `amaryllis_release_adoption_channel_manifest_coverage_pct`
  - `amaryllis_release_adoption_api_quickstart_pass_rate_pct`

Optional nightly mission metric export:

- set `AMARYLLIS_NIGHTLY_MISSION_REPORT_PATH=/abs/path/nightly-mission-success-recovery-latest.json`,
- Linux installer/service manifest default path is
  `~/.local/share/amaryllis/observability/nightly-mission-success-recovery-latest.json`,
- `/service/observability/metrics` additionally exports nightly gauges:
  - `amaryllis_nightly_mission_snapshot_loaded` (`0|1`)
  - `amaryllis_nightly_mission_status` (`1=pass`, `0=fail`)
  - `amaryllis_nightly_success_rate_pct`
  - `amaryllis_nightly_p95_latency_ms`
  - `amaryllis_nightly_latency_jitter_ms`
  - `amaryllis_nightly_burn_rate_gate_passed` (`1=pass`, `0=fail`)

## SLO / SLI

Current targets are versioned by SLO profile manifests (`slo_profiles/*.json`) and can still be overridden via env.

- Request availability target
- Request latency p95 target (ms)
- Run success rate target
- Rolling SLO window

The runtime computes:

- SLI values in-window
- Error budget remaining
- Error budget burn rate

Quality budgets are also profile-scoped:

- request error-budget burn-rate budget
- run error-budget burn-rate budget
- perf smoke p95 latency budget
- perf smoke error-rate budget

Active runtime/SLO profile and effective quality budget are exposed in `GET /service/observability/slo`.

## Incident Detection

Incidents are opened automatically when thresholds are breached (availability, latency p95, run success rate) and recovered automatically when the signal returns within targets.

Key endpoints:

- `GET /service/observability/slo`
- `GET /service/observability/incidents`
- `GET /service/observability/metrics`

## Profile Drift Gate

Blocking check for CI/release pipelines:

```bash
python scripts/release/check_runtime_profile_drift.py
```

Reference:

- `docs/runtime-profiles.md`

## OpenTelemetry

OTel export is opt-in and disabled by default.

Enable export explicitly:

- `AMARYLLIS_OTEL_ENABLED=true`
- `AMARYLLIS_OTEL_OTLP_ENDPOINT=http://collector:4318/v1/traces`

If OTel packages are missing, runtime falls back to local telemetry without crashing.
