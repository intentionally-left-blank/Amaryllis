from __future__ import annotations

from datetime import datetime, timezone
import logging
import random
import time
from typing import Any, Iterator

from models.providers.anthropic_provider import AnthropicProvider
from models.providers.base import ModelProvider
from models.providers.mlx_provider import MLXProvider
from models.providers.openai_provider import OpenAIProvider
from models.providers.ollama_provider import OllamaProvider
from models.providers.openrouter_provider import OpenRouterProvider
from models.routing import (
    ModelCandidate,
    RoutingConstraints,
    estimate_model_size_b,
    infer_model_tags,
    normalize_route_mode,
    quality_tier_for_model,
    score_candidate,
    speed_tier_for_model,
)
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
        self._provider_failure_counts: dict[str, int] = {}
        self._provider_circuit_until: dict[str, float] = {}

    def list_models(self) -> dict[str, Any]:
        provider_payload: dict[str, Any] = {}

        for name, provider in self.providers.items():
            try:
                if self._is_provider_circuit_open(name):
                    provider_payload[name] = {
                        "available": False,
                        "error": (
                            f"Provider circuit is open. "
                            f"cooldown_remaining_sec={self._provider_cooldown_remaining(name):.2f}"
                        ),
                        "items": [],
                        "failure_count": self._provider_failure_counts.get(name, 0),
                        "circuit_open": True,
                    }
                    continue
                provider_payload[name] = {
                    "available": True,
                    "error": None,
                    "items": self._call_provider_resilient(
                        provider_name=name,
                        operation="list_models",
                        call=provider.list_models,
                    ),
                    "failure_count": self._provider_failure_counts.get(name, 0),
                    "circuit_open": False,
                }
            except Exception as exc:
                provider_payload[name] = {
                    "available": False,
                    "error": str(exc),
                    "items": [],
                    "failure_count": self._provider_failure_counts.get(name, 0),
                    "circuit_open": self._is_provider_circuit_open(name),
                }

        return {
            "active": {
                "provider": self.active_provider,
                "model": self.active_model,
            },
            "providers": provider_payload,
            "capabilities": self.provider_capabilities(),
            "suggested": self._get_suggested_models(),
            "routing_modes": [
                "balanced",
                "local_first",
                "quality_first",
                "coding",
                "reasoning",
            ],
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
                if self._is_provider_circuit_open(name):
                    latency_ms = round((time.perf_counter() - start) * 1000.0, 2)
                    checks[name] = {
                        "status": "circuit_open",
                        "latency_ms": latency_ms,
                        "active": name == self.active_provider,
                        "detail": (
                            f"cooldown_remaining_sec={self._provider_cooldown_remaining(name):.2f}"
                        ),
                        "failure_count": self._provider_failure_counts.get(name, 0),
                        "circuit_open": True,
                    }
                    continue
                checker = getattr(provider, "health_check", None)
                if callable(checker):
                    raw = self._call_provider_resilient(
                        provider_name=name,
                        operation="health_check",
                        call=checker,
                    )
                else:
                    self._call_provider_resilient(
                        provider_name=name,
                        operation="list_models",
                        call=provider.list_models,
                    )
                    raw = {"status": "ok"}

                latency_ms = round((time.perf_counter() - start) * 1000.0, 2)
                payload = raw if isinstance(raw, dict) else {"status": "ok", "detail": str(raw)}
                checks[name] = {
                    "status": str(payload.get("status", "ok")),
                    "latency_ms": latency_ms,
                    "active": name == self.active_provider,
                    "detail": payload.get("detail"),
                    "failure_count": self._provider_failure_counts.get(name, 0),
                    "circuit_open": self._is_provider_circuit_open(name),
                }
            except Exception as exc:
                latency_ms = round((time.perf_counter() - start) * 1000.0, 2)
                checks[name] = {
                    "status": "error",
                    "latency_ms": latency_ms,
                    "active": name == self.active_provider,
                    "detail": str(exc),
                    "failure_count": self._provider_failure_counts.get(name, 0),
                    "circuit_open": self._is_provider_circuit_open(name),
                }
        return checks

    def model_capability_matrix(
        self,
        *,
        include_suggested: bool = True,
        limit_per_provider: int = 120,
    ) -> dict[str, Any]:
        provider_caps = self.provider_capabilities()
        candidates = self._build_model_candidates(
            provider_capabilities=provider_caps,
            include_suggested=include_suggested,
            limit_per_provider=limit_per_provider,
        )
        items = [item.to_dict() for item in candidates]

        by_provider: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            by_provider.setdefault(str(item["provider"]), []).append(item)
        for provider_name in by_provider:
            by_provider[provider_name] = sorted(by_provider[provider_name], key=lambda row: str(row["model"]))

        return {
            "generated_at": self._utc_now_iso(),
            "active": {
                "provider": self.active_provider,
                "model": self.active_model,
            },
            "providers": provider_caps,
            "count": len(items),
            "items": sorted(items, key=lambda row: (str(row["provider"]), str(row["model"]))),
            "by_provider": by_provider,
        }

    def choose_route(
        self,
        *,
        mode: str = "balanced",
        provider: str | None = None,
        model: str | None = None,
        require_stream: bool = True,
        require_tools: bool = False,
        prefer_local: bool | None = None,
        min_params_b: float | None = None,
        max_params_b: float | None = None,
        include_suggested: bool = False,
        limit_per_provider: int = 120,
    ) -> dict[str, Any]:
        normalized_mode = normalize_route_mode(mode)
        constraints = RoutingConstraints(
            mode=normalized_mode,
            require_stream=bool(require_stream),
            require_tools=bool(require_tools),
            prefer_local=prefer_local,
            min_params_b=min_params_b,
            max_params_b=max_params_b,
        )

        if provider or model:
            provider_name, model_name = self._resolve_target(model=model, provider=provider)
            fallbacks = self._fallback_targets(provider_name=provider_name, model_name=model_name)
            return {
                "mode": normalized_mode,
                "constraints": self._route_constraints_dict(constraints),
                "selected": {
                    "provider": provider_name,
                    "model": model_name,
                    "reason": "explicit_target",
                },
                "fallbacks": [{"provider": p, "model": m} for p, m in fallbacks],
                "considered_count": 1,
            }

        provider_caps = self.provider_capabilities()
        candidates = self._build_model_candidates(
            provider_capabilities=provider_caps,
            include_suggested=include_suggested,
            limit_per_provider=limit_per_provider,
        )
        scored: list[tuple[float, ModelCandidate]] = []
        for candidate in candidates:
            score = score_candidate(candidate, constraints)
            if score is None:
                continue
            scored.append((score, candidate))

        if not scored:
            raise ValueError("No model candidates satisfy routing constraints.")

        scored.sort(
            key=lambda pair: (
                pair[0],
                1 if pair[1].active else 0,
                1 if pair[1].installed else 0,
            ),
            reverse=True,
        )
        selected_score, selected_candidate = scored[0]
        selected = selected_candidate.to_dict()
        selected["score"] = selected_score

        fallback_items: list[dict[str, Any]] = []
        seen = {f"{selected_candidate.provider}:{selected_candidate.model}"}
        for score, candidate in scored[1:]:
            key = f"{candidate.provider}:{candidate.model}"
            if key in seen:
                continue
            seen.add(key)
            payload = candidate.to_dict()
            payload["score"] = score
            fallback_items.append(payload)
            if len(fallback_items) >= 6:
                break

        return {
            "mode": normalized_mode,
            "constraints": self._route_constraints_dict(constraints),
            "selected": selected,
            "fallbacks": fallback_items,
            "considered_count": len(scored),
            "top_candidates": [
                {**candidate.to_dict(), "score": score}
                for score, candidate in scored[:10]
            ],
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
        routing: dict[str, Any] | None = None,
        fallback_targets: list[tuple[str, str]] | None = None,
    ) -> dict[str, Any]:
        route_payload: dict[str, Any] | None = None
        routed_fallbacks: list[tuple[str, str]] = []

        if routing and provider is None and model is None:
            route_payload = self.choose_route(
                mode=str(routing.get("mode", "balanced")),
                provider=None,
                model=None,
                require_stream=bool(routing.get("require_stream", True)),
                require_tools=bool(routing.get("require_tools", False)),
                prefer_local=routing.get("prefer_local"),
                min_params_b=self._to_float_or_none(routing.get("min_params_b")),
                max_params_b=self._to_float_or_none(routing.get("max_params_b")),
                include_suggested=bool(routing.get("include_suggested", False)),
            )
            (provider_name, model_name), routed_fallbacks = self._targets_from_route(route_payload)
        else:
            provider_name, model_name = self._resolve_target(model=model, provider=provider)

        targets = fallback_targets if fallback_targets is not None else (
            routed_fallbacks if routed_fallbacks else self._fallback_targets(
                provider_name=provider_name,
                model_name=model_name,
            )
        )

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
                "routing": route_payload,
            }
        except Exception as primary_exc:
            for fallback_provider, fallback_model in targets:
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
                        "routing": route_payload,
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
        routing: dict[str, Any] | None = None,
        fallback_targets: list[tuple[str, str]] | None = None,
    ) -> tuple[Iterator[str], str, str]:
        routed_fallbacks: list[tuple[str, str]] = []
        if routing and provider is None and model is None:
            route_payload = self.choose_route(
                mode=str(routing.get("mode", "balanced")),
                provider=None,
                model=None,
                require_stream=bool(routing.get("require_stream", True)),
                require_tools=bool(routing.get("require_tools", False)),
                prefer_local=routing.get("prefer_local"),
                min_params_b=self._to_float_or_none(routing.get("min_params_b")),
                max_params_b=self._to_float_or_none(routing.get("max_params_b")),
                include_suggested=bool(routing.get("include_suggested", False)),
            )
            (provider_name, model_name), routed_fallbacks = self._targets_from_route(route_payload)
        else:
            provider_name, model_name = self._resolve_target(model=model, provider=provider)

        effective_fallbacks = fallback_targets if fallback_targets is not None else (
            routed_fallbacks if routed_fallbacks else self._fallback_targets(
                provider_name=provider_name,
                model_name=model_name,
            )
        )
        targets = [(provider_name, model_name)] + effective_fallbacks
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

    def _build_model_candidates(
        self,
        *,
        provider_capabilities: dict[str, Any],
        include_suggested: bool,
        limit_per_provider: int,
    ) -> list[ModelCandidate]:
        rows: list[ModelCandidate] = []
        seen: set[str] = set()

        for provider_name, provider in self.providers.items():
            caps = provider_capabilities.get(provider_name, {})
            local = bool(caps.get("local", False))
            supports_stream = bool(caps.get("supports_stream", True))
            supports_tools = bool(caps.get("supports_tools", False))
            requires_api_key = bool(caps.get("requires_api_key", False))

            provider_models: list[dict[str, Any]] = []
            try:
                provider_models = self._call_provider_resilient(
                    provider_name=provider_name,
                    operation="list_models",
                    call=provider.list_models,
                )
            except Exception as exc:
                self.logger.debug("candidate_list_models_failed provider=%s error=%s", provider_name, exc)
                provider_models = []

            for item in provider_models[: max(1, limit_per_provider)]:
                model_id = str(item.get("id", "")).strip()
                if not model_id:
                    continue
                key = f"{provider_name}:{model_id}"
                if key in seen:
                    continue
                seen.add(key)
                metadata = item.get("metadata")
                rows.append(
                    self._candidate_from_model_id(
                        provider_name=provider_name,
                        model_id=model_id,
                        source="listed",
                        provider_local=local,
                        supports_stream=supports_stream,
                        supports_tools=supports_tools,
                        requires_api_key=requires_api_key,
                        active=bool(item.get("active", False) or model_id == self.active_model),
                        installed=bool(local),
                        metadata=metadata if isinstance(metadata, dict) else {},
                    )
                )

            defaults = [self._default_model_for_provider(provider_name)]
            if provider_name == self.active_provider and self.active_model:
                defaults.append(self.active_model)
            for model_id in defaults:
                normalized = str(model_id).strip()
                if not normalized:
                    continue
                key = f"{provider_name}:{normalized}"
                if key in seen:
                    continue
                seen.add(key)
                rows.append(
                    self._candidate_from_model_id(
                        provider_name=provider_name,
                        model_id=normalized,
                        source="default",
                        provider_local=local,
                        supports_stream=supports_stream,
                        supports_tools=supports_tools,
                        requires_api_key=requires_api_key,
                        active=provider_name == self.active_provider and normalized == self.active_model,
                        installed=bool(local and provider_name == self.active_provider and normalized == self.active_model),
                        metadata={},
                    )
                )

            if not include_suggested:
                continue

            suggested_getter = getattr(provider, "suggested_models", None)
            if not callable(suggested_getter):
                continue
            try:
                suggested_raw = suggested_getter(limit=limit_per_provider)
                suggested = self._normalize_suggested(suggested_raw)
            except Exception as exc:
                self.logger.debug("candidate_suggested_failed provider=%s error=%s", provider_name, exc)
                suggested = []

            for item in suggested[: max(1, limit_per_provider)]:
                model_id = str(item.get("id", "")).strip()
                if not model_id:
                    continue
                key = f"{provider_name}:{model_id}"
                if key in seen:
                    continue
                seen.add(key)
                rows.append(
                    self._candidate_from_model_id(
                        provider_name=provider_name,
                        model_id=model_id,
                        source="suggested",
                        provider_local=local,
                        supports_stream=supports_stream,
                        supports_tools=supports_tools,
                        requires_api_key=requires_api_key,
                        active=False,
                        installed=False,
                        metadata={},
                    )
                )

        return rows

    def _candidate_from_model_id(
        self,
        *,
        provider_name: str,
        model_id: str,
        source: str,
        provider_local: bool,
        supports_stream: bool,
        supports_tools: bool,
        requires_api_key: bool,
        active: bool,
        installed: bool,
        metadata: dict[str, Any],
    ) -> ModelCandidate:
        estimated = estimate_model_size_b(model_id)
        tags = infer_model_tags(model_id)
        quality = quality_tier_for_model(model_id, estimated)
        speed = speed_tier_for_model(provider_local, estimated, tags)
        return ModelCandidate(
            provider=provider_name,
            model=model_id,
            local=provider_local,
            installed=installed,
            active=active,
            supports_stream=supports_stream,
            supports_tools=supports_tools,
            requires_api_key=requires_api_key,
            estimated_params_b=estimated,
            quality_tier=quality,
            speed_tier=speed,
            tags=tags,
            source=source,
            metadata=metadata,
        )

    @staticmethod
    def _route_constraints_dict(constraints: RoutingConstraints) -> dict[str, Any]:
        return {
            "mode": constraints.mode,
            "require_stream": constraints.require_stream,
            "require_tools": constraints.require_tools,
            "prefer_local": constraints.prefer_local,
            "min_params_b": constraints.min_params_b,
            "max_params_b": constraints.max_params_b,
        }

    @staticmethod
    def _targets_from_route(route: dict[str, Any]) -> tuple[tuple[str, str], list[tuple[str, str]]]:
        selected = route.get("selected")
        if not isinstance(selected, dict):
            raise ValueError("Route payload is missing selected target.")
        provider_name = str(selected.get("provider", "")).strip()
        model_name = str(selected.get("model", "")).strip()
        if not provider_name or not model_name:
            raise ValueError("Route selected target is incomplete.")

        fallback_targets: list[tuple[str, str]] = []
        raw_fallbacks = route.get("fallbacks")
        if isinstance(raw_fallbacks, list):
            for item in raw_fallbacks:
                if not isinstance(item, dict):
                    continue
                fallback_provider = str(item.get("provider", "")).strip()
                fallback_model = str(item.get("model", "")).strip()
                if fallback_provider and fallback_model:
                    fallback_targets.append((fallback_provider, fallback_model))
        return (provider_name, model_name), fallback_targets

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _to_float_or_none(value: Any) -> float | None:
        if value is None:
            return None
        try:
            return float(value)
        except Exception:
            return None

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
        return self._call_provider_resilient(
            provider_name=provider_name,
            operation="chat",
            call=lambda: provider.chat(
                messages=messages,
                model=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
            ),
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
        return self._call_provider_resilient(
            provider_name=provider_name,
            operation="stream_chat",
            call=lambda: provider.stream_chat(
                messages=messages,
                model=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
            ),
        )

    def _call_provider_resilient(
        self,
        *,
        provider_name: str,
        operation: str,
        call: Any,
    ) -> Any:
        if self._is_provider_circuit_open(provider_name):
            remaining = self._provider_cooldown_remaining(provider_name)
            raise RuntimeError(
                f"Provider '{provider_name}' circuit is open "
                f"(cooldown_remaining_sec={remaining:.2f})."
            )

        attempts = max(1, int(self.config.provider_retry_attempts))
        last_exc: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                result = call()
                self._record_provider_success(provider_name)
                return result
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                self._record_provider_failure(provider_name)
                retryable = self._is_retryable_provider_error(exc)
                if attempt >= attempts or not retryable:
                    break
                delay = self._provider_retry_delay(attempt=attempt)
                if delay > 0:
                    time.sleep(delay)

        assert last_exc is not None
        raise RuntimeError(
            f"Provider '{provider_name}' {operation} failed: {last_exc}"
        ) from last_exc

    def _record_provider_success(self, provider_name: str) -> None:
        self._provider_failure_counts[provider_name] = 0
        self._provider_circuit_until.pop(provider_name, None)

    def _record_provider_failure(self, provider_name: str) -> None:
        failures = self._provider_failure_counts.get(provider_name, 0) + 1
        self._provider_failure_counts[provider_name] = failures
        threshold = max(1, int(self.config.provider_circuit_failure_threshold))
        if failures >= threshold:
            cooldown = max(1.0, float(self.config.provider_circuit_cooldown_sec))
            self._provider_circuit_until[provider_name] = time.monotonic() + cooldown
            self.logger.warning(
                "provider_circuit_open provider=%s failures=%s cooldown_sec=%.2f",
                provider_name,
                failures,
                cooldown,
            )

    def _is_provider_circuit_open(self, provider_name: str) -> bool:
        until = self._provider_circuit_until.get(provider_name)
        if until is None:
            return False
        if time.monotonic() >= until:
            self._provider_circuit_until.pop(provider_name, None)
            self._provider_failure_counts[provider_name] = 0
            return False
        return True

    def _provider_cooldown_remaining(self, provider_name: str) -> float:
        until = self._provider_circuit_until.get(provider_name)
        if until is None:
            return 0.0
        return max(0.0, until - time.monotonic())

    def _provider_retry_delay(self, *, attempt: int) -> float:
        base = max(0.0, float(self.config.provider_retry_backoff_sec))
        jitter = max(0.0, float(self.config.provider_retry_jitter_sec))
        delay = base * (2 ** max(0, attempt - 1))
        if jitter > 0:
            delay += random.uniform(0.0, jitter)
        return max(0.0, delay)

    @staticmethod
    def _is_retryable_provider_error(exc: Exception) -> bool:
        if isinstance(exc, TimeoutError):
            return True
        if isinstance(exc, OSError):
            return True
        message = str(exc).lower()
        retry_keywords = (
            "timeout",
            "tempor",
            "connection",
            "network",
            "unavailable",
            "overloaded",
            "429",
            "too many requests",
            "rate limit",
            "502",
            "503",
            "504",
            "try again",
        )
        return any(keyword in message for keyword in retry_keywords)
