from __future__ import annotations

import importlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
except Exception:  # pragma: no cover - dependency may be unavailable
    TestClient = None  # type: ignore[assignment]

from voice.stt_adapter import STTTranscriptionRequest, STTTranscriptionResult


class _FakeSuccessSTTAdapter:
    def __init__(self) -> None:
        self.last_request: STTTranscriptionRequest | None = None

    def describe(self) -> dict[str, object]:
        return {
            "backend": "test-success",
            "provider": "fake-stt-success",
            "available": True,
            "supports_local": True,
        }

    def transcribe(self, request: STTTranscriptionRequest) -> STTTranscriptionResult:
        self.last_request = request
        return STTTranscriptionResult(
            ok=True,
            provider="fake-stt-success",
            text="hello local stt",
            language=request.language or "en",
            duration_ms=12,
            metadata={"test": True},
        )


class _FakeUnavailableSTTAdapter:
    def describe(self) -> dict[str, object]:
        return {
            "backend": "test-unavailable",
            "provider": "fake-stt-unavailable",
            "available": False,
            "reason": "test adapter unavailable",
            "supports_local": True,
        }

    def transcribe(self, request: STTTranscriptionRequest) -> STTTranscriptionResult:
        _ = request
        return STTTranscriptionResult(
            ok=False,
            provider="fake-stt-unavailable",
            text="",
            unavailable=True,
            error="test adapter unavailable",
            duration_ms=1,
        )


@unittest.skipIf(TestClient is None, "fastapi dependency is not available")
class VoiceAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="amaryllis-tests-voice-api-")
        support_dir = Path(cls._tmp.name) / "support"
        auth_tokens = {
            "admin-token": {"user_id": "admin", "scopes": ["admin", "user"]},
            "user-token": {"user_id": "user-1", "scopes": ["user"]},
            "user2-token": {"user_id": "user-2", "scopes": ["user"]},
            "service-token": {"user_id": "svc-runtime", "scopes": ["service"]},
        }
        cls._env_patch = patch.dict(
            os.environ,
            {
                "AMARYLLIS_SUPPORT_DIR": str(support_dir),
                "AMARYLLIS_AUTH_ENABLED": "true",
                "AMARYLLIS_AUTH_TOKENS": json.dumps(auth_tokens, ensure_ascii=False),
                "AMARYLLIS_MEMORY_CONSOLIDATION_ENABLED": "false",
                "AMARYLLIS_MCP_ENDPOINTS": "",
                "AMARYLLIS_SECURITY_PROFILE": "production",
            },
            clear=False,
        )
        cls._env_patch.start()

        import runtime.server as server_module

        cls.server_module = importlib.reload(server_module)
        cls._client_cm = TestClient(cls.server_module.app)
        cls.client = cls._client_cm.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls._client_cm.__exit__(None, None, None)
        cls._env_patch.stop()
        cls._tmp.cleanup()

    @staticmethod
    def _auth(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def test_voice_session_owner_scope_and_state_transitions(self) -> None:
        started = self.client.post(
            "/voice/sessions/start",
            headers=self._auth("user-token"),
            json={
                "user_id": "user-1",
                "mode": "ptt",
                "sample_rate_hz": 16000,
                "input_device": "default",
                "language": "en",
            },
        )
        self.assertEqual(started.status_code, 200)
        started_payload = started.json()
        session = started_payload.get("voice_session", {})
        session_id = str(session.get("id"))
        self.assertTrue(session_id)
        self.assertEqual(str(session.get("state")), "listening")
        self.assertTrue(bool(started_payload.get("action_receipt", {}).get("signature")))

        foreign_get = self.client.get(
            f"/voice/sessions/{session_id}",
            headers=self._auth("user2-token"),
        )
        self.assertEqual(foreign_get.status_code, 403)
        self.assertEqual(foreign_get.json()["error"]["type"], "permission_denied")

        own_get = self.client.get(
            f"/voice/sessions/{session_id}",
            headers=self._auth("user-token"),
        )
        self.assertEqual(own_get.status_code, 200)
        self.assertEqual(str(own_get.json().get("voice_session", {}).get("id")), session_id)

        foreign_stop = self.client.post(
            f"/voice/sessions/{session_id}/stop",
            headers=self._auth("user2-token"),
            json={"reason": "attempt-foreign-stop"},
        )
        self.assertEqual(foreign_stop.status_code, 403)
        self.assertEqual(foreign_stop.json()["error"]["type"], "permission_denied")

        stopped = self.client.post(
            f"/voice/sessions/{session_id}/stop",
            headers=self._auth("user-token"),
            json={"reason": "done-speaking"},
        )
        self.assertEqual(stopped.status_code, 200)
        stopped_payload = stopped.json()
        stopped_session = stopped_payload.get("voice_session", {})
        self.assertEqual(str(stopped_session.get("state")), "stopped")
        transitions = stopped_session.get("transitions", [])
        self.assertTrue(any(str(item.get("to_state")) == "stopping" for item in transitions))
        self.assertTrue(any(str(item.get("to_state")) == "stopped" for item in transitions))

        stopped_again = self.client.post(
            f"/voice/sessions/{session_id}/stop",
            headers=self._auth("user-token"),
            json={"reason": "idempotent"},
        )
        self.assertEqual(stopped_again.status_code, 200)
        self.assertEqual(
            str(stopped_again.json().get("voice_session", {}).get("state")),
            "stopped",
        )

    def test_voice_sessions_listing_is_user_scoped(self) -> None:
        owner_start = self.client.post(
            "/voice/sessions/start",
            headers=self._auth("user-token"),
            json={"user_id": "user-1", "mode": "ptt"},
        )
        self.assertEqual(owner_start.status_code, 200)

        other_start = self.client.post(
            "/voice/sessions/start",
            headers=self._auth("user2-token"),
            json={"user_id": "user-2", "mode": "ptt"},
        )
        self.assertEqual(other_start.status_code, 200)

        owner_list = self.client.get("/voice/sessions", headers=self._auth("user-token"))
        self.assertEqual(owner_list.status_code, 200)
        owner_items = owner_list.json().get("items", [])
        self.assertTrue(owner_items)
        self.assertTrue(all(str(item.get("user_id")) == "user-1" for item in owner_items))

        admin_list_user2 = self.client.get(
            "/voice/sessions",
            headers=self._auth("admin-token"),
            params={"user_id": "user-2"},
        )
        self.assertEqual(admin_list_user2.status_code, 200)
        admin_items = admin_list_user2.json().get("items", [])
        self.assertTrue(admin_items)
        self.assertTrue(all(str(item.get("user_id")) == "user-2" for item in admin_items))

        service_denied = self.client.get("/voice/sessions", headers=self._auth("service-token"))
        self.assertEqual(service_denied.status_code, 403)
        self.assertEqual(service_denied.json()["error"]["type"], "permission_denied")

    def test_voice_stt_health_and_graceful_unavailable_transcribe(self) -> None:
        services = self.server_module.app.state.services
        original_adapter = services.stt_adapter
        services.stt_adapter = _FakeUnavailableSTTAdapter()
        try:
            health = self.client.get("/voice/stt/health", headers=self._auth("user-token"))
            self.assertEqual(health.status_code, 200)
            self.assertFalse(bool(health.json().get("stt", {}).get("available")))

            transcribe = self.client.post(
                "/voice/stt/transcribe",
                headers=self._auth("user-token"),
                json={
                    "user_id": "user-1",
                    "audio_base64": "aGVsbG8=",
                    "language": "en",
                },
            )
            self.assertEqual(transcribe.status_code, 200)
            payload = transcribe.json()
            transcription = payload.get("transcription", {})
            self.assertFalse(bool(transcription.get("ok")))
            self.assertTrue(bool(transcription.get("unavailable")))
            self.assertTrue(str(transcription.get("error", "")).strip())
            self.assertTrue(bool(payload.get("action_receipt", {}).get("signature")))
        finally:
            services.stt_adapter = original_adapter

    def test_voice_stt_transcribe_enforces_session_owner(self) -> None:
        services = self.server_module.app.state.services
        original_adapter = services.stt_adapter
        fake_adapter = _FakeSuccessSTTAdapter()
        services.stt_adapter = fake_adapter
        try:
            started = self.client.post(
                "/voice/sessions/start",
                headers=self._auth("user-token"),
                json={"user_id": "user-1", "mode": "ptt"},
            )
            self.assertEqual(started.status_code, 200)
            session_id = str(started.json().get("voice_session", {}).get("id"))
            self.assertTrue(session_id)

            foreign = self.client.post(
                "/voice/stt/transcribe",
                headers=self._auth("user2-token"),
                json={
                    "session_id": session_id,
                    "audio_base64": "aGVsbG8=",
                    "language": "en",
                },
            )
            self.assertEqual(foreign.status_code, 403)
            self.assertEqual(foreign.json()["error"]["type"], "permission_denied")

            missing = self.client.post(
                "/voice/stt/transcribe",
                headers=self._auth("user-token"),
                json={
                    "session_id": "voice-missing",
                    "audio_base64": "aGVsbG8=",
                },
            )
            self.assertEqual(missing.status_code, 404)
            self.assertEqual(missing.json()["error"]["type"], "not_found")

            own = self.client.post(
                "/voice/stt/transcribe",
                headers=self._auth("user-token"),
                json={
                    "session_id": session_id,
                    "audio_base64": "aGVsbG8=",
                    "language": "en",
                    "metadata": {"source": "voice-api-test"},
                },
            )
            self.assertEqual(own.status_code, 200)
            payload = own.json()
            transcription = payload.get("transcription", {})
            self.assertTrue(bool(transcription.get("ok")))
            self.assertEqual(str(transcription.get("text")), "hello local stt")
            self.assertTrue(bool(payload.get("action_receipt", {}).get("signature")))

            self.assertIsNotNone(fake_adapter.last_request)
            assert fake_adapter.last_request is not None
            self.assertEqual(fake_adapter.last_request.language, "en")
            self.assertEqual(
                str(fake_adapter.last_request.metadata.get("session_id")),
                session_id,
            )
            self.assertEqual(
                str(fake_adapter.last_request.metadata.get("user_id")),
                "user-1",
            )
        finally:
            services.stt_adapter = original_adapter


if __name__ == "__main__":
    unittest.main()
