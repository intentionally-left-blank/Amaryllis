from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any


@dataclass(frozen=True)
class AutonomyCircuitState:
    armed: bool
    revision: int
    updated_at: str
    armed_at: str | None
    armed_by: str | None
    disarmed_at: str | None
    disarmed_by: str | None
    reason: str | None
    request_id: str | None


class AutonomyCircuitBreaker:
    """Thread-safe emergency brake for autonomous run creation."""

    def __init__(self) -> None:
        now = _utc_now_iso()
        self._state = AutonomyCircuitState(
            armed=False,
            revision=0,
            updated_at=now,
            armed_at=None,
            armed_by=None,
            disarmed_at=now,
            disarmed_by=None,
            reason=None,
            request_id=None,
        )
        self._lock = Lock()

    def is_armed(self) -> bool:
        with self._lock:
            return bool(self._state.armed)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = self._state
        return _state_to_payload(state)

    def arm(
        self,
        *,
        actor: str | None = None,
        reason: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_actor = _normalize_optional(actor)
        normalized_reason = _normalize_optional(reason)
        normalized_request_id = _normalize_optional(request_id)
        now = _utc_now_iso()

        with self._lock:
            previous = self._state
            self._state = AutonomyCircuitState(
                armed=True,
                revision=previous.revision + 1,
                updated_at=now,
                armed_at=previous.armed_at or now,
                armed_by=normalized_actor,
                disarmed_at=previous.disarmed_at,
                disarmed_by=previous.disarmed_by,
                reason=normalized_reason,
                request_id=normalized_request_id,
            )
            current = self._state
        return _state_to_payload(current)

    def disarm(
        self,
        *,
        actor: str | None = None,
        reason: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_actor = _normalize_optional(actor)
        normalized_reason = _normalize_optional(reason)
        normalized_request_id = _normalize_optional(request_id)
        now = _utc_now_iso()

        with self._lock:
            previous = self._state
            self._state = AutonomyCircuitState(
                armed=False,
                revision=previous.revision + 1,
                updated_at=now,
                armed_at=previous.armed_at,
                armed_by=previous.armed_by,
                disarmed_at=now,
                disarmed_by=normalized_actor,
                reason=normalized_reason,
                request_id=normalized_request_id,
            )
            current = self._state
        return _state_to_payload(current)


def _state_to_payload(state: AutonomyCircuitState) -> dict[str, Any]:
    return {
        "status": "armed" if state.armed else "disarmed",
        "armed": bool(state.armed),
        "revision": int(state.revision),
        "updated_at": state.updated_at,
        "armed_at": state.armed_at,
        "armed_by": state.armed_by,
        "disarmed_at": state.disarmed_at,
        "disarmed_by": state.disarmed_by,
        "reason": state.reason,
        "request_id": state.request_id,
    }


def _normalize_optional(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
