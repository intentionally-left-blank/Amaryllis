from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from tools.tool_registry import ToolRegistry

SUPPORTED_BROWSER_ACTIONS: tuple[str, ...] = (
    "navigate",
    "click",
    "type",
    "press",
    "scroll",
    "wait",
    "extract",
    "screenshot",
)
MUTATING_BROWSER_ACTIONS: set[str] = {
    "navigate",
    "click",
    "type",
    "press",
    "scroll",
}
MAX_TIMEOUT_MS = 120_000
MAX_WAIT_MS = 60_000


@dataclass(frozen=True)
class BrowserActionRequest:
    action: str
    url: str | None = None
    selector: str | None = None
    text: str | None = None
    value: str | None = None
    keys: list[str] = field(default_factory=list)
    timeout_ms: int = 10_000
    wait_ms: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_arguments(cls, arguments: dict[str, Any]) -> "BrowserActionRequest":
        args = arguments if isinstance(arguments, dict) else {}
        action = str(args.get("action", "")).strip().lower()
        if action not in set(SUPPORTED_BROWSER_ACTIONS):
            allowed = ", ".join(SUPPORTED_BROWSER_ACTIONS)
            raise ValueError(f"Unsupported browser action '{action}'. Allowed values: {allowed}.")

        timeout_ms = _normalize_int(args.get("timeout_ms"), default=10_000, minimum=100, maximum=MAX_TIMEOUT_MS)
        wait_raw = args.get("wait_ms")
        wait_ms = None
        if wait_raw not in (None, ""):
            wait_ms = _normalize_int(wait_raw, default=500, minimum=0, maximum=MAX_WAIT_MS)

        keys: list[str] = []
        raw_keys = args.get("keys")
        if isinstance(raw_keys, list):
            keys = [str(item).strip() for item in raw_keys if str(item).strip()]
        elif raw_keys not in (None, ""):
            keys = [str(raw_keys).strip()]

        metadata_raw = args.get("metadata")
        if isinstance(metadata_raw, dict):
            metadata = dict(metadata_raw)
        else:
            metadata = {}

        return cls(
            action=action,
            url=_optional_str(args.get("url")),
            selector=_optional_str(args.get("selector")),
            text=_optional_str(args.get("text")),
            value=_optional_str(args.get("value")),
            keys=keys,
            timeout_ms=timeout_ms,
            wait_ms=wait_ms,
            metadata=metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "url": self.url,
            "selector": self.selector,
            "text": self.text,
            "value": self.value,
            "keys": list(self.keys),
            "timeout_ms": int(self.timeout_ms),
            "wait_ms": self.wait_ms,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class BrowserActionResult:
    ok: bool
    provider: str
    action: str
    status: str
    message: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "provider": str(self.provider),
            "action": str(self.action),
            "status": str(self.status),
            "message": self.message,
            "data": dict(self.data),
            "warnings": list(self.warnings),
            "metadata": dict(self.metadata),
        }


class BrowserActionAdapter(Protocol):
    def execute(self, request: BrowserActionRequest) -> BrowserActionResult:
        ...

    def describe(self) -> dict[str, Any]:
        ...


class StubBrowserActionAdapter:
    def __init__(self, provider_name: str = "stub-browser") -> None:
        self.provider_name = str(provider_name or "stub-browser").strip() or "stub-browser"

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "kind": "stub",
            "actions": list(SUPPORTED_BROWSER_ACTIONS),
            "supports_real_browser": False,
        }

    def execute(self, request: BrowserActionRequest) -> BrowserActionResult:
        return BrowserActionResult(
            ok=True,
            provider=self.provider_name,
            action=request.action,
            status="stubbed",
            message="Browser adapter stub executed. No real browser provider configured.",
            data={
                "echo": request.to_dict(),
                "capabilities": self.describe(),
            },
            warnings=[
                "browser_action is currently running in stub mode",
            ],
            metadata={"stub": True},
        )


def register_browser_action_tool(
    registry: ToolRegistry,
    adapter: BrowserActionAdapter,
    *,
    tool_name: str = "browser_action",
    replace_existing: bool = False,
) -> bool:
    if registry.get(tool_name) is not None and not replace_existing:
        return False

    def _handler(arguments: dict[str, Any]) -> dict[str, Any]:
        request = BrowserActionRequest.from_arguments(arguments or {})
        result = adapter.execute(request)
        payload = result.to_dict()
        payload["adapter"] = adapter.describe()
        payload["request"] = request.to_dict()
        return payload

    registry.register(
        name=tool_name,
        description=(
            "Typed browser action adapter tool (navigate/click/type/extract/screenshot) "
            "with provider-agnostic contract."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(SUPPORTED_BROWSER_ACTIONS),
                },
                "url": {"type": "string"},
                "selector": {"type": "string"},
                "text": {"type": "string"},
                "value": {"type": "string"},
                "keys": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "timeout_ms": {"type": "integer", "minimum": 100, "maximum": MAX_TIMEOUT_MS},
                "wait_ms": {"type": "integer", "minimum": 0, "maximum": MAX_WAIT_MS},
                "metadata": {"type": "object", "additionalProperties": True},
            },
            "required": ["action"],
            "additionalProperties": True,
        },
        handler=_handler,
        source="local",
        risk_level="medium",
        approval_mode="conditional",
        approval_predicate=lambda args: str(args.get("action", "")).strip().lower() in MUTATING_BROWSER_ACTIONS,
        isolation="network_readonly",
    )
    return True


def _optional_str(value: Any) -> str | None:
    normalized = str(value).strip() if value not in (None, "") else ""
    return normalized or None


def _normalize_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed
