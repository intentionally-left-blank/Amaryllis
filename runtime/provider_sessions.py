from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from typing import Any
from uuid import uuid4

from runtime.errors import NotFoundError, PermissionDeniedError, ValidationError
from storage.database import Database


SUPPORTED_PROVIDER_SESSION_PROVIDERS: tuple[str, ...] = ("openai", "anthropic", "openrouter", "reddit", "x")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_provider(provider: str) -> str:
    normalized = str(provider or "").strip().lower()
    if normalized not in set(SUPPORTED_PROVIDER_SESSION_PROVIDERS):
        raise ValidationError(
            "Unsupported provider. Allowed: " + ", ".join(SUPPORTED_PROVIDER_SESSION_PROVIDERS)
        )
    return normalized


def _credential_fingerprint(provider: str, credential_ref: str) -> str:
    payload = f"{provider}:{credential_ref}".encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    return digest[:24]


@dataclass
class ProviderSessionManager:
    database: Database

    def create_session(
        self,
        *,
        user_id: str,
        provider: str,
        credential_ref: str,
        scopes: list[str] | None = None,
        display_name: str | None = None,
        expires_at: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_user = str(user_id or "").strip()
        if not normalized_user:
            raise ValidationError("user_id is required")
        normalized_provider = _normalize_provider(provider)
        normalized_ref = str(credential_ref or "").strip()
        if not normalized_ref:
            raise ValidationError("credential_ref is required")

        normalized_scopes = sorted(
            {
                str(item or "").strip().lower()
                for item in (scopes or [])
                if str(item or "").strip()
            }
        )
        session_id = str(uuid4())
        fingerprint = _credential_fingerprint(normalized_provider, normalized_ref)
        now = _utc_now_iso()
        self.database.create_provider_session(
            session_id=session_id,
            user_id=normalized_user,
            provider=normalized_provider,
            credential_ref=normalized_ref,
            credential_fingerprint=fingerprint,
            scopes=normalized_scopes,
            display_name=(str(display_name).strip() if display_name not in (None, "") else None),
            created_at=now,
            updated_at=now,
            expires_at=(str(expires_at).strip() if expires_at not in (None, "") else None),
            metadata=metadata or {},
        )
        created = self.database.get_provider_session(session_id)
        if created is None:
            raise NotFoundError(f"Provider session not found after create: {session_id}")
        return created

    def list_sessions(
        self,
        *,
        user_id: str,
        provider: str | None = None,
        include_revoked: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        normalized_user = str(user_id or "").strip()
        if not normalized_user:
            raise ValidationError("user_id is required")
        normalized_provider = None
        if provider not in (None, ""):
            normalized_provider = _normalize_provider(str(provider))
        return self.database.list_provider_sessions(
            user_id=normalized_user,
            provider=normalized_provider,
            include_revoked=bool(include_revoked),
            limit=max(1, min(int(limit), 500)),
        )

    def get_session(self, session_id: str) -> dict[str, Any]:
        normalized_id = str(session_id or "").strip()
        if not normalized_id:
            raise ValidationError("session_id is required")
        item = self.database.get_provider_session(normalized_id)
        if item is None:
            raise NotFoundError(f"Provider session not found: {normalized_id}")
        return item

    def revoke_session(
        self,
        *,
        session_id: str,
        actor_user_id: str,
        is_admin: bool = False,
        reason: str | None = None,
    ) -> dict[str, Any]:
        normalized_id = str(session_id or "").strip()
        if not normalized_id:
            raise ValidationError("session_id is required")
        normalized_actor = str(actor_user_id or "").strip()
        if not normalized_actor:
            raise ValidationError("actor_user_id is required")
        item = self.database.get_provider_session(normalized_id)
        if item is None:
            raise NotFoundError(f"Provider session not found: {normalized_id}")
        owner_user_id = str(item.get("user_id") or "").strip()
        if not is_admin and owner_user_id != normalized_actor:
            raise PermissionDeniedError("Cross-user provider session revoke is not allowed")
        self.database.revoke_provider_session(
            session_id=normalized_id,
            revoked_reason=(str(reason).strip() if reason not in (None, "") else None),
        )
        updated = self.database.get_provider_session(normalized_id)
        if updated is None:
            raise NotFoundError(f"Provider session not found after revoke: {normalized_id}")
        return updated

