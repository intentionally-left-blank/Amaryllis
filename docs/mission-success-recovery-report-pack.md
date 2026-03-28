# Mission Success/Recovery Report Pack

## Purpose
`P4-D02` provides a public KPI report pack v2 for mission success and recovery signals across
release and nightly pipelines.

Script:
- `scripts/release/build_mission_success_recovery_report.py`
- `scripts/release/mission_report_pack_gate.py` (blocking schema/completeness gate)
- `scripts/release/publish_mission_success_recovery_snapshot.py` (runtime-export helper)

## Output

Default output:
- `artifacts/mission-success-recovery-report.json`

Nightly output:
- `artifacts/nightly-mission-success-recovery-report.json`
- `artifacts/nightly-mission-success-recovery-runtime-export.json`

Suite id:
- `mission_success_recovery_report_pack_v2`

## Supported Sources

Release scope:
- mission queue load gate report
- fault-injection reliability report
- release quality dashboard snapshot
- adoption KPI trend gate report
- distribution resilience report
- macOS desktop parity smoke report (staging)
- user journey benchmark report

Nightly scope:
- nightly reliability report
- nightly burn-rate gate report
- adoption KPI trend gate report
- nightly user journey benchmark report
- macOS desktop parity smoke report (staging, optional)

The script accepts any subset and produces a normalized report with:
- source metadata
- extracted KPI values
- normalized pass/fail checks (`gte` / `lte`)
- summary status (`pass` / `fail`)
- class-level KPI/check breakdown (`mission_execution`, `recovery`, `quality`, `runtime_qos`, `distribution`, `desktop_staging`, `user_flow`, `adoption_growth`, `nightly_reliability`)

User-flow slice includes onboarding activation KPIs when present in journey source:
- activation success rate
- blocked activation rate
- p95 activation latency
- install success rate
- retention proxy success rate
- feature adoption rate (`plan -> execute -> result`)

Optional user-flow source flag:
- `--user-journey-report <path>`

Optional distribution source flag:
- `--distribution-resilience-report <path>`

Optional macOS desktop staging source flag:
- `--macos-desktop-parity-report <path>`

Optional adoption trend source flag:
- `--adoption-kpi-trend-report <path>`

## CI Integration

- Release workflow (`release-gate.yml`) exports:
  - `artifacts/mission-success-recovery-report.json`
  - `artifacts/mission-report-pack-gate-report.json`
- Nightly workflow (`nightly-reliability.yml`) exports:
  - `artifacts/nightly-mission-success-recovery-report.json`
  - `artifacts/nightly-mission-report-pack-gate-report.json`
  - `artifacts/nightly-mission-success-recovery-runtime-export.json`

Blocking gate reference:
- `docs/mission-report-pack-gate.md`

This makes mission reliability KPIs both publishable and contract-validated for each release and nightly run.
