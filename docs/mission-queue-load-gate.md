# Mission Queue Load Gate

## Objective

Validate queue stability for mission runs under concurrent submission pressure:

- queue drains fully,
- success rate stays within SLO,
- p95 queue wait and p95 end-to-end latency stay within budget.

## Gate Script

```bash
python scripts/release/mission_queue_load_gate.py \
  --runs-total 40 \
  --submit-concurrency 8 \
  --worker-count 4 \
  --task-latency-ms 35 \
  --scenario-timeout-sec 30 \
  --min-success-rate-pct 99 \
  --max-failed-runs 0 \
  --max-p95-queue-wait-ms 1500 \
  --max-p95-end-to-end-ms 5000 \
  --output artifacts/mission-queue-load-report.json
```

## Report

`artifacts/mission-queue-load-report.json` contains:

- gate config and thresholds,
- status distribution for all submitted runs,
- queue wait and end-to-end latency metrics,
- queue drain state (`queued_remaining`, `running_remaining`).

## Blocking Conditions

Gate fails when any of these conditions is true:

- success rate is below `--min-success-rate-pct`,
- failed/canceled runs exceed `--max-failed-runs`,
- `p95_queue_wait_ms` exceeds threshold,
- `p95_end_to_end_ms` exceeds threshold,
- queue is not fully drained by timeout.
