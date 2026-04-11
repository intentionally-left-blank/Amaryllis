# Provider Entitlement Diagnostics Gate

`scripts/release/provider_entitlement_diagnostics_gate.py` validates the machine-readable entitlement diagnostics contract.

## Goal

Ensure support/debug tooling can resolve provider access failures without raw logs.

## Runtime Endpoint

- `GET /auth/providers/diagnostics`

Query:

- `user_id` (optional, owner/admin scoped)
- `provider` (optional: `openai|anthropic|openrouter|reddit|x`)
- `session_limit` (optional, default `50`, max `500`)

Single-provider response:

- `contract_version = provider_entitlement_diagnostics_v1`
- `card.status` (`ready|degraded|blocked`)
- `card.summary.error_code`
- `card.route_policy`
- `card.error_contract`
- `card.checks[]`
- `card.failure_signatures[]`
- `card.next_actions[]`

Aggregate response:

- `items[]`
- `status_counts`

## Gate Checks

- diagnostics endpoint is listed in provider auth contract,
- blocked diagnostics when provider access is not configured,
- transition to ready after creating provider session,
- blocked again after session revoke (if no server key),
- aggregate diagnostics contract validity.

## Usage

```bash
python scripts/release/provider_entitlement_diagnostics_gate.py \
  --output artifacts/provider-entitlement-diagnostics-gate-report.json
```

Report suite id: `provider_entitlement_diagnostics_gate_v1`.
