from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


class Database:
    def __init__(self, database_path: Path) -> None:
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

        self._conn = sqlite3.connect(self.database_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS episodic_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    agent_id TEXT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS semantic_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS user_memory (
                    user_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (user_id, key)
                );

                CREATE TABLE IF NOT EXISTS agents (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    system_prompt TEXT NOT NULL,
                    model TEXT,
                    tools_json TEXT NOT NULL,
                    user_id TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )
            self._conn.commit()

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO settings(key, value)
                VALUES(?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, value),
            )
            self._conn.commit()

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM settings WHERE key = ?",
                (key,),
            ).fetchone()
        return row["value"] if row else default

    def add_episodic_event(
        self,
        user_id: str,
        agent_id: str | None,
        role: str,
        content: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO episodic_memory(user_id, agent_id, role, content, created_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (user_id, agent_id, role, content, self._utc_now()),
            )
            self._conn.commit()

    def list_episodic_events(
        self,
        user_id: str,
        agent_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []

        with self._lock:
            if agent_id:
                rows = self._conn.execute(
                    """
                    SELECT role, content, created_at
                    FROM episodic_memory
                    WHERE user_id = ? AND agent_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (user_id, agent_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT role, content, created_at
                    FROM episodic_memory
                    WHERE user_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (user_id, limit),
                ).fetchall()

        events = [dict(row) for row in rows]
        events.reverse()
        return events

    def add_semantic_entry(
        self,
        user_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO semantic_memory(user_id, text, metadata_json, created_at)
                VALUES(?, ?, ?, ?)
                """,
                (user_id, text, metadata_json, self._utc_now()),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def set_user_memory(self, user_id: str, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO user_memory(user_id, key, value, updated_at)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(user_id, key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=excluded.updated_at
                """,
                (user_id, key, value, self._utc_now()),
            )
            self._conn.commit()

    def get_user_memory(self, user_id: str) -> dict[str, str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value FROM user_memory WHERE user_id = ?",
                (user_id,),
            ).fetchall()

        return {row["key"]: row["value"] for row in rows}

    def upsert_agent(self, agent: dict[str, Any]) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO agents(id, name, system_prompt, model, tools_json, user_id, created_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name=excluded.name,
                    system_prompt=excluded.system_prompt,
                    model=excluded.model,
                    tools_json=excluded.tools_json,
                    user_id=excluded.user_id
                """,
                (
                    agent["id"],
                    agent["name"],
                    agent["system_prompt"],
                    agent.get("model"),
                    json.dumps(agent.get("tools", []), ensure_ascii=False),
                    agent.get("user_id"),
                    agent["created_at"],
                ),
            )
            self._conn.commit()

    def get_agent(self, agent_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM agents WHERE id = ?",
                (agent_id,),
            ).fetchone()

        if not row:
            return None

        data = dict(row)
        data["tools"] = json.loads(data.pop("tools_json") or "[]")
        return data

    def list_agents(self, user_id: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            if user_id:
                rows = self._conn.execute(
                    "SELECT * FROM agents WHERE user_id = ? ORDER BY created_at DESC",
                    (user_id,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM agents ORDER BY created_at DESC"
                ).fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            data["tools"] = json.loads(data.pop("tools_json") or "[]")
            result.append(data)
        return result
