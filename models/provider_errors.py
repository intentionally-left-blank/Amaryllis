from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ProviderErrorClass = Literal[
    "rate_limit",
    "quota",
    "timeout",
    "auth",
    "entitlement",
    "invalid_request",
    "server",
    "network",
    "circuit_open",
    "budget_limit",
    "unavailable",
    "unknown",
]


@dataclass(frozen=True)
class ProviderErrorInfo:
    provider: str
    operation: str
    error_class: ProviderErrorClass
    message: str
    raw_message: str
    retryable: bool
    status_code: int | None = None


class ProviderOperationError(RuntimeError):
    def __init__(self, info: ProviderErrorInfo) -> None:
        self.info = info
        super().__init__(
            f"Provider '{info.provider}' {info.operation} failed [{info.error_class}]: {info.message}"
        )


def classify_provider_error(
    *,
    provider: str,
    operation: str,
    error: Exception,
) -> ProviderErrorInfo:
    if isinstance(error, ProviderOperationError):
        return error.info

    raw = str(error).strip()
    lowered = raw.lower()
    status_code = _extract_http_status(lowered)

    if "circuit is open" in lowered:
        return _build(
            provider=provider,
            operation=operation,
            error_class="circuit_open",
            raw=raw,
            status_code=status_code,
        )

    if "budget limit reached" in lowered:
        return _build(
            provider=provider,
            operation=operation,
            error_class="budget_limit",
            raw=raw,
            status_code=status_code,
        )

    if _contains_any(
        lowered,
        (
            "entitlement denied",
            "entitlement check requires user_id",
            "chat feature is disabled",
        ),
    ):
        return _build(
            provider=provider,
            operation=operation,
            error_class="entitlement",
            raw=raw,
            status_code=status_code,
        )

    if _contains_any(
        lowered,
        (
            "too many requests",
            "rate limit",
            "429",
            "throttl",
        ),
    ):
        return _build(
            provider=provider,
            operation=operation,
            error_class="rate_limit",
            raw=raw,
            status_code=status_code,
        )

    if _contains_any(
        lowered,
        (
            "insufficient_quota",
            "quota",
            "billing",
            "payment",
        ),
    ):
        return _build(
            provider=provider,
            operation=operation,
            error_class="quota",
            raw=raw,
            status_code=status_code,
        )

    if _contains_any(
        lowered,
        (
            "unauthorized",
            "forbidden",
            "invalid api key",
            "api key",
            "authentication",
            "401",
            "403",
        ),
    ):
        return _build(
            provider=provider,
            operation=operation,
            error_class="auth",
            raw=raw,
            status_code=status_code,
        )

    if _contains_any(
        lowered,
        (
            "invalid request",
            "bad request",
            "unprocessable",
            "400",
            "404",
            "422",
        ),
    ):
        return _build(
            provider=provider,
            operation=operation,
            error_class="invalid_request",
            raw=raw,
            status_code=status_code,
        )

    if _contains_any(
        lowered,
        (
            "timeout",
            "timed out",
            "deadline exceeded",
            "readtimeout",
            "writetimeout",
        ),
    ):
        return _build(
            provider=provider,
            operation=operation,
            error_class="timeout",
            raw=raw,
            status_code=status_code,
        )

    if _contains_any(
        lowered,
        (
            "connection",
            "network",
            "dns",
            "refused",
            "reset by peer",
            "name or service not known",
        ),
    ):
        return _build(
            provider=provider,
            operation=operation,
            error_class="network",
            raw=raw,
            status_code=status_code,
        )

    if _contains_any(
        lowered,
        (
            "service unavailable",
            "bad gateway",
            "gateway timeout",
            "internal server error",
            "500",
            "502",
            "503",
            "504",
        ),
    ):
        return _build(
            provider=provider,
            operation=operation,
            error_class="server",
            raw=raw,
            status_code=status_code,
        )

    if _contains_any(
        lowered,
        (
            "unavailable",
            "not available",
            "temporarily",
            "overloaded",
            "try again",
        ),
    ):
        return _build(
            provider=provider,
            operation=operation,
            error_class="unavailable",
            raw=raw,
            status_code=status_code,
        )

    return _build(
        provider=provider,
        operation=operation,
        error_class="unknown",
        raw=raw,
        status_code=status_code,
    )


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(item in text for item in needles)


def _extract_http_status(text: str) -> int | None:
    for token in (" 429", " 503", " 502", " 504", " 500", " 401", " 403", " 400", " 404", " 422"):
        if token in f" {text}":
            try:
                return int(token.strip())
            except Exception:
                return None
    return None


def _build(
    *,
    provider: str,
    operation: str,
    error_class: ProviderErrorClass,
    raw: str,
    status_code: int | None,
) -> ProviderErrorInfo:
    retryable = error_class in {
        "rate_limit",
        "timeout",
        "server",
        "network",
        "circuit_open",
        "budget_limit",
        "unavailable",
    }
    return ProviderErrorInfo(
        provider=provider,
        operation=operation,
        error_class=error_class,
        message=raw or "unknown provider error",
        raw_message=raw or "unknown provider error",
        retryable=retryable,
        status_code=status_code,
    )
