from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from voice.stt_adapter import (
    LocalWhisperPythonSTTAdapter,
    STTTranscriptionRequest,
    create_stt_adapter_from_env,
)


class _FakeWhisperModel:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def transcribe(self, path: str, **kwargs: object) -> dict[str, object]:
        self.calls.append((path, kwargs))
        return {
            "text": "hello from local stt",
            "language": "en",
            "segments": [{"id": 1}],
        }


class _FakeWhisperModule:
    def __init__(self, model: _FakeWhisperModel) -> None:
        self.model = model
        self.loaded_models: list[str] = []

    def load_model(self, model_name: str) -> _FakeWhisperModel:
        self.loaded_models.append(model_name)
        return self.model


class STTAdapterTests(unittest.TestCase):
    def test_whisper_adapter_gracefully_reports_unavailable_when_module_missing(self) -> None:
        adapter = LocalWhisperPythonSTTAdapter(module_name="module_that_does_not_exist_for_tests")
        result = adapter.transcribe(
            STTTranscriptionRequest(
                audio_path="/tmp/does-not-matter.wav",
                language="en",
            )
        )
        self.assertFalse(result.ok)
        self.assertTrue(result.unavailable)
        self.assertTrue(str(result.error).strip())
        description = adapter.describe()
        self.assertFalse(bool(description.get("available")))

    def test_whisper_adapter_transcribes_audio_path_with_fake_module(self) -> None:
        model = _FakeWhisperModel()
        module = _FakeWhisperModule(model)
        adapter = LocalWhisperPythonSTTAdapter(model_name="tiny")

        with tempfile.NamedTemporaryFile(prefix="amaryllis-stt-", suffix=".wav", delete=False) as temp_file:
            temp_file.write(b"fake-audio")
            audio_path = temp_file.name

        try:
            with patch("voice.stt_adapter.import_module", return_value=module):
                result = adapter.transcribe(
                    STTTranscriptionRequest(
                        audio_path=audio_path,
                        language="en",
                        prompt="focus",
                        temperature=0.2,
                    )
                )
        finally:
            Path(audio_path).unlink(missing_ok=True)

        self.assertTrue(result.ok)
        self.assertFalse(result.unavailable)
        self.assertEqual(result.text, "hello from local stt")
        self.assertEqual(module.loaded_models, ["tiny"])
        self.assertEqual(len(model.calls), 1)
        called_path, called_kwargs = model.calls[0]
        self.assertEqual(called_path, audio_path)
        self.assertEqual(called_kwargs.get("language"), "en")
        self.assertEqual(called_kwargs.get("initial_prompt"), "focus")
        self.assertEqual(called_kwargs.get("temperature"), 0.2)

    def test_whisper_adapter_transcribes_audio_bytes_and_cleans_temp_file(self) -> None:
        model = _FakeWhisperModel()
        module = _FakeWhisperModule(model)
        adapter = LocalWhisperPythonSTTAdapter()

        with patch("voice.stt_adapter.import_module", return_value=module):
            result = adapter.transcribe(
                STTTranscriptionRequest(
                    audio_bytes=b"fake-bytes",
                    language="en",
                )
            )

        self.assertTrue(result.ok)
        self.assertEqual(len(model.calls), 1)
        temp_path, _ = model.calls[0]
        self.assertFalse(Path(temp_path).exists())

    def test_create_stt_adapter_from_env_handles_unknown_backend(self) -> None:
        with patch.dict(os.environ, {"AMARYLLIS_VOICE_STT_BACKEND": "unknown-backend"}, clear=False):
            adapter = create_stt_adapter_from_env()
            description = adapter.describe()
            self.assertFalse(bool(description.get("available")))
            self.assertIn("unsupported", str(description.get("reason")).lower())


if __name__ == "__main__":
    unittest.main()
