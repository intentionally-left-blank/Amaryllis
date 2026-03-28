# Personalization Adapter Gate

`scripts/release/personalization_adapter_gate.py` validates the adapter-personalization lane contract:
- signed adapter registration,
- activation stack semantics (single active adapter per `user_id + base_package_id` scope),
- rollback to previous active adapter,
- rejection of bad signatures.

The gate emits `personalization_adapter_gate_v1` JSON and blocks on contract regressions.

## Run Locally

```bash
python scripts/release/personalization_adapter_gate.py \
  --min-registered-adapters 2 \
  --output artifacts/personalization-adapter-gate-report.json
```

## Key Flags

- `--min-registered-adapters`: minimum adapters expected in the tested scope.
- `--output`: optional JSON report path.
