# Runtime Lifecycle Contract (Phase 3)

## Goal

Define a deterministic service-management contract for local runtime lifecycle on Linux and macOS.

Current implementation slice (`P3-B01`):

- manifest renderer: `scripts/runtime/render_service_manifest.py`
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

## Contract Guarantees

- Deterministic rendering for same input args.
- Explicit runtime env wiring in both targets:
  - `AMARYLLIS_LINUX_INSTALL_ROOT`
  - `AMARYLLIS_LINUX_RELEASE_CHANNEL`
  - `AMARYLLIS_HOST`
  - `AMARYLLIS_PORT`
- Extra env entries accepted via repeatable `--environment KEY=VALUE`.
- Invalid env entries fail fast.

## Planned Next Steps

- lifecycle installer/uninstaller commands for `systemd --user` and `launchctl`.
- start/stop/status/rollback CLI wrappers.
- release smoke gate for lifecycle startup SLA and recovery checks.

## Tests

- `tests/test_runtime_service_manifest_renderer.py`
