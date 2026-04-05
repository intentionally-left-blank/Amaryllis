from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
from typing import Any, Iterator
from uuid import uuid4

from kernel.contracts import CognitionBackendContract
from models.model_manager import ModelManager


class ModelManagerCognitionBackend:
    """Contract adapter that delegates cognition operations to ModelManager."""

    def __init__(self, manager: ModelManager) -> None:
        self._manager = manager

    @property
    def manager(self) -> ModelManager:
        return self._manager

    @property
    def active_provider(self) -> str:
        return str(self._manager.active_provider)

    @active_provider.setter
    def active_provider(self, value: str) -> None:
        self._manager.active_provider = str(value)

    @property
    def active_model(self) -> str:
        return str(self._manager.active_model)

    @active_model.setter
    def active_model(self, value: str) -> None:
        self._manager.active_model = str(value)

    @property
    def providers(self) -> dict[str, Any]:
        return self._manager.providers

    @providers.setter
    def providers(self, value: dict[str, Any]) -> None:
        self._manager.providers = value

    def __getattr__(self, item: str) -> Any:
        return getattr(self._manager, item)

    def list_models(
        self,
        *,
        include_suggested: bool = True,
        include_remote_providers: bool = True,
        max_items_per_provider: int | None = None,
    ) -> dict[str, Any]:
        return self._manager.list_models(
            include_suggested=include_suggested,
            include_remote_providers=include_remote_providers,
            max_items_per_provider=max_items_per_provider,
        )

    def provider_capabilities(self) -> dict[str, Any]:
        return self._manager.provider_capabilities()

    def provider_health(self) -> dict[str, Any]:
        return self._manager.provider_health()

    def model_capability_matrix(
        self,
        *,
        include_suggested: bool = True,
        limit_per_provider: int = 120,
    ) -> dict[str, Any]:
        return self._manager.model_capability_matrix(
            include_suggested=include_suggested,
            limit_per_provider=limit_per_provider,
        )

    def recommend_onboarding_profile(self) -> dict[str, Any]:
        return self._manager.recommend_onboarding_profile()

    def onboarding_activation_plan(
        self,
        *,
        profile: str | None = None,
        include_remote_providers: bool = True,
        limit: int = 120,
        require_metadata: bool | None = None,
    ) -> dict[str, Any]:
        return self._manager.onboarding_activation_plan(
            profile=profile,
            include_remote_providers=include_remote_providers,
            limit=limit,
            require_metadata=require_metadata,
        )

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
        return self._manager.onboarding_activate(
            profile=profile,
            include_remote_providers=include_remote_providers,
            limit=limit,
            require_metadata=require_metadata,
            activate=activate,
            run_smoke_test=run_smoke_test,
            smoke_prompt=smoke_prompt,
        )

    def model_package_catalog(
        self,
        *,
        profile: str | None = None,
        include_remote_providers: bool = True,
        limit: int = 120,
    ) -> dict[str, Any]:
        return self._manager.model_package_catalog(
            profile=profile,
            include_remote_providers=include_remote_providers,
            limit=limit,
        )

    def model_package_license_admission(
        self,
        *,
        package_id: str,
        require_metadata: bool | None = None,
    ) -> dict[str, Any]:
        return self._manager.model_package_license_admission(
            package_id=package_id,
            require_metadata=require_metadata,
        )

    def install_model_package(
        self,
        *,
        package_id: str,
        activate: bool = True,
    ) -> dict[str, Any]:
        return self._manager.install_model_package(package_id=package_id, activate=activate)

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
        return self._manager.choose_route(
            mode=mode,
            provider=provider,
            model=model,
            require_stream=require_stream,
            require_tools=require_tools,
            prefer_local=prefer_local,
            min_params_b=min_params_b,
            max_params_b=max_params_b,
            include_suggested=include_suggested,
            limit_per_provider=limit_per_provider,
        )

    def debug_failover_state(
        self,
        *,
        session_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        return self._manager.debug_failover_state(session_id=session_id, limit=limit)

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
        return self._manager.chat(
            messages=messages,
            model=model,
            provider=provider,
            temperature=temperature,
            max_tokens=max_tokens,
            routing=routing,
            fallback_targets=fallback_targets,
            session_id=session_id,
            user_id=user_id,
        )

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
        return self._manager.stream_chat(
            messages=messages,
            model=model,
            provider=provider,
            temperature=temperature,
            max_tokens=max_tokens,
            routing=routing,
            fallback_targets=fallback_targets,
            session_id=session_id,
            user_id=user_id,
        )

    def download_model(self, model_id: str, provider: str | None = None) -> dict[str, Any]:
        return self._manager.download_model(model_id=model_id, provider=provider)

    def start_model_download(self, model_id: str, provider: str | None = None) -> dict[str, Any]:
        return self._manager.start_model_download(model_id=model_id, provider=provider)

    def get_model_download_job(self, job_id: str) -> dict[str, Any]:
        return self._manager.get_model_download_job(job_id=job_id)

    def list_model_download_jobs(self, limit: int = 100) -> dict[str, Any]:
        return self._manager.list_model_download_jobs(limit=limit)

    def load_model(self, model_id: str, provider: str | None = None) -> dict[str, Any]:
        return self._manager.load_model(model_id=model_id, provider=provider)


class DeterministicCognitionBackend:
    """
    Deterministic backend used for contract and integration testing.

    It intentionally avoids provider/network calls and produces stable outputs.
    """

    def __init__(self) -> None:
        self.active_provider = "deterministic"
        self.active_model = "deterministic-v1"
        self.providers: dict[str, Any] = {}
        self._jobs: dict[str, dict[str, Any]] = {}
        self._job_order: deque[str] = deque(maxlen=500)

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _normalize_mode(mode: str | None) -> str:
        normalized = str(mode or "").strip().lower()
        if normalized in {"balanced", "local_first", "quality_first", "coding", "reasoning"}:
            return normalized
        return "balanced"

    def provider_capabilities(self) -> dict[str, Any]:
        return {
            "deterministic": {
                "local": True,
                "supports_download": True,
                "supports_load": True,
                "supports_stream": True,
                "supports_tools": True,
                "requires_api_key": False,
            }
        }

    def provider_health(self) -> dict[str, Any]:
        return {
            "deterministic": {
                "status": "ok",
                "latency_ms": 0.0,
                "active": True,
                "detail": "deterministic backend",
                "failure_count": 0,
                "circuit_open": False,
                "guardrails": {"enabled": False},
            }
        }

    def _model_items(self, *, model_name: str) -> list[dict[str, Any]]:
        return [
            {
                "id": model_name,
                "provider": "deterministic",
                "active": True,
                "metadata": {"source": "deterministic"},
            }
        ]

    def list_models(
        self,
        *,
        include_suggested: bool = True,
        include_remote_providers: bool = True,
        max_items_per_provider: int | None = None,
    ) -> dict[str, Any]:
        _ = (include_remote_providers, max_items_per_provider)
        model_name = str(self.active_model or "deterministic-v1")
        suggested: dict[str, list[dict[str, Any]]] = {}
        if include_suggested:
            suggested = {
                "deterministic": [
                    {
                        "id": model_name,
                        "label": model_name,
                    }
                ]
            }

        return {
            "active": {
                "provider": "deterministic",
                "model": model_name,
            },
            "providers": {
                "deterministic": {
                    "available": True,
                    "error": None,
                    "items": self._model_items(model_name=model_name),
                    "failure_count": 0,
                    "circuit_open": False,
                    "guardrails": {"enabled": False},
                }
            },
            "capabilities": self.provider_capabilities(),
            "suggested": suggested,
            "routing_modes": [
                "balanced",
                "local_first",
                "quality_first",
                "coding",
                "reasoning",
            ],
        }

    def model_capability_matrix(
        self,
        *,
        include_suggested: bool = True,
        limit_per_provider: int = 120,
    ) -> dict[str, Any]:
        _ = (include_suggested, limit_per_provider)
        model_name = str(self.active_model or "deterministic-v1")
        item = {
            "provider": "deterministic",
            "model": model_name,
            "local": True,
            "installed": True,
            "active": True,
            "supports_stream": True,
            "supports_tools": True,
            "requires_api_key": False,
            "estimated_params_b": 0.1,
            "quality_tier": "standard",
            "speed_tier": "fast",
            "tags": ["deterministic"],
            "source": "listed",
            "metadata": {"source": "deterministic"},
        }
        return {
            "generated_at": self._utc_now_iso(),
            "active": {
                "provider": "deterministic",
                "model": model_name,
            },
            "providers": self.provider_capabilities(),
            "count": 1,
            "items": [item],
            "by_provider": {"deterministic": [item]},
        }

    def recommend_onboarding_profile(self) -> dict[str, Any]:
        model_name = str(self.active_model or "deterministic-v1")
        profiles: dict[str, Any] = {}
        for profile_id, route_mode in (
            ("fast", "local_first"),
            ("balanced", "balanced"),
            ("quality", "quality_first"),
        ):
            profiles[profile_id] = {
                "id": profile_id,
                "route_mode": route_mode,
                "intent": "deterministic onboarding profile",
                "constraints": {
                    "mode": route_mode,
                    "require_stream": True,
                    "require_tools": False,
                    "prefer_local": True,
                    "min_params_b": None,
                    "max_params_b": None,
                },
                "selected": {
                    "provider": "deterministic",
                    "model": model_name,
                    "reason": "deterministic_route",
                },
                "fallbacks": [],
                "considered_count": 1,
            }
        return {
            "generated_at": self._utc_now_iso(),
            "active": {
                "provider": "deterministic",
                "model": model_name,
            },
            "hardware": {
                "platform": "deterministic",
                "machine": "synthetic",
                "cpu_count_logical": 8,
                "memory_bytes": 16 * 1024 * 1024 * 1024,
                "memory_gb": 16.0,
                "provider_count": 1,
                "local_provider_available": True,
                "cloud_provider_available": False,
            },
            "recommended_profile": "balanced",
            "reason_codes": ["deterministic_backend_default"],
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
        selected_profile = str(profile or onboarding.get("recommended_profile") or "balanced").strip().lower()
        if selected_profile not in {"fast", "balanced", "quality"}:
            selected_profile = "balanced"
        catalog = self.model_package_catalog(
            profile=selected_profile,
            include_remote_providers=include_remote_providers,
            limit=limit,
        )
        package = {}
        package_id = ""
        packages = catalog.get("packages")
        if isinstance(packages, list) and packages:
            first = packages[0]
            if isinstance(first, dict):
                package = first
                package_id = str(first.get("package_id", "")).strip()
        license_admission = (
            self.model_package_license_admission(
                package_id=package_id,
                require_metadata=require_metadata,
            )
            if package_id
            else {
                "package_id": "",
                "status": "deny",
                "admitted": False,
                "errors": ["no_package_candidates_for_profile"],
                "warnings": [],
                "summary": {"license_policy_id": "deterministic.default"},
                "require_metadata": bool(require_metadata) if require_metadata is not None else False,
            }
        )
        top_package_ids: list[str] = []
        profiles_payload = catalog.get("profiles")
        if isinstance(profiles_payload, dict):
            selected_profile_payload = profiles_payload.get(selected_profile)
            if isinstance(selected_profile_payload, dict):
                top_items = selected_profile_payload.get("top_package_ids")
                if isinstance(top_items, list):
                    top_package_ids = [str(item).strip() for item in top_items if str(item).strip()]
        blockers = [str(item) for item in license_admission.get("errors", []) if str(item).strip()]
        return {
            "plan_version": "onboarding_activation_plan_v1",
            "generated_at": self._utc_now_iso(),
            "active": onboarding.get("active", {}),
            "hardware": onboarding.get("hardware", {}),
            "recommended_profile": str(onboarding.get("recommended_profile", "balanced")),
            "selected_profile": selected_profile,
            "reason_codes": [str(item) for item in onboarding.get("reason_codes", []) if str(item).strip()],
            "profiles": onboarding.get("profiles", {}),
            "catalog": {
                "count": int(catalog.get("count", 0)),
                "top_package_ids": top_package_ids,
            },
            "selected_package_id": package_id,
            "selected_package": package,
            "license_admission": license_admission,
            "require_metadata": bool(require_metadata) if require_metadata is not None else False,
            "ready_to_install": bool(license_admission.get("admitted")),
            "blockers": blockers,
            "next_action": "install_package" if bool(license_admission.get("admitted")) else "resolve_blockers",
            "install": dict(package.get("install") or {}) if isinstance(package, dict) else {},
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
        package_id = str(plan.get("selected_package_id", "")).strip()
        blockers = [str(item) for item in plan.get("blockers", []) if str(item).strip()]
        if not package_id or not bool(plan.get("ready_to_install")):
            if not blockers:
                blockers = ["activation_plan_not_ready"]
            return {
                "activation_version": "onboarding_activate_v1",
                "generated_at": self._utc_now_iso(),
                "status": "blocked",
                "ready": False,
                "selected_profile": str(plan.get("selected_profile", "")),
                "selected_package_id": package_id,
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

        install_result = self.install_model_package(package_id=package_id, activate=activate)
        smoke_result: dict[str, Any] = {
            "requested": bool(run_smoke_test),
            "status": "skipped",
        }
        status = "activated"
        if run_smoke_test:
            if activate:
                prompt = str(smoke_prompt or "").strip() or "Reply with a short readiness confirmation."
                smoke_response = self.chat(messages=[{"role": "user", "content": prompt}])
                content = str(smoke_response.get("content", "")).strip()
                smoke_result = {
                    "requested": True,
                    "status": "passed",
                    "prompt": prompt,
                    "response_preview": content[:240],
                    "provider": str(smoke_response.get("provider", "")).strip(),
                    "model": str(smoke_response.get("model", "")).strip(),
                }
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
            "selected_package_id": package_id,
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
        _ = (include_remote_providers, limit)
        model_name = str(self.active_model or "deterministic-v1")
        selected_profile = str(profile or "balanced").strip().lower()
        if selected_profile not in {"fast", "balanced", "quality"}:
            selected_profile = "balanced"
        package_id = f"deterministic::{model_name}"
        package = {
            "package_id": package_id,
            "provider": "deterministic",
            "model": model_name,
            "label": model_name,
            "source": "deterministic",
            "local": True,
            "installed": True,
            "active": True,
            "quality_tier": "standard",
            "speed_tier": "fast",
            "tags": ["deterministic"],
            "estimated_params_b": 0.1,
            "estimated_download_bytes": 1024,
            "requirements": {
                "local_runtime_required": True,
                "min_memory_gb": 2.0,
                "recommended_memory_gb": 4.0,
            },
            "compatibility": {
                "fit": "fit",
                "hardware_memory_gb": 16.0,
            },
            "recommended_profiles": ["balanced", "fast"],
            "profile_scores": {
                "fast": 1.0,
                "balanced": 1.1,
                "quality": 0.9,
            },
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
                        "require_metadata": False,
                    },
                },
                "download_step": {
                    "endpoint": "/models/download/start",
                    "payload": {
                        "model_id": model_name,
                        "provider": "deterministic",
                    },
                },
                "activate_step": {
                    "endpoint": "/models/load",
                    "payload": {
                        "model_id": model_name,
                        "provider": "deterministic",
                    },
                },
            },
        }
        return {
            "catalog_version": "model_package_catalog_v1",
            "generated_at": self._utc_now_iso(),
            "active": {
                "provider": "deterministic",
                "model": model_name,
            },
            "hardware": {
                "platform": "deterministic",
                "machine": "synthetic",
                "cpu_count_logical": 8,
                "memory_bytes": 16 * 1024 * 1024 * 1024,
                "memory_gb": 16.0,
                "provider_count": 1,
                "local_provider_available": True,
                "cloud_provider_available": False,
            },
            "recommended_profile": "balanced",
            "selected_profile": selected_profile,
            "profiles": {
                "fast": {"route_mode": "local_first", "top_package_ids": [package_id]},
                "balanced": {"route_mode": "balanced", "top_package_ids": [package_id]},
                "quality": {"route_mode": "quality_first", "top_package_ids": [package_id]},
            },
            "count": 1,
            "packages": [package],
        }

    def install_model_package(
        self,
        *,
        package_id: str,
        activate: bool = True,
    ) -> dict[str, Any]:
        normalized = str(package_id or "").strip()
        if not normalized:
            raise ValueError("package_id is required")
        if "::" in normalized:
            provider_name, model_name = normalized.split("::", 1)
        else:
            provider_name, model_name = "deterministic", normalized
        provider_name = str(provider_name).strip() or "deterministic"
        model_name = str(model_name).strip()
        if not model_name:
            raise ValueError("package_id must include model")
        load_payload: dict[str, Any] | None = None
        steps = [
            {
                "step": "download",
                "status": "skipped",
                "reason": "already_installed",
            }
        ]
        if activate:
            self.active_provider = provider_name
            self.active_model = model_name
            load_payload = {
                "status": "loaded",
                "provider": provider_name,
                "model": model_name,
                "active": {
                    "provider": provider_name,
                    "model": model_name,
                },
            }
            steps.append({"step": "activate", "status": "completed"})
        else:
            steps.append({"step": "activate", "status": "skipped", "reason": "activate_disabled"})
        return {
            "package_id": f"{provider_name}::{model_name}",
            "provider": provider_name,
            "model": model_name,
            "download": None,
            "load": load_payload,
            "steps": steps,
            "active": {
                "provider": self.active_provider,
                "model": self.active_model,
            },
        }

    def model_package_license_admission(
        self,
        *,
        package_id: str,
        require_metadata: bool | None = None,
    ) -> dict[str, Any]:
        normalized = str(package_id or "").strip()
        if not normalized:
            raise ValueError("package_id is required")
        if "::" in normalized:
            provider_name, model_name = normalized.split("::", 1)
        else:
            provider_name, model_name = "deterministic", normalized
        provider_name = str(provider_name).strip()
        model_name = str(model_name).strip()
        if not provider_name or not model_name:
            raise ValueError("package_id must include both provider and model")

        admitted = provider_name == "deterministic"
        errors = [] if admitted else [f"unknown_provider:{provider_name}"]
        return {
            "package_id": f"{provider_name}::{model_name}",
            "provider": provider_name,
            "model": model_name,
            "status": "allow" if admitted else "deny",
            "admitted": admitted,
            "errors": errors,
            "warnings": [],
            "summary": {
                "license_policy_id": "deterministic.default",
                "require_metadata": bool(require_metadata) if require_metadata is not None else False,
            },
            "require_metadata": bool(require_metadata) if require_metadata is not None else False,
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
        _ = (
            require_stream,
            require_tools,
            prefer_local,
            min_params_b,
            max_params_b,
            include_suggested,
            limit_per_provider,
        )
        selected_provider = str(provider or self.active_provider or "deterministic")
        selected_model = str(model or self.active_model or "deterministic-v1")
        return {
            "mode": self._normalize_mode(mode),
            "constraints": {
                "mode": self._normalize_mode(mode),
                "require_stream": bool(require_stream),
                "require_tools": bool(require_tools),
                "prefer_local": prefer_local,
                "min_params_b": min_params_b,
                "max_params_b": max_params_b,
            },
            "selected": {
                "provider": selected_provider,
                "model": selected_model,
                "reason": "deterministic_route",
            },
            "fallbacks": [],
            "considered_count": 1,
            "top_candidates": [
                {
                    "provider": selected_provider,
                    "model": selected_model,
                    "score": 1.0,
                }
            ],
        }

    def debug_failover_state(
        self,
        *,
        session_id: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        _ = limit
        return {
            "session_id": session_id,
            "selected_pin": None,
            "pins_count": 0,
            "pins": [],
            "recent_failovers": [],
            "recent_failovers_count": 0,
            "provider_runtime": {
                "deterministic": {
                    "failure_count": 0,
                    "circuit_open": False,
                    "circuit_remaining_sec": 0.0,
                    "latest_failure_started_at": 0.0,
                }
            },
        }

    @staticmethod
    def _last_user_content(messages: list[dict[str, Any]]) -> str:
        for row in reversed(messages):
            if str(row.get("role", "")).strip().lower() != "user":
                continue
            content = row.get("content")
            if isinstance(content, str) and content.strip():
                return content.strip()
        return "ok"

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
        _ = (temperature, max_tokens, fallback_targets, session_id, user_id)
        provider_name = str(provider or self.active_provider or "deterministic")
        model_name = str(model or self.active_model or "deterministic-v1")
        prompt = self._last_user_content(messages)
        mode = self._normalize_mode((routing or {}).get("mode"))
        return {
            "content": f"[deterministic:{mode}] {prompt}",
            "provider": provider_name,
            "model": model_name,
            "routing": {
                "mode": mode,
                "selected": {
                    "provider": provider_name,
                    "model": model_name,
                    "reason": "deterministic",
                },
                "final": {
                    "provider": provider_name,
                    "model": model_name,
                    "fallback_used": False,
                },
            },
        }

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
        _ = (temperature, max_tokens, fallback_targets, session_id, user_id)
        provider_name = str(provider or self.active_provider or "deterministic")
        model_name = str(model or self.active_model or "deterministic-v1")
        prompt = self._last_user_content(messages)
        mode = self._normalize_mode((routing or {}).get("mode"))
        chunks = [f"[deterministic:{mode}] ", prompt]
        route = {
            "mode": mode,
            "selected": {
                "provider": provider_name,
                "model": model_name,
                "reason": "deterministic",
            },
            "final": {
                "provider": provider_name,
                "model": model_name,
                "fallback_used": False,
            },
        }
        return iter(chunks), provider_name, model_name, route

    def download_model(self, model_id: str, provider: str | None = None) -> dict[str, Any]:
        provider_name = str(provider or self.active_provider or "deterministic")
        return {
            "status": "downloaded",
            "provider": provider_name,
            "model": str(model_id),
            "size_bytes": 0,
        }

    def start_model_download(self, model_id: str, provider: str | None = None) -> dict[str, Any]:
        provider_name = str(provider or self.active_provider or "deterministic")
        now = self._utc_now_iso()
        job = {
            "id": str(uuid4()),
            "provider": provider_name,
            "model": str(model_id),
            "status": "succeeded",
            "progress": 1.0,
            "completed_bytes": 0,
            "total_bytes": 0,
            "message": "Download completed",
            "error": None,
            "result": {
                "status": "downloaded",
                "provider": provider_name,
                "model": str(model_id),
            },
            "created_at": now,
            "updated_at": now,
            "finished_at": now,
        }
        self._jobs[str(job["id"])] = dict(job)
        self._job_order.append(str(job["id"]))
        return {
            "job": dict(job),
            "already_running": False,
        }

    def get_model_download_job(self, job_id: str) -> dict[str, Any]:
        key = str(job_id or "").strip()
        if not key:
            raise ValueError("job_id is required")
        job = self._jobs.get(key)
        if job is None:
            raise ValueError(f"Unknown download job: {key}")
        return dict(job)

    def list_model_download_jobs(self, limit: int = 100) -> dict[str, Any]:
        normalized_limit = max(1, int(limit))
        ordered = list(self._job_order)[-normalized_limit:]
        items = [dict(self._jobs[item_id]) for item_id in reversed(ordered) if item_id in self._jobs]
        return {
            "items": items,
            "count": len(items),
        }

    def load_model(self, model_id: str, provider: str | None = None) -> dict[str, Any]:
        provider_name = str(provider or self.active_provider or "deterministic")
        model_name = str(model_id or "").strip()
        if not model_name:
            raise ValueError("model_id is required")
        self.active_provider = provider_name
        self.active_model = model_name
        return {
            "status": "loaded",
            "provider": provider_name,
            "model": model_name,
            "active": {
                "provider": provider_name,
                "model": model_name,
            },
        }


def ensure_cognition_backend_contract(backend: Any) -> CognitionBackendContract:
    if not isinstance(backend, CognitionBackendContract):
        raise TypeError("Backend does not satisfy CognitionBackendContract")
    return backend
