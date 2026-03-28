# Action Explainability Gate

## Purpose

`P4-A03` requires a blocking contract that validates:

- timeline stream endpoint for run execution (`/agents/runs/{run_id}/events`),
- explainability payload endpoint (`/agents/runs/{run_id}/explain`),
- per-action plain-language fields (`reason`, `result`, `next_step`),
- docs/runtime contract alignment.

Script:
- `scripts/release/action_explainability_gate.py`

## Inputs

Optional:
- `--timeline-doc` (default: `docs/mission-audit-timeline.md`)
- `--explain-doc` (default: `docs/agent-run-explainability-feed.md`)
- `--token` (default: `dev-token`)
- `--output` (optional JSON report path)

## Output

Suite id:
- `action_explainability_gate_v1`

Report includes:
- docs checks for timeline/explainability endpoints and required fields,
- runtime checks for mission run lifecycle, timeline SSE availability, explainability payload structure, and owner enforcement.

## CI Integration

Release workflow (`release-gate.yml`):
- runs gate as blocking step,
- uploads `artifacts/action-explainability-gate-report.json`.

Nightly workflow (`nightly-reliability.yml`):
- runs gate as blocking step,
- uploads `artifacts/nightly-action-explainability-gate-report.json`.
