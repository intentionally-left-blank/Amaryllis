from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field

router = APIRouter(tags=["agents"])


class CreateAgentRequest(BaseModel):
    name: str = Field(min_length=1)
    system_prompt: str = Field(min_length=1)
    model: str | None = None
    tools: list[str] = Field(default_factory=list)
    user_id: str | None = None


class AgentChatRequest(BaseModel):
    user_id: str = Field(min_length=1)
    message: str = Field(min_length=1)
    session_id: str | None = None


@router.post("/agents/create")
def create_agent(payload: CreateAgentRequest, request: Request) -> dict[str, Any]:
    services = request.app.state.services
    try:
        agent = services.agent_manager.create_agent(
            name=payload.name,
            system_prompt=payload.system_prompt,
            model=payload.model,
            tools=payload.tools,
            user_id=payload.user_id,
        )
        return agent.to_record()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/agents")
def list_agents(request: Request, user_id: str | None = Query(default=None)) -> dict[str, Any]:
    services = request.app.state.services
    agents = services.agent_manager.list_agents(user_id=user_id)
    return {
        "items": [agent.to_record() for agent in agents],
        "count": len(agents),
    }


@router.post("/agents/{agent_id}/chat")
def chat_agent(
    payload: AgentChatRequest,
    request: Request,
    agent_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    try:
        return services.agent_manager.chat(
            agent_id=agent_id,
            user_message=payload.message,
            user_id=payload.user_id,
            session_id=payload.session_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
