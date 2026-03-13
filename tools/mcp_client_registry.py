from __future__ import annotations

import logging
import time
import re
from typing import Any
from urllib.parse import urlparse

import httpx

from tools.tool_registry import ToolRegistry


class MCPClientRegistry:
    def __init__(
        self,
        endpoints: list[str],
        timeout_sec: float = 10.0,
        failure_threshold: int = 2,
        quarantine_sec: float = 60.0,
    ) -> None:
        self.endpoints = [item.strip().rstrip("/") for item in endpoints if item.strip()]
        self.timeout_sec = max(1.0, timeout_sec)
        self.failure_threshold = max(1, int(failure_threshold))
        self.quarantine_sec = max(1.0, float(quarantine_sec))
        self.logger = logging.getLogger("amaryllis.tools.mcp_client_registry")
        self._health_by_endpoint: dict[str, dict[str, Any]] = {}

    def register_remote_tools(self, registry: ToolRegistry) -> int:
        total = 0
        for index, endpoint in enumerate(self.endpoints, start=1):
            alias = self._alias(endpoint=endpoint, index=index)
            total += self._register_from_endpoint(registry=registry, endpoint=endpoint, alias=alias)
        return total

    def _register_from_endpoint(self, registry: ToolRegistry, endpoint: str, alias: str) -> int:
        if self._is_quarantined(endpoint):
            self.logger.warning("mcp_discovery_skipped_quarantine endpoint=%s", endpoint)
            return 0
        try:
            response = httpx.get(f"{endpoint}/mcp/tools", timeout=self.timeout_sec)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            self.logger.error("mcp_discovery_failed endpoint=%s error=%s", endpoint, exc)
            self._record_failure(endpoint=endpoint, error=str(exc))
            return 0
        self._record_success(endpoint=endpoint)

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
            if self._is_quarantined(endpoint):
                raise RuntimeError(f"MCP endpoint is temporarily quarantined: {endpoint}")
            try:
                response = httpx.post(
                    f"{endpoint}/mcp/tools/{remote_name}/invoke",
                    json={"arguments": arguments},
                    timeout=self.timeout_sec,
                )
                response.raise_for_status()
                payload = response.json()
            except Exception as exc:
                self._record_failure(endpoint=endpoint, error=str(exc))
                raise
            self._record_success(endpoint=endpoint)
            if isinstance(payload, dict) and "result" in payload:
                return payload["result"]
            return payload

        return handler

    def debug_health(self) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        now = self._now()
        for endpoint in self.endpoints:
            state = self._state(endpoint)
            quarantined_until = float(state.get("quarantined_until", 0.0))
            is_quarantined = quarantined_until > now
            items.append(
                {
                    "endpoint": endpoint,
                    "is_quarantined": is_quarantined,
                    "quarantined_until_unix": quarantined_until if quarantined_until > 0 else None,
                    "cooldown_remaining_sec": round(max(0.0, quarantined_until - now), 3)
                    if is_quarantined
                    else 0.0,
                    "consecutive_failures": int(state.get("consecutive_failures", 0)),
                    "total_failures": int(state.get("total_failures", 0)),
                    "total_successes": int(state.get("total_successes", 0)),
                    "last_error": state.get("last_error"),
                    "last_failure_at_unix": state.get("last_failure_at"),
                    "last_success_at_unix": state.get("last_success_at"),
                }
            )
        return {
            "failure_threshold": self.failure_threshold,
            "quarantine_sec": self.quarantine_sec,
            "items": items,
            "count": len(items),
        }

    def _state(self, endpoint: str) -> dict[str, Any]:
        state = self._health_by_endpoint.get(endpoint)
        if state is not None:
            return state
        state = {
            "consecutive_failures": 0,
            "total_failures": 0,
            "total_successes": 0,
            "quarantined_until": 0.0,
            "last_error": None,
            "last_failure_at": None,
            "last_success_at": None,
        }
        self._health_by_endpoint[endpoint] = state
        return state

    def _is_quarantined(self, endpoint: str) -> bool:
        state = self._state(endpoint)
        until = float(state.get("quarantined_until", 0.0))
        now = self._now()
        if until <= now:
            if until > 0:
                state["quarantined_until"] = 0.0
                state["consecutive_failures"] = 0
            return False
        return True

    def _record_failure(self, *, endpoint: str, error: str) -> None:
        state = self._state(endpoint)
        now = self._now()
        state["consecutive_failures"] = int(state.get("consecutive_failures", 0)) + 1
        state["total_failures"] = int(state.get("total_failures", 0)) + 1
        state["last_error"] = str(error)
        state["last_failure_at"] = now
        if int(state.get("consecutive_failures", 0)) >= self.failure_threshold:
            state["quarantined_until"] = now + self.quarantine_sec

    def _record_success(self, *, endpoint: str) -> None:
        state = self._state(endpoint)
        state["consecutive_failures"] = 0
        state["total_successes"] = int(state.get("total_successes", 0)) + 1
        state["last_success_at"] = self._now()
        state["last_error"] = None
        state["quarantined_until"] = 0.0

    @staticmethod
    def _alias(endpoint: str, index: int) -> str:
        parsed = urlparse(endpoint)
        host = parsed.hostname or f"endpoint{index}"
        normalized = re.sub(r"[^a-zA-Z0-9]+", "_", host).strip("_").lower()
        return normalized or f"endpoint{index}"

    @staticmethod
    def _now() -> float:
        return time.time()
