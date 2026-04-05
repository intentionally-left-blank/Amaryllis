from __future__ import annotations

import copy
from typing import Any


def _clean(value: Any) -> str:
    return str(value or "").strip()


def canonicalize_news_digest_snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    source = copy.deepcopy(payload if isinstance(payload, dict) else {})
    sections_raw = source.get("sections")
    sections_input = sections_raw if isinstance(sections_raw, list) else []

    sections: list[dict[str, Any]] = []
    for index, section in enumerate(sections_input, start=1):
        if not isinstance(section, dict):
            continue
        refs_raw = section.get("source_refs")
        refs_input = refs_raw if isinstance(refs_raw, list) else []
        refs: list[dict[str, Any]] = []
        for ref in refs_input:
            if not isinstance(ref, dict):
                continue
            refs.append(
                {
                    "source": _clean(ref.get("source")).lower(),
                    "url": _clean(ref.get("url")),
                    "canonical_id": _clean(ref.get("canonical_id")),
                    "canonical_story_key": _clean(ref.get("canonical_story_key")),
                    "title": _clean(ref.get("title")),
                    "published_at": _clean(ref.get("published_at")),
                }
            )
        refs.sort(key=lambda item: (_clean(item.get("source")), _clean(item.get("url")), _clean(item.get("canonical_id"))))

        sections.append(
            {
                "section_id": _clean(section.get("section_id")) or f"story-{index}",
                "headline": _clean(section.get("headline")),
                "summary": _clean(section.get("summary")),
                "confidence": _clean(section.get("confidence")).lower(),
                "published_at": _clean(section.get("published_at")),
                "canonical_story_key": _clean(section.get("canonical_story_key")),
                "source_count": int(section.get("source_count") or 0),
                "provenance_count": int(section.get("provenance_count") or 0),
                "source_refs": refs,
            }
        )

    top_links_raw = source.get("top_links")
    top_links = [_clean(item) for item in (top_links_raw if isinstance(top_links_raw, list) else []) if _clean(item)]

    policy_raw = source.get("citation_policy") if isinstance(source.get("citation_policy"), dict) else {}
    confidence_levels_raw = policy_raw.get("confidence_levels")
    confidence_levels = [
        _clean(item).lower()
        for item in (confidence_levels_raw if isinstance(confidence_levels_raw, list) else [])
        if _clean(item)
    ]

    metrics_raw = source.get("metrics") if isinstance(source.get("metrics"), dict) else {}
    histogram_raw = metrics_raw.get("confidence_histogram")
    histogram_input = histogram_raw if isinstance(histogram_raw, dict) else {}
    histogram = {
        "high": int(histogram_input.get("high") or 0),
        "medium": int(histogram_input.get("medium") or 0),
        "low": int(histogram_input.get("low") or 0),
    }

    return {
        "topic": _clean(source.get("topic")),
        "summary": _clean(source.get("summary")),
        "sections": sections,
        "top_links": top_links,
        "citation_policy": {
            "version": _clean(policy_raw.get("version")),
            "requires_source_refs_per_section": bool(policy_raw.get("requires_source_refs_per_section", False)),
            "min_source_refs_per_section": int(policy_raw.get("min_source_refs_per_section") or 0),
            "confidence_levels": confidence_levels,
        },
        "metrics": {
            "section_count": int(metrics_raw.get("section_count") or 0),
            "sections_with_citations": int(metrics_raw.get("sections_with_citations") or 0),
            "citation_coverage_rate": float(metrics_raw.get("citation_coverage_rate") or 0.0),
            "confidence_histogram": histogram,
        },
    }

