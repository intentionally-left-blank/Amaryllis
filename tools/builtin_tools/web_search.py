from __future__ import annotations

import html
import re
from typing import Any

import httpx

from tools.tool_registry import ToolRegistry

RESULT_PATTERN = re.compile(r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>')


def _web_search_handler(arguments: dict[str, Any]) -> dict[str, Any]:
    query = str(arguments.get("query", "")).strip()
    limit = int(arguments.get("limit", 5))

    if not query:
        raise ValueError("query is required")

    url = "https://duckduckgo.com/html/"
    response = httpx.get(url, params={"q": query}, timeout=15.0)
    response.raise_for_status()

    matches = RESULT_PATTERN.findall(response.text)
    results: list[dict[str, str]] = []

    for href, title_html in matches[: max(1, limit)]:
        title = html.unescape(re.sub(r"<[^>]+>", "", title_html)).strip()
        results.append({"title": title, "url": href})

    return {
        "query": query,
        "results": results,
    }


def register(registry: ToolRegistry) -> None:
    registry.register(
        name="web_search",
        description="Search the web for public information.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["query"],
        },
        handler=_web_search_handler,
    )
