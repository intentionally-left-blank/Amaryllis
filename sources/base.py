from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from typing import Any, Protocol


SUPPORTED_NEWS_SOURCES: tuple[str, ...] = ("reddit", "x", "web")


@dataclass(frozen=True)
class SourceQuery:
    query: str
    limit: int = 20
    window_hours: int = 24
    language: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceItem:
    source: str
    canonical_id: str
    url: str
    title: str
    published_at: str
    excerpt: str | None = None
    author: str | None = None
    raw_score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "canonical_id": self.canonical_id,
            "url": self.url,
            "title": self.title,
            "published_at": self.published_at,
            "excerpt": self.excerpt,
            "author": self.author,
            "raw_score": self.raw_score,
            "metadata": dict(self.metadata),
        }


class SourceConnector(Protocol):
    name: str

    def health(self) -> dict[str, Any]:
        ...

    def search(self, query: SourceQuery) -> list[SourceItem]:
        ...


def canonical_source_id(*, source: str, url: str, title: str | None = None) -> str:
    material = "|".join(
        [
            str(source or "").strip().lower(),
            str(url or "").strip(),
            str(title or "").strip(),
        ]
    )
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
    return digest[:32]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_source_name(source: str) -> str:
    normalized = str(source or "").strip().lower()
    if normalized not in set(SUPPORTED_NEWS_SOURCES):
        raise ValueError("Unsupported source. Allowed: reddit, x, web")
    return normalized

