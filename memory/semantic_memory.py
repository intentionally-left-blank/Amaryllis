from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from storage.database import Database
from storage.vector_store import VectorStore


class SemanticMemory:
    def __init__(self, database: Database, vector_store: VectorStore) -> None:
        self.database = database
        self.vector_store = vector_store

    def add(
        self,
        user_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
        kind: str = "fact",
        confidence: float = 0.8,
        importance: float = 0.5,
        fingerprint: str | None = None,
    ) -> int:
        entry_metadata = {**(metadata or {}), "user_id": user_id}
        semantic_id = self.database.add_semantic_entry(
            user_id=user_id,
            text=text,
            metadata=entry_metadata,
            kind=kind,
            confidence=confidence,
            importance=importance,
            fingerprint=fingerprint,
        )
        self.vector_store.add_text(
            text=text,
            metadata={
                **entry_metadata,
                "semantic_id": semantic_id,
                "kind": kind,
                "confidence": confidence,
                "importance": importance,
                "fingerprint": fingerprint,
            },
        )
        return semantic_id

    def search(self, user_id: str, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        if top_k <= 0:
            return []

        # Pull a wider candidate set from vector search and rerank by memory signal.
        matches = self.vector_store.search(query=query, top_k=max(top_k * 4, top_k))
        reranked: list[dict[str, Any]] = []
        seen: set[int] = set()

        for match in matches:
            metadata = match.get("metadata", {})
            if not isinstance(metadata, dict) or metadata.get("user_id") != user_id:
                continue

            semantic_id_raw = metadata.get("semantic_id")
            try:
                semantic_id = int(semantic_id_raw)
            except (TypeError, ValueError):
                semantic_id = None

            entry: dict[str, Any] | None = None
            if semantic_id is not None:
                if semantic_id in seen:
                    continue
                entry = self.database.get_semantic_entry(semantic_id=semantic_id, user_id=user_id)
                if entry is None or int(entry.get("is_active", 0)) != 1:
                    continue
                seen.add(semantic_id)

            row_metadata = entry.get("metadata", {}) if entry else metadata
            if not isinstance(row_metadata, dict):
                row_metadata = {}

            vector_score = _clamp(float(match.get("score", 0.0)))
            confidence = _clamp(float((entry or {}).get("confidence", row_metadata.get("confidence", 0.8))))
            importance = _clamp(float((entry or {}).get("importance", row_metadata.get("importance", 0.5))))
            recency_score = _recency_score((entry or {}).get("created_at"))

            # Simple weighted score for retrieval quality.
            score = (
                0.55 * vector_score
                + 0.15 * recency_score
                + 0.15 * confidence
                + 0.15 * importance
            )

            reranked.append(
                {
                    "text": str((entry or {}).get("text", match.get("text", ""))),
                    "score": score,
                    "vector_score": vector_score,
                    "recency_score": recency_score,
                    "metadata": row_metadata,
                    "kind": str((entry or {}).get("kind", row_metadata.get("kind", "fact"))),
                    "confidence": confidence,
                    "importance": importance,
                    "semantic_id": semantic_id,
                    "created_at": (entry or {}).get("created_at"),
                }
            )

        reranked.sort(key=lambda item: float(item.get("score", 0.0)), reverse=True)
        return reranked[:top_k]


def _clamp(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _recency_score(created_at: Any) -> float:
    if not isinstance(created_at, str) or not created_at:
        return 0.5
    try:
        timestamp = datetime.fromisoformat(created_at)
    except ValueError:
        return 0.5
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    age_hours = max(
        0.0,
        (datetime.now(timezone.utc) - timestamp.astimezone(timezone.utc)).total_seconds() / 3600.0,
    )
    # 1.0 now, ~0.5 after one day, lower for old items.
    return 1.0 / (1.0 + (age_hours / 24.0))
