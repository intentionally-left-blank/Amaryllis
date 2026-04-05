from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Any
from urllib.parse import urlparse, urlunparse

from sources.base import SourceQuery
from sources.registry import SourceConnectorRegistry


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_domain_list(items: list[str] | None) -> list[str]:
    if not items:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item or "").strip().lower()
        if not value:
            continue
        if value.startswith("http://") or value.startswith("https://"):
            parsed = urlparse(value)
            value = str(parsed.netloc or "").strip().lower()
        value = value.lstrip(".")
        if not value or value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _normalize_seed_url_list(items: list[str] | None) -> list[str]:
    if not items:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for item in items:
        raw = str(item or "").strip()
        if not raw:
            continue
        if not raw.startswith(("http://", "https://")):
            candidate = f"https://{raw}"
        else:
            candidate = raw
        parsed = urlparse(candidate)
        if not parsed.netloc:
            continue
        rebuilt = urlunparse(
            (
                parsed.scheme.lower() or "https",
                parsed.netloc.lower(),
                parsed.path or "/",
                "",
                parsed.query,
                "",
            )
        )
        key = rebuilt.lower()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(rebuilt)
    return normalized


def _seed_url_domain(seed_url: str) -> str | None:
    parsed = urlparse(str(seed_url or "").strip())
    host = str(parsed.netloc or "").strip().lower().lstrip(".")
    if not host:
        return None
    return host


def _seed_url_path_queries(seed_url: str, *, max_depth: int) -> list[str]:
    parsed = urlparse(str(seed_url or "").strip())
    segments = [segment for segment in str(parsed.path or "").split("/") if segment]
    if not segments:
        return []
    normalized_segments: list[str] = []
    for segment in segments:
        token = re.sub(r"[-_]+", " ", segment).strip()
        token = re.sub(r"\s+", " ", token)
        if token:
            normalized_segments.append(token)
    if not normalized_segments:
        return []

    limit = max(1, min(int(max_depth), 5))
    expanded: list[str] = []
    for idx in range(min(limit, len(normalized_segments))):
        phrase = " ".join(normalized_segments[: idx + 1]).strip()
        if phrase:
            expanded.append(phrase)
    return expanded


def _host_matches(host: str, domain: str) -> bool:
    normalized_host = str(host or "").strip().lower().lstrip(".")
    normalized_domain = str(domain or "").strip().lower().lstrip(".")
    if not normalized_host or not normalized_domain:
        return False
    return normalized_host == normalized_domain or normalized_host.endswith(f".{normalized_domain}")


def _canonical_url_key(url: str) -> str:
    parsed = urlparse(str(url or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return str(url or "").strip()
    clean_query = "&".join(
        part
        for part in str(parsed.query or "").split("&")
        if part and not part.lower().startswith(("utm_", "ref=", "fbclid=", "gclid="))
    )
    normalized = urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path or "/",
            "",
            clean_query,
            "",
        )
    )
    return normalized


def _canonical_story_key(item: dict[str, Any]) -> str:
    explicit = str(item.get("canonical_story_key") or "").strip()
    if explicit:
        return explicit
    metadata = item.get("metadata")
    if isinstance(metadata, dict):
        for key in ("canonical_story_key", "story_key", "dedup_key"):
            value = str(metadata.get(key) or "").strip()
            if value:
                return value
    return _canonical_url_key(str(item.get("url") or ""))


def _to_provenance_entry(item: dict[str, Any]) -> dict[str, Any]:
    metadata = item.get("metadata")
    metadata_payload = dict(metadata) if isinstance(metadata, dict) else {}
    canonical_story_key = _canonical_story_key(item)
    return {
        "source": str(item.get("source") or "").strip().lower(),
        "canonical_id": str(item.get("canonical_id") or "").strip(),
        "canonical_story_key": canonical_story_key,
        "url": str(item.get("url") or "").strip(),
        "title": str(item.get("title") or "").strip(),
        "published_at": str(item.get("published_at") or "").strip(),
        "ingested_at": str(item.get("ingested_at") or "").strip(),
        "raw_score": item.get("raw_score"),
        "author": item.get("author"),
        "metadata": metadata_payload,
    }


def _merge_provenance(base: dict[str, Any], incoming: dict[str, Any]) -> None:
    metadata = base.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}
        base["metadata"] = metadata
    provenance_raw = metadata.get("provenance")
    provenance: list[dict[str, Any]]
    if isinstance(provenance_raw, list):
        provenance = [item for item in provenance_raw if isinstance(item, dict)]
    else:
        provenance = []

    incoming_entry = _to_provenance_entry(incoming)
    incoming_signature = (
        str(incoming_entry.get("source") or ""),
        str(incoming_entry.get("canonical_id") or ""),
        str(incoming_entry.get("url") or ""),
    )
    known_signatures = {
        (
            str(item.get("source") or ""),
            str(item.get("canonical_id") or ""),
            str(item.get("url") or ""),
        )
        for item in provenance
    }
    if incoming_signature not in known_signatures:
        provenance.append(incoming_entry)
    metadata["provenance"] = provenance

    merged_sources_raw = metadata.get("merged_sources")
    if isinstance(merged_sources_raw, list):
        merged_sources = [str(item).strip().lower() for item in merged_sources_raw if str(item).strip()]
    else:
        merged_sources = []
    base_source = str(base.get("source") or "").strip().lower()
    if base_source and base_source not in merged_sources:
        merged_sources.append(base_source)
    incoming_source = str(incoming.get("source") or "").strip().lower()
    if incoming_source and incoming_source not in merged_sources:
        merged_sources.append(incoming_source)
    metadata["merged_sources"] = merged_sources
    metadata["merged_count"] = len(provenance)
    story_key = _canonical_story_key(base) or _canonical_story_key(incoming)
    if story_key:
        base["canonical_story_key"] = story_key
        metadata["canonical_story_key"] = story_key
        metadata["dedup_policy"] = {
            "strategy": "canonical_url_key_v1",
            "key": story_key,
        }

    if not str(base.get("excerpt") or "").strip():
        excerpt = str(incoming.get("excerpt") or "").strip()
        if excerpt:
            base["excerpt"] = excerpt
    if not str(base.get("author") or "").strip():
        author = str(incoming.get("author") or "").strip()
        if author:
            base["author"] = author

    base_score = base.get("raw_score")
    incoming_score = incoming.get("raw_score")
    try:
        if incoming_score is not None and (base_score is None or float(incoming_score) > float(base_score)):
            base["raw_score"] = incoming_score
    except Exception:
        if base_score is None and incoming_score is not None:
            base["raw_score"] = incoming_score


def _normalize_query_list(items: list[str] | None) -> list[str]:
    if not items:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        value = str(item or "").strip()
        if not value:
            continue
        lowered = value.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        result.append(value)
    return result


def build_query_bundle(
    *,
    topic: str,
    queries: list[str] | None = None,
    include_domains: list[str] | None = None,
    seed_urls: list[str] | None = None,
    max_depth: int = 1,
) -> list[str]:
    normalized_topic = str(topic or "").strip()
    query_items = _normalize_query_list(queries)
    if not query_items:
        query_items = [normalized_topic] if normalized_topic else []
    if not query_items:
        return []

    normalized_seed_urls = _normalize_seed_url_list(seed_urls)
    domains = _normalize_domain_list(include_domains)
    domain_set: set[str] = set(domains)
    for seed in normalized_seed_urls:
        host = _seed_url_domain(seed)
        if host:
            domain_set.add(host)

    depth = max(1, min(int(max_depth), 5))

    result: list[str] = []
    seen: set[str] = set()

    def _append(candidate: str) -> None:
        query_text = str(candidate or "").strip()
        if not query_text:
            return
        key = query_text.lower()
        if key in seen:
            return
        seen.add(key)
        result.append(query_text)

    if domain_set:
        for query_text in query_items:
            for domain in sorted(domain_set):
                _append(f"{query_text} site:{domain}")
    else:
        for query_text in query_items:
            _append(query_text)

    for seed in normalized_seed_urls:
        domain = _seed_url_domain(seed)
        if not domain:
            continue
        path_hints = _seed_url_path_queries(seed, max_depth=depth)
        for query_text in query_items:
            _append(f"{query_text} site:{domain}")
            for hint in path_hints:
                _append(f"{query_text} site:{domain} {hint}")

    # Keep bundle bounded for predictable latency.
    return result[:30]


@dataclass
class NewsIngestionPipeline:
    source_registry: SourceConnectorRegistry

    def ingest_preview(
        self,
        *,
        topic: str,
        sources: list[str],
        window_hours: int,
        max_items_per_source: int,
        internet_scope: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_scope = internet_scope if isinstance(internet_scope, dict) else {}
        include_domains = _normalize_domain_list(normalized_scope.get("include_domains"))
        exclude_domains = _normalize_domain_list(normalized_scope.get("exclude_domains"))
        seed_urls = _normalize_seed_url_list(normalized_scope.get("seed_urls"))
        max_depth_raw = normalized_scope.get("max_depth", 1)
        try:
            max_depth = max(1, min(int(max_depth_raw), 5))
        except Exception:
            max_depth = 1

        include_set = set(include_domains)
        for seed in seed_urls:
            host = _seed_url_domain(seed)
            if host:
                include_set.add(host)
        effective_include_domains = sorted(include_set)

        query_bundle = build_query_bundle(
            topic=topic,
            queries=_normalize_query_list(normalized_scope.get("queries")),
            include_domains=effective_include_domains,
            seed_urls=seed_urls,
            max_depth=max_depth,
        )
        if not query_bundle:
            query_bundle = [str(topic or "").strip()]

        collected: list[dict[str, Any]] = []
        per_source: dict[str, int] = {}
        connector_errors: dict[str, str] = {}
        now = _utc_now_iso()
        for source in sources:
            normalized_source = str(source or "").strip().lower()
            try:
                items = self.source_registry.search(
                    source=normalized_source,
                    query=SourceQuery(
                        query=str(topic or "").strip(),
                        limit=max(1, min(int(max_items_per_source), 100)),
                        window_hours=max(1, min(int(window_hours), 168)),
                        metadata={
                            "query_bundle": list(query_bundle),
                            "include_domains": list(effective_include_domains),
                            "exclude_domains": list(exclude_domains),
                            "seed_urls": list(seed_urls),
                            "scope": {
                                **dict(normalized_scope),
                                "max_depth": max_depth,
                            },
                        },
                    ),
                )
            except Exception as exc:
                connector_errors[normalized_source] = str(exc)
                per_source[normalized_source] = 0
                continue

            filtered: list[dict[str, Any]] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url") or "").strip()
                if not url:
                    continue
                parsed = urlparse(url)
                host = str(parsed.netloc or "").strip().lower()
                if effective_include_domains:
                    if not any(_host_matches(host, domain) for domain in effective_include_domains):
                        continue
                if exclude_domains:
                    if any(_host_matches(host, domain) for domain in exclude_domains):
                        continue
                payload = dict(item)
                payload["ingested_at"] = now
                filtered.append(payload)
            per_source[normalized_source] = len(filtered)
            collected.extend(filtered)

        deduped: list[dict[str, Any]] = []
        deduped_by_key: dict[str, dict[str, Any]] = {}
        for item in collected:
            key = _canonical_story_key(item)
            if not key:
                continue
            existing = deduped_by_key.get(key)
            if existing is None:
                payload = dict(item)
                payload["canonical_story_key"] = key
                payload_metadata = payload.get("metadata")
                if not isinstance(payload_metadata, dict):
                    payload_metadata = {}
                    payload["metadata"] = payload_metadata
                payload_metadata["provenance"] = [_to_provenance_entry(payload)]
                source_name = str(payload.get("source") or "").strip().lower()
                payload_metadata["merged_sources"] = [source_name] if source_name else []
                payload_metadata["merged_count"] = 1
                payload_metadata["canonical_story_key"] = key
                payload_metadata["dedup_policy"] = {
                    "strategy": "canonical_url_key_v1",
                    "key": key,
                }
                deduped_by_key[key] = payload
                deduped.append(payload)
                continue
            _merge_provenance(existing, item)

        return {
            "topic": str(topic or "").strip(),
            "window_hours": max(1, min(int(window_hours), 168)),
            "sources": [str(item).strip().lower() for item in sources if str(item).strip()],
            "internet_scope": {
                **dict(normalized_scope),
                "include_domains": list(effective_include_domains),
                "exclude_domains": list(exclude_domains),
                "seed_urls": list(seed_urls),
                "max_depth": max_depth,
            },
            "query_bundle": list(query_bundle),
            "per_source_count": per_source,
            "connector_errors": connector_errors,
            "raw_count": len(collected),
            "deduped_count": len(deduped),
            "duplicate_count": max(0, len(collected) - len(deduped)),
            "dedup_policy": {
                "strategy": "canonical_url_key_v1",
                "unique_story_count": len(deduped_by_key),
            },
            "items": deduped,
            "generated_at": now,
        }
