from __future__ import annotations

from storage.database import Database


class UserMemory:
    def __init__(self, database: Database) -> None:
        self.database = database

    def set(self, user_id: str, key: str, value: str) -> None:
        self.database.set_user_memory(user_id=user_id, key=key, value=value)

    def get_all(self, user_id: str) -> dict[str, str]:
        return self.database.get_user_memory(user_id=user_id)
