# macOS Desktop Parity Smoke (Staging)

## Purpose

`P4-B02` adds a staging smoke report for the macOS desktop action surface:

- notifications,
- clipboard read/write,
- app launch,
- window list/focus/close.

Script:
- `scripts/release/macos_desktop_parity_smoke.py`

This check is host-agnostic and validates the `MacOSDesktopActionAdapter` contract in
synthetic/staging mode (no dependency on running on a real macOS CI host).

## Local Run

```bash
python3 scripts/release/macos_desktop_parity_smoke.py \
  --iterations 2 \
  --output artifacts/macos-desktop-parity-smoke-report.json
```

## Output

Default output (when `--output` is provided):
- `artifacts/macos-desktop-parity-smoke-report.json`

Suite id:
- `macos_desktop_parity_smoke_v1`

Report includes:
- `checks[]` with per-round parity checks,
- `failures[]`,
- `summary` (`checks_total`, `checks_passed`, `checks_failed`, `error_rate_pct`, `status`, latency percentiles),
- host platform metadata and staging target marker (`darwin`).

Exit codes:
- `0`: all checks passed,
- `1`: report generated but parity checks failed,
- `2`: invalid CLI arguments.

## CI Integration

- Release workflow (`release-gate.yml`):
  - runs macOS desktop parity smoke as non-blocking staging step in canary stage,
  - uploads `artifacts/macos-desktop-parity-smoke-report.json`.
- Nightly workflow (`nightly-reliability.yml`):
  - runs the same smoke as non-blocking staging step,
  - uploads `artifacts/nightly-macos-desktop-parity-smoke-report.json`.
