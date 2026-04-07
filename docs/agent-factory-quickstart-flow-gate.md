# Agent Factory Quickstart Flow Gate

`scripts/release/agent_factory_quickstart_flow_gate.py` validates one-phrase quickstart parity across:

- `POST /v1/agents/quickstart/plan`
- `POST /v1/agents/quickstart` (apply + idempotent replay)
- `POST /v1/chat/completions` (intent shortcut + idempotent replay)

## What It Validates

- `plan` endpoint has no side effects (agent count stays unchanged).
- `apply` creates exactly one agent and replay with same `idempotency_key` does not duplicate.
- chat quickstart creates exactly one agent and replay with same `session_id` does not duplicate.
- canonicalized quickstart contract stays aligned across `plan`, `apply`, and `chat`:
  - `kind`, `name`, `focus`, `tools`
  - `sources`, `source_policy`
  - `automation` (`schedule_type`, `schedule`, `timezone`, `start_immediately`)
- `inference_reason_view` exists on all paths.

## Fixture

Default fixture:

- `eval/fixtures/agent_factory/quickstart_flow_cases.json`

Each case contains:

- `id`
- `request`
- `expected.kind`
- `expected.source_policy_mode`
- `expected.schedule_type` (including empty string for non-scheduled flows)

## Run Locally

```bash
python scripts/release/agent_factory_quickstart_flow_gate.py \
  --output artifacts/agent-factory-quickstart-flow-gate-report.json
```

Optional flags:

- `--fixture <path>`
- `--min-pass-rate <0..1>`

## Report Contract

- `suite`: `agent_factory_quickstart_flow_gate_v1`
- `summary`:
  - `status`: `pass|fail`
  - `cases_total`, `cases_passed`, `cases_failed`
  - `pass_rate`, `min_pass_rate`
- `cases[]`:
  - `id`
  - `status`
  - `mismatches[]` (`field`, `expected`, `actual`)
  - `observed.plan|apply|chat` (canonicalized payloads)

## CI Wiring

- Release workflow: `.github/workflows/release-gate.yml`
  - blocking step writes `artifacts/agent-factory-quickstart-flow-gate-report.json`
- Nightly workflow: `.github/workflows/nightly-reliability.yml`
  - blocking step writes `artifacts/nightly-agent-factory-quickstart-flow-gate-report.json`
