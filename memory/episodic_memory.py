from __future__ import annotations

from storage.database import Database


class EpisodicMemory:
    def __init__(self, database: Database) -> None:
        self.database = database

    def add(self, user_id: str, agent_id: str | None, role: str, content: str) -> None:
        self.database.add_episodic_event(
            user_id=user_id,
            agent_id=agent_id,
            role=role,
            content=content,
        )

    def recent(
        self,
        user_id: str,
        agent_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, str]]:
        return self.database.list_episodic_events(
            user_id=user_id,
            agent_id=agent_id,
            limit=limit,
        )
