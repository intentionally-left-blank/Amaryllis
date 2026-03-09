from __future__ import annotations

from typing import Any

from storage.database import Database
from storage.vector_store import VectorStore


class SemanticMemory:
    def __init__(self, database: Database, vector_store: VectorStore) -> None:
        self.database = database
        self.vector_store = vector_store

    def add(self, user_id: str, text: str, metadata: dict[str, Any] | None = None) -> int:
        entry_metadata = {**(metadata or {}), "user_id": user_id}
        semantic_id = self.database.add_semantic_entry(
            user_id=user_id,
            text=text,
            metadata=entry_metadata,
        )
        self.vector_store.add_text(
            text=text,
            metadata={**entry_metadata, "semantic_id": semantic_id},
        )
        return semantic_id

    def search(self, user_id: str, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        matches = self.vector_store.search(query=query, top_k=top_k)
        filtered: list[dict[str, Any]] = []
        for match in matches:
            metadata = match.get("metadata", {})
            if metadata.get("user_id") == user_id:
                filtered.append(match)
        return filtered
