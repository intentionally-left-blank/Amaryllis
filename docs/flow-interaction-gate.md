# Flow + Interaction Gate

## Purpose

`P4-A01` + `P4-A02` require a blocking contract that validates:

- unified flow session API state machine for text/voice/visual lifecycle,
- explicit `plan` vs `execute` dispatch semantics and trust-boundary signaling,
- docs and runtime API alignment for client integration.

Script:
- `scripts/release/flow_interaction_gate.py`

## Inputs

Optional:
- `--flow-doc` (default: `docs/flow-session-contract.md`)
- `--interaction-doc` (default: `docs/agent-run-interaction-modes.md`)
- `--token` (default: `dev-token`)
- `--output` (optional JSON report path)

## Output

Suite id:
- `flow_interaction_gate_v1`

Report includes:
- docs contract checks for endpoint/state/channel coverage,
- runtime checks for `/flow/sessions/*` state transitions and channel activity,
- runtime checks for `/agents/runs/interaction-modes` and `/agents/{agent_id}/runs/dispatch` (`plan` dry-run + `execute` run creation).

## CI Integration

Release workflow (`release-gate.yml`):
- runs gate as blocking step,
- uploads `artifacts/flow-interaction-gate-report.json`.

Nightly workflow (`nightly-reliability.yml`):
- runs gate as blocking step,
- uploads `artifacts/nightly-flow-interaction-gate-report.json`.
