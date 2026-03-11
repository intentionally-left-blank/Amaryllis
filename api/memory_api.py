from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from memory.models import ExtractionResult, MemoryContext

router = APIRouter(tags=["memory"])


class MemoryContextResponse(BaseModel):
    request_id: str
    user_id: str
    agent_id: str | None = None
    session_id: str | None = None
    query: str
    context: MemoryContext


class RetrievalDebugItem(BaseModel):
    rank: int = Field(ge=1)
    semantic_id: int | None = None
    kind: str
    text: str
    score: float
    vector_score: float
    recency_score: float
    confidence: float
    importance: float
    created_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalDebugResponse(BaseModel):
    request_id: str
    user_id: str
    query: str
    top_k: int
    items: list[RetrievalDebugItem]


class MemoryExtractionRecord(BaseModel):
    user_id: str
    agent_id: str | None = None
    session_id: str | None = None
    source_role: str
    source_text: str
    extracted_json: ExtractionResult
    created_at: str


class MemoryExtractionsResponse(BaseModel):
    request_id: str
    user_id: str
    count: int
    items: list[MemoryExtractionRecord]


class MemoryConflictRecord(BaseModel):
    layer: str
    key: str
    previous_value: str | None = None
    incoming_value: str | None = None
    resolution: str
    confidence_prev: float | None = None
    confidence_new: float | None = None
    created_at: str


class MemoryConflictsResponse(BaseModel):
    request_id: str
    user_id: str
    count: int
    items: list[MemoryConflictRecord]


@router.get("/debug/memory/context", response_model=MemoryContextResponse)
def debug_memory_context(
    request: Request,
    user_id: str = Query(..., min_length=1),
    query: str = Query("", min_length=0),
    agent_id: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    working_limit: int = Query(default=12, ge=0, le=64),
    episodic_limit: int = Query(default=16, ge=0, le=128),
    semantic_top_k: int = Query(default=8, ge=0, le=64),
) -> MemoryContextResponse:
    services = request.app.state.services
    context = services.memory_manager.build_context(
        user_id=user_id,
        agent_id=agent_id,
        query=query,
        session_id=session_id,
        working_limit=working_limit,
        episodic_limit=episodic_limit,
        semantic_top_k=semantic_top_k,
    )
    request_id = str(getattr(request.state, "request_id", ""))
    return MemoryContextResponse(
        request_id=request_id,
        user_id=user_id,
        agent_id=agent_id,
        session_id=session_id,
        query=query,
        context=context,
    )


@router.get("/debug/memory/retrieval", response_model=RetrievalDebugResponse)
def debug_memory_retrieval(
    request: Request,
    user_id: str = Query(..., min_length=1),
    query: str = Query(..., min_length=1),
    top_k: int = Query(default=8, ge=1, le=64),
) -> RetrievalDebugResponse:
    services = request.app.state.services
    items = services.memory_manager.debug_retrieval(
        user_id=user_id,
        query=query,
        top_k=top_k,
    )
    request_id = str(getattr(request.state, "request_id", ""))
    typed_items = [RetrievalDebugItem(**item) for item in items]
    return RetrievalDebugResponse(
        request_id=request_id,
        user_id=user_id,
        query=query,
        top_k=top_k,
        items=typed_items,
    )


@router.get("/debug/memory/extractions", response_model=MemoryExtractionsResponse)
def debug_memory_extractions(
    request: Request,
    user_id: str = Query(..., min_length=1),
    limit: int = Query(default=50, ge=1, le=200),
) -> MemoryExtractionsResponse:
    services = request.app.state.services
    request_id = str(getattr(request.state, "request_id", ""))
    items = services.memory_manager.list_extractions(user_id=user_id, limit=limit)
    typed_items = [MemoryExtractionRecord(**item) for item in items]
    return MemoryExtractionsResponse(
        request_id=request_id,
        user_id=user_id,
        count=len(typed_items),
        items=typed_items,
    )


@router.get("/debug/memory/conflicts", response_model=MemoryConflictsResponse)
def debug_memory_conflicts(
    request: Request,
    user_id: str = Query(..., min_length=1),
    limit: int = Query(default=50, ge=1, le=200),
) -> MemoryConflictsResponse:
    services = request.app.state.services
    request_id = str(getattr(request.state, "request_id", ""))
    items = services.memory_manager.list_conflicts(user_id=user_id, limit=limit)
    typed_items = [MemoryConflictRecord(**item) for item in items]
    return MemoryConflictsResponse(
        request_id=request_id,
        user_id=user_id,
        count=len(typed_items),
        items=typed_items,
    )
