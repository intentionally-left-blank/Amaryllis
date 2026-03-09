from __future__ import annotations

from typing import Any

from memory.episodic_memory import EpisodicMemory
from memory.semantic_memory import SemanticMemory
from memory.user_memory import UserMemory


class MemoryManager:
    def __init__(
        self,
        episodic: EpisodicMemory,
        semantic: SemanticMemory,
        user_memory: UserMemory,
    ) -> None:
        self.episodic = episodic
        self.semantic = semantic
        self.user_memory = user_memory

    def add_interaction(
        self,
        user_id: str,
        agent_id: str | None,
        role: str,
        content: str,
    ) -> None:
        self.episodic.add(
            user_id=user_id,
            agent_id=agent_id,
            role=role,
            content=content,
        )

    def remember_fact(
        self,
        user_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        return self.semantic.add(
            user_id=user_id,
            text=text,
            metadata=metadata,
        )

    def set_user_preference(self, user_id: str, key: str, value: str) -> None:
        self.user_memory.set(user_id=user_id, key=key, value=value)

    def get_context(self, user_id: str, agent_id: str | None, query: str) -> dict[str, Any]:
        return {
            "episodic": self.episodic.recent(user_id=user_id, agent_id=agent_id, limit=12),
            "semantic": self.semantic.search(user_id=user_id, query=query, top_k=5),
            "user": self.user_memory.get_all(user_id=user_id),
        }
