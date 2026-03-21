from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable
from uuid import uuid4

UNIFIED_SESSION_CHANNELS: set[str] = {"text", "voice", "visual"}
UNIFIED_SESSION_STATES: set[str] = {
    "created",
    "listening",
    "planning",
    "acting",
    "reviewing",
    "closed",
}
_INITIAL_STATE = "__init__"
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    _INITIAL_STATE: {"created"},
    "created": {"listening", "planning", "acting", "reviewing", "closed"},
    "listening": {"planning", "acting", "reviewing", "closed"},
    "planning": {"acting", "reviewing", "closed"},
    "acting": {"reviewing", "planning", "closed"},
    "reviewing": {"planning", "acting", "closed"},
    "closed": set(),
}

TelemetryEmitter = Callable[[str, dict[str, Any]], None]


class UnifiedSessionManager:
    def __init__(
        self,
        *,
        telemetry_emitter: TelemetryEmitter | None = None,
        max_sessions: int = 20_000,
    ) -> None:
        self._lock = RLock()
        self._sessions: dict[str, dict[str, Any]] = {}
        self._telemetry_emitter = telemetry_emitter
        self._max_sessions = max(1, int(max_sessions))

    def start_session(
        self,
        *,
        user_id: str,
        channels: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        initial_state: str = "created",
        request_id: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        normalized_user = str(user_id or "").strip()
        if not normalized_user:
            raise ValueError("user_id is required")

        resolved_channels = self._normalize_channels(channels)
        payload_metadata = dict(metadata) if isinstance(metadata, dict) else {}
        normalized_initial = str(initial_state or "created").strip().lower() or "created"
        if normalized_initial not in UNIFIED_SESSION_STATES:
            allowed = ", ".join(sorted(UNIFIED_SESSION_STATES))
            raise ValueError(f"Invalid initial_state '{initial_state}'. Allowed values: {allowed}.")

        session_id = f"flow-{uuid4().hex}"
        now = _utcnow_iso()
        with self._lock:
            self._evict_closed_if_needed()
            if len(self._sessions) >= self._max_sessions:
                raise ValueError("unified session capacity reached")

            session = {
                "id": session_id,
                "user_id": normalized_user,
                "state": _INITIAL_STATE,
                "channels": resolved_channels,
                "metadata": payload_metadata,
                "created_at": now,
                "updated_at": now,
                "closed_at": None,
                "duration_ms": None,
                "transitions": [],
                "channel_activity": {
                    channel: {
                        "events_count": 0,
                        "last_event": None,
                        "last_at": None,
                    }
                    for channel in resolved_channels
                },
                "telemetry": {
                    "events_emitted": 0,
                    "transition_count": 0,
                    "last_transition_at": None,
                    "last_state": None,
                    "last_actor": None,
                },
            }
            self._sessions[session_id] = session

            self._transition(
                session,
                to_state="created",
                reason="session_started",
                actor=actor,
                request_id=request_id,
                metadata={"channels": resolved_channels},
            )
            if normalized_initial != "created":
                self._transition(
                    session,
                    to_state=normalized_initial,
                    reason="initial_state_requested",
                    actor=actor,
                    request_id=request_id,
                )

            self._emit_for_session(
                session,
                event="flow_session_started",
                payload={
                    "session_id": session_id,
                    "user_id": normalized_user,
                    "state": str(session.get("state") or ""),
                    "channels": resolved_channels,
                    "actor": actor,
                    "request_id": request_id,
                },
            )
            return _snapshot(session)

    def transition_session(
        self,
        *,
        session_id: str,
        to_state: str,
        reason: str,
        actor: str | None = None,
        metadata: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_id = str(session_id or "").strip()
        if not normalized_id:
            raise ValueError("session_id is required")
        normalized_reason = str(reason or "").strip() or "state_transition"
        with self._lock:
            session = self._sessions.get(normalized_id)
            if session is None:
                raise ValueError(f"Flow session not found: {normalized_id}")
            self._transition(
                session,
                to_state=to_state,
                reason=normalized_reason,
                actor=actor,
                metadata=metadata,
                request_id=request_id,
            )
            return _snapshot(session)

    def record_activity(
        self,
        *,
        session_id: str,
        channel: str,
        event: str,
        actor: str | None = None,
        metadata: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_id = str(session_id or "").strip()
        if not normalized_id:
            raise ValueError("session_id is required")
        normalized_channel = str(channel or "").strip().lower()
        if normalized_channel not in UNIFIED_SESSION_CHANNELS:
            allowed = ", ".join(sorted(UNIFIED_SESSION_CHANNELS))
            raise ValueError(f"Invalid channel '{channel}'. Allowed values: {allowed}.")
        normalized_event = str(event or "").strip()
        if not normalized_event:
            raise ValueError("event is required")

        with self._lock:
            session = self._sessions.get(normalized_id)
            if session is None:
                raise ValueError(f"Flow session not found: {normalized_id}")
            if str(session.get("state") or "") == "closed":
                raise ValueError("Flow session is closed")

            channels = session.get("channels")
            if not isinstance(channels, list) or normalized_channel not in channels:
                raise ValueError(
                    f"Flow session channel '{normalized_channel}' is not enabled for this session"
                )

            activity = session.get("channel_activity")
            if not isinstance(activity, dict):
                activity = {}
                session["channel_activity"] = activity
            state = activity.get(normalized_channel)
            if not isinstance(state, dict):
                state = {"events_count": 0, "last_event": None, "last_at": None}
                activity[normalized_channel] = state

            state["events_count"] = int(state.get("events_count", 0)) + 1
            state["last_event"] = normalized_event
            state["last_at"] = _utcnow_iso()
            session["updated_at"] = str(state["last_at"])

            self._emit_for_session(
                session,
                event="flow_session_activity",
                payload={
                    "session_id": normalized_id,
                    "user_id": str(session.get("user_id") or ""),
                    "state": str(session.get("state") or ""),
                    "channel": normalized_channel,
                    "event": normalized_event,
                    "actor": actor,
                    "request_id": request_id,
                    "metadata": dict(metadata) if isinstance(metadata, dict) else {},
                },
            )
            return _snapshot(session)

    def get_session(self, *, session_id: str) -> dict[str, Any]:
        normalized_id = str(session_id or "").strip()
        if not normalized_id:
            raise ValueError("session_id is required")
        with self._lock:
            session = self._sessions.get(normalized_id)
            if session is None:
                raise ValueError(f"Flow session not found: {normalized_id}")
            return _snapshot(session)

    def list_sessions(
        self,
        *,
        user_id: str | None = None,
        state: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        normalized_user = str(user_id or "").strip() or None
        normalized_state = str(state or "").strip().lower() or None
        if normalized_state is not None and normalized_state not in UNIFIED_SESSION_STATES:
            allowed = ", ".join(sorted(UNIFIED_SESSION_STATES))
            raise ValueError(f"Invalid flow session state '{state}'. Allowed values: {allowed}.")

        capped_limit = max(1, min(int(limit), 2000))
        with self._lock:
            rows = list(self._sessions.values())
            rows.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
            items: list[dict[str, Any]] = []
            for row in rows:
                if normalized_user is not None and str(row.get("user_id") or "") != normalized_user:
                    continue
                if normalized_state is not None and str(row.get("state") or "") != normalized_state:
                    continue
                items.append(_snapshot(row))
                if len(items) >= capped_limit:
                    break
            return items

    def _transition(
        self,
        session: dict[str, Any],
        *,
        to_state: str,
        reason: str,
        actor: str | None = None,
        metadata: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> None:
        from_state = str(session.get("state") or _INITIAL_STATE)
        target_state = str(to_state or "").strip().lower()
        if target_state not in UNIFIED_SESSION_STATES:
            raise ValueError(f"Invalid flow session transition target: {to_state}")
        allowed = _ALLOWED_TRANSITIONS.get(from_state, set())
        if target_state not in allowed:
            raise ValueError(f"Invalid flow session transition: {from_state} -> {target_state}")

        now = _utcnow_iso()
        transition = {
            "from_state": None if from_state == _INITIAL_STATE else from_state,
            "to_state": target_state,
            "at": now,
            "reason": str(reason or "").strip() or "state_transition",
            "actor": _optional_str(actor),
            "metadata": dict(metadata) if isinstance(metadata, dict) else {},
            "request_id": _optional_str(request_id),
        }

        transitions = session.get("transitions")
        if not isinstance(transitions, list):
            transitions = []
            session["transitions"] = transitions
        transitions.append(transition)

        previous_started = _parse_iso(session.get("created_at"))
        session["state"] = target_state
        session["updated_at"] = now
        if target_state == "closed":
            session["closed_at"] = now
            if previous_started is not None:
                delta_ms = int((datetime.now(timezone.utc) - previous_started).total_seconds() * 1000)
                session["duration_ms"] = max(0, delta_ms)

        telemetry = session.get("telemetry")
        if not isinstance(telemetry, dict):
            telemetry = {}
            session["telemetry"] = telemetry
        telemetry["transition_count"] = int(telemetry.get("transition_count", 0)) + 1
        telemetry["last_transition_at"] = now
        telemetry["last_state"] = target_state
        telemetry["last_actor"] = _optional_str(actor)

        self._emit_for_session(
            session,
            event="flow_session_transition",
            payload={
                "session_id": str(session.get("id") or ""),
                "user_id": str(session.get("user_id") or ""),
                "from_state": None if from_state == _INITIAL_STATE else from_state,
                "to_state": target_state,
                "reason": transition["reason"],
                "actor": _optional_str(actor),
                "request_id": _optional_str(request_id),
            },
        )

    def _emit_for_session(self, session: dict[str, Any], *, event: str, payload: dict[str, Any]) -> None:
        telemetry = session.get("telemetry")
        if not isinstance(telemetry, dict):
            telemetry = {}
            session["telemetry"] = telemetry
        telemetry["events_emitted"] = int(telemetry.get("events_emitted", 0)) + 1
        if self._telemetry_emitter is None:
            return
        try:
            self._telemetry_emitter(event, payload)
        except Exception:
            return

    def _normalize_channels(self, channels: list[str] | None) -> list[str]:
        raw = list(channels or ["text"])
        normalized = sorted({str(item).strip().lower() for item in raw if str(item).strip()})
        if not normalized:
            normalized = ["text"]
        invalid = [item for item in normalized if item not in UNIFIED_SESSION_CHANNELS]
        if invalid:
            allowed = ", ".join(sorted(UNIFIED_SESSION_CHANNELS))
            raise ValueError(
                f"Invalid channels: {', '.join(invalid)}. Allowed values: {allowed}."
            )
        return normalized

    def _evict_closed_if_needed(self) -> None:
        if len(self._sessions) < self._max_sessions:
            return
        rows = [
            item
            for item in self._sessions.values()
            if str(item.get("state") or "") == "closed"
        ]
        rows.sort(key=lambda row: str(row.get("updated_at") or ""))
        while len(self._sessions) >= self._max_sessions and rows:
            candidate = rows.pop(0)
            candidate_id = str(candidate.get("id") or "")
            if candidate_id and candidate_id in self._sessions:
                self._sessions.pop(candidate_id, None)


def _optional_str(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(payload)


def _parse_iso(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return None
