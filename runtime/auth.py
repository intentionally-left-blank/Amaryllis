from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from runtime.errors import AuthenticationError, PermissionDeniedError


@dataclass(frozen=True)
class AuthTokenSpec:
    token: str
    user_id: str
    scopes: tuple[str, ...]


@dataclass(frozen=True)
class AuthContext:
    token_id: str
    user_id: str
    scopes: frozenset[str]

    def has_scope(self, scope: str) -> bool:
        normalized = str(scope or "").strip().lower()
        if not normalized:
            return False
        return normalized in self.scopes

    @property
    def is_admin(self) -> bool:
        return self.has_scope("admin")

    @property
    def is_service(self) -> bool:
        return self.has_scope("service")

    @property
    def is_user(self) -> bool:
        return self.has_scope("user")

    def has_any_scope(self, *scopes: str) -> bool:
        for scope in scopes:
            if self.has_scope(scope):
                return True
        return False


class AuthManager:
    def __init__(self, *, enabled: bool, token_specs: tuple[AuthTokenSpec, ...]) -> None:
        self.logger = logging.getLogger("amaryllis.auth")
        self.enabled = bool(enabled)
        self._contexts_by_token: dict[str, AuthContext] = {}

        for item in token_specs:
            token = str(item.token).strip()
            user_id = str(item.user_id).strip()
            raw_scopes = [str(scope).strip().lower() for scope in item.scopes]
            scopes = [scope for scope in raw_scopes if scope]
            if not token or not user_id:
                continue
            effective_scopes = frozenset(scopes or ["user"])
            self._contexts_by_token[token] = AuthContext(
                token_id=user_id,
                user_id=user_id,
                scopes=effective_scopes,
            )

        if self.enabled and not self._contexts_by_token:
            raise ValueError("Authentication is enabled, but no auth tokens are configured.")

    def authenticate_request(self, request: Any) -> AuthContext:
        if not self.enabled:
            return AuthContext(
                token_id="anonymous",
                user_id="anonymous",
                scopes=frozenset({"admin", "user"}),
            )

        token = self._extract_token(request)
        if not token:
            raise AuthenticationError("Missing bearer token")

        context = self._contexts_by_token.get(token)
        if context is None:
            raise AuthenticationError("Invalid bearer token")
        return context

    @staticmethod
    def _extract_token(request: Any) -> str:
        auth_header = str(request.headers.get("authorization", "")).strip()
        if auth_header.lower().startswith("bearer "):
            return auth_header[7:].strip()
        return str(request.headers.get("x-amaryllis-token", "")).strip()


def auth_context_from_request(request: Any) -> AuthContext:
    context = getattr(request.state, "auth_context", None)
    if isinstance(context, AuthContext):
        return context
    raise AuthenticationError("Authentication context is missing")


def require_admin(request: Any) -> AuthContext:
    context = auth_context_from_request(request)
    if context.is_admin:
        return context
    raise PermissionDeniedError("Admin scope is required")


def resolve_user_id(
    *,
    request_user_id: str | None,
    auth: AuthContext,
) -> str:
    normalized = str(request_user_id or "").strip()
    if auth.is_admin:
        return normalized or auth.user_id
    if normalized and normalized != auth.user_id:
        raise PermissionDeniedError("Cross-user access is not allowed")
    return auth.user_id


def assert_owner(
    *,
    owner_user_id: str | None,
    auth: AuthContext,
    resource_name: str,
    resource_id: str | None = None,
) -> None:
    if auth.is_admin:
        return

    owner = str(owner_user_id or "").strip()
    if not owner or owner != auth.user_id:
        suffix = f": {resource_id}" if resource_id else ""
        raise PermissionDeniedError(f"Access denied to {resource_name}{suffix}")


def auth_context_payload(context: AuthContext) -> dict[str, Any]:
    return {
        "user_id": context.user_id,
        "scopes": sorted(context.scopes),
        "is_admin": context.is_admin,
    }
