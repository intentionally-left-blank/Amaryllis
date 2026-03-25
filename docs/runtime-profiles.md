# Runtime Profiles and SLO Budgets

Amaryllis now boots through versioned profile manifests to make runtime behavior deterministic across local/dev/CI/release environments.

## Runtime Profiles

Runtime profile manifests live in:

- `runtime/profiles/dev.json`
- `runtime/profiles/ci.json`
- `runtime/profiles/release.json`

Each manifest defines:

- `schema_version`
- `profile`
- `slo_profile` mapping
- `required_env` (fail-fast required environment variables)
- `env_defaults` (default environment values applied when unset)

Select runtime profile:

```bash
export AMARYLLIS_RUNTIME_PROFILE=dev
```

Optional custom manifest directory:

```bash
export AMARYLLIS_RUNTIME_PROFILE_DIR=/absolute/path/to/runtime/profiles
```

If a profile is missing or invalid, startup fails with `AppConfigError`.

## SLO Profiles

SLO profile manifests live in:

- `slo_profiles/dev.json`
- `slo_profiles/ci.json`
- `slo_profiles/release.json`

Each SLO profile is versioned and defines:

- SLO targets (`window`, availability/latency/run-success, min samples)
- quality budgets (burn-rate budgets + perf smoke budget)

Runtime profile selects SLO profile by default, but can be overridden:

```bash
export AMARYLLIS_SLO_PROFILE=release
```

Optional custom SLO profile directory:

```bash
export AMARYLLIS_SLO_PROFILE_DIR=/absolute/path/to/slo_profiles
```

## Quality Budget Environment Keys

These are auto-populated from the selected SLO profile unless explicitly set:

- `AMARYLLIS_SLO_BUDGET_REQUEST_BURN_RATE`
- `AMARYLLIS_SLO_BUDGET_RUN_BURN_RATE`
- `AMARYLLIS_PERF_BUDGET_MAX_P95_MS`
- `AMARYLLIS_PERF_BUDGET_MAX_ERROR_RATE_PCT`

`/service/observability/slo` now includes active runtime/SLO profile and effective quality budget.

## QoS Governor Keys

QoS governor controls runtime mode switching (`quality` / `balanced` / `power_save`):

- `AMARYLLIS_QOS_MODE`
- `AMARYLLIS_QOS_AUTO_ENABLED`
- `AMARYLLIS_QOS_THERMAL_STATE`
- `AMARYLLIS_QOS_TTFT_TARGET_MS`
- `AMARYLLIS_QOS_TTFT_CRITICAL_MS`
- `AMARYLLIS_QOS_REQUEST_LATENCY_TARGET_MS`
- `AMARYLLIS_QOS_REQUEST_LATENCY_CRITICAL_MS`
- `AMARYLLIS_QOS_KV_PRESSURE_TARGET_EVENTS`
- `AMARYLLIS_QOS_KV_PRESSURE_CRITICAL_EVENTS`

Runtime service endpoints:

- `GET /service/qos` (current mode, route mode, thermal state, thresholds, observed metrics)
- `POST /service/qos/mode` (manual mode/auto toggle/thermal override update for service scope)
- `POST /service/qos/thermal` (explicit thermal-state update: `unknown/cool/warm/hot/critical`)

## Drift Check (CI)

Use the blocking drift check:

```bash
python scripts/release/check_runtime_profile_drift.py
```

The check validates profile schemas, loads `AppConfig` through each profile, and fails on target/budget drift.
