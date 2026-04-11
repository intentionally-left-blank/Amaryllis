# Provider Route Policy

`P9-C02` introduces a deterministic provider access route contract for `session vs server key` fallback behavior.

## Endpoints

- `GET /auth/providers/entitlements`
- `GET /auth/providers/routing-policy`
- `GET /auth/providers/diagnostics`

## Contract

`route_policy.version = provider_route_policy_v1`

Fields:

- `preferred_order`: deterministic route priority (`user_session`, then `server_api_key` when supported).
- `available_routes`: currently configured routes for user/provider.
- `selected_route`: chosen runtime access route (`user_session | server_api_key | none`).
- `fallback_routes`: remaining configured routes in deterministic order.
- `decision_reason`: machine-readable selection reason.

`error_contract.version = provider_entitlement_error_v1`

If `selected_route=none`:

- `status=error`
- `error_type=entitlement_setup_required`
- `error_code=provider_access_not_configured`
- `http_status=403`
- `next_actions[]` recovery steps (`create_provider_session`, `configure_server_key`)

If route is available:

- `status=ok`
- `error_code=null`

## Minimal Check

```bash
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8000/auth/providers/routing-policy?user_id=<id>&provider=openai"
```

Use this response to drive UI hints before requesting `/v1/chat/completions` or quickstart flows.

For machine-readable troubleshooting checks and failure signatures, see provider diagnostics card endpoint:

```bash
curl -H "Authorization: Bearer <token>" \
  "http://localhost:8000/auth/providers/diagnostics?user_id=<id>&provider=openai"
```
