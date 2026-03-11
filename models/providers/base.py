from __future__ import annotations

from typing import Any, Iterator, Protocol


class ModelProvider(Protocol):
    active_model: str | None

    def list_models(self) -> list[dict[str, Any]]:
        ...

    def suggested_models(self, limit: int = 100) -> list[dict[str, str]]:
        ...

    def capabilities(self) -> dict[str, Any]:
        ...

    def health_check(self) -> dict[str, Any]:
        ...

    def download_model(self, model_id: str) -> dict[str, Any]:
        ...

    def load_model(self, model_id: str) -> dict[str, Any]:
        ...

    def chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> str:
        ...

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> Iterator[str]:
        ...
