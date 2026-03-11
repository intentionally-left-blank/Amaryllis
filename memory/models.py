from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class WorkingMemoryItem(BaseModel):
    key: str
    value: str
    session_id: str
    kind: str = "note"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    updated_at: str


class EpisodicMemoryItem(BaseModel):
    role: str
    content: str
    created_at: str
    session_id: str | None = None
    kind: str = "interaction"
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    fingerprint: str | None = None


class SemanticMemoryItem(BaseModel):
    text: str
    score: float
    vector_score: float | None = None
    recency_score: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    kind: str = "fact"
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)


class ProfileMemoryItem(BaseModel):
    key: str
    value: str
    updated_at: str
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    importance: float = Field(default=0.7, ge=0.0, le=1.0)
    source: str | None = None


class ExtractionCandidate(BaseModel):
    kind: str
    text: str
    key: str | None = None
    value: str | None = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class ExtractionResult(BaseModel):
    facts: list[ExtractionCandidate] = Field(default_factory=list)
    preferences: list[ExtractionCandidate] = Field(default_factory=list)
    tasks: list[ExtractionCandidate] = Field(default_factory=list)


class MemoryContext(BaseModel):
    working: list[WorkingMemoryItem] = Field(default_factory=list)
    episodic: list[EpisodicMemoryItem] = Field(default_factory=list)
    semantic: list[SemanticMemoryItem] = Field(default_factory=list)
    profile: list[ProfileMemoryItem] = Field(default_factory=list)
