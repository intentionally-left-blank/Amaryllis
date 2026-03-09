from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


class MLXProvider:
    def __init__(self, models_dir: Path) -> None:
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)

        self.logger = logging.getLogger("amaryllis.models.mlx")
        self.active_model: str | None = None

        self._model = None
        self._tokenizer = None
        self._generate_fn = None

    @staticmethod
    def _model_to_folder(model_id: str) -> str:
        return model_id.replace("/", "__")

    @staticmethod
    def _folder_to_model(folder_name: str) -> str:
        return folder_name.replace("__", "/")

    def list_models(self) -> list[dict[str, Any]]:
        models: list[dict[str, Any]] = []

        for item in sorted(self.models_dir.iterdir()):
            if not item.is_dir():
                continue

            metadata_path = item / "model.json"
            metadata: dict[str, Any] = {}
            if metadata_path.exists():
                try:
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                except Exception:
                    metadata = {}

            model_id = metadata.get("model_id") or self._folder_to_model(item.name)
            models.append(
                {
                    "id": model_id,
                    "provider": "mlx",
                    "path": str(item),
                    "active": model_id == self.active_model,
                    "metadata": metadata,
                }
            )

        return models

    def download_model(self, model_id: str) -> dict[str, Any]:
        folder = self.models_dir / self._model_to_folder(model_id)
        folder.mkdir(parents=True, exist_ok=True)

        metadata = {
            "model_id": model_id,
            "provider": "mlx",
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
            "source": "local-placeholder",
        }

        try:
            from huggingface_hub import snapshot_download  # type: ignore

            snapshot_download(
                repo_id=model_id,
                local_dir=str(folder),
                local_dir_use_symlinks=False,
            )
            metadata["source"] = "huggingface_hub"
        except Exception as exc:  # pragma: no cover - optional path
            metadata["note"] = (
                "huggingface_hub is not available or download failed. "
                "Model may still load lazily via mlx_lm if installed."
            )
            metadata["error"] = str(exc)
            self.logger.warning("mlx_download_fallback model=%s error=%s", model_id, exc)

        (folder / "model.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return {
            "status": "downloaded",
            "provider": "mlx",
            "model": model_id,
            "path": str(folder),
            "metadata": metadata,
        }

    def load_model(self, model_id: str) -> dict[str, Any]:
        try:
            from mlx_lm import generate as mlx_generate  # type: ignore
            from mlx_lm import load as mlx_load  # type: ignore
        except Exception as exc:  # pragma: no cover - runtime dependency
            raise RuntimeError(
                "mlx_lm is required for MLX inference. Install with: pip install mlx-lm"
            ) from exc

        local_path = self.models_dir / self._model_to_folder(model_id)
        model_source = str(local_path) if local_path.exists() else model_id

        self.logger.info("mlx_load_start model=%s source=%s", model_id, model_source)
        self._model, self._tokenizer = mlx_load(model_source)
        self._generate_fn = mlx_generate
        self.active_model = model_id
        self.logger.info("mlx_load_done model=%s", model_id)

        return {
            "status": "loaded",
            "provider": "mlx",
            "model": model_id,
            "source": model_source,
        }

    def chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> str:
        if self.active_model != model or self._model is None or self._tokenizer is None:
            self.load_model(model)

        prompt = self._messages_to_prompt(messages)
        generate_fn = self._generate_fn
        if generate_fn is None or self._model is None or self._tokenizer is None:
            raise RuntimeError("MLX model is not loaded")

        attempts = [
            {"max_tokens": max_tokens, "temp": temperature, "verbose": False},
            {"max_tokens": max_tokens, "temperature": temperature, "verbose": False},
            {"max_tokens": max_tokens, "verbose": False},
        ]

        last_exc: Exception | None = None
        for kwargs in attempts:
            try:
                output = generate_fn(self._model, self._tokenizer, prompt=prompt, **kwargs)
                if output is None:
                    return ""
                if isinstance(output, str):
                    return output.strip()
                return str(output).strip()
            except TypeError as exc:
                last_exc = exc
                continue

        raise RuntimeError(f"Failed to call mlx_lm.generate: {last_exc}")

    def stream_chat(
        self,
        messages: list[dict[str, Any]],
        model: str,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> Iterator[str]:
        text = self.chat(
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        if not text:
            return iter(())

        chunks = [f"{token} " for token in text.split(" ")]
        if chunks:
            chunks[-1] = chunks[-1].rstrip()
        return iter(chunks)

    @staticmethod
    def _messages_to_prompt(messages: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for message in messages:
            role = str(message.get("role", "user")).upper()
            content = str(message.get("content", ""))
            lines.append(f"{role}: {content}")
        lines.append("ASSISTANT:")
        return "\n".join(lines)
