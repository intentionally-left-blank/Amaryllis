from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any
from uuid import uuid4


class ToolPermissionManager:
    def __init__(self, default_ttl_sec: int = 600) -> None:
        self._lock = Lock()
        self._prompts: dict[str, dict[str, Any]] = {}
        self.default_ttl_sec = max(1, int(default_ttl_sec))

    def request(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        reason: str,
        scope: str = "request",
        ttl_sec: int | None = None,
        request_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        prompt_id = str(uuid4())
        normalized_scope = self._normalize_scope(scope)
        scope_value = self._resolve_scope_value(
            scope=normalized_scope,
            request_id=request_id,
            user_id=user_id,
            session_id=session_id,
        )
        effective_ttl_sec = max(1, int(ttl_sec if ttl_sec is not None else self.default_ttl_sec))
        created_at = self._utc_now()
        expires_at = (self._parse_iso_datetime(created_at) + timedelta(seconds=effective_ttl_sec)).isoformat()
        payload = {
            "id": prompt_id,
            "status": "pending",
            "tool_name": tool_name,
            "arguments_hash": self._arguments_hash(arguments),
            "arguments_preview": arguments,
            "reason": reason,
            "scope": normalized_scope,
            "scope_value": scope_value,
            "ttl_sec": effective_ttl_sec,
            "request_id": request_id,
            "user_id": user_id,
            "session_id": session_id,
            "created_at": created_at,
            "updated_at": created_at,
            "expires_at": expires_at,
            "approved_at": None,
            "consumed_at": None,
        }
        with self._lock:
            self._prompts[prompt_id] = payload
        return payload

    def approve(self, prompt_id: str) -> dict[str, Any]:
        with self._lock:
            self._refresh_expired_locked()
            prompt = self._prompts.get(prompt_id)
            if not prompt:
                raise ValueError(f"Permission prompt not found: {prompt_id}")
            if str(prompt.get("status")) == "expired":
                raise ValueError(f"Permission prompt expired: {prompt_id}")
            prompt["status"] = "approved"
            prompt["updated_at"] = self._utc_now()
            prompt["approved_at"] = prompt["updated_at"]
            return dict(prompt)

    def deny(self, prompt_id: str) -> dict[str, Any]:
        with self._lock:
            self._refresh_expired_locked()
            prompt = self._prompts.get(prompt_id)
            if not prompt:
                raise ValueError(f"Permission prompt not found: {prompt_id}")
            prompt["status"] = "denied"
            prompt["updated_at"] = self._utc_now()
            return dict(prompt)

    def list(self, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        with self._lock:
            self._refresh_expired_locked()
            rows = list(self._prompts.values())
        rows.sort(key=lambda item: str(item.get("created_at", "")), reverse=True)
        if status:
            rows = [item for item in rows if str(item.get("status")) == status]
        return [dict(item) for item in rows[:limit]]

    def consume_if_approved(
        self,
        prompt_id: str,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        request_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> bool:
        with self._lock:
            self._refresh_expired_locked()
            prompt = self._prompts.get(prompt_id)
            if not prompt:
                return False
            if str(prompt.get("status")) != "approved":
                return False
            if str(prompt.get("tool_name")) != tool_name:
                return False
            if str(prompt.get("arguments_hash")) != self._arguments_hash(arguments):
                return False
            if not self._scope_matches(
                prompt=prompt,
                request_id=request_id,
                user_id=user_id,
                session_id=session_id,
            ):
                return False
            prompt["status"] = "consumed"
            prompt["updated_at"] = self._utc_now()
            prompt["consumed_at"] = prompt["updated_at"]
        return True

    @staticmethod
    def _arguments_hash(arguments: dict[str, Any]) -> str:
        encoded = json.dumps(arguments, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _parse_iso_datetime(value: str) -> datetime:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    @staticmethod
    def _normalize_scope(scope: str) -> str:
        normalized = str(scope or "").strip().lower()
        if normalized not in {"request", "session", "user", "global"}:
            return "request"
        return normalized

    @staticmethod
    def _resolve_scope_value(
        *,
        scope: str,
        request_id: str | None,
        user_id: str | None,
        session_id: str | None,
    ) -> str:
        if scope == "request":
            return str(request_id or "").strip() or "none"
        if scope == "session":
            return str(session_id or "").strip() or "none"
        if scope == "user":
            return str(user_id or "").strip() or "none"
        return "global"

    @staticmethod
    def _scope_matches(
        *,
        prompt: dict[str, Any],
        request_id: str | None,
        user_id: str | None,
        session_id: str | None,
    ) -> bool:
        scope = str(prompt.get("scope") or "request").strip().lower()
        expected_value = str(prompt.get("scope_value") or "none").strip()
        if scope == "global":
            return True
        actual_value = ToolPermissionManager._resolve_scope_value(
            scope=scope,
            request_id=request_id,
            user_id=user_id,
            session_id=session_id,
        )
        return actual_value == expected_value

    def _refresh_expired_locked(self) -> None:
        now = datetime.now(timezone.utc)
        for prompt in self._prompts.values():
            status = str(prompt.get("status") or "").strip().lower()
            if status not in {"pending", "approved"}:
                continue
            expires_raw = str(prompt.get("expires_at") or "").strip()
            if not expires_raw:
                continue
            try:
                expires_at = self._parse_iso_datetime(expires_raw)
            except Exception:
                continue
            if now >= expires_at:
                prompt["status"] = "expired"
                prompt["updated_at"] = self._utc_now()
