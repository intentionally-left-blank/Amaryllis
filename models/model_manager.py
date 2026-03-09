from __future__ import annotations

import logging
import time
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

        self._suggested_cache: dict[str, list[dict[str, str]]] = {}
        self._suggested_cache_until: float = 0.0
        self._suggested_cache_ttl_seconds = 6 * 60 * 60

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
            "suggested": self._get_suggested_models(),
        }

    def download_model(self, model_id: str, provider: str | None = None) -> dict[str, Any]:
        provider_name = provider or self.active_provider
        selected = self.providers.get(provider_name)
        if selected is None:
            raise ValueError(f"Unknown provider: {provider_name}")

        result = selected.download_model(model_id)
        self._invalidate_suggested_cache()
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

    def _get_suggested_models(self) -> dict[str, list[dict[str, str]]]:
        now = time.time()
        if self._suggested_cache and now < self._suggested_cache_until:
            return self._suggested_cache

        suggested: dict[str, list[dict[str, str]]] = {}
        for provider_name, provider in self.providers.items():
            items: list[dict[str, str]] = []
            suggested_getter = getattr(provider, "suggested_models", None)
            if callable(suggested_getter):
                try:
                    raw_items = suggested_getter(limit=400)
                    items = self._normalize_suggested(raw_items)
                except Exception as exc:
                    self.logger.warning(
                        "provider_suggested_models_failed provider=%s error=%s",
                        provider_name,
                        exc,
                    )
            suggested[provider_name] = items

        self._suggested_cache = suggested
        self._suggested_cache_until = now + self._suggested_cache_ttl_seconds
        return suggested

    @staticmethod
    def _normalize_suggested(items: Any) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        seen: set[str] = set()
        if not isinstance(items, list):
            return normalized

        for raw in items:
            if not isinstance(raw, dict):
                continue
            model_id = str(raw.get("id", "")).strip()
            if not model_id or model_id in seen:
                continue
            label = str(raw.get("label", model_id)).strip() or model_id
            seen.add(model_id)
            normalized.append({"id": model_id, "label": label})

        return normalized

    def _invalidate_suggested_cache(self) -> None:
        self._suggested_cache = {}
        self._suggested_cache_until = 0.0

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
