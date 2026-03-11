from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from threading import Lock
from typing import Any
from uuid import uuid4


class ToolPermissionManager:
    def __init__(self) -> None:
        self._lock = Lock()
        self._prompts: dict[str, dict[str, Any]] = {}

    def request(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        reason: str,
        request_id: str | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        prompt_id = str(uuid4())
        payload = {
            "id": prompt_id,
            "status": "pending",
            "tool_name": tool_name,
            "arguments_hash": self._arguments_hash(arguments),
            "arguments_preview": arguments,
            "reason": reason,
            "request_id": request_id,
            "user_id": user_id,
            "session_id": session_id,
            "created_at": self._utc_now(),
            "updated_at": self._utc_now(),
        }
        with self._lock:
            self._prompts[prompt_id] = payload
        return payload

    def approve(self, prompt_id: str) -> dict[str, Any]:
        with self._lock:
            prompt = self._prompts.get(prompt_id)
            if not prompt:
                raise ValueError(f"Permission prompt not found: {prompt_id}")
            prompt["status"] = "approved"
            prompt["updated_at"] = self._utc_now()
            return dict(prompt)

    def deny(self, prompt_id: str) -> dict[str, Any]:
        with self._lock:
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
    ) -> bool:
        with self._lock:
            prompt = self._prompts.get(prompt_id)
            if not prompt:
                return False
            if str(prompt.get("status")) != "approved":
                return False
            if str(prompt.get("tool_name")) != tool_name:
                return False
            if str(prompt.get("arguments_hash")) != self._arguments_hash(arguments):
                return False
            prompt["status"] = "consumed"
            prompt["updated_at"] = self._utc_now()
        return True

    @staticmethod
    def _arguments_hash(arguments: dict[str, Any]) -> str:
        encoded = json.dumps(arguments, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()
