from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


@dataclass
class Agent:
    id: str
    name: str
    system_prompt: str
    model: str | None
    tools: list[str]
    user_id: str | None
    created_at: str

    @classmethod
    def create(
        cls,
        name: str,
        system_prompt: str,
        model: str | None,
        tools: list[str] | None,
        user_id: str | None,
    ) -> "Agent":
        return cls(
            id=str(uuid4()),
            name=name,
            system_prompt=system_prompt,
            model=model,
            tools=tools or [],
            user_id=user_id,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "Agent":
        return cls(
            id=record["id"],
            name=record["name"],
            system_prompt=record["system_prompt"],
            model=record.get("model"),
            tools=list(record.get("tools", [])),
            user_id=record.get("user_id"),
            created_at=record["created_at"],
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "system_prompt": self.system_prompt,
            "model": self.model,
            "tools": self.tools,
            "user_id": self.user_id,
            "created_at": self.created_at,
        }
