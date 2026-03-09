from __future__ import annotations

import json
from typing import Any, Iterator

import httpx


class OllamaProvider:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.active_model: str | None = None

    def list_models(self) -> list[dict[str, Any]]:
        with httpx.Client(base_url=self.base_url, timeout=20.0) as client:
            response = client.get("/api/tags")
            response.raise_for_status()
            payload = response.json()

        result: list[dict[str, Any]] = []
        for item in payload.get("models", []):
            model_name = item.get("name")
            if not model_name:
                continue
            result.append(
                {
                    "id": model_name,
                    "provider": "ollama",
                    "active": model_name == self.active_model,
                    "metadata": item,
                }
            )
        return result

    def download_model(self, model_id: str) -> dict[str, Any]:
        with httpx.Client(base_url=self.base_url, timeout=120.0) as client:
            response = client.post(
                "/api/pull",
                json={"name": model_id, "stream": False},
            )
            response.raise_for_status()

        return {
            "status": "downloaded",
            "provider": "ollama",
            "model": model_id,
        }

    def load_model(self, model_id: str) -> dict[str, Any]:
        with httpx.Client(base_url=self.base_url, timeout=20.0) as client:
            response = client.post("/api/show", json={"name": model_id})
            response.raise_for_status()

        self.active_model = model_id
        return {
            "status": "loaded",
            "provider": "ollama",
            "model": model_id,
        }

    def chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> str:
        with httpx.Client(base_url=self.base_url, timeout=120.0) as client:
            response = client.post(
                "/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": False,
                    "options": {
                        "temperature": temperature,
                        "num_predict": max_tokens,
                    },
                },
            )
            response.raise_for_status()
            payload = response.json()

        message = payload.get("message", {})
        content = message.get("content")
        if content is None:
            return ""
        return str(content)

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> Iterator[str]:
        with httpx.Client(base_url=self.base_url, timeout=120.0) as client:
            with client.stream(
                "POST",
                "/api/chat",
                json={
                    "model": model,
                    "messages": messages,
                    "stream": True,
                    "options": {
                        "temperature": temperature,
                        "num_predict": max_tokens,
                    },
                },
            ) as response:
                response.raise_for_status()
                for line in response.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    message = chunk.get("message", {})
                    content = message.get("content", "")
                    if content:
                        yield str(content)
                    if chunk.get("done"):
                        break
