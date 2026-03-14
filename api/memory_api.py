from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from memory.eval_suite import MemoryQualityEvaluator
from memory.models import ExtractionResult, MemoryContext
from runtime.auth import auth_context_from_request, resolve_user_id

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


class MemoryConsolidateRequest(BaseModel):
    user_id: str = Field(min_length=1)
    session_id: str | None = None
    semantic_limit: int = Field(default=1000, ge=10, le=5000)


class MemoryConsolidateResponse(BaseModel):
    request_id: str
    summary: dict[str, Any]


class ProfileDecayDebugItem(BaseModel):
    key: str
    value: str
    source: str | None = None
    updated_at: str
    confidence_raw: float
    confidence_effective: float
    confidence_decay_factor: float
    age_days: float
    decayed: bool


class ProfileDecayDebugResponse(BaseModel):
    request_id: str
    user_id: str
    count: int
    items: list[ProfileDecayDebugItem]


class MemoryEvalRequest(BaseModel):
    suite: str = Field(default="core", min_length=1)


class MemoryEvalCaseResult(BaseModel):
    id: str
    description: str
    passed: bool
    score: float
    details: dict[str, Any] = Field(default_factory=dict)


class MemoryEvalResponse(BaseModel):
    request_id: str
    suite: str
    total_cases: int
    passed_cases: int
    failed_cases: int
    pass_rate: float
    average_score: float
    cases: list[MemoryEvalCaseResult]


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
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=user_id, auth=auth)
    context = services.memory_manager.build_context(
        user_id=effective_user_id,
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
        user_id=effective_user_id,
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
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=user_id, auth=auth)
    items = services.memory_manager.debug_retrieval(
        user_id=effective_user_id,
        query=query,
        top_k=top_k,
    )
    request_id = str(getattr(request.state, "request_id", ""))
    typed_items = [RetrievalDebugItem(**item) for item in items]
    return RetrievalDebugResponse(
        request_id=request_id,
        user_id=effective_user_id,
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
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=user_id, auth=auth)
    request_id = str(getattr(request.state, "request_id", ""))
    items = services.memory_manager.list_extractions(user_id=effective_user_id, limit=limit)
    typed_items = [MemoryExtractionRecord(**item) for item in items]
    return MemoryExtractionsResponse(
        request_id=request_id,
        user_id=effective_user_id,
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
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=user_id, auth=auth)
    request_id = str(getattr(request.state, "request_id", ""))
    items = services.memory_manager.list_conflicts(user_id=effective_user_id, limit=limit)
    typed_items = [MemoryConflictRecord(**item) for item in items]
    return MemoryConflictsResponse(
        request_id=request_id,
        user_id=effective_user_id,
        count=len(typed_items),
        items=typed_items,
    )


@router.get("/debug/memory/profile-decay", response_model=ProfileDecayDebugResponse)
def debug_memory_profile_decay(
    request: Request,
    user_id: str = Query(..., min_length=1),
    limit: int = Query(default=100, ge=1, le=500),
) -> ProfileDecayDebugResponse:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=user_id, auth=auth)
    request_id = str(getattr(request.state, "request_id", ""))
    items = services.memory_manager.debug_profile_decay(user_id=effective_user_id, limit=limit)
    typed_items = [ProfileDecayDebugItem(**item) for item in items]
    return ProfileDecayDebugResponse(
        request_id=request_id,
        user_id=effective_user_id,
        count=len(typed_items),
        items=typed_items,
    )


@router.post("/debug/memory/consolidate", response_model=MemoryConsolidateResponse)
def debug_memory_consolidate(
    payload: MemoryConsolidateRequest,
    request: Request,
) -> MemoryConsolidateResponse:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    effective_user_id = resolve_user_id(request_user_id=payload.user_id, auth=auth)
    request_id = str(getattr(request.state, "request_id", ""))
    summary = services.memory_manager.consolidate_user_memory(
        user_id=effective_user_id,
        session_id=payload.session_id,
        semantic_limit=payload.semantic_limit,
    )
    return MemoryConsolidateResponse(
        request_id=request_id,
        summary=summary,
    )


@router.post("/debug/memory/eval", response_model=MemoryEvalResponse)
def debug_memory_eval(
    payload: MemoryEvalRequest,
    request: Request,
) -> MemoryEvalResponse:
    services = request.app.state.services
    request_id = str(getattr(request.state, "request_id", ""))
    evaluator = MemoryQualityEvaluator(
        profile_decay_enabled=services.config.memory_profile_decay_enabled,
        profile_decay_half_life_days=services.config.memory_profile_decay_half_life_days,
        profile_decay_floor=services.config.memory_profile_decay_floor,
        profile_decay_min_delta=services.config.memory_profile_decay_min_delta,
    )
    summary = evaluator.run(suite=payload.suite)
    typed_cases = [MemoryEvalCaseResult(**item) for item in summary.get("cases", [])]
    return MemoryEvalResponse(
        request_id=request_id,
        suite=str(summary.get("suite", payload.suite)),
        total_cases=int(summary.get("total_cases", len(typed_cases))),
        passed_cases=int(summary.get("passed_cases", 0)),
        failed_cases=int(summary.get("failed_cases", 0)),
        pass_rate=float(summary.get("pass_rate", 0.0)),
        average_score=float(summary.get("average_score", 0.0)),
        cases=typed_cases,
    )
