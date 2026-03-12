from __future__ import annotations

from typing import Any

from agents.agent import Agent
from agents.agent_run_manager import AgentRunManager
from storage.database import Database
from tasks.task_executor import TaskExecutor


class AgentManager:
    def __init__(
        self,
        database: Database,
        task_executor: TaskExecutor,
        run_manager: AgentRunManager | None = None,
    ) -> None:
        self.database = database
        self.task_executor = task_executor
        self.run_manager = run_manager

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

    def create_run(
        self,
        agent_id: str,
        user_message: str,
        user_id: str,
        session_id: str | None,
        max_attempts: int | None = None,
    ) -> dict[str, Any]:
        if self.run_manager is None:
            raise ValueError("Run manager is not configured")

        agent = self.get_agent(agent_id)
        if agent is None:
            raise ValueError(f"Agent not found: {agent_id}")

        return self.run_manager.create_run(
            agent=agent,
            user_id=user_id,
            session_id=session_id,
            user_message=user_message,
            max_attempts=max_attempts,
        )

    def get_run(self, run_id: str) -> dict[str, Any]:
        if self.run_manager is None:
            raise ValueError("Run manager is not configured")
        run = self.run_manager.get_run(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        return run

    def list_runs(
        self,
        user_id: str | None = None,
        agent_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if self.run_manager is None:
            raise ValueError("Run manager is not configured")
        return self.run_manager.list_runs(
            user_id=user_id,
            agent_id=agent_id,
            status=status,
            limit=limit,
        )

    def cancel_run(self, run_id: str) -> dict[str, Any]:
        if self.run_manager is None:
            raise ValueError("Run manager is not configured")
        return self.run_manager.cancel_run(run_id)

    def resume_run(self, run_id: str) -> dict[str, Any]:
        if self.run_manager is None:
            raise ValueError("Run manager is not configured")
        return self.run_manager.resume_run(run_id)

    def replay_run(self, run_id: str) -> dict[str, Any]:
        if self.run_manager is None:
            raise ValueError("Run manager is not configured")
        return self.run_manager.replay_run(run_id)
