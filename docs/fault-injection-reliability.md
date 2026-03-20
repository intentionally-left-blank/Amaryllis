# Fault-Injection Reliability Gate

## Objective

Validate retry and recovery behavior under representative fault classes before release:

- provider transient fault (`rate_limit`),
- network transient fault (`network`),
- tool fault budget breach (`budget_exceeded` guardrail path).

## Gate Script

```bash
python scripts/release/fault_injection_reliability_gate.py \
  --retry-max-attempts 2 \
  --scenario-timeout-sec 8 \
  --min-pass-rate-pct 100 \
  --output artifacts/fault-injection-reliability-report.json
```

## Scenarios

1. `provider_rate_limit_recovery`
   - first attempt fails with provider `rate_limit`,
   - retry is scheduled,
   - run must succeed on retry.

2. `network_fault_recovery`
   - first attempt fails with provider `network`,
   - retry is scheduled,
   - run must succeed on retry.

3. `tool_fault_budget_guardrail`
   - tool error event is injected,
   - run budget (`max_tool_errors=0`) is exceeded,
   - run must fail deterministically with `failure_class=budget_exceeded` and no retry.

## Output

`artifacts/fault-injection-reliability-report.json` contains:

- gate metadata and thresholds,
- per-scenario status and diagnostics,
- aggregate pass/fail summary used by CI blocking logic.
