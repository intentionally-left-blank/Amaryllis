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

Nightly adoption KPI schema companion gate:

```bash
python3 scripts/release/adoption_kpi_schema_gate.py \
  --user-journey-report artifacts/nightly-user-journey-benchmark-report.json \
  --api-quickstart-report artifacts/nightly-api-quickstart-compat-report.json \
  --distribution-channel-manifest-report artifacts/nightly-distribution-channel-manifest-report.json \
  --output artifacts/nightly-adoption-kpi-schema-gate-report.json
```

Nightly first-run activation gate:

```bash
python3 scripts/release/first_run_activation_gate.py \
  --output artifacts/nightly-first-run-activation-gate-report.json
```

Nightly localization/governance gate:

```bash
python3 scripts/release/localization_governance_gate.py \
  --output artifacts/nightly-localization-governance-gate-report.json
```

Nightly flow/interaction gate:

```bash
python3 scripts/release/flow_interaction_gate.py \
  --output artifacts/nightly-flow-interaction-gate-report.json
```

Nightly action explainability gate:

```bash
python3 scripts/release/action_explainability_gate.py \
  --output artifacts/nightly-action-explainability-gate-report.json
```

Nightly desktop action rollback gate:

```bash
python3 scripts/release/desktop_action_rollback_gate.py \
  --output artifacts/nightly-desktop-action-rollback-gate-report.json
```

Nightly supervisor mission gate:

```bash
python3 scripts/release/supervisor_mission_gate.py \
  --output artifacts/nightly-supervisor-mission-gate-report.json
```

Nightly generation-loop conformance gate:

```bash
python3 scripts/release/generation_loop_conformance_gate.py \
  --min-providers 1 \
  --max-warning-providers 2 \
  --output artifacts/nightly-generation-loop-conformance-gate-report.json
```

Nightly KV pressure policy gate:

```bash
python3 scripts/release/kv_pressure_policy_gate.py \
  --min-pressure-events 1 \
  --min-critical-events 1 \
  --output artifacts/nightly-kv-pressure-policy-gate-report.json
```

Nightly QoS mode envelope gate:

```bash
python3 scripts/release/qos_mode_envelope_gate.py \
  --journey-iterations 2 \
  --max-p95-journey-latency-ms 4000 \
  --max-p95-plan-dispatch-latency-ms 1800 \
  --max-p95-execute-dispatch-latency-ms 1800 \
  --max-p95-activation-latency-ms 600000 \
  --max-failed-modes 0 \
  --output artifacts/nightly-qos-mode-envelope-gate-report.json
```

Nightly distribution channel render + publish-ready validation:

```bash
python3 scripts/release/render_distribution_channel_manifests.py \
  --version "0.0.0-nightly-<sha7>" \
  --windows-x64-url "https://github.com/<org>/<repo>/releases/download/nightly-<sha>/amaryllis-windows-x64.zip" \
  --windows-x64-sha256 "<sha256>" \
  --macos-arm64-url "https://github.com/<org>/<repo>/releases/download/nightly-<sha>/amaryllis-macos-arm64.tar.gz" \
  --macos-arm64-sha256 "<sha256>" \
  --macos-x64-url "https://github.com/<org>/<repo>/releases/download/nightly-<sha>/amaryllis-macos-x64.tar.gz" \
  --macos-x64-sha256 "<sha256>" \
  --flathub-archive-url "https://github.com/<org>/<repo>/releases/download/nightly-<sha>/amaryllis-flathub.tar.gz" \
  --flathub-archive-sha256 "<sha256>" \
  --output-dir artifacts/nightly-distribution-channels-rendered \
  --report artifacts/nightly-distribution-channels-rendered-report.json

python3 scripts/release/distribution_channel_render_gate.py \
  --render-report artifacts/nightly-distribution-channels-rendered-report.json \
  --expected-version "0.0.0-nightly-<sha7>" \
  --output artifacts/nightly-distribution-channel-render-gate-report.json
```

Nightly adoption KPI snapshot build + publish:

```bash
python3 scripts/release/build_adoption_kpi_snapshot.py \
  --schema-gate-report artifacts/nightly-adoption-kpi-schema-gate-report.json \
  --user-journey-report artifacts/nightly-user-journey-benchmark-report.json \
  --api-quickstart-report artifacts/nightly-api-quickstart-compat-report.json \
  --distribution-channel-manifest-report artifacts/nightly-distribution-channel-manifest-report.json \
  --output artifacts/nightly-adoption-kpi-snapshot-report.json \
  --release-id "nightly-<sha>" \
  --release-channel nightly \
  --commit-sha "<sha>"

python3 scripts/release/adoption_kpi_trend_gate.py \
  --snapshot-report artifacts/nightly-adoption-kpi-snapshot-report.json \
  --baseline eval/baselines/quality/adoption_kpi_snapshot_baseline.json \
  --max-activation-success-regression-pct 1 \
  --max-activation-blocked-rate-increase-pct 0 \
  --max-install-success-regression-pct 1 \
  --max-retention-proxy-regression-pct 1 \
  --max-feature-adoption-regression-pct 2 \
  --max-api-quickstart-pass-rate-regression-pct 1 \
  --max-channel-coverage-regression-pct 1 \
  --output artifacts/nightly-adoption-kpi-trend-gate-report.json

python3 scripts/release/publish_adoption_kpi_snapshot.py \
  --snapshot-report artifacts/nightly-adoption-kpi-snapshot-report.json \
  --channel nightly \
  --expect-release-channel nightly \
  --output artifacts/nightly-adoption-kpi-snapshot-runtime-export.json
```

Nightly mission report pack gate:

```bash
python3 scripts/release/mission_report_pack_gate.py \
  --report artifacts/nightly-mission-success-recovery-report.json \
  --expected-scope nightly \
  --output artifacts/nightly-mission-report-pack-gate-report.json
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

Nightly adoption KPI schema gate artifact:

```text
artifacts/nightly-adoption-kpi-schema-gate-report.json
```

Nightly first-run activation gate artifact:

```text
artifacts/nightly-first-run-activation-gate-report.json
```

Nightly localization/governance gate artifact:

```text
artifacts/nightly-localization-governance-gate-report.json
```

Nightly flow/interaction gate artifact:

```text
artifacts/nightly-flow-interaction-gate-report.json
```

Nightly action explainability gate artifact:

```text
artifacts/nightly-action-explainability-gate-report.json
```

Nightly desktop action rollback gate artifact:

```text
artifacts/nightly-desktop-action-rollback-gate-report.json
```

Nightly supervisor mission gate artifact:

```text
artifacts/nightly-supervisor-mission-gate-report.json
```

Nightly generation-loop conformance gate artifact:

```text
artifacts/nightly-generation-loop-conformance-gate-report.json
```

Nightly KV pressure policy gate artifact:

```text
artifacts/nightly-kv-pressure-policy-gate-report.json
```

Nightly QoS mode envelope gate artifact:

```text
artifacts/nightly-qos-mode-envelope-gate-report.json
```

Nightly adoption KPI snapshot artifacts:

```text
artifacts/nightly-adoption-kpi-snapshot-report.json
artifacts/nightly-adoption-kpi-trend-gate-report.json
artifacts/nightly-adoption-kpi-snapshot-runtime-export.json
```

Nightly distribution channel rendered-manifest artifacts:

```text
artifacts/nightly-distribution-channels-rendered-report.json
artifacts/nightly-distribution-channels-rendered/
artifacts/nightly-distribution-channel-render-gate-report.json
```

Mission success/recovery report pack artifact:

```text
artifacts/nightly-mission-success-recovery-report.json
```

Mission report pack gate artifact:

```text
artifacts/nightly-mission-report-pack-gate-report.json
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
eval/baselines/quality/adoption_kpi_snapshot_baseline.json
```

Used for trend deltas only. Strict pass/fail is governed by explicit threshold flags/env vars.
