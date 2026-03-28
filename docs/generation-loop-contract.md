# Generation Loop Contract

## Purpose
Define a portable, testable contract for local generation loop behavior across CPU/GPU/NPU backends.

## Endpoint
- `GET /models/generation-loop/contract`
- `GET /v1/models/generation-loop/contract`

## Response Shape (v1)
- `contract_version`: `generation_loop_contract_v1`
- `generated_at`: UTC timestamp
- `active`: active provider/model
- `contract`: normalized loop semantics
- `modes`: supported routing modes
- `providers`: provider capability + conformance matrix
- `summary`: pass/warn counters

## Core Semantics
- stages: `prefill -> decode -> finalize`
- cache: KV cache is required and pressure signaling is standardized through telemetry (`low/elevated/high/critical`, token-budget based)
- fallback: deterministic ordered resolution for route selection and fallback chain
- streaming: SSE chunked stream is the portability baseline
- tool calling: grammar path is capability-gated and constrained by policy/sandbox

## Conformance Matrix
Each provider includes:
- capability declaration (`supports_stream`, `supports_tools`, `supports_load`, etc.)
- conformance checks
- status (`pass` or `warn`)
- issues list for non-conforming capabilities

This endpoint is the source of truth for Phase 4 portability checks (`P4-E01`).

## Conformance Gate
- Script: `/Users/bogdan/Amaryllis/scripts/release/generation_loop_conformance_gate.py`
- Example:
  - `python scripts/release/generation_loop_conformance_gate.py --min-providers 1 --max-warning-providers 2`
- Detailed gate reference:
  - `docs/generation-loop-conformance-gate.md`
- CI artifacts:
  - release: `artifacts/generation-loop-conformance-gate-report.json`
  - nightly: `artifacts/nightly-generation-loop-conformance-gate-report.json`

## KV Pressure Policy Gate
- Script: `/Users/bogdan/Amaryllis/scripts/release/kv_pressure_policy_gate.py`
- Example:
  - `python scripts/release/kv_pressure_policy_gate.py --min-pressure-events 1 --min-critical-events 1`
- Detailed gate reference:
  - `docs/kv-pressure-policy-gate.md`
- CI artifacts:
  - release: `artifacts/kv-pressure-policy-gate-report.json`
  - nightly: `artifacts/nightly-kv-pressure-policy-gate-report.json`
