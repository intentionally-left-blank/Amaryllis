from __future__ import annotations

import unittest
from typing import Any

from kernel.contracts import CognitionBackendContract
from models.cognition_backends import DeterministicCognitionBackend, ModelManagerCognitionBackend


class _FakeModelManager:
    def __init__(self) -> None:
        self.active_provider = "fake"
        self.active_model = "fake-model"
        self.providers: dict[str, Any] = {"fake": object()}
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.jobs: dict[str, dict[str, Any]] = {
            "job-1": {
                "id": "job-1",
                "provider": "fake",
                "model": "fake-model",
                "status": "succeeded",
                "progress": 1.0,
                "completed_bytes": 1,
                "total_bytes": 1,
                "message": "ok",
                "error": None,
                "result": {"status": "downloaded"},
                "created_at": "now",
                "updated_at": "now",
                "finished_at": "now",
            }
        }

    def list_models(
        self,
        *,
        include_suggested: bool = True,
        include_remote_providers: bool = True,
        max_items_per_provider: int | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "list_models",
                {
                    "include_suggested": include_suggested,
                    "include_remote_providers": include_remote_providers,
                    "max_items_per_provider": max_items_per_provider,
                },
            )
        )
        return {
            "active": {"provider": self.active_provider, "model": self.active_model},
            "providers": {"fake": {"available": True, "items": [{"id": self.active_model}]}},
            "capabilities": self.provider_capabilities(),
            "suggested": {},
            "routing_modes": ["balanced"],
        }

    @staticmethod
    def provider_capabilities() -> dict[str, Any]:
        return {
            "fake": {
                "local": True,
                "supports_download": True,
                "supports_load": True,
                "supports_stream": True,
                "supports_tools": True,
                "requires_api_key": False,
            }
        }

    @staticmethod
    def provider_health() -> dict[str, Any]:
        return {"fake": {"status": "ok"}}

    def model_capability_matrix(self, *, include_suggested: bool = True, limit_per_provider: int = 120) -> dict[str, Any]:
        self.calls.append(
            (
                "model_capability_matrix",
                {
                    "include_suggested": include_suggested,
                    "limit_per_provider": limit_per_provider,
                },
            )
        )
        return {"active": {"provider": self.active_provider, "model": self.active_model}, "items": []}

    def recommend_onboarding_profile(self) -> dict[str, Any]:
        self.calls.append(("recommend_onboarding_profile", {}))
        return {
            "recommended_profile": "balanced",
            "profiles": {
                "balanced": {
                    "selected": {
                        "provider": self.active_provider,
                        "model": self.active_model,
                    }
                }
            },
        }

    def onboarding_activation_plan(
        self,
        *,
        profile: str | None = None,
        include_remote_providers: bool = True,
        limit: int = 120,
        require_metadata: bool | None = None,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "onboarding_activation_plan",
                {
                    "profile": profile,
                    "include_remote_providers": include_remote_providers,
                    "limit": limit,
                    "require_metadata": require_metadata,
                },
            )
        )
        package_id = f"{self.active_provider}::{self.active_model}"
        return {
            "plan_version": "onboarding_activation_plan_v1",
            "recommended_profile": "balanced",
            "selected_profile": str(profile or "balanced"),
            "selected_package_id": package_id,
            "selected_package": {"package_id": package_id},
            "license_admission": {"package_id": package_id, "admitted": True, "status": "allow"},
            "ready_to_install": True,
            "blockers": [],
            "next_action": "install_package",
            "install": {"endpoint": "/models/packages/install"},
        }

    def model_package_catalog(
        self,
        *,
        profile: str | None = None,
        include_remote_providers: bool = True,
        limit: int = 120,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "model_package_catalog",
                {
                    "profile": profile,
                    "include_remote_providers": include_remote_providers,
                    "limit": limit,
                },
            )
        )
        package_id = f"{self.active_provider}::{self.active_model}"
        return {
            "selected_profile": profile or "balanced",
            "packages": [{"package_id": package_id}],
            "count": 1,
        }

    def install_model_package(self, *, package_id: str, activate: bool = True) -> dict[str, Any]:
        self.calls.append(("install_model_package", {"package_id": package_id, "activate": activate}))
        return {
            "package_id": package_id,
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
        self.calls.append(
            (
                "model_package_license_admission",
                {
                    "package_id": package_id,
                    "require_metadata": require_metadata,
                },
            )
        )
        return {
            "package_id": package_id,
            "provider": self.active_provider,
            "model": self.active_model,
            "status": "allow",
            "admitted": True,
            "errors": [],
            "warnings": [],
            "summary": {"license_policy_id": "test.default"},
            "require_metadata": bool(require_metadata) if require_metadata is not None else False,
        }

    def choose_route(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("choose_route", dict(kwargs)))
        return {
            "mode": "balanced",
            "constraints": {},
            "selected": {"provider": self.active_provider, "model": self.active_model, "reason": "fake"},
            "fallbacks": [],
            "considered_count": 1,
        }

    def debug_failover_state(self, *, session_id: str | None = None, limit: int = 100) -> dict[str, Any]:
        self.calls.append(("debug_failover_state", {"session_id": session_id, "limit": limit}))
        return {"session_id": session_id, "recent_failovers": [], "recent_failovers_count": 0}

    def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("chat", {"messages": list(messages), **dict(kwargs)}))
        return {
            "content": "fake-response",
            "provider": self.active_provider,
            "model": self.active_model,
            "routing": {"selected": {"provider": self.active_provider, "model": self.active_model}},
        }

    def stream_chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> tuple[object, str, str, dict[str, Any]]:
        self.calls.append(("stream_chat", {"messages": list(messages), **dict(kwargs)}))
        return iter(["fake-", "stream"]), self.active_provider, self.active_model, {"selected": {"provider": "fake"}}

    def download_model(self, model_id: str, provider: str | None = None) -> dict[str, Any]:
        self.calls.append(("download_model", {"model_id": model_id, "provider": provider}))
        return {"status": "downloaded", "provider": provider or self.active_provider, "model": model_id}

    def start_model_download(self, model_id: str, provider: str | None = None) -> dict[str, Any]:
        self.calls.append(("start_model_download", {"model_id": model_id, "provider": provider}))
        return {"already_running": False, "job": dict(self.jobs["job-1"])}

    def get_model_download_job(self, job_id: str) -> dict[str, Any]:
        self.calls.append(("get_model_download_job", {"job_id": job_id}))
        return dict(self.jobs[job_id])

    def list_model_download_jobs(self, limit: int = 100) -> dict[str, Any]:
        self.calls.append(("list_model_download_jobs", {"limit": limit}))
        return {"items": [dict(item) for item in self.jobs.values()], "count": len(self.jobs)}

    def load_model(self, model_id: str, provider: str | None = None) -> dict[str, Any]:
        self.calls.append(("load_model", {"model_id": model_id, "provider": provider}))
        self.active_provider = provider or self.active_provider
        self.active_model = model_id
        return {
            "status": "loaded",
            "provider": self.active_provider,
            "model": self.active_model,
            "active": {"provider": self.active_provider, "model": self.active_model},
        }


class CognitionBackendsTests(unittest.TestCase):
    def _run_contract_suite(self, backend: CognitionBackendContract) -> None:
        listing = backend.list_models()
        self.assertIn("active", listing)
        self.assertIn("providers", listing)

        caps = backend.provider_capabilities()
        self.assertIsInstance(caps, dict)
        self.assertTrue(caps)

        health = backend.provider_health()
        self.assertIsInstance(health, dict)
        self.assertTrue(health)

        matrix = backend.model_capability_matrix(include_suggested=True, limit_per_provider=8)
        self.assertIn("active", matrix)

        onboarding = backend.recommend_onboarding_profile()
        self.assertIn("recommended_profile", onboarding)
        self.assertIn("profiles", onboarding)

        activation = backend.onboarding_activation_plan(profile="balanced", include_remote_providers=True, limit=10)
        self.assertEqual(str(activation.get("plan_version")), "onboarding_activation_plan_v1")
        self.assertTrue(str(activation.get("selected_package_id", "")).strip())
        self.assertIn("install", activation)

        catalog = backend.model_package_catalog(profile="balanced", include_remote_providers=True, limit=10)
        self.assertIn("packages", catalog)
        self.assertGreaterEqual(int(catalog.get("count", 0)), 1)

        package_id = str((catalog.get("packages") or [{}])[0].get("package_id", ""))
        installed = backend.install_model_package(package_id=package_id, activate=True)
        self.assertEqual(str(installed.get("package_id")), package_id)
        admission = backend.model_package_license_admission(package_id=package_id)
        self.assertEqual(str(admission.get("package_id")), package_id)
        self.assertTrue(bool(admission.get("admitted")))

        route = backend.choose_route(mode="balanced", require_stream=True)
        selected = route.get("selected")
        self.assertIsInstance(selected, dict)

        chat = backend.chat(messages=[{"role": "user", "content": "hello"}], session_id="s1")
        self.assertIn("content", chat)
        self.assertTrue(str(chat.get("provider", "")).strip())
        self.assertTrue(str(chat.get("model", "")).strip())

        stream, provider, model, routing = backend.stream_chat(
            messages=[{"role": "user", "content": "hello"}],
            session_id="s1",
        )
        first_chunk = next(stream)
        self.assertIsInstance(first_chunk, str)
        self.assertTrue(str(provider).strip())
        self.assertTrue(str(model).strip())
        if routing is not None:
            self.assertIsInstance(routing, dict)

        diagnostics = backend.debug_failover_state(session_id="s1", limit=10)
        self.assertIsInstance(diagnostics, dict)

        download = backend.download_model(model_id="test/model")
        self.assertIn("status", download)

        started = backend.start_model_download(model_id="test/model")
        self.assertIn("job", started)
        job_id = str((started.get("job") or {}).get("id"))
        self.assertTrue(job_id)

        fetched = backend.get_model_download_job(job_id=job_id)
        self.assertEqual(str(fetched.get("id")), job_id)

        jobs = backend.list_model_download_jobs(limit=10)
        self.assertIn("items", jobs)

        loaded = backend.load_model(model_id="test/model", provider=str(chat.get("provider")))
        self.assertIn("active", loaded)

    def test_deterministic_backend_passes_contract_suite(self) -> None:
        backend = DeterministicCognitionBackend()
        self.assertIsInstance(backend, CognitionBackendContract)
        self._run_contract_suite(backend)

    def test_model_manager_adapter_passes_contract_suite(self) -> None:
        fake = _FakeModelManager()
        backend = ModelManagerCognitionBackend(fake)  # type: ignore[arg-type]
        self.assertIsInstance(backend, CognitionBackendContract)
        self._run_contract_suite(backend)
        self.assertTrue(any(name == "chat" for name, _ in fake.calls))

    def test_model_manager_adapter_forwards_mutable_provider_state(self) -> None:
        fake = _FakeModelManager()
        backend = ModelManagerCognitionBackend(fake)  # type: ignore[arg-type]
        backend.providers = {"x": object()}
        backend.active_provider = "x"
        backend.active_model = "x-model"
        self.assertEqual(fake.providers.keys(), {"x"})
        self.assertEqual(fake.active_provider, "x")
        self.assertEqual(fake.active_model, "x-model")


if __name__ == "__main__":
    unittest.main()
