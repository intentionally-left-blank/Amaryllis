# Injection Containment Gate

`scripts/release/injection_containment_gate.py` runs deterministic regression scenarios for:
- embedded tool-call prompt injection in RAG context,
- multi-payload tool-call responses,
- unsafe deserialization payloads in tool arguments (`pickle`, `cloudpickle`, `pandas.read_pickle`, YAML python tags).

The gate produces `injection_containment_gate_v1` report JSON and blocks when:
- containment score for attack scenarios is below threshold,
- failed scenario count exceeds configured maximum,
- required scenarios are missing or failing.

## Run Locally

```bash
python scripts/release/injection_containment_gate.py \
  --min-containment-score-pct 100 \
  --max-failed-scenarios 0 \
  --require-scenario rag_embedded_tool_call_is_ignored \
  --require-scenario pickle_deserialization_blocked \
  --require-scenario cloudpickle_deserialization_blocked \
  --require-scenario pandas_read_pickle_blocked \
  --require-scenario yaml_python_tag_blocked \
  --output artifacts/injection-containment-report.json
```

## Key Flags

- `--min-containment-score-pct`: minimum required containment score (0..100).
- `--max-failed-scenarios`: maximum number of failed scenarios allowed.
- `--require-scenario`: enforce pass status for specific scenario id(s).
- `--output`: optional JSON report path.

## Report Summary Fields

- `summary.containment_score_pct`
- `summary.failed_scenarios`
- `summary.attack_scenarios`
- `summary.attack_contained`
- `summary.errors`
