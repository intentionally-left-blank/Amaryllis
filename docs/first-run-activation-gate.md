# First-Run Activation Gate

## Purpose

`P4-H01` + `P4-H02` require a blocking contract that validates:

- first-run onboarding profile recommendation flow,
- activation-plan contract and selected package selection,
- model package catalog/list/install/license-admission API surface,
- activation execution path for first answer readiness.

Script:
- `scripts/release/first_run_activation_gate.py`

## Inputs

Optional:
- `--onboarding-doc` (default: `docs/model-onboarding-profiles.md`)
- `--catalog-doc` (default: `docs/model-package-catalog.md`)
- `--token` (default: `dev-token`)
- `--profile` (`fast|balanced|quality`, default: `balanced`)
- `--limit` (default: `20`)
- `--output` (optional JSON report path)

## Output

Suite id:
- `first_run_activation_gate_v1`

Report payload:
- `checks` for docs contract and runtime endpoint flow,
- `summary` (`checks_total`, `checks_failed`, `status`).

Runtime checks use deterministic backend and validate:
- `GET /models/onboarding/profile`,
- `GET /models/onboarding/activation-plan`,
- `GET /models/packages`,
- `GET /models/packages/license-admission`,
- `POST /models/onboarding/activate`.

## CI Integration

Release workflow (`release-gate.yml`):
- runs `first_run_activation_gate.py` as blocking step,
- uploads `artifacts/first-run-activation-gate-report.json`.

Nightly workflow (`nightly-reliability.yml`):
- runs `first_run_activation_gate.py` as blocking step,
- uploads `artifacts/nightly-first-run-activation-gate-report.json`.
