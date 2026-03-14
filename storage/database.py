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
                    lease_owner,
                    lease_expires_at,
                    backoff_until,
                    circuit_open_until,
                    last_dispatch_key,
                    created_at,
                    updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, NULL, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?)
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
