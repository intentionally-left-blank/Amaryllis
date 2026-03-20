from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime


def canonical_api_path(path: str) -> str:
    normalized = str(path or "").strip()
    if normalized.startswith("/v1/"):
        return normalized[3:]
    if normalized == "/v1":
        return "/"
    return normalized


@dataclass(frozen=True)
class APILifecyclePolicy:
    version: str
    release_channel: str
    deprecation_sunset_days: int
    deprecation_doc_path: str = "/docs/api-lifecycle"
    legacy_prefixes: tuple[str, ...] = (
        "/models",
        "/agents",
        "/automations",
        "/inbox",
        "/tools",
        "/voice",
        "/mcp",
        "/debug",
    )

    def __post_init__(self) -> None:
        channel = str(self.release_channel or "").strip().lower()
        if channel not in {"alpha", "beta", "stable"}:
            object.__setattr__(self, "release_channel", "stable")
        sunset_days = max(1, int(self.deprecation_sunset_days))
        object.__setattr__(self, "deprecation_sunset_days", sunset_days)
        object.__setattr__(
            self,
            "_sunset_at",
            datetime.now(timezone.utc) + timedelta(days=sunset_days),
        )

    @property
    def sunset_at(self) -> datetime:
        return getattr(self, "_sunset_at")

    def is_versioned_path(self, path: str) -> bool:
        normalized = str(path or "").strip()
        return normalized == "/v1" or normalized.startswith("/v1/")

    def is_legacy_api_path(self, path: str) -> bool:
        normalized = str(path or "").strip()
        if not normalized:
            return False
        if self.is_versioned_path(normalized):
            return False
        if normalized in {"/health", "/health/providers"}:
            return False
        if normalized.startswith("/service/"):
            return False
        if normalized.startswith("/security/"):
            return False
        canonical = canonical_api_path(normalized)
        return any(canonical == prefix or canonical.startswith(prefix + "/") for prefix in self.legacy_prefixes)

    def response_headers(self, path: str) -> dict[str, str]:
        headers = {
            "X-Amaryllis-API-Version": self.version,
            "X-Amaryllis-Release-Channel": self.release_channel,
        }
        if self.is_legacy_api_path(path):
            headers["Deprecation"] = "true"
            headers["Sunset"] = format_datetime(self.sunset_at, usegmt=True)
            headers["Link"] = f"<{self.deprecation_doc_path}>; rel=\"deprecation\""
        return headers

    def describe(self) -> dict[str, object]:
        return {
            "version": self.version,
            "release_channel": self.release_channel,
            "deprecation": {
                "legacy_paths": list(self.legacy_prefixes),
                "sunset_at": self.sunset_at.isoformat(),
                "sunset_rfc7231": format_datetime(self.sunset_at, usegmt=True),
                "doc_path": self.deprecation_doc_path,
            },
        }
