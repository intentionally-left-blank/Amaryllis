from __future__ import annotations

import logging
import time
from typing import Any, Iterator

from models.providers.anthropic_provider import AnthropicProvider
from models.providers.base import ModelProvider
from models.providers.mlx_provider import MLXProvider
from models.providers.openai_provider import OpenAIProvider
from models.providers.ollama_provider import OllamaProvider
from models.providers.openrouter_provider import OpenRouterProvider
from runtime.config import AppConfig
from storage.database import Database


class ModelManager:
    def __init__(self, config: AppConfig, database: Database) -> None:
        self.config = config
        self.database = database
        self.logger = logging.getLogger("amaryllis.models.manager")

        self.providers: dict[str, ModelProvider] = {
            "mlx": MLXProvider(config.models_dir),
            "ollama": OllamaProvider(config.ollama_base_url),
        }
        if config.openai_api_key or config.openai_base_url != "https://api.openai.com/v1":
            self.providers["openai"] = OpenAIProvider(
                base_url=config.openai_base_url,
                api_key=config.openai_api_key,
            )
        if config.anthropic_api_key or config.anthropic_base_url != "https://api.anthropic.com/v1":
            self.providers["anthropic"] = AnthropicProvider(
                base_url=config.anthropic_base_url,
                api_key=config.anthropic_api_key,
            )
        if config.openrouter_api_key or config.openrouter_base_url != "https://openrouter.ai/api/v1":
            self.providers["openrouter"] = OpenRouterProvider(
                base_url=config.openrouter_base_url,
                api_key=config.openrouter_api_key,
            )

        self.active_provider = database.get_setting("active_provider", config.default_provider) or config.default_provider
        self.active_model = database.get_setting("active_model", config.default_model) or config.default_model

        if self.active_provider not in self.providers:
            self.active_provider = config.default_provider if config.default_provider in self.providers else "mlx"
            self.database.set_setting("active_provider", self.active_provider)
            if self.active_model:
                self.database.set_setting("active_model", self.active_model)

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
            "capabilities": self.provider_capabilities(),
            "suggested": self._get_suggested_models(),
        }

    def provider_capabilities(self) -> dict[str, Any]:
        matrix: dict[str, Any] = {}
        for name, provider in self.providers.items():
            getter = getattr(provider, "capabilities", None)
            if callable(getter):
                try:
                    raw = getter()
                except Exception as exc:
                    self.logger.warning("provider_capabilities_failed provider=%s error=%s", name, exc)
                    raw = {}
            else:
                raw = {}

            payload = raw if isinstance(raw, dict) else {}
            matrix[name] = {
                "local": bool(payload.get("local", False)),
                "supports_download": bool(payload.get("supports_download", False)),
                "supports_load": bool(payload.get("supports_load", True)),
                "supports_stream": bool(payload.get("supports_stream", True)),
                "supports_tools": bool(payload.get("supports_tools", False)),
                "requires_api_key": bool(payload.get("requires_api_key", False)),
            }
        return matrix

    def provider_health(self) -> dict[str, Any]:
        checks: dict[str, Any] = {}
        for name, provider in self.providers.items():
            start = time.perf_counter()
            try:
                checker = getattr(provider, "health_check", None)
                if callable(checker):
                    raw = checker()
                else:
                    provider.list_models()
                    raw = {"status": "ok"}

                latency_ms = round((time.perf_counter() - start) * 1000.0, 2)
                payload = raw if isinstance(raw, dict) else {"status": "ok", "detail": str(raw)}
                checks[name] = {
                    "status": str(payload.get("status", "ok")),
                    "latency_ms": latency_ms,
                    "active": name == self.active_provider,
                    "detail": payload.get("detail"),
                }
            except Exception as exc:
                latency_ms = round((time.perf_counter() - start) * 1000.0, 2)
                checks[name] = {
                    "status": "error",
                    "latency_ms": latency_ms,
                    "active": name == self.active_provider,
                    "detail": str(exc),
                }
        return checks

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
            for fallback_provider, fallback_model in self._fallback_targets(
                provider_name=provider_name,
                model_name=model_name,
            ):
                self.logger.warning(
                    "chat_fallback_try from_provider=%s from_model=%s to_provider=%s to_model=%s error=%s",
                    provider_name,
                    model_name,
                    fallback_provider,
                    fallback_model,
                    primary_exc,
                )
                try:
                    content = self._provider_chat(
                        provider_name=fallback_provider,
                        model_name=fallback_model,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                    return {
                        "content": content,
                        "provider": fallback_provider,
                        "model": fallback_model,
                        "fallback": True,
                    }
                except Exception as fallback_exc:
                    self.logger.warning(
                        "chat_fallback_failed provider=%s model=%s error=%s",
                        fallback_provider,
                        fallback_model,
                        fallback_exc,
                    )
            raise

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        provider: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> tuple[Iterator[str], str, str]:
        provider_name, model_name = self._resolve_target(model=model, provider=provider)
        targets = [(provider_name, model_name)] + self._fallback_targets(
            provider_name=provider_name,
            model_name=model_name,
        )
        last_exc: Exception | None = None

        for idx, (target_provider, target_model) in enumerate(targets):
            if idx > 0 and last_exc is not None:
                self.logger.warning(
                    "stream_fallback_try from_provider=%s from_model=%s to_provider=%s to_model=%s error=%s",
                    provider_name,
                    model_name,
                    target_provider,
                    target_model,
                    last_exc,
                )

            try:
                iterator = self._provider_stream_chat(
                    provider_name=target_provider,
                    model_name=target_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                primed_iterator, has_content = self._prime_stream_iterator(iterator)
                if not has_content and idx > 0:
                    self.logger.warning(
                        "stream_fallback_empty provider=%s model=%s",
                        target_provider,
                        target_model,
                    )
                return primed_iterator, target_provider, target_model
            except Exception as exc:
                last_exc = exc
                if idx > 0:
                    self.logger.warning(
                        "stream_fallback_failed provider=%s model=%s error=%s",
                        target_provider,
                        target_model,
                        exc,
                    )

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("stream_chat failed without an explicit error")

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
        if model:
            model_name = model
        elif provider and provider != self.active_provider:
            model_name = self._default_model_for_provider(provider_name)
        else:
            model_name = self.active_model or self.config.default_model
        if provider_name not in self.providers:
            raise ValueError(f"Unknown provider: {provider_name}")
        return provider_name, model_name

    def _default_model_for_provider(self, provider_name: str) -> str:
        if provider_name == "openai":
            return self.database.get_setting("openai_default_model", "gpt-4o-mini") or "gpt-4o-mini"
        if provider_name == "anthropic":
            return (
                self.database.get_setting("anthropic_default_model", "claude-3-5-sonnet-latest")
                or "claude-3-5-sonnet-latest"
            )
        if provider_name == "openrouter":
            return (
                self.database.get_setting("openrouter_default_model", "openai/gpt-4o-mini")
                or "openai/gpt-4o-mini"
            )
        if provider_name == "ollama":
            return self.database.get_setting("ollama_fallback_model", "llama3.2") or "llama3.2"
        return self.config.default_model

    def _fallback_targets(self, provider_name: str, model_name: str) -> list[tuple[str, str]]:
        if not self.config.enable_ollama_fallback:
            return []

        targets: list[tuple[str, str]] = []

        if provider_name == "mlx":
            if "ollama" in self.providers:
                ollama_model = self.database.get_setting("ollama_fallback_model", model_name) or model_name
                targets.append(("ollama", ollama_model))
            return self._unique_targets(targets)

        if provider_name in {"openai", "openrouter", "anthropic"}:
            if self.active_provider in {"mlx", "ollama"} and self.active_provider in self.providers:
                local_active_model = self.active_model or self._default_model_for_provider(self.active_provider)
                targets.append((self.active_provider, local_active_model))

            if "mlx" in self.providers:
                targets.append(("mlx", self.config.default_model))

            if "ollama" in self.providers:
                ollama_model = self.database.get_setting("ollama_fallback_model", "llama3.2") or "llama3.2"
                targets.append(("ollama", ollama_model))

        return self._unique_targets(targets)

    @staticmethod
    def _unique_targets(targets: list[tuple[str, str]]) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        seen: set[str] = set()
        for provider_name, model_name in targets:
            key = f"{provider_name}:{model_name}"
            if key in seen:
                continue
            seen.add(key)
            result.append((provider_name, model_name))
        return result

    @staticmethod
    def _prime_stream_iterator(iterator: Iterator[str]) -> tuple[Iterator[str], bool]:
        try:
            first = next(iterator)
        except StopIteration:
            return iter(()), False

        def chain() -> Iterator[str]:
            yield first
            for chunk in iterator:
                yield chunk

        return chain(), True

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
