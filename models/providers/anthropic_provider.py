from __future__ import annotations

import json
from typing import Any, Iterator

import httpx

FALLBACK_ANTHROPIC_MODELS: list[str] = [
    "claude-3-5-haiku-latest",
    "claude-3-5-sonnet-latest",
    "claude-3-7-sonnet-latest",
]


class AnthropicProvider:
    def __init__(self, base_url: str, api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.active_model: str | None = None

    def list_models(self) -> list[dict[str, Any]]:
        if not self.api_key:
            return []

        with httpx.Client(base_url=self.base_url, timeout=20.0, headers=self._headers()) as client:
            response = client.get("/models")
            response.raise_for_status()
            payload = response.json()

        result: list[dict[str, Any]] = []
        for item in payload.get("data", []):
            model_id = item.get("id")
            if not model_id:
                continue
            result.append(
                {
                    "id": str(model_id),
                    "provider": "anthropic",
                    "active": model_id == self.active_model,
                    "metadata": item,
                }
            )
        return result

    def suggested_models(self, limit: int = 20) -> list[dict[str, str]]:
        suggestions: list[dict[str, str]] = []
        for model_id in FALLBACK_ANTHROPIC_MODELS[:limit]:
            suggestions.append({"id": model_id, "label": model_id})
        return suggestions

    def capabilities(self) -> dict[str, Any]:
        return {
            "local": False,
            "supports_download": False,
            "supports_load": True,
            "supports_stream": True,
            "supports_tools": False,
            "requires_api_key": True,
        }

    def health_check(self) -> dict[str, Any]:
        if not self.api_key:
            return {
                "status": "disabled",
                "detail": "API key is not configured",
            }

        with httpx.Client(base_url=self.base_url, timeout=8.0, headers=self._headers()) as client:
            response = client.get("/models")
            response.raise_for_status()

        return {
            "status": "ok",
            "detail": "reachable=true",
        }

    def download_model(self, model_id: str) -> dict[str, Any]:
        raise RuntimeError(
            "Anthropic provider does not support local downloads. Use load with a remote model id."
        )

    def load_model(self, model_id: str) -> dict[str, Any]:
        self.active_model = model_id
        return {
            "status": "loaded",
            "provider": "anthropic",
            "model": model_id,
        }

    def chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> str:
        system_prompt, normalized_messages = self._normalize_messages(messages)
        payload: dict[str, Any] = {
            "model": model,
            "messages": normalized_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_prompt:
            payload["system"] = system_prompt

        with httpx.Client(base_url=self.base_url, timeout=180.0, headers=self._headers()) as client:
            response = client.post("/messages", json=payload)
            response.raise_for_status()
            body = response.json()

        parts: list[str] = []
        for block in body.get("content", []):
            if not isinstance(block, dict):
                continue
            if block.get("type") != "text":
                continue
            text = block.get("text")
            if isinstance(text, str) and text:
                parts.append(text)
        return "".join(parts).strip()

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> Iterator[str]:
        system_prompt, normalized_messages = self._normalize_messages(messages)
        payload: dict[str, Any] = {
            "model": model,
            "messages": normalized_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True,
        }
        if system_prompt:
            payload["system"] = system_prompt

        with httpx.Client(base_url=self.base_url, timeout=180.0, headers=self._headers()) as client:
            emitted = False
            with client.stream("POST", "/messages", json=payload) as response:
                response.raise_for_status()

                for line in response.iter_lines():
                    if not line:
                        continue
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw:
                        continue
                    if raw == "[DONE]":
                        break

                    try:
                        chunk = json.loads(raw)
                    except json.JSONDecodeError:
                        continue

                    text = self._extract_text_delta(chunk)
                    if text:
                        emitted = True
                        yield text

            if not emitted:
                fallback = self.chat(
                    messages=messages,
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                if fallback:
                    yield fallback

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if self.api_key:
            headers["x-api-key"] = self.api_key
        return headers

    @staticmethod
    def _normalize_messages(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, str]]]:
        system_parts: list[str] = []
        normalized: list[dict[str, str]] = []
        for item in messages:
            role = str(item.get("role", "user")).strip().lower()
            content = str(item.get("content", ""))
            if role == "system":
                if content:
                    system_parts.append(content)
                continue
            if role not in {"user", "assistant"}:
                role = "user"
                content = f"[{item.get('role', 'message')}] {content}"
            normalized.append({"role": role, "content": content})

        system_prompt = "\n\n".join(part for part in system_parts if part.strip()) or None
        return system_prompt, normalized

    @staticmethod
    def _extract_text_delta(chunk: dict[str, Any]) -> str:
        if chunk.get("type") == "content_block_delta":
            delta = chunk.get("delta", {})
            if isinstance(delta, dict):
                text = delta.get("text")
                if isinstance(text, str):
                    return text

        delta = chunk.get("delta")
        if isinstance(delta, dict):
            text = delta.get("text")
            if isinstance(text, str):
                return text

        content_block = chunk.get("content_block")
        if isinstance(content_block, dict):
            text = content_block.get("text")
            if isinstance(text, str):
                return text

        return ""
