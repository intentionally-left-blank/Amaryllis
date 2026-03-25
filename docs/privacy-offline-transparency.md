# Privacy and Offline Transparency Contract

## Goal
Expose a machine-readable, user-visible contract for:

- offline readiness,
- current network requirements,
- telemetry export mode (opt-in),
- declared network intents and controls.

This contract powers the product "offline/network intent" panel and supports release policy checks.

## Endpoints

- `GET /privacy/transparency` (`user` or `admin` scope)
- `GET /service/privacy/transparency` (`service` or `admin` scope)
- `GET /v1/privacy/transparency`
- `GET /v1/service/privacy/transparency`

## Contract (high level)

- `contract_version`: versioned schema id.
- `generated_at`: UTC timestamp.
- `active`: currently active provider/model.
- `offline`:
  - `offline_possible`
  - `offline_ready_now`
  - `network_required_now`
  - `local_providers` / `cloud_providers`
- `telemetry`:
  - local events are always written to `local_events_path`,
  - export is opt-in via `AMARYLLIS_OTEL_ENABLED=true`,
  - `export_enabled` and `export_active` show configured vs active export state.
- `network_intents`: explicit reasons why network may be used (`cloud inference`, `model download`, `MCP`, `OTel export`) plus control hints.
- `policy_docs`: reference docs shown in UX.

Service endpoint additionally returns:

- `actor`
- `scopes`

## Policy Defaults

- Telemetry export default: `AMARYLLIS_OTEL_ENABLED=false`.
- Local telemetry remains enabled by default.
- Users can inspect all declared network intents before enabling cloud paths.

## CI Gate

- `python scripts/release/offline_transparency_gate.py`
