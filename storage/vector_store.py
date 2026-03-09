from __future__ import annotations

import hashlib
import json
from pathlib import Path
from threading import Lock
from typing import Any

import numpy as np

try:
    import faiss  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    faiss = None


class VectorStore:
    def __init__(self, index_path: Path, dimension: int = 256) -> None:
        self.index_path = Path(index_path)
        self.meta_path = self.index_path.with_suffix(".meta.json")
        self.dimension = dimension
        self._lock = Lock()
        self._entries: list[dict[str, Any]] = []

        self._index = None
        if faiss is not None:
            self._index = faiss.IndexFlatIP(self.dimension)

        self._load()

    def _load(self) -> None:
        if self.meta_path.exists():
            try:
                self._entries = json.loads(self.meta_path.read_text(encoding="utf-8"))
            except Exception:
                self._entries = []

        if self._index is not None and self.index_path.exists():
            try:
                self._index = faiss.read_index(str(self.index_path))
            except Exception:
                self._index = faiss.IndexFlatIP(self.dimension)
                for entry in self._entries:
                    vec = np.array(entry["vector"], dtype=np.float32).reshape(1, -1)
                    self._index.add(vec)

    def persist(self) -> None:
        with self._lock:
            self.meta_path.parent.mkdir(parents=True, exist_ok=True)
            self.meta_path.write_text(
                json.dumps(self._entries, ensure_ascii=False),
                encoding="utf-8",
            )
            if self._index is not None:
                faiss.write_index(self._index, str(self.index_path))

    def add_text(self, text: str, metadata: dict[str, Any] | None = None) -> int:
        vec = self._embed(text)
        entry = {
            "text": text,
            "metadata": metadata or {},
            "vector": vec.tolist(),
        }

        with self._lock:
            self._entries.append(entry)
            entry_id = len(self._entries) - 1
            if self._index is not None:
                self._index.add(vec.reshape(1, -1))

        self.persist()
        return entry_id

    def search(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        if top_k <= 0:
            return []

        query_vec = self._embed(query)

        with self._lock:
            if not self._entries:
                return []

            if self._index is not None:
                distances, indices = self._index.search(query_vec.reshape(1, -1), top_k)
                results: list[dict[str, Any]] = []
                for score, idx in zip(distances[0], indices[0]):
                    if idx < 0 or idx >= len(self._entries):
                        continue
                    entry = self._entries[idx]
                    results.append(
                        {
                            "score": float(score),
                            "text": entry["text"],
                            "metadata": entry["metadata"],
                        }
                    )
                return results

            scores: list[tuple[float, int]] = []
            for idx, entry in enumerate(self._entries):
                vec = np.array(entry["vector"], dtype=np.float32)
                score = float(np.dot(query_vec, vec))
                scores.append((score, idx))

            scores.sort(key=lambda x: x[0], reverse=True)
            result: list[dict[str, Any]] = []
            for score, idx in scores[:top_k]:
                entry = self._entries[idx]
                result.append(
                    {
                        "score": float(score),
                        "text": entry["text"],
                        "metadata": entry["metadata"],
                    }
                )
            return result

    def _embed(self, text: str) -> np.ndarray:
        vec = np.zeros(self.dimension, dtype=np.float32)
        tokens = text.lower().split()
        if not tokens:
            return vec

        for token in tokens:
            digest = hashlib.sha1(token.encode("utf-8")).hexdigest()
            idx = int(digest, 16) % self.dimension
            vec[idx] += 1.0

        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec
