from __future__ import annotations

import json
from typing import Any, Iterator

import httpx


class OpenRouterProvider:
    def __init__(self, base_url: str, api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.active_model: str | None = None

    def list_models(self) -> list[dict[str, Any]]:
        with httpx.Client(base_url=self.base_url, timeout=30.0, headers=self._headers()) as client:
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
                    "provider": "openrouter",
                    "active": model_id == self.active_model,
                    "metadata": item,
                }
            )
        return result

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

    def suggested_models(self, limit: int = 20) -> list[dict[str, str]]:
        return []

    def download_model(self, model_id: str) -> dict[str, Any]:
        raise RuntimeError(
            "OpenRouter provider does not support local downloads. Use load with a remote model id."
        )

    def load_model(self, model_id: str) -> dict[str, Any]:
        self.active_model = model_id
        return {
            "status": "loaded",
            "provider": "openrouter",
            "model": model_id,
        }

    def chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> str:
        with httpx.Client(base_url=self.base_url, timeout=180.0, headers=self._headers()) as client:
            last_error: str | None = None
            for payload in self._payload_variants(
                messages=messages,
                model=model,
                stream=False,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                try:
                    response = client.post("/chat/completions", json=payload)
                    response.raise_for_status()
                    body = response.json()

                    choices = body.get("choices", [])
                    if not choices:
                        return ""
                    message = choices[0].get("message", {})
                    return self._content_to_text(message.get("content"))
                except httpx.HTTPStatusError as exc:
                    last_error = self._error_message(exc.response)
                    continue

            raise RuntimeError(last_error or "OpenRouter request failed")

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> Iterator[str]:
        with httpx.Client(base_url=self.base_url, timeout=180.0, headers=self._headers()) as client:
            last_error: str | None = None
            for payload in self._payload_variants(
                messages=messages,
                model=model,
                stream=True,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                try:
                    emitted = False
                    with client.stream("POST", "/chat/completions", json=payload) as response:
                        response.raise_for_status()
                        for line in response.iter_lines():
                            if not line:
                                continue
                            if not line.startswith("data:"):
                                continue

                            body = line[5:].strip()
                            if not body:
                                continue
                            if body == "[DONE]":
                                break

                            try:
                                chunk = json.loads(body)
                            except json.JSONDecodeError:
                                continue

                            choices = chunk.get("choices", [])
                            if not choices:
                                continue
                            delta = choices[0].get("delta", {})
                            content = delta.get("content")
                            text = self._content_to_text(content)
                            if text:
                                emitted = True
                                yield text

                    if not emitted:
                        fallback_text = self.chat(
                            messages=messages,
                            model=model,
                            temperature=temperature,
                            max_tokens=max_tokens,
                        )
                        if fallback_text:
                            yield fallback_text
                    return
                except httpx.HTTPStatusError as exc:
                    last_error = self._error_message(exc.response)
                    continue

            raise RuntimeError(last_error or "OpenRouter streaming request failed")

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _payload_variants(
        messages: list[dict[str, Any]],
        model: str,
        stream: bool,
        temperature: float,
        max_tokens: int,
    ) -> list[dict[str, Any]]:
        base = {"model": model, "messages": messages, "stream": stream}
        candidates = [
            {**base, "temperature": temperature, "max_tokens": max_tokens},
            {**base, "max_tokens": max_tokens},
            {**base, "max_completion_tokens": max_tokens},
            base,
        ]

        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for payload in candidates:
            key = json.dumps(payload, ensure_ascii=False, sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            unique.append(payload)
        return unique

    @staticmethod
    def _error_message(response: httpx.Response) -> str:
        try:
            payload = response.json()
            error = payload.get("error", {})
            if isinstance(error, dict):
                message = error.get("message")
                if isinstance(message, str) and message.strip():
                    return message
        except Exception:
            pass

        text = response.text.strip()
        if text:
            return text
        return f"HTTP {response.status_code}"

    @staticmethod
    def _content_to_text(content: Any) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts)
        return str(content)
