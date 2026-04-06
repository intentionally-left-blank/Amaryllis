from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "NewsIngestionPipeline",
    "build_query_bundle",
    "compose_grounded_digest",
    "NewsDigestOutboundDispatcher",
    "SUPPORTED_NEWS_OUTBOUND_CHANNELS",
]

_EXPORT_TO_MODULE_ATTR: dict[str, tuple[str, str]] = {
    "NewsIngestionPipeline": ("news.pipeline", "NewsIngestionPipeline"),
    "build_query_bundle": ("news.pipeline", "build_query_bundle"),
    "compose_grounded_digest": ("news.digest", "compose_grounded_digest"),
    "NewsDigestOutboundDispatcher": ("news.outbound", "NewsDigestOutboundDispatcher"),
    "SUPPORTED_NEWS_OUTBOUND_CHANNELS": ("news.outbound", "SUPPORTED_NEWS_OUTBOUND_CHANNELS"),
}


def __getattr__(name: str) -> Any:
    module_attr = _EXPORT_TO_MODULE_ATTR.get(name)
    if module_attr is None:
        raise AttributeError(f"module 'news' has no attribute {name!r}")
    module_name, attr_name = module_attr
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value
