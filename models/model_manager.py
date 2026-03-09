from __future__ import annotations

import logging
from typing import Any, Iterator

from models.providers.mlx_provider import MLXProvider
from models.providers.ollama_provider import OllamaProvider
from runtime.config import AppConfig
from storage.database import Database


class ModelManager:
    def __init__(self, config: AppConfig, database: Database) -> None:
        self.config = config
        self.database = database
        self.logger = logging.getLogger("amaryllis.models.manager")

        self.providers = {
            "mlx": MLXProvider(config.models_dir),
            "ollama": OllamaProvider(config.ollama_base_url),
        }

        self.active_provider = database.get_setting("active_provider", config.default_provider) or config.default_provider
        self.active_model = database.get_setting("active_model", config.default_model) or config.default_model

    def list_models(self) -> dict[str, Any]:
        provider_payload: dict[str, Any] = {}

        for name, provider in self.providers.items():
            try:
                provider_payload[name] = {
                    "available": True,
                    "error": None,
                    "items": provider.list_models(),
                }
            except Exception as exc:
                provider_payload[name] = {
                    "available": False,
                    "error": str(exc),
                    "items": [],
                }

        return {
            "active": {
                "provider": self.active_provider,
                "model": self.active_model,
            },
            "providers": provider_payload,
        }

    def download_model(self, model_id: str, provider: str | None = None) -> dict[str, Any]:
        provider_name = provider or self.active_provider
        selected = self.providers.get(provider_name)
        if selected is None:
            raise ValueError(f"Unknown provider: {provider_name}")

        result = selected.download_model(model_id)
        return result

    def load_model(self, model_id: str, provider: str | None = None) -> dict[str, Any]:
        provider_name = provider or self.active_provider
        selected = self.providers.get(provider_name)
        if selected is None:
            raise ValueError(f"Unknown provider: {provider_name}")

        result = selected.load_model(model_id)
        self.active_provider = provider_name
        self.active_model = model_id

        self.database.set_setting("active_provider", provider_name)
        self.database.set_setting("active_model", model_id)

        return {
            **result,
            "active": {
                "provider": self.active_provider,
                "model": self.active_model,
            },
        }

    def chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        provider: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> dict[str, Any]:
        provider_name, model_name = self._resolve_target(model=model, provider=provider)

        try:
            content = self._provider_chat(
                provider_name=provider_name,
                model_name=model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return {
                "content": content,
                "provider": provider_name,
                "model": model_name,
            }
        except Exception as primary_exc:
            if provider_name != "mlx" or not self.config.enable_ollama_fallback:
                raise

            self.logger.warning(
                "mlx_failed_fallback_to_ollama model=%s error=%s",
                model_name,
                primary_exc,
            )

            fallback_model = self.database.get_setting("ollama_fallback_model", model_name) or model_name
            content = self._provider_chat(
                provider_name="ollama",
                model_name=fallback_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return {
                "content": content,
                "provider": "ollama",
                "model": fallback_model,
                "fallback": True,
            }

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        provider: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> tuple[Iterator[str], str, str]:
        provider_name, model_name = self._resolve_target(model=model, provider=provider)

        try:
            iterator = self._provider_stream_chat(
                provider_name=provider_name,
                model_name=model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return iterator, provider_name, model_name
        except Exception as primary_exc:
            if provider_name != "mlx" or not self.config.enable_ollama_fallback:
                raise

            self.logger.warning(
                "mlx_stream_failed_fallback_to_ollama model=%s error=%s",
                model_name,
                primary_exc,
            )

            fallback_model = self.database.get_setting("ollama_fallback_model", model_name) or model_name
            iterator = self._provider_stream_chat(
                provider_name="ollama",
                model_name=fallback_model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return iterator, "ollama", fallback_model

    def _resolve_target(self, model: str | None, provider: str | None) -> tuple[str, str]:
        provider_name = provider or self.active_provider or self.config.default_provider
        model_name = model or self.active_model or self.config.default_model
        if provider_name not in self.providers:
            raise ValueError(f"Unknown provider: {provider_name}")
        return provider_name, model_name

    def _provider_chat(
        self,
        provider_name: str,
        model_name: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> str:
        provider = self.providers[provider_name]
        return provider.chat(
            messages=messages,
            model=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def _provider_stream_chat(
        self,
        provider_name: str,
        model_name: str,
        messages: list[dict[str, Any]],
        temperature: float,
        max_tokens: int,
    ) -> Iterator[str]:
        provider = self.providers[provider_name]
        return provider.stream_chat(
            messages=messages,
            model=model_name,
            temperature=temperature,
            max_tokens=max_tokens,
        )
