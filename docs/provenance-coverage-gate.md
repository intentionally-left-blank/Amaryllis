# Provenance Coverage Gate

## Purpose
`P4-F01` blocking gate for provenance-first responses.

It validates that:
- chat responses always expose the provenance contract payload,
- grounded responses include verifiable source trace,
- streaming responses carry provenance in the first SSE chunk,
- grounded generation telemetry exports provenance coverage signals.

## Script
- `/Users/bogdan/Amaryllis/scripts/release/provenance_coverage_gate.py`

## What It Checks
1. Seeds user memory with deterministic facts.
2. Non-stream grounded chat (`user_id` + memory-backed query):
   - `provenance.version == provenance_v1`,
   - `provenance.grounded == true`,
   - `provenance.sources` length >= configured minimum,
   - source item has required fields (`layer/source_id/rank/score/excerpt`).
3. Stream chat first chunk includes `provenance`.
4. Chat for a user without memory facts still returns provenance payload contract with `grounded=false`.
5. `generation_loop_metrics` telemetry row for grounded request includes:
   - `provenance_grounded=true`,
   - `provenance_sources_count` above threshold.

## Local Run

```bash
python scripts/release/provenance_coverage_gate.py \
  --min-grounded-sources 1 \
  --output artifacts/provenance-coverage-gate-report.json
```

## CI Artifacts
- release: `artifacts/provenance-coverage-gate-report.json`
- nightly: `artifacts/nightly-provenance-coverage-gate-report.json`

## Report Contract
- `suite`: `provenance_coverage_gate_v1`
- `summary.status`: `pass|fail`
- `summary.errors`: failed checks
- `checks`: machine-readable per-check trace
