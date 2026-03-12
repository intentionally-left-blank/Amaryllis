from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any
from uuid import uuid4

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

    def list_memory_users(self, limit: int = 200) -> list[str]:
        if limit <= 0:
            return []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT user_id FROM (
                    SELECT user_id FROM episodic_memory
                    UNION
                    SELECT user_id FROM semantic_memory
                    UNION
                    SELECT user_id FROM user_memory
                    UNION
                    SELECT user_id FROM working_memory
                )
                WHERE user_id IS NOT NULL AND TRIM(user_id) != ''
                ORDER BY user_id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [str(row["user_id"]) for row in rows]

    def add_security_audit_event(
        self,
        *,
        event_type: str,
        action: str | None = None,
        actor: str | None = None,
        request_id: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        status: str = "succeeded",
        details: dict[str, Any] | None = None,
        signature: dict[str, Any] | None = None,
    ) -> int:
        details_json = json.dumps(details or {}, ensure_ascii=False)
        signature_json = json.dumps(signature or {}, ensure_ascii=False)
        normalized_status = str(status or "succeeded").strip().lower() or "succeeded"
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO security_audit_events(
                    event_type,
                    action,
                    actor,
                    request_id,
                    target_type,
                    target_id,
                    status,
                    details_json,
                    signature_json,
                    created_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_type,
                    action,
                    actor,
                    request_id,
                    target_type,
                    target_id,
                    normalized_status,
                    details_json,
                    signature_json,
                    self._utc_now(),
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def list_security_audit_events(
        self,
        *,
        limit: int = 200,
        action: str | None = None,
        status: str | None = None,
        actor: str | None = None,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []

        query = "SELECT * FROM security_audit_events WHERE 1 = 1"
        params: list[Any] = []
        if action:
            query += " AND action = ?"
            params.append(action)
        if status:
            query += " AND status = ?"
            params.append(str(status).strip().lower())
        if actor:
            query += " AND actor = ?"
            params.append(actor)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()
        result = [self._decode_security_audit_row(dict(row)) for row in rows]
        result.reverse()
        return result

    def create_agent_run(
        self,
        run_id: str,
        agent_id: str,
        user_id: str,
        session_id: str | None,
        input_message: str,
        status: str = "queued",
        max_attempts: int = 2,
    ) -> None:
        now = self._utc_now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO agent_runs(
                    id,
                    agent_id,
                    user_id,
                    session_id,
                    input_message,
                    status,
                    attempts,
                    max_attempts,
                    cancel_requested,
                    checkpoints_json,
                    created_at,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, 0, ?, 0, '[]', ?, ?)
                """,
                (run_id, agent_id, user_id, session_id, input_message, status, max_attempts, now, now),
            )
            self._conn.commit()

    def update_agent_run_fields(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return

        allowed = {
            "status",
            "attempts",
            "max_attempts",
            "cancel_requested",
            "result_json",
            "error_message",
            "checkpoints_json",
            "started_at",
            "finished_at",
            "updated_at",
        }
        sanitized: dict[str, Any] = {}
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key in {"result_json", "checkpoints_json"} and isinstance(value, (dict, list)):
                sanitized[key] = json.dumps(value, ensure_ascii=False)
            elif key == "cancel_requested" and isinstance(value, bool):
                sanitized[key] = 1 if value else 0
            else:
                sanitized[key] = value

        if not sanitized:
            return

        if "updated_at" not in sanitized:
            sanitized["updated_at"] = self._utc_now()

        assignments = ", ".join(f"{column} = ?" for column in sanitized.keys())
        values = list(sanitized.values()) + [run_id]
        with self._lock:
            self._conn.execute(
                f"UPDATE agent_runs SET {assignments} WHERE id = ?",
                values,
            )
            self._conn.commit()

    def append_agent_run_checkpoint(self, run_id: str, checkpoint: dict[str, Any]) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT checkpoints_json FROM agent_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            if not row:
                return

            checkpoints: list[dict[str, Any]]
            try:
                checkpoints = json.loads(row["checkpoints_json"] or "[]")
            except Exception:
                checkpoints = []
            if not isinstance(checkpoints, list):
                checkpoints = []

            checkpoints.append(
                {
                    "timestamp": self._utc_now(),
                    **checkpoint,
                }
            )
            self._conn.execute(
                """
                UPDATE agent_runs
                SET checkpoints_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (json.dumps(checkpoints, ensure_ascii=False), self._utc_now(), run_id),
            )
            self._conn.commit()

    def get_agent_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM agent_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        if not row:
            return None
        return self._decode_agent_run_row(dict(row))

    def list_agent_runs(
        self,
        user_id: str | None = None,
        agent_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []

        query = "SELECT * FROM agent_runs WHERE 1 = 1"
        params: list[Any] = []
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        if agent_id:
            query += " AND agent_id = ?"
            params.append(agent_id)
        if status:
            query += " AND status = ?"
            params.append(status)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()
        return [self._decode_agent_run_row(dict(row)) for row in rows]

    def create_automation(
        self,
        automation_id: str,
        agent_id: str,
        user_id: str,
        session_id: str | None,
        message: str,
        interval_sec: int,
        next_run_at: str,
        schedule_type: str,
        schedule: dict[str, Any],
        timezone_name: str,
    ) -> None:
        now = self._utc_now()
        schedule_json = json.dumps(schedule, ensure_ascii=False)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO automations(
                    id,
                    agent_id,
                    user_id,
                    session_id,
                    message,
                    interval_sec,
                    schedule_type,
                    schedule_json,
                    timezone,
                    is_enabled,
                    next_run_at,
                    last_run_at,
                    last_error,
                    created_at,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, NULL, NULL, ?, ?)
                """,
                (
                    automation_id,
                    agent_id,
                    user_id,
                    session_id,
                    message,
                    max(10, interval_sec),
                    schedule_type,
                    schedule_json,
                    timezone_name,
                    next_run_at,
                    now,
                    now,
                ),
            )
            self._conn.commit()

    def get_automation(self, automation_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM automations WHERE id = ?",
                (automation_id,),
            ).fetchone()
        if not row:
            return None
        return self._decode_automation_row(dict(row))

    def list_automations(
        self,
        user_id: str | None = None,
        agent_id: str | None = None,
        enabled: bool | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []

        query = "SELECT * FROM automations WHERE 1 = 1"
        params: list[Any] = []
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        if agent_id:
            query += " AND agent_id = ?"
            params.append(agent_id)
        if enabled is not None:
            query += " AND is_enabled = ?"
            params.append(1 if enabled else 0)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()
        return [self._decode_automation_row(dict(row)) for row in rows]

    def list_due_automations(self, now_iso: str, limit: int = 20) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM automations
                WHERE is_enabled = 1 AND next_run_at <= ?
                ORDER BY next_run_at ASC
                LIMIT ?
                """,
                (now_iso, limit),
            ).fetchall()
        return [self._decode_automation_row(dict(row)) for row in rows]

    def update_automation_fields(self, automation_id: str, **fields: Any) -> None:
        if not fields:
            return

        allowed = {
            "agent_id",
            "user_id",
            "session_id",
            "message",
            "interval_sec",
            "schedule_type",
            "schedule_json",
            "timezone",
            "is_enabled",
            "next_run_at",
            "last_run_at",
            "last_error",
            "consecutive_failures",
            "escalation_level",
            "updated_at",
        }
        sanitized: dict[str, Any] = {}
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "interval_sec":
                try:
                    sanitized[key] = max(10, int(value))
                except Exception:
                    continue
            elif key == "schedule_json" and isinstance(value, (dict, list)):
                sanitized[key] = json.dumps(value, ensure_ascii=False)
            elif key == "is_enabled" and isinstance(value, bool):
                sanitized[key] = 1 if value else 0
            elif key == "consecutive_failures":
                try:
                    sanitized[key] = max(0, int(value))
                except Exception:
                    continue
            elif key == "escalation_level":
                normalized = str(value or "").strip().lower()
                if normalized not in {"none", "warning", "critical"}:
                    normalized = "none"
                sanitized[key] = normalized
            else:
                sanitized[key] = value

        if not sanitized:
            return
        if "updated_at" not in sanitized:
            sanitized["updated_at"] = self._utc_now()

        assignments = ", ".join(f"{column} = ?" for column in sanitized.keys())
        values = list(sanitized.values()) + [automation_id]
        with self._lock:
            self._conn.execute(
                f"UPDATE automations SET {assignments} WHERE id = ?",
                values,
            )
            self._conn.commit()

    def delete_automation(self, automation_id: str) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM automations WHERE id = ?",
                (automation_id,),
            )
            self._conn.execute(
                "DELETE FROM automation_events WHERE automation_id = ?",
                (automation_id,),
            )
            self._conn.commit()
        return int(cursor.rowcount or 0) > 0

    def add_automation_event(
        self,
        automation_id: str,
        event_type: str,
        message: str,
        run_id: str | None = None,
    ) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO automation_events(
                    automation_id,
                    event_type,
                    message,
                    run_id,
                    created_at
                )
                VALUES(?, ?, ?, ?, ?)
                """,
                (
                    automation_id,
                    event_type,
                    message,
                    run_id,
                    self._utc_now(),
                ),
            )
            self._conn.commit()
            return int(cursor.lastrowid)

    def list_automation_events(self, automation_id: str, limit: int = 100) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, automation_id, event_type, message, run_id, created_at
                FROM automation_events
                WHERE automation_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (automation_id, limit),
            ).fetchall()
        result = [dict(row) for row in rows]
        result.reverse()
        return result

    def add_inbox_item(
        self,
        *,
        user_id: str,
        category: str,
        severity: str,
        title: str,
        body: str,
        source_type: str | None = None,
        source_id: str | None = None,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        requires_action: bool = False,
    ) -> dict[str, Any]:
        item_id = str(uuid4())
        now = self._utc_now()
        normalized_category = str(category or "general").strip().lower() or "general"
        normalized_severity = str(severity or "info").strip().lower() or "info"
        if normalized_severity not in {"info", "warning", "error"}:
            normalized_severity = "info"
        payload = json.dumps(metadata or {}, ensure_ascii=False)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO inbox_items(
                    id,
                    user_id,
                    category,
                    severity,
                    title,
                    body,
                    source_type,
                    source_id,
                    run_id,
                    metadata_json,
                    is_read,
                    requires_action,
                    created_at,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    item_id,
                    user_id,
                    normalized_category,
                    normalized_severity,
                    title,
                    body,
                    source_type,
                    source_id,
                    run_id,
                    payload,
                    1 if requires_action else 0,
                    now,
                    now,
                ),
            )
            self._conn.commit()

        item = self.get_inbox_item(item_id)
        assert item is not None
        return item

    def get_inbox_item(self, item_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM inbox_items WHERE id = ?",
                (item_id,),
            ).fetchone()
        if not row:
            return None
        return self._decode_inbox_row(dict(row))

    def list_inbox_items(
        self,
        *,
        user_id: str | None = None,
        unread_only: bool = False,
        category: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []

        query = "SELECT * FROM inbox_items WHERE 1 = 1"
        params: list[Any] = []
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        if unread_only:
            query += " AND is_read = 0"
        if category:
            query += " AND category = ?"
            params.append(str(category).strip().lower())
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()
        return [self._decode_inbox_row(dict(row)) for row in rows]

    def set_inbox_item_read(self, item_id: str, is_read: bool) -> dict[str, Any] | None:
        now = self._utc_now()
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE inbox_items
                SET is_read = ?, updated_at = ?
                WHERE id = ?
                """,
                (1 if is_read else 0, now, item_id),
            )
            self._conn.commit()
        if int(cursor.rowcount or 0) <= 0:
            return None
        return self.get_inbox_item(item_id)

    def _decode_agent_run_row(self, row: dict[str, Any]) -> dict[str, Any]:
        try:
            row["result"] = json.loads(row["result_json"]) if row.get("result_json") else None
        except Exception:
            row["result"] = None

        try:
            checkpoints = json.loads(row.get("checkpoints_json") or "[]")
            row["checkpoints"] = checkpoints if isinstance(checkpoints, list) else []
        except Exception:
            row["checkpoints"] = []
        row.pop("result_json", None)
        row.pop("checkpoints_json", None)
        return row

    @staticmethod
    def _decode_automation_row(row: dict[str, Any]) -> dict[str, Any]:
        row["is_enabled"] = bool(int(row.get("is_enabled", 0)))
        interval = row.get("interval_sec")
        try:
            row["interval_sec"] = int(interval)
        except Exception:
            row["interval_sec"] = 60
        schedule_json = row.pop("schedule_json", "{}")
        try:
            parsed = json.loads(schedule_json or "{}")
            row["schedule"] = parsed if isinstance(parsed, dict) else {}
        except Exception:
            row["schedule"] = {}
        row["schedule_type"] = str(row.get("schedule_type") or "interval")
        row["timezone"] = str(row.get("timezone") or "UTC")
        try:
            row["consecutive_failures"] = max(0, int(row.get("consecutive_failures", 0)))
        except Exception:
            row["consecutive_failures"] = 0
        level = str(row.get("escalation_level") or "none").strip().lower()
        if level not in {"none", "warning", "critical"}:
            level = "none"
        row["escalation_level"] = level
        return row

    @staticmethod
    def _decode_inbox_row(row: dict[str, Any]) -> dict[str, Any]:
        row["is_read"] = bool(int(row.get("is_read", 0)))
        row["requires_action"] = bool(int(row.get("requires_action", 0)))
        metadata_json = row.pop("metadata_json", "{}")
        try:
            parsed = json.loads(metadata_json or "{}")
            row["metadata"] = parsed if isinstance(parsed, dict) else {}
        except Exception:
            row["metadata"] = {}
        row["category"] = str(row.get("category") or "general").strip().lower()
        severity = str(row.get("severity") or "info").strip().lower()
        if severity not in {"info", "warning", "error"}:
            severity = "info"
        row["severity"] = severity
        return row

    @staticmethod
    def _decode_security_audit_row(row: dict[str, Any]) -> dict[str, Any]:
        details_json = row.pop("details_json", "{}")
        signature_json = row.pop("signature_json", "{}")
        try:
            details = json.loads(details_json or "{}")
        except Exception:
            details = {}
        try:
            signature = json.loads(signature_json or "{}")
        except Exception:
            signature = {}

        row["details"] = details if isinstance(details, dict) else {}
        row["signature"] = signature if isinstance(signature, dict) else {}
        row["status"] = str(row.get("status") or "succeeded").strip().lower() or "succeeded"
        row["event_type"] = str(row.get("event_type") or "signed_action").strip() or "signed_action"
        return row

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
