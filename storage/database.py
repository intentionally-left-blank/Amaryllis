from __future__ import annotations

from contextlib import contextmanager
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Iterator
from urllib.parse import urlparse, urlunparse
from uuid import uuid4

from storage.migrations import apply_migrations


class Database:
    def __init__(self, database_path: Path) -> None:
        self.logger = logging.getLogger("amaryllis.storage.database")
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._write_tx_depth = 0
        self._pending_commit = False

        self._conn = sqlite3.connect(self.database_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row

        with self._lock:
            self._configure_connection()
            applied = apply_migrations(self._conn)
            self._configure_connection()
        if applied:
            self.logger.info("sqlite_migrations_applied versions=%s", ",".join(str(v) for v in applied))

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def _configure_connection(self) -> None:
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA temp_store=MEMORY")
        self._conn.execute("PRAGMA foreign_keys=ON")

    def _commit_locked(self) -> None:
        if self._write_tx_depth > 0:
            self._pending_commit = True
            return
        self._conn.commit()

    @contextmanager
    def write_transaction(self) -> Iterator[None]:
        with self._lock:
            if self._write_tx_depth == 0:
                self._pending_commit = False
            self._write_tx_depth += 1
            try:
                yield
            except Exception:
                if self._write_tx_depth == 1:
                    self._conn.rollback()
                    self._pending_commit = False
                raise
            else:
                if self._write_tx_depth == 1 and self._pending_commit:
                    self._conn.commit()
                    self._pending_commit = False
            finally:
                self._write_tx_depth = max(0, self._write_tx_depth - 1)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def backup_to(self, destination_path: Path) -> None:
        target = Path(destination_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_target = target.with_suffix(f"{target.suffix}.tmp")
        if tmp_target.exists():
            tmp_target.unlink(missing_ok=True)
        with self._lock:
            self._conn.execute("PRAGMA wal_checkpoint(FULL)")
            backup_conn = sqlite3.connect(str(tmp_target))
            try:
                self._conn.backup(backup_conn)
                backup_conn.commit()
            finally:
                backup_conn.close()
        os.replace(tmp_target, target)

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
            self._commit_locked()

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
            self._commit_locked()

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
            self._commit_locked()
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
            self._commit_locked()

    def set_user_memory(
        self,
        user_id: str,
        key: str,
        value: str,
        confidence: float = 0.9,
        importance: float = 0.7,
        source: str | None = None,
        updated_at: str | None = None,
    ) -> None:
        timestamp = updated_at or self._utc_now()
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
                (user_id, key, value, confidence, importance, source, timestamp),
            )
            self._commit_locked()

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
            self._commit_locked()

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
            self._commit_locked()

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
            self._commit_locked()
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
            self._commit_locked()
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
            self._commit_locked()
            return int(cursor.lastrowid)

    def list_security_audit_events(
        self,
        *,
        limit: int = 200,
        event_type: str | None = None,
        action: str | None = None,
        status: str | None = None,
        actor: str | None = None,
        request_id: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []

        query = "SELECT * FROM security_audit_events WHERE 1 = 1"
        params: list[Any] = []
        if event_type:
            query += " AND event_type = ?"
            params.append(str(event_type).strip())
        if action:
            query += " AND action = ?"
            params.append(action)
        if status:
            query += " AND status = ?"
            params.append(str(status).strip().lower())
        if actor:
            query += " AND actor = ?"
            params.append(actor)
        if request_id:
            query += " AND request_id = ?"
            params.append(request_id)
        if target_type:
            query += " AND target_type = ?"
            params.append(target_type)
        if target_id:
            query += " AND target_id = ?"
            params.append(target_id)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()
        result = [self._decode_security_audit_row(dict(row)) for row in rows]
        result.reverse()
        return result

    def add_terminal_action_receipt(
        self,
        *,
        action: str,
        tool_name: str,
        actor: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        request_id: str | None = None,
        permission_id: str | None = None,
        status: str = "succeeded",
        risk_level: str = "medium",
        policy_level: str | None = None,
        rollback_hint: str | None = None,
        arguments: dict[str, Any] | None = None,
        result: Any = None,
        error_message: str | None = None,
        details: dict[str, Any] | None = None,
        action_receipt: dict[str, Any] | None = None,
    ) -> int:
        normalized_status = str(status or "succeeded").strip().lower() or "succeeded"
        if normalized_status not in {"succeeded", "failed", "blocked", "canceled"}:
            normalized_status = "succeeded"
        normalized_risk = str(risk_level or "medium").strip().lower() or "medium"
        if normalized_risk not in {"low", "medium", "high", "critical"}:
            normalized_risk = "medium"
        normalized_action = str(action or "tool_invoke").strip().lower() or "tool_invoke"
        normalized_tool_name = str(tool_name or "").strip()
        if not normalized_tool_name:
            raise ValueError("tool_name is required")

        arguments_json = json.dumps(arguments or {}, ensure_ascii=False)
        result_json = json.dumps(result, ensure_ascii=False) if result is not None else None
        details_json = json.dumps(details or {}, ensure_ascii=False)
        action_receipt_json = json.dumps(action_receipt or {}, ensure_ascii=False)

        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO terminal_action_receipts(
                    action,
                    tool_name,
                    actor,
                    user_id,
                    session_id,
                    request_id,
                    permission_id,
                    status,
                    risk_level,
                    policy_level,
                    rollback_hint,
                    arguments_json,
                    result_json,
                    error_message,
                    details_json,
                    action_receipt_json,
                    created_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_action,
                    normalized_tool_name,
                    actor,
                    user_id,
                    session_id,
                    request_id,
                    permission_id,
                    normalized_status,
                    normalized_risk,
                    str(policy_level).strip() if policy_level not in (None, "") else None,
                    str(rollback_hint).strip() if rollback_hint not in (None, "") else None,
                    arguments_json,
                    result_json,
                    str(error_message) if error_message not in (None, "") else None,
                    details_json,
                    action_receipt_json,
                    self._utc_now(),
                ),
            )
            self._commit_locked()
            return int(cursor.lastrowid)

    def list_terminal_action_receipts(
        self,
        *,
        limit: int = 200,
        tool_name: str | None = None,
        status: str | None = None,
        actor: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
        request_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []

        query = "SELECT * FROM terminal_action_receipts WHERE 1 = 1"
        params: list[Any] = []
        if tool_name:
            query += " AND tool_name = ?"
            params.append(str(tool_name).strip())
        if status:
            query += " AND status = ?"
            params.append(str(status).strip().lower())
        if actor:
            query += " AND actor = ?"
            params.append(actor)
        if user_id:
            query += " AND user_id = ?"
            params.append(user_id)
        if session_id:
            query += " AND session_id = ?"
            params.append(session_id)
        if request_id:
            query += " AND request_id = ?"
            params.append(request_id)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()
        items = [self._decode_terminal_action_receipt_row(dict(row)) for row in rows]
        items.reverse()
        return items

    def create_filesystem_patch_preview(
        self,
        *,
        user_id: str,
        actor: str | None,
        session_id: str | None,
        request_id: str | None,
        path: str,
        target_path: str,
        after_content: str,
        before_exists: bool,
        before_sha256: str | None,
        before_size: int | None,
        after_sha256: str,
        after_size: int,
        diff: dict[str, Any],
        expires_at: str,
    ) -> dict[str, Any]:
        preview_id = str(uuid4())
        now = self._utc_now()
        with self._lock:
            self._expire_filesystem_patch_previews_locked(now_iso=now)
            self._conn.execute(
                """
                INSERT INTO filesystem_patch_previews(
                    id,
                    user_id,
                    actor,
                    session_id,
                    request_id,
                    path,
                    target_path,
                    after_content,
                    before_exists,
                    before_sha256,
                    before_size,
                    after_sha256,
                    after_size,
                    diff_json,
                    status,
                    expires_at,
                    approved_at,
                    applied_at,
                    approval_actor,
                    consumed_request_id,
                    created_at,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, ?, ?)
                """,
                (
                    preview_id,
                    str(user_id or "").strip(),
                    str(actor).strip() if actor not in (None, "") else None,
                    str(session_id).strip() if session_id not in (None, "") else None,
                    str(request_id).strip() if request_id not in (None, "") else None,
                    str(path or "").strip(),
                    str(target_path or "").strip(),
                    str(after_content or ""),
                    1 if bool(before_exists) else 0,
                    str(before_sha256).strip() if before_sha256 not in (None, "") else None,
                    int(before_size) if before_size not in (None, "") else None,
                    str(after_sha256 or "").strip(),
                    max(0, int(after_size)),
                    json.dumps(diff or {}, ensure_ascii=False),
                    "pending",
                    str(expires_at or "").strip() or now,
                    now,
                    now,
                ),
            )
            self._commit_locked()
            row = self._conn.execute(
                "SELECT * FROM filesystem_patch_previews WHERE id = ?",
                (preview_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Failed to persist filesystem patch preview.")
        return self._decode_filesystem_patch_preview_row(dict(row), include_after_content=False)

    def get_filesystem_patch_preview(
        self,
        preview_id: str,
        *,
        include_after_content: bool = False,
    ) -> dict[str, Any] | None:
        normalized_id = str(preview_id or "").strip()
        if not normalized_id:
            return None
        with self._lock:
            self._expire_filesystem_patch_previews_locked(now_iso=self._utc_now())
            row = self._conn.execute(
                "SELECT * FROM filesystem_patch_previews WHERE id = ?",
                (normalized_id,),
            ).fetchone()
        if row is None:
            return None
        return self._decode_filesystem_patch_preview_row(dict(row), include_after_content=include_after_content)

    def list_filesystem_patch_previews(
        self,
        *,
        user_id: str | None = None,
        session_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
        include_after_content: bool = False,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        query = "SELECT * FROM filesystem_patch_previews WHERE 1 = 1"
        params: list[Any] = []
        if user_id:
            query += " AND user_id = ?"
            params.append(str(user_id).strip())
        if session_id:
            query += " AND session_id = ?"
            params.append(str(session_id).strip())
        if status:
            query += " AND status = ?"
            params.append(str(status).strip().lower())
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            self._expire_filesystem_patch_previews_locked(now_iso=self._utc_now())
            rows = self._conn.execute(query, tuple(params)).fetchall()
        result = [
            self._decode_filesystem_patch_preview_row(dict(row), include_after_content=include_after_content)
            for row in rows
        ]
        result.reverse()
        return result

    def approve_filesystem_patch_preview(
        self,
        *,
        preview_id: str,
        actor: str | None,
    ) -> dict[str, Any]:
        normalized_id = str(preview_id or "").strip()
        if not normalized_id:
            raise ValueError("preview_id is required")
        now = self._utc_now()
        with self._lock:
            self._expire_filesystem_patch_previews_locked(now_iso=now)
            row = self._conn.execute(
                "SELECT * FROM filesystem_patch_previews WHERE id = ?",
                (normalized_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Filesystem patch preview not found: {normalized_id}")
            status = str(row["status"] or "").strip().lower()
            if status == "applied":
                raise ValueError(f"Filesystem patch preview already applied: {normalized_id}")
            if status == "expired":
                raise ValueError(f"Filesystem patch preview expired: {normalized_id}")
            if status != "approved":
                self._conn.execute(
                    """
                    UPDATE filesystem_patch_previews
                    SET status = 'approved',
                        approved_at = ?,
                        approval_actor = ?,
                        updated_at = ?
                    WHERE id = ? AND status = 'pending'
                    """,
                    (
                        now,
                        str(actor).strip() if actor not in (None, "") else None,
                        now,
                        normalized_id,
                    ),
                )
                self._commit_locked()
            row_after = self._conn.execute(
                "SELECT * FROM filesystem_patch_previews WHERE id = ?",
                (normalized_id,),
            ).fetchone()
        if row_after is None:
            raise ValueError(f"Filesystem patch preview not found: {normalized_id}")
        return self._decode_filesystem_patch_preview_row(dict(row_after), include_after_content=False)

    def mark_filesystem_patch_preview_applied(
        self,
        *,
        preview_id: str,
        consumed_request_id: str | None,
    ) -> dict[str, Any]:
        normalized_id = str(preview_id or "").strip()
        if not normalized_id:
            raise ValueError("preview_id is required")
        now = self._utc_now()
        with self._lock:
            self._expire_filesystem_patch_previews_locked(now_iso=now)
            row = self._conn.execute(
                "SELECT status FROM filesystem_patch_previews WHERE id = ?",
                (normalized_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Filesystem patch preview not found: {normalized_id}")
            status = str(row["status"] or "").strip().lower()
            if status == "expired":
                raise ValueError(f"Filesystem patch preview expired: {normalized_id}")
            if status not in {"approved", "applied"}:
                raise ValueError(
                    f"Filesystem patch preview must be approved before apply: {normalized_id}"
                )
            if status == "approved":
                self._conn.execute(
                    """
                    UPDATE filesystem_patch_previews
                    SET status = 'applied',
                        applied_at = ?,
                        consumed_request_id = ?,
                        updated_at = ?
                    WHERE id = ? AND status = 'approved'
                    """,
                    (
                        now,
                        str(consumed_request_id).strip() if consumed_request_id not in (None, "") else None,
                        now,
                        normalized_id,
                    ),
                )
                self._commit_locked()
            row_after = self._conn.execute(
                "SELECT * FROM filesystem_patch_previews WHERE id = ?",
                (normalized_id,),
            ).fetchone()
        if row_after is None:
            raise ValueError(f"Filesystem patch preview not found: {normalized_id}")
        return self._decode_filesystem_patch_preview_row(dict(row_after), include_after_content=False)

    def _expire_filesystem_patch_previews_locked(self, *, now_iso: str) -> None:
        self._conn.execute(
            """
            UPDATE filesystem_patch_previews
            SET status = 'expired',
                updated_at = ?
            WHERE status IN ('pending', 'approved')
              AND expires_at <= ?
            """,
            (now_iso, now_iso),
        )

    def record_auth_token_activity(
        self,
        *,
        token_fingerprint: str,
        user_id: str,
        scopes: list[str] | tuple[str, ...] | set[str],
        request_id: str | None,
        path: str,
        method: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        normalized_fingerprint = str(token_fingerprint or "").strip()
        normalized_user = str(user_id or "").strip()
        if not normalized_fingerprint or not normalized_user:
            return
        normalized_scopes = sorted({str(scope or "").strip().lower() for scope in scopes if str(scope or "").strip()})
        now = self._utc_now()
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        scopes_json = json.dumps(normalized_scopes, ensure_ascii=False)
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO security_auth_token_activity(
                    token_fingerprint,
                    user_id,
                    scopes_json,
                    first_seen_at,
                    last_seen_at,
                    last_request_id,
                    last_path,
                    last_method,
                    request_count,
                    metadata_json
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(token_fingerprint) DO UPDATE SET
                    user_id=excluded.user_id,
                    scopes_json=excluded.scopes_json,
                    last_seen_at=excluded.last_seen_at,
                    last_request_id=excluded.last_request_id,
                    last_path=excluded.last_path,
                    last_method=excluded.last_method,
                    request_count=security_auth_token_activity.request_count + 1,
                    metadata_json=excluded.metadata_json
                """,
                (
                    normalized_fingerprint,
                    normalized_user,
                    scopes_json,
                    now,
                    now,
                    request_id,
                    path,
                    str(method or "").upper(),
                    metadata_json,
                ),
            )
            self._commit_locked()

    def list_auth_token_activity(
        self,
        *,
        limit: int = 200,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        query = "SELECT * FROM security_auth_token_activity WHERE 1 = 1"
        params: list[Any] = []
        if user_id:
            query += " AND user_id = ?"
            params.append(str(user_id).strip())
        query += " ORDER BY last_seen_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()
        return [self._decode_auth_token_activity_row(dict(row)) for row in rows]

    def create_provider_session(
        self,
        *,
        session_id: str,
        user_id: str,
        provider: str,
        credential_ref: str,
        credential_fingerprint: str | None,
        scopes: list[str] | tuple[str, ...] | set[str],
        display_name: str | None,
        created_at: str,
        updated_at: str,
        expires_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        normalized_scopes = sorted(
            {str(scope or "").strip().lower() for scope in scopes if str(scope or "").strip()}
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO provider_sessions(
                    id,
                    user_id,
                    provider,
                    display_name,
                    session_type,
                    credential_ref,
                    credential_fingerprint,
                    scopes_json,
                    status,
                    metadata_json,
                    expires_at,
                    revoked_at,
                    revoked_reason,
                    last_used_at,
                    created_at,
                    updated_at
                )
                VALUES(?, ?, ?, ?, 'reference', ?, ?, ?, 'active', ?, ?, NULL, NULL, NULL, ?, ?)
                """,
                (
                    str(session_id or "").strip(),
                    str(user_id or "").strip(),
                    str(provider or "").strip().lower(),
                    str(display_name).strip() if display_name not in (None, "") else None,
                    str(credential_ref or "").strip(),
                    str(credential_fingerprint).strip() if credential_fingerprint not in (None, "") else None,
                    json.dumps(normalized_scopes, ensure_ascii=False),
                    json.dumps(metadata or {}, ensure_ascii=False),
                    str(expires_at).strip() if expires_at not in (None, "") else None,
                    str(created_at or self._utc_now()),
                    str(updated_at or self._utc_now()),
                ),
            )
            self._commit_locked()

    def get_provider_session(self, session_id: str) -> dict[str, Any] | None:
        normalized = str(session_id or "").strip()
        if not normalized:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM provider_sessions WHERE id = ?",
                (normalized,),
            ).fetchone()
        if row is None:
            return None
        return self._decode_provider_session_row(dict(row))

    def list_provider_sessions(
        self,
        *,
        user_id: str | None = None,
        provider: str | None = None,
        include_revoked: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        query = "SELECT * FROM provider_sessions WHERE 1 = 1"
        params: list[Any] = []
        if user_id:
            query += " AND user_id = ?"
            params.append(str(user_id).strip())
        if provider:
            query += " AND provider = ?"
            params.append(str(provider).strip().lower())
        if not include_revoked:
            query += " AND status != 'revoked'"
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()
        return [self._decode_provider_session_row(dict(row)) for row in rows]

    def revoke_provider_session(
        self,
        *,
        session_id: str,
        revoked_reason: str | None = None,
    ) -> bool:
        normalized = str(session_id or "").strip()
        if not normalized:
            return False
        now = self._utc_now()
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE provider_sessions
                SET
                    status = 'revoked',
                    revoked_at = CASE WHEN revoked_at IS NULL THEN ? ELSE revoked_at END,
                    revoked_reason = COALESCE(?, revoked_reason),
                    updated_at = ?
                WHERE id = ? AND status != 'revoked'
                """,
                (
                    now,
                    str(revoked_reason).strip() if revoked_reason not in (None, "") else None,
                    now,
                    normalized,
                ),
            )
            self._commit_locked()
        return int(cursor.rowcount or 0) > 0

    def touch_provider_session(self, *, session_id: str) -> bool:
        normalized = str(session_id or "").strip()
        if not normalized:
            return False
        now = self._utc_now()
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE provider_sessions
                SET last_used_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, normalized),
            )
            self._commit_locked()
        return int(cursor.rowcount or 0) > 0

    @staticmethod
    def _canonical_news_story_key(url: str) -> str:
        parsed = urlparse(str(url or "").strip())
        if not parsed.scheme or not parsed.netloc:
            return str(url or "").strip()
        clean_query = "&".join(
            part
            for part in str(parsed.query or "").split("&")
            if part and not part.lower().startswith(("utm_", "ref=", "fbclid=", "gclid="))
        )
        return urlunparse(
            (
                parsed.scheme.lower(),
                parsed.netloc.lower(),
                parsed.path or "/",
                "",
                clean_query,
                "",
            )
        )

    @staticmethod
    def _safe_news_score(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except Exception:
            return None

    @classmethod
    def _resolve_news_story_key(cls, *, item: dict[str, Any], metadata: dict[str, Any]) -> str:
        direct = str(item.get("canonical_story_key") or "").strip()
        if direct:
            return direct
        for key in ("canonical_story_key", "story_key", "dedup_key"):
            candidate = str(metadata.get(key) or "").strip()
            if candidate:
                return candidate
        return cls._canonical_news_story_key(str(item.get("url") or ""))

    @staticmethod
    def _normalize_news_metadata_payload(value: Any) -> dict[str, Any]:
        return dict(value) if isinstance(value, dict) else {}

    @classmethod
    def _news_provenance_entry_from_item(
        cls,
        *,
        item: dict[str, Any],
        metadata: dict[str, Any],
        story_key: str,
    ) -> dict[str, Any]:
        return {
            "source": str(item.get("source") or "").strip().lower(),
            "canonical_id": str(item.get("canonical_id") or "").strip(),
            "canonical_story_key": story_key,
            "url": str(item.get("url") or "").strip(),
            "title": str(item.get("title") or "").strip(),
            "published_at": str(item.get("published_at") or "").strip(),
            "ingested_at": str(item.get("ingested_at") or "").strip(),
            "raw_score": item.get("raw_score"),
            "author": item.get("author"),
            "metadata": {
                key: payload
                for key, payload in metadata.items()
                if key not in {"provenance", "merged_sources", "merged_count", "canonical_story_key", "dedup_policy"}
            },
        }

    @classmethod
    def _news_provenance_entry_from_row(
        cls,
        *,
        row: dict[str, Any],
        metadata: dict[str, Any],
        story_key: str,
    ) -> dict[str, Any]:
        return {
            "source": str(row.get("source") or "").strip().lower(),
            "canonical_id": str(row.get("canonical_id") or "").strip(),
            "canonical_story_key": story_key,
            "url": str(row.get("url") or "").strip(),
            "title": str(row.get("title") or "").strip(),
            "published_at": str(row.get("published_at") or "").strip(),
            "ingested_at": str(row.get("ingested_at") or "").strip(),
            "raw_score": row.get("raw_score"),
            "author": row.get("author"),
            "metadata": {
                key: payload
                for key, payload in metadata.items()
                if key not in {"provenance", "merged_sources", "merged_count", "canonical_story_key", "dedup_policy"}
            },
        }

    @staticmethod
    def _news_provenance_signature(entry: dict[str, Any]) -> tuple[str, str, str]:
        return (
            str(entry.get("source") or "").strip().lower(),
            str(entry.get("canonical_id") or "").strip(),
            str(entry.get("url") or "").strip(),
        )

    @classmethod
    def _merge_news_metadata(
        cls,
        *,
        existing_row: dict[str, Any],
        existing_metadata: dict[str, Any],
        incoming_item: dict[str, Any],
        incoming_metadata: dict[str, Any],
        story_key: str,
    ) -> dict[str, Any]:
        merged: dict[str, Any] = dict(existing_metadata)
        for key, payload in incoming_metadata.items():
            if key in {"provenance", "merged_sources", "merged_count", "canonical_story_key", "dedup_policy"}:
                continue
            existing_value = merged.get(key)
            if existing_value in (None, "", [], {}) and payload not in (None, "", [], {}):
                merged[key] = payload

        provenance: list[dict[str, Any]] = []
        existing_provenance = existing_metadata.get("provenance")
        if isinstance(existing_provenance, list):
            provenance.extend(item for item in existing_provenance if isinstance(item, dict))
        if not provenance:
            provenance.append(
                cls._news_provenance_entry_from_row(
                    row=existing_row,
                    metadata=existing_metadata,
                    story_key=story_key,
                )
            )

        incoming_provenance_raw = incoming_metadata.get("provenance")
        if isinstance(incoming_provenance_raw, list):
            incoming_provenance = [item for item in incoming_provenance_raw if isinstance(item, dict)]
        else:
            incoming_provenance = [
                cls._news_provenance_entry_from_item(
                    item=incoming_item,
                    metadata=incoming_metadata,
                    story_key=story_key,
                )
            ]

        known_signatures = {cls._news_provenance_signature(item) for item in provenance}
        for item in incoming_provenance:
            entry = dict(item)
            if not str(entry.get("canonical_story_key") or "").strip():
                entry["canonical_story_key"] = story_key
            signature = cls._news_provenance_signature(entry)
            if signature in known_signatures:
                continue
            known_signatures.add(signature)
            provenance.append(entry)

        merged_sources: list[str] = []
        existing_sources = existing_metadata.get("merged_sources")
        if isinstance(existing_sources, list):
            for item in existing_sources:
                source = str(item or "").strip().lower()
                if source and source not in merged_sources:
                    merged_sources.append(source)
        for item in provenance:
            source = str(item.get("source") or "").strip().lower()
            if source and source not in merged_sources:
                merged_sources.append(source)
        existing_source = str(existing_row.get("source") or "").strip().lower()
        if existing_source and existing_source not in merged_sources:
            merged_sources.append(existing_source)
        incoming_source = str(incoming_item.get("source") or "").strip().lower()
        if incoming_source and incoming_source not in merged_sources:
            merged_sources.append(incoming_source)

        merged["provenance"] = provenance
        merged["merged_sources"] = merged_sources
        merged["merged_count"] = len(provenance)
        merged["canonical_story_key"] = story_key
        merged["dedup_policy"] = {
            "strategy": "canonical_url_key_v1",
            "key": story_key,
        }
        return merged

    @classmethod
    def _pick_preferred_news_url(cls, *, existing_url: str, incoming_url: str, story_key: str) -> str:
        normalized_existing = str(existing_url or "").strip()
        normalized_incoming = str(incoming_url or "").strip()
        if not normalized_existing:
            return normalized_incoming
        if not normalized_incoming:
            return normalized_existing
        existing_key = cls._canonical_news_story_key(normalized_existing)
        incoming_key = cls._canonical_news_story_key(normalized_incoming)
        if existing_key == incoming_key == story_key:
            return normalized_incoming if len(normalized_incoming) < len(normalized_existing) else normalized_existing
        if incoming_key == story_key and existing_key != story_key:
            return normalized_incoming
        return normalized_existing

    def upsert_news_items(
        self,
        *,
        user_id: str,
        topic: str,
        items: list[dict[str, Any]],
    ) -> int:
        normalized_user = str(user_id or "").strip()
        normalized_topic = str(topic or "").strip()
        if not normalized_user or not normalized_topic or not items:
            return 0
        upserted = 0
        with self._lock:
            for item in items:
                if not isinstance(item, dict):
                    continue
                source = str(item.get("source") or "").strip().lower()
                canonical_id = str(item.get("canonical_id") or "").strip()
                url = str(item.get("url") or "").strip()
                title = str(item.get("title") or "").strip()
                published_at = str(item.get("published_at") or "").strip()
                ingested_at = str(item.get("ingested_at") or "").strip() or self._utc_now()
                if not source or not canonical_id or not url or not title or not published_at:
                    continue
                metadata_payload = self._normalize_news_metadata_payload(item.get("metadata"))
                story_key = self._resolve_news_story_key(item=item, metadata=metadata_payload)
                if not story_key:
                    continue
                incoming_score = self._safe_news_score(item.get("raw_score"))

                existing_story_row = self._conn.execute(
                    """
                    SELECT *
                    FROM news_items
                    WHERE user_id = ? AND topic = ? AND canonical_story_key = ?
                    ORDER BY id ASC
                    LIMIT 1
                    """,
                    (normalized_user, normalized_topic, story_key),
                ).fetchone()

                if existing_story_row is not None:
                    existing = dict(existing_story_row)
                    try:
                        existing_metadata_payload = json.loads(str(existing.get("metadata_json") or "{}"))
                        if not isinstance(existing_metadata_payload, dict):
                            existing_metadata_payload = {}
                    except Exception:
                        existing_metadata_payload = {}

                    merged_metadata = self._merge_news_metadata(
                        existing_row=existing,
                        existing_metadata=existing_metadata_payload,
                        incoming_item={**dict(item), "canonical_story_key": story_key},
                        incoming_metadata=metadata_payload,
                        story_key=story_key,
                    )

                    merged_url = self._pick_preferred_news_url(
                        existing_url=str(existing.get("url") or ""),
                        incoming_url=url,
                        story_key=story_key,
                    )
                    merged_title = str(existing.get("title") or "").strip() or title
                    incoming_excerpt = str(item.get("excerpt") or "").strip()
                    incoming_author = str(item.get("author") or "").strip()
                    merged_excerpt = str(existing.get("excerpt") or "").strip() or incoming_excerpt
                    merged_author = str(existing.get("author") or "").strip() or incoming_author

                    existing_score = self._safe_news_score(existing.get("raw_score"))
                    if existing_score is None:
                        merged_score = incoming_score
                    elif incoming_score is None:
                        merged_score = existing_score
                    else:
                        merged_score = max(existing_score, incoming_score)

                    existing_published_at = str(existing.get("published_at") or "").strip()
                    if existing_published_at and published_at:
                        merged_published_at = min(existing_published_at, published_at)
                    else:
                        merged_published_at = existing_published_at or published_at
                    merged_ingested_at = max(str(existing.get("ingested_at") or "").strip(), ingested_at)

                    cursor = self._conn.execute(
                        """
                        UPDATE news_items
                        SET
                            url = ?,
                            title = ?,
                            excerpt = ?,
                            author = ?,
                            published_at = ?,
                            ingested_at = ?,
                            raw_score = ?,
                            metadata_json = ?,
                            canonical_story_key = ?
                        WHERE id = ?
                        """,
                        (
                            merged_url,
                            merged_title,
                            merged_excerpt or None,
                            merged_author or None,
                            merged_published_at,
                            merged_ingested_at,
                            merged_score,
                            json.dumps(merged_metadata, ensure_ascii=False),
                            story_key,
                            int(existing.get("id")),
                        ),
                    )
                    if int(cursor.rowcount or 0) > 0:
                        upserted += 1
                    continue

                enriched_metadata = dict(metadata_payload)
                enriched_metadata["canonical_story_key"] = story_key
                enriched_metadata["dedup_policy"] = {
                    "strategy": "canonical_url_key_v1",
                    "key": story_key,
                }
                if not isinstance(enriched_metadata.get("provenance"), list):
                    enriched_metadata["provenance"] = [
                        self._news_provenance_entry_from_item(
                            item={**dict(item), "canonical_story_key": story_key},
                            metadata=metadata_payload,
                            story_key=story_key,
                        )
                    ]
                if not isinstance(enriched_metadata.get("merged_sources"), list):
                    enriched_metadata["merged_sources"] = [source]
                enriched_metadata["merged_count"] = max(
                    1,
                    int(len([entry for entry in enriched_metadata.get("provenance", []) if isinstance(entry, dict)])),
                )

                cursor = self._conn.execute(
                    """
                    INSERT INTO news_items(
                        user_id,
                        topic,
                        source,
                        canonical_id,
                        canonical_story_key,
                        url,
                        title,
                        excerpt,
                        author,
                        published_at,
                        ingested_at,
                        raw_score,
                        metadata_json
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, topic, source, canonical_id, published_at) DO UPDATE SET
                        canonical_story_key=excluded.canonical_story_key,
                        url=excluded.url,
                        title=excluded.title,
                        excerpt=excluded.excerpt,
                        author=excluded.author,
                        ingested_at=excluded.ingested_at,
                        raw_score=excluded.raw_score,
                        metadata_json=excluded.metadata_json
                    """,
                    (
                        normalized_user,
                        normalized_topic,
                        source,
                        canonical_id,
                        story_key,
                        url,
                        title,
                        str(item.get("excerpt") or "").strip() or None,
                        str(item.get("author") or "").strip() or None,
                        published_at,
                        ingested_at,
                        incoming_score,
                        json.dumps(enriched_metadata, ensure_ascii=False),
                    ),
                )
                if int(cursor.rowcount or 0) > 0:
                    upserted += 1
            self._commit_locked()
        return upserted

    def list_news_items(
        self,
        *,
        user_id: str,
        topic: str | None = None,
        source: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        normalized_user = str(user_id or "").strip()
        if not normalized_user or limit <= 0:
            return []
        query = "SELECT * FROM news_items WHERE user_id = ?"
        params: list[Any] = [normalized_user]
        if topic not in (None, ""):
            query += " AND topic = ?"
            params.append(str(topic).strip())
        if source not in (None, ""):
            query += " AND source = ?"
            params.append(str(source).strip().lower())
        query += " ORDER BY ingested_at DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()
        return [self._decode_news_item_row(dict(row)) for row in rows]

    @staticmethod
    def _normalize_news_delivery_topic(topic: str | None) -> str:
        normalized = str(topic or "").strip()
        return normalized if normalized else "*"

    def upsert_news_delivery_policies(
        self,
        *,
        user_id: str,
        topic: str | None,
        channels: list[dict[str, Any]],
    ) -> int:
        normalized_user = str(user_id or "").strip()
        if not normalized_user:
            return 0
        normalized_topic = self._normalize_news_delivery_topic(topic)
        if not channels:
            return 0
        now = self._utc_now()
        persisted = 0
        with self._lock:
            for entry in channels:
                if not isinstance(entry, dict):
                    continue
                channel = str(entry.get("channel") or "").strip().lower()
                if not channel:
                    continue
                max_targets_raw = entry.get("max_targets")
                try:
                    max_targets = int(max_targets_raw)
                except Exception:
                    max_targets = 3
                max_targets = max(1, min(max_targets, 20))
                targets: list[str] = []
                raw_targets = entry.get("targets")
                if isinstance(raw_targets, list):
                    for item in raw_targets:
                        target = str(item or "").strip()
                        if not target or target in targets:
                            continue
                        targets.append(target)
                        if len(targets) >= 20:
                            break
                options_raw = entry.get("options")
                options = options_raw if isinstance(options_raw, dict) else {}
                self._conn.execute(
                    """
                    INSERT INTO news_delivery_policies(
                        user_id,
                        topic,
                        channel,
                        is_enabled,
                        max_targets,
                        targets_json,
                        options_json,
                        created_at,
                        updated_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, topic, channel) DO UPDATE SET
                        is_enabled=excluded.is_enabled,
                        max_targets=excluded.max_targets,
                        targets_json=excluded.targets_json,
                        options_json=excluded.options_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        normalized_user,
                        normalized_topic,
                        channel,
                        1 if bool(entry.get("enabled", True)) else 0,
                        max_targets,
                        json.dumps(targets, ensure_ascii=False),
                        json.dumps(options, ensure_ascii=False),
                        now,
                        now,
                    ),
                )
                persisted += 1
            self._commit_locked()
        return persisted

    def list_news_delivery_policies(
        self,
        *,
        user_id: str,
        topic: str | None = None,
        include_global: bool = True,
    ) -> list[dict[str, Any]]:
        normalized_user = str(user_id or "").strip()
        if not normalized_user:
            return []
        normalized_topic = str(topic or "").strip()
        query = "SELECT * FROM news_delivery_policies WHERE user_id = ?"
        params: list[Any] = [normalized_user]
        if normalized_topic:
            if include_global:
                query += " AND topic IN (?, '*')"
                params.append(normalized_topic)
                order_clause = " ORDER BY CASE WHEN topic = ? THEN 0 ELSE 1 END, channel ASC, updated_at DESC"
                params.append(normalized_topic)
            else:
                query += " AND topic = ?"
                params.append(normalized_topic)
                order_clause = " ORDER BY channel ASC, updated_at DESC"
        else:
            order_clause = " ORDER BY topic ASC, channel ASC, updated_at DESC"
        query += order_clause
        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()
        return [self._decode_news_delivery_policy_row(dict(row)) for row in rows]

    def add_news_delivery_events(
        self,
        *,
        user_id: str,
        topic: str,
        events: list[dict[str, Any]],
    ) -> int:
        normalized_user = str(user_id or "").strip()
        normalized_topic = str(topic or "").strip()
        if not normalized_user or not normalized_topic or not events:
            return 0
        delivered_at = self._utc_now()
        inserted = 0
        with self._lock:
            for event in events:
                if not isinstance(event, dict):
                    continue
                channel = str(event.get("channel") or "").strip().lower()
                target = str(event.get("target") or "").strip()
                status = str(event.get("status") or "").strip().lower() or "unknown"
                if not channel or not target:
                    continue
                detail = str(event.get("detail") or "").strip() or None
                digest_hash = str(event.get("digest_hash") or "").strip() or None
                metadata_raw = event.get("metadata")
                metadata = metadata_raw if isinstance(metadata_raw, dict) else {}
                self._conn.execute(
                    """
                    INSERT INTO news_delivery_events(
                        user_id,
                        topic,
                        channel,
                        target,
                        status,
                        detail,
                        digest_hash,
                        metadata_json,
                        delivered_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        normalized_user,
                        normalized_topic,
                        channel,
                        target,
                        status,
                        detail,
                        digest_hash,
                        json.dumps(metadata, ensure_ascii=False),
                        delivered_at,
                    ),
                )
                inserted += 1
            self._commit_locked()
        return inserted

    def list_news_delivery_events(
        self,
        *,
        user_id: str,
        topic: str | None = None,
        channel: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        normalized_user = str(user_id or "").strip()
        if not normalized_user or limit <= 0:
            return []
        query = "SELECT * FROM news_delivery_events WHERE user_id = ?"
        params: list[Any] = [normalized_user]
        if topic not in (None, ""):
            query += " AND topic = ?"
            params.append(str(topic).strip())
        if channel not in (None, ""):
            query += " AND channel = ?"
            params.append(str(channel).strip().lower())
        query += " ORDER BY delivered_at DESC LIMIT ?"
        params.append(max(1, int(limit)))
        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()
        return [self._decode_news_delivery_event_row(dict(row)) for row in rows]

    def upsert_secret_inventory_items(self, items: list[dict[str, Any]]) -> None:
        if not items:
            return
        now = self._utc_now()
        with self._lock:
            for item in items:
                key = str(item.get("secret_key") or "").strip()
                provider = str(item.get("provider") or "runtime").strip() or "runtime"
                if not key:
                    continue
                metadata_json = json.dumps(item.get("metadata") or {}, ensure_ascii=False)
                self._conn.execute(
                    """
                    INSERT INTO security_secret_inventory(
                        secret_key,
                        provider,
                        is_required,
                        source,
                        value_fingerprint,
                        value_present,
                        last_rotated_at,
                        rotation_period_days,
                        expires_at,
                        status,
                        metadata_json,
                        updated_at
                    )
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(secret_key) DO UPDATE SET
                        provider=excluded.provider,
                        is_required=excluded.is_required,
                        source=excluded.source,
                        value_fingerprint=excluded.value_fingerprint,
                        value_present=excluded.value_present,
                        last_rotated_at=excluded.last_rotated_at,
                        rotation_period_days=excluded.rotation_period_days,
                        expires_at=excluded.expires_at,
                        status=excluded.status,
                        metadata_json=excluded.metadata_json,
                        updated_at=excluded.updated_at
                    """,
                    (
                        key,
                        provider,
                        1 if bool(item.get("is_required", True)) else 0,
                        str(item.get("source") or "env").strip() or "env",
                        str(item.get("value_fingerprint") or "").strip() or None,
                        1 if bool(item.get("value_present", False)) else 0,
                        str(item.get("last_rotated_at") or "").strip() or None,
                        max(1, int(item.get("rotation_period_days", 90))),
                        str(item.get("expires_at") or "").strip() or None,
                        str(item.get("status") or "unknown").strip().lower() or "unknown",
                        metadata_json,
                        now,
                    ),
                )
            self._commit_locked()

    def list_secret_inventory(
        self,
        *,
        limit: int = 200,
        status: str | None = None,
        provider: str | None = None,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        query = "SELECT * FROM security_secret_inventory WHERE 1 = 1"
        params: list[Any] = []
        if status:
            query += " AND status = ?"
            params.append(str(status).strip().lower())
        if provider:
            query += " AND provider = ?"
            params.append(str(provider).strip())
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()
        return [self._decode_secret_inventory_row(dict(row)) for row in rows]

    def create_access_review(
        self,
        *,
        review_id: str,
        reviewer: str | None,
        snapshot: dict[str, Any],
        summary: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = self._utc_now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO security_access_reviews(
                    id,
                    reviewer,
                    status,
                    started_at,
                    completed_at,
                    summary,
                    snapshot_json,
                    decisions_json,
                    findings_json,
                    metadata_json,
                    updated_at
                )
                VALUES(?, ?, 'open', ?, NULL, ?, ?, '{}', '[]', ?, ?)
                """,
                (
                    review_id,
                    reviewer,
                    now,
                    summary,
                    json.dumps(snapshot or {}, ensure_ascii=False),
                    json.dumps(metadata or {}, ensure_ascii=False),
                    now,
                ),
            )
            self._commit_locked()

    def complete_access_review(
        self,
        *,
        review_id: str,
        reviewer: str | None,
        summary: str | None,
        decisions: dict[str, Any] | None,
        findings: list[dict[str, Any]] | None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        now = self._utc_now()
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE security_access_reviews
                SET
                    reviewer = COALESCE(?, reviewer),
                    status = 'completed',
                    completed_at = ?,
                    summary = ?,
                    decisions_json = ?,
                    findings_json = ?,
                    metadata_json = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    reviewer,
                    now,
                    summary,
                    json.dumps(decisions or {}, ensure_ascii=False),
                    json.dumps(findings or [], ensure_ascii=False),
                    json.dumps(metadata or {}, ensure_ascii=False),
                    now,
                    review_id,
                ),
            )
            self._commit_locked()
        return int(cursor.rowcount or 0) > 0

    def get_access_review(self, review_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM security_access_reviews WHERE id = ?",
                (review_id,),
            ).fetchone()
        if row is None:
            return None
        return self._decode_access_review_row(dict(row))

    def list_access_reviews(self, *, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        query = "SELECT * FROM security_access_reviews WHERE 1 = 1"
        params: list[Any] = []
        if status:
            query += " AND status = ?"
            params.append(str(status).strip().lower())
        query += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()
        return [self._decode_access_review_row(dict(row)) for row in rows]

    def create_security_incident(
        self,
        *,
        incident_id: str,
        category: str,
        severity: str,
        status: str,
        title: str,
        description: str,
        owner: str | None,
        request_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        now = self._utc_now()
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO security_incidents(
                    id,
                    category,
                    severity,
                    status,
                    title,
                    description,
                    owner,
                    opened_at,
                    acknowledged_at,
                    resolved_at,
                    impact,
                    containment,
                    root_cause,
                    recovery_actions,
                    request_id,
                    metadata_json,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?, ?)
                """,
                (
                    incident_id,
                    category,
                    severity,
                    status,
                    title,
                    description,
                    owner,
                    now,
                    request_id,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    now,
                ),
            )
            self._commit_locked()

    def update_security_incident_fields(self, incident_id: str, **fields: Any) -> bool:
        if not fields:
            return False
        allowed = {
            "status",
            "owner",
            "acknowledged_at",
            "resolved_at",
            "impact",
            "containment",
            "root_cause",
            "recovery_actions",
            "metadata_json",
            "updated_at",
        }
        sanitized: dict[str, Any] = {}
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "metadata_json" and isinstance(value, dict):
                sanitized[key] = json.dumps(value, ensure_ascii=False)
            elif key == "status":
                normalized = str(value or "").strip().lower()
                if normalized not in {"open", "acknowledged", "contained", "resolved", "closed"}:
                    continue
                sanitized[key] = normalized
            else:
                sanitized[key] = value
        if not sanitized:
            return False
        if "updated_at" not in sanitized:
            sanitized["updated_at"] = self._utc_now()
        assignments = ", ".join(f"{column} = ?" for column in sanitized.keys())
        values = list(sanitized.values()) + [incident_id]
        with self._lock:
            cursor = self._conn.execute(
                f"UPDATE security_incidents SET {assignments} WHERE id = ?",
                values,
            )
            self._commit_locked()
        return int(cursor.rowcount or 0) > 0

    def get_security_incident(self, incident_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM security_incidents WHERE id = ?",
                (incident_id,),
            ).fetchone()
        if row is None:
            return None
        return self._decode_security_incident_row(dict(row))

    def list_security_incidents(
        self,
        *,
        limit: int = 200,
        status: str | None = None,
        severity: str | None = None,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        query = "SELECT * FROM security_incidents WHERE 1 = 1"
        params: list[Any] = []
        if status:
            query += " AND status = ?"
            params.append(str(status).strip().lower())
        if severity:
            query += " AND severity = ?"
            params.append(str(severity).strip().lower())
        if category:
            query += " AND category = ?"
            params.append(str(category).strip().lower())
        query += " ORDER BY opened_at DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()
        return [self._decode_security_incident_row(dict(row)) for row in rows]

    def add_security_incident_event(
        self,
        *,
        incident_id: str,
        event_type: str,
        actor: str | None,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> int:
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO security_incident_events(
                    incident_id,
                    event_type,
                    actor,
                    message,
                    details_json,
                    created_at
                )
                VALUES(?, ?, ?, ?, ?, ?)
                """,
                (
                    incident_id,
                    str(event_type or "").strip().lower() or "note",
                    actor,
                    message,
                    json.dumps(details or {}, ensure_ascii=False),
                    self._utc_now(),
                ),
            )
            self._commit_locked()
            return int(cursor.lastrowid)

    def list_security_incident_events(
        self,
        *,
        incident_id: str,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, incident_id, event_type, actor, message, details_json, created_at
                FROM security_incident_events
                WHERE incident_id = ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (incident_id, limit),
            ).fetchall()
        return [self._decode_security_incident_event_row(dict(row)) for row in rows]

    def upsert_supervisor_graph(
        self,
        *,
        graph_id: str,
        user_id: str,
        status: str,
        objective: str,
        graph: dict[str, Any],
        created_at: str,
        updated_at: str,
        launched_at: str | None = None,
        finished_at: str | None = None,
        checkpoint_count: int = 0,
    ) -> None:
        normalized_graph_id = str(graph_id or "").strip()
        if not normalized_graph_id:
            raise ValueError("graph_id is required")
        normalized_user_id = str(user_id or "").strip()
        if not normalized_user_id:
            raise ValueError("user_id is required")
        normalized_objective = str(objective or "").strip()
        if not normalized_objective:
            raise ValueError("objective is required")
        normalized_status = str(status or "").strip().lower() or "planned"
        payload = graph if isinstance(graph, dict) else {}
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO supervisor_graphs(
                    id,
                    user_id,
                    status,
                    objective,
                    graph_json,
                    checkpoint_count,
                    created_at,
                    updated_at,
                    launched_at,
                    finished_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    user_id=excluded.user_id,
                    status=excluded.status,
                    objective=excluded.objective,
                    graph_json=excluded.graph_json,
                    checkpoint_count=excluded.checkpoint_count,
                    updated_at=excluded.updated_at,
                    launched_at=excluded.launched_at,
                    finished_at=excluded.finished_at
                """,
                (
                    normalized_graph_id,
                    normalized_user_id,
                    normalized_status,
                    normalized_objective,
                    json.dumps(payload, ensure_ascii=False),
                    max(0, int(checkpoint_count)),
                    str(created_at or self._utc_now()),
                    str(updated_at or self._utc_now()),
                    launched_at,
                    finished_at,
                ),
            )
            self._commit_locked()

    def get_supervisor_graph(self, graph_id: str) -> dict[str, Any] | None:
        normalized_graph_id = str(graph_id or "").strip()
        if not normalized_graph_id:
            return None
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM supervisor_graphs WHERE id = ?",
                (normalized_graph_id,),
            ).fetchone()
        if row is None:
            return None
        return self._decode_supervisor_graph_row(dict(row))

    def list_supervisor_graphs(
        self,
        *,
        user_id: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []

        query = "SELECT * FROM supervisor_graphs WHERE 1 = 1"
        params: list[Any] = []
        if user_id:
            query += " AND user_id = ?"
            params.append(str(user_id).strip())
        if status:
            query += " AND status = ?"
            params.append(str(status).strip().lower())
        query += " ORDER BY updated_at DESC LIMIT ?"
        params.append(max(1, int(limit)))

        with self._lock:
            rows = self._conn.execute(query, tuple(params)).fetchall()
        return [self._decode_supervisor_graph_row(dict(row)) for row in rows]

    def delete_supervisor_graph(self, graph_id: str) -> bool:
        normalized_graph_id = str(graph_id or "").strip()
        if not normalized_graph_id:
            return False
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM supervisor_graphs WHERE id = ?",
                (normalized_graph_id,),
            )
            self._commit_locked()
        return int(cursor.rowcount or 0) > 0

    def create_agent_run(
        self,
        run_id: str,
        agent_id: str,
        user_id: str,
        session_id: str | None,
        input_message: str,
        status: str = "queued",
        max_attempts: int = 2,
        budget: dict[str, Any] | None = None,
    ) -> None:
        now = self._utc_now()
        budget_json = json.dumps(budget or {}, ensure_ascii=False)
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
                    stop_reason,
                    failure_class,
                    lease_owner,
                    lease_token,
                    lease_expires_at,
                    checkpoints_json,
                    budget_json,
                    metrics_json,
                    created_at,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, 0, ?, 0, NULL, NULL, NULL, NULL, NULL, '[]', ?, '{}', ?, ?)
                """,
                (
                    run_id,
                    agent_id,
                    user_id,
                    session_id,
                    input_message,
                    status,
                    max_attempts,
                    budget_json,
                    now,
                    now,
                ),
            )
            self._commit_locked()

    def update_agent_run_fields(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return

        allowed = {
            "status",
            "attempts",
            "max_attempts",
            "cancel_requested",
            "stop_reason",
            "failure_class",
            "lease_owner",
            "lease_token",
            "lease_expires_at",
            "result_json",
            "error_message",
            "checkpoints_json",
            "budget_json",
            "metrics_json",
            "started_at",
            "finished_at",
            "updated_at",
        }
        sanitized: dict[str, Any] = {}
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key in {"result_json", "checkpoints_json", "budget_json", "metrics_json"} and isinstance(
                value, (dict, list)
            ):
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
            self._commit_locked()

    def append_agent_run_checkpoint(
        self,
        run_id: str,
        checkpoint: dict[str, Any],
        *,
        issue_update: dict[str, Any] | None = None,
        issue_artifact: dict[str, Any] | None = None,
        tool_call_record: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            now = self._utc_now()
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

            payload = dict(checkpoint)
            payload.setdefault("timestamp", now)
            checkpoints.append(payload)
            self._conn.execute(
                """
                UPDATE agent_runs
                SET checkpoints_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (json.dumps(checkpoints, ensure_ascii=False), now, run_id),
            )

            if isinstance(issue_update, dict):
                self._upsert_agent_run_issue_no_commit(run_id=run_id, now=now, **issue_update)
            if isinstance(issue_artifact, dict):
                self._upsert_agent_run_issue_artifact_no_commit(run_id=run_id, now=now, **issue_artifact)
            if isinstance(tool_call_record, dict):
                self._upsert_agent_run_tool_call_no_commit(run_id=run_id, now=now, **tool_call_record)
            self._commit_locked()

    def get_agent_run(
        self,
        run_id: str,
        include_issues: bool = False,
        include_artifacts: bool = False,
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM agent_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
        if not row:
            return None
        decoded = self._decode_agent_run_row(dict(row))
        if include_issues:
            decoded["issues"] = self.list_agent_run_issues(run_id=run_id, limit=500)
        if include_artifacts:
            decoded["issue_artifacts"] = self.list_agent_run_issue_artifacts(run_id=run_id, limit=2000)
        return decoded

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

    def claim_agent_run_lease(
        self,
        *,
        run_id: str,
        lease_owner: str,
        lease_token: str,
        lease_expires_at: str,
        now_iso: str | None = None,
        allowed_statuses: tuple[str, ...] = ("queued", "running"),
    ) -> dict[str, Any] | None:
        normalized_run_id = str(run_id or "").strip()
        normalized_owner = str(lease_owner or "").strip()
        normalized_token = str(lease_token or "").strip()
        normalized_statuses = tuple(
            str(item or "").strip().lower()
            for item in allowed_statuses
            if str(item or "").strip()
        )
        if not normalized_run_id or not normalized_owner or not normalized_token or not normalized_statuses:
            return None
        now = str(now_iso or self._utc_now())
        placeholders = ", ".join(["?"] * len(normalized_statuses))
        with self._lock:
            cursor = self._conn.execute(
                f"""
                UPDATE agent_runs
                SET lease_owner = ?, lease_token = ?, lease_expires_at = ?, updated_at = ?
                WHERE
                    id = ?
                    AND status IN ({placeholders})
                    AND (
                        lease_expires_at IS NULL
                        OR lease_expires_at <= ?
                        OR (lease_owner = ? AND lease_token = ?)
                    )
                """,
                (
                    normalized_owner,
                    normalized_token,
                    lease_expires_at,
                    self._utc_now(),
                    normalized_run_id,
                    *normalized_statuses,
                    now,
                    normalized_owner,
                    normalized_token,
                ),
            )
            if int(cursor.rowcount or 0) <= 0:
                self._commit_locked()
                return None
            row = self._conn.execute(
                "SELECT * FROM agent_runs WHERE id = ?",
                (normalized_run_id,),
            ).fetchone()
            self._commit_locked()
        if row is None:
            return None
        return self._decode_agent_run_row(dict(row))

    def release_agent_run_lease(
        self,
        *,
        run_id: str,
        lease_owner: str,
        lease_token: str,
    ) -> bool:
        normalized_run_id = str(run_id or "").strip()
        normalized_owner = str(lease_owner or "").strip()
        normalized_token = str(lease_token or "").strip()
        if not normalized_run_id or not normalized_owner or not normalized_token:
            return False
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE agent_runs
                SET lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE id = ? AND lease_owner = ? AND lease_token = ?
                """,
                (self._utc_now(), normalized_run_id, normalized_owner, normalized_token),
            )
            self._commit_locked()
        return int(cursor.rowcount or 0) > 0

    def refresh_agent_run_lease(
        self,
        *,
        run_id: str,
        lease_owner: str,
        lease_token: str,
        lease_expires_at: str,
        now_iso: str | None = None,
        allowed_statuses: tuple[str, ...] = ("running",),
    ) -> bool:
        normalized_run_id = str(run_id or "").strip()
        normalized_owner = str(lease_owner or "").strip()
        normalized_token = str(lease_token or "").strip()
        normalized_statuses = tuple(
            str(item or "").strip().lower()
            for item in allowed_statuses
            if str(item or "").strip()
        )
        if not normalized_run_id or not normalized_owner or not normalized_token or not normalized_statuses:
            return False
        now = str(now_iso or self._utc_now())
        placeholders = ", ".join(["?"] * len(normalized_statuses))
        with self._lock:
            cursor = self._conn.execute(
                f"""
                UPDATE agent_runs
                SET lease_expires_at = ?, updated_at = ?
                WHERE
                    id = ?
                    AND lease_owner = ?
                    AND lease_token = ?
                    AND status IN ({placeholders})
                    AND (
                        lease_expires_at IS NULL
                        OR lease_expires_at >= ?
                    )
                """,
                (
                    lease_expires_at,
                    self._utc_now(),
                    normalized_run_id,
                    normalized_owner,
                    normalized_token,
                    *normalized_statuses,
                    now,
                ),
            )
            self._commit_locked()
        return int(cursor.rowcount or 0) > 0

    def upsert_agent_run_issue(
        self,
        *,
        run_id: str,
        issue_id: str,
        issue_order: int,
        title: str,
        status: str,
        depends_on: list[str] | None = None,
        attempt_count: int = 0,
        last_error: str | None = None,
        payload: dict[str, Any] | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
    ) -> None:
        with self._lock:
            self._upsert_agent_run_issue_no_commit(
                run_id=run_id,
                issue_id=issue_id,
                issue_order=issue_order,
                title=title,
                status=status,
                depends_on=depends_on,
                attempt_count=attempt_count,
                last_error=last_error,
                payload=payload,
                started_at=started_at,
                finished_at=finished_at,
                now=self._utc_now(),
            )
            self._commit_locked()

    def _upsert_agent_run_issue_no_commit(
        self,
        *,
        run_id: str,
        issue_id: str,
        issue_order: int,
        title: str,
        status: str,
        depends_on: list[str] | None = None,
        attempt_count: int = 0,
        last_error: str | None = None,
        payload: dict[str, Any] | None = None,
        started_at: str | None = None,
        finished_at: str | None = None,
        now: str | None = None,
    ) -> None:
        timestamp = now or self._utc_now()
        normalized_issue_order = max(0, int(issue_order))
        normalized_attempt_count = max(0, int(attempt_count))
        normalized_status = str(status or "planned").strip().lower() or "planned"
        if normalized_status not in {"planned", "running", "blocked", "done", "failed"}:
            normalized_status = "planned"
        normalized_title = str(title or issue_id).strip() or issue_id
        depends_on_json = json.dumps(depends_on or [], ensure_ascii=False)
        payload_json = json.dumps(payload or {}, ensure_ascii=False)
        self._conn.execute(
            """
            INSERT INTO agent_run_issues(
                run_id,
                issue_id,
                issue_order,
                title,
                status,
                depends_on_json,
                attempt_count,
                last_error,
                payload_json,
                created_at,
                updated_at,
                started_at,
                finished_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, issue_id) DO UPDATE SET
                issue_order=excluded.issue_order,
                title=excluded.title,
                status=excluded.status,
                depends_on_json=excluded.depends_on_json,
                attempt_count=excluded.attempt_count,
                last_error=excluded.last_error,
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at,
                started_at=excluded.started_at,
                finished_at=excluded.finished_at
            """,
            (
                run_id,
                issue_id,
                normalized_issue_order,
                normalized_title,
                normalized_status,
                depends_on_json,
                normalized_attempt_count,
                last_error,
                payload_json,
                timestamp,
                timestamp,
                started_at,
                finished_at,
            ),
        )

    def get_agent_run_issue(self, *, run_id: str, issue_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    run_id,
                    issue_id,
                    issue_order,
                    title,
                    status,
                    depends_on_json,
                    attempt_count,
                    last_error,
                    payload_json,
                    created_at,
                    updated_at,
                    started_at,
                    finished_at
                FROM agent_run_issues
                WHERE run_id = ? AND issue_id = ?
                """,
                (run_id, issue_id),
            ).fetchone()
        if row is None:
            return None
        return self._decode_agent_run_issue_row(dict(row))

    def list_agent_run_issues(self, *, run_id: str, limit: int = 200) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT
                    run_id,
                    issue_id,
                    issue_order,
                    title,
                    status,
                    depends_on_json,
                    attempt_count,
                    last_error,
                    payload_json,
                    created_at,
                    updated_at,
                    started_at,
                    finished_at
                FROM agent_run_issues
                WHERE run_id = ?
                ORDER BY issue_order ASC, updated_at ASC
                LIMIT ?
                """,
                (run_id, limit),
            ).fetchall()
        return [self._decode_agent_run_issue_row(dict(row)) for row in rows]

    def upsert_agent_run_issue_artifact(
        self,
        *,
        run_id: str,
        issue_id: str,
        artifact_key: str,
        artifact: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            self._upsert_agent_run_issue_artifact_no_commit(
                run_id=run_id,
                issue_id=issue_id,
                artifact_key=artifact_key,
                artifact=artifact,
                now=self._utc_now(),
            )
            self._commit_locked()

    def _upsert_agent_run_issue_artifact_no_commit(
        self,
        *,
        run_id: str,
        issue_id: str,
        artifact_key: str,
        artifact: dict[str, Any] | None = None,
        now: str | None = None,
    ) -> None:
        timestamp = now or self._utc_now()
        normalized_issue_id = str(issue_id or "").strip() or "unknown"
        normalized_key = str(artifact_key or "").strip() or "result"
        artifact_json = json.dumps(artifact or {}, ensure_ascii=False)
        self._conn.execute(
            """
            INSERT INTO agent_run_issue_artifacts(
                run_id,
                issue_id,
                artifact_key,
                artifact_json,
                created_at,
                updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, issue_id, artifact_key) DO UPDATE SET
                artifact_json=excluded.artifact_json,
                updated_at=excluded.updated_at
            """,
            (
                run_id,
                normalized_issue_id,
                normalized_key,
                artifact_json,
                timestamp,
                timestamp,
            ),
        )

    def list_agent_run_issue_artifacts(
        self,
        *,
        run_id: str,
        issue_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        with self._lock:
            if issue_id is not None:
                rows = self._conn.execute(
                    """
                    SELECT
                        run_id,
                        issue_id,
                        artifact_key,
                        artifact_json,
                        created_at,
                        updated_at
                    FROM agent_run_issue_artifacts
                    WHERE run_id = ? AND issue_id = ?
                    ORDER BY updated_at ASC
                    LIMIT ?
                    """,
                    (run_id, issue_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT
                        run_id,
                        issue_id,
                        artifact_key,
                        artifact_json,
                        created_at,
                        updated_at
                    FROM agent_run_issue_artifacts
                    WHERE run_id = ?
                    ORDER BY updated_at ASC
                    LIMIT ?
                    """,
                    (run_id, limit),
                ).fetchall()
        return [self._decode_agent_run_issue_artifact_row(dict(row)) for row in rows]

    def upsert_agent_run_tool_call(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        status: str,
        result: dict[str, Any] | None = None,
        error_message: str | None = None,
        attempt: int = 0,
    ) -> None:
        with self._lock:
            self._upsert_agent_run_tool_call_no_commit(
                run_id=run_id,
                idempotency_key=idempotency_key,
                tool_name=tool_name,
                arguments=arguments,
                status=status,
                result=result,
                error_message=error_message,
                attempt=attempt,
                now=self._utc_now(),
            )
            self._commit_locked()

    def _upsert_agent_run_tool_call_no_commit(
        self,
        *,
        run_id: str,
        idempotency_key: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        status: str,
        result: dict[str, Any] | None = None,
        error_message: str | None = None,
        attempt: int = 0,
        now: str | None = None,
    ) -> None:
        timestamp = now or self._utc_now()
        normalized_key = str(idempotency_key or "").strip()
        if not normalized_key:
            return
        normalized_tool_name = str(tool_name or "").strip() or "unknown"
        normalized_status = str(status or "unknown").strip().lower() or "unknown"
        arguments_json = json.dumps(arguments or {}, ensure_ascii=False)
        result_json = json.dumps(result, ensure_ascii=False) if isinstance(result, dict) else None
        normalized_attempt = max(0, int(attempt))
        existing = self._conn.execute(
            """
            SELECT status FROM agent_run_tool_calls
            WHERE run_id = ? AND idempotency_key = ?
            """,
            (run_id, normalized_key),
        ).fetchone()
        if existing is not None:
            existing_status = str(existing["status"] or "").strip().lower()
            if existing_status == "succeeded" and normalized_status != "succeeded":
                return
        self._conn.execute(
            """
            INSERT INTO agent_run_tool_calls(
                run_id,
                idempotency_key,
                tool_name,
                arguments_json,
                status,
                result_json,
                error_message,
                attempt,
                created_at,
                updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(run_id, idempotency_key) DO UPDATE SET
                tool_name=excluded.tool_name,
                arguments_json=excluded.arguments_json,
                status=excluded.status,
                result_json=excluded.result_json,
                error_message=excluded.error_message,
                attempt=excluded.attempt,
                updated_at=excluded.updated_at
            """,
            (
                run_id,
                normalized_key,
                normalized_tool_name,
                arguments_json,
                normalized_status,
                result_json,
                error_message,
                normalized_attempt,
                timestamp,
                timestamp,
            ),
        )

    def get_agent_run_tool_call(
        self,
        *,
        run_id: str,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT
                    run_id,
                    idempotency_key,
                    tool_name,
                    arguments_json,
                    status,
                    result_json,
                    error_message,
                    attempt,
                    created_at,
                    updated_at
                FROM agent_run_tool_calls
                WHERE run_id = ? AND idempotency_key = ?
                """,
                (run_id, idempotency_key),
            ).fetchone()
        if row is None:
            return None
        return self._decode_agent_run_tool_call_row(dict(row))

    def list_agent_run_tool_calls(
        self,
        *,
        run_id: str,
        status: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        with self._lock:
            if status is not None:
                rows = self._conn.execute(
                    """
                    SELECT
                        run_id,
                        idempotency_key,
                        tool_name,
                        arguments_json,
                        status,
                        result_json,
                        error_message,
                        attempt,
                        created_at,
                        updated_at
                    FROM agent_run_tool_calls
                    WHERE run_id = ? AND status = ?
                    ORDER BY updated_at ASC
                    LIMIT ?
                    """,
                    (run_id, str(status).strip().lower(), limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    """
                    SELECT
                        run_id,
                        idempotency_key,
                        tool_name,
                        arguments_json,
                        status,
                        result_json,
                        error_message,
                        attempt,
                        created_at,
                        updated_at
                    FROM agent_run_tool_calls
                    WHERE run_id = ?
                    ORDER BY updated_at ASC
                    LIMIT ?
                    """,
                    (run_id, limit),
                ).fetchall()
        return [self._decode_agent_run_tool_call_row(dict(row)) for row in rows]

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
        mission_policy: dict[str, Any] | None = None,
    ) -> None:
        now = self._utc_now()
        schedule_json = json.dumps(schedule, ensure_ascii=False)
        mission_policy_json = json.dumps(mission_policy or {}, ensure_ascii=False)
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
                    mission_policy_json,
                    timezone,
                    is_enabled,
                    next_run_at,
                    last_run_at,
                    last_error,
                    lease_owner,
                    lease_expires_at,
                    backoff_until,
                    circuit_open_until,
                    last_dispatch_key,
                    created_at,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, NULL, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?)
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
                    mission_policy_json,
                    timezone_name,
                    next_run_at,
                    now,
                    now,
                ),
            )
            self._commit_locked()

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

    def claim_due_automations(
        self,
        *,
        now_iso: str,
        limit: int,
        lease_owner: str,
        lease_expires_at: str,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        claimed: list[dict[str, Any]] = []
        with self._lock:
            candidate_rows = self._conn.execute(
                """
                SELECT id FROM automations
                WHERE
                    is_enabled = 1
                    AND next_run_at <= ?
                    AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
                    AND (backoff_until IS NULL OR backoff_until <= ?)
                    AND (circuit_open_until IS NULL OR circuit_open_until <= ?)
                ORDER BY next_run_at ASC
                LIMIT ?
                """,
                (now_iso, now_iso, now_iso, now_iso, limit),
            ).fetchall()
            for row in candidate_rows:
                automation_id = str(row["id"])
                cursor = self._conn.execute(
                    """
                    UPDATE automations
                    SET lease_owner = ?, lease_expires_at = ?, updated_at = ?
                    WHERE
                        id = ?
                        AND is_enabled = 1
                        AND next_run_at <= ?
                        AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
                        AND (backoff_until IS NULL OR backoff_until <= ?)
                        AND (circuit_open_until IS NULL OR circuit_open_until <= ?)
                    """,
                    (
                        lease_owner,
                        lease_expires_at,
                        self._utc_now(),
                        automation_id,
                        now_iso,
                        now_iso,
                        now_iso,
                        now_iso,
                    ),
                )
                if int(cursor.rowcount or 0) <= 0:
                    continue
                claimed_row = self._conn.execute(
                    "SELECT * FROM automations WHERE id = ?",
                    (automation_id,),
                ).fetchone()
                if claimed_row is not None:
                    claimed.append(self._decode_automation_row(dict(claimed_row)))
            self._commit_locked()
        return claimed

    def release_automation_lease(
        self,
        *,
        automation_id: str,
        lease_owner: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE automations
                SET lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
                WHERE id = ? AND (lease_owner = ? OR lease_owner IS NULL)
                """,
                (self._utc_now(), automation_id, lease_owner),
            )
            self._commit_locked()

    def register_automation_dispatch(
        self,
        *,
        automation_id: str,
        dispatch_key: str,
        source: str,
        run_id: str | None = None,
        stale_before_iso: str | None = None,
    ) -> bool:
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO automation_dispatches(
                        automation_id,
                        dispatch_key,
                        source,
                        run_id,
                        created_at
                    )
                    VALUES(?, ?, ?, ?, ?)
                    """,
                    (automation_id, dispatch_key, source, run_id, self._utc_now()),
                )
            except sqlite3.IntegrityError:
                if stale_before_iso is None:
                    self._commit_locked()
                    return False
                existing = self._conn.execute(
                    """
                    SELECT run_id, created_at
                    FROM automation_dispatches
                    WHERE automation_id = ? AND dispatch_key = ?
                    """,
                    (automation_id, dispatch_key),
                ).fetchone()
                if existing is None:
                    self._commit_locked()
                    return False
                existing_run_id = existing["run_id"]
                existing_created_at_raw = existing["created_at"]
                if existing_run_id:
                    self._commit_locked()
                    return False
                try:
                    existing_created_at = datetime.fromisoformat(str(existing_created_at_raw))
                    stale_before = datetime.fromisoformat(str(stale_before_iso))
                    if existing_created_at.tzinfo is None:
                        existing_created_at = existing_created_at.replace(tzinfo=timezone.utc)
                    if stale_before.tzinfo is None:
                        stale_before = stale_before.replace(tzinfo=timezone.utc)
                    if existing_created_at > stale_before:
                        self._commit_locked()
                        return False
                except Exception:
                    self._commit_locked()
                    return False

                cursor = self._conn.execute(
                    """
                    UPDATE automation_dispatches
                    SET source = ?, run_id = ?, created_at = ?
                    WHERE automation_id = ? AND dispatch_key = ?
                    """,
                    (source, run_id, self._utc_now(), automation_id, dispatch_key),
                )
                self._commit_locked()
                return int(cursor.rowcount or 0) > 0
            self._commit_locked()
        return True

    def update_automation_dispatch_run_id(
        self,
        *,
        automation_id: str,
        dispatch_key: str,
        run_id: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE automation_dispatches
                SET run_id = ?, created_at = ?
                WHERE automation_id = ? AND dispatch_key = ?
                """,
                (run_id, self._utc_now(), automation_id, dispatch_key),
            )
            self._commit_locked()

    def delete_automation_dispatch(
        self,
        *,
        automation_id: str,
        dispatch_key: str,
    ) -> None:
        with self._lock:
            self._conn.execute(
                """
                DELETE FROM automation_dispatches
                WHERE automation_id = ? AND dispatch_key = ?
                """,
                (automation_id, dispatch_key),
            )
            self._commit_locked()

    def list_recent_automation_events(self, limit: int = 500) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, automation_id, event_type, message, run_id, created_at
                FROM automation_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        result = [dict(row) for row in rows]
        result.reverse()
        return result

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
            "mission_policy_json",
            "timezone",
            "is_enabled",
            "next_run_at",
            "last_run_at",
            "last_error",
            "consecutive_failures",
            "escalation_level",
            "lease_owner",
            "lease_expires_at",
            "backoff_until",
            "circuit_open_until",
            "last_dispatch_key",
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
            elif key in {"schedule_json", "mission_policy_json"} and isinstance(value, (dict, list)):
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
            self._commit_locked()

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
            self._conn.execute(
                "DELETE FROM automation_dispatches WHERE automation_id = ?",
                (automation_id,),
            )
            self._commit_locked()
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
            self._commit_locked()
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
            self._commit_locked()

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
            self._commit_locked()
        if int(cursor.rowcount or 0) <= 0:
            return None
        return self.get_inbox_item(item_id)

    @staticmethod
    def _decode_supervisor_graph_row(row: dict[str, Any]) -> dict[str, Any]:
        graph_json = row.pop("graph_json", "{}")
        try:
            parsed = json.loads(graph_json or "{}")
            graph = parsed if isinstance(parsed, dict) else {}
        except Exception:
            graph = {}

        graph_id = str(row.get("id") or graph.get("id") or "").strip()
        graph["id"] = graph_id
        graph["user_id"] = str(row.get("user_id") or graph.get("user_id") or "").strip()
        graph["objective"] = str(row.get("objective") or graph.get("objective") or "").strip()
        graph["status"] = str(row.get("status") or graph.get("status") or "planned").strip().lower() or "planned"
        graph["created_at"] = str(row.get("created_at") or graph.get("created_at") or "")
        graph["updated_at"] = str(row.get("updated_at") or graph.get("updated_at") or "")
        graph["launched_at"] = (
            str(row.get("launched_at"))
            if row.get("launched_at") not in (None, "")
            else graph.get("launched_at")
        )
        graph["finished_at"] = (
            str(row.get("finished_at"))
            if row.get("finished_at") not in (None, "")
            else graph.get("finished_at")
        )
        try:
            graph["checkpoint_count"] = max(0, int(row.get("checkpoint_count", 0)))
        except Exception:
            graph["checkpoint_count"] = 0
        return graph

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
        try:
            budget = json.loads(row.get("budget_json") or "{}")
            row["budget"] = budget if isinstance(budget, dict) else {}
        except Exception:
            row["budget"] = {}
        try:
            metrics = json.loads(row.get("metrics_json") or "{}")
            row["metrics"] = metrics if isinstance(metrics, dict) else {}
        except Exception:
            row["metrics"] = {}
        for key in ("lease_owner", "lease_token", "lease_expires_at"):
            value = row.get(key)
            row[key] = str(value) if value not in (None, "") else None
        row.pop("result_json", None)
        row.pop("checkpoints_json", None)
        row.pop("budget_json", None)
        row.pop("metrics_json", None)
        return row

    @staticmethod
    def _decode_agent_run_issue_row(row: dict[str, Any]) -> dict[str, Any]:
        depends_on_json = row.pop("depends_on_json", "[]")
        payload_json = row.pop("payload_json", "{}")
        try:
            depends_on = json.loads(depends_on_json or "[]")
            row["depends_on"] = [str(item) for item in depends_on] if isinstance(depends_on, list) else []
        except Exception:
            row["depends_on"] = []
        try:
            payload = json.loads(payload_json or "{}")
            row["payload"] = payload if isinstance(payload, dict) else {}
        except Exception:
            row["payload"] = {}
        try:
            row["issue_order"] = max(0, int(row.get("issue_order", 0)))
        except Exception:
            row["issue_order"] = 0
        try:
            row["attempt_count"] = max(0, int(row.get("attempt_count", 0)))
        except Exception:
            row["attempt_count"] = 0
        status = str(row.get("status") or "planned").strip().lower() or "planned"
        if status not in {"planned", "running", "blocked", "done", "failed"}:
            status = "planned"
        row["status"] = status
        return row

    @staticmethod
    def _decode_agent_run_issue_artifact_row(row: dict[str, Any]) -> dict[str, Any]:
        artifact_json = row.pop("artifact_json", "{}")
        try:
            parsed = json.loads(artifact_json or "{}")
            row["artifact"] = parsed if isinstance(parsed, dict) else {}
        except Exception:
            row["artifact"] = {}
        row["issue_id"] = str(row.get("issue_id") or "unknown").strip() or "unknown"
        row["artifact_key"] = str(row.get("artifact_key") or "result").strip() or "result"
        return row

    @staticmethod
    def _decode_agent_run_tool_call_row(row: dict[str, Any]) -> dict[str, Any]:
        arguments_json = row.pop("arguments_json", "{}")
        result_json = row.pop("result_json", None)
        try:
            parsed_args = json.loads(arguments_json or "{}")
            row["arguments"] = parsed_args if isinstance(parsed_args, dict) else {}
        except Exception:
            row["arguments"] = {}
        if isinstance(result_json, str) and result_json.strip():
            try:
                parsed_result = json.loads(result_json)
                row["result"] = parsed_result if isinstance(parsed_result, dict) else None
            except Exception:
                row["result"] = None
        else:
            row["result"] = None
        row["idempotency_key"] = str(row.get("idempotency_key") or "").strip()
        row["tool_name"] = str(row.get("tool_name") or "unknown").strip() or "unknown"
        row["status"] = str(row.get("status") or "unknown").strip().lower() or "unknown"
        try:
            row["attempt"] = max(0, int(row.get("attempt", 0)))
        except Exception:
            row["attempt"] = 0
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
        mission_policy_json = row.pop("mission_policy_json", "{}")
        try:
            parsed_policy = json.loads(mission_policy_json or "{}")
            row["mission_policy"] = parsed_policy if isinstance(parsed_policy, dict) else {}
        except Exception:
            row["mission_policy"] = {}
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
        for key in ("lease_owner", "lease_expires_at", "backoff_until", "circuit_open_until", "last_dispatch_key"):
            value = row.get(key)
            row[key] = str(value) if value not in (None, "") else None
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
    def _decode_provider_session_row(row: dict[str, Any]) -> dict[str, Any]:
        scopes_json = row.pop("scopes_json", "[]")
        metadata_json = row.pop("metadata_json", "{}")
        try:
            parsed_scopes = json.loads(scopes_json or "[]")
            row["scopes"] = (
                [str(item).strip().lower() for item in parsed_scopes if str(item).strip()]
                if isinstance(parsed_scopes, list)
                else []
            )
        except Exception:
            row["scopes"] = []
        try:
            parsed_meta = json.loads(metadata_json or "{}")
            row["metadata"] = parsed_meta if isinstance(parsed_meta, dict) else {}
        except Exception:
            row["metadata"] = {}
        row["provider"] = str(row.get("provider") or "").strip().lower()
        row["status"] = str(row.get("status") or "active").strip().lower() or "active"
        row["session_type"] = str(row.get("session_type") or "reference").strip().lower() or "reference"
        credential_ref = str(row.get("credential_ref") or "").strip()
        if credential_ref:
            if len(credential_ref) <= 8:
                row["credential_ref_hint"] = "***"
            else:
                row["credential_ref_hint"] = f"{credential_ref[:6]}...{credential_ref[-4:]}"
        else:
            row["credential_ref_hint"] = None
        row.pop("credential_ref", None)
        for key in (
            "display_name",
            "credential_fingerprint",
            "expires_at",
            "revoked_at",
            "revoked_reason",
            "last_used_at",
            "created_at",
            "updated_at",
        ):
            value = row.get(key)
            row[key] = str(value) if value not in (None, "") else None
        return row

    @staticmethod
    def _decode_news_item_row(row: dict[str, Any]) -> dict[str, Any]:
        metadata_json = row.pop("metadata_json", "{}")
        try:
            parsed = json.loads(metadata_json or "{}")
            row["metadata"] = parsed if isinstance(parsed, dict) else {}
        except Exception:
            row["metadata"] = {}
        row["user_id"] = str(row.get("user_id") or "").strip()
        row["topic"] = str(row.get("topic") or "").strip()
        row["source"] = str(row.get("source") or "").strip().lower()
        row["canonical_id"] = str(row.get("canonical_id") or "").strip()
        row["url"] = str(row.get("url") or "").strip()
        canonical_story_key = str(row.get("canonical_story_key") or "").strip()
        if not canonical_story_key:
            canonical_story_key = str(row.get("metadata", {}).get("canonical_story_key") or "").strip()
        row["canonical_story_key"] = canonical_story_key or None
        row["title"] = str(row.get("title") or "").strip()
        for key in ("excerpt", "author", "published_at", "ingested_at"):
            value = row.get(key)
            row[key] = str(value) if value not in (None, "") else None
        if row.get("raw_score") is not None:
            try:
                row["raw_score"] = float(row.get("raw_score"))
            except Exception:
                row["raw_score"] = None
        else:
            row["raw_score"] = None
        return row

    @staticmethod
    def _decode_news_delivery_policy_row(row: dict[str, Any]) -> dict[str, Any]:
        row["topic"] = str(row.get("topic") or "*").strip() or "*"
        row["channel"] = str(row.get("channel") or "").strip().lower()
        row["is_enabled"] = bool(int(row.get("is_enabled", 0)))
        try:
            row["max_targets"] = max(1, int(row.get("max_targets", 3)))
        except Exception:
            row["max_targets"] = 3
        targets_json = row.pop("targets_json", "[]")
        options_json = row.pop("options_json", "{}")
        try:
            parsed_targets = json.loads(targets_json or "[]")
            row["targets"] = [str(item).strip() for item in parsed_targets if str(item).strip()] if isinstance(parsed_targets, list) else []
        except Exception:
            row["targets"] = []
        try:
            parsed_options = json.loads(options_json or "{}")
            row["options"] = parsed_options if isinstance(parsed_options, dict) else {}
        except Exception:
            row["options"] = {}
        return row

    @staticmethod
    def _decode_news_delivery_event_row(row: dict[str, Any]) -> dict[str, Any]:
        row["channel"] = str(row.get("channel") or "").strip().lower()
        row["target"] = str(row.get("target") or "").strip()
        row["status"] = str(row.get("status") or "").strip().lower() or "unknown"
        for key in ("detail", "digest_hash"):
            value = row.get(key)
            row[key] = str(value) if value not in (None, "") else None
        metadata_json = row.pop("metadata_json", "{}")
        try:
            parsed = json.loads(metadata_json or "{}")
            row["metadata"] = parsed if isinstance(parsed, dict) else {}
        except Exception:
            row["metadata"] = {}
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

    @staticmethod
    def _decode_terminal_action_receipt_row(row: dict[str, Any]) -> dict[str, Any]:
        arguments_json = row.pop("arguments_json", "{}")
        result_json = row.pop("result_json", None)
        details_json = row.pop("details_json", "{}")
        action_receipt_json = row.pop("action_receipt_json", "{}")
        try:
            arguments = json.loads(arguments_json or "{}")
        except Exception:
            arguments = {}
        try:
            result = json.loads(result_json) if result_json not in (None, "") else None
        except Exception:
            result = None
        try:
            details = json.loads(details_json or "{}")
        except Exception:
            details = {}
        try:
            action_receipt = json.loads(action_receipt_json or "{}")
        except Exception:
            action_receipt = {}

        row["arguments"] = arguments if isinstance(arguments, dict) else {}
        row["result"] = result
        row["details"] = details if isinstance(details, dict) else {}
        row["action_receipt"] = action_receipt if isinstance(action_receipt, dict) else {}
        row["action"] = str(row.get("action") or "tool_invoke").strip().lower() or "tool_invoke"
        row["tool_name"] = str(row.get("tool_name") or "").strip()
        row["status"] = str(row.get("status") or "succeeded").strip().lower() or "succeeded"
        row["risk_level"] = str(row.get("risk_level") or "medium").strip().lower() or "medium"
        return row

    @staticmethod
    def _decode_filesystem_patch_preview_row(
        row: dict[str, Any],
        *,
        include_after_content: bool = False,
    ) -> dict[str, Any]:
        diff_json = row.pop("diff_json", "{}")
        after_content = row.pop("after_content", "")
        try:
            diff = json.loads(diff_json or "{}")
        except Exception:
            diff = {}
        row["diff"] = diff if isinstance(diff, dict) else {}
        row["status"] = str(row.get("status") or "pending").strip().lower() or "pending"
        row["before_exists"] = bool(int(row.get("before_exists", 0)))
        if include_after_content:
            row["after_content"] = str(after_content or "")
        return row

    @staticmethod
    def _decode_auth_token_activity_row(row: dict[str, Any]) -> dict[str, Any]:
        scopes_json = row.pop("scopes_json", "[]")
        metadata_json = row.pop("metadata_json", "{}")
        try:
            parsed_scopes = json.loads(scopes_json or "[]")
            row["scopes"] = [str(item).strip().lower() for item in parsed_scopes] if isinstance(parsed_scopes, list) else []
        except Exception:
            row["scopes"] = []
        try:
            parsed_meta = json.loads(metadata_json or "{}")
            row["metadata"] = parsed_meta if isinstance(parsed_meta, dict) else {}
        except Exception:
            row["metadata"] = {}
        try:
            row["request_count"] = max(0, int(row.get("request_count", 0)))
        except Exception:
            row["request_count"] = 0
        row["token_fingerprint"] = str(row.get("token_fingerprint") or "").strip()
        row["user_id"] = str(row.get("user_id") or "").strip()
        row["last_method"] = str(row.get("last_method") or "").strip().upper() or None
        return row

    @staticmethod
    def _decode_secret_inventory_row(row: dict[str, Any]) -> dict[str, Any]:
        metadata_json = row.pop("metadata_json", "{}")
        try:
            parsed_meta = json.loads(metadata_json or "{}")
            row["metadata"] = parsed_meta if isinstance(parsed_meta, dict) else {}
        except Exception:
            row["metadata"] = {}
        row["is_required"] = bool(int(row.get("is_required", 0)))
        row["value_present"] = bool(int(row.get("value_present", 0)))
        try:
            row["rotation_period_days"] = max(1, int(row.get("rotation_period_days", 90)))
        except Exception:
            row["rotation_period_days"] = 90
        row["status"] = str(row.get("status") or "unknown").strip().lower() or "unknown"
        row["secret_key"] = str(row.get("secret_key") or "").strip()
        row["provider"] = str(row.get("provider") or "runtime").strip() or "runtime"
        return row

    @staticmethod
    def _decode_access_review_row(row: dict[str, Any]) -> dict[str, Any]:
        snapshot_json = row.pop("snapshot_json", "{}")
        decisions_json = row.pop("decisions_json", "{}")
        findings_json = row.pop("findings_json", "[]")
        metadata_json = row.pop("metadata_json", "{}")
        try:
            parsed_snapshot = json.loads(snapshot_json or "{}")
            row["snapshot"] = parsed_snapshot if isinstance(parsed_snapshot, dict) else {}
        except Exception:
            row["snapshot"] = {}
        try:
            parsed_decisions = json.loads(decisions_json or "{}")
            row["decisions"] = parsed_decisions if isinstance(parsed_decisions, dict) else {}
        except Exception:
            row["decisions"] = {}
        try:
            parsed_findings = json.loads(findings_json or "[]")
            row["findings"] = parsed_findings if isinstance(parsed_findings, list) else []
        except Exception:
            row["findings"] = []
        try:
            parsed_meta = json.loads(metadata_json or "{}")
            row["metadata"] = parsed_meta if isinstance(parsed_meta, dict) else {}
        except Exception:
            row["metadata"] = {}
        row["status"] = str(row.get("status") or "open").strip().lower() or "open"
        return row

    @staticmethod
    def _decode_security_incident_row(row: dict[str, Any]) -> dict[str, Any]:
        metadata_json = row.pop("metadata_json", "{}")
        try:
            parsed_meta = json.loads(metadata_json or "{}")
            row["metadata"] = parsed_meta if isinstance(parsed_meta, dict) else {}
        except Exception:
            row["metadata"] = {}
        row["status"] = str(row.get("status") or "open").strip().lower() or "open"
        row["severity"] = str(row.get("severity") or "medium").strip().lower() or "medium"
        row["category"] = str(row.get("category") or "security").strip().lower() or "security"
        return row

    @staticmethod
    def _decode_security_incident_event_row(row: dict[str, Any]) -> dict[str, Any]:
        details_json = row.pop("details_json", "{}")
        try:
            parsed_details = json.loads(details_json or "{}")
            row["details"] = parsed_details if isinstance(parsed_details, dict) else {}
        except Exception:
            row["details"] = {}
        row["event_type"] = str(row.get("event_type") or "note").strip().lower() or "note"
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
            self._commit_locked()

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

    def delete_agent(self, agent_id: str) -> bool:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM agents WHERE id = ?",
                (agent_id,),
            )
            self._commit_locked()
            return int(cursor.rowcount or 0) > 0
