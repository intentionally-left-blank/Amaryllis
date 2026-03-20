# Autonomy Levels (L0-L5)

## Runtime Contract

- Config key: `autonomy_level`
- Environment variable: `AMARYLLIS_AUTONOMY_LEVEL`
- Allowed values: `l0`, `l1`, `l2`, `l3`, `l4`, `l5`
- Default: `l3`

This level is enforced at the tool execution boundary (`ToolExecutor.execute`) before action dispatch.

## Policy-Pack Contract (Schema v1)

Autonomy rules are now loaded from policy-pack JSON instead of hardcoded branches in runtime code.

- Default pack: `policies/autonomy/default.json`
- Config key: `autonomy_policy_pack_path`
- Environment variable: `AMARYLLIS_AUTONOMY_POLICY_PACK_PATH`
- Validation gate: `python scripts/release/check_autonomy_policy_pack.py`

Strict contract:
- all levels (`l0`..`l5`) must be declared,
- each level must define all risk tiers (`low`, `medium`, `high`, `critical`),
- each rule must define booleans `allow` and `requires_approval`,
- blocked rules (`allow=false`) must include explicit `reason`,
- approval rules (`requires_approval=true`) must define `approval_scope` and `approval_ttl_sec`.

Runtime fails fast if policy-pack is missing/invalid.

## Behavior Matrix

| Level | Low Risk | Medium Risk | High Risk | Critical Risk |
|---|---|---|---|---|
| `l0` | blocked | blocked | blocked | blocked |
| `l1` | approval required | blocked | blocked | blocked |
| `l2` | allowed | approval required | blocked | blocked |
| `l3` | allowed | allowed | approval required | blocked |
| `l4` | allowed | allowed | approval required | approval required |
| `l5` | allowed | allowed | allowed (policy-driven) | allowed (policy-driven) |

Notes:
- Isolation policy, signing policy, sandbox, and tool approval controls remain active at every level.
- `l5` does not bypass security controls; it only removes extra autonomy-level restrictions.

## Debug Visibility

Tool guardrails debug endpoint includes current autonomy policy snapshot:

```text
GET /v1/debug/tools/guardrails
```

Response includes:
- `autonomy_policy.level`
- `autonomy_policy.rules`

## High-Risk Action Receipts

For high/critical tool invocations (`risk_level in {"high", "critical"}`), runtime now emits explicit high-risk receipts:

- security audit `event_type`: `high_risk_action_receipt`
- audit details include:
  - `actor`
  - `policy_level`
  - `policy` (`autonomy_level`, `approval_enforcement_mode`, `isolation_profile`)
  - `rollback_hint`
  - `risk_level`
  - `session_id`
  - `permission_id`

`POST /mcp/tools/{tool_name}/invoke` successful responses for high-risk tools include:

- `action_receipt` (signed receipt)
- `high_risk_action` (explicit context mirrored from audit details)
