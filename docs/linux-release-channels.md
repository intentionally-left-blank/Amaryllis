# Linux Release Channels and Rollback Strategy

## Goal

Provide safe `stable/canary` rollouts on Linux with deterministic rollback and no release-data loss during channel switches.

## Channel Model

Install root layout:

- `releases/<release_id>`: immutable runtime snapshot (`src` + `venv`)
- `channels/stable` -> symlink to active stable release
- `channels/canary` -> symlink to active canary release
- `current` -> compatibility pointer (follows stable by default)

Launcher behavior (`~/.local/bin/amaryllis-runtime`):

- default channel: `stable`
- override with `AMARYLLIS_LINUX_RELEASE_CHANNEL=canary`

## Rollout Flow

1. Build candidate release and run release gates.
2. Install to canary:

```bash
./scripts/install_linux.sh --channel canary
```

3. Validate canary in real environment (health, key workflows, latency/error budget).
4. Promote commit to stable and install:

```bash
./scripts/install_linux.sh --channel stable
```

## Rollback Flow

Rollback command:

```bash
python3 scripts/release/linux_channel_rollback.py \
  --channel <stable|canary> \
  --steps 1
```

Safety properties:

- rollback only targets releases present in channel history,
- rollback refuses if current channel target is outside tracked history,
- stable rollback also updates `current`,
- channel history appends rollback target for auditability.

## Retention and Data Safety

Installer pruning uses `AMARYLLIS_KEEP_RELEASES` and preserves active targets:

- `current`
- `channels/stable`
- `channels/canary`

This prevents deleting currently deployed stable/canary rollback points.

## Verification

Blocking release gate includes:

- `scripts/release/linux_installer_smoke.py --require-linux`

Smoke verifies:

- stable install and upgrade,
- canary install and upgrade,
- canary rollback via `linux_channel_rollback.py`,
- launcher contract after rollback,
- prior canary release remains available after rollback.

Additional contract tests:

- `tests/test_linux_channel_rollback.py`
- `tests/test_linux_installer_smoke.py`
- `tests/test_linux_installer_script.py`
