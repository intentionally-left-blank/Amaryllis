# Autonomy Policy-Pack

## Purpose

Policy-pack externalizes autonomy decisions (`l0`..`l5`) into a versioned JSON contract.

This removes hardcoded L2/L3 branching from runtime logic and makes behavior auditable and replaceable.

## Default Pack

```text
policies/autonomy/default.json
```

## Schema

Top-level keys:
- `schema_version` (must be `1`)
- `pack`
- `description`
- `rules`

`rules` contract:
- level keys: `l0`, `l1`, `l2`, `l3`, `l4`, `l5`
- risk keys per level: `low`, `medium`, `high`, `critical`
- per risk rule:
  - `allow` (`bool`)
  - `requires_approval` (`bool`)
  - `reason` (`string`, required when `allow=false`)
  - `approval_scope` (`request|session|user|global`, required when `requires_approval=true`)
  - `approval_ttl_sec` (`int >= 1`, required when `requires_approval=true`)

## Runtime Binding

Environment key:

```bash
export AMARYLLIS_AUTONOMY_POLICY_PACK_PATH=/absolute/path/to/policy-pack.json
```

Runtime startup validates pack strictly via `AppConfig.from_env()` and fails fast on invalid contract.

## CI Validation

```bash
python3 scripts/release/check_autonomy_policy_pack.py
```
