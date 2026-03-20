# Linux Parity Matrix (P2-E01)

## Purpose
This matrix defines the minimum Linux acceptance surface for Tier-1 runtime parity.

## Scope
- Runtime/API checks are executed via `scripts/release/linux_parity_smoke.py`.
- Gate is blocking in CI (`.github/workflows/release-gate.yml`).
- Linux target is validated on `ubuntu-latest` with `--require-linux`.

## Parity Matrix

| Domain | Required capability | Acceptance signal | Smoke path(s) |
|---|---|---|---|
| `run` | Agent lifecycle + run lifecycle are callable | Agent create + run create/list/get + diagnostics return `200` and API version headers | `/agents/create`, `/agents/{id}/runs`, `/agents/runs/{run_id}`, `/agents/runs/{run_id}/diagnostics` |
| `voice` | Voice session control + STT health | STT health + session start/list/get/stop return `200` | `/voice/stt/health`, `/voice/sessions/start`, `/voice/sessions`, `/voice/sessions/{id}`, `/voice/sessions/{id}/stop` |
| `tools` | Tool catalog + permission prompt listing + debug health | Tool and MCP listings + guardrail/debug health return `200` | `/tools`, `/tools/permissions/prompts`, `/mcp/tools`, `/debug/tools/guardrails`, `/debug/tools/mcp-health` |
| `observability` | Service health/SLO/lifecycle/metrics endpoints | All service observability endpoints return `200`, `/service/observability/metrics` returns `text/plain` | `/health`, `/service/health`, `/service/observability/slo`, `/service/observability/metrics`, `/service/api/lifecycle` |

## Local Run

```bash
python3 scripts/release/linux_parity_smoke.py \
  --iterations 1 \
  --output artifacts/linux-parity-smoke-report.json
```

## CI Run

```bash
python3 scripts/release/linux_parity_smoke.py \
  --iterations "${AMARYLLIS_LINUX_PARITY_ITERATIONS:-1}" \
  --require-linux \
  --output artifacts/linux-parity-smoke-report.json
```

## Report Contract

The smoke report includes:
- platform metadata (`system`, `release`, `machine`, `sys_platform`)
- per-domain pass/fail counters
- failed checks with endpoint-level details
- latency summary (`p50`, `p95`, `max`)
