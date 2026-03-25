# Mission Success/Recovery Report Pack

## Purpose
`P3-D02` provides a public KPI report pack for mission success and recovery signals across
release and nightly pipelines.

Script:
- `scripts/release/build_mission_success_recovery_report.py`
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
- distribution resilience report
- macOS desktop parity smoke report (staging)
- user journey benchmark report

Nightly scope:
- nightly reliability report
- nightly burn-rate gate report
- nightly user journey benchmark report
- macOS desktop parity smoke report (staging, optional)

The script accepts any subset and produces a normalized report with:
- source metadata
- extracted KPI values
- normalized pass/fail checks (`gte` / `lte`)
- summary status (`pass` / `fail`)
- class-level KPI/check breakdown (`mission_execution`, `recovery`, `quality`, `runtime_qos`, `distribution`, `desktop_staging`, `user_flow`, `nightly_reliability`)

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

## CI Integration

- Release workflow (`release-gate.yml`) exports:
  - `artifacts/mission-success-recovery-report.json`
- Nightly workflow (`nightly-reliability.yml`) exports:
  - `artifacts/nightly-mission-success-recovery-report.json`
  - `artifacts/nightly-mission-success-recovery-runtime-export.json`

This makes mission reliability KPIs available as machine-readable artifacts for each release and nightly run.
