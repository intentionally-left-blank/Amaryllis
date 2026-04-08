from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import inspect
import json
import logging
import os
import platform
import random
import re
from threading import Lock, Thread
import time
from typing import Any, Iterator
from uuid import uuid4

from models.model_artifact_admission import (
    evaluate_license_admission,
    validate_model_package_manifest,
)
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


@dataclass
class _ProviderRuntimeState:
    failure_count: int = 0
    circuit_until_monotonic: float | None = None
    latest_failure_started_at: float = 0.0


@dataclass
class _ModelDownloadJob:
    id: str
    provider: str
    model: str
    status: str
    progress: float
    completed_bytes: int | None
    total_bytes: int | None
    message: str | None
    error: str | None
    result: dict[str, Any] | None
    created_at: str
    updated_at: str
    finished_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "model": self.model,
            "status": self.status,
            "progress": self.progress,
            "completed_bytes": self.completed_bytes,
            "total_bytes": self.total_bytes,
            "message": self.message,
            "error": self.error,
            "result": self.result,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "finished_at": self.finished_at,
        }


@dataclass
class _PersonalizationRegistryState:
    adapters: dict[str, dict[str, Any]]
    active_by_scope: dict[str, str]


def _parse_bool_env(value: Any) -> bool:
    normalized = str(value or "").strip().lower()
    return normalized in {"1", "true", "yes", "on"}


class ModelManager:
    def __init__(
        self,
        config: AppConfig,
        database: Database,
        entitlement_resolver: Any | None = None,
    ) -> None:
        self.config = config
        self.database = database
        self.entitlement_resolver = entitlement_resolver
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

        self._active_target_lock = Lock()
        self.active_provider = database.get_setting("active_provider", config.default_provider) or config.default_provider
        self.active_model = database.get_setting("active_model", config.default_model) or config.default_model

        if self.active_provider not in self.providers:
            self.active_provider = config.default_provider if config.default_provider in self.providers else "mlx"
            self.database.set_setting("active_provider", self.active_provider)
            if self.active_model:
                self.database.set_setting("active_model", self.active_model)

        self._suggested_cache: dict[str, list[dict[str, Any]]] = {}
        self._suggested_cache_until: float = 0.0
        self._suggested_cache_ttl_seconds = 6 * 60 * 60
        self._suggested_cache_lock = Lock()
        self._suggested_refresh_inflight = False
        self._provider_models_cache: dict[str, dict[str, Any]] = {}
        self._provider_models_refreshing: set[str] = set()
        self._provider_models_cache_lock = Lock()
        self._provider_models_cache_ttl_local_sec = 20.0
        self._provider_models_cache_ttl_cloud_sec = 180.0
        self._provider_states: dict[str, _ProviderRuntimeState] = {}
        self._provider_state_lock = Lock()
        self._cloud_rate_records: dict[str, deque[float]] = {}
        self._cloud_budget_records: dict[str, deque[tuple[float, int]]] = {}
        self._guardrail_lock = Lock()
        self._session_route_pins: dict[str, dict[str, Any]] = {}
        self._recent_failover_events: deque[dict[str, Any]] = deque(maxlen=500)
        self._route_lock = Lock()
        self._download_lock = Lock()
        self._download_jobs: dict[str, _ModelDownloadJob] = {}
        self._download_job_order: deque[str] = deque(maxlen=800)
        self._personalization_lock = Lock()
        self._personalization_registry_setting_key = "personalization_adapter_registry_v1"
        self._personalization_registry = self._load_personalization_registry()

    def list_models(
        self,
        *,
        include_suggested: bool = True,
        include_remote_providers: bool = True,
        max_items_per_provider: int | None = None,
    ) -> dict[str, Any]:
        active_provider, active_model = self._active_target()
        provider_payload: dict[str, Any] = {}
        item_limit = max(1, int(max_items_per_provider)) if max_items_per_provider else None

        for name, provider in self.providers.items():
            is_cloud = self._is_cloud_provider(name)
            include_provider = include_remote_providers or not is_cloud
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
                if include_provider:
                    provider_available, provider_error, provider_items = self._list_provider_models_cached(
                        provider_name=name,
                        provider=provider,
                        allow_background_refresh=is_cloud,
                    )
                else:
                    provider_available, provider_error, provider_items = self._cached_provider_payload_or_default(
                        provider_name=name,
                        include_items=not is_cloud,
                    )
                if item_limit is not None and len(provider_items) > item_limit:
                    provider_items = provider_items[:item_limit]
                provider_payload[name] = {
                    "available": provider_available,
                    "error": provider_error,
                    "items": provider_items,
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
                "provider": active_provider,
                "model": active_model,
            },
            "providers": provider_payload,
            "capabilities": self.provider_capabilities(),
            "suggested": self._get_suggested_models() if include_suggested else {},
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
        active_provider, _ = self._active_target()
        checks: dict[str, Any] = {}
        for name, provider in self.providers.items():
            start = time.perf_counter()
            try:
                if self._is_provider_circuit_open(name):
                    latency_ms = round((time.perf_counter() - start) * 1000.0, 2)
                    checks[name] = {
                        "status": "circuit_open",
                        "latency_ms": latency_ms,
                        "active": name == active_provider,
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
                    "active": name == active_provider,
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
                    "active": name == active_provider,
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
        active_provider, active_model = self._active_target()
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
                "provider": active_provider,
                "model": active_model,
            },
            "providers": provider_caps,
            "count": len(items),
            "items": sorted(items, key=lambda row: (str(row["provider"]), str(row["model"]))),
            "by_provider": by_provider,
        }

    def recommend_onboarding_profile(self) -> dict[str, Any]:
        active_provider, active_model = self._active_target()
        provider_caps = self.provider_capabilities()
        candidates = self._build_model_candidates(
            provider_capabilities=provider_caps,
            include_suggested=True,
            limit_per_provider=120,
        )
        hardware = self._onboarding_hardware_snapshot(provider_capabilities=provider_caps)
        recommended_profile, reason_codes = self._onboarding_profile_from_hardware(hardware)
        profile_specs = self._onboarding_profile_specs(hardware.get("memory_gb"))

        profiles: dict[str, Any] = {}
        for profile_id, spec in profile_specs.items():
            constraints = RoutingConstraints(
                mode=str(spec["route_mode"]),
                require_stream=True,
                require_tools=False,
                prefer_local=spec.get("prefer_local"),
                min_params_b=spec.get("min_params_b"),
                max_params_b=spec.get("max_params_b"),
            )
            selected, fallbacks, considered_count = self._select_onboarding_route(
                candidates=candidates,
                constraints=constraints,
                active_provider=active_provider,
                active_model=active_model,
            )
            profiles[profile_id] = {
                "id": profile_id,
                "route_mode": str(spec["route_mode"]),
                "intent": str(spec["intent"]),
                "constraints": self._route_constraints_dict(constraints),
                "selected": selected,
                "fallbacks": fallbacks,
                "considered_count": considered_count,
            }

        if recommended_profile not in profiles:
            recommended_profile = "balanced"
            reason_codes.append("fallback_balanced_profile")

        return {
            "generated_at": self._utc_now_iso(),
            "active": {
                "provider": active_provider,
                "model": active_model,
            },
            "hardware": hardware,
            "recommended_profile": recommended_profile,
            "reason_codes": reason_codes,
            "profiles": profiles,
        }

    def onboarding_activation_plan(
        self,
        *,
        profile: str | None = None,
        include_remote_providers: bool = True,
        limit: int = 120,
        require_metadata: bool | None = None,
    ) -> dict[str, Any]:
        onboarding = self.recommend_onboarding_profile()
        recommended_profile = self._normalize_onboarding_profile(
            str(onboarding.get("recommended_profile", "balanced")),
            fallback="balanced",
        )
        selected_profile = self._normalize_onboarding_profile(profile, fallback=recommended_profile)
        catalog = self.model_package_catalog(
            profile=selected_profile,
            include_remote_providers=include_remote_providers,
            limit=limit,
        )

        effective_require_metadata = (
            self._license_metadata_required_for_onboarding()
            if require_metadata is None
            else bool(require_metadata)
        )

        package_rows_raw = catalog.get("packages")
        package_rows = package_rows_raw if isinstance(package_rows_raw, list) else []
        package_by_id: dict[str, dict[str, Any]] = {}
        for row in package_rows:
            if not isinstance(row, dict):
                continue
            package_id = str(row.get("package_id", "")).strip()
            if not package_id:
                continue
            package_by_id[package_id] = row

        top_package_ids: list[str] = []
        profile_items = catalog.get("profiles")
        if isinstance(profile_items, dict):
            selected_profile_payload = profile_items.get(selected_profile)
            if isinstance(selected_profile_payload, dict):
                top_items = selected_profile_payload.get("top_package_ids")
                if isinstance(top_items, list):
                    top_package_ids = [str(item).strip() for item in top_items if str(item).strip()]

        selected_package: dict[str, Any] = {}
        selected_package_id = ""
        for package_id in top_package_ids:
            candidate = package_by_id.get(package_id)
            if candidate is None:
                continue
            selected_package = candidate
            selected_package_id = package_id
            break
        if not selected_package and package_rows:
            fallback = package_rows[0]
            if isinstance(fallback, dict):
                selected_package = fallback
                selected_package_id = str(fallback.get("package_id", "")).strip()

        blockers: list[str] = []
        ready_to_install = False
        license_admission: dict[str, Any] = {
            "package_id": selected_package_id,
            "status": "deny",
            "admitted": False,
            "errors": [],
            "warnings": [],
            "summary": {},
            "require_metadata": effective_require_metadata,
        }
        install_contract = dict(selected_package.get("install") or {}) if selected_package else {}

        if selected_package_id:
            try:
                license_admission = self.model_package_license_admission(
                    package_id=selected_package_id,
                    require_metadata=effective_require_metadata,
                )
            except Exception as exc:
                license_admission = {
                    "package_id": selected_package_id,
                    "provider": str(selected_package.get("provider", "")),
                    "model": str(selected_package.get("model", "")),
                    "status": "deny",
                    "admitted": False,
                    "errors": [f"license_admission_error:{exc}"],
                    "warnings": [],
                    "summary": {},
                    "require_metadata": effective_require_metadata,
                }
            blockers = [str(item) for item in license_admission.get("errors", []) if str(item).strip()]
            ready_to_install = bool(license_admission.get("admitted"))
        else:
            blockers = ["no_package_candidates_for_profile"]
            license_admission["errors"] = list(blockers)

        return {
            "plan_version": "onboarding_activation_plan_v1",
            "generated_at": self._utc_now_iso(),
            "active": onboarding.get("active") or catalog.get("active") or {},
            "hardware": onboarding.get("hardware") or catalog.get("hardware") or {},
            "recommended_profile": recommended_profile,
            "selected_profile": selected_profile,
            "reason_codes": [str(item) for item in onboarding.get("reason_codes", []) if str(item).strip()],
            "profiles": onboarding.get("profiles") if isinstance(onboarding.get("profiles"), dict) else {},
            "catalog": {
                "count": int(catalog.get("count", len(package_rows))),
                "top_package_ids": top_package_ids,
            },
            "selected_package_id": selected_package_id,
            "selected_package": selected_package,
            "license_admission": license_admission,
            "require_metadata": effective_require_metadata,
            "ready_to_install": ready_to_install,
            "blockers": blockers,
            "next_action": "install_package" if ready_to_install else "resolve_blockers",
            "install": install_contract,
        }

    def onboarding_activate(
        self,
        *,
        profile: str | None = None,
        include_remote_providers: bool = True,
        limit: int = 120,
        require_metadata: bool | None = None,
        activate: bool = True,
        run_smoke_test: bool = True,
        smoke_prompt: str | None = None,
    ) -> dict[str, Any]:
        plan = self.onboarding_activation_plan(
            profile=profile,
            include_remote_providers=include_remote_providers,
            limit=limit,
            require_metadata=require_metadata,
        )
        selected_package_id = str(plan.get("selected_package_id", "")).strip()
        blockers = [str(item) for item in plan.get("blockers", []) if str(item).strip()]
        if not selected_package_id:
            if not blockers:
                blockers = ["no_package_selected"]
            return {
                "activation_version": "onboarding_activate_v1",
                "generated_at": self._utc_now_iso(),
                "status": "blocked",
                "ready": False,
                "selected_profile": str(plan.get("selected_profile", "")),
                "selected_package_id": "",
                "blockers": blockers,
                "activation_plan": plan,
                "install": {},
                "smoke_test": {
                    "requested": bool(run_smoke_test),
                    "status": "skipped",
                    "reason": "activation_blocked",
                },
                "active": plan.get("active") if isinstance(plan.get("active"), dict) else {},
            }
        if not bool(plan.get("ready_to_install")):
            if not blockers:
                blockers = ["activation_plan_not_ready"]
            return {
                "activation_version": "onboarding_activate_v1",
                "generated_at": self._utc_now_iso(),
                "status": "blocked",
                "ready": False,
                "selected_profile": str(plan.get("selected_profile", "")),
                "selected_package_id": selected_package_id,
                "blockers": blockers,
                "activation_plan": plan,
                "install": {},
                "smoke_test": {
                    "requested": bool(run_smoke_test),
                    "status": "skipped",
                    "reason": "activation_blocked",
                },
                "active": plan.get("active") if isinstance(plan.get("active"), dict) else {},
            }

        install_result = self.install_model_package(package_id=selected_package_id, activate=activate)
        smoke_result: dict[str, Any] = {
            "requested": bool(run_smoke_test),
            "status": "skipped",
        }
        status = "activated"
        if run_smoke_test:
            if activate:
                prompt = str(smoke_prompt or "").strip() or "Reply with a short readiness confirmation."
                try:
                    smoke_response = self.chat(
                        messages=[{"role": "user", "content": prompt}],
                        provider=str(install_result.get("provider", "")).strip() or None,
                        model=str(install_result.get("model", "")).strip() or None,
                        temperature=0.0,
                        max_tokens=96,
                    )
                    content = str(smoke_response.get("content", "")).strip()
                    smoke_result = {
                        "requested": True,
                        "status": "passed",
                        "prompt": prompt,
                        "response_preview": content[:240],
                        "provider": str(smoke_response.get("provider", "")).strip(),
                        "model": str(smoke_response.get("model", "")).strip(),
                    }
                except Exception as exc:
                    smoke_result = {
                        "requested": True,
                        "status": "failed",
                        "prompt": prompt,
                        "error": str(exc),
                    }
                    status = "activated_with_smoke_warning"
            else:
                smoke_result = {
                    "requested": True,
                    "status": "skipped",
                    "reason": "activate_disabled",
                }

        return {
            "activation_version": "onboarding_activate_v1",
            "generated_at": self._utc_now_iso(),
            "status": status,
            "ready": status == "activated",
            "selected_profile": str(plan.get("selected_profile", "")),
            "selected_package_id": selected_package_id,
            "blockers": [],
            "activation_plan": plan,
            "install": install_result,
            "smoke_test": smoke_result,
            "active": (
                install_result.get("active")
                if isinstance(install_result.get("active"), dict)
                else (plan.get("active") if isinstance(plan.get("active"), dict) else {})
            ),
        }

    def model_package_catalog(
        self,
        *,
        profile: str | None = None,
        include_remote_providers: bool = True,
        limit: int = 120,
    ) -> dict[str, Any]:
        normalized_limit = max(1, min(int(limit), 500))
        active_provider, active_model = self._active_target()
        provider_caps = self.provider_capabilities()
        hardware = self._onboarding_hardware_snapshot(provider_capabilities=provider_caps)
        recommended_profile, _ = self._onboarding_profile_from_hardware(hardware)
        selected_profile = self._normalize_onboarding_profile(profile, fallback=recommended_profile)
        profile_specs = self._onboarding_profile_specs(hardware.get("memory_gb"))
        profile_constraints: dict[str, RoutingConstraints] = {}
        for profile_id, spec in profile_specs.items():
            profile_constraints[profile_id] = RoutingConstraints(
                mode=str(spec["route_mode"]),
                require_stream=True,
                require_tools=False,
                prefer_local=spec.get("prefer_local"),
                min_params_b=spec.get("min_params_b"),
                max_params_b=spec.get("max_params_b"),
            )

        memory_gb: float | None = None
        try:
            raw_memory = hardware.get("memory_gb")
            memory_gb = float(raw_memory) if raw_memory is not None else None
        except Exception:
            memory_gb = None

        candidates = self._build_model_candidates(
            provider_capabilities=provider_caps,
            include_suggested=True,
            limit_per_provider=normalized_limit,
        )
        rows: list[dict[str, Any]] = []
        for candidate in candidates:
            if not include_remote_providers and not candidate.local:
                continue

            profile_scores: dict[str, float] = {}
            for profile_id, constraints in profile_constraints.items():
                score = score_candidate(candidate, constraints)
                if score is None:
                    continue
                profile_scores[profile_id] = score - self._provider_guardrail_penalty(candidate.provider)
            if not profile_scores:
                continue
            license_admission = self._license_admission_for_target(
                provider_name=candidate.provider,
                model_name=candidate.model,
                metadata=candidate.metadata if isinstance(candidate.metadata, dict) else {},
                require_metadata=self._license_metadata_required_for_onboarding(),
            )

            rows.append(
                self._package_row_from_candidate(
                    candidate=candidate,
                    memory_gb=memory_gb,
                    active_provider=active_provider,
                    active_model=active_model,
                    profile_scores=profile_scores,
                    license_admission=license_admission,
                )
            )

        rows.sort(
            key=lambda row: (
                1 if bool(row.get("active")) else 0,
                1 if bool(row.get("installed")) else 0,
                self._fit_rank_for_package(str((row.get("compatibility") or {}).get("fit", "unknown"))),
                float((row.get("profile_scores") or {}).get(selected_profile, -999.0)),
            ),
            reverse=True,
        )
        trimmed = rows[:normalized_limit]
        top_by_profile = self._build_profile_top_packages(
            rows=trimmed,
            profile_ids=list(profile_constraints.keys()),
            top_k=6,
        )

        profiles_payload: dict[str, Any] = {}
        for profile_id, spec in profile_specs.items():
            profiles_payload[profile_id] = {
                "route_mode": str(spec["route_mode"]),
                "top_package_ids": top_by_profile.get(profile_id, []),
            }

        return {
            "catalog_version": "model_package_catalog_v1",
            "generated_at": self._utc_now_iso(),
            "active": {
                "provider": active_provider,
                "model": active_model,
            },
            "hardware": hardware,
            "recommended_profile": recommended_profile,
            "selected_profile": selected_profile,
            "profiles": profiles_payload,
            "count": len(trimmed),
            "packages": trimmed,
        }

    def install_model_package(
        self,
        *,
        package_id: str,
        activate: bool = True,
    ) -> dict[str, Any]:
        provider_name, model_name = self._parse_package_id(package_id)
        if provider_name not in self.providers:
            raise ValueError(f"Unknown provider: {provider_name}")

        license_admission = self.model_package_license_admission(
            package_id=package_id,
            require_metadata=None,
        )
        if not bool(license_admission.get("admitted")):
            reasons = ", ".join([str(item) for item in license_admission.get("errors", [])[:4]])
            raise ValueError(
                "Model package license admission failed "
                f"provider={provider_name} model={model_name} errors={reasons}"
            )

        provider_caps = self.provider_capabilities().get(provider_name, {})
        supports_download = bool(provider_caps.get("supports_download", False))
        installed_before = False
        if supports_download:
            installed_before = self._model_installed(provider_name=provider_name, model_name=model_name)

        steps: list[dict[str, Any]] = [
            {
                "step": "license_admission",
                "status": "completed",
                "admitted": True,
                "policy_id": str((license_admission.get("summary") or {}).get("license_policy_id") or ""),
                "warnings": [str(item) for item in license_admission.get("warnings", [])],
            }
        ]
        download_result: dict[str, Any] | None = None
        if supports_download:
            if installed_before:
                steps.append(
                    {
                        "step": "download",
                        "status": "skipped",
                        "reason": "already_installed",
                    }
                )
            else:
                download_result = self.download_model(model_id=model_name, provider=provider_name)
                steps.append(
                    {
                        "step": "download",
                        "status": "completed",
                    }
                )
        else:
            steps.append(
                {
                    "step": "download",
                    "status": "skipped",
                    "reason": "provider_download_not_supported",
                }
            )

        load_result: dict[str, Any] | None = None
        if activate:
            load_result = self.load_model(model_id=model_name, provider=provider_name)
            steps.append(
                {
                    "step": "activate",
                    "status": "completed",
                }
            )
        else:
            steps.append(
                {
                    "step": "activate",
                    "status": "skipped",
                    "reason": "activate_disabled",
                }
            )

        final_provider, final_model = self._active_target()
        return {
            "package_id": self._package_id_from_target(provider_name=provider_name, model_name=model_name),
            "provider": provider_name,
            "model": model_name,
            "license_admission": license_admission,
            "download": download_result,
            "load": load_result,
            "steps": steps,
            "active": {
                "provider": final_provider,
                "model": final_model,
            },
        }

    def model_package_license_admission(
        self,
        *,
        package_id: str,
        require_metadata: bool | None = None,
    ) -> dict[str, Any]:
        provider_name, model_name = self._parse_package_id(package_id)
        if provider_name not in self.providers:
            raise ValueError(f"Unknown provider: {provider_name}")
        effective_require_metadata = (
            self._license_metadata_required_for_onboarding()
            if require_metadata is None
            else bool(require_metadata)
        )
        admission = self._license_admission_for_target(
            provider_name=provider_name,
            model_name=model_name,
            metadata=self._lookup_model_metadata(provider_name=provider_name, model_name=model_name),
            require_metadata=effective_require_metadata,
        )
        return {
            **admission,
            "package_id": self._package_id_from_target(provider_name=provider_name, model_name=model_name),
            "require_metadata": effective_require_metadata,
        }

    def personalization_adapter_contract(self) -> dict[str, Any]:
        with self._personalization_lock:
            adapters_total = len(self._personalization_registry.adapters)
            active_scopes = len(self._personalization_registry.active_by_scope)
        return {
            "contract_version": "personalization_adapter_contract_v1",
            "generated_at": self._utc_now_iso(),
            "policy": {
                "signature_algorithm": "hmac-sha256",
                "signature_key_env": "AMARYLLIS_ADAPTER_SIGNING_KEY",
                "signature_key_id_env": "AMARYLLIS_ADAPTER_KEY_ID",
                "require_managed_trust": self.config.security_profile == "production",
                "max_active_adapters_per_scope": 1,
                "scope": "user_id + base_package_id",
                "base_model_immutable": True,
            },
            "summary": {
                "adapters_total": adapters_total,
                "active_scopes_total": active_scopes,
            },
        }

    def list_personalization_adapters(
        self,
        *,
        user_id: str,
        base_package_id: str | None = None,
    ) -> dict[str, Any]:
        normalized_user = str(user_id or "").strip()
        if not normalized_user:
            raise ValueError("user_id is required")

        normalized_base = str(base_package_id or "").strip()
        if normalized_base:
            provider_name, _ = self._parse_package_id(normalized_base)
            if provider_name not in self.providers:
                raise ValueError(f"Unknown provider in base_package_id: {provider_name}")

        with self._personalization_lock:
            rows: list[dict[str, Any]] = []
            for adapter in self._personalization_registry.adapters.values():
                if not isinstance(adapter, dict):
                    continue
                if str(adapter.get("user_id") or "").strip() != normalized_user:
                    continue
                if normalized_base and str(adapter.get("base_package_id") or "").strip() != normalized_base:
                    continue
                rows.append(dict(adapter))

            rows.sort(
                key=lambda item: (
                    str(item.get("updated_at") or ""),
                    str(item.get("created_at") or ""),
                    str(item.get("adapter_id") or ""),
                ),
                reverse=True,
            )

            active_by_scope: dict[str, str] = {}
            for scope_key, adapter_id in self._personalization_registry.active_by_scope.items():
                parsed = self._parse_personalization_scope_key(scope_key)
                if parsed is None:
                    continue
                scope_user_id, scope_base_package_id = parsed
                if scope_user_id != normalized_user:
                    continue
                if normalized_base and scope_base_package_id != normalized_base:
                    continue
                active_by_scope[scope_base_package_id] = str(adapter_id)

        return {
            "registry_version": "personalization_registry_v1",
            "generated_at": self._utc_now_iso(),
            "user_id": normalized_user,
            "base_package_id": normalized_base or None,
            "count": len(rows),
            "active_by_base_package": active_by_scope,
            "items": rows,
        }

    def register_personalization_adapter(
        self,
        *,
        user_id: str,
        adapter_id: str,
        base_package_id: str,
        artifact_sha256: str,
        recipe_id: str,
        signature: dict[str, Any],
        metadata: dict[str, Any] | None = None,
        activate: bool = False,
    ) -> dict[str, Any]:
        normalized_user = str(user_id or "").strip()
        if not normalized_user:
            raise ValueError("user_id is required")
        normalized_adapter_id = str(adapter_id or "").strip()
        if not normalized_adapter_id:
            raise ValueError("adapter_id is required")
        normalized_base = str(base_package_id or "").strip()
        provider_name, _ = self._parse_package_id(normalized_base)
        if provider_name not in self.providers:
            raise ValueError(f"Unknown provider in base_package_id: {provider_name}")
        normalized_artifact_sha256 = self._normalize_sha256_hex(artifact_sha256)
        if normalized_artifact_sha256 is None:
            raise ValueError("artifact_sha256 must be a 64-char SHA-256 hex string")
        normalized_recipe_id = str(recipe_id or "").strip()
        if not normalized_recipe_id:
            raise ValueError("recipe_id is required")
        metadata_payload = dict(metadata) if isinstance(metadata, dict) else {}

        signature_error = self._verify_personalization_signature(
            user_id=normalized_user,
            adapter_id=normalized_adapter_id,
            base_package_id=normalized_base,
            artifact_sha256=normalized_artifact_sha256,
            recipe_id=normalized_recipe_id,
            metadata=metadata_payload,
            signature=signature,
        )
        if signature_error is not None:
            raise ValueError(signature_error)

        with self._personalization_lock:
            if normalized_adapter_id in self._personalization_registry.adapters:
                raise ValueError(f"adapter_id already exists: {normalized_adapter_id}")

            now = self._utc_now_iso()
            record = {
                "adapter_id": normalized_adapter_id,
                "user_id": normalized_user,
                "base_package_id": normalized_base,
                "artifact_sha256": normalized_artifact_sha256,
                "recipe_id": normalized_recipe_id,
                "metadata": metadata_payload,
                "signature": dict(signature),
                "status": "registered",
                "created_at": now,
                "updated_at": now,
                "activated_at": None,
                "previous_adapter_id": None,
                "rolled_back_at": None,
                "rollback_of": None,
            }
            self._personalization_registry.adapters[normalized_adapter_id] = record
            previous_adapter_id: str | None = None
            if bool(activate):
                previous_adapter_id = self._activate_personalization_adapter_unlocked(
                    user_id=normalized_user,
                    adapter_id=normalized_adapter_id,
                )
            self._persist_personalization_registry_unlocked()
            persisted = dict(self._personalization_registry.adapters[normalized_adapter_id])

        return {
            "registry_version": "personalization_registry_v1",
            "status": "activated" if bool(activate) else "registered",
            "adapter": persisted,
            "previous_active_adapter_id": previous_adapter_id,
            "generated_at": self._utc_now_iso(),
        }

    def activate_personalization_adapter(
        self,
        *,
        user_id: str,
        adapter_id: str,
    ) -> dict[str, Any]:
        normalized_user = str(user_id or "").strip()
        normalized_adapter_id = str(adapter_id or "").strip()
        if not normalized_user:
            raise ValueError("user_id is required")
        if not normalized_adapter_id:
            raise ValueError("adapter_id is required")

        with self._personalization_lock:
            previous_adapter_id = self._activate_personalization_adapter_unlocked(
                user_id=normalized_user,
                adapter_id=normalized_adapter_id,
            )
            self._persist_personalization_registry_unlocked()
            record = dict(self._personalization_registry.adapters[normalized_adapter_id])

        return {
            "registry_version": "personalization_registry_v1",
            "status": "activated",
            "adapter": record,
            "previous_active_adapter_id": previous_adapter_id,
            "generated_at": self._utc_now_iso(),
        }

    def rollback_personalization_adapter(
        self,
        *,
        user_id: str,
        base_package_id: str,
    ) -> dict[str, Any]:
        normalized_user = str(user_id or "").strip()
        if not normalized_user:
            raise ValueError("user_id is required")
        normalized_base = str(base_package_id or "").strip()
        provider_name, _ = self._parse_package_id(normalized_base)
        if provider_name not in self.providers:
            raise ValueError(f"Unknown provider in base_package_id: {provider_name}")

        with self._personalization_lock:
            scope_key = self._personalization_scope_key(
                user_id=normalized_user,
                base_package_id=normalized_base,
            )
            current_active_adapter_id = str(
                self._personalization_registry.active_by_scope.get(scope_key) or ""
            ).strip()
            if not current_active_adapter_id:
                raise ValueError("No active adapter to rollback for this base_package_id")

            current = self._personalization_registry.adapters.get(current_active_adapter_id)
            if not isinstance(current, dict):
                raise ValueError("Active adapter record is missing")
            if str(current.get("user_id") or "").strip() != normalized_user:
                raise ValueError("Adapter ownership mismatch")

            previous_adapter_id = str(current.get("previous_adapter_id") or "").strip()
            if not previous_adapter_id:
                raise ValueError("No previous adapter available for rollback")
            previous = self._personalization_registry.adapters.get(previous_adapter_id)
            if not isinstance(previous, dict):
                raise ValueError("Previous adapter record is missing")
            if str(previous.get("user_id") or "").strip() != normalized_user:
                raise ValueError("Previous adapter ownership mismatch")

            now = self._utc_now_iso()
            current["status"] = "rolled_back"
            current["updated_at"] = now
            current["rolled_back_at"] = now
            current["rollback_of"] = previous_adapter_id

            previous["status"] = "active"
            previous["updated_at"] = now
            previous["activated_at"] = now
            self._personalization_registry.active_by_scope[scope_key] = previous_adapter_id
            self._persist_personalization_registry_unlocked()

            rolled_back = dict(current)
            active = dict(previous)

        return {
            "registry_version": "personalization_registry_v1",
            "status": "rolled_back",
            "user_id": normalized_user,
            "base_package_id": normalized_base,
            "rolled_back_adapter": rolled_back,
            "active_adapter": active,
            "generated_at": self._utc_now_iso(),
        }

    def _activate_personalization_adapter_unlocked(
        self,
        *,
        user_id: str,
        adapter_id: str,
    ) -> str | None:
        adapter = self._personalization_registry.adapters.get(adapter_id)
        if not isinstance(adapter, dict):
            raise ValueError(f"Unknown adapter_id: {adapter_id}")
        adapter_user_id = str(adapter.get("user_id") or "").strip()
        if adapter_user_id != user_id:
            raise ValueError("Adapter does not belong to the user")
        base_package_id = str(adapter.get("base_package_id") or "").strip()
        if not base_package_id:
            raise ValueError("Adapter is missing base_package_id")

        scope_key = self._personalization_scope_key(
            user_id=adapter_user_id,
            base_package_id=base_package_id,
        )
        previous_adapter_id = str(self._personalization_registry.active_by_scope.get(scope_key) or "").strip()
        now = self._utc_now_iso()

        if previous_adapter_id and previous_adapter_id != adapter_id:
            previous = self._personalization_registry.adapters.get(previous_adapter_id)
            if isinstance(previous, dict):
                previous["status"] = "inactive"
                previous["updated_at"] = now

        adapter["status"] = "active"
        adapter["updated_at"] = now
        adapter["activated_at"] = now
        adapter["rolled_back_at"] = None
        adapter["rollback_of"] = None
        if previous_adapter_id and previous_adapter_id != adapter_id:
            adapter["previous_adapter_id"] = previous_adapter_id
        self._personalization_registry.active_by_scope[scope_key] = adapter_id
        return previous_adapter_id if previous_adapter_id and previous_adapter_id != adapter_id else None

    def _load_personalization_registry(self) -> _PersonalizationRegistryState:
        raw = str(self.database.get_setting(self._personalization_registry_setting_key, "") or "").strip()
        if not raw:
            return _PersonalizationRegistryState(adapters={}, active_by_scope={})
        try:
            payload = json.loads(raw)
        except Exception:
            return _PersonalizationRegistryState(adapters={}, active_by_scope={})
        if not isinstance(payload, dict):
            return _PersonalizationRegistryState(adapters={}, active_by_scope={})

        adapters_raw = payload.get("adapters")
        active_raw = payload.get("active_by_scope")
        adapters: dict[str, dict[str, Any]] = {}
        active_by_scope: dict[str, str] = {}
        if isinstance(adapters_raw, dict):
            for adapter_id, item in adapters_raw.items():
                if not isinstance(item, dict):
                    continue
                normalized_adapter_id = str(adapter_id or "").strip()
                if not normalized_adapter_id:
                    continue
                adapter = dict(item)
                adapter["adapter_id"] = normalized_adapter_id
                adapters[normalized_adapter_id] = adapter
        if isinstance(active_raw, dict):
            for scope_key, adapter_id in active_raw.items():
                normalized_scope_key = str(scope_key or "").strip()
                normalized_adapter_id = str(adapter_id or "").strip()
                if not normalized_scope_key or not normalized_adapter_id:
                    continue
                if normalized_adapter_id not in adapters:
                    continue
                active_by_scope[normalized_scope_key] = normalized_adapter_id
        return _PersonalizationRegistryState(adapters=adapters, active_by_scope=active_by_scope)

    def _persist_personalization_registry_unlocked(self) -> None:
        payload = {
            "schema_version": "personalization_registry_v1",
            "updated_at": self._utc_now_iso(),
            "adapters": self._personalization_registry.adapters,
            "active_by_scope": self._personalization_registry.active_by_scope,
        }
        self.database.set_setting(
            self._personalization_registry_setting_key,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
        )

    def _verify_personalization_signature(
        self,
        *,
        user_id: str,
        adapter_id: str,
        base_package_id: str,
        artifact_sha256: str,
        recipe_id: str,
        metadata: dict[str, Any],
        signature: dict[str, Any],
    ) -> str | None:
        if not isinstance(signature, dict):
            return "adapter.signature_missing"

        algorithm = str(signature.get("algorithm") or "").strip().lower()
        if algorithm != "hmac-sha256":
            return "adapter.signature.algorithm_invalid"
        key_id = str(signature.get("key_id") or "").strip()
        if not key_id:
            return "adapter.signature.key_id_missing"
        trust_level = str(signature.get("trust_level") or "").strip().lower()
        if trust_level not in {"managed", "development"}:
            return "adapter.signature.trust_level_invalid"
        if self.config.security_profile == "production" and trust_level != "managed":
            return "adapter.signature.trust_level_not_managed"

        expected_key_id = str(os.getenv("AMARYLLIS_ADAPTER_KEY_ID", "")).strip()
        if expected_key_id and key_id != expected_key_id:
            return "adapter.signature.key_id_mismatch"

        signature_value = self._normalize_sha256_hex(signature.get("value"))
        if signature_value is None:
            return "adapter.signature.value_invalid"

        signing_key = str(os.getenv("AMARYLLIS_ADAPTER_SIGNING_KEY", "")).strip()
        if not signing_key:
            return "adapter.signature.signing_key_missing"

        unsigned_payload = {
            "adapter_id": adapter_id,
            "artifact_sha256": artifact_sha256,
            "base_package_id": base_package_id,
            "metadata": metadata,
            "recipe_id": recipe_id,
            "user_id": user_id,
        }
        canonical = self._canonical_json(unsigned_payload)
        expected_signature = hmac.new(
            signing_key.encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected_signature, signature_value):
            return "adapter.signature.mismatch"
        return None

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
        active_provider, _ = self._active_target()
        provider_name = provider or active_provider
        selected = self.providers.get(provider_name)
        if selected is None:
            raise ValueError(f"Unknown provider: {provider_name}")
        license_admission = self._license_admission_for_target(
            provider_name=provider_name,
            model_name=model_id,
            metadata=self._lookup_model_metadata(provider_name=provider_name, model_name=model_id),
            require_metadata=self._license_metadata_required_for_onboarding(),
        )
        if not bool(license_admission.get("admitted")):
            reasons = ", ".join([str(item) for item in license_admission.get("errors", [])[:4]])
            raise ValueError(
                "Model download license admission failed "
                f"provider={provider_name} model={model_id} errors={reasons}"
            )

        result = self._download_via_provider(
            provider_name=provider_name,
            provider=selected,
            model_id=model_id,
            progress_callback=None,
        )
        result = self._enforce_model_artifact_admission(
            provider_name=provider_name,
            model_id=model_id,
            result=result,
        )
        result["license_admission"] = license_admission
        self._invalidate_provider_models_cache(provider_name)
        self._invalidate_suggested_cache()
        return result

    def start_model_download(self, model_id: str, provider: str | None = None) -> dict[str, Any]:
        active_provider, _ = self._active_target()
        provider_name = provider or active_provider
        selected = self.providers.get(provider_name)
        if selected is None:
            raise ValueError(f"Unknown provider: {provider_name}")
        license_admission = self._license_admission_for_target(
            provider_name=provider_name,
            model_name=model_id,
            metadata=self._lookup_model_metadata(provider_name=provider_name, model_name=model_id),
            require_metadata=self._license_metadata_required_for_onboarding(),
        )
        if not bool(license_admission.get("admitted")):
            reasons = ", ".join([str(item) for item in license_admission.get("errors", [])[:4]])
            raise ValueError(
                "Model download license admission failed "
                f"provider={provider_name} model={model_id} errors={reasons}"
            )

        with self._download_lock:
            running = self._find_running_download_unlocked(provider_name=provider_name, model_id=model_id)
            if running is not None:
                return {"job": running.to_dict(), "already_running": True}

            created_at = self._utc_now_iso()
            job_id = str(uuid4())
            job = _ModelDownloadJob(
                id=job_id,
                provider=provider_name,
                model=model_id,
                status="queued",
                progress=0.0,
                completed_bytes=0,
                total_bytes=None,
                message="Queued",
                error=None,
                result=None,
                created_at=created_at,
                updated_at=created_at,
                finished_at=None,
            )
            self._download_jobs[job_id] = job
            self._download_job_order.append(job_id)

        worker = Thread(
            target=self._run_model_download_job,
            args=(job_id, provider_name, model_id),
            daemon=True,
        )
        worker.start()
        return {"job": job.to_dict(), "already_running": False}

    def get_model_download_job(self, job_id: str) -> dict[str, Any]:
        normalized = str(job_id).strip()
        if not normalized:
            raise ValueError("job_id is required")
        with self._download_lock:
            job = self._download_jobs.get(normalized)
            if job is None:
                raise ValueError(f"Unknown download job: {normalized}")
            return job.to_dict()

    def list_model_download_jobs(self, limit: int = 100) -> dict[str, Any]:
        with self._download_lock:
            ordered_ids = list(self._download_job_order)[-max(1, limit) :]
            items = [self._download_jobs[job_id].to_dict() for job_id in reversed(ordered_ids) if job_id in self._download_jobs]
        return {
            "items": items,
            "count": len(items),
        }

    def load_model(self, model_id: str, provider: str | None = None) -> dict[str, Any]:
        active_provider, _ = self._active_target()
        provider_name = provider or active_provider
        selected = self.providers.get(provider_name)
        if selected is None:
            raise ValueError(f"Unknown provider: {provider_name}")

        result = selected.load_model(model_id)
        self._set_active_target(provider_name=provider_name, model_name=model_id)
        self._invalidate_provider_models_cache(provider_name)

        self.database.set_setting("active_provider", provider_name)
        self.database.set_setting("active_model", model_id)
        final_provider, final_model = self._active_target()

        return {
            **result,
            "active": {
                "provider": final_provider,
                "model": final_model,
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
        user_id: str | None = None,
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
                user_id=user_id,
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
                    user_id=user_id,
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
        user_id: str | None = None,
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
                    user_id=user_id,
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
            "provider_runtime": self._provider_runtime_snapshot(),
        }

    def _provider_runtime_snapshot(self) -> dict[str, dict[str, Any]]:
        now = time.monotonic()
        with self._provider_state_lock:
            snapshot: dict[str, dict[str, Any]] = {}
            for provider_name, state in self._provider_states.items():
                remaining = 0.0
                if state.circuit_until_monotonic is not None:
                    remaining = max(0.0, float(state.circuit_until_monotonic) - now)
                snapshot[str(provider_name)] = {
                    "failure_count": int(state.failure_count),
                    "circuit_open": bool(remaining > 0.0),
                    "circuit_remaining_sec": round(remaining, 3),
                    "latest_failure_started_at": float(state.latest_failure_started_at),
                }
            return snapshot

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
        explicit_model_without_provider = provider is None and model is not None
        explicit_local_model_target = provider in {"mlx", "ollama"} and model is not None
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

        if fallback_targets is not None:
            targets = fallback_targets
        elif routed_fallbacks:
            targets = routed_fallbacks
        elif explicit_model_without_provider or explicit_local_model_target:
            # Respect explicit user target. Automatic cross-provider fallback here
            # can hide the real root cause (e.g. selected MLX model error turning
            # into an unrelated Ollama error).
            targets = []
        else:
            targets = self._fallback_targets(
                provider_name=provider_name,
                model_name=model_name,
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
            elif error_info.error_class in {"rate_limit", "quota", "budget_limit", "auth", "entitlement"}:
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

    def _get_suggested_models(self) -> dict[str, list[dict[str, Any]]]:
        now = time.time()
        cached: dict[str, list[dict[str, Any]]] = {}
        cache_fresh = False
        with self._suggested_cache_lock:
            if self._suggested_cache:
                cached = self._clone_suggested_map(self._suggested_cache)
                cache_fresh = now < self._suggested_cache_until

        if not cached:
            cached = self._build_fallback_suggested_map(limit=120)
            with self._suggested_cache_lock:
                if not self._suggested_cache:
                    self._suggested_cache = self._clone_suggested_map(cached)
                    # Keep fallback cache short-lived so runtime can refresh quickly in background.
                    self._suggested_cache_until = now + 120.0

        if not cache_fresh:
            self._refresh_suggested_cache_async(limit=160)

        return cached

    def _refresh_suggested_cache_async(self, *, limit: int) -> None:
        with self._suggested_cache_lock:
            if self._suggested_refresh_inflight:
                return
            self._suggested_refresh_inflight = True

        def worker() -> None:
            try:
                suggested: dict[str, list[dict[str, Any]]] = {}
                for provider_name, provider in self.providers.items():
                    suggested[provider_name] = self._load_suggested_models_once(
                        provider_name=provider_name,
                        provider=provider,
                        limit=limit,
                    )

                with self._suggested_cache_lock:
                    self._suggested_cache = self._clone_suggested_map(suggested)
                    self._suggested_cache_until = time.time() + self._suggested_cache_ttl_seconds
            finally:
                with self._suggested_cache_lock:
                    self._suggested_refresh_inflight = False

        Thread(target=worker, daemon=True).start()

    def _load_suggested_models_once(
        self,
        *,
        provider_name: str,
        provider: ModelProvider,
        limit: int,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        suggested_getter = getattr(provider, "suggested_models", None)
        if callable(suggested_getter):
            try:
                raw_items = suggested_getter(limit=limit)
                items = self._normalize_suggested(raw_items)
            except Exception as exc:
                self.logger.warning(
                    "provider_suggested_models_failed provider=%s error=%s",
                    provider_name,
                    exc,
                )
        return items

    def _build_fallback_suggested_map(self, *, limit: int) -> dict[str, list[dict[str, Any]]]:
        suggested: dict[str, list[dict[str, Any]]] = {}
        for provider_name, provider in self.providers.items():
            rows: list[dict[str, Any]] = []
            fallback_getter = getattr(provider, "fallback_suggested_models", None)
            if callable(fallback_getter):
                try:
                    rows = self._normalize_suggested(fallback_getter(limit=limit))
                except Exception as exc:
                    self.logger.debug(
                        "provider_fallback_suggested_failed provider=%s error=%s",
                        provider_name,
                        exc,
                    )
            suggested[provider_name] = rows
        return suggested

    def _list_provider_models_cached(
        self,
        *,
        provider_name: str,
        provider: ModelProvider,
        allow_background_refresh: bool,
    ) -> tuple[bool, str | None, list[dict[str, Any]]]:
        now = time.time()
        cache = self._provider_models_cache_snapshot(provider_name=provider_name)
        ttl = self._provider_models_cache_ttl(provider_name)
        if cache is not None:
            age = max(0.0, now - float(cache.get("timestamp", 0.0)))
            if age <= ttl:
                return (
                    bool(cache.get("available", True)),
                    cache.get("error"),
                    self._copy_provider_items(cache.get("items")),
                )
            if allow_background_refresh:
                self._refresh_provider_models_async(provider_name=provider_name, provider=provider)
                return (
                    bool(cache.get("available", True)),
                    cache.get("error"),
                    self._copy_provider_items(cache.get("items")),
                )

        try:
            items = self._call_provider_resilient(
                provider_name=provider_name,
                operation="list_models",
                call=provider.list_models,
                max_attempts=1,
            )
            normalized = self._normalize_provider_items(items)
            self._store_provider_models_cache(
                provider_name=provider_name,
                available=True,
                error=None,
                items=normalized,
            )
            return True, None, self._copy_provider_items(normalized)
        except Exception as exc:
            message = str(exc)
            self._store_provider_models_cache(
                provider_name=provider_name,
                available=False,
                error=message,
                items=[],
            )
            if cache is not None:
                return (
                    bool(cache.get("available", False)),
                    cache.get("error") or message,
                    self._copy_provider_items(cache.get("items")),
                )
            return False, message, []

    def _cached_provider_payload_or_default(
        self,
        *,
        provider_name: str,
        include_items: bool = True,
    ) -> tuple[bool, str | None, list[dict[str, Any]]]:
        cache = self._provider_models_cache_snapshot(provider_name=provider_name)
        if cache is None:
            return True, None, []
        items = self._copy_provider_items(cache.get("items")) if include_items else []
        return (
            bool(cache.get("available", True)),
            cache.get("error"),
            items,
        )

    def _refresh_provider_models_async(self, *, provider_name: str, provider: ModelProvider) -> None:
        with self._provider_models_cache_lock:
            if provider_name in self._provider_models_refreshing:
                return
            self._provider_models_refreshing.add(provider_name)

        def worker() -> None:
            try:
                try:
                    items = self._call_provider_resilient(
                        provider_name=provider_name,
                        operation="list_models",
                        call=provider.list_models,
                        max_attempts=1,
                    )
                    normalized = self._normalize_provider_items(items)
                    self._store_provider_models_cache(
                        provider_name=provider_name,
                        available=True,
                        error=None,
                        items=normalized,
                    )
                except Exception as exc:
                    self._store_provider_models_cache(
                        provider_name=provider_name,
                        available=False,
                        error=str(exc),
                        items=[],
                    )
            finally:
                with self._provider_models_cache_lock:
                    self._provider_models_refreshing.discard(provider_name)

        Thread(target=worker, daemon=True).start()

    def _provider_models_cache_snapshot(self, *, provider_name: str) -> dict[str, Any] | None:
        with self._provider_models_cache_lock:
            raw = self._provider_models_cache.get(provider_name)
            if raw is None:
                return None
            return {
                "timestamp": float(raw.get("timestamp", 0.0)),
                "available": bool(raw.get("available", True)),
                "error": raw.get("error"),
                "items": self._copy_provider_items(raw.get("items")),
            }

    def _store_provider_models_cache(
        self,
        *,
        provider_name: str,
        available: bool,
        error: str | None,
        items: list[dict[str, Any]],
    ) -> None:
        with self._provider_models_cache_lock:
            self._provider_models_cache[provider_name] = {
                "timestamp": time.time(),
                "available": bool(available),
                "error": error,
                "items": self._copy_provider_items(items),
            }

    def _provider_models_cache_ttl(self, provider_name: str) -> float:
        if self._is_cloud_provider(provider_name):
            return float(self._provider_models_cache_ttl_cloud_sec)
        return float(self._provider_models_cache_ttl_local_sec)

    @staticmethod
    def _normalize_provider_items(items: Any) -> list[dict[str, Any]]:
        if not isinstance(items, list):
            return []
        rows: list[dict[str, Any]] = []
        for item in items:
            if isinstance(item, dict):
                rows.append(dict(item))
        return rows

    @staticmethod
    def _copy_provider_items(items: Any) -> list[dict[str, Any]]:
        if not isinstance(items, list):
            return []
        rows: list[dict[str, Any]] = []
        for item in items:
            if isinstance(item, dict):
                rows.append(dict(item))
        return rows

    @staticmethod
    def _normalize_suggested(items: Any) -> list[dict[str, Any]]:
        normalized: list[dict[str, Any]] = []
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
            size_bytes = ModelManager._to_int_or_none(raw.get("size_bytes"))
            seen.add(model_id)
            row: dict[str, Any] = {"id": model_id, "label": label}
            metadata = raw.get("metadata")
            metadata_payload = dict(metadata) if isinstance(metadata, dict) else {}
            for key in (
                "license",
                "spdx_id",
                "license_spdx_id",
                "license_source",
                "allows_commercial_use",
                "allows_derivatives",
                "requires_share_alike",
                "restrictions",
                "license_restrictions",
            ):
                if key in raw and key not in metadata_payload:
                    metadata_payload[key] = raw.get(key)
            if metadata_payload:
                row["metadata"] = metadata_payload
            if size_bytes is not None and size_bytes > 0:
                row["size_bytes"] = size_bytes
            else:
                estimated = estimate_model_size_b(model_id)
                if estimated is not None:
                    row["size_bytes"] = int(estimated * 1_000_000_000 * 0.56)
            normalized.append(row)

        return normalized

    def _build_model_candidates(
        self,
        *,
        provider_capabilities: dict[str, Any],
        include_suggested: bool,
        limit_per_provider: int,
    ) -> list[ModelCandidate]:
        active_provider, active_model = self._active_target()
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
                        active=bool(item.get("active", False) or model_id == active_model),
                        installed=bool(local),
                        metadata=metadata if isinstance(metadata, dict) else {},
                    )
                )

            defaults = [self._default_model_for_provider(provider_name)]
            if provider_name == active_provider and active_model:
                defaults.append(active_model)
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
                        active=provider_name == active_provider and normalized == active_model,
                        installed=bool(local and provider_name == active_provider and normalized == active_model),
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

    def _select_onboarding_route(
        self,
        *,
        candidates: list[ModelCandidate],
        constraints: RoutingConstraints,
        active_provider: str,
        active_model: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
        scored: list[tuple[float, ModelCandidate]] = []
        for candidate in candidates:
            score = score_candidate(candidate, constraints)
            if score is None:
                continue
            penalty = self._provider_guardrail_penalty(candidate.provider)
            scored.append((score - penalty, candidate))

        if not scored:
            return (
                {
                    "provider": active_provider,
                    "model": active_model,
                    "reason": "fallback_active_model",
                },
                [],
                0,
            )

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
        selected["reason"] = "onboarding_profile_route"

        fallbacks: list[dict[str, Any]] = []
        seen = {f"{selected_candidate.provider}:{selected_candidate.model}"}
        for score, candidate in scored[1:]:
            key = f"{candidate.provider}:{candidate.model}"
            if key in seen:
                continue
            seen.add(key)
            payload = candidate.to_dict()
            payload["score"] = score
            payload["guardrail_penalty"] = self._provider_guardrail_penalty(candidate.provider)
            fallbacks.append(payload)
            if len(fallbacks) >= 4:
                break
        return selected, fallbacks, len(scored)

    @staticmethod
    def _onboarding_profile_specs(memory_gb: Any) -> dict[str, dict[str, Any]]:
        normalized_memory: float | None
        try:
            normalized_memory = float(memory_gb) if memory_gb is not None else None
        except Exception:
            normalized_memory = None

        fast_max_params = 8.0
        if normalized_memory is not None and normalized_memory <= 8.0:
            fast_max_params = 4.0

        balanced_max_params: float | None = None
        if normalized_memory is not None and normalized_memory < 16.0:
            balanced_max_params = 12.0

        return {
            "fast": {
                "route_mode": "local_first",
                "prefer_local": True,
                "min_params_b": None,
                "max_params_b": fast_max_params,
                "intent": "Lowest latency and memory usage for first response.",
            },
            "balanced": {
                "route_mode": "balanced",
                "prefer_local": True,
                "min_params_b": None,
                "max_params_b": balanced_max_params,
                "intent": "Default quality/latency trade-off for daily usage.",
            },
            "quality": {
                "route_mode": "quality_first",
                "prefer_local": None,
                "min_params_b": 8.0,
                "max_params_b": None,
                "intent": "Best answer quality, may use more compute or remote providers.",
            },
        }

    @staticmethod
    def _onboarding_profile_from_hardware(hardware: dict[str, Any]) -> tuple[str, list[str]]:
        memory_gb: float | None = None
        cpu_count: int | None = None
        try:
            raw_memory = hardware.get("memory_gb")
            memory_gb = float(raw_memory) if raw_memory is not None else None
        except Exception:
            memory_gb = None
        try:
            raw_cpu = hardware.get("cpu_count_logical")
            cpu_count = int(raw_cpu) if raw_cpu is not None else None
        except Exception:
            cpu_count = None

        has_cloud = bool(hardware.get("cloud_provider_available", False))
        reasons: list[str] = []
        if memory_gb is not None and memory_gb < 12.0:
            reasons.append("low_memory")
        if cpu_count is not None and cpu_count <= 4:
            reasons.append("low_cpu")
        if reasons:
            return "fast", reasons

        high_compute = False
        if memory_gb is not None and cpu_count is not None and memory_gb >= 28.0 and cpu_count >= 10:
            high_compute = True
        elif memory_gb is not None and cpu_count is not None and memory_gb >= 20.0 and cpu_count >= 8 and has_cloud:
            high_compute = True
        if high_compute:
            return "quality", ["high_compute_headroom"]

        return "balanced", ["default_balanced_start"]

    def _onboarding_hardware_snapshot(
        self,
        *,
        provider_capabilities: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cpu_count = os.cpu_count() or 0
        memory_bytes = self._detect_system_memory_bytes()
        memory_gb: float | None = None
        if memory_bytes is not None and memory_bytes > 0:
            memory_gb = round(float(memory_bytes) / float(1024**3), 2)

        caps = provider_capabilities if isinstance(provider_capabilities, dict) else self.provider_capabilities()
        local_available = False
        cloud_available = False
        for item in caps.values():
            payload = item if isinstance(item, dict) else {}
            if bool(payload.get("local", False)):
                local_available = True
            else:
                cloud_available = True

        return {
            "platform": platform.system().lower(),
            "machine": platform.machine().lower(),
            "cpu_count_logical": int(cpu_count),
            "memory_bytes": memory_bytes,
            "memory_gb": memory_gb,
            "provider_count": len(caps),
            "local_provider_available": local_available,
            "cloud_provider_available": cloud_available,
        }

    @staticmethod
    def _detect_system_memory_bytes() -> int | None:
        try:
            page_size = os.sysconf("SC_PAGE_SIZE")
            page_count = os.sysconf("SC_PHYS_PAGES")
            if int(page_size) > 0 and int(page_count) > 0:
                return int(page_size) * int(page_count)
        except Exception:
            return None
        return None

    @staticmethod
    def _build_profile_top_packages(
        *,
        rows: list[dict[str, Any]],
        profile_ids: list[str],
        top_k: int = 6,
    ) -> dict[str, list[str]]:
        top: dict[str, list[str]] = {}
        for profile_id in profile_ids:
            ranked = sorted(
                rows,
                key=lambda row: float((row.get("profile_scores") or {}).get(profile_id, -999.0)),
                reverse=True,
            )
            picked: list[str] = []
            seen: set[str] = set()
            for row in ranked:
                package_id = str(row.get("package_id", "")).strip()
                if not package_id or package_id in seen:
                    continue
                seen.add(package_id)
                picked.append(package_id)
                if len(picked) >= max(1, top_k):
                    break
            top[profile_id] = picked
        return top

    def _package_row_from_candidate(
        self,
        *,
        candidate: ModelCandidate,
        memory_gb: float | None,
        active_provider: str,
        active_model: str,
        profile_scores: dict[str, float],
        license_admission: dict[str, Any],
    ) -> dict[str, Any]:
        package_id = self._package_id_from_target(
            provider_name=candidate.provider,
            model_name=candidate.model,
        )
        requirements = self._memory_requirements_for_candidate(candidate)
        compatibility_fit = self._memory_fit_for_candidate(
            memory_gb=memory_gb,
            requirements=requirements,
        )
        ranked_profiles = sorted(profile_scores.items(), key=lambda item: item[1], reverse=True)
        recommended_profiles = [profile_id for profile_id, _ in ranked_profiles[:2]]
        if not recommended_profiles:
            recommended_profiles = ["balanced"]

        return {
            "package_id": package_id,
            "provider": candidate.provider,
            "model": candidate.model,
            "label": candidate.metadata.get("label") or candidate.model,
            "source": candidate.source,
            "local": candidate.local,
            "installed": candidate.installed,
            "active": candidate.provider == active_provider and candidate.model == active_model,
            "quality_tier": candidate.quality_tier,
            "speed_tier": candidate.speed_tier,
            "tags": list(candidate.tags),
            "estimated_params_b": candidate.estimated_params_b,
            "estimated_download_bytes": self._estimated_download_size_bytes(candidate),
            "requirements": requirements,
            "compatibility": {
                "fit": compatibility_fit,
                "hardware_memory_gb": memory_gb,
            },
            "license_admission": license_admission,
            "recommended_profiles": recommended_profiles,
            "profile_scores": profile_scores,
            "install": {
                "endpoint": "/models/packages/install",
                "payload": {
                    "package_id": package_id,
                    "activate": True,
                },
                "license_admission_step": {
                    "endpoint": "/models/packages/license-admission",
                    "query": {
                        "package_id": package_id,
                        "require_metadata": self._license_metadata_required_for_onboarding(),
                    },
                },
                "download_step": {
                    "endpoint": "/models/download/start",
                    "payload": {
                        "model_id": candidate.model,
                        "provider": candidate.provider,
                    },
                },
                "activate_step": {
                    "endpoint": "/models/load",
                    "payload": {
                        "model_id": candidate.model,
                        "provider": candidate.provider,
                    },
                },
            },
        }

    def _lookup_model_metadata(self, *, provider_name: str, model_name: str) -> dict[str, Any]:
        provider = self.providers.get(provider_name)
        if provider is None:
            return {}

        def _extract_from_items(items: list[dict[str, Any]]) -> dict[str, Any]:
            target = str(model_name).strip()
            for item in items:
                if not isinstance(item, dict):
                    continue
                if str(item.get("id", "")).strip() != target:
                    continue
                metadata = item.get("metadata")
                payload = dict(metadata) if isinstance(metadata, dict) else {}
                for key in (
                    "license",
                    "spdx_id",
                    "license_spdx_id",
                    "license_source",
                    "allows_commercial_use",
                    "allows_derivatives",
                    "requires_share_alike",
                    "restrictions",
                    "license_restrictions",
                ):
                    if key in item and key not in payload:
                        payload[key] = item.get(key)
                return payload
            return {}

        try:
            _, _, listed = self._list_provider_models_cached(
                provider_name=provider_name,
                provider=provider,
                allow_background_refresh=False,
            )
            metadata = _extract_from_items(listed)
            if metadata:
                return metadata
        except Exception:
            pass

        suggested_getter = getattr(provider, "suggested_models", None)
        if callable(suggested_getter):
            try:
                suggested = self._normalize_suggested(suggested_getter(limit=300))
                metadata = _extract_from_items(suggested)
                if metadata:
                    return metadata
            except Exception:
                pass
        return {}

    @staticmethod
    def _normalize_license_payload_from_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(metadata, dict):
            return {}
        embedded = metadata.get("license")
        if isinstance(embedded, dict):
            payload = dict(embedded)
            return payload

        payload: dict[str, Any] = {}
        embedded_str = str(embedded or "").strip()
        spdx_id = str(
            metadata.get("spdx_id")
            or metadata.get("license_spdx_id")
            or embedded_str
            or ""
        ).strip()
        if spdx_id:
            payload["spdx_id"] = spdx_id

        source = str(metadata.get("license_source") or metadata.get("source") or "").strip()
        if source:
            payload["source"] = source

        for key in ("allows_commercial_use", "allows_derivatives", "requires_share_alike"):
            value = metadata.get(key)
            if isinstance(value, bool):
                payload[key] = value

        restrictions = metadata.get("license_restrictions")
        if not isinstance(restrictions, list):
            restrictions = metadata.get("restrictions")
        if isinstance(restrictions, list):
            payload["restrictions"] = [str(item).strip() for item in restrictions if str(item).strip()]
        return payload

    def _license_admission_for_target(
        self,
        *,
        provider_name: str,
        model_name: str,
        metadata: dict[str, Any],
        require_metadata: bool,
    ) -> dict[str, Any]:
        payload = self._normalize_license_payload_from_metadata(metadata)
        decision = evaluate_license_admission(
            payload or None,
            require_license_policy=True,
            require_license_metadata=bool(require_metadata),
        )
        summary = dict(decision.get("summary") or {})
        errors = [str(item) for item in decision.get("errors", []) if str(item).strip()]
        warnings = [str(item) for item in decision.get("warnings", []) if str(item).strip()]
        admitted = bool(decision.get("ok"))
        status = "allow" if admitted and not warnings else ("allow_with_warning" if admitted else "deny")
        return {
            "provider": provider_name,
            "model": model_name,
            "status": status,
            "admitted": admitted,
            "errors": errors,
            "warnings": warnings,
            "summary": summary,
        }

    @staticmethod
    def _fit_rank_for_package(fit: str) -> int:
        if fit == "fit":
            return 3
        if fit == "tight":
            return 2
        if fit == "unknown":
            return 1
        return 0

    @staticmethod
    def _normalize_onboarding_profile(profile: str | None, *, fallback: str = "balanced") -> str:
        normalized = str(profile or "").strip().lower()
        if normalized in {"fast", "balanced", "quality"}:
            return normalized
        fallback_normalized = str(fallback or "").strip().lower()
        if fallback_normalized in {"fast", "balanced", "quality"}:
            return fallback_normalized
        return "balanced"

    @staticmethod
    def _license_metadata_required_for_onboarding() -> bool:
        return _parse_bool_env(os.getenv("AMARYLLIS_LICENSE_ADMISSION_REQUIRE_METADATA", "false"))

    @staticmethod
    def _package_id_from_target(*, provider_name: str, model_name: str) -> str:
        return f"{provider_name}::{model_name}"

    @staticmethod
    def _parse_package_id(package_id: str) -> tuple[str, str]:
        normalized = str(package_id or "").strip()
        if not normalized:
            raise ValueError("package_id is required")
        parts = normalized.split("::", 1)
        if len(parts) != 2:
            raise ValueError("package_id must use '<provider>::<model>' format")
        provider_name, model_name = parts[0].strip(), parts[1].strip()
        if not provider_name or not model_name:
            raise ValueError("package_id must include both provider and model")
        return provider_name, model_name

    @staticmethod
    def _normalize_sha256_hex(value: Any) -> str | None:
        normalized = str(value or "").strip().lower()
        if not normalized:
            return None
        if re.fullmatch(r"[0-9a-f]{64}", normalized) is None:
            return None
        return normalized

    @staticmethod
    def _canonical_json(payload: dict[str, Any]) -> str:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _personalization_scope_key(*, user_id: str, base_package_id: str) -> str:
        return f"{str(user_id).strip()}||{str(base_package_id).strip()}"

    @staticmethod
    def _parse_personalization_scope_key(scope_key: str) -> tuple[str, str] | None:
        normalized = str(scope_key or "").strip()
        if not normalized:
            return None
        parts = normalized.split("||", 1)
        if len(parts) != 2:
            return None
        user_id = str(parts[0] or "").strip()
        base_package_id = str(parts[1] or "").strip()
        if not user_id or not base_package_id:
            return None
        return user_id, base_package_id

    @staticmethod
    def _estimated_download_size_bytes(candidate: ModelCandidate) -> int | None:
        metadata = candidate.metadata if isinstance(candidate.metadata, dict) else {}
        for key in ("size_bytes", "estimated_total_bytes"):
            value = metadata.get(key)
            if value is None:
                continue
            try:
                parsed = int(value)
            except Exception:
                continue
            if parsed > 0:
                return parsed
        if candidate.estimated_params_b is not None:
            return int(float(candidate.estimated_params_b) * 1_000_000_000 * 0.56)
        return None

    @staticmethod
    def _memory_requirements_for_candidate(candidate: ModelCandidate) -> dict[str, Any]:
        local_runtime_required = bool(candidate.local)
        estimated_params = candidate.estimated_params_b
        if not local_runtime_required:
            return {
                "local_runtime_required": False,
                "min_memory_gb": 2.0,
                "recommended_memory_gb": 4.0,
            }

        if estimated_params is None:
            return {
                "local_runtime_required": True,
                "min_memory_gb": 8.0,
                "recommended_memory_gb": 16.0,
            }

        min_memory_gb = max(4.0, round(float(estimated_params) * 0.7, 1))
        recommended_memory_gb = max(min_memory_gb + 2.0, round(float(estimated_params) * 1.05, 1))
        return {
            "local_runtime_required": True,
            "min_memory_gb": min_memory_gb,
            "recommended_memory_gb": recommended_memory_gb,
        }

    @staticmethod
    def _memory_fit_for_candidate(
        *,
        memory_gb: float | None,
        requirements: dict[str, Any],
    ) -> str:
        if not bool(requirements.get("local_runtime_required", False)):
            return "fit"
        if memory_gb is None:
            return "unknown"
        min_memory = float(requirements.get("min_memory_gb", 0.0))
        recommended_memory = float(requirements.get("recommended_memory_gb", min_memory))
        if memory_gb >= recommended_memory:
            return "fit"
        if memory_gb >= min_memory:
            return "tight"
        return "not_recommended"

    def _model_installed(self, *, provider_name: str, model_name: str) -> bool:
        provider = self.providers.get(provider_name)
        if provider is None:
            return False
        try:
            items = self._call_provider_resilient(
                provider_name=provider_name,
                operation="list_models",
                call=provider.list_models,
                max_attempts=1,
            )
        except Exception:
            return False
        if not isinstance(items, list):
            return False
        target = str(model_name).strip()
        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("id", "")).strip() == target:
                return True
        return False

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

    @staticmethod
    def _to_int_or_none(value: Any) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except Exception:
            return None

    def _active_target(self) -> tuple[str, str]:
        with self._active_target_lock:
            return self.active_provider, self.active_model

    def _set_active_target(self, *, provider_name: str, model_name: str) -> None:
        with self._active_target_lock:
            self.active_provider = provider_name
            self.active_model = model_name

    @staticmethod
    def _clone_suggested_map(items: dict[str, list[dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
        result: dict[str, list[dict[str, Any]]] = {}
        for provider_name, values in items.items():
            normalized_provider = str(provider_name)
            if not isinstance(values, list):
                result[normalized_provider] = []
                continue
            result[normalized_provider] = [dict(item) for item in values if isinstance(item, dict)]
        return result

    def _invalidate_suggested_cache(self) -> None:
        with self._suggested_cache_lock:
            self._suggested_cache_until = 0.0

    def _invalidate_provider_models_cache(self, provider_name: str | None = None) -> None:
        with self._provider_models_cache_lock:
            if provider_name is None:
                self._provider_models_cache = {}
                return
            self._provider_models_cache.pop(provider_name, None)

    def _find_running_download_unlocked(
        self,
        *,
        provider_name: str,
        model_id: str,
    ) -> _ModelDownloadJob | None:
        for job in self._download_jobs.values():
            if job.provider != provider_name or job.model != model_id:
                continue
            if job.status in {"queued", "running"}:
                return job
        return None

    def _run_model_download_job(self, job_id: str, provider_name: str, model_id: str) -> None:
        selected = self.providers.get(provider_name)
        if selected is None:
            self._set_download_job_failed(job_id=job_id, message=f"Unknown provider: {provider_name}")
            return

        self._update_download_job(
            job_id=job_id,
            status="running",
            progress=0.0,
            message="Starting download",
        )

        def progress_callback(payload: dict[str, Any]) -> None:
            completed = self._to_int_or_none(payload.get("completed_bytes"))
            total = self._to_int_or_none(payload.get("total_bytes"))
            progress = self._to_float_or_none(payload.get("progress"))
            if progress is None and completed is not None and total and total > 0:
                progress = float(completed) / float(total)
            normalized_progress = max(0.0, min(1.0, progress if progress is not None else 0.0))
            status = str(payload.get("status", "running")).strip().lower() or "running"
            message = str(payload.get("message", "")).strip() or None
            self._update_download_job(
                job_id=job_id,
                status="running" if status not in {"failed", "succeeded"} else status,
                progress=normalized_progress,
                completed_bytes=completed,
                total_bytes=total,
                message=message,
            )

        try:
            result = self._download_via_provider(
                provider_name=provider_name,
                provider=selected,
                model_id=model_id,
                progress_callback=progress_callback,
            )
            result = self._enforce_model_artifact_admission(
                provider_name=provider_name,
                model_id=model_id,
                result=result,
            )
            self._invalidate_provider_models_cache(provider_name)
            self._invalidate_suggested_cache()
            completed = self._to_int_or_none(result.get("size_bytes"))
            self._update_download_job(
                job_id=job_id,
                status="succeeded",
                progress=1.0,
                completed_bytes=completed,
                total_bytes=completed,
                message="Download completed",
                result=dict(result),
                finished=True,
            )
            self.logger.info(
                "model_download_job_succeeded job_id=%s provider=%s model=%s",
                job_id,
                provider_name,
                model_id,
            )
        except Exception as exc:
            self._set_download_job_failed(job_id=job_id, message=str(exc))
            self.logger.error(
                "model_download_job_failed job_id=%s provider=%s model=%s error=%s",
                job_id,
                provider_name,
                model_id,
                exc,
            )

    def _download_via_provider(
        self,
        *,
        provider_name: str,
        provider: ModelProvider,
        model_id: str,
        progress_callback: Any | None,
    ) -> dict[str, Any]:
        method = provider.download_model
        kwargs: dict[str, Any] = {}
        if callable(progress_callback):
            try:
                signature = inspect.signature(method)
                if "progress_callback" in signature.parameters:
                    kwargs["progress_callback"] = progress_callback
            except Exception:
                kwargs = {}
        result = method(model_id, **kwargs) if kwargs else method(model_id)
        if not isinstance(result, dict):
            raise ValueError(f"Invalid download result from provider '{provider_name}'")
        return result

    def admit_model_artifact(
        self,
        *,
        manifest: dict[str, Any],
        strict: bool = True,
        artifact_root: str | None = None,
    ) -> dict[str, Any]:
        signing_key = str(os.getenv("AMARYLLIS_MODEL_PACKAGE_SIGNING_KEY", "")).strip() or None
        require_signing_key = _parse_bool_env(
            os.getenv("AMARYLLIS_MODEL_PACKAGE_REQUIRE_SIGNING_KEY", "false")
        )
        decision = validate_model_package_manifest(
            manifest,
            signing_key=signing_key,
            require_signing_key=bool(require_signing_key and strict),
            require_managed_trust=bool(strict),
            artifact_root=artifact_root,
        )
        return {
            **decision,
            "admitted": bool(decision.get("ok")),
            "mode": "strict" if strict else "advisory",
        }

    def _enforce_model_artifact_admission(
        self,
        *,
        provider_name: str,
        model_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        manifest = result.get("artifact_manifest")
        if not isinstance(manifest, dict):
            return result

        artifact_root = str(result.get("path") or "").strip() or None
        decision = self.admit_model_artifact(
            manifest=manifest,
            strict=True,
            artifact_root=artifact_root,
        )
        output = dict(result)
        output["artifact_admission"] = decision
        if not bool(decision.get("admitted")):
            reasons = ", ".join([str(item) for item in decision.get("errors", [])[:4]])
            raise ValueError(
                "Model artifact admission failed "
                f"provider={provider_name} model={model_id} errors={reasons}"
            )
        return output

    def _set_download_job_failed(self, *, job_id: str, message: str) -> None:
        self._update_download_job(
            job_id=job_id,
            status="failed",
            progress=0.0,
            message=message,
            error=message,
            finished=True,
        )

    def _update_download_job(
        self,
        *,
        job_id: str,
        status: str | None = None,
        progress: float | None = None,
        completed_bytes: int | None = None,
        total_bytes: int | None = None,
        message: str | None = None,
        error: str | None = None,
        result: dict[str, Any] | None = None,
        finished: bool = False,
    ) -> None:
        with self._download_lock:
            job = self._download_jobs.get(job_id)
            if job is None:
                return
            if status is not None:
                job.status = status
            if progress is not None:
                job.progress = max(0.0, min(1.0, float(progress)))
            if completed_bytes is not None:
                job.completed_bytes = max(0, int(completed_bytes))
            if total_bytes is not None:
                job.total_bytes = max(0, int(total_bytes))
            if message is not None:
                job.message = message
            if error is not None:
                job.error = error
            if result is not None:
                job.result = result
            now = self._utc_now_iso()
            job.updated_at = now
            if finished:
                job.finished_at = now

    def _resolve_target(self, model: str | None, provider: str | None) -> tuple[str, str]:
        active_provider, active_model = self._active_target()
        inferred_provider = self._infer_provider_for_model(model)
        provider_name = provider or inferred_provider or active_provider or self.config.default_provider
        if model:
            model_name = model
        elif provider and provider != active_provider:
            model_name = self._default_model_for_provider(provider_name)
        else:
            model_name = active_model or self.config.default_model
        if provider_name not in self.providers:
            raise ValueError(f"Unknown provider: {provider_name}")
        return provider_name, model_name

    def _infer_provider_for_model(self, model: str | None) -> str | None:
        normalized = str(model or "").strip()
        if not normalized:
            return None
        lowered = normalized.lower()
        if lowered.startswith("mlx-community/"):
            return "mlx"
        if lowered.startswith("claude-"):
            return "anthropic"
        if "/" in normalized:
            # OpenRouter-style model ids usually look like "vendor/model".
            if lowered.startswith("openai/") or lowered.startswith("anthropic/"):
                return "openrouter"
        if lowered.startswith(("gpt-", "o1", "o3", "o4")):
            return "openai"
        return None

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

        active_provider, active_model = self._active_target()
        targets: list[tuple[str, str]] = []

        if provider_name == "mlx":
            if "ollama" in self.providers:
                ollama_model = (
                    self.database.get_setting("ollama_fallback_model", "llama3.2")
                    or "llama3.2"
                )
                targets.append(("ollama", ollama_model))
            return self._unique_targets(targets)

        if provider_name in {"openai", "openrouter", "anthropic"}:
            if active_provider in {"mlx", "ollama"} and active_provider in self.providers:
                local_active_model = active_model or self._default_model_for_provider(active_provider)
                targets.append((active_provider, local_active_model))

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
        user_id: str | None = None,
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
            before_call=lambda: self._before_provider_call(
                provider_name=provider_name,
                model_name=model_name,
                user_id=user_id,
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
        user_id: str | None = None,
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
            before_call=lambda: self._before_provider_call(
                provider_name=provider_name,
                model_name=model_name,
                user_id=user_id,
                messages=messages,
                max_tokens=max_tokens,
            ),
        )

    def _before_provider_call(
        self,
        *,
        provider_name: str,
        model_name: str,
        user_id: str | None,
        messages: list[dict[str, Any]],
        max_tokens: int,
    ) -> None:
        self._enforce_provider_entitlement(
            provider_name=provider_name,
            model_name=model_name,
            user_id=user_id,
        )
        self._enforce_cloud_guardrails(
            provider_name=provider_name,
            messages=messages,
            max_tokens=max_tokens,
        )

    def _enforce_provider_entitlement(
        self,
        *,
        provider_name: str,
        model_name: str,
        user_id: str | None,
    ) -> None:
        if not self._is_cloud_provider(provider_name):
            return
        if self.entitlement_resolver is None:
            return

        normalized_user = str(user_id or "").strip()
        if not normalized_user:
            raise RuntimeError(
                f"Provider entitlement check requires user_id for cloud provider '{provider_name}'."
            )

        resolver = getattr(self.entitlement_resolver, "resolve_provider", None)
        if not callable(resolver):
            raise RuntimeError("Provider entitlement resolver is not available.")

        payload = resolver(user_id=normalized_user, provider=provider_name)
        if not isinstance(payload, dict):
            raise RuntimeError("Provider entitlement resolver returned invalid payload.")

        route_policy = payload.get("route_policy")
        if not isinstance(route_policy, dict):
            route_policy = {}
        error_contract = payload.get("error_contract")
        if not isinstance(error_contract, dict):
            error_contract = {}

        if not bool(payload.get("available", False)):
            error_code = str(error_contract.get("error_code") or "provider_access_not_configured").strip()
            selected_route = str(route_policy.get("selected_route") or "none").strip().lower() or "none"
            available_routes = list(route_policy.get("available_routes") or [])
            contract_message = str(error_contract.get("message") or "").strip()
            reason_message = contract_message or "Create provider session or configure server provider key."
            raise RuntimeError(
                (
                    f"Provider entitlement denied for '{provider_name}' and user '{normalized_user}'. "
                    f"error_code={error_code} selected_route={selected_route} "
                    f"available_routes={available_routes}. {reason_message}"
                )
            )

        feature_flags = payload.get("feature_flags")
        if isinstance(feature_flags, dict) and feature_flags.get("chat") is False:
            selected_route = str(route_policy.get("selected_route") or "none").strip().lower() or "none"
            raise RuntimeError(
                (
                    f"Provider entitlement denied for '{provider_name}' and user '{normalized_user}': "
                    f"chat feature is disabled. error_code=provider_chat_disabled "
                    f"selected_route={selected_route}"
                )
            )

    def _call_provider_resilient(
        self,
        *,
        provider_name: str,
        operation: str,
        call: Any,
        before_call: Any | None = None,
        max_attempts: int | None = None,
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

        attempts = max_attempts if max_attempts is not None else int(self.config.provider_retry_attempts)
        attempts = max(1, int(attempts))
        last_info: ProviderErrorInfo | None = None
        for attempt in range(1, attempts + 1):
            call_started_at = time.monotonic()
            try:
                if callable(before_call):
                    before_call()
                result = call()
                self._record_provider_success(provider_name, call_started_at=call_started_at)
                return result
            except Exception as exc:  # noqa: BLE001
                info = classify_provider_error(
                    provider=provider_name,
                    operation=operation,
                    error=exc,
                )
                last_info = info
                self._record_provider_failure(provider_name, call_started_at=call_started_at)
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

    def _provider_state_unlocked(self, provider_name: str) -> _ProviderRuntimeState:
        state = self._provider_states.get(provider_name)
        if state is None:
            state = _ProviderRuntimeState()
            self._provider_states[provider_name] = state
        return state

    def _record_provider_success(self, provider_name: str, *, call_started_at: float) -> None:
        ignored_stale_success = False
        with self._provider_state_lock:
            state = self._provider_state_unlocked(provider_name)
            if call_started_at < state.latest_failure_started_at:
                ignored_stale_success = True
            else:
                state.failure_count = 0
                state.circuit_until_monotonic = None
        if ignored_stale_success:
            self.logger.debug(
                "provider_success_ignored_stale provider=%s started_at=%.6f",
                provider_name,
                call_started_at,
            )

    def _record_provider_failure(self, provider_name: str, *, call_started_at: float) -> None:
        threshold = max(1, int(self.config.provider_circuit_failure_threshold))
        cooldown = max(1.0, float(self.config.provider_circuit_cooldown_sec))
        opened = False
        failures = 0
        with self._provider_state_lock:
            state = self._provider_state_unlocked(provider_name)
            state.latest_failure_started_at = max(float(state.latest_failure_started_at), float(call_started_at))
            failures = int(state.failure_count) + 1
            state.failure_count = failures
            if failures >= threshold:
                state.circuit_until_monotonic = time.monotonic() + cooldown
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
            state = self._provider_state_unlocked(provider_name)
            until = state.circuit_until_monotonic
            if until is None:
                return False
            if time.monotonic() >= until:
                state.circuit_until_monotonic = None
                state.failure_count = 0
                return False
            return True

    def _provider_cooldown_remaining(self, provider_name: str) -> float:
        with self._provider_state_lock:
            state = self._provider_state_unlocked(provider_name)
            until = state.circuit_until_monotonic
            if until is None:
                return 0.0
            remaining = max(0.0, until - time.monotonic())
            if remaining <= 0.0:
                state.circuit_until_monotonic = None
                state.failure_count = 0
                return 0.0
            return remaining

    def _provider_failure_count(self, provider_name: str) -> int:
        with self._provider_state_lock:
            state = self._provider_state_unlocked(provider_name)
            return int(state.failure_count)

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
