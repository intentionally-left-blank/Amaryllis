# Model Onboarding Profiles

## Purpose
`GET /models/onboarding/profile` provides first-run profile recommendation for model routing.
`GET /models/onboarding/activation-plan` provides one-shot activation intent:
recommended profile + package choice + license preflight + install contract.

The goal is to get a new user to the first successful response without manual model tuning.

## Contract
Response fields:
- `generated_at`: UTC timestamp.
- `request_id`: request correlation id from runtime middleware.
- `active`: currently active provider/model pair.
- `hardware`: runtime-detected machine snapshot (`platform`, `machine`, `cpu_count_logical`, `memory_bytes`, `memory_gb`, provider availability flags).
- `recommended_profile`: one of `fast`, `balanced`, `quality`.
- `reason_codes`: machine-readable reason labels for the recommendation.
- `profiles`: profile map for `fast`, `balanced`, `quality` with:
  - `route_mode` (`local_first`, `balanced`, `quality_first`)
  - routing constraints
  - selected model target
  - fallback candidates

Activation plan additional fields:
- `plan_version`: currently `onboarding_activation_plan_v1`.
- `selected_profile`: normalized profile used for package selection.
- `selected_package_id` / `selected_package`: package chosen for first-run activation.
- `license_admission`: standalone policy decision for selected package.
- `install`: one-click install contract payload (`/models/packages/install`).
- `ready_to_install`: `true` when preflight passes.
- `blockers`: machine-readable reasons when activation is blocked.
- `next_action`: `install_package` or `resolve_blockers`.

## Recommendation Logic (MVP)
- `fast`: selected for low-memory/low-CPU machines.
- `quality`: selected for high-compute machines (and/or cloud-capable setups).
- `balanced`: default profile otherwise.

Profile targets are selected from the same candidate matrix used by routing (`ModelManager`), with provider guardrail penalties applied.

## Deterministic Backend
`DeterministicCognitionBackend` returns a stable onboarding payload with `recommended_profile=balanced` for contract/runtime tests.

## Test Coverage
- `tests/test_model_onboarding_profile.py`
- `tests/test_model_onboarding_profile_api.py`
- `tests/test_cognition_backends.py`
- `tests/test_cognition_backend_runtime.py`
