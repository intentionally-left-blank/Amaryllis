from __future__ import annotations

import json
import logging
from typing import Any, Iterator

import httpx

FALLBACK_OLLAMA_SUGGESTED_MODELS: list[str] = [
    "llama3.3",
    "llama3.2",
    "llama3.2:3b",
    "llama3.2:1b",
    "llama3.1",
    "llama3.1:70b",
    "qwen2.5",
    "qwen2.5:14b",
    "qwen2.5:7b",
    "qwen2.5:3b",
    "qwen2.5:1.5b",
    "qwen2.5-coder",
    "qwen2.5-coder:14b",
    "qwen2.5-coder:7b",
    "mistral",
    "mistral-nemo",
    "mixtral",
    "phi4",
    "phi3.5",
    "deepseek-r1",
    "deepseek-r1:32b",
    "deepseek-r1:14b",
    "deepseek-r1:8b",
    "deepseek-r1:7b",
    "deepseek-r1:1.5b",
    "deepseek-coder-v2",
    "codellama",
    "starcoder2",
    "gemma2",
    "gemma2:27b",
    "gemma2:9b",
    "gemma2:2b",
    "command-r",
    "command-r-plus",
    "nous-hermes2",
    "solar",
    "yi",
    "dolphin-mixtral",
    "tinyllama",
    "smollm2",
    "granite3.1-dense",
]


class OllamaProvider:
    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.active_model: str | None = None
        self.logger = logging.getLogger("amaryllis.models.ollama")

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

    def health_check(self) -> dict[str, Any]:
        with httpx.Client(base_url=self.base_url, timeout=2.5) as client:
            response = client.get("/api/tags")
            response.raise_for_status()
            payload = response.json()
        model_count = len(payload.get("models", []))
        return {
            "status": "ok",
            "detail": f"reachable=true models={model_count}",
        }

    def suggested_models(self, limit: int = 200) -> list[dict[str, str]]:
        suggestions: list[dict[str, str]] = []
        seen: set[str] = set()

        def add(model_id: str) -> None:
            normalized = model_id.strip()
            if not normalized or normalized in seen:
                return
            seen.add(normalized)
            suggestions.append(
                {
                    "id": normalized,
                    "label": self._label_from_model_id(normalized),
                }
            )

        try:
            for item in self.list_models():
                model_id = str(item.get("id", "")).strip()
                if model_id:
                    add(model_id)
        except Exception as exc:
            self.logger.warning("ollama_local_models_unavailable error=%s", exc)

        for model_id in FALLBACK_OLLAMA_SUGGESTED_MODELS:
            add(model_id)

        return suggestions[:limit]

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

    @staticmethod
    def _label_from_model_id(model_id: str) -> str:
        normalized = model_id.replace(":", " ").replace("-", " ").strip()
        pretty = " ".join(segment for segment in normalized.split() if segment)
        return pretty or model_id
