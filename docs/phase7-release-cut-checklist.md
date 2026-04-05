# Phase 7 Release Cut Checklist

## Scope

Phase 7 objective:
- production-grade autonomous news mission lane with grounded digests,
- provider entitlement enforcement,
- outbound delivery policy controls,
- release/nightly blocking reliability and security gates.

## Blocking Gates

Run and keep all green:

```bash
python3 scripts/release/api_compat_gate.py
python3 scripts/release/api_quickstart_compatibility_gate.py
python3 scripts/release/news_mission_gate.py --min-citation-coverage 0.95 --min-sections 1 --output artifacts/news-mission-gate-report.json
python3 scripts/security/provider_session_policy_check.py --output artifacts/provider-session-policy-check-report.json
python3 scripts/release/check_eval_replay_determinism.py
python3 scripts/release/check_import_boundaries.py
python3 scripts/release/phase7_release_cut_gate.py \
  --news-report artifacts/news-mission-gate-report.json \
  --provider-report artifacts/provider-session-policy-check-report.json \
  --mission-report artifacts/mission-success-recovery-report.json \
  --mission-pack-gate-report artifacts/mission-report-pack-gate-report.json \
  --output artifacts/phase7-release-cut-gate-report.json
```

## KPI Exit Targets

- `news_citation_coverage_rate >= 0.95`
- `news_mission_success_rate_pct >= 99`
- `mission_success_rate_pct >= 99`
- `provider_session_revocation_propagation_p95_sec <= 60`

Validate in:
- `artifacts/news-mission-gate-report.json`
- `artifacts/mission-success-recovery-report.json`
- `artifacts/provider-session-policy-check-report.json`

## Contract and API Checks

- `contracts/news_mission_v1.json` matches live endpoints:
  - `/news/delivery/policies/upsert`
  - `/news/delivery/policies`
  - `/news/delivery/events`
  - `/news/digest/compose` outbound fields
- `news/contract` endpoint lists outbound delivery endpoints.

## Security and Policy Checks

- Cross-tenant access to provider sessions is blocked.
- Cross-tenant access to news delivery policies/events is blocked.
- Raw `credential_ref` is never returned by API payloads.
- Outbound channels enforce per-channel target limits.

## CI Wiring Checks

- Release workflow passes `--news-mission-report` into `build_mission_success_recovery_report.py`.
- Nightly workflow runs `news_mission_gate.py`.
- Nightly mission report pack includes news KPIs from the news gate report.

## Sign-Off

Fill before tag:

- Product owner sign-off: `pending`
- Runtime/security sign-off: `pending`
- API compatibility sign-off: `pending`
- Reliability sign-off: `pending`
- Release manager sign-off: `pending`
