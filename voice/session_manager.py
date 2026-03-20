from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Callable
from uuid import uuid4

VOICE_SESSION_MODES: set[str] = {"ptt"}
VOICE_SESSION_STATES: set[str] = {"created", "listening", "stopping", "stopped"}
_INITIAL_STATE = "__init__"
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    _INITIAL_STATE: {"created"},
    "created": {"listening", "stopping", "stopped"},
    "listening": {"stopping"},
    "stopping": {"stopped"},
    "stopped": set(),
}

TelemetryEmitter = Callable[[str, dict[str, Any]], None]


class VoiceSessionManager:
    def __init__(
        self,
        *,
        telemetry_emitter: TelemetryEmitter | None = None,
        max_sessions: int = 10_000,
    ) -> None:
        self._lock = RLock()
        self._sessions: dict[str, dict[str, Any]] = {}
        self._telemetry_emitter = telemetry_emitter
        self._max_sessions = max(1, int(max_sessions))

    def start_session(
        self,
        *,
        user_id: str,
        mode: str = "ptt",
        input_device: str | None = None,
        sample_rate_hz: int = 16_000,
        language: str | None = None,
        metadata: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_user = str(user_id or "").strip()
        if not normalized_user:
            raise ValueError("user_id is required")

        normalized_mode = str(mode or "").strip().lower() or "ptt"
        if normalized_mode not in VOICE_SESSION_MODES:
            allowed = ", ".join(sorted(VOICE_SESSION_MODES))
            raise ValueError(f"Unsupported voice mode '{mode}'. Allowed values: {allowed}.")

        rate = int(sample_rate_hz)
        if rate < 8_000 or rate > 96_000:
            raise ValueError("sample_rate_hz must be in range [8000, 96000]")

        session_id = f"voice-{uuid4().hex}"
        now = _utcnow_iso()
        payload_metadata = dict(metadata) if isinstance(metadata, dict) else {}

        with self._lock:
            self._evict_stopped_if_needed()
            if len(self._sessions) >= self._max_sessions:
                raise ValueError("voice session capacity reached")

            session = {
                "id": session_id,
                "user_id": normalized_user,
                "mode": normalized_mode,
                "state": _INITIAL_STATE,
                "input_device": _optional_str(input_device),
                "sample_rate_hz": rate,
                "language": _optional_str(language),
                "metadata": payload_metadata,
                "started_at": now,
                "updated_at": now,
                "stopped_at": None,
                "duration_ms": None,
                "transitions": [],
                "telemetry": {
                    "events_emitted": 0,
                    "transition_count": 0,
                    "last_event": None,
                    "last_transition_at": None,
                    "last_state": None,
                },
            }
            self._sessions[session_id] = session

            self._transition(
                session,
                to_state="created",
                reason="start_requested",
                request_id=request_id,
                metadata={"mode": normalized_mode},
            )
            self._transition(
                session,
                to_state="listening",
                reason="ptt_listen_ready",
                request_id=request_id,
            )
            self._emit_for_session(
                session,
                event="voice_session_started",
                payload={
                    "session_id": session_id,
                    "user_id": normalized_user,
                    "mode": normalized_mode,
                    "state": str(session.get("state")),
                    "request_id": request_id,
                },
            )
            return _snapshot(session)

    def stop_session(
        self,
        *,
        session_id: str,
        reason: str | None = None,
        actor: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_id = str(session_id or "").strip()
        if not normalized_id:
            raise ValueError("session_id is required")

        stop_reason = str(reason or "").strip() or "user_stop"
        with self._lock:
            session = self._sessions.get(normalized_id)
            if session is None:
                raise ValueError(f"Voice session not found: {normalized_id}")

            state = str(session.get("state") or "")
            if state == "stopped":
                self._emit_for_session(
                    session,
                    event="voice_session_stop_noop",
                    payload={
                        "session_id": normalized_id,
                        "user_id": str(session.get("user_id") or ""),
                        "state": state,
                        "reason": stop_reason,
                        "actor": actor,
                        "request_id": request_id,
                    },
                )
                return _snapshot(session)

            if state in {"created", "listening"}:
                self._transition(
                    session,
                    to_state="stopping",
                    reason=stop_reason,
                    request_id=request_id,
                    metadata={"actor": actor},
                )
                self._transition(
                    session,
                    to_state="stopped",
                    reason=stop_reason,
                    request_id=request_id,
                    metadata={"actor": actor},
                )
            elif state == "stopping":
                self._transition(
                    session,
                    to_state="stopped",
                    reason=stop_reason,
                    request_id=request_id,
                    metadata={"actor": actor},
                )
            else:
                raise ValueError(f"Unsupported voice session state: {state}")

            self._emit_for_session(
                session,
                event="voice_session_stopped",
                payload={
                    "session_id": normalized_id,
                    "user_id": str(session.get("user_id") or ""),
                    "state": str(session.get("state") or ""),
                    "reason": stop_reason,
                    "actor": actor,
                    "duration_ms": session.get("duration_ms"),
                    "request_id": request_id,
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
                raise ValueError(f"Voice session not found: {normalized_id}")
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
        if normalized_state is not None and normalized_state not in VOICE_SESSION_STATES:
            allowed = ", ".join(sorted(VOICE_SESSION_STATES))
            raise ValueError(f"Invalid voice session state '{state}'. Allowed values: {allowed}.")

        capped_limit = max(1, min(int(limit), 1000))
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
        request_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        from_state = str(session.get("state") or _INITIAL_STATE)
        target_state = str(to_state or "").strip().lower()
        if target_state not in VOICE_SESSION_STATES:
            raise ValueError(f"Invalid voice session transition target: {to_state}")

        allowed = _ALLOWED_TRANSITIONS.get(from_state, set())
        if target_state not in allowed:
            raise ValueError(
                f"Invalid voice session transition: {from_state} -> {target_state}"
            )

        now = _utcnow_iso()
        transition = {
            "from_state": None if from_state == _INITIAL_STATE else from_state,
            "to_state": target_state,
            "at": now,
            "reason": str(reason or "").strip() or "unspecified",
            "metadata": dict(metadata) if isinstance(metadata, dict) else {},
        }
        transitions = session.get("transitions")
        if not isinstance(transitions, list):
            transitions = []
            session["transitions"] = transitions
        transitions.append(transition)
        session["state"] = target_state
        session["updated_at"] = now
        if target_state == "stopped":
            session["stopped_at"] = now
            duration_ms = _duration_ms(
                started_at=str(session.get("started_at") or ""),
                ended_at=now,
            )
            if duration_ms is not None:
                session["duration_ms"] = duration_ms

        telemetry = session.get("telemetry")
        if not isinstance(telemetry, dict):
            telemetry = {}
            session["telemetry"] = telemetry
        telemetry["transition_count"] = len(transitions)
        telemetry["last_transition_at"] = now
        telemetry["last_state"] = target_state

        self._emit_for_session(
            session,
            event="voice_session_transition",
            payload={
                "session_id": str(session.get("id") or ""),
                "user_id": str(session.get("user_id") or ""),
                "from_state": transition["from_state"],
                "to_state": target_state,
                "reason": transition["reason"],
                "request_id": request_id,
            },
        )

    def _emit_for_session(
        self,
        session: dict[str, Any],
        *,
        event: str,
        payload: dict[str, Any],
    ) -> None:
        telemetry = session.get("telemetry")
        if not isinstance(telemetry, dict):
            telemetry = {}
            session["telemetry"] = telemetry
        telemetry["events_emitted"] = int(telemetry.get("events_emitted", 0)) + 1
        telemetry["last_event"] = str(event)
        self._emit(event=event, payload=payload)

    def _emit(self, *, event: str, payload: dict[str, Any]) -> None:
        emitter = self._telemetry_emitter
        if emitter is None:
            return
        try:
            emitter(str(event), dict(payload))
        except Exception:
            pass

    def _evict_stopped_if_needed(self) -> None:
        if len(self._sessions) < self._max_sessions:
            return
        stopped_sessions: list[tuple[str, str]] = []
        for session_id, session in self._sessions.items():
            if str(session.get("state") or "") != "stopped":
                continue
            stopped_sessions.append((session_id, str(session.get("updated_at") or "")))
        stopped_sessions.sort(key=lambda item: item[1])
        while len(self._sessions) >= self._max_sessions and stopped_sessions:
            victim_id, _ = stopped_sessions.pop(0)
            self._sessions.pop(victim_id, None)


def _optional_str(value: Any) -> str | None:
    text = str(value).strip() if value not in (None, "") else ""
    return text or None


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str) -> datetime | None:
    normalized = str(value or "").strip()
    if not normalized:
        return None
    if normalized.endswith("Z"):
        normalized = normalized[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _duration_ms(*, started_at: str, ended_at: str) -> int | None:
    start = _parse_iso(started_at)
    end = _parse_iso(ended_at)
    if start is None or end is None:
        return None
    delta_ms = int((end - start).total_seconds() * 1000.0)
    return max(0, delta_ms)


def _snapshot(session: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(session)
    if str(payload.get("state") or "") == _INITIAL_STATE:
        payload["state"] = "created"
    return payload
