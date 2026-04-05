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
from unittest.mock import patch


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate news mission E2E contract: mission planning, ingest+dedup persistence, "
            "grounded digest composition, and inbox delivery artifact."
        )
    )
    parser.add_argument(
        "--min-citation-coverage",
        type=float,
        default=float(os.getenv("AMARYLLIS_NEWS_GATE_MIN_CITATION_COVERAGE", "0.95")),
        help="Minimum citation coverage rate required in digest metrics.",
    )
    parser.add_argument(
        "--min-sections",
        type=int,
        default=int(os.getenv("AMARYLLIS_NEWS_GATE_MIN_SECTIONS", "1")),
        help="Minimum digest section count required.",
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


def main() -> int:
    args = _parse_args()
    project_root = Path(__file__).resolve().parents[2]
    if str(project_root) not in sys.path:
        sys.path.insert(0, str(project_root))

    min_coverage = max(0.0, min(float(args.min_citation_coverage), 1.0))
    min_sections = max(1, int(args.min_sections))

    try:
        from fastapi.testclient import TestClient  # noqa: PLC0415
    except Exception as exc:
        print(f"[news-mission-gate] FAILED import_error={exc}")
        return 2

    errors: list[str] = []
    checks: list[dict[str, Any]] = []
    report: dict[str, Any] = {}
    app: Any | None = None

    with tempfile.TemporaryDirectory(prefix="amaryllis-news-mission-gate-") as tmp:
        support_dir = Path(tmp) / "support"
        auth_tokens = {
            "user-token": {"user_id": "user-1", "scopes": ["user"]},
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
            print(f"[news-mission-gate] FAILED import_or_boot_error={exc}")
            return 2

        try:
            services = getattr(getattr(app, "state", None), "services", None)
            if services is None:
                print("[news-mission-gate] FAILED missing_services")
                return 2

            fake_report = {
                "topic": "AI",
                "window_hours": 24,
                "sources": ["web", "reddit"],
                "internet_scope": {},
                "query_bundle": ["AI site:example.com"],
                "per_source_count": {"web": 2, "reddit": 1},
                "connector_errors": {},
                "raw_count": 3,
                "deduped_count": 2,
                "duplicate_count": 1,
                "dedup_policy": {"strategy": "canonical_url_key_v1", "unique_story_count": 2},
                "generated_at": "2026-04-06T00:00:00+00:00",
                "items": [
                    {
                        "source": "web",
                        "canonical_id": "web-1",
                        "canonical_story_key": "https://example.com/story-1",
                        "url": "https://example.com/story-1",
                        "title": "AI Story One",
                        "excerpt": "Story one excerpt",
                        "author": "team",
                        "published_at": "2026-04-05T10:00:00+00:00",
                        "ingested_at": "2026-04-06T00:00:00+00:00",
                        "raw_score": 0.93,
                        "metadata": {
                            "canonical_story_key": "https://example.com/story-1",
                            "merged_sources": ["web", "reddit"],
                            "merged_count": 2,
                            "dedup_policy": {
                                "strategy": "canonical_url_key_v1",
                                "key": "https://example.com/story-1",
                            },
                            "provenance": [
                                {
                                    "source": "web",
                                    "canonical_id": "web-1",
                                    "canonical_story_key": "https://example.com/story-1",
                                    "url": "https://example.com/story-1",
                                    "title": "AI Story One",
                                    "published_at": "2026-04-05T10:00:00+00:00",
                                },
                                {
                                    "source": "reddit",
                                    "canonical_id": "t3_story_1",
                                    "canonical_story_key": "https://example.com/story-1",
                                    "url": "https://example.com/story-1",
                                    "title": "Discussion for Story One",
                                    "published_at": "2026-04-05T10:10:00+00:00",
                                },
                            ],
                        },
                    },
                    {
                        "source": "web",
                        "canonical_id": "web-2",
                        "canonical_story_key": "https://example.com/story-2",
                        "url": "https://example.com/story-2",
                        "title": "AI Story Two",
                        "excerpt": "Story two excerpt",
                        "author": "team",
                        "published_at": "2026-04-05T09:00:00+00:00",
                        "ingested_at": "2026-04-06T00:00:00+00:00",
                        "raw_score": 0.84,
                        "metadata": {
                            "canonical_story_key": "https://example.com/story-2",
                            "merged_sources": ["web"],
                            "merged_count": 1,
                            "dedup_policy": {
                                "strategy": "canonical_url_key_v1",
                                "key": "https://example.com/story-2",
                            },
                            "provenance": [
                                {
                                    "source": "web",
                                    "canonical_id": "web-2",
                                    "canonical_story_key": "https://example.com/story-2",
                                    "url": "https://example.com/story-2",
                                    "title": "AI Story Two",
                                    "published_at": "2026-04-05T09:00:00+00:00",
                                }
                            ],
                        },
                    },
                ],
            }

            with TestClient(app) as client:
                planned = client.post(
                    "/news/missions/plan",
                    headers=_auth("user-token"),
                    json={
                        "user_id": "user-1",
                        "topic": "AI",
                        "sources": ["web", "reddit"],
                        "window_hours": 24,
                        "max_items_per_source": 20,
                        "timezone": "UTC",
                        "start_immediately": False,
                    },
                )
                checks.append({"name": "plan_status", "status": planned.status_code, "expected": 200})
                if planned.status_code != 200:
                    errors.append(f"plan_status:{planned.status_code}")
                plan_payload = planned.json() if planned.headers.get("content-type", "").startswith("application/json") else {}
                mission_plan = plan_payload.get("mission_plan") if isinstance(plan_payload, dict) else {}
                schedule_type = str((mission_plan or {}).get("schedule_type") or "")
                checks.append({"name": "plan_schedule_type", "value": schedule_type, "expected": "weekly"})
                if schedule_type != "weekly":
                    errors.append(f"plan_schedule_type:{schedule_type}")

                with patch.object(services.news_pipeline, "ingest_preview", return_value=fake_report):
                    ingested = client.post(
                        "/news/ingest/preview",
                        headers=_auth("user-token"),
                        json={
                            "user_id": "user-1",
                            "topic": "AI",
                            "sources": ["web", "reddit"],
                            "window_hours": 24,
                            "max_items_per_source": 20,
                            "internet_scope": {},
                            "persist": True,
                        },
                    )

                checks.append({"name": "ingest_status", "status": ingested.status_code, "expected": 200})
                if ingested.status_code != 200:
                    errors.append(f"ingest_status:{ingested.status_code}")
                ingest_payload = ingested.json() if ingested.headers.get("content-type", "").startswith("application/json") else {}
                report_payload = ingest_payload.get("report") if isinstance(ingest_payload, dict) else {}
                deduped_count = int((report_payload or {}).get("deduped_count") or 0)
                duplicate_count = int((report_payload or {}).get("duplicate_count") or 0)
                persisted_count = int((ingest_payload or {}).get("persisted_count") or 0)
                checks.append({"name": "ingest_deduped_count", "value": deduped_count, "expected": 2})
                checks.append({"name": "ingest_duplicate_count", "value": duplicate_count, "expected": 1})
                checks.append({"name": "ingest_persisted_count", "value": persisted_count, "expected": 2})
                if deduped_count != 2:
                    errors.append(f"ingest_deduped_count:{deduped_count}")
                if duplicate_count != 1:
                    errors.append(f"ingest_duplicate_count:{duplicate_count}")
                if persisted_count != 2:
                    errors.append(f"ingest_persisted_count:{persisted_count}")

                composed = client.post(
                    "/news/digest/compose",
                    headers=_auth("user-token"),
                    json={
                        "user_id": "user-1",
                        "topic": "AI",
                        "deliver_to_inbox": True,
                    },
                )
                checks.append({"name": "digest_status", "status": composed.status_code, "expected": 200})
                if composed.status_code != 200:
                    errors.append(f"digest_status:{composed.status_code}")
                compose_payload = composed.json() if composed.headers.get("content-type", "").startswith("application/json") else {}
                digest = compose_payload.get("digest") if isinstance(compose_payload, dict) else {}
                metrics = digest.get("metrics") if isinstance(digest, dict) else {}
                section_count = int((metrics or {}).get("section_count") or 0)
                citation_coverage = float((metrics or {}).get("citation_coverage_rate") or 0.0)
                checks.append({"name": "digest_section_count", "value": section_count, "min": min_sections})
                checks.append(
                    {"name": "digest_citation_coverage", "value": citation_coverage, "min": min_coverage}
                )
                if section_count < min_sections:
                    errors.append(f"section_count_below_min:{section_count}<{min_sections}")
                if citation_coverage < min_coverage:
                    errors.append(f"citation_coverage_below_min:{citation_coverage}<{min_coverage}")

                sections = digest.get("sections") if isinstance(digest, dict) else []
                if isinstance(sections, list):
                    for idx, section in enumerate(sections, start=1):
                        if not isinstance(section, dict):
                            errors.append(f"section_invalid_type:{idx}")
                            continue
                        refs = section.get("source_refs")
                        confidence = str(section.get("confidence") or "").strip().lower()
                        if not isinstance(refs, list) or not refs:
                            errors.append(f"section_missing_refs:{idx}")
                        if confidence not in {"low", "medium", "high"}:
                            errors.append(f"section_bad_confidence:{idx}:{confidence}")
                else:
                    errors.append("digest_sections_not_list")

                inbox_item = compose_payload.get("inbox_item") if isinstance(compose_payload, dict) else {}
                inbox_id = str((inbox_item or {}).get("id") or "")
                inbox_category = str((inbox_item or {}).get("category") or "")
                checks.append({"name": "digest_inbox_item_present", "value": bool(inbox_id)})
                checks.append({"name": "digest_inbox_item_category", "value": inbox_category, "expected": "news"})
                if not inbox_id:
                    errors.append("digest_inbox_item_missing")
                if inbox_category != "news":
                    errors.append(f"digest_inbox_bad_category:{inbox_category}")

                inbox_listed = client.get(
                    "/inbox",
                    headers=_auth("user-token"),
                    params={"user_id": "user-1", "category": "news", "limit": 20},
                )
                checks.append({"name": "inbox_list_status", "status": inbox_listed.status_code, "expected": 200})
                if inbox_listed.status_code != 200:
                    errors.append(f"inbox_list_status:{inbox_listed.status_code}")
                inbox_payload = (
                    inbox_listed.json()
                    if inbox_listed.headers.get("content-type", "").startswith("application/json")
                    else {}
                )
                inbox_items = inbox_payload.get("items") if isinstance(inbox_payload, dict) else []
                inbox_ids = {
                    str(item.get("id"))
                    for item in inbox_items
                    if isinstance(item, dict) and str(item.get("id") or "").strip()
                }
                checks.append({"name": "inbox_item_id_visible", "value": inbox_id in inbox_ids})
                if inbox_id and inbox_id not in inbox_ids:
                    errors.append("digest_inbox_item_not_listed")

                delivery_policy = client.post(
                    "/news/delivery/policies/upsert",
                    headers=_auth("user-token"),
                    json={
                        "user_id": "user-1",
                        "topic": "AI",
                        "channels": [
                            {
                                "channel": "webhook",
                                "enabled": True,
                                "max_targets": 1,
                                "targets": ["https://example.com/hooks/news"],
                            },
                            {
                                "channel": "email",
                                "enabled": True,
                                "max_targets": 1,
                                "targets": ["digest@example.com"],
                            },
                        ],
                    },
                )
                checks.append({"name": "delivery_policy_upsert_status", "status": delivery_policy.status_code, "expected": 200})
                if delivery_policy.status_code != 200:
                    errors.append(f"delivery_policy_upsert_status:{delivery_policy.status_code}")

                outbound = client.post(
                    "/news/digest/compose",
                    headers=_auth("user-token"),
                    json={
                        "user_id": "user-1",
                        "topic": "AI",
                        "deliver_to_inbox": False,
                        "deliver_to_outbound": True,
                        "outbound_dry_run": True,
                    },
                )
                checks.append({"name": "outbound_digest_status", "status": outbound.status_code, "expected": 200})
                if outbound.status_code != 200:
                    errors.append(f"outbound_digest_status:{outbound.status_code}")
                outbound_payload = (
                    outbound.json()
                    if outbound.headers.get("content-type", "").startswith("application/json")
                    else {}
                )
                outbound_delivery = (
                    outbound_payload.get("outbound_delivery") if isinstance(outbound_payload, dict) else {}
                )
                outbound_summary = outbound_delivery.get("summary") if isinstance(outbound_delivery, dict) else {}
                outbound_events = int(outbound_payload.get("outbound_event_count") or 0)
                outbound_attempts = int((outbound_summary or {}).get("attempted_targets") or 0)
                outbound_delivered = int((outbound_summary or {}).get("delivered_targets") or 0)
                checks.append({"name": "outbound_attempted_targets", "value": outbound_attempts, "expected": 2})
                checks.append({"name": "outbound_delivered_targets", "value": outbound_delivered, "expected": 2})
                checks.append({"name": "outbound_event_count", "value": outbound_events, "expected": 2})
                if outbound_attempts != 2:
                    errors.append(f"outbound_attempted_targets:{outbound_attempts}")
                if outbound_delivered != 2:
                    errors.append(f"outbound_delivered_targets:{outbound_delivered}")
                if outbound_events != 2:
                    errors.append(f"outbound_event_count:{outbound_events}")

                outbound_event_list = client.get(
                    "/news/delivery/events",
                    headers=_auth("user-token"),
                    params={"user_id": "user-1", "topic": "AI", "limit": 20},
                )
                checks.append(
                    {
                        "name": "outbound_event_list_status",
                        "status": outbound_event_list.status_code,
                        "expected": 200,
                    }
                )
                if outbound_event_list.status_code != 200:
                    errors.append(f"outbound_event_list_status:{outbound_event_list.status_code}")

            report = {
                "suite": "news_mission_gate_v1",
                "summary": {
                    "status": "pass" if not errors else "fail",
                    "checks_total": len(checks),
                    "checks_failed": len(errors),
                    "errors": errors,
                    "min_citation_coverage": min_coverage,
                    "min_sections": min_sections,
                },
                "checks": checks,
            }
        finally:
            if app is not None:
                _shutdown_app(app)

    if args.output:
        output = Path(args.output)
        if not output.is_absolute():
            output = project_root / output
        _write_json(output, report)
        print(f"[news-mission-gate] report={output}")

    if errors:
        print("[news-mission-gate] FAILED")
        for item in errors:
            print(f"- {item}")
        return 1
    print("[news-mission-gate] OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
