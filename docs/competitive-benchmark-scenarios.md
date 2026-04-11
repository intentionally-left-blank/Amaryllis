# Competitive Benchmark Scenarios

`P9-D01` defines competitor-neutral benchmark scenarios for product outcomes, not vendor-specific API behavior.

## Dataset

- `eval/datasets/quality/competitive_benchmark_scenarios_v1.json`
- suite: `competitive_benchmark_scenarios_v1`
- schema version: `1`

## Lanes

The dataset covers four product lanes:

- `create`: one-phrase request -> runnable autonomous agent
- `schedule`: schedule normalization and deterministic timing contracts
- `quality`: grounded and actionable output quality
- `recovery`: clear remediation path for access/runtime failures

Current v1 shape:

- 8 scenarios total
- 2 scenarios per lane

## Contract Rules

Each scenario must include:

- stable `id`
- `lane`, `title`, `objective`, `prompt`
- `expected_outcomes.checks[]` + `expected_outcomes.kpi_targets`
- `evidence.must_capture[]`
- `reproducibility.seed`, `max_retries`, `replay_window_sec`

Root contract must include reproducibility and audit metadata:

- deterministic backend requirement
- idempotency requirement
- required trace fields
- required summary metrics

## Vendor Neutrality

Scenario text is intentionally vendor-agnostic. Brand-specific markers are disallowed by contract checks to keep the benchmark auditable and portable.

## Dataset Gate

Script:

- `scripts/release/competitive_benchmark_dataset_gate.py`

Usage:

```bash
python scripts/release/competitive_benchmark_dataset_gate.py \
  --output artifacts/competitive-benchmark-dataset-report.json
```

Report suite id: `competitive_benchmark_dataset_gate_v1`.
