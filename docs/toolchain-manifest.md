# Toolchain Manifest (P2-B01)

Amaryllis pins runtime toolchain inputs in a versioned manifest:

- `runtime/toolchains/core.json`

## Why

This closes reproducibility drift between local bootstrap and CI by enforcing one source of truth for:

- Python version (`3.11.11`)
- bootstrap Python binary (`python3.11`)
- CI setup action (`actions/setup-python@v5`)
- CI runner (`ubuntu-latest`)
- Swift tools version (`5.9`)

## Drift Check

Blocking check script:

```bash
python scripts/release/check_toolchain_drift.py
```

The check validates:

- `python-version` pins in configured workflow files
- `actions/setup-python` action version in those workflows
- `runs-on` values in those workflows
- `swift-tools-version` in `macos/AmaryllisApp/Package.swift`
- default bootstrap Python fallback in `scripts/bootstrap/reproducible_local_bootstrap.sh`

Optional local Python executable validation:

```bash
python scripts/release/check_toolchain_drift.py --check-python-executable python3.11
```

## CI and Bootstrap Wiring

- CI workflows call `check_toolchain_drift.py` before dependency install.
- `scripts/bootstrap/reproducible_local_bootstrap.sh` runs the same check with `--check-python-executable` and fails fast on mismatch.

## System Dependency Notes

The manifest also documents baseline system package expectations for Linux/macOS under `system_dependencies`.
