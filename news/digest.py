from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _coerce_citation(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": _clean_text(entry.get("source")).lower(),
        "url": _clean_text(entry.get("url")),
        "canonical_id": _clean_text(entry.get("canonical_id")),
        "canonical_story_key": _clean_text(entry.get("canonical_story_key")),
        "title": _clean_text(entry.get("title")),
        "published_at": _clean_text(entry.get("published_at")),
    }


def _citation_signature(ref: dict[str, Any]) -> tuple[str, str, str]:
    return (
        _clean_text(ref.get("source")).lower(),
        _clean_text(ref.get("canonical_id")),
        _clean_text(ref.get("url")),
    )


def _extract_source_refs(item: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = item.get("metadata")
    metadata_payload = dict(metadata) if isinstance(metadata, dict) else {}
    refs: list[dict[str, Any]] = []
    provenance = metadata_payload.get("provenance")
    if isinstance(provenance, list):
        refs.extend(_coerce_citation(entry) for entry in provenance if isinstance(entry, dict))

    if not refs:
        refs.append(
            _coerce_citation(
                {
                    "source": item.get("source"),
                    "url": item.get("url"),
                    "canonical_id": item.get("canonical_id"),
                    "canonical_story_key": item.get("canonical_story_key")
                    or metadata_payload.get("canonical_story_key"),
                    "title": item.get("title"),
                    "published_at": item.get("published_at"),
                }
            )
        )

    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for ref in refs:
        signature = _citation_signature(ref)
        if signature in seen:
            continue
        seen.add(signature)
        if not _clean_text(ref.get("url")):
            continue
        deduped.append(ref)
    return deduped


def _section_confidence(*, refs: list[dict[str, Any]], raw_score: float | None) -> str:
    unique_sources = {
        _clean_text(ref.get("source")).lower()
        for ref in refs
        if _clean_text(ref.get("source"))
    }
    if len(unique_sources) >= 2:
        return "high"
    if refs and raw_score is not None and raw_score >= 0.75:
        return "high"
    if refs:
        return "medium"
    return "low"


def _rank_key(item: dict[str, Any]) -> tuple[float, str, str]:
    score = _safe_float(item.get("raw_score")) or 0.0
    published_at = _clean_text(item.get("published_at"))
    title = _clean_text(item.get("title")).lower()
    return (score, published_at, title)


def _section_summary(item: dict[str, Any]) -> str:
    excerpt = _clean_text(item.get("excerpt"))
    if excerpt:
        return excerpt[:320]
    title = _clean_text(item.get("title"))
    if title:
        return f"Key update: {title}."
    return "Key update with limited extractable summary."


def compose_grounded_digest(
    *,
    topic: str,
    items: list[dict[str, Any]],
    max_sections: int = 7,
) -> dict[str, Any]:
    normalized_topic = _clean_text(topic) or "General"
    bounded_sections = max(1, min(int(max_sections), 20))
    normalized_items = [item for item in items if isinstance(item, dict)]
    ranked = sorted(normalized_items, key=_rank_key, reverse=True)
    selected = ranked[:bounded_sections]

    sections: list[dict[str, Any]] = []
    sections_with_citations = 0
    confidence_histogram = {"high": 0, "medium": 0, "low": 0}

    for idx, item in enumerate(selected, start=1):
        refs = _extract_source_refs(item)
        if refs:
            sections_with_citations += 1
        confidence = _section_confidence(refs=refs, raw_score=_safe_float(item.get("raw_score")))
        confidence_histogram[confidence] = int(confidence_histogram.get(confidence, 0)) + 1
        sections.append(
            {
                "section_id": f"story-{idx}",
                "headline": _clean_text(item.get("title")) or f"{normalized_topic} update #{idx}",
                "summary": _section_summary(item),
                "confidence": confidence,
                "published_at": _clean_text(item.get("published_at")) or None,
                "source_refs": refs,
                "source_count": len(
                    {
                        _clean_text(ref.get("source")).lower()
                        for ref in refs
                        if _clean_text(ref.get("source"))
                    }
                ),
                "provenance_count": len(refs),
                "canonical_story_key": _clean_text(item.get("canonical_story_key"))
                or _clean_text((item.get("metadata") or {}).get("canonical_story_key")),
            }
        )

    section_count = len(sections)
    coverage = float(sections_with_citations / section_count) if section_count else 1.0
    top_links = [ref["url"] for section in sections for ref in section.get("source_refs", [])[:1] if ref.get("url")][:10]
    summary = (
        f"{normalized_topic}: {section_count} ключевых обновлений, "
        f"покрытие ссылками {coverage:.0%}."
    )

    return {
        "topic": normalized_topic,
        "generated_at": _utc_now_iso(),
        "summary": summary,
        "sections": sections,
        "top_links": top_links,
        "citation_policy": {
            "version": "digest_citation_policy_v1",
            "requires_source_refs_per_section": True,
            "min_source_refs_per_section": 1,
            "confidence_levels": ["low", "medium", "high"],
        },
        "metrics": {
            "section_count": section_count,
            "sections_with_citations": sections_with_citations,
            "citation_coverage_rate": round(coverage, 4),
            "confidence_histogram": confidence_histogram,
        },
    }

