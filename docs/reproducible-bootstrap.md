# Reproducible Local Bootstrap

This path is intended for clean-machine setup and CI-like reproducibility.

## One Command

From repository root:

```bash
./scripts/bootstrap/reproducible_local_bootstrap.sh
```

The script will:
- validate toolchain manifest drift (`scripts/release/check_toolchain_drift.py`) and pinned Python runtime,
- create a virtual environment (`.venv` by default),
- install deterministic dependencies from `requirements.lock`,
- run dependency drift guard (`scripts/release/check_dependency_drift.py`),
- run runtime/SLO profile drift guard (`scripts/release/check_runtime_profile_drift.py`),
- validate golden task suite schema (`scripts/eval/run_golden_tasks.py --validate-only`).
- validate deterministic eval/replay fixtures (`scripts/release/check_eval_replay_determinism.py`).

Reference:

- `docs/toolchain-manifest.md`
- `docs/eval-replay-determinism.md`

## Environment Variables

- `AMARYLLIS_BOOTSTRAP_VENV`: custom venv directory (default: `<repo>/.venv`)
- `AMARYLLIS_BOOTSTRAP_PYTHON`: explicit python executable (default and pinned: `python3.11`)

Examples:

```bash
AMARYLLIS_BOOTSTRAP_VENV="$HOME/.venvs/amaryllis" \
AMARYLLIS_BOOTSTRAP_PYTHON="python3.11" \
./scripts/bootstrap/reproducible_local_bootstrap.sh
```

## After Bootstrap

Activate environment:

```bash
source .venv/bin/activate
```

Run runtime:

```bash
uvicorn runtime.server:app --host localhost --port 8000 --reload
```
