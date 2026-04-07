# Agent Factory Plan Perf Gate

`scripts/release/agent_factory_plan_perf_gate.py` is a blocking latency/reliability gate for:

- `POST /v1/agents/quickstart/plan`
- concurrent quickstart plan generation under deterministic runtime backend.

## What It Validates

- p95 latency for plan requests (`p95_latency_ms`) stays within configured budget.
- request error rate (`error_rate_pct`) stays within configured budget.
- every successful response includes a valid `quickstart_plan` with `inference_reason`.
- mixed multilingual/timezone-heavy prompt set remains stable under concurrent load.

## Run Locally

```bash
python scripts/release/agent_factory_plan_perf_gate.py \
  --baseline "eval/baselines/quality/agent_factory_plan_perf_envelope.json" \
  --baseline-profile release \
  --output artifacts/agent-factory-plan-perf-gate-report.json
```

Optional CLI overrides (higher priority than baseline profile):

- `--requests-total`
- `--concurrency`
- `--max-p95-latency-ms`
- `--max-error-rate-pct`

## Baseline Envelope

Profile envelopes are stored in:

- `eval/baselines/quality/agent_factory_plan_perf_envelope.json`

Current calibrated profiles:

- `release` (default release-gate profile)
- `nightly` (default nightly-reliability profile)
- `dev_macos`
- `dev_linux`

## Baseline Refresh

Drift/suggestion script:

- `scripts/release/agent_factory_plan_perf_baseline_refresh.py`

Example:

```bash
python scripts/release/agent_factory_plan_perf_baseline_refresh.py \
  --baseline eval/baselines/quality/agent_factory_plan_perf_envelope.json \
  --report release=artifacts/agent-factory-plan-perf-gate-release-report.json \
  --report nightly=artifacts/agent-factory-plan-perf-gate-nightly-report.json \
  --report dev_linux=artifacts/agent-factory-plan-perf-gate-dev-linux-report.json \
  --output artifacts/agent-factory-plan-perf-baseline-refresh-report.json \
  --write-updated-baseline artifacts/agent_factory_plan_perf_envelope_suggested.json
```

Scheduled workflow:

- `.github/workflows/agent-factory-baseline-refresh.yml`
- auto-generates:
  - `artifacts/agent-factory-plan-perf-baseline-pr-template.md`
  - `artifacts/agent-factory-plan-perf-baseline-pr-template-metadata.json`

PR-template generator:

- `scripts/release/agent_factory_plan_perf_baseline_pr_template.py`

## Baseline Update Policy Gate

Baseline PRs are validated by:

- script: `scripts/release/agent_factory_plan_perf_baseline_policy_gate.py`
- script: `scripts/release/agent_factory_plan_perf_baseline_pr_description_gate.py`
- workflow: `.github/workflows/agent-factory-baseline-policy-gate.yml`
- PR template: `.github/PULL_REQUEST_TEMPLATE/agent-factory-baseline-refresh.md`

What this gate enforces:

- per-profile p95 threshold drift above auto limits requires manual approval metadata;
- any `max_error_rate_pct` threshold change requires manual approval metadata;
- changed baseline must include `change_control` metadata.
- PR description must include:
  - `Refresh artifact reference` (non-placeholder)
  - `Approver identity` (non-placeholder handle/email)

Default auto-drift limits:

- max increase without manual approval: `15%`
- max decrease without manual approval: `20%`

Required `change_control` fields for changed baseline:

- `change_id`
- `reason`
- `ticket`
- `requested_by`

Additional required fields when manual approval is required:

- `manual_approval=true`
- `approved_by` (non-empty list)
- `approved_at`
- optional `approval_scope` (if provided, must include all profiles requiring manual approval)

## Report Contract

- `suite`: `agent_factory_plan_perf_gate_v1`
- `summary`:
  - `status`: `pass|fail`
  - `requests_total`, `requests_succeeded`, `requests_failed`
  - `error_rate_pct`
  - `p50_latency_ms`, `p95_latency_ms`, `max_latency_ms`
  - `total_duration_ms`
- `thresholds`:
  - `max_p95_latency_ms`
  - `max_error_rate_pct`
- `gate_config`:
  - `baseline_path`, `baseline_suite`, `baseline_profile`
  - resolved `requests_total`, `concurrency`
- `breaches[]`
- `failure_samples[]`

## CI Wiring

- Release workflow: `.github/workflows/release-gate.yml`
  - blocking step writes `artifacts/agent-factory-plan-perf-gate-report.json`
- Nightly workflow: `.github/workflows/nightly-reliability.yml`
  - blocking step writes `artifacts/nightly-agent-factory-plan-perf-gate-report.json`
