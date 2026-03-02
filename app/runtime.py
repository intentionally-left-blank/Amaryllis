from __future__ import annotations

import logging
import time
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from app.context import build_context
from app.errors import ModuleExecutionError, ModuleLoadError, ValidationError
from app.loader import ModuleLoader, ModuleLoaderError
from app.models import ExecuteRequest, ExecuteResponse, ModuleExecutionResult

session_store: dict[str, dict[str, Any]] = {}


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
            raw_result = loaded_module.run(context.model_dump())
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
