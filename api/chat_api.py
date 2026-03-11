from __future__ import annotations

import json
import time
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from runtime.errors import ProviderError, ValidationError

router = APIRouter(tags=["chat"])


class ChatMessage(BaseModel):
    role: str
    content: str | None = None
    name: str | None = None


class ToolFunction(BaseModel):
    name: str
    description: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)


class ToolDefinition(BaseModel):
    type: str = "function"
    function: ToolFunction


class ChatCompletionsRequest(BaseModel):
    model: str | None = None
    provider: str | None = None
    messages: list[ChatMessage]
    stream: bool = False
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=512, ge=1, le=8192)
    tools: list[ToolDefinition] | None = None


def _normalize_messages(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for message in messages:
        normalized.append(message.model_dump(exclude_none=True))
    return normalized


def _tool_names_from_request(payload: ChatCompletionsRequest, request: Request) -> list[str]:
    if not payload.tools:
        return []

    services = request.app.state.services
    names: list[str] = []
    for tool in payload.tools:
        name = tool.function.name
        if services.tool_registry.get(name) is not None:
            names.append(name)
    return names


def _chat_once(
    request: Request,
    payload: ChatCompletionsRequest,
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    services = request.app.state.services
    return services.model_manager.chat(
        messages=messages,
        model=payload.model,
        provider=payload.provider,
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
    )


def _chat_with_tool_loop(
    request: Request,
    payload: ChatCompletionsRequest,
    messages: list[dict[str, Any]],
    tool_names: list[str],
) -> tuple[str, str, str, list[dict[str, Any]]]:
    services = request.app.state.services
    reasoning_messages = list(messages)

    if tool_names:
        reasoning_messages.append(
            {
                "role": "system",
                "content": services.tool_executor.render_tool_instruction(tool_names),
            }
        )

    first = _chat_once(request=request, payload=payload, messages=reasoning_messages)
    response_text = str(first.get("content", "")).strip()
    provider_used = str(first.get("provider", payload.provider or "unknown"))
    model_used = str(first.get("model", payload.model or "unknown"))
    tool_events: list[dict[str, Any]] = []

    if not tool_names:
        return response_text, provider_used, model_used, tool_events

    for _ in range(2):
        parsed = services.tool_executor.parse_tool_call(response_text)
        if not parsed:
            break

        tool_name = str(parsed["name"])
        if tool_name not in tool_names:
            tool_events.append(
                {
                    "tool": tool_name,
                    "error": "Tool is not allowed",
                }
            )
            break

        try:
            tool_result = services.tool_executor.execute(tool_name, parsed["arguments"])
        except Exception as exc:
            tool_result = {
                "tool": tool_name,
                "error": str(exc),
            }

        tool_events.append(tool_result)

        reasoning_messages.append({"role": "assistant", "content": response_text})
        reasoning_messages.append(
            {
                "role": "tool",
                "name": tool_name,
                "content": json.dumps(tool_result, ensure_ascii=False),
            }
        )
        reasoning_messages.append(
            {
                "role": "system",
                "content": "Use tool output and provide final answer for the user.",
            }
        )

        followup = _chat_once(request=request, payload=payload, messages=reasoning_messages)
        response_text = str(followup.get("content", "")).strip()
        provider_used = str(followup.get("provider", provider_used))
        model_used = str(followup.get("model", model_used))

    return response_text, provider_used, model_used, tool_events


@router.post("/v1/chat/completions")
def chat_completions(payload: ChatCompletionsRequest, request: Request):
    if not payload.messages:
        raise ValidationError("messages must not be empty")

    services = request.app.state.services
    normalized_messages = _normalize_messages(payload.messages)
    tool_names = _tool_names_from_request(payload=payload, request=request)
    request_id = str(getattr(request.state, "request_id", ""))

    if payload.stream:
        stream_messages = list(normalized_messages)
        if tool_names:
            stream_messages.append(
                {
                    "role": "system",
                    "content": services.tool_executor.render_tool_instruction(tool_names),
                }
            )

        try:
            iterator, provider_used, model_used = services.model_manager.stream_chat(
                messages=stream_messages,
                model=payload.model,
                provider=payload.provider,
                temperature=payload.temperature,
                max_tokens=payload.max_tokens,
            )
        except Exception as exc:
            raise ProviderError(str(exc)) from exc

        completion_id = f"chatcmpl-{uuid4().hex}"
        created = int(time.time())

        def event_stream():
            first_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model_used,
                "provider": provider_used,
                "request_id": request_id,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"role": "assistant"},
                        "finish_reason": None,
                    }
                ],
            }
            yield f"data: {json.dumps(first_chunk, ensure_ascii=False)}\n\n"

            stream_error = False
            try:
                for chunk in iterator:
                    payload_chunk = {
                        "id": completion_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model_used,
                        "provider": provider_used,
                        "request_id": request_id,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": chunk},
                                "finish_reason": None,
                            }
                        ],
                    }
                    yield f"data: {json.dumps(payload_chunk, ensure_ascii=False)}\n\n"
            except Exception as exc:
                stream_error = True
                error_chunk = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model_used,
                    "provider": provider_used,
                    "request_id": request_id,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": f"Error: {exc}"},
                            "finish_reason": "error",
                        }
                    ],
                }
                yield f"data: {json.dumps(error_chunk, ensure_ascii=False)}\n\n"

            done_chunk = {
                "id": completion_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model_used,
                "provider": provider_used,
                "request_id": request_id,
                "choices": [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "error" if stream_error else "stop",
                    }
                ],
            }
            yield f"data: {json.dumps(done_chunk, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    try:
        content, provider_used, model_used, tool_events = _chat_with_tool_loop(
            request=request,
            payload=payload,
            messages=normalized_messages,
            tool_names=tool_names,
        )
    except Exception as exc:
        raise ProviderError(str(exc)) from exc

    completion_id = f"chatcmpl-{uuid4().hex}"
    created = int(time.time())

    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created,
        "model": model_used,
        "provider": provider_used,
        "request_id": request_id,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
        "tool_events": tool_events,
    }
