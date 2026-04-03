from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from runtime.config import AppConfig
from runtime.provider_sessions import SUPPORTED_PROVIDER_SESSION_PROVIDERS
from storage.database import Database


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class EntitlementResolver:
    config: AppConfig
    database: Database

    def _server_key_available(self, provider: str) -> bool:
        if provider == "openai":
            return bool(self.config.openai_api_key)
        if provider == "anthropic":
            return bool(self.config.anthropic_api_key)
        if provider == "openrouter":
            return bool(self.config.openrouter_api_key)
        return False

    def resolve_provider(self, *, user_id: str, provider: str) -> dict[str, Any]:
        normalized_user = str(user_id or "").strip()
        normalized_provider = str(provider or "").strip().lower()
        if normalized_provider not in set(SUPPORTED_PROVIDER_SESSION_PROVIDERS):
            raise ValueError("Unsupported provider")

        sessions = self.database.list_provider_sessions(
            user_id=normalized_user,
            provider=normalized_provider,
            include_revoked=False,
            limit=50,
        )
        session_count = len(sessions)
        has_server_key = self._server_key_available(normalized_provider)
        available = bool(has_server_key or session_count > 0)

        access_mode = "none"
        if has_server_key:
            access_mode = "server_api_key"
        elif session_count > 0:
            access_mode = "user_session"

        allowed_models: list[str] = []
        if normalized_provider == "openai":
            allowed_models = [str(self.database.get_setting("openai_default_model", "gpt-4o-mini") or "gpt-4o-mini")]
        elif normalized_provider == "anthropic":
            allowed_models = [
                str(
                    self.database.get_setting("anthropic_default_model", "claude-3-5-sonnet-latest")
                    or "claude-3-5-sonnet-latest"
                )
            ]
        elif normalized_provider == "openrouter":
            allowed_models = [
                str(
                    self.database.get_setting("openrouter_default_model", "openai/gpt-4o-mini")
                    or "openai/gpt-4o-mini"
                )
            ]

        return {
            "provider": normalized_provider,
            "user_id": normalized_user,
            "available": available,
            "access_mode": access_mode,
            "session_count": session_count,
            "server_key_available": has_server_key,
            "allowed_models": allowed_models,
            "rate_tier": "standard" if available else "none",
            "feature_flags": {
                "chat": available,
                "automation_news": available,
                "passthrough": session_count > 0,
            },
            "checked_at": _utc_now_iso(),
        }

    def resolve_all(self, *, user_id: str) -> dict[str, Any]:
        items = [
            self.resolve_provider(user_id=user_id, provider=provider)
            for provider in SUPPORTED_PROVIDER_SESSION_PROVIDERS
        ]
        available = [item["provider"] for item in items if bool(item.get("available"))]
        return {
            "user_id": str(user_id or "").strip(),
            "providers": items,
            "available_providers": available,
            "count": len(items),
            "checked_at": _utc_now_iso(),
        }

