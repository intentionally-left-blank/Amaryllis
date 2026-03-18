from __future__ import annotations

import gc
import inspect
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Iterator

from models.routing import estimate_model_size_b

MLX_MEMORY_BUDGET_RATIO = 0.72
MLX_MEMORY_OVERHEAD_FACTOR = 1.35
FALLBACK_MLX_SUGGESTED_MODELS: list[str] = [
    "mlx-community/Qwen2.5-0.5B-Instruct-4bit",
    "mlx-community/Qwen2.5-1.5B-Instruct-4bit",
    "mlx-community/Qwen2.5-3B-Instruct-4bit",
    "mlx-community/Qwen2.5-7B-Instruct-4bit",
    "mlx-community/Qwen2.5-14B-Instruct-4bit",
    "mlx-community/Qwen2.5-32B-Instruct-4bit",
    "mlx-community/Qwen2.5-72B-Instruct-4bit",
    "mlx-community/Qwen2.5-Coder-1.5B-Instruct-4bit",
    "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit",
    "mlx-community/Qwen2.5-Coder-14B-Instruct-4bit",
    "mlx-community/Qwen2.5-Coder-32B-Instruct-4bit",
    "mlx-community/QwQ-32B-Preview-4bit",
    "mlx-community/Llama-3.2-1B-Instruct-4bit",
    "mlx-community/Llama-3.2-3B-Instruct-4bit",
    "mlx-community/Llama-3.1-8B-Instruct-4bit",
    "mlx-community/Llama-3.1-70B-Instruct-4bit",
    "mlx-community/Meta-Llama-3-8B-Instruct-4bit",
    "mlx-community/Meta-Llama-3-70B-Instruct-4bit",
    "mlx-community/Mistral-7B-Instruct-v0.3-4bit",
    "mlx-community/Mistral-Nemo-Instruct-2407-4bit",
    "mlx-community/Mixtral-8x7B-Instruct-v0.1-4bit",
    "mlx-community/Mixtral-8x22B-Instruct-v0.1-4bit",
    "mlx-community/c4ai-command-r-v01-4bit",
    "mlx-community/c4ai-command-r-plus-08-2024-4bit",
    "mlx-community/Phi-3-mini-4k-instruct-4bit",
    "mlx-community/Phi-3-medium-4k-instruct-4bit",
    "mlx-community/Phi-3.5-mini-instruct-4bit",
    "mlx-community/Phi-3.5-MoE-instruct-4bit",
    "mlx-community/phi-4-4bit",
    "mlx-community/gemma-2-2b-it-4bit",
    "mlx-community/gemma-2-9b-it-4bit",
    "mlx-community/gemma-2-27b-it-4bit",
    "mlx-community/DeepSeek-R1-Distill-Qwen-1.5B-4bit",
    "mlx-community/DeepSeek-R1-Distill-Qwen-7B-4bit",
    "mlx-community/DeepSeek-R1-Distill-Qwen-14B-4bit",
    "mlx-community/DeepSeek-R1-Distill-Qwen-32B-4bit",
    "mlx-community/DeepSeek-R1-Distill-Llama-8B-4bit",
    "mlx-community/DeepSeek-R1-Distill-Llama-70B-4bit",
    "mlx-community/deepseek-coder-1.3b-instruct-4bit",
    "mlx-community/deepseek-coder-6.7b-instruct-4bit",
    "mlx-community/deepseek-coder-33b-instruct-4bit",
    "mlx-community/StarCoder2-3B-4bit",
    "mlx-community/StarCoder2-7B-4bit",
    "mlx-community/StarCoder2-15B-4bit",
    "mlx-community/CodeLlama-7b-Instruct-hf-4bit",
    "mlx-community/CodeLlama-13b-Instruct-hf-4bit",
    "mlx-community/CodeLlama-34b-Instruct-hf-4bit",
    "mlx-community/SmolLM2-1.7B-Instruct-4bit",
    "mlx-community/SmolLM2-360M-Instruct-4bit",
    "mlx-community/TinyLlama-1.1B-Chat-v1.0-4bit",
    "mlx-community/OpenHermes-2.5-Mistral-7B-4bit",
    "mlx-community/Nous-Hermes-2-Mixtral-8x7B-DPO-4bit",
    "mlx-community/zephyr-7b-beta-4bit",
    "mlx-community/yi-1.5-6b-chat-4bit",
    "mlx-community/yi-1.5-9b-chat-4bit",
    "mlx-community/yi-1.5-34b-chat-4bit",
    "mlx-community/solar-10.7b-instruct-v1.0-4bit",
    "mlx-community/dolphin-2.6-mistral-7b-4bit",
    "mlx-community/OpenBioLLM-Llama3-8B-4bit",
    "mlx-community/Granite-3.1-8B-Instruct-4bit",
    "mlx-community/Granite-3.1-2B-Instruct-4bit",
]


class MLXProvider:
    def __init__(self, models_dir: Path) -> None:
        self.models_dir = Path(models_dir)
        self.models_dir.mkdir(parents=True, exist_ok=True)

        self.logger = logging.getLogger("amaryllis.models.mlx")
        self.active_model: str | None = None

        self._model = None
        self._tokenizer = None
        self._generate_fn = None
        self._inference_lock = RLock()

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
            if not (item / "config.json").exists():
                # Skip incomplete/broken folders left after interrupted downloads.
                continue

            metadata_path = item / "model.json"
            metadata: dict[str, Any] = {}
            if metadata_path.exists():
                try:
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                except Exception:
                    metadata = {}

            model_id = metadata.get("model_id") or self._folder_to_model(item.name)
            size_bytes = _to_int(metadata.get("size_bytes"))
            if size_bytes is None:
                size_bytes = _to_int(metadata.get("estimated_total_bytes"))
            if size_bytes is None:
                estimated = estimate_model_size_b(str(model_id))
                if estimated is not None:
                    size_bytes = int(estimated * 1_000_000_000 * 0.56)
            if size_bytes is not None and size_bytes > 0:
                metadata["size_bytes"] = int(size_bytes)
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

    def health_check(self) -> dict[str, Any]:
        installed_models = self.list_models()
        return {
            "status": "ok",
            "detail": f"local_provider_ready=true installed_models={len(installed_models)}",
        }

    def capabilities(self) -> dict[str, Any]:
        return {
            "local": True,
            "supports_download": True,
            "supports_load": True,
            "supports_stream": True,
            "supports_tools": False,
            "requires_api_key": False,
        }

    def fallback_suggested_models(self, limit: int = 300) -> list[dict[str, Any]]:
        suggestions: list[dict[str, Any]] = []
        seen: set[str] = set()
        for model_id in FALLBACK_MLX_SUGGESTED_MODELS:
            normalized = model_id.strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            suggestions.append(
                {
                    "id": normalized,
                    "label": self._label_from_model_id(normalized),
                    "size_bytes": self._estimate_download_size_bytes(normalized),
                }
            )
            if len(suggestions) >= max(1, limit):
                break
        return suggestions

    def suggested_models(self, limit: int = 300) -> list[dict[str, Any]]:
        target_limit = max(1, min(int(limit), 220))
        suggestions = self.fallback_suggested_models(limit=target_limit)
        seen = {str(item.get("id", "")).strip() for item in suggestions if isinstance(item, dict)}

        def add(model_id: str) -> None:
            normalized = model_id.strip()
            if not normalized or normalized in seen:
                return
            seen.add(normalized)
            suggestions.append(
                {
                    "id": normalized,
                    "label": self._label_from_model_id(normalized),
                    "size_bytes": self._estimate_download_size_bytes(normalized),
                }
            )
            if len(suggestions) > target_limit:
                del suggestions[target_limit:]

        try:
            from huggingface_hub import HfApi  # type: ignore

            api = HfApi()
            for model in api.list_models(author="mlx-community", limit=target_limit):
                model_id = getattr(model, "id", None) or getattr(model, "modelId", None)
                if isinstance(model_id, str):
                    add(model_id)
                    if len(suggestions) >= target_limit:
                        break

            self.logger.info("mlx_suggested_catalog_loaded count=%s", len(suggestions[:target_limit]))
        except Exception as exc:  # pragma: no cover - runtime dependency/network
            self.logger.warning("mlx_suggested_catalog_fetch_failed error=%s", exc)
        return suggestions[:target_limit]

    def download_model(
        self,
        model_id: str,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        folder = self.models_dir / self._model_to_folder(model_id)
        folder.mkdir(parents=True, exist_ok=True)

        metadata = {
            "model_id": model_id,
            "provider": "mlx",
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
            "source": "local-placeholder",
        }

        self._emit_progress(
            progress_callback,
            {
                "status": "queued",
                "message": "Preparing model download",
                "progress": 0.0,
            },
        )

        try:
            from huggingface_hub import HfApi, hf_hub_download, snapshot_download  # type: ignore

            api = HfApi()
            repo_files = self._list_repo_files(api=api, model_id=model_id)
            total_bytes = sum(size for _, size in repo_files if size and size > 0)
            if total_bytes > 0:
                metadata["estimated_total_bytes"] = total_bytes

            self._emit_progress(
                progress_callback,
                {
                    "status": "running",
                    "message": "Starting file transfer",
                    "completed_bytes": 0,
                    "total_bytes": total_bytes if total_bytes > 0 else None,
                    "progress": 0.0,
                },
            )

            downloaded_bytes = 0
            if repo_files:
                for index, (filename, expected_size) in enumerate(repo_files, start=1):
                    kwargs: dict[str, Any] = {
                        "repo_id": model_id,
                        "filename": filename,
                        "local_dir": str(folder),
                        "resume_download": True,
                    }
                    signature = inspect.signature(hf_hub_download)
                    if "local_dir_use_symlinks" in signature.parameters:
                        kwargs["local_dir_use_symlinks"] = False
                    local_path = Path(hf_hub_download(**kwargs))
                    file_size = expected_size if expected_size and expected_size > 0 else int(local_path.stat().st_size)
                    downloaded_bytes += max(0, int(file_size))

                    progress: float | None = None
                    if total_bytes > 0:
                        progress = max(0.0, min(1.0, float(downloaded_bytes) / float(total_bytes)))
                    self._emit_progress(
                        progress_callback,
                        {
                            "status": "running",
                            "message": f"Downloading file {index}/{len(repo_files)}",
                            "current_file": filename,
                            "completed_bytes": downloaded_bytes,
                            "total_bytes": total_bytes if total_bytes > 0 else None,
                            "progress": progress,
                        },
                    )
            else:
                kwargs = {
                    "repo_id": model_id,
                    "local_dir": str(folder),
                    "resume_download": True,
                }
                signature = inspect.signature(snapshot_download)
                if "local_dir_use_symlinks" in signature.parameters:
                    kwargs["local_dir_use_symlinks"] = False
                snapshot_download(**kwargs)

            size_bytes = self._folder_size_bytes(folder)
            if not self._has_mlx_weights(folder):
                raise RuntimeError(
                    (
                        f"Model '{model_id}' is not MLX-chat compatible: "
                        "no .safetensors weights were found after download."
                    )
                )
            if not (folder / "config.json").exists():
                raise RuntimeError(
                    (
                        f"Model '{model_id}' is incomplete for MLX runtime: "
                        "config.json is missing after download."
                    )
                )
            metadata["size_bytes"] = size_bytes
            metadata["source"] = "huggingface_hub"
            metadata["status"] = "ok"
            self._emit_progress(
                progress_callback,
                {
                    "status": "succeeded",
                    "message": "Download completed",
                    "completed_bytes": size_bytes,
                    "total_bytes": size_bytes,
                    "progress": 1.0,
                },
            )
        except Exception as exc:  # pragma: no cover - runtime dependency/network
            metadata["status"] = "failed"
            metadata["error"] = str(exc)
            self.logger.error("mlx_download_failed model=%s error=%s", model_id, exc)
            self._emit_progress(
                progress_callback,
                {
                    "status": "failed",
                    "message": str(exc),
                    "progress": 0.0,
                },
            )

            (folder / "model.json").write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            raise RuntimeError(
                f"Failed to download model '{model_id}'. Check internet access and model id."
            ) from exc

        (folder / "model.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return {
            "status": "downloaded",
            "provider": "mlx",
            "model": model_id,
            "path": str(folder),
            "size_bytes": _to_int(metadata.get("size_bytes")),
            "metadata": metadata,
        }

    def load_model(self, model_id: str) -> dict[str, Any]:
        with self._inference_lock:
            if (
                self.active_model == model_id
                and self._model is not None
                and self._tokenizer is not None
                and self._generate_fn is not None
            ):
                return {
                    "status": "loaded",
                    "provider": "mlx",
                    "model": model_id,
                    "source": "cached",
                }

            self._guard_memory_budget(model_id=model_id)

            if self._model is not None or self._tokenizer is not None:
                self._model = None
                self._tokenizer = None
                self._generate_fn = None
                gc.collect()

            try:
                from mlx_lm import generate as mlx_generate  # type: ignore
                from mlx_lm import load as mlx_load  # type: ignore
            except Exception as exc:  # pragma: no cover - runtime dependency
                raise RuntimeError(
                    "mlx_lm is required for MLX inference. Install with: pip install mlx-lm"
                ) from exc

            local_path = self.models_dir / self._model_to_folder(model_id)
            local_config = local_path / "config.json"
            local_ready = local_path.exists() and local_config.exists()
            if local_path.exists() and not local_ready:
                self.logger.warning(
                    "mlx_local_model_incomplete model=%s path=%s reason=missing_config",
                    model_id,
                    local_path,
                )
            model_source = str(local_path) if local_ready else model_id

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
        with self._inference_lock:
            if self.active_model != model or self._model is None or self._tokenizer is None:
                self.load_model(model)

            prompt = self._build_prompt(messages=messages, tokenizer=self._tokenizer)
            generate_fn = self._generate_fn
            if generate_fn is None or self._model is None or self._tokenizer is None:
                raise RuntimeError("MLX model is not loaded")

            attempts = self._build_generation_attempts(
                generate_fn=generate_fn,
                max_tokens=max_tokens,
                temperature=temperature,
            )

            last_exc: Exception | None = None
            for kwargs in attempts:
                try:
                    output = generate_fn(self._model, self._tokenizer, prompt=prompt, **kwargs)
                    if output is None:
                        return ""
                    raw_text = output if isinstance(output, str) else str(output)
                    return self._normalize_generation_output(raw_text, prompt=prompt)
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
    def _build_generation_attempts(
        *,
        generate_fn: Callable[..., Any],
        max_tokens: int,
        temperature: float,
    ) -> list[dict[str, Any]]:
        # Backward/forward compatibility across mlx_lm generate signatures.
        attempts: list[dict[str, Any]] = [
            {"max_tokens": max_tokens, "temp": temperature, "verbose": False},
            {"max_tokens": max_tokens, "temperature": temperature, "verbose": False},
            {"max_tokens": max_tokens, "verbose": False},
        ]
        stop_sequences = [
            "\nUSER:",
            "\nUser:",
            "\nuser:",
            "\n### User",
            "\nHuman:",
            "<|user|>",
            "<|im_start|>user",
        ]

        try:
            signature = inspect.signature(generate_fn)
            params = signature.parameters
            has_var_kwargs = any(
                parameter.kind == inspect.Parameter.VAR_KEYWORD
                for parameter in params.values()
            )
        except Exception:
            params = {}
            has_var_kwargs = True

        def supports(name: str) -> bool:
            return has_var_kwargs or name in params

        stop_key: str | None = None
        for candidate in ("stop", "stop_sequences", "stop_words"):
            if supports(candidate):
                stop_key = candidate
                break

        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for attempt in attempts:
            row = dict(attempt)
            if not has_var_kwargs:
                row = {key: value for key, value in row.items() if key in params}
            if stop_key is not None:
                row[stop_key] = stop_sequences
            marker = json.dumps(row, sort_keys=True, default=str)
            if marker in seen:
                continue
            seen.add(marker)
            normalized.append(row)
        return normalized or [{"max_tokens": max_tokens}]

    @staticmethod
    def _normalize_chat_messages(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        for item in messages:
            if not isinstance(item, dict):
                continue
            role_raw = str(item.get("role", "user")).strip().lower()
            role = role_raw if role_raw in {"system", "user", "assistant"} else "user"
            content = MLXProvider._content_to_text(item.get("content"))
            if not content:
                continue
            normalized.append({"role": role, "content": content})
        return normalized

    @staticmethod
    def _content_to_text(value: Any) -> str:
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, list):
            chunks: list[str] = []
            for item in value:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        chunks.append(text.strip())
            return "\n".join(chunks).strip()
        if value is None:
            return ""
        return str(value).strip()

    @staticmethod
    def _build_prompt(messages: list[dict[str, Any]], tokenizer: Any) -> str:
        normalized_messages = MLXProvider._normalize_chat_messages(messages)
        apply_chat_template = getattr(tokenizer, "apply_chat_template", None)
        if callable(apply_chat_template):
            kwargs: dict[str, Any] = {"tokenize": False}
            try:
                signature = inspect.signature(apply_chat_template)
                if "add_generation_prompt" in signature.parameters:
                    kwargs["add_generation_prompt"] = True
            except Exception:
                kwargs["add_generation_prompt"] = True
            try:
                rendered = apply_chat_template(normalized_messages, **kwargs)
                if isinstance(rendered, str) and rendered.strip():
                    return rendered
            except Exception:
                pass
        return MLXProvider._messages_to_prompt(normalized_messages)

    @staticmethod
    def _normalize_generation_output(raw_text: str, *, prompt: str) -> str:
        text = str(raw_text or "").strip()
        if not text:
            return ""

        prompt_trimmed = str(prompt or "").strip()
        if prompt_trimmed and text.startswith(prompt_trimmed):
            text = text[len(prompt_trimmed):].lstrip()

        # Remove common assistant prefixes.
        text = re.sub(r"^\s*(assistant|Assistant|ASSISTANT)\s*:\s*", "", text)
        text = re.sub(r"^\s*(ассистент|АССИСТЕНТ)\s*:\s*", "", text)
        for prefix in ("<|assistant|>", "<|im_start|>assistant"):
            if text.startswith(prefix):
                text = text[len(prefix):].lstrip()

        # Keep only the first assistant turn; drop synthetic conversation tails.
        line_marker = re.compile(
            r"(?im)^\s*(user|assistant|system|human|пользователь|ассистент|система)\s*:\s*"
        )
        for match in line_marker.finditer(text):
            if match.start() <= 1:
                continue
            text = text[: match.start()]
            break
        for marker in ("<|user|>", "<|im_start|>user", "<|start_header_id|>user<|end_header_id|>"):
            position = text.find(marker)
            if position > 0:
                text = text[:position]
                break

        return text.strip()

    @staticmethod
    def _label_from_model_id(model_id: str) -> str:
        name = model_id.split("/")[-1].replace("-", " ").replace("_", " ").strip()
        pretty = " ".join(segment for segment in name.split() if segment)
        return pretty or model_id

    @staticmethod
    def _emit_progress(
        progress_callback: Callable[[dict[str, Any]], None] | None,
        payload: dict[str, Any],
    ) -> None:
        if not callable(progress_callback):
            return
        try:
            progress_callback(payload)
        except Exception:
            return

    @staticmethod
    def _list_repo_files(api: Any, model_id: str) -> list[tuple[str, int]]:
        try:
            info = api.model_info(repo_id=model_id, files_metadata=True)
        except Exception:
            return []

        siblings = getattr(info, "siblings", None)
        if not isinstance(siblings, list):
            return []

        rows: list[tuple[str, int]] = []
        for sibling in siblings:
            filename = str(getattr(sibling, "rfilename", "")).strip()
            if not filename:
                continue
            size = _to_int(getattr(sibling, "size", None)) or 0
            rows.append((filename, max(0, size)))
        return rows

    @staticmethod
    def _estimate_download_size_bytes(model_id: str) -> int | None:
        estimated_params_b = estimate_model_size_b(model_id)
        if estimated_params_b is None:
            return None
        normalized = model_id.lower()
        bytes_per_param = 2.0
        if "8bit" in normalized or "q8" in normalized:
            bytes_per_param = 1.1
        elif "6bit" in normalized or "q6" in normalized:
            bytes_per_param = 0.82
        elif "5bit" in normalized or "q5" in normalized:
            bytes_per_param = 0.67
        elif "4bit" in normalized or "q4" in normalized:
            bytes_per_param = 0.56
        estimated = int(estimated_params_b * 1_000_000_000 * bytes_per_param)
        return estimated if estimated > 0 else None

    @staticmethod
    def _physical_memory_bytes() -> int | None:
        try:
            page_size = int(os.sysconf("SC_PAGE_SIZE"))
            page_count = int(os.sysconf("SC_PHYS_PAGES"))
        except Exception:
            return None
        total = page_size * page_count
        if total <= 0:
            return None
        return total

    def _guard_memory_budget(self, *, model_id: str) -> None:
        estimated_download_bytes = self._estimate_download_size_bytes(model_id)
        physical_memory_bytes = self._physical_memory_bytes()
        if estimated_download_bytes is None or physical_memory_bytes is None:
            return

        estimated_runtime_bytes = int(estimated_download_bytes * MLX_MEMORY_OVERHEAD_FACTOR)
        budget_bytes = int(float(physical_memory_bytes) * MLX_MEMORY_BUDGET_RATIO)
        if estimated_runtime_bytes <= budget_bytes:
            return

        required_gb = float(estimated_runtime_bytes) / float(1024**3)
        budget_gb = float(budget_bytes) / float(1024**3)
        raise RuntimeError(
            (
                f"Model '{model_id}' is too large for this Mac. "
                f"Estimated runtime memory ~{required_gb:.1f} GiB, safe budget ~{budget_gb:.1f} GiB. "
                f"Choose a smaller model (for example 1.5B/3B/7B 4-bit)."
            )
        )

    @staticmethod
    def _folder_size_bytes(path: Path) -> int:
        total = 0
        try:
            for item in path.rglob("*"):
                if item.is_file():
                    total += int(item.stat().st_size)
        except Exception:
            return 0
        return max(0, total)

    @staticmethod
    def _has_mlx_weights(path: Path) -> bool:
        try:
            for item in path.rglob("*.safetensors"):
                if item.is_file():
                    return True
        except Exception:
            return False
        return False

    @staticmethod
    def _messages_to_prompt(messages: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for message in messages:
            role = str(message.get("role", "user")).upper()
            content = str(message.get("content", ""))
            lines.append(f"{role}: {content}")
        lines.append("ASSISTANT:")
        return "\n".join(lines)


def _to_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None
