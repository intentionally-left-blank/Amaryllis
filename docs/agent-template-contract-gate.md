# Agent Template Contract Gate

`scripts/release/agent_template_contract_gate.py` validates mission template quality as a blocking release gate.

## Scope

- template schema contract (`/automations/mission/templates` source),
- template apply contract (`apply_mission_template`),
- deterministic replay snapshot for `template apply -> mission plan` path.

## Inputs

- fixture cases: `eval/fixtures/agent_templates/template_contract_cases.json`
- expected snapshot: `eval/fixtures/agent_templates/template_contract_snapshot.json`

## Usage

```bash
python scripts/release/agent_template_contract_gate.py \
  --output artifacts/agent-template-contract-gate-report.json
```

Refresh canonical snapshot when template contract intentionally changes:

```bash
python scripts/release/agent_template_contract_gate.py --update-snapshot
```

## Failure Classes

- case contract mismatch (`template`, `mission_policy`, `apply_payload`, risk/recommendation fields),
- fixture-vs-catalog mismatch (`catalog_version`, template count),
- canonical replay drift against snapshot fixture.

## Output

Report suite id: `agent_template_contract_gate_v1`.

Summary fields:

- `summary.status`
- `summary.cases_total`
- `summary.cases_failed`
- `summary.snapshot_drift_cases`
- `summary.catalog_failures`
