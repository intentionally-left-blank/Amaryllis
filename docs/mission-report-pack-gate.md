# Mission Report Pack Gate

## Purpose

`mission_report_pack_gate.py` validates the KPI pack contract produced by:

- `scripts/release/build_mission_success_recovery_report.py`

The gate enforces:

- suite/schema identity (`mission_success_recovery_report_pack_v2`, `schema_version=2`),
- scope consistency (`release` or `nightly`),
- required class coverage in `class_order` + `class_breakdown`,
- required KPI key presence for the selected scope,
- summary consistency (`checks_total`, pass/fail status expectations).

Release scope requires autonomy breaker gate KPIs
(`autonomy_breaker_gate_passed`, `autonomy_breaker_domains_contract_passed`) in addition to
mission/recovery/quality/distribution/user-flow/adoption KPIs and Phase 7 news KPIs
(`news_citation_coverage_rate`, `news_mission_success_rate_pct`).

Nightly scope requires breaker soak + autonomy breaker gate KPI presence
(`nightly_breaker_soak_gate_passed`, `nightly_autonomy_breaker_gate_passed`,
`nightly_autonomy_breaker_domains_contract_passed`) in addition to nightly reliability/burn/adoption
and user-flow KPIs plus Phase 7 news KPIs
(`news_citation_coverage_rate`, `news_mission_success_rate_pct`).

This turns the mission KPI pack into a formal blocking contract instead of a best-effort export.

## Local Run

Release report validation:

```bash
python3 scripts/release/mission_report_pack_gate.py \
  --report artifacts/mission-success-recovery-report.json \
  --expected-scope release \
  --output artifacts/mission-report-pack-gate-report.json
```

Nightly report validation:

```bash
python3 scripts/release/mission_report_pack_gate.py \
  --report artifacts/nightly-mission-success-recovery-report.json \
  --expected-scope nightly \
  --output artifacts/nightly-mission-report-pack-gate-report.json
```

## Report Contract

- `suite`: `mission_report_pack_gate_v1`
- `summary.status`: `pass | fail`
- `summary.checks_total`: total checks
- `summary.checks_failed`: failed checks count
- `checks[]`: machine-readable check list (`name`, `ok`, `detail`)

## CI Integration

- Release workflow (`release-gate.yml`):
  - blocking run for `artifacts/mission-success-recovery-report.json`,
  - artifact: `artifacts/mission-report-pack-gate-report.json`.

- Nightly workflow (`nightly-reliability.yml`):
  - blocking run for `artifacts/nightly-mission-success-recovery-report.json`,
  - artifact: `artifacts/nightly-mission-report-pack-gate-report.json`.
