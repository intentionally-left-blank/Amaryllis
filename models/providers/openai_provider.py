from __future__ import annotations

import json
from typing import Any, Iterator

import httpx


class OpenAIProvider:
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
                    "provider": "openai",
                    "active": model_id == self.active_model,
                    "metadata": item,
                }
            )
        return result

    def suggested_models(self, limit: int = 20) -> list[dict[str, str]]:
        return []

    def download_model(self, model_id: str) -> dict[str, Any]:
        raise RuntimeError(
            "OpenAI provider does not support local downloads. Use load with a remote model id."
        )

    def load_model(self, model_id: str) -> dict[str, Any]:
        self.active_model = model_id
        return {
            "status": "loaded",
            "provider": "openai",
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
            response = client.post(
                "/chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            response.raise_for_status()
            payload = response.json()

        choices = payload.get("choices", [])
        if not choices:
            return ""
        message = choices[0].get("message", {})
        return self._content_to_text(message.get("content"))

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> Iterator[str]:
        with httpx.Client(base_url=self.base_url, timeout=180.0, headers=self._headers()) as client:
            with client.stream(
                "POST",
                "/chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": True,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    if not line.startswith("data:"):
                        continue

                    payload = line[5:].strip()
                    if not payload:
                        continue
                    if payload == "[DONE]":
                        break

                    try:
                        chunk = json.loads(payload)
                    except json.JSONDecodeError:
                        continue

                    choices = chunk.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    content = delta.get("content")
                    text = self._content_to_text(content)
                    if text:
                        yield text

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

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
