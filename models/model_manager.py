from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import logging
import random
from threading import Lock
import time
from typing import Any, Iterator

from models.provider_errors import (
    ProviderErrorInfo,
    ProviderOperationError,
    classify_provider_error,
)
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
        self._provider_state_lock = Lock()
        self._cloud_rate_records: dict[str, deque[float]] = {}
        self._cloud_budget_records: dict[str, deque[tuple[float, int]]] = {}
        self._guardrail_lock = Lock()
        self._session_route_pins: dict[str, dict[str, Any]] = {}
        self._recent_failover_events: deque[dict[str, Any]] = deque(maxlen=500)
        self._route_lock = Lock()

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
                        "failure_count": self._provider_failure_count(name),
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
                    "failure_count": self._provider_failure_count(name),
                    "circuit_open": False,
                    "guardrails": self._provider_guardrail_status(name),
                }
            except Exception as exc:
                provider_payload[name] = {
                    "available": False,
                    "error": str(exc),
                    "items": [],
                    "failure_count": self._provider_failure_count(name),
                    "circuit_open": self._is_provider_circuit_open(name),
                    "guardrails": self._provider_guardrail_status(name),
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
                        "failure_count": self._provider_failure_count(name),
                        "circuit_open": True,
                        "guardrails": self._provider_guardrail_status(name),
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
                    "failure_count": self._provider_failure_count(name),
                    "circuit_open": self._is_provider_circuit_open(name),
                    "guardrails": self._provider_guardrail_status(name),
                }
            except Exception as exc:
                latency_ms = round((time.perf_counter() - start) * 1000.0, 2)
                checks[name] = {
                    "status": "error",
                    "latency_ms": latency_ms,
                    "active": name == self.active_provider,
                    "detail": str(exc),
                    "failure_count": self._provider_failure_count(name),
                    "circuit_open": self._is_provider_circuit_open(name),
                    "guardrails": self._provider_guardrail_status(name),
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
            penalty = self._provider_guardrail_penalty(candidate.provider)
            final_score = score - penalty
            scored.append((final_score, candidate))

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
        selected["guardrail_penalty"] = self._provider_guardrail_penalty(selected_candidate.provider)

        fallback_items: list[dict[str, Any]] = []
        seen = {f"{selected_candidate.provider}:{selected_candidate.model}"}
        for score, candidate in scored[1:]:
            key = f"{candidate.provider}:{candidate.model}"
            if key in seen:
                continue
            seen.add(key)
            payload = candidate.to_dict()
            payload["score"] = score
            payload["guardrail_penalty"] = self._provider_guardrail_penalty(candidate.provider)
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
        session_id: str | None = None,
    ) -> dict[str, Any]:
        (provider_name, model_name), targets, route_payload = self._resolve_runtime_targets(
            model=model,
            provider=provider,
            routing=routing,
            fallback_targets=fallback_targets,
            session_id=session_id,
            require_stream=False,
            require_tools=False,
        )

        failover_events: list[dict[str, Any]] = []
        try:
            content = self._provider_chat(
                provider_name=provider_name,
                model_name=model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            self._set_session_pin(
                session_id=session_id,
                provider_name=provider_name,
                model_name=model_name,
                reason="primary_success",
            )
            return {
                "content": content,
                "provider": provider_name,
                "model": model_name,
                "routing": self._build_route_trace(
                    route_payload=route_payload,
                    final_provider=provider_name,
                    final_model=model_name,
                    fallback_used=False,
                    failover_events=failover_events,
                    session_id=session_id,
                ),
            }
        except Exception as primary_exc:
            primary_info = classify_provider_error(
                provider=provider_name,
                operation="chat",
                error=primary_exc,
            )
            event = {
                "attempt": 1,
                "provider": provider_name,
                "model": model_name,
                "error_class": primary_info.error_class,
                "retryable": primary_info.retryable,
                "message": primary_info.message,
            }
            failover_events.append(event)
            self._record_failover_event(
                session_id=session_id,
                provider_name=provider_name,
                model_name=model_name,
                info=primary_info,
                attempt=1,
            )

        ordered_targets = self._prioritize_failover_targets(
            primary_provider=provider_name,
            primary_model=model_name,
            targets=targets,
            error_info=primary_info,
            session_id=session_id,
        )
        last_error_info: ProviderErrorInfo = primary_info

        for attempt_index, (fallback_provider, fallback_model) in enumerate(ordered_targets, start=2):
            self.logger.warning(
                "chat_fallback_try from_provider=%s from_model=%s to_provider=%s to_model=%s error_class=%s message=%s",
                provider_name,
                model_name,
                fallback_provider,
                fallback_model,
                primary_info.error_class,
                primary_info.message,
            )
            try:
                content = self._provider_chat(
                    provider_name=fallback_provider,
                    model_name=fallback_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                self._set_session_pin(
                    session_id=session_id,
                    provider_name=fallback_provider,
                    model_name=fallback_model,
                    reason=f"fallback_success:{primary_info.error_class}",
                )
                return {
                    "content": content,
                    "provider": fallback_provider,
                    "model": fallback_model,
                    "fallback": True,
                    "routing": self._build_route_trace(
                        route_payload=route_payload,
                        final_provider=fallback_provider,
                        final_model=fallback_model,
                        fallback_used=True,
                        failover_events=failover_events,
                        session_id=session_id,
                    ),
                }
            except Exception as fallback_exc:
                fallback_info = classify_provider_error(
                    provider=fallback_provider,
                    operation="chat",
                    error=fallback_exc,
                )
                last_error_info = fallback_info
                failover_events.append(
                    {
                        "attempt": attempt_index,
                        "provider": fallback_provider,
                        "model": fallback_model,
                        "error_class": fallback_info.error_class,
                        "retryable": fallback_info.retryable,
                        "message": fallback_info.message,
                    }
                )
                self._record_failover_event(
                    session_id=session_id,
                    provider_name=fallback_provider,
                    model_name=fallback_model,
                    info=fallback_info,
                    attempt=attempt_index,
                )
                self.logger.warning(
                    "chat_fallback_failed provider=%s model=%s error_class=%s message=%s",
                    fallback_provider,
                    fallback_model,
                    fallback_info.error_class,
                    fallback_info.message,
                )

        raise ProviderOperationError(last_error_info)

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str | None = None,
        provider: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 512,
        routing: dict[str, Any] | None = None,
        fallback_targets: list[tuple[str, str]] | None = None,
        session_id: str | None = None,
    ) -> tuple[Iterator[str], str, str, dict[str, Any] | None]:
        (provider_name, model_name), targets, route_payload = self._resolve_runtime_targets(
            model=model,
            provider=provider,
            routing=routing,
            fallback_targets=fallback_targets,
            session_id=session_id,
            require_stream=True,
            require_tools=False,
        )

        failover_events: list[dict[str, Any]] = []
        last_error_info: ProviderErrorInfo | None = None
        attempt_targets: list[tuple[str, str]] = [(provider_name, model_name)]
        ordered_targets = targets

        while attempt_targets:
            target_provider, target_model = attempt_targets.pop(0)
            attempt = len(failover_events) + 1
            try:
                iterator = self._provider_stream_chat(
                    provider_name=target_provider,
                    model_name=target_model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                primed_iterator, has_content = self._prime_stream_iterator(iterator)
                if not has_content:
                    raise RuntimeError("Empty streaming response")

                self._set_session_pin(
                    session_id=session_id,
                    provider_name=target_provider,
                    model_name=target_model,
                    reason="stream_success" if attempt == 1 else f"stream_fallback_success:{attempt}",
                )
                route_trace = self._build_route_trace(
                    route_payload=route_payload,
                    final_provider=target_provider,
                    final_model=target_model,
                    fallback_used=attempt > 1,
                    failover_events=failover_events,
                    session_id=session_id,
                )
                return primed_iterator, target_provider, target_model, route_trace
            except Exception as exc:
                info = classify_provider_error(
                    provider=target_provider,
                    operation="stream_chat",
                    error=exc,
                )
                last_error_info = info
                failover_events.append(
                    {
                        "attempt": attempt,
                        "provider": target_provider,
                        "model": target_model,
                        "error_class": info.error_class,
                        "retryable": info.retryable,
                        "message": info.message,
                    }
                )
                self._record_failover_event(
                    session_id=session_id,
                    provider_name=target_provider,
                    model_name=target_model,
                    info=info,
                    attempt=attempt,
                )

                if attempt == 1:
                    ordered_targets = self._prioritize_failover_targets(
                        primary_provider=provider_name,
                        primary_model=model_name,
                        targets=ordered_targets,
                        error_info=info,
                        session_id=session_id,
                    )
                    attempt_targets.extend(ordered_targets)
                self.logger.warning(
                    "stream_fallback_failed provider=%s model=%s error_class=%s message=%s",
                    target_provider,
                    target_model,
                    info.error_class,
                    info.message,
                )

        if last_error_info is not None:
            raise ProviderOperationError(last_error_info)
        raise RuntimeError("stream_chat failed without an explicit error")

    def debug_failover_state(
        self,
        *,
        session_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        normalized_limit = max(1, min(500, int(limit)))
        with self._route_lock:
            items = list(self._recent_failover_events)[-normalized_limit:]
            pins = dict(self._session_route_pins)

        selected_pin: dict[str, Any] | None = None
        if session_id:
            selected_pin = pins.get(session_id)

        return {
            "session_id": session_id,
            "selected_pin": selected_pin,
            "pins_count": len(pins),
            "pins": [
                {"session_id": key, **value}
                for key, value in sorted(pins.items(), key=lambda item: item[0])[:100]
            ],
            "recent_failovers": items,
            "recent_failovers_count": len(items),
        }

    def _resolve_runtime_targets(
        self,
        *,
        model: str | None,
        provider: str | None,
        routing: dict[str, Any] | None,
        fallback_targets: list[tuple[str, str]] | None,
        session_id: str | None,
        require_stream: bool,
        require_tools: bool,
    ) -> tuple[tuple[str, str], list[tuple[str, str]], dict[str, Any] | None]:
        route_payload: dict[str, Any] | None = None
        routed_fallbacks: list[tuple[str, str]] = []
        explicit_target = provider is not None or model is not None
        primary_reason = "default_target"

        if explicit_target:
            provider_name, model_name = self._resolve_target(model=model, provider=provider)
            primary_reason = "explicit_target"
        else:
            pin = self._get_session_pin(session_id)
            if pin is not None:
                provider_name = str(pin.get("provider", "")).strip()
                model_name = str(pin.get("model", "")).strip()
                if provider_name in self.providers and model_name:
                    primary_reason = "session_pin"
                    route_payload = {
                        "mode": "session_pin",
                        "selected": {
                            "provider": provider_name,
                            "model": model_name,
                            "reason": "session_pin",
                        },
                        "requested_mode": str(routing.get("mode", "balanced")) if isinstance(routing, dict) else None,
                        "session_pin": pin,
                        "fallbacks": [],
                    }
                else:
                    provider_name, model_name = self._resolve_target(model=model, provider=provider)
            elif routing:
                route_payload = self.choose_route(
                    mode=str(routing.get("mode", "balanced")),
                    provider=None,
                    model=None,
                    require_stream=require_stream,
                    require_tools=require_tools,
                    prefer_local=routing.get("prefer_local"),
                    min_params_b=self._to_float_or_none(routing.get("min_params_b")),
                    max_params_b=self._to_float_or_none(routing.get("max_params_b")),
                    include_suggested=bool(routing.get("include_suggested", False)),
                )
                (provider_name, model_name), routed_fallbacks = self._targets_from_route(route_payload)
                primary_reason = "route_selected"
            else:
                provider_name, model_name = self._resolve_target(model=model, provider=provider)

        if route_payload is None:
            route_payload = {
                "mode": str(routing.get("mode", "direct")) if isinstance(routing, dict) else "direct",
                "selected": {
                    "provider": provider_name,
                    "model": model_name,
                    "reason": primary_reason,
                },
            }

        targets = fallback_targets if fallback_targets is not None else (
            routed_fallbacks if routed_fallbacks else self._fallback_targets(
                provider_name=provider_name,
                model_name=model_name,
            )
        )
        if isinstance(route_payload, dict) and "fallbacks" not in route_payload:
            route_payload["fallbacks"] = [
                {"provider": item_provider, "model": item_model}
                for item_provider, item_model in targets
            ]
        return (provider_name, model_name), targets, route_payload

    def _build_route_trace(
        self,
        *,
        route_payload: dict[str, Any] | None,
        final_provider: str,
        final_model: str,
        fallback_used: bool,
        failover_events: list[dict[str, Any]],
        session_id: str | None,
    ) -> dict[str, Any] | None:
        if route_payload is None and not failover_events:
            return None

        payload = dict(route_payload or {})
        payload["final"] = {
            "provider": final_provider,
            "model": final_model,
            "fallback_used": fallback_used,
        }
        if failover_events:
            payload["failover_events"] = failover_events
        if session_id:
            pin = self._get_session_pin(session_id)
            if pin is not None:
                payload["session_pin"] = pin
        return payload

    def _prioritize_failover_targets(
        self,
        *,
        primary_provider: str,
        primary_model: str,
        targets: list[tuple[str, str]],
        error_info: ProviderErrorInfo,
        session_id: str | None,
    ) -> list[tuple[str, str]]:
        local_providers = {"mlx", "ollama"}
        pin = self._get_session_pin(session_id)
        pinned_key = None
        if pin is not None:
            pinned_provider = str(pin.get("provider", "")).strip()
            pinned_model = str(pin.get("model", "")).strip()
            if pinned_provider and pinned_model:
                pinned_key = f"{pinned_provider}:{pinned_model}"

        ranked: list[tuple[tuple[float, float, int], tuple[str, str]]] = []
        seen: set[str] = set()
        for idx, (target_provider, target_model) in enumerate(targets):
            if target_provider not in self.providers:
                continue
            key = f"{target_provider}:{target_model}"
            if key == f"{primary_provider}:{primary_model}" or key in seen:
                continue
            seen.add(key)

            group = 1.0
            if pinned_key is not None and key == pinned_key:
                group = -1.0
            elif error_info.error_class in {"rate_limit", "quota", "budget_limit", "auth"}:
                group = 0.0 if target_provider in local_providers else 2.0
            elif error_info.error_class in {"timeout", "server", "network", "unavailable", "circuit_open"}:
                group = 0.0 if target_provider != primary_provider else 2.0
                if target_provider in local_providers:
                    group -= 0.2
            elif error_info.error_class == "invalid_request":
                group = 0.0 if target_provider in local_providers else 2.5
            if self._is_provider_circuit_open(target_provider):
                group += 2.5

            pressure = self._provider_guardrail_pressure(target_provider)
            ranked.append(((group, pressure, idx), (target_provider, target_model)))

        ranked.sort(key=lambda item: item[0])
        return [target for _, target in ranked]

    def _record_failover_event(
        self,
        *,
        session_id: str | None,
        provider_name: str,
        model_name: str,
        info: ProviderErrorInfo,
        attempt: int,
    ) -> None:
        row = {
            "timestamp": self._utc_now_iso(),
            "session_id": session_id,
            "provider": provider_name,
            "model": model_name,
            "operation": info.operation,
            "error_class": info.error_class,
            "retryable": info.retryable,
            "message": info.message,
            "status_code": info.status_code,
            "attempt": attempt,
        }
        with self._route_lock:
            self._recent_failover_events.append(row)

    def _get_session_pin(self, session_id: str | None) -> dict[str, Any] | None:
        if not session_id:
            return None
        with self._route_lock:
            pin = self._session_route_pins.get(session_id)
            if pin is None:
                return None
            return dict(pin)

    def _set_session_pin(
        self,
        *,
        session_id: str | None,
        provider_name: str,
        model_name: str,
        reason: str,
    ) -> None:
        if not session_id:
            return
        row = {
            "provider": provider_name,
            "model": model_name,
            "reason": reason,
            "updated_at": self._utc_now_iso(),
        }
        with self._route_lock:
            self._session_route_pins[session_id] = row

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
            before_call=lambda: self._enforce_cloud_guardrails(
                provider_name=provider_name,
                messages=messages,
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
            before_call=lambda: self._enforce_cloud_guardrails(
                provider_name=provider_name,
                messages=messages,
                max_tokens=max_tokens,
            ),
        )

    def _call_provider_resilient(
        self,
        *,
        provider_name: str,
        operation: str,
        call: Any,
        before_call: Any | None = None,
    ) -> Any:
        if self._is_provider_circuit_open(provider_name):
            remaining = self._provider_cooldown_remaining(provider_name)
            raise ProviderOperationError(
                ProviderErrorInfo(
                    provider=provider_name,
                    operation=operation,
                    error_class="circuit_open",
                    message=(
                        f"Provider '{provider_name}' circuit is open "
                        f"(cooldown_remaining_sec={remaining:.2f})."
                    ),
                    raw_message=(
                        f"Provider '{provider_name}' circuit is open "
                        f"(cooldown_remaining_sec={remaining:.2f})."
                    ),
                    retryable=True,
                )
            )

        attempts = max(1, int(self.config.provider_retry_attempts))
        last_info: ProviderErrorInfo | None = None
        for attempt in range(1, attempts + 1):
            try:
                if callable(before_call):
                    before_call()
                result = call()
                self._record_provider_success(provider_name)
                return result
            except Exception as exc:  # noqa: BLE001
                info = classify_provider_error(
                    provider=provider_name,
                    operation=operation,
                    error=exc,
                )
                last_info = info
                self._record_provider_failure(provider_name)
                retryable = info.retryable
                if attempt >= attempts or not retryable:
                    break
                delay = self._provider_retry_delay(attempt=attempt)
                if delay > 0:
                    time.sleep(delay)

        if last_info is None:
            last_info = ProviderErrorInfo(
                provider=provider_name,
                operation=operation,
                error_class="unknown",
                message=f"Provider '{provider_name}' {operation} failed with unknown error.",
                raw_message=f"Provider '{provider_name}' {operation} failed with unknown error.",
                retryable=False,
            )
        raise ProviderOperationError(last_info)

    def _record_provider_success(self, provider_name: str) -> None:
        with self._provider_state_lock:
            self._provider_failure_counts[provider_name] = 0
            self._provider_circuit_until.pop(provider_name, None)

    def _record_provider_failure(self, provider_name: str) -> None:
        threshold = max(1, int(self.config.provider_circuit_failure_threshold))
        cooldown = max(1.0, float(self.config.provider_circuit_cooldown_sec))
        opened = False
        with self._provider_state_lock:
            failures = self._provider_failure_counts.get(provider_name, 0) + 1
            self._provider_failure_counts[provider_name] = failures
            if failures >= threshold:
                self._provider_circuit_until[provider_name] = time.monotonic() + cooldown
                opened = True
        if opened:
            self.logger.warning(
                "provider_circuit_open provider=%s failures=%s cooldown_sec=%.2f",
                provider_name,
                failures,
                cooldown,
            )

    def _is_provider_circuit_open(self, provider_name: str) -> bool:
        with self._provider_state_lock:
            until = self._provider_circuit_until.get(provider_name)
            if until is None:
                return False
            if time.monotonic() >= until:
                self._provider_circuit_until.pop(provider_name, None)
                self._provider_failure_counts[provider_name] = 0
                return False
            return True

    def _provider_cooldown_remaining(self, provider_name: str) -> float:
        with self._provider_state_lock:
            until = self._provider_circuit_until.get(provider_name)
            if until is None:
                return 0.0
            remaining = max(0.0, until - time.monotonic())
            if remaining <= 0.0:
                self._provider_circuit_until.pop(provider_name, None)
                self._provider_failure_counts[provider_name] = 0
                return 0.0
            return remaining

    def _provider_failure_count(self, provider_name: str) -> int:
        with self._provider_state_lock:
            return int(self._provider_failure_counts.get(provider_name, 0))

    def _provider_retry_delay(self, *, attempt: int) -> float:
        base = max(0.0, float(self.config.provider_retry_backoff_sec))
        jitter = max(0.0, float(self.config.provider_retry_jitter_sec))
        delay = base * (2 ** max(0, attempt - 1))
        if jitter > 0:
            delay += random.uniform(0.0, jitter)
        return max(0.0, delay)

    @staticmethod
    def _is_cloud_provider(provider_name: str) -> bool:
        return provider_name in {"openai", "openrouter", "anthropic"}

    def _estimate_budget_units(
        self,
        *,
        messages: list[dict[str, Any]],
        max_tokens: int,
    ) -> int:
        input_chars = 0
        for item in messages:
            content = item.get("content")
            if isinstance(content, str):
                input_chars += len(content)
            elif content is not None:
                input_chars += len(str(content))
        # rough budget approximation: prompt chars + expected generation pressure
        return max(1, input_chars + (max(1, int(max_tokens)) * 4))

    def _provider_guardrail_status(self, provider_name: str) -> dict[str, Any]:
        if not self._is_cloud_provider(provider_name):
            return {
                "enabled": False,
            }
        now = time.monotonic()
        with self._guardrail_lock:
            rate_records = self._cloud_rate_records.setdefault(provider_name, deque())
            budget_records = self._cloud_budget_records.setdefault(provider_name, deque())
            self._prune_guardrail_deques(now=now, rate_records=rate_records, budget_records=budget_records)
            used_units = sum(units for _, units in budget_records)
            return {
                "enabled": True,
                "rate_window_sec": self.config.cloud_rate_window_sec,
                "rate_max_requests": self.config.cloud_rate_max_requests,
                "rate_used_requests": len(rate_records),
                "budget_window_sec": self.config.cloud_budget_window_sec,
                "budget_max_units": self.config.cloud_budget_max_units,
                "budget_used_units": used_units,
                "budget_remaining_units": max(0, self.config.cloud_budget_max_units - used_units),
            }

    def _provider_guardrail_pressure(self, provider_name: str) -> float:
        status = self._provider_guardrail_status(provider_name)
        if not bool(status.get("enabled", False)):
            return 0.0

        rate_max = max(1, int(status.get("rate_max_requests", 1)))
        rate_used = max(0, int(status.get("rate_used_requests", 0)))
        budget_max = max(1, int(status.get("budget_max_units", 1)))
        budget_used = max(0, int(status.get("budget_used_units", 0)))

        rate_ratio = float(rate_used) / float(rate_max)
        budget_ratio = float(budget_used) / float(budget_max)
        return max(0.0, min(1.0, max(rate_ratio, budget_ratio)))

    def _provider_guardrail_penalty(self, provider_name: str) -> float:
        pressure = self._provider_guardrail_pressure(provider_name)
        if pressure >= 0.95:
            return 1.1
        if pressure >= 0.85:
            return 0.55
        if pressure >= 0.75:
            return 0.25
        if pressure >= 0.60:
            return 0.10
        return 0.0

    def _enforce_cloud_guardrails(
        self,
        *,
        provider_name: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
    ) -> None:
        if not self._is_cloud_provider(provider_name):
            return

        now = time.monotonic()
        request_units = self._estimate_budget_units(messages=messages, max_tokens=max_tokens)
        with self._guardrail_lock:
            rate_records = self._cloud_rate_records.setdefault(provider_name, deque())
            budget_records = self._cloud_budget_records.setdefault(provider_name, deque())
            self._prune_guardrail_deques(now=now, rate_records=rate_records, budget_records=budget_records)

            rate_used = len(rate_records)
            rate_limit = max(1, int(self.config.cloud_rate_max_requests))
            if rate_used >= rate_limit:
                raise RuntimeError(
                    (
                        f"Cloud provider rate limit reached for '{provider_name}': "
                        f"{rate_used}/{rate_limit} requests in "
                        f"{self.config.cloud_rate_window_sec:.0f}s window."
                    )
                )

            budget_used = sum(units for _, units in budget_records)
            budget_limit = max(1, int(self.config.cloud_budget_max_units))
            if budget_used + request_units > budget_limit:
                raise RuntimeError(
                    (
                        f"Cloud provider budget limit reached for '{provider_name}': "
                        f"{budget_used + request_units}/{budget_limit} units in "
                        f"{self.config.cloud_budget_window_sec:.0f}s window."
                    )
                )

            rate_records.append(now)
            budget_records.append((now, request_units))

    def _prune_guardrail_deques(
        self,
        *,
        now: float,
        rate_records: deque[float],
        budget_records: deque[tuple[float, int]],
    ) -> None:
        rate_cutoff = now - max(1.0, float(self.config.cloud_rate_window_sec))
        while rate_records and rate_records[0] < rate_cutoff:
            rate_records.popleft()

        budget_cutoff = now - max(1.0, float(self.config.cloud_budget_window_sec))
        while budget_records and budget_records[0][0] < budget_cutoff:
            budget_records.popleft()
