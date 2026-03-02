from __future__ import annotations

import json
import logging
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from app.context import build_context
from app.errors import ModuleExecutionError, ModuleLoadError, ValidationError
from app.loader import ModuleLoader, ModuleLoaderError
from app.models import ExecuteRequest, ExecuteResponse, ModuleExecutionResult

session_store: dict[str, dict[str, Any]] = {}
DEFAULT_TIMEOUT_SECONDS = 10.0


class RuntimeService:
    def __init__(self, loader: ModuleLoader | None = None) -> None:
        self.loader = loader or ModuleLoader()
        self.logger = logging.getLogger("amaryllis.runtime")
        self.session_store = session_store

    def execute(self, request: ExecuteRequest, request_id: str) -> ExecuteResponse:
        started_at = time.perf_counter()

        self.logger.info(
            "execution_started request_id=%s module=%s",
            request_id,
            request.module,
        )

        memory = self._load_session_memory(request.session_id)

        try:
            context = build_context(
                request_id=request_id,
                user_id=request.user_id,
                session_id=request.session_id,
                input_data=request.input,
                memory=memory,
                metadata={"module": request.module},
            )
        except PydanticValidationError as exc:
            execution_time_ms = self._elapsed_ms(started_at)
            self.logger.error(
                "execution_failed request_id=%s module=%s execution_time_ms=%d",
                request_id,
                request.module,
                execution_time_ms,
                exc_info=True,
            )
            raise ValidationError(f"Context validation failed: {exc}", request_id=request_id) from exc

        try:
            loaded_module = self.loader.load(request.module)
        except ModuleLoaderError as exc:
            execution_time_ms = self._elapsed_ms(started_at)
            self.logger.error(
                "execution_failed request_id=%s module=%s execution_time_ms=%d",
                request_id,
                request.module,
                execution_time_ms,
                exc_info=True,
            )
            raise ModuleLoadError(str(exc), request_id=request_id) from exc

        try:
            raw_result = self._execute_module_subprocess(
                module_name=request.module,
                request_id=request_id,
                entrypoint_path=loaded_module.entrypoint_path,
                context=context.model_dump(),
                timeout_ms=loaded_module.manifest.resources.timeout_ms,
            )
            result = ModuleExecutionResult.model_validate(raw_result)
        except PydanticValidationError as exc:
            execution_time_ms = self._elapsed_ms(started_at)
            self.logger.error(
                "execution_failed request_id=%s module=%s execution_time_ms=%d",
                request_id,
                request.module,
                execution_time_ms,
                exc_info=True,
            )
            raise ValidationError(f"Module output validation failed: {exc}", request_id=request_id) from exc
        except ModuleExecutionError:
            raise
        except Exception as exc:
            execution_time_ms = self._elapsed_ms(started_at)
            self.logger.error(
                "execution_failed request_id=%s module=%s execution_time_ms=%d",
                request_id,
                request.module,
                execution_time_ms,
                exc_info=True,
            )
            raise ModuleExecutionError(f"Module execution failed: {exc}", request_id=request_id) from exc

        self._persist_session_memory(request.session_id, memory, result.memory_write)

        execution_time_ms = self._elapsed_ms(started_at)
        self.logger.info(
            "execution_finished request_id=%s module=%s execution_time_ms=%d",
            request_id,
            request.module,
            execution_time_ms,
        )

        return ExecuteResponse(
            request_id=request_id,
            module=request.module,
            output=result.output,
            memory_write=result.memory_write,
            execution_time_ms=execution_time_ms,
        )

    @staticmethod
    def _elapsed_ms(started_at: float) -> int:
        return int((time.perf_counter() - started_at) * 1000)

    def _load_session_memory(self, session_id: str | None) -> dict[str, Any]:
        if session_id is None:
            return {}

        return dict(self.session_store.get(session_id, {}))

    def _persist_session_memory(
        self,
        session_id: str | None,
        current_memory: dict[str, Any],
        memory_write: dict[str, Any],
    ) -> None:
        if session_id is None:
            return

        merged_memory = dict(current_memory)
        merged_memory.update(memory_write)
        self.session_store[session_id] = merged_memory

    def _execute_module_subprocess(
        self,
        module_name: str,
        request_id: str,
        entrypoint_path: Path,
        context: dict[str, Any],
        timeout_ms: int,
    ) -> dict[str, Any]:
        timeout_seconds = self._resolve_timeout_seconds(timeout_ms)

        try:
            completed = subprocess.run(
                [sys.executable, str(entrypoint_path)],
                input=json.dumps(context),
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            self.logger.error(
                "subprocess_timeout request_id=%s module=%s timeout_seconds=%.3f",
                request_id,
                module_name,
                timeout_seconds,
            )
            if exc.stderr:
                self.logger.error(
                    "subprocess_stderr request_id=%s module=%s stderr=%s",
                    request_id,
                    module_name,
                    exc.stderr.strip(),
                )
            raise ModuleExecutionError(
                f"Module execution timed out after {timeout_seconds:.3f} seconds.",
                request_id=request_id,
            ) from exc
        except Exception as exc:
            self.logger.error(
                "subprocess_start_failed request_id=%s module=%s",
                request_id,
                module_name,
                exc_info=True,
            )
            raise ModuleExecutionError(
                f"Failed to start module subprocess: {exc}",
                request_id=request_id,
            ) from exc

        stderr = (completed.stderr or "").strip()
        if stderr:
            self.logger.error(
                "subprocess_stderr request_id=%s module=%s stderr=%s",
                request_id,
                module_name,
                stderr,
            )

        if completed.returncode != 0:
            raise ModuleExecutionError(
                f"Module subprocess exited with code {completed.returncode}.",
                request_id=request_id,
            )

        stdout = (completed.stdout or "").strip()
        if not stdout:
            raise ModuleExecutionError(
                "Module subprocess returned empty stdout.",
                request_id=request_id,
            )

        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise ModuleExecutionError(
                "Module subprocess returned non-JSON stdout.",
                request_id=request_id,
            ) from exc

        if not isinstance(parsed, dict):
            raise ModuleExecutionError(
                "Module subprocess output must be a JSON object.",
                request_id=request_id,
            )

        return parsed

    @staticmethod
    def _resolve_timeout_seconds(timeout_ms: int | None) -> float:
        if timeout_ms is None or timeout_ms <= 0:
            return DEFAULT_TIMEOUT_SECONDS
        return timeout_ms / 1000.0
