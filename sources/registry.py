from __future__ import annotations

from typing import Any

from sources.base import SUPPORTED_NEWS_SOURCES, SourceConnector, SourceQuery, normalize_source_name
from sources.reddit_connector import RedditSourceConnector
from sources.web_connector import WebSourceConnector
from sources.x_connector import XSourceConnector


class SourceConnectorRegistry:
    def __init__(self) -> None:
        self._connectors: dict[str, SourceConnector] = {}
        self.register(WebSourceConnector())
        self.register(RedditSourceConnector())
        self.register(XSourceConnector())

    def register(self, connector: SourceConnector) -> None:
        name = normalize_source_name(str(getattr(connector, "name", "")))
        self._connectors[name] = connector

    def names(self) -> list[str]:
        return sorted(self._connectors.keys())

    def get(self, source: str) -> SourceConnector | None:
        normalized = str(source or "").strip().lower()
        return self._connectors.get(normalized)

    def health(self) -> dict[str, Any]:
        items: dict[str, Any] = {}
        for name, connector in self._connectors.items():
            try:
                payload = connector.health()
            except Exception as exc:
                payload = {"source": name, "status": "error", "detail": str(exc)}
            items[name] = payload if isinstance(payload, dict) else {"source": name, "status": "unknown"}
        return {
            "supported_sources": list(SUPPORTED_NEWS_SOURCES),
            "connectors": items,
        }

    def search(self, *, source: str, query: SourceQuery) -> list[dict[str, Any]]:
        connector = self.get(source)
        if connector is None:
            raise ValueError(f"Unknown source connector: {source}")
        items = connector.search(query)
        return [item.to_dict() for item in items]

