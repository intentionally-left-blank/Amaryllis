# Cognition Backends

## Purpose
Decouple chat/reasoning runtime behavior from a single concrete model manager implementation.

## Contract
- Kernel protocol: `CognitionBackendContract` in `kernel/contracts.py`.
- Runtime/API use the contract surface (`services.model_manager`) instead of binding to a concrete backend class.

## Implementations
- `ModelManagerCognitionBackend`:
  - Adapter over existing `models.model_manager.ModelManager`.
  - Preserves current production behavior.
- `DeterministicCognitionBackend`:
  - Fully local deterministic backend for integration tests and contract validation.
  - Supports the same API surface (`chat`, `stream_chat`, routing, health, model endpoints, onboarding profile + activation plan, package catalog/install/license preflight`).

## Runtime Selection
Set backend mode via environment variable:

```bash
export AMARYLLIS_COGNITION_BACKEND=model_manager
# or
export AMARYLLIS_COGNITION_BACKEND=deterministic
```

Default: `model_manager`.

## Compatibility Validation
- `tests/test_cognition_backends.py`
- `tests/test_cognition_backend_runtime.py`
- `tests/test_kernel_contracts.py`
