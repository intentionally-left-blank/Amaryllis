from __future__ import annotations

import base64
from dataclasses import dataclass, field
from importlib import import_module
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import perf_counter
from typing import Any, Protocol

DEFAULT_STT_BACKEND = "whisper_python"
SUPPORTED_STT_BACKENDS: tuple[str, ...] = (
    "whisper_python",
    "none",
)


@dataclass(frozen=True)
class STTTranscriptionRequest:
    audio_path: str | None = None
    audio_bytes: bytes | None = None
    language: str | None = None
    prompt: str | None = None
    temperature: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "STTTranscriptionRequest":
        data = payload if isinstance(payload, dict) else {}
        audio_path = _optional_str(data.get("audio_path"))
        audio_bytes = _extract_audio_bytes(data.get("audio_base64"))
        language = _optional_str(data.get("language"))
        prompt = _optional_str(data.get("prompt"))
        temperature = _optional_float(data.get("temperature"))
        metadata_raw = data.get("metadata")
        metadata = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}
        if audio_path is None and audio_bytes is None:
            raise ValueError("audio_path or audio_base64 is required")
        return cls(
            audio_path=audio_path,
            audio_bytes=audio_bytes,
            language=language,
            prompt=prompt,
            temperature=temperature,
            metadata=metadata,
        )


@dataclass(frozen=True)
class STTTranscriptionResult:
    ok: bool
    provider: str
    text: str
    language: str | None = None
    duration_ms: int | None = None
    unavailable: bool = False
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "provider": str(self.provider),
            "text": str(self.text),
            "language": self.language,
            "duration_ms": self.duration_ms,
            "unavailable": bool(self.unavailable),
            "error": self.error,
            "metadata": dict(self.metadata),
            "warnings": list(self.warnings),
        }


class STTAdapter(Protocol):
    def describe(self) -> dict[str, Any]:
        ...

    def transcribe(self, request: STTTranscriptionRequest) -> STTTranscriptionResult:
        ...


class UnavailableSTTAdapter:
    def __init__(self, *, reason: str, backend: str = "none") -> None:
        self._reason = str(reason or "STT backend is unavailable").strip() or "STT backend is unavailable"
        self._backend = str(backend or "none").strip() or "none"

    def describe(self) -> dict[str, Any]:
        return {
            "backend": self._backend,
            "provider": "stt-unavailable",
            "available": False,
            "reason": self._reason,
            "supports_local": True,
        }

    def transcribe(self, request: STTTranscriptionRequest) -> STTTranscriptionResult:
        _ = request
        return STTTranscriptionResult(
            ok=False,
            provider="stt-unavailable",
            text="",
            unavailable=True,
            error=self._reason,
            metadata={"backend": self._backend},
            warnings=["stt backend unavailable"],
        )


class LocalWhisperPythonSTTAdapter:
    def __init__(
        self,
        *,
        model_name: str = "base",
        module_name: str = "whisper",
        provider_name: str = "local-whisper-python",
    ) -> None:
        self.model_name = str(model_name or "base").strip() or "base"
        self.module_name = str(module_name or "whisper").strip() or "whisper"
        self.provider_name = str(provider_name or "local-whisper-python").strip() or "local-whisper-python"
        self._module: Any | None = None
        self._load_error: str | None = None
        self._model_cache: Any | None = None

    def describe(self) -> dict[str, Any]:
        module = self._ensure_module()
        return {
            "backend": "whisper_python",
            "provider": self.provider_name,
            "available": module is not None,
            "reason": self._load_error,
            "model_name": self.model_name,
            "supports_local": True,
        }

    def transcribe(self, request: STTTranscriptionRequest) -> STTTranscriptionResult:
        started = perf_counter()
        module = self._ensure_module()
        if module is None:
            return STTTranscriptionResult(
                ok=False,
                provider=self.provider_name,
                text="",
                language=request.language,
                duration_ms=_elapsed_ms(started),
                unavailable=True,
                error=self._load_error or "whisper module is unavailable",
                metadata={"backend": "whisper_python", "model_name": self.model_name},
                warnings=["local stt backend is unavailable"],
            )

        temp_path: str | None = None
        audio_path = _optional_str(request.audio_path)
        if audio_path is None and request.audio_bytes is not None:
            with NamedTemporaryFile(prefix="amaryllis-voice-", suffix=".wav", delete=False) as temp_file:
                temp_file.write(request.audio_bytes)
                temp_path = temp_file.name
            audio_path = temp_path

        if audio_path is None:
            raise ValueError("audio_path or audio_bytes is required")

        target = Path(audio_path)
        if not target.exists():
            raise ValueError(f"Audio file not found: {audio_path}")

        try:
            model = self._load_model(module)
            kwargs: dict[str, Any] = {}
            if request.language:
                kwargs["language"] = request.language
            if request.prompt:
                kwargs["initial_prompt"] = request.prompt
            if request.temperature is not None:
                kwargs["temperature"] = float(request.temperature)
            output = model.transcribe(str(target), **kwargs)
            output_payload = output if isinstance(output, dict) else {}
            text = str(output_payload.get("text") or "").strip()
            language = _optional_str(output_payload.get("language")) or request.language
            segments = output_payload.get("segments")
            segment_count = len(segments) if isinstance(segments, list) else 0
            return STTTranscriptionResult(
                ok=bool(text),
                provider=self.provider_name,
                text=text,
                language=language,
                duration_ms=_elapsed_ms(started),
                metadata={
                    "backend": "whisper_python",
                    "model_name": self.model_name,
                    "segment_count": segment_count,
                },
            )
        except Exception as exc:
            return STTTranscriptionResult(
                ok=False,
                provider=self.provider_name,
                text="",
                language=request.language,
                duration_ms=_elapsed_ms(started),
                unavailable=False,
                error=str(exc),
                metadata={
                    "backend": "whisper_python",
                    "model_name": self.model_name,
                },
            )
        finally:
            if temp_path:
                try:
                    Path(temp_path).unlink(missing_ok=True)
                except Exception:
                    pass

    def _ensure_module(self) -> Any | None:
        if self._module is not None:
            return self._module
        if self._load_error is not None:
            return None
        try:
            self._module = import_module(self.module_name)
            return self._module
        except Exception as exc:
            self._load_error = str(exc)
            self._module = None
            return None

    def _load_model(self, module: Any) -> Any:
        if self._model_cache is not None:
            return self._model_cache
        loader = getattr(module, "load_model", None)
        if not callable(loader):
            raise RuntimeError("whisper module does not provide load_model()")
        self._model_cache = loader(self.model_name)
        return self._model_cache


def create_stt_adapter_from_env() -> STTAdapter:
    backend = str(os.getenv("AMARYLLIS_VOICE_STT_BACKEND", DEFAULT_STT_BACKEND)).strip().lower()
    if backend in {"whisper_python", "whisper", "local_whisper"}:
        model_name = str(os.getenv("AMARYLLIS_VOICE_STT_MODEL", "base")).strip() or "base"
        return LocalWhisperPythonSTTAdapter(model_name=model_name)
    if backend in {"none", "disabled"}:
        return UnavailableSTTAdapter(reason="Voice STT backend is disabled by configuration", backend=backend)
    allowed = ", ".join(SUPPORTED_STT_BACKENDS)
    return UnavailableSTTAdapter(
        reason=f"Unsupported voice STT backend '{backend}'. Allowed values: {allowed}.",
        backend=backend,
    )


def _optional_str(value: Any) -> str | None:
    text = str(value).strip() if value not in (None, "") else ""
    return text or None


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except Exception:
        raise ValueError(f"Invalid float value: {value}") from None
    return parsed


def _extract_audio_bytes(value: Any) -> bytes | None:
    if value in (None, ""):
        return None
    encoded = str(value).strip()
    if not encoded:
        return None
    try:
        return base64.b64decode(encoded, validate=True)
    except Exception:
        raise ValueError("audio_base64 must be valid base64 content") from None


def _elapsed_ms(started: float) -> int:
    return max(0, int((perf_counter() - started) * 1000.0))
