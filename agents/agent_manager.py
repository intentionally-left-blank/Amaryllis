from __future__ import annotations

from typing import Any

from agents.agent import Agent
from storage.database import Database
from tasks.task_executor import TaskExecutor


class AgentManager:
    def __init__(self, database: Database, task_executor: TaskExecutor) -> None:
        self.database = database
        self.task_executor = task_executor

    def create_agent(
        self,
        name: str,
        system_prompt: str,
        model: str | None,
        tools: list[str] | None,
        user_id: str | None,
    ) -> Agent:
        agent = Agent.create(
            name=name,
            system_prompt=system_prompt,
            model=model,
            tools=tools,
            user_id=user_id,
        )
        self.database.upsert_agent(agent.to_record())
        return agent

    def list_agents(self, user_id: str | None = None) -> list[Agent]:
        return [Agent.from_record(item) for item in self.database.list_agents(user_id=user_id)]

    def get_agent(self, agent_id: str) -> Agent | None:
        record = self.database.get_agent(agent_id)
        if record is None:
            return None
        return Agent.from_record(record)

    def chat(
        self,
        agent_id: str,
        user_message: str,
        user_id: str,
        session_id: str | None,
    ) -> dict[str, Any]:
        agent = self.get_agent(agent_id)
        if agent is None:
            raise ValueError(f"Agent not found: {agent_id}")

        return self.task_executor.execute(
            agent=agent,
            user_id=user_id,
            session_id=session_id,
            user_message=user_message,
        )
