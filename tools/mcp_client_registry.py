from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from tools.tool_registry import ToolRegistry


class MCPClientRegistry:
    def __init__(self, endpoints: list[str], timeout_sec: float = 10.0) -> None:
        self.endpoints = [item.strip().rstrip("/") for item in endpoints if item.strip()]
        self.timeout_sec = max(1.0, timeout_sec)
        self.logger = logging.getLogger("amaryllis.tools.mcp_client_registry")

    def register_remote_tools(self, registry: ToolRegistry) -> int:
        total = 0
        for index, endpoint in enumerate(self.endpoints, start=1):
            alias = self._alias(endpoint=endpoint, index=index)
            total += self._register_from_endpoint(registry=registry, endpoint=endpoint, alias=alias)
        return total

    def _register_from_endpoint(self, registry: ToolRegistry, endpoint: str, alias: str) -> int:
        try:
            response = httpx.get(f"{endpoint}/mcp/tools", timeout=self.timeout_sec)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            self.logger.error("mcp_discovery_failed endpoint=%s error=%s", endpoint, exc)
            return 0

        if isinstance(payload, dict):
            items = payload.get("items", [])
        elif isinstance(payload, list):
            items = payload
        else:
            items = []

        count = 0
        for item in items:
            if not isinstance(item, dict):
                continue
            remote_name = str(item.get("name", "")).strip()
            if not remote_name:
                continue
            local_name = f"mcp_{alias}_{remote_name}"
            description = str(item.get("description", f"Remote MCP tool {remote_name}"))
            input_schema = item.get("input_schema", {})
            if not isinstance(input_schema, dict):
                input_schema = {}

            registry.register(
                name=local_name,
                description=description,
                input_schema=input_schema,
                handler=self._proxy_handler(endpoint=endpoint, remote_name=remote_name),
                source=f"mcp:{endpoint}",
                risk_level=str(item.get("risk_level", "medium")),
                approval_mode=str(item.get("approval_mode", "none")),
                isolation="remote_proxy",
            )
            count += 1

        self.logger.info("mcp_discovery_done endpoint=%s alias=%s tools=%s", endpoint, alias, count)
        return count

    def _proxy_handler(self, endpoint: str, remote_name: str):
        def handler(arguments: dict[str, Any]) -> Any:
            response = httpx.post(
                f"{endpoint}/mcp/tools/{remote_name}/invoke",
                json={"arguments": arguments},
                timeout=self.timeout_sec,
            )
            response.raise_for_status()
            payload = response.json()
            if isinstance(payload, dict) and "result" in payload:
                return payload["result"]
            return payload

        return handler

    @staticmethod
    def _alias(endpoint: str, index: int) -> str:
        parsed = urlparse(endpoint)
        host = parsed.hostname or f"endpoint{index}"
        normalized = re.sub(r"[^a-zA-Z0-9]+", "_", host).strip("_").lower()
        return normalized or f"endpoint{index}"
