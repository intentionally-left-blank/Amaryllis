# Generation Loop Conformance Gate

## Purpose

`generation_loop_conformance_gate.py` validates `P4-E01` portability contract output from:

- `GET /models/generation-loop/contract`

Gate assertions:

- contract identity (`generation_loop_contract_v1`),
- minimum provider coverage in conformance matrix,
- optional required-provider presence,
- warning-provider budget (`status=warn` upper bound),
- provider status shape (`pass|warn`).

This makes generation-loop portability checks release/nightly blocking instead of advisory.

## Local Run

```bash
python3 scripts/release/generation_loop_conformance_gate.py \
  --min-providers 1 \
  --max-warning-providers 2 \
  --output artifacts/generation-loop-conformance-gate-report.json
```

## Report Contract

- `suite`: `generation_loop_conformance_gate_v1`
- `summary.status`: `pass | fail`
- `summary.provider_count`: providers observed in conformance matrix
- `summary.warning_count`: providers with status `warn`
- `summary.errors[]`: failed checks

## CI Integration

- Release workflow (`release-gate.yml`):
  - blocking run,
  - artifact: `artifacts/generation-loop-conformance-gate-report.json`.
- Nightly workflow (`nightly-reliability.yml`):
  - blocking run,
  - artifact: `artifacts/nightly-generation-loop-conformance-gate-report.json`.
