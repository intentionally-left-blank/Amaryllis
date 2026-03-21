# Runtime Lifecycle Contract (Phase 3)

## Goal

Define a deterministic service-management contract for local runtime lifecycle on Linux and macOS.

Current implementation slice (`P3-B01`, `P3-B02`, `P3-B03`):

- manifest renderer: `scripts/runtime/render_service_manifest.py`
- lifecycle manager: `scripts/runtime/manage_service.py`
- targets: `linux-systemd`, `macos-launchd`

## Manifest Renderer

Linux systemd unit:

```bash
python3 scripts/runtime/render_service_manifest.py \
  --target linux-systemd \
  --service-name amaryllis-runtime \
  --channel stable \
  --output /tmp/amaryllis-runtime.service
```

macOS launchd plist:

```bash
python3 scripts/runtime/render_service_manifest.py \
  --target macos-launchd \
  --service-name amaryllis-runtime \
  --channel stable \
  --output /tmp/org.amaryllis.amaryllis-runtime.plist
```

Install service (manifest write + control hooks):

```bash
python3 scripts/runtime/manage_service.py install \
  --target linux-systemd \
  --channel stable
```

Dry-run lifecycle actions:

```bash
python3 scripts/runtime/manage_service.py status --target linux-systemd --dry-run
python3 scripts/runtime/manage_service.py uninstall --target linux-systemd --dry-run
python3 scripts/runtime/manage_service.py rollback --target linux-systemd --dry-run
```

Lifecycle smoke + startup SLO gate:

```bash
python3 scripts/release/runtime_lifecycle_smoke_gate.py \
  --output artifacts/runtime-lifecycle-smoke-report.json
```

## Contract Guarantees

- Deterministic rendering for same input args.
- Explicit runtime env wiring in both targets:
  - `AMARYLLIS_LINUX_INSTALL_ROOT`
  - `AMARYLLIS_LINUX_RELEASE_CHANNEL`
  - `AMARYLLIS_HOST`
  - `AMARYLLIS_PORT`
  - `AMARYLLIS_RELEASE_QUALITY_DASHBOARD_PATH`
  - `AMARYLLIS_NIGHTLY_MISSION_REPORT_PATH`
- Extra env entries accepted via repeatable `--environment KEY=VALUE`.
- Invalid env entries fail fast.
- Manifest writes are atomic (temp-file + replace) to avoid partial state.
- Install keeps deterministic rollback snapshot at:
  - `<manifest-path>.rollback.bak`
- Failed install attempts auto-restore previous manifest (or remove newly written manifest on first install).
- Explicit rollback command restores from backup and reapplies runtime control hooks:
  - `python3 scripts/runtime/manage_service.py rollback --target linux-systemd`

## Next Hardening Steps

- Add dedicated-host integration smoke (real `systemctl --user` / `launchctl`) in addition to current dry-control contract checks.
- Extend startup gate with recovery-time thresholds after forced lifecycle failure.

## Tests

- `tests/test_runtime_service_manifest_renderer.py`
- `tests/test_runtime_service_lifecycle.py`
- `tests/test_runtime_lifecycle_smoke_gate.py`
