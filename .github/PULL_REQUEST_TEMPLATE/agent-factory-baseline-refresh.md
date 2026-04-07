## Agent Factory Baseline Refresh

- Refresh artifact reference: <paste GitHub Actions artifact URL>
- Refresh report path: artifacts/agent-factory-plan-perf-baseline-refresh-report.json
- Suggested baseline path: artifacts/agent_factory_plan_perf_envelope_suggested.json
- Approver identity: <@github-handle>
- Approval timestamp (UTC ISO8601): <YYYY-MM-DDTHH:MM:SSZ>

## change_control

Copy the prefilled `change_control` block from:

- `artifacts/agent-factory-plan-perf-baseline-pr-template.md`
- `artifacts/agent-factory-plan-perf-baseline-pr-template-metadata.json`

and place it into:

- `eval/baselines/quality/agent_factory_plan_perf_envelope.json`

## Checklist

- [ ] Baseline thresholds were updated from refresh artifacts only.
- [ ] `change_control` metadata is present and complete in baseline JSON.
- [ ] PR description includes real `Refresh artifact reference` and `Approver identity` values.
- [ ] Manual approval metadata (`approved_by`, `approved_at`) is not placeholder text.
