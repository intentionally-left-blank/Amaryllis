#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate provenance-by-default contract for RAG-grounded chat responses, "
            "including source trace coverage and telemetry emission."
        )
    )
    parser.add_argument(
        "--min-grounded-sources",
        type=int,
        default=int(os.getenv("AMARYLLIS_PROVENANCE_GATE_MIN_GROUNDED_SOURCES", "1")),
        help="Minimum required source count for grounded response.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional JSON report output path.",
    )
    return parser.parse_args()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _shutdown_app(app: object) -> None:
    services = getattr(getattr(app, "state", None), "services", None)
    if services is None:
        return
    try:
        services.automation_scheduler.stop()
        if services.memory_consolidation_worker is not None:
            services.memory_consolidation_worker.stop()
        if services.backup_scheduler is not None:
            services.backup_scheduler.stop()
        services.agent_run_manager.stop()
        services.database.close()
        services.vector_store.persist()
    except Exception:
        pass


def _request_json(client: Any, *, method: str, path: str, headers: dict[str, str], payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    response = client.request(method, path, headers=headers, json=payload)
    data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
    return int(response.status_code), (data if isinstance(data, dict) else {})


def _request_stream_first_chunk(
    client: Any,
    *,
    path: str,
    headers: dict[str, str],
    payload: dict[str, Any],
) -> tuple[int, dict[str, Any], str]:
    response = client.post(path, headers=headers, json=payload)
    status_code = int(response.status_code)
    if status_code != 200:
        return status_code, {}, ""
    chunk_payload: dict[str, Any] = {}
    raw_text = ""
    for line in response.iter_lines():
        raw = str(line or "")
        raw_text = raw
        if not raw.startswith("data: "):
            continue
        data = raw.removeprefix("data: ").strip()
        if not data or data == "[DONE]":
            continue
        try:
            parsed = json.loads(data)
        except Exception:
            continue
        if isinstance(parsed, dict):
            chunk_payload = parsed
            break
    return status_code, chunk_payload, raw_text


def _load_generation_metrics(path: Path, *, request_id: str) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except Exception:
            continue
        if not isinstance(event, dict):
            continue
        if str(event.get("event_type") or "") != "generation_loop_metrics":
            continue
        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
        if str(payload.get("request_id") or "") != request_id:
            continue
        rows.append(payload)
    return rows


def main() -> int:
    args = _parse_args()
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    min_grounded_sources = max(1, int(args.min_grounded_sources))

    try:
        from fastapi.testclient import TestClient  # noqa: PLC0415
    except Exception as exc:
        print(f"[provenance-coverage-gate] FAILED import_error={exc}")
        return 2

    errors: list[str] = []
    checks: list[dict[str, Any]] = []
    app: Any | None = None
    report: dict[str, Any] = {}

    with tempfile.TemporaryDirectory(prefix="amaryllis-provenance-gate-") as tmp:
        support_dir = Path(tmp) / "support"
        telemetry_path = support_dir / "data" / "telemetry.jsonl"

        auth_tokens = {
            "user-token": {"user_id": "user-1", "scopes": ["user"]},
            "isolated-user-token": {"user_id": "user-2", "scopes": ["user"]},
            "service-token": {"user_id": "svc-runtime", "scopes": ["service"]},
            "admin-token": {"user_id": "admin", "scopes": ["admin", "user"]},
        }
        os.environ["AMARYLLIS_SUPPORT_DIR"] = str(support_dir)
        os.environ["AMARYLLIS_AUTH_ENABLED"] = "true"
        os.environ["AMARYLLIS_AUTH_TOKENS"] = json.dumps(auth_tokens, ensure_ascii=False)
        os.environ["AMARYLLIS_MEMORY_CONSOLIDATION_ENABLED"] = "false"
        os.environ["AMARYLLIS_MCP_ENDPOINTS"] = ""
        os.environ["AMARYLLIS_SECURITY_PROFILE"] = "production"
        os.environ["AMARYLLIS_COGNITION_BACKEND"] = "deterministic"

        try:
            import runtime.server as server_module  # noqa: PLC0415

            server_module = importlib.reload(server_module)
            app = server_module.app
        except Exception as exc:
            print(f"[provenance-coverage-gate] FAILED import_or_boot_error={exc}")
            return 2

        try:
            services = getattr(getattr(app, "state", None), "services", None)
            if services is None:
                print("[provenance-coverage-gate] FAILED missing_services")
                return 2

            facts = [
                "The codename for this release is amaryllis-orbit.",
                "The release contact is Alex Stone.",
                "The support escalation channel is #release-war-room.",
            ]
            for fact in facts:
                services.memory_manager.remember_fact(
                    user_id="user-1",
                    text=fact,
                    metadata={"source": "provenance_gate"},
                )

            with TestClient(app) as client:
                # Non-stream grounded response should include source trace.
                status, grounded = _request_json(
                    client,
                    method="POST",
                    path="/v1/chat/completions",
                    headers=_auth("user-token"),
                    payload={
                        "user_id": "user-1",
                        "messages": [
                            {"role": "user", "content": "What is the codename for this release?"},
                        ],
                        "stream": False,
                        "max_tokens": 128,
                    },
                )
                checks.append({"name": "chat_grounded_non_stream_status", "status": status, "expected": 200})
                if status != 200:
                    errors.append(f"chat_grounded_non_stream_status:{status}")

                provenance = grounded.get("provenance") if isinstance(grounded.get("provenance"), dict) else {}
                request_id = str(grounded.get("request_id") or "")
                checks.append({"name": "chat_grounded_request_id_present", "value": bool(request_id)})
                if not request_id:
                    errors.append("chat_grounded_missing_request_id")

                version = str(provenance.get("version") or "")
                grounded_flag = bool(provenance.get("grounded"))
                sources = provenance.get("sources") if isinstance(provenance.get("sources"), list) else []
                checks.append({"name": "chat_grounded_provenance_version", "value": version})
                checks.append({"name": "chat_grounded_flag", "value": grounded_flag})
                checks.append({"name": "chat_grounded_sources_count", "value": len(sources)})
                if version != "provenance_v1":
                    errors.append(f"chat_grounded_bad_version:{version}")
                if not grounded_flag:
                    errors.append("chat_grounded_flag_false")
                if len(sources) < min_grounded_sources:
                    errors.append(
                        f"chat_grounded_sources_below_min:{len(sources)}<{min_grounded_sources}"
                    )

                if sources:
                    first = sources[0] if isinstance(sources[0], dict) else {}
                    for required in ("layer", "source_id", "rank", "score", "excerpt"):
                        if required not in first:
                            errors.append(f"chat_grounded_source_missing_field:{required}")

                # Streaming response first chunk must carry provenance payload.
                stream_status, first_chunk, stream_raw = _request_stream_first_chunk(
                    client,
                    path="/v1/chat/completions",
                    headers=_auth("user-token"),
                    payload={
                        "user_id": "user-1",
                        "messages": [
                            {"role": "user", "content": "Who is the release contact?"},
                        ],
                        "stream": True,
                        "max_tokens": 128,
                    },
                )
                checks.append({"name": "chat_stream_status", "status": stream_status, "expected": 200})
                if stream_status != 200:
                    errors.append(f"chat_stream_status:{stream_status}")
                chunk_provenance = (
                    first_chunk.get("provenance")
                    if isinstance(first_chunk.get("provenance"), dict)
                    else {}
                )
                checks.append(
                    {
                        "name": "chat_stream_first_chunk_has_provenance",
                        "value": bool(chunk_provenance),
                        "raw": stream_raw,
                    }
                )
                if not chunk_provenance:
                    errors.append("chat_stream_missing_provenance")

                # Non-grounded response (user without memory facts) should still carry provenance object.
                anon_status, anon = _request_json(
                    client,
                    method="POST",
                    path="/v1/chat/completions",
                    headers=_auth("isolated-user-token"),
                    payload={
                        "messages": [
                            {"role": "user", "content": "Say hello without memory context."},
                        ],
                        "stream": False,
                        "max_tokens": 64,
                    },
                )
                checks.append({"name": "chat_anon_status", "status": anon_status, "expected": 200})
                if anon_status != 200:
                    errors.append(f"chat_anon_status:{anon_status}")
                anon_provenance = anon.get("provenance") if isinstance(anon.get("provenance"), dict) else {}
                checks.append({"name": "chat_anon_has_provenance", "value": bool(anon_provenance)})
                if not anon_provenance:
                    errors.append("chat_anon_missing_provenance")
                if str(anon_provenance.get("version") or "") != "provenance_v1":
                    errors.append("chat_anon_bad_provenance_version")
                if bool(anon_provenance.get("grounded", True)):
                    errors.append("chat_anon_should_not_be_grounded")

                # Telemetry for grounded request must include provenance_* fields.
                generation_rows = _load_generation_metrics(telemetry_path, request_id=request_id)
                checks.append({"name": "telemetry_generation_rows", "value": len(generation_rows)})
                if not generation_rows:
                    errors.append("telemetry_missing_generation_loop_metrics")
                else:
                    row = generation_rows[-1]
                    if not bool(row.get("provenance_grounded", False)):
                        errors.append("telemetry_provenance_grounded_false")
                    if int(row.get("provenance_sources_count") or 0) < min_grounded_sources:
                        errors.append("telemetry_provenance_sources_count_below_min")

            report = {
                "suite": "provenance_coverage_gate_v1",
                "summary": {
                    "status": "pass" if not errors else "fail",
                    "checks_total": len(checks),
                    "checks_failed": len(errors),
                    "errors": errors,
                    "min_grounded_sources": min_grounded_sources,
                },
                "checks": checks,
            }
        finally:
            if app is not None:
                _shutdown_app(app)

    if args.output:
        output_path = Path(args.output)
        if not output_path.is_absolute():
            output_path = project_root / output_path
        _write_json(output_path, report)
        print(f"[provenance-coverage-gate] report={output_path}")

    if errors:
        print("[provenance-coverage-gate] FAILED")
        for item in errors:
            print(f"- {item}")
        return 1

    print("[provenance-coverage-gate] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
