from __future__ import annotations

from typing import Any

from sources.base import SourceConnector, SourceItem, SourceQuery


class XSourceConnector(SourceConnector):
    name = "x"

    def __init__(self) -> None:
        self._status = "planned"

    def health(self) -> dict[str, Any]:
        return {
            "source": self.name,
            "status": self._status,
            "detail": "connector_stub_oauth_pipeline_pending",
        }

    def search(self, query: SourceQuery) -> list[SourceItem]:
        _ = query
        return []

