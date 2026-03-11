from __future__ import annotations

from typing import Any

from storage.database import Database


class UserMemory:
    def __init__(self, database: Database) -> None:
        self.database = database

    def set(
        self,
        user_id: str,
        key: str,
        value: str,
        confidence: float = 0.9,
        importance: float = 0.7,
        source: str | None = None,
    ) -> None:
        self.database.set_user_memory(
            user_id=user_id,
            key=key,
            value=value,
            confidence=confidence,
            importance=importance,
            source=source,
        )

    def get_all(self, user_id: str) -> dict[str, str]:
        return self.database.get_user_memory(user_id=user_id)

    def get(self, user_id: str, key: str) -> dict[str, Any] | None:
        return self.database.get_user_memory_item(user_id=user_id, key=key)

    def items(self, user_id: str) -> list[dict[str, Any]]:
        return self.database.get_user_memory_items(user_id=user_id)
