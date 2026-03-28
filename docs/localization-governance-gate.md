# Localization + Governance Gate

## Purpose

`P4-H06` + `P4-H07` require a blocking contract that validates:

- RU/EN localization baseline docs and starter templates,
- OSS governance package (`CONTRIBUTING`, `CODE_OF_CONDUCT`, `GOVERNANCE`, `MAINTAINERS`, `TRADEMARK`, `DCO`),
- PR template alignment with DCO expectations.

Script:
- `scripts/release/localization_governance_gate.py`

## Inputs

Optional:
- `--root` (repo root, default current repo)
- `--output` (report path)

## Output

Suite id:
- `localization_governance_gate_v1`

Report includes:
- per-file contract checks,
- missing snippets diagnostics,
- summary status (`pass` / `fail`).

## CI Integration

Release workflow (`release-gate.yml`):
- runs gate as blocking step,
- uploads `artifacts/localization-governance-gate-report.json`.

Nightly workflow (`nightly-reliability.yml`):
- runs gate as blocking step,
- uploads `artifacts/nightly-localization-governance-gate-report.json`.
