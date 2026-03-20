# Release Provenance and SBOM

## Objective

Produce deterministic release metadata artifacts that can be attached to every candidate build:

- dependency inventory (SBOM) from `requirements.lock`,
- signed release provenance with immutable material digests.

## Generator Script

Use:

```bash
python scripts/release/generate_release_provenance.py \
  --repo-root . \
  --artifact artifacts/release-source.tar.gz \
  --sbom-output artifacts/release-sbom.json \
  --provenance-output artifacts/release-provenance.json \
  --signature-output artifacts/release-provenance.sig
```

Outputs:

- `artifacts/release-sbom.json`
- `artifacts/release-provenance.json`
- `artifacts/release-provenance.sig`

## Signing Policy

Environment variables:

- `AMARYLLIS_PROVENANCE_SIGNING_KEY`: HMAC key used to sign canonical provenance payload.
- `AMARYLLIS_PROVENANCE_KEY_ID`: key identifier embedded in provenance.

Modes:

- default: if signing key is missing, script signs with development fallback key and marks `signature.trust_level=development`.
- strict release mode: pass `--require-signing-key` to fail when signing key is missing.

Recommended CI policy:

- pull request / branch builds: allow development signing for pipeline continuity,
- tag builds (`v*`): enforce `--require-signing-key`.

## Included Materials

The provenance artifact tracks SHA-256 digests for:

- `requirements.lock`,
- toolchain manifest (`runtime/toolchains/core.json`),
- runtime profile manifests (`runtime/profiles/*.json`),
- SLO profiles (`slo_profiles/*.json`),
- generated SBOM,
- additional release artifacts passed via `--artifact`.
