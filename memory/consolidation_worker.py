from __future__ import annotations

import logging
from threading import Event, Thread
from typing import Any, Protocol

from memory.memory_manager import MemoryManager
from storage.database import Database


class TelemetrySink(Protocol):
    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        ...


class MemoryConsolidationWorker:
    def __init__(
        self,
        *,
        database: Database,
        memory_manager: MemoryManager,
        interval_sec: float = 600.0,
        semantic_limit: int = 1000,
        max_users_per_tick: int = 20,
        telemetry: TelemetrySink | None = None,
    ) -> None:
        self.logger = logging.getLogger("amaryllis.memory.consolidation")
        self.database = database
        self.memory_manager = memory_manager
        self.interval_sec = max(30.0, float(interval_sec))
        self.semantic_limit = max(100, int(semantic_limit))
        self.max_users_per_tick = max(1, int(max_users_per_tick))
        self.telemetry = telemetry

        self._thread: Thread | None = None
        self._stop = Event()
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stop.clear()
        self._thread = Thread(
            target=self._loop,
            name="amaryllis-memory-consolidation",
            daemon=True,
        )
        self._thread.start()
        self.logger.info(
            "memory_consolidation_worker_started interval_sec=%s semantic_limit=%s max_users=%s",
            self.interval_sec,
            self.semantic_limit,
            self.max_users_per_tick,
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
        self.logger.info("memory_consolidation_worker_stopped")

    def run_once(self) -> dict[str, Any]:
        users = self.database.list_memory_users(limit=self.max_users_per_tick)
        processed = 0
        deactivated_total = 0
        conflicts_total = 0

        for user_id in users:
            try:
                summary = self.memory_manager.consolidate_user_memory(
                    user_id=user_id,
                    semantic_limit=self.semantic_limit,
                )
                processed += 1
                deactivated_total += int(summary.get("semantic_deactivated", 0))
                conflicts_total += int(summary.get("conflicts_recorded", 0))
            except Exception as exc:  # noqa: BLE001
                self.logger.warning("memory_consolidation_user_failed user_id=%s error=%s", user_id, exc)

        payload = {
            "users_seen": len(users),
            "users_processed": processed,
            "semantic_deactivated_total": deactivated_total,
            "conflicts_recorded_total": conflicts_total,
        }
        self._emit("memory_consolidation_tick", payload)
        return payload

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception as exc:  # noqa: BLE001
                self.logger.exception("memory_consolidation_tick_failed error=%s", exc)
            self._stop.wait(self.interval_sec)

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.telemetry is None:
            return
        try:
            self.telemetry.emit(event_type=event_type, payload=payload)
        except Exception:
            self.logger.debug("memory_consolidation_telemetry_emit_failed event=%s", event_type)
