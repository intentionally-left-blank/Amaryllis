from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from storage.migrations import apply_migrations


class Database:
    def __init__(self, database_path: Path) -> None:
        self.logger = logging.getLogger("amaryllis.storage.database")
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

        self._conn = sqlite3.connect(self.database_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

        with self._lock:
            applied = apply_migrations(self._conn)
        if applied:
            self.logger.info("sqlite_migrations_applied versions=%s", ",".join(str(v) for v in applied))

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
        session_id: str | None = None,
        kind: str = "interaction",
        confidence: float = 1.0,
        importance: float = 0.5,
        fingerprint: str | None = None,
        is_active: bool = True,
        superseded_by: int | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO episodic_memory(
                    user_id,
                    agent_id,
                    session_id,
                    role,
                    content,
                    kind,
                    confidence,
                    importance,
                    fingerprint,
                    is_active,
                    superseded_by,
                    created_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    agent_id,
                    session_id,
                    role,
                    content,
                    kind,
                    confidence,
                    importance,
                    fingerprint,
                    1 if is_active else 0,
                    superseded_by,
                    self._utc_now(),
                ),
            )
            self._conn.commit()

    def list_episodic_events(
        self,
        user_id: str,
        agent_id: str | None = None,
        session_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []

        with self._lock:
            if agent_id and session_id:
                rows = self._conn.execute(
                    """
                    SELECT role, content, created_at, session_id, kind, confidence, importance, fingerprint
                    FROM episodic_memory
                    WHERE user_id = ? AND agent_id = ? AND session_id = ? AND is_active = 1
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (user_id, agent_id, session_id, limit),
                ).fetchall()
            elif agent_id:
                rows = self._conn.execute(
                    """
                    SELECT role, content, created_at, session_id, kind, confidence, importance, fingerprint
                    FROM episodic_memory
                    WHERE user_id = ? AND agent_id = ? AND is_active = 1
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (user_id, agent_id, limit),
                ).fetchall()
            elif session_id:
                rows = self._conn.execute(
                    """
                    SELECT role, content, created_at, session_id, kind, confidence, importance, fingerprint
                    FROM episodic_memory
                    WHERE user_id = ? AND session_id = ? AND is_active = 1
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (user_id, session_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT role, content, created_at, session_id, kind, confidence, importance, fingerprint
                    FROM episodic_memory
                    WHERE user_id = ? AND is_active = 1
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
        kind: str = "fact",
        confidence: float = 0.8,
        importance: float = 0.5,
        fingerprint: str | None = None,
        is_active: bool = True,
        superseded_by: int | None = None,
    ) -> int:
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO semantic_memory(
                    user_id,
                    text,
                    metadata_json,
                    kind,
                    confidence,
                    importance,
                    fingerprint,
                    is_active,
                    superseded_by,
                    created_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    text,
                    metadata_json,
                    kind,
                    confidence,
                    importance,
                    fingerprint,
                    1 if is_active else 0,
                    superseded_by,
                    self._utc_now(),
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def get_semantic_entry(
        self,
        semantic_id: int,
        user_id: str | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            if user_id:
                row = self._conn.execute(
                    """
                    SELECT
                        id,
                        user_id,
                        text,
                        metadata_json,
                        kind,
                        confidence,
                        importance,
                        fingerprint,
                        is_active,
                        superseded_by,
                        created_at
                    FROM semantic_memory
                    WHERE id = ? AND user_id = ?
                    """,
                    (semantic_id, user_id),
                ).fetchone()
            else:
                row = self._conn.execute(
                    """
                    SELECT
                        id,
                        user_id,
                        text,
                        metadata_json,
                        kind,
                        confidence,
                        importance,
                        fingerprint,
                        is_active,
                        superseded_by,
                        created_at
                    FROM semantic_memory
                    WHERE id = ?
                    """,
                    (semantic_id,),
                ).fetchone()

        if not row:
            return None

        item = dict(row)
        try:
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
        except Exception:
            item["metadata"] = {}
        return item

    def list_semantic_entries(
        self,
        user_id: str,
        kind: str | None = None,
        active_only: bool = True,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []

        with self._lock:
            if kind and active_only:
                rows = self._conn.execute(
                    """
                    SELECT
                        id,
                        user_id,
                        text,
                        metadata_json,
                        kind,
                        confidence,
                        importance,
                        fingerprint,
                        is_active,
                        superseded_by,
                        created_at
                    FROM semantic_memory
                    WHERE user_id = ? AND kind = ? AND is_active = 1
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (user_id, kind, limit),
                ).fetchall()
            elif kind:
                rows = self._conn.execute(
                    """
                    SELECT
                        id,
                        user_id,
                        text,
                        metadata_json,
                        kind,
                        confidence,
                        importance,
                        fingerprint,
                        is_active,
                        superseded_by,
                        created_at
                    FROM semantic_memory
                    WHERE user_id = ? AND kind = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (user_id, kind, limit),
                ).fetchall()
            elif active_only:
                rows = self._conn.execute(
                    """
                    SELECT
                        id,
                        user_id,
                        text,
                        metadata_json,
                        kind,
                        confidence,
                        importance,
                        fingerprint,
                        is_active,
                        superseded_by,
                        created_at
                    FROM semantic_memory
                    WHERE user_id = ? AND is_active = 1
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (user_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT
                        id,
                        user_id,
                        text,
                        metadata_json,
                        kind,
                        confidence,
                        importance,
                        fingerprint,
                        is_active,
                        superseded_by,
                        created_at
                    FROM semantic_memory
                    WHERE user_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (user_id, limit),
                ).fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            except Exception:
                item["metadata"] = {}
            result.append(item)
        return result

    def deactivate_semantic_entry(self, semantic_id: int, superseded_by: int | None = None) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE semantic_memory
                SET is_active = 0, superseded_by = ?
                WHERE id = ?
                """,
                (superseded_by, semantic_id),
            )
            self._conn.commit()

    def set_user_memory(
        self,
        user_id: str,
        key: str,
        value: str,
        confidence: float = 0.9,
        importance: float = 0.7,
        source: str | None = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO user_memory(user_id, key, value, confidence, importance, source, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, key) DO UPDATE SET
                    value=excluded.value,
                    confidence=excluded.confidence,
                    importance=excluded.importance,
                    source=excluded.source,
                    updated_at=excluded.updated_at
                """,
                (user_id, key, value, confidence, importance, source, self._utc_now()),
            )
            self._conn.commit()

    def get_user_memory(self, user_id: str) -> dict[str, str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value FROM user_memory WHERE user_id = ?",
                (user_id,),
            ).fetchall()

        return {row["key"]: row["value"] for row in rows}

    def get_user_memory_items(self, user_id: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT key, value, updated_at, confidence, importance, source
                FROM user_memory
                WHERE user_id = ?
                ORDER BY updated_at DESC
                """,
                (user_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_user_memory_item(self, user_id: str, key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT key, value, updated_at, confidence, importance, source
                FROM user_memory
                WHERE user_id = ? AND key = ?
                """,
                (user_id, key),
            ).fetchone()
        return dict(row) if row else None

    def upsert_working_memory(
        self,
        user_id: str,
        session_id: str,
        key: str,
        value: str,
        kind: str = "note",
        confidence: float = 0.5,
        importance: float = 0.5,
        is_active: bool = True,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO working_memory(
                    user_id,
                    session_id,
                    key,
                    value,
                    kind,
                    confidence,
                    importance,
                    is_active,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, session_id, key) DO UPDATE SET
                    value=excluded.value,
                    kind=excluded.kind,
                    confidence=excluded.confidence,
                    importance=excluded.importance,
                    is_active=excluded.is_active,
                    updated_at=excluded.updated_at
                """,
                (
                    user_id,
                    session_id,
                    key,
                    value,
                    kind,
                    confidence,
                    importance,
                    1 if is_active else 0,
                    self._utc_now(),
                ),
            )
            self._conn.commit()

    def list_working_memory(
        self,
        user_id: str,
        session_id: str | None = None,
        limit: int = 16,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []

        with self._lock:
            if session_id:
                rows = self._conn.execute(
                    """
                    SELECT key, value, session_id, kind, confidence, importance, updated_at
                    FROM working_memory
                    WHERE user_id = ? AND session_id = ? AND is_active = 1
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (user_id, session_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT key, value, session_id, kind, confidence, importance, updated_at
                    FROM working_memory
                    WHERE user_id = ? AND is_active = 1
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (user_id, limit),
                ).fetchall()
        return [dict(row) for row in rows]

    def clear_working_memory_session(self, user_id: str, session_id: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE working_memory
                SET is_active = 0, updated_at = ?
                WHERE user_id = ? AND session_id = ?
                """,
                (self._utc_now(), user_id, session_id),
            )
            self._conn.commit()

    def add_extraction_record(
        self,
        user_id: str,
        agent_id: str | None,
        session_id: str | None,
        source_role: str,
        source_text: str,
        extracted_json: dict[str, Any],
    ) -> int:
        payload = json.dumps(extracted_json, ensure_ascii=False)
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO memory_extractions(
                    user_id,
                    agent_id,
                    session_id,
                    source_role,
                    source_text,
                    extracted_json,
                    created_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, agent_id, session_id, source_role, source_text, payload, self._utc_now()),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def list_extraction_records(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        if limit <= 0:
            return []

        with self._lock:
            rows = self._conn.execute(
                """
                SELECT user_id, agent_id, session_id, source_role, source_text, extracted_json, created_at
                FROM memory_extractions
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                item["extracted_json"] = json.loads(item.get("extracted_json", "{}"))
            except Exception:
                item["extracted_json"] = {}
            result.append(item)
        result.reverse()
        return result

    def add_conflict_record(
        self,
        user_id: str,
        layer: str,
        key: str,
        previous_value: str | None,
        incoming_value: str | None,
        resolution: str,
        confidence_prev: float | None = None,
        confidence_new: float | None = None,
    ) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO memory_conflicts(
                    user_id,
                    layer,
                    key,
                    previous_value,
                    incoming_value,
                    resolution,
                    confidence_prev,
                    confidence_new,
                    created_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    layer,
                    key,
                    previous_value,
                    incoming_value,
                    resolution,
                    confidence_prev,
                    confidence_new,
                    self._utc_now(),
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def list_conflict_records(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        if limit <= 0:
            return []

        with self._lock:
            rows = self._conn.execute(
                """
                SELECT layer, key, previous_value, incoming_value, resolution, confidence_prev, confidence_new, created_at
                FROM memory_conflicts
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        result = [dict(row) for row in rows]
        result.reverse()
        return result

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
