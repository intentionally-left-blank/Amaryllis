# Supervisor Mission Gate

## Purpose

`supervisor_mission_gate.py` validates Epic C contract as a release/nightly blocking gate:

- bounded supervisor task-graph semantics (`create/launch/tick/list/get`),
- checkpoint + resume behavior across runtime restart,
- objective verification policy (`auto/manual`, explicit `verify` override),
- ownership boundary enforcement for supervisor graph access.

The gate combines:

1. doc contract checks (`docs/supervisor-task-graph-contract.md`),
2. deterministic manager-level checks (`SupervisorTaskGraphManager` + SQLite checkpoint store),
3. runtime API smoke checks (`/supervisor/graphs/*`) under auth scopes.

## Inputs

- Supervisor contract doc:
  - `docs/supervisor-task-graph-contract.md`
- Runtime API:
  - `/supervisor/graphs/contract`
  - `/supervisor/graphs/create`
  - `/supervisor/graphs`
  - `/supervisor/graphs/{graph_id}`
  - `/supervisor/graphs/{graph_id}/launch`
  - `/supervisor/graphs/{graph_id}/tick`
  - `/supervisor/graphs/{graph_id}/verify`

## Local Run

```bash
python3 scripts/release/supervisor_mission_gate.py \
  --output artifacts/supervisor-mission-gate-report.json
```

## Report Contract

- `suite`: `supervisor_mission_gate_v1`
- `summary.status`: `pass | fail`
- `summary.checks_total`: number of checks executed
- `summary.checks_failed`: number of failed checks
- `checks[]`: machine-readable check list (`name`, `ok`, `detail`)

## CI Integration

- Release gate workflow:
  - `.github/workflows/release-gate.yml`
  - blocking step writes: `artifacts/supervisor-mission-gate-report.json`
- Nightly reliability workflow:
  - `.github/workflows/nightly-reliability.yml`
  - blocking step writes: `artifacts/nightly-supervisor-mission-gate-report.json`

Both artifacts are uploaded for operator diagnostics and trend tracking.
