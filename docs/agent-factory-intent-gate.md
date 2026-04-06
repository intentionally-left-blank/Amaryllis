# Agent Factory Intent Gate

`scripts/release/agent_factory_intent_gate.py` validates deterministic Agent Factory intent inference against fixture scenarios:

- resolved `kind` (`news|coding|general`),
- `source_policy.mode` (`open_web|channels|allowlist`),
- explainability hints (`inference_reason.resolved_kind`, `inference_reason.mixed_intent`),
- inferred schedule fields when expected (`schedule_type`, `interval_hours`, `hour`, `minute`).

## Fixture

Default fixture:

- `eval/fixtures/agent_factory/intent_inference_cases.json`

The fixture can contain multilingual/noisy prompts; each case defines expected outputs used by the gate.

## Run Locally

```bash
python scripts/release/agent_factory_intent_gate.py \
  --output artifacts/agent-factory-intent-gate-report.json
```

Optional flags:

- `--fixture <path>` to test a different fixture suite.
- `--min-pass-rate <0..1>` to relax/tighten acceptance.

## Report Contract

- `suite`: `agent_factory_intent_gate_v1`
- `summary`:
  - `status`: `pass|fail`
  - `cases_total`, `cases_passed`, `cases_failed`
  - `pass_rate`, `min_pass_rate`
- `cases[]`:
  - `id`
  - `status`
  - `mismatches[]` (`field`, `expected`, `actual`)

## CI Wiring

- Release workflow: `.github/workflows/release-gate.yml`
  - blocking step writes `artifacts/agent-factory-intent-gate-report.json`
- Nightly workflow: `.github/workflows/nightly-reliability.yml`
  - blocking step writes `artifacts/nightly-agent-factory-intent-gate-report.json`
