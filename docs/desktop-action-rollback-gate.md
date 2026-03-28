# Desktop Action Rollback Gate

## Purpose

`P4-B01` + `P4-B03` require a blocking contract that validates:

- Linux desktop action adapter surface is policy-gated and runtime-integrated,
- mutating desktop actions expose deterministic rollback hints,
- terminal action receipts preserve rollback metadata for operator/audit usage.

Script:
- `scripts/release/desktop_action_rollback_gate.py`

## Inputs

Optional:
- `--desktop-doc` (default: `docs/linux-desktop-action-adapters.md`)
- `--token` (default: `dev-token`)
- `--output` (optional JSON report path)

## Output

Suite id:
- `desktop_action_rollback_gate_v1`

Report includes:
- docs checks for action surface + rollback-hint language,
- adapter checks for mutating/read action metadata (`rollback_hint`, `mutating`),
- runtime checks for registration/policy contract and terminal receipt rollback context.

## CI Integration

Release workflow (`release-gate.yml`):
- runs gate as blocking step,
- uploads `artifacts/desktop-action-rollback-gate-report.json`.

Nightly workflow (`nightly-reliability.yml`):
- runs gate as blocking step,
- uploads `artifacts/nightly-desktop-action-rollback-gate-report.json`.
