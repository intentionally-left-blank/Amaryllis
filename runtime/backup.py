from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
import shutil
import sqlite3
import tarfile
import tempfile
from datetime import datetime, timedelta, timezone
from threading import Event, Lock, RLock, Thread
import time
from typing import Any, Protocol
from uuid import uuid4

from storage.database import Database
from storage.vector_store import VectorStore


class TelemetrySink(Protocol):
    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        ...


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    value = dt or _utc_now()
    return value.isoformat()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _is_subpath(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except Exception:
        return False


def _safe_unpack_archive(archive: tarfile.TarFile, destination: Path) -> None:
    root = destination.resolve()
    for member in archive.getmembers():
        candidate = (destination / member.name).resolve()
        try:
            candidate.relative_to(root)
        except Exception as exc:
            raise RuntimeError(f"Archive path traversal blocked: {member.name}") from exc
        if member.issym() or member.islnk():
            raise RuntimeError(f"Archive symlink entry is not allowed: {member.name}")
        if member.isdir():
            candidate.mkdir(parents=True, exist_ok=True)
            continue
        if not member.isfile():
            raise RuntimeError(f"Unsupported archive entry type: {member.name}")
        candidate.parent.mkdir(parents=True, exist_ok=True)
        file_obj = archive.extractfile(member)
        if file_obj is None:
            raise RuntimeError(f"Failed to read archive entry: {member.name}")
        with file_obj:
            with candidate.open("wb") as output:
                shutil.copyfileobj(file_obj, output)


def _collect_db_stats(database_path: Path) -> dict[str, Any]:
    if not database_path.exists():
        raise FileNotFoundError(f"Database snapshot not found: {database_path}")
    conn = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        version_row = conn.execute("SELECT MAX(version) AS version FROM schema_migrations").fetchone()
        schema_version = int(version_row["version"] or 0) if version_row else 0
        table_rows = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type = 'table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name ASC
            """
        ).fetchall()
        row_counts: dict[str, int] = {}
        for row in table_rows:
            table_name = str(row["name"])
            count_row = conn.execute(f'SELECT COUNT(*) AS total FROM "{table_name}"').fetchone()
            row_counts[table_name] = int(count_row["total"] or 0) if count_row else 0
        return {
            "schema_version": schema_version,
            "row_counts": row_counts,
        }
    finally:
        conn.close()


class BackupManager:
    def __init__(
        self,
        *,
        database: Database,
        vector_store: VectorStore,
        data_dir: Path,
        backup_dir: Path,
        database_path: Path,
        identity_path: Path,
        app_version: str,
        retention_count: int = 120,
        retention_days: int = 30,
        verify_on_create: bool = True,
        telemetry: TelemetrySink | None = None,
    ) -> None:
        self.logger = logging.getLogger("amaryllis.backup.manager")
        self.database = database
        self.vector_store = vector_store
        self.data_dir = Path(data_dir)
        self.backup_dir = Path(backup_dir)
        self.database_path = Path(database_path)
        self.identity_path = Path(identity_path)
        self.app_version = str(app_version or "0.0.0")
        self.retention_count = max(1, int(retention_count))
        self.retention_days = max(1, int(retention_days))
        self.verify_on_create = bool(verify_on_create)
        self.telemetry = telemetry
        self._lock = RLock()

        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def create_backup(
        self,
        *,
        trigger: str,
        actor: str | None = None,
        request_id: str | None = None,
        verify: bool | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            started_at = _utc_now()
            backup_id = f"{started_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:12]}"
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            archive_path = self.backup_dir / f"{backup_id}.tar.gz"
            tmp_archive = self.backup_dir / f".{backup_id}.tmp.tar.gz"
            metadata_path = self.backup_dir / f"{backup_id}.meta.json"
            verification_enabled = self.verify_on_create if verify is None else bool(verify)
            self._emit(
                "backup_started",
                {
                    "backup_id": backup_id,
                    "trigger": trigger,
                    "actor": actor,
                    "request_id": request_id,
                },
            )
            try:
                if tmp_archive.exists():
                    tmp_archive.unlink()
                with tempfile.TemporaryDirectory(prefix=f"amaryllis-backup-{backup_id}-") as tmp:
                    stage_root = Path(tmp) / "bundle"
                    stage_root.mkdir(parents=True, exist_ok=True)
                    self._build_backup_stage(
                        stage=stage_root,
                        backup_id=backup_id,
                        trigger=trigger,
                        actor=actor,
                    )
                    with tarfile.open(tmp_archive, mode="w:gz") as tar:
                        for path in sorted(stage_root.rglob("*")):
                            arcname = str(path.relative_to(stage_root))
                            tar.add(path, arcname=arcname, recursive=False)
                os.replace(tmp_archive, archive_path)
                archive_size_bytes = int(archive_path.stat().st_size)
                archive_sha256 = _sha256_file(archive_path)
                verification: dict[str, Any] = {
                    "requested": verification_enabled,
                    "ok": None,
                    "details": {},
                }
                if verification_enabled:
                    verification_result = self.verify_backup(backup_id=backup_id)
                    verification["ok"] = bool(verification_result.get("ok", False))
                    verification["details"] = verification_result
                metadata = {
                    "backup_id": backup_id,
                    "created_at": _iso(started_at),
                    "app_version": self.app_version,
                    "trigger": trigger,
                    "archive_path": str(archive_path),
                    "archive_size_bytes": archive_size_bytes,
                    "archive_sha256": archive_sha256,
                    "verification": verification,
                    "actor": actor,
                    "request_id": request_id,
                }
                metadata_path.write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                retention = self.enforce_retention()
                duration_ms = round((_utc_now() - started_at).total_seconds() * 1000.0, 2)
                result = {
                    **metadata,
                    "retention": retention,
                    "duration_ms": duration_ms,
                }
                self._persist_status(success=result)
                self._emit(
                    "backup_succeeded",
                    {
                        "backup_id": backup_id,
                        "trigger": trigger,
                        "archive_size_bytes": archive_size_bytes,
                        "verification_ok": verification.get("ok"),
                        "duration_ms": duration_ms,
                    },
                )
                return result
            except Exception as exc:
                if tmp_archive.exists():
                    tmp_archive.unlink(missing_ok=True)
                failure = {
                    "backup_id": backup_id,
                    "created_at": _iso(started_at),
                    "trigger": trigger,
                    "error": str(exc),
                    "actor": actor,
                    "request_id": request_id,
                }
                self._persist_status(failure=failure)
                self._emit(
                    "backup_failed",
                    {
                        "backup_id": backup_id,
                        "trigger": trigger,
                        "error": str(exc),
                    },
                )
                raise

    def list_backups(self, *, limit: int = 100) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for archive_path in self._list_backup_archives():
            backup_id = archive_path.name.replace(".tar.gz", "")
            metadata_path = self.backup_dir / f"{backup_id}.meta.json"
            payload: dict[str, Any]
            if metadata_path.exists():
                try:
                    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
                except Exception:
                    payload = {"backup_id": backup_id}
            else:
                payload = {"backup_id": backup_id}
            payload["archive_path"] = str(archive_path)
            try:
                payload.setdefault("archive_size_bytes", int(archive_path.stat().st_size))
            except Exception:
                payload.setdefault("archive_size_bytes", 0)
            entries.append(payload)
            if len(entries) >= max(1, int(limit)):
                break
        return entries

    def verify_backup(self, *, backup_id: str | None = None, archive_path: Path | None = None) -> dict[str, Any]:
        with self._lock:
            source = self._resolve_backup_path(backup_id=backup_id, archive_path=archive_path)
            started = _utc_now()
            with tempfile.TemporaryDirectory(prefix="amaryllis-verify-backup-") as tmp:
                extracted = Path(tmp) / "extract"
                extracted.mkdir(parents=True, exist_ok=True)
                with tarfile.open(source, mode="r:gz") as tar:
                    _safe_unpack_archive(tar, extracted)
                manifest_path = extracted / "manifest.json"
                if not manifest_path.exists():
                    raise RuntimeError("Backup manifest is missing")
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                files = manifest.get("files")
                if not isinstance(files, list):
                    raise RuntimeError("Backup manifest files section is invalid")
                checksum_errors: list[str] = []
                for item in files:
                    if not isinstance(item, dict):
                        continue
                    rel = str(item.get("path", "")).strip()
                    expected = str(item.get("sha256", "")).strip().lower()
                    if not rel or not expected:
                        checksum_errors.append(rel or "<unknown>")
                        continue
                    target = extracted / rel
                    if not target.exists():
                        checksum_errors.append(rel)
                        continue
                    actual = _sha256_file(target)
                    if actual != expected:
                        checksum_errors.append(rel)
                db_rel = str(manifest.get("database_snapshot", "")).strip()
                if not db_rel:
                    raise RuntimeError("Manifest does not contain database_snapshot")
                db_path = extracted / db_rel
                before = _collect_db_stats(db_path)
                db_runtime = Database(db_path)
                db_runtime.close()
                after = _collect_db_stats(db_path)
                overlapping_tables = set(before["row_counts"].keys()) & set(after["row_counts"].keys())
                row_count_changed = {
                    name: {
                        "before": int(before["row_counts"][name]),
                        "after": int(after["row_counts"][name]),
                    }
                    for name in sorted(overlapping_tables)
                    if int(before["row_counts"][name]) != int(after["row_counts"][name])
                }
                ok = not checksum_errors and not row_count_changed
                result = {
                    "ok": ok,
                    "backup_id": str(manifest.get("backup_id") or source.name.replace(".tar.gz", "")),
                    "archive_path": str(source),
                    "checked_at": _iso(),
                    "checksums_ok": not checksum_errors,
                    "checksum_errors": checksum_errors,
                    "migration_ok": True,
                    "schema_version_before": int(before["schema_version"]),
                    "schema_version_after": int(after["schema_version"]),
                    "row_count_changed": row_count_changed,
                    "duration_ms": round((_utc_now() - started).total_seconds() * 1000.0, 2),
                }
                if not ok:
                    self._emit(
                        "backup_verify_failed",
                        {
                            "archive_path": str(source),
                            "checksum_errors": checksum_errors,
                            "row_count_changed": row_count_changed,
                        },
                    )
                return result

    def run_restore_drill(self, *, backup_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            source = self._resolve_backup_path(backup_id=backup_id, archive_path=None)
            verify = self.verify_backup(archive_path=source)
            if not bool(verify.get("ok", False)):
                return {
                    "ok": False,
                    "backup_id": verify.get("backup_id"),
                    "error": "verify_failed",
                    "verify": verify,
                }
            with tempfile.TemporaryDirectory(prefix="amaryllis-restore-drill-") as tmp:
                target = Path(tmp) / "restored-data"
                restore = self.restore_backup(
                    archive_path=source,
                    target_data_dir=target,
                    preserve_existing=False,
                )
                restored_db = target / self.database_path.name
                db_runtime = Database(restored_db)
                db_runtime.close()
                return {
                    "ok": True,
                    "backup_id": verify.get("backup_id"),
                    "verify": verify,
                    "restore": restore,
                    "restored_database_path": str(restored_db),
                }

    def restore_backup(
        self,
        *,
        archive_path: Path,
        target_data_dir: Path,
        preserve_existing: bool = True,
    ) -> dict[str, Any]:
        source = self._resolve_backup_path(archive_path=archive_path, backup_id=None)
        target_data_dir = Path(target_data_dir)
        target_data_dir.parent.mkdir(parents=True, exist_ok=True)
        rollback_path: Path | None = None
        with tempfile.TemporaryDirectory(prefix="amaryllis-restore-stage-") as tmp:
            extracted = Path(tmp) / "extract"
            extracted.mkdir(parents=True, exist_ok=True)
            with tarfile.open(source, mode="r:gz") as tar:
                _safe_unpack_archive(tar, extracted)
            source_data = extracted / "data"
            if not source_data.exists():
                raise RuntimeError("Backup archive does not contain data directory")
            restore_stage = target_data_dir.parent / f".{target_data_dir.name}.restore-{uuid4().hex[:10]}"
            if restore_stage.exists():
                shutil.rmtree(restore_stage, ignore_errors=True)
            shutil.copytree(source_data, restore_stage, dirs_exist_ok=False)
            if target_data_dir.exists():
                if preserve_existing:
                    rollback_path = target_data_dir.parent / (
                        f"{target_data_dir.name}.rollback-{_utc_now().strftime('%Y%m%dT%H%M%SZ')}"
                    )
                    os.replace(target_data_dir, rollback_path)
                else:
                    shutil.rmtree(target_data_dir, ignore_errors=True)
            os.replace(restore_stage, target_data_dir)
        return {
            "ok": True,
            "archive_path": str(source),
            "target_data_dir": str(target_data_dir),
            "rollback_path": str(rollback_path) if rollback_path is not None else None,
        }

    def enforce_retention(self) -> dict[str, Any]:
        deleted: list[str] = []
        cutoff = _utc_now() - timedelta(days=self.retention_days)
        archives = self._list_backup_archives()
        for idx, archive_path in enumerate(archives):
            created = datetime.fromtimestamp(archive_path.stat().st_mtime, tz=timezone.utc)
            over_count = idx >= self.retention_count
            over_age = created < cutoff
            if not over_count and not over_age:
                continue
            backup_id = archive_path.name.replace(".tar.gz", "")
            metadata_path = self.backup_dir / f"{backup_id}.meta.json"
            archive_path.unlink(missing_ok=True)
            metadata_path.unlink(missing_ok=True)
            deleted.append(backup_id)
        return {
            "deleted": deleted,
            "kept": max(0, len(archives) - len(deleted)),
            "retention_count": self.retention_count,
            "retention_days": self.retention_days,
        }

    def status(self) -> dict[str, Any]:
        status_path = self.backup_dir / "status.json"
        if not status_path.exists():
            return {
                "backup_dir": str(self.backup_dir),
                "last_success": None,
                "last_failure": None,
            }
        try:
            payload = json.loads(status_path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                payload.setdefault("backup_dir", str(self.backup_dir))
                return payload
        except Exception:
            pass
        return {
            "backup_dir": str(self.backup_dir),
            "last_success": None,
            "last_failure": None,
        }

    def _build_backup_stage(
        self,
        *,
        stage: Path,
        backup_id: str,
        trigger: str,
        actor: str | None,
    ) -> None:
        data_stage = stage / "data"
        data_stage.mkdir(parents=True, exist_ok=True)
        db_snapshot = data_stage / self.database_path.name
        self.database.backup_to(db_snapshot)
        self.vector_store.persist()

        if self.data_dir.exists():
            for src in sorted(self.data_dir.rglob("*")):
                if not src.is_file():
                    continue
                if src.resolve() == self.database_path.resolve():
                    continue
                name = src.name
                if name.endswith("-wal") or name.endswith("-shm") or name.endswith("-journal"):
                    continue
                if _is_subpath(src, self.backup_dir):
                    continue
                rel = src.relative_to(self.data_dir)
                dst = data_stage / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(src, dst)
                except FileNotFoundError:
                    continue

        if self.identity_path.exists() and not _is_subpath(self.identity_path, self.data_dir):
            external_identity = stage / "external" / "identity.json"
            external_identity.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.identity_path, external_identity)

        db_stats = _collect_db_stats(db_snapshot)
        files: list[dict[str, Any]] = []
        for file_path in sorted(stage.rglob("*")):
            if not file_path.is_file():
                continue
            rel = str(file_path.relative_to(stage))
            files.append(
                {
                    "path": rel,
                    "size_bytes": int(file_path.stat().st_size),
                    "sha256": _sha256_file(file_path),
                }
            )
        manifest = {
            "backup_id": backup_id,
            "created_at": _iso(),
            "app_version": self.app_version,
            "trigger": trigger,
            "actor": actor,
            "database_snapshot": f"data/{self.database_path.name}",
            "database_stats": db_stats,
            "files": files,
        }
        (stage / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def _resolve_backup_path(
        self,
        *,
        backup_id: str | None,
        archive_path: Path | None,
    ) -> Path:
        if archive_path is not None:
            resolved = Path(archive_path).expanduser().resolve()
            if not resolved.exists():
                raise FileNotFoundError(f"Backup archive not found: {resolved}")
            return resolved
        if backup_id:
            candidate = self.backup_dir / f"{backup_id}.tar.gz"
            if not candidate.exists():
                raise FileNotFoundError(f"Backup archive not found: {candidate}")
            return candidate
        latest = self.latest_backup_path()
        if latest is None:
            raise FileNotFoundError("No backup archives found")
        return latest

    def latest_backup_path(self) -> Path | None:
        archives = self._list_backup_archives()
        return archives[0] if archives else None

    def _list_backup_archives(self) -> list[Path]:
        if not self.backup_dir.exists():
            return []
        archives = sorted(
            [path for path in self.backup_dir.glob("*.tar.gz") if path.is_file()],
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        return archives

    def _persist_status(
        self,
        *,
        success: dict[str, Any] | None = None,
        failure: dict[str, Any] | None = None,
    ) -> None:
        status_path = self.backup_dir / "status.json"
        status_path.parent.mkdir(parents=True, exist_ok=True)
        current: dict[str, Any] = {}
        if status_path.exists():
            try:
                raw = json.loads(status_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    current = raw
            except Exception:
                current = {}
        if success is not None:
            current["last_success"] = success
        if failure is not None:
            current["last_failure"] = failure
        current["updated_at"] = _iso()
        current["backup_dir"] = str(self.backup_dir)
        status_path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.telemetry is None:
            return
        try:
            self.telemetry.emit(event_type=event_type, payload=payload)
        except Exception:
            self.logger.debug("backup_telemetry_emit_failed event=%s", event_type)


class BackupScheduler:
    def __init__(
        self,
        *,
        manager: BackupManager,
        interval_sec: float = 3600.0,
        restore_drill_interval_sec: float = 86400.0,
        restore_drill_enabled: bool = True,
        telemetry: TelemetrySink | None = None,
    ) -> None:
        self.logger = logging.getLogger("amaryllis.backup.scheduler")
        self.manager = manager
        self.interval_sec = max(30.0, float(interval_sec))
        self.restore_drill_interval_sec = max(300.0, float(restore_drill_interval_sec))
        self.restore_drill_enabled = bool(restore_drill_enabled)
        self.telemetry = telemetry

        self._thread: Thread | None = None
        self._stop = Event()
        self._started = False
        self._state_lock = Lock()
        self._last_backup_at: str | None = None
        self._last_backup_error: str | None = None
        self._last_restore_drill_at: str | None = None
        self._last_restore_drill_error: str | None = None

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stop.clear()
        self._thread = Thread(target=self._loop, name="amaryllis-backup-scheduler", daemon=True)
        self._thread.start()
        self.logger.info(
            "backup_scheduler_started interval_sec=%s restore_drill_enabled=%s restore_drill_interval_sec=%s",
            self.interval_sec,
            self.restore_drill_enabled,
            self.restore_drill_interval_sec,
        )

    def stop(self) -> None:
        if not self._started:
            return
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
        self._thread = None
        self._started = False
        self.logger.info("backup_scheduler_stopped")

    def health_snapshot(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "started": self._started,
                "interval_sec": self.interval_sec,
                "restore_drill_enabled": self.restore_drill_enabled,
                "restore_drill_interval_sec": self.restore_drill_interval_sec,
                "last_backup_at": self._last_backup_at,
                "last_backup_error": self._last_backup_error,
                "last_restore_drill_at": self._last_restore_drill_at,
                "last_restore_drill_error": self._last_restore_drill_error,
            }

    def run_backup_now(self, *, trigger: str, actor: str | None, request_id: str | None) -> dict[str, Any]:
        try:
            result = self.manager.create_backup(
                trigger=trigger,
                actor=actor,
                request_id=request_id,
                verify=None,
            )
            with self._state_lock:
                self._last_backup_at = str(result.get("created_at") or _iso())
                self._last_backup_error = None
            return result
        except Exception as exc:
            with self._state_lock:
                self._last_backup_error = str(exc)
            raise

    def run_restore_drill_now(self, *, backup_id: str | None = None) -> dict[str, Any]:
        result = self.manager.run_restore_drill(backup_id=backup_id)
        with self._state_lock:
            self._last_restore_drill_at = _iso()
            self._last_restore_drill_error = None if bool(result.get("ok", False)) else str(result.get("error"))
        return result

    def _loop(self) -> None:
        next_backup_deadline = time.monotonic() + self.interval_sec
        next_drill_deadline = time.monotonic() + self.restore_drill_interval_sec
        while not self._stop.is_set():
            now_mono = time.monotonic()
            if now_mono >= next_backup_deadline:
                try:
                    result = self.run_backup_now(
                        trigger="scheduled",
                        actor="backup_scheduler",
                        request_id=None,
                    )
                    self._emit(
                        "backup_scheduled_tick",
                        {
                            "ok": True,
                            "backup_id": result.get("backup_id"),
                        },
                    )
                except Exception as exc:
                    self.logger.exception("backup_scheduled_failed error=%s", exc)
                    self._emit(
                        "backup_scheduled_tick",
                        {
                            "ok": False,
                            "error": str(exc),
                        },
                    )
                next_backup_deadline = now_mono + self.interval_sec

            if self.restore_drill_enabled and now_mono >= next_drill_deadline:
                try:
                    drill = self.run_restore_drill_now()
                    self._emit(
                        "backup_restore_drill_tick",
                        {
                            "ok": bool(drill.get("ok", False)),
                            "backup_id": drill.get("backup_id"),
                        },
                    )
                except Exception as exc:
                    with self._state_lock:
                        self._last_restore_drill_error = str(exc)
                        self._last_restore_drill_at = _iso()
                    self.logger.exception("backup_restore_drill_failed error=%s", exc)
                    self._emit(
                        "backup_restore_drill_tick",
                        {
                            "ok": False,
                            "error": str(exc),
                        },
                    )
                next_drill_deadline = now_mono + self.restore_drill_interval_sec

            self._stop.wait(1.0)

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.telemetry is None:
            return
        try:
            self.telemetry.emit(event_type=event_type, payload=payload)
        except Exception:
            self.logger.debug("backup_scheduler_telemetry_emit_failed event=%s", event_type)
