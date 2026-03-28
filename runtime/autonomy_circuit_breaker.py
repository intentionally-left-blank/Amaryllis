from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from threading import Lock
from typing import Any

SUPPORTED_CIRCUIT_BREAKER_SCOPE_TYPES: tuple[str, ...] = ("global", "user", "agent")
CIRCUIT_BREAKER_STATE_SCHEMA_VERSION = 1
FAIL_SAFE_RECOVERY_REASON = "state_recovery_failed"
FAIL_SAFE_RECOVERY_REQUEST_ID = "startup-state-recovery"
FAIL_SAFE_RECOVERY_ACTOR = "svc-runtime"


@dataclass(frozen=True)
class AutonomyCircuitScope:
    key: str
    scope_type: str
    scope_user_id: str | None
    scope_agent_id: str | None
    armed_at: str
    armed_by: str | None
    reason: str | None
    request_id: str | None
    revision: int


class AutonomyCircuitBreaker:
    """Thread-safe emergency brake for autonomous run creation."""

    def __init__(
        self,
        *,
        state_path: str | Path | None = None,
    ) -> None:
        now = _utc_now_iso()
        self._lock = Lock()
        self._state_path = _normalize_state_path(state_path)
        self._restore_status = "disabled" if self._state_path is None else "empty"
        self._restore_error: str | None = None
        self._active_scopes: dict[str, AutonomyCircuitScope] = {}
        self._revision = 0
        self._updated_at = now
        self._armed_by: str | None = None
        self._disarmed_at: str | None = now
        self._disarmed_by: str | None = None
        self._reason: str | None = None
        self._request_id: str | None = None
        if self._state_path is not None:
            self._restore_from_state_file()

    def is_armed(self) -> bool:
        with self._lock:
            return bool(self._active_scopes)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_unlocked()

    def evaluate_run_creation(
        self,
        *,
        user_id: str | None,
        agent_id: str | None,
    ) -> dict[str, Any]:
        normalized_user_id = _normalize_optional(user_id)
        normalized_agent_id = _normalize_optional(agent_id)
        with self._lock:
            matched = self._matched_scopes_unlocked(
                user_id=normalized_user_id,
                agent_id=normalized_agent_id,
            )
            return {
                "blocked": bool(matched),
                "matched_scope_count": len(matched),
                "matched_scopes": [_scope_to_payload(item) for item in matched],
                "active_scope_count": len(self._active_scopes),
                "revision": self._revision,
                "updated_at": self._updated_at,
            }

    def arm(
        self,
        *,
        actor: str | None = None,
        reason: str | None = None,
        request_id: str | None = None,
        scope_type: str = "global",
        scope_user_id: str | None = None,
        scope_agent_id: str | None = None,
    ) -> dict[str, Any]:
        (
            normalized_scope_type,
            normalized_scope_user_id,
            normalized_scope_agent_id,
        ) = normalize_circuit_breaker_scope(
            scope_type=scope_type,
            scope_user_id=scope_user_id,
            scope_agent_id=scope_agent_id,
        )
        key = circuit_breaker_scope_key(
            scope_type=normalized_scope_type,
            scope_user_id=normalized_scope_user_id,
            scope_agent_id=normalized_scope_agent_id,
        )
        normalized_actor = _normalize_optional(actor)
        normalized_reason = _normalize_optional(reason)
        normalized_request_id = _normalize_optional(request_id)
        now = _utc_now_iso()

        with self._lock:
            previous_state = self._capture_state_unlocked()
            self._revision += 1
            previous = self._active_scopes.get(key)
            armed_at = previous.armed_at if previous is not None else now
            active_scope = AutonomyCircuitScope(
                key=key,
                scope_type=normalized_scope_type,
                scope_user_id=normalized_scope_user_id,
                scope_agent_id=normalized_scope_agent_id,
                armed_at=armed_at,
                armed_by=normalized_actor,
                reason=normalized_reason,
                request_id=normalized_request_id,
                revision=self._revision,
            )
            self._active_scopes[key] = active_scope
            self._updated_at = now
            self._armed_by = normalized_actor
            self._reason = normalized_reason
            self._request_id = normalized_request_id
            self._persist_or_revert_unlocked(previous_state)
            snapshot = self._snapshot_unlocked()

        snapshot["target_scope"] = {
            "action": "arm",
            "exists": True,
            "scope": _scope_to_payload(active_scope),
        }
        return snapshot

    def disarm(
        self,
        *,
        actor: str | None = None,
        reason: str | None = None,
        request_id: str | None = None,
        scope_type: str = "global",
        scope_user_id: str | None = None,
        scope_agent_id: str | None = None,
    ) -> dict[str, Any]:
        (
            normalized_scope_type,
            normalized_scope_user_id,
            normalized_scope_agent_id,
        ) = normalize_circuit_breaker_scope(
            scope_type=scope_type,
            scope_user_id=scope_user_id,
            scope_agent_id=scope_agent_id,
        )
        key = circuit_breaker_scope_key(
            scope_type=normalized_scope_type,
            scope_user_id=normalized_scope_user_id,
            scope_agent_id=normalized_scope_agent_id,
        )
        normalized_actor = _normalize_optional(actor)
        normalized_reason = _normalize_optional(reason)
        normalized_request_id = _normalize_optional(request_id)
        now = _utc_now_iso()

        with self._lock:
            previous_state = self._capture_state_unlocked()
            removed = self._active_scopes.pop(key, None)
            self._revision += 1
            self._updated_at = now
            self._disarmed_at = now
            self._disarmed_by = normalized_actor
            self._reason = normalized_reason
            self._request_id = normalized_request_id
            self._persist_or_revert_unlocked(previous_state)
            snapshot = self._snapshot_unlocked()

        snapshot["target_scope"] = {
            "action": "disarm",
            "exists": removed is not None,
            "scope": _scope_to_payload(removed) if removed is not None else {
                "key": key,
                "scope_type": normalized_scope_type,
                "scope_user_id": normalized_scope_user_id,
                "scope_agent_id": normalized_scope_agent_id,
            },
        }
        return snapshot

    def _matched_scopes_unlocked(
        self,
        *,
        user_id: str | None,
        agent_id: str | None,
    ) -> list[AutonomyCircuitScope]:
        matched: list[AutonomyCircuitScope] = []
        for key in sorted(self._active_scopes):
            scope = self._active_scopes[key]
            if scope.scope_type == "global":
                matched.append(scope)
                continue
            if scope.scope_type == "user" and scope.scope_user_id == user_id:
                matched.append(scope)
                continue
            if scope.scope_type == "agent" and scope.scope_agent_id == agent_id:
                matched.append(scope)
        return matched

    def _snapshot_unlocked(self) -> dict[str, Any]:
        active_items = [_scope_to_payload(self._active_scopes[key]) for key in sorted(self._active_scopes)]
        armed = bool(active_items)
        armed_at: str | None = None
        if armed:
            armed_at = min(str(item.get("armed_at") or "") for item in active_items if str(item.get("armed_at") or ""))
        return {
            "status": "armed" if armed else "disarmed",
            "armed": armed,
            "revision": int(self._revision),
            "updated_at": self._updated_at,
            "armed_at": armed_at,
            "armed_by": self._armed_by,
            "disarmed_at": self._disarmed_at,
            "disarmed_by": self._disarmed_by,
            "reason": self._reason,
            "request_id": self._request_id,
            "active_scope_count": len(active_items),
            "active_scopes": active_items,
            "persistence": {
                "enabled": self._state_path is not None,
                "state_path": str(self._state_path) if self._state_path is not None else None,
                "schema_version": CIRCUIT_BREAKER_STATE_SCHEMA_VERSION,
                "restore_status": self._restore_status,
                "restore_error": self._restore_error,
            },
        }

    def _persist_or_revert_unlocked(self, previous_state: dict[str, Any]) -> None:
        try:
            self._persist_state_unlocked()
        except Exception as exc:  # pragma: no cover - exercised through API integration paths
            self._restore_state_unlocked(previous_state)
            raise RuntimeError(f"Failed to persist autonomy circuit breaker state: {exc}") from exc

    def _persist_state_unlocked(self) -> None:
        if self._state_path is None:
            return
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": CIRCUIT_BREAKER_STATE_SCHEMA_VERSION,
            "saved_at": _utc_now_iso(),
            "revision": int(self._revision),
            "updated_at": self._updated_at,
            "armed_by": self._armed_by,
            "disarmed_at": self._disarmed_at,
            "disarmed_by": self._disarmed_by,
            "reason": self._reason,
            "request_id": self._request_id,
            "active_scopes": [_scope_to_payload(self._active_scopes[key]) for key in sorted(self._active_scopes)],
        }
        temp_path = self._state_path.with_suffix(f"{self._state_path.suffix}.tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp_path, self._state_path)

    def _restore_from_state_file(self) -> None:
        assert self._state_path is not None
        if not self._state_path.exists():
            self._restore_status = "empty"
            self._restore_error = None
            return

        now = _utc_now_iso()
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._restore_from_payload_unlocked(payload=raw, fallback_now=now)
            self._restore_status = "restored"
            self._restore_error = None
        except Exception as exc:  # pragma: no cover - deterministic in unit test
            self._active_scopes = {
                "global": AutonomyCircuitScope(
                    key="global",
                    scope_type="global",
                    scope_user_id=None,
                    scope_agent_id=None,
                    armed_at=now,
                    armed_by=FAIL_SAFE_RECOVERY_ACTOR,
                    reason=FAIL_SAFE_RECOVERY_REASON,
                    request_id=FAIL_SAFE_RECOVERY_REQUEST_ID,
                    revision=max(1, int(self._revision) + 1),
                )
            }
            self._revision = max(1, int(self._revision) + 1)
            self._updated_at = now
            self._armed_by = FAIL_SAFE_RECOVERY_ACTOR
            self._disarmed_at = None
            self._disarmed_by = None
            self._reason = FAIL_SAFE_RECOVERY_REASON
            self._request_id = FAIL_SAFE_RECOVERY_REQUEST_ID
            self._restore_status = "fail_safe_armed"
            self._restore_error = f"{type(exc).__name__}: {exc}"
            try:
                self._persist_state_unlocked()
            except Exception:
                pass

    def _restore_from_payload_unlocked(self, *, payload: dict[str, Any], fallback_now: str) -> None:
        if not isinstance(payload, dict):
            raise ValueError("state payload must be an object")

        schema_version = int(payload.get("schema_version", CIRCUIT_BREAKER_STATE_SCHEMA_VERSION))
        if schema_version != CIRCUIT_BREAKER_STATE_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported state schema version: {schema_version} (expected {CIRCUIT_BREAKER_STATE_SCHEMA_VERSION})"
            )

        restored_revision = _coerce_non_negative_int(payload.get("revision"), fallback=0)
        restored_updated_at = _normalize_timestamp(payload.get("updated_at"), fallback=fallback_now)
        restored_armed_by = _normalize_optional(payload.get("armed_by"))
        restored_disarmed_at = _normalize_timestamp(payload.get("disarmed_at"), fallback=None)
        restored_disarmed_by = _normalize_optional(payload.get("disarmed_by"))
        restored_reason = _normalize_optional(payload.get("reason"))
        restored_request_id = _normalize_optional(payload.get("request_id"))

        raw_scopes = payload.get("active_scopes")
        if raw_scopes is None:
            raw_scopes = []
        if not isinstance(raw_scopes, list):
            raise ValueError("active_scopes must be an array")

        restored_scopes: dict[str, AutonomyCircuitScope] = {}
        for item in raw_scopes:
            if not isinstance(item, dict):
                continue
            scope = _scope_from_payload(
                payload=item,
                fallback_revision=restored_revision,
                fallback_armed_at=restored_updated_at,
            )
            restored_scopes[scope.key] = scope

        max_scope_revision = max((scope.revision for scope in restored_scopes.values()), default=restored_revision)
        self._active_scopes = restored_scopes
        self._revision = max(restored_revision, max_scope_revision)
        self._updated_at = restored_updated_at
        self._armed_by = restored_armed_by
        self._disarmed_at = restored_disarmed_at
        self._disarmed_by = restored_disarmed_by
        self._reason = restored_reason
        self._request_id = restored_request_id

    def _capture_state_unlocked(self) -> dict[str, Any]:
        return {
            "active_scopes": dict(self._active_scopes),
            "revision": int(self._revision),
            "updated_at": self._updated_at,
            "armed_by": self._armed_by,
            "disarmed_at": self._disarmed_at,
            "disarmed_by": self._disarmed_by,
            "reason": self._reason,
            "request_id": self._request_id,
            "restore_status": self._restore_status,
            "restore_error": self._restore_error,
        }

    def _restore_state_unlocked(self, state: dict[str, Any]) -> None:
        self._active_scopes = dict(state.get("active_scopes") or {})
        self._revision = int(state.get("revision", 0))
        self._updated_at = str(state.get("updated_at") or _utc_now_iso())
        self._armed_by = _normalize_optional(state.get("armed_by"))
        self._disarmed_at = _normalize_optional(state.get("disarmed_at"))
        self._disarmed_by = _normalize_optional(state.get("disarmed_by"))
        self._reason = _normalize_optional(state.get("reason"))
        self._request_id = _normalize_optional(state.get("request_id"))
        self._restore_status = str(state.get("restore_status") or self._restore_status)
        self._restore_error = _normalize_optional(state.get("restore_error"))


def normalize_circuit_breaker_scope(
    *,
    scope_type: str | None,
    scope_user_id: str | None = None,
    scope_agent_id: str | None = None,
) -> tuple[str, str | None, str | None]:
    normalized_scope_type = str(scope_type or "global").strip().lower() or "global"
    normalized_scope_user_id = _normalize_optional(scope_user_id)
    normalized_scope_agent_id = _normalize_optional(scope_agent_id)

    if normalized_scope_type not in SUPPORTED_CIRCUIT_BREAKER_SCOPE_TYPES:
        raise ValueError(
            "scope_type must be one of: " + ", ".join(SUPPORTED_CIRCUIT_BREAKER_SCOPE_TYPES)
        )

    if normalized_scope_type == "global":
        if normalized_scope_user_id is not None or normalized_scope_agent_id is not None:
            raise ValueError("scope_user_id/scope_agent_id are not allowed when scope_type=global")
        return "global", None, None

    if normalized_scope_type == "user":
        if normalized_scope_user_id is None:
            raise ValueError("scope_user_id is required when scope_type=user")
        if normalized_scope_agent_id is not None:
            raise ValueError("scope_agent_id is not allowed when scope_type=user")
        return "user", normalized_scope_user_id, None

    if normalized_scope_agent_id is None:
        raise ValueError("scope_agent_id is required when scope_type=agent")
    if normalized_scope_user_id is not None:
        raise ValueError("scope_user_id is not allowed when scope_type=agent")
    return "agent", None, normalized_scope_agent_id


def circuit_breaker_scope_key(
    *,
    scope_type: str,
    scope_user_id: str | None = None,
    scope_agent_id: str | None = None,
) -> str:
    normalized_scope_type, normalized_scope_user_id, normalized_scope_agent_id = normalize_circuit_breaker_scope(
        scope_type=scope_type,
        scope_user_id=scope_user_id,
        scope_agent_id=scope_agent_id,
    )
    if normalized_scope_type == "global":
        return "global"
    if normalized_scope_type == "user":
        return f"user:{normalized_scope_user_id}"
    return f"agent:{normalized_scope_agent_id}"


def _scope_to_payload(scope: AutonomyCircuitScope) -> dict[str, Any]:
    return {
        "key": scope.key,
        "scope_type": scope.scope_type,
        "scope_user_id": scope.scope_user_id,
        "scope_agent_id": scope.scope_agent_id,
        "armed_at": scope.armed_at,
        "armed_by": scope.armed_by,
        "reason": scope.reason,
        "request_id": scope.request_id,
        "revision": scope.revision,
    }


def _normalize_optional(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _normalize_state_path(path: str | Path | None) -> Path | None:
    if path is None:
        return None
    normalized = Path(str(path)).expanduser()
    return normalized


def _coerce_non_negative_int(value: Any, *, fallback: int = 0) -> int:
    try:
        return max(0, int(value))
    except Exception:
        return max(0, int(fallback))


def _normalize_timestamp(value: Any, *, fallback: str | None) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return fallback
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return fallback
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _scope_from_payload(
    *,
    payload: dict[str, Any],
    fallback_revision: int,
    fallback_armed_at: str,
) -> AutonomyCircuitScope:
    scope_type, scope_user_id, scope_agent_id = normalize_circuit_breaker_scope(
        scope_type=str(payload.get("scope_type") or "global"),
        scope_user_id=_normalize_optional(payload.get("scope_user_id")),
        scope_agent_id=_normalize_optional(payload.get("scope_agent_id")),
    )
    key = circuit_breaker_scope_key(
        scope_type=scope_type,
        scope_user_id=scope_user_id,
        scope_agent_id=scope_agent_id,
    )
    return AutonomyCircuitScope(
        key=key,
        scope_type=scope_type,
        scope_user_id=scope_user_id,
        scope_agent_id=scope_agent_id,
        armed_at=_normalize_timestamp(payload.get("armed_at"), fallback=fallback_armed_at) or fallback_armed_at,
        armed_by=_normalize_optional(payload.get("armed_by")),
        reason=_normalize_optional(payload.get("reason")),
        request_id=_normalize_optional(payload.get("request_id")),
        revision=_coerce_non_negative_int(payload.get("revision"), fallback=fallback_revision),
    )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
