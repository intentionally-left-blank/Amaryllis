# Provider Auth Onboarding

## Goal

Give users a clear, safe flow to connect provider access and verify entitlements without reading logs.

## Endpoints

- `GET /auth/providers/contract`
- `GET /auth/providers/onboarding`
- `POST /auth/providers/sessions`
- `GET /auth/providers/sessions`
- `POST /auth/providers/sessions/{session_id}/revoke`
- `GET /auth/providers/entitlements`
- `GET /auth/providers/routing-policy`
- `GET /auth/providers/diagnostics`

## Onboarding Contract

`GET /auth/providers/onboarding` returns `provider_auth_onboarding_v1`.

Query:

- `user_id` (optional, owner/admin scoped)
- `provider` (optional: `openai|anthropic|openrouter|reddit|x`)

If `provider` is set:

- returns a single `card` with:
  - `status`: `ready | setup_required`
  - `reason_codes`
  - `next_actions`
  - `route_policy` summary (`selected_route`, `fallback_routes`, `decision_reason`)
  - `error_contract` (`error_code`, `http_status`, recovery actions) when setup is incomplete
  - `entitlement` snapshot
  - safe `create_session` and `verify_entitlements` examples

If `provider` is omitted:

- returns `items[]` for all supported providers
- includes `ready_count` summary

## Security Notes

- `credential_ref` must reference an external secret; raw provider tokens must not be sent.
- Revoke stale or compromised sessions via `/auth/providers/sessions/{session_id}/revoke`.
- Use minimal `scopes` for each session.
- Route fallback semantics are documented in `docs/provider-route-policy.md`.

## Minimal Flow

1. `GET /auth/providers/onboarding?user_id=<id>&provider=openai`
2. If `status=setup_required`, call `POST /auth/providers/sessions` with `credential_ref`.
3. Re-check `GET /auth/providers/entitlements?user_id=<id>&provider=openai`.
4. Revoke old sessions when rotating credentials.
5. Inspect deterministic route selection via `GET /auth/providers/routing-policy?user_id=<id>&provider=openai`.
6. Use machine-readable diagnostics card: `GET /auth/providers/diagnostics?user_id=<id>&provider=openai`.
