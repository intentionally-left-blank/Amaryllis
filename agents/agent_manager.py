from __future__ import annotations

from typing import Any

from agents.agent import Agent
from agents.agent_run_manager import AgentRunManager
from kernel.contracts import ExecutorContract
from storage.database import Database


class AgentManager:
    def __init__(
        self,
        database: Database,
        task_executor: ExecutorContract,
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
        self._assert_agent_owner(agent=agent, user_id=user_id)

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
        budget: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self.run_manager is None:
            raise ValueError("Run manager is not configured")

        agent = self.get_agent(agent_id)
        if agent is None:
            raise ValueError(f"Agent not found: {agent_id}")
        self._assert_agent_owner(agent=agent, user_id=user_id)

        return self.run_manager.create_run(
            agent=agent,
            user_id=user_id,
            session_id=session_id,
            user_message=user_message,
            max_attempts=max_attempts,
            budget=budget,
        )

    def simulate_run(
        self,
        *,
        agent_id: str,
        user_message: str,
        user_id: str,
        session_id: str | None,
        max_attempts: int | None = None,
        budget: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        agent = self.get_agent(agent_id)
        if agent is None:
            raise ValueError(f"Agent not found: {agent_id}")
        self._assert_agent_owner(agent=agent, user_id=user_id)

        simulator = getattr(self.task_executor, "simulate_run", None)
        if simulator is None or not callable(simulator):
            raise ValueError("Task executor does not support simulation mode")

        simulation = simulator(
            agent=agent,
            user_id=user_id,
            session_id=session_id,
            user_message=user_message,
            requested_budget=budget,
            max_attempts=max_attempts,
        )
        if not isinstance(simulation, dict):
            raise ValueError("Simulation mode returned invalid payload")

        attempts_limit = 1
        effective_budget = budget if isinstance(budget, dict) else {}
        if self.run_manager is not None:
            attempts_limit = max(1, int(max_attempts or self.run_manager.default_max_attempts))
            effective_budget = self.run_manager._normalize_run_budget(budget)
        elif max_attempts is not None:
            attempts_limit = max(1, int(max_attempts))

        simulation["run_preview"] = {
            "max_attempts": attempts_limit,
            "budget": effective_budget,
        }
        simulation["apply_hint"] = {
            "endpoint": f"/agents/{agent_id}/runs",
            "payload": {
                "user_id": user_id,
                "session_id": session_id,
                "message": user_message,
                "max_attempts": attempts_limit,
                "budget": effective_budget,
            },
        }
        return simulation

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

    def kill_switch_runs(
        self,
        *,
        actor: str | None = None,
        reason: str | None = None,
        include_running: bool = True,
        include_queued: bool = True,
        limit: int = 5000,
    ) -> dict[str, Any]:
        if self.run_manager is None:
            raise ValueError("Run manager is not configured")
        return self.run_manager.kill_switch_runs(
            actor=actor,
            reason=reason,
            include_running=include_running,
            include_queued=include_queued,
            limit=limit,
        )

    def replay_run(self, run_id: str) -> dict[str, Any]:
        if self.run_manager is None:
            raise ValueError("Run manager is not configured")
        return self.run_manager.replay_run(run_id)

    def replay_run_filtered(
        self,
        run_id: str,
        *,
        preset: str | None = None,
        stages: list[str] | None = None,
        statuses: list[str] | None = None,
        failure_classes: list[str] | None = None,
        retryable: bool | None = None,
        attempt: int | None = None,
        timeline_limit: int | None = None,
    ) -> dict[str, Any]:
        if self.run_manager is None:
            raise ValueError("Run manager is not configured")
        return self.run_manager.replay_run_filtered(
            run_id,
            preset=preset,
            stages=stages,
            statuses=statuses,
            failure_classes=failure_classes,
            retryable=retryable,
            attempt=attempt,
            timeline_limit=timeline_limit,
        )

    def diagnose_run(self, run_id: str) -> dict[str, Any]:
        if self.run_manager is None:
            raise ValueError("Run manager is not configured")
        return self.run_manager.diagnose_run(run_id)

    def build_run_diagnostics_package(self, run_id: str) -> dict[str, Any]:
        if self.run_manager is None:
            raise ValueError("Run manager is not configured")
        return self.run_manager.build_run_diagnostics_package(run_id)

    def build_run_audit_timeline(
        self,
        run_id: str,
        *,
        include_tool_calls: bool = True,
        include_security_actions: bool = True,
        limit: int = 2000,
    ) -> dict[str, Any]:
        if self.run_manager is None:
            raise ValueError("Run manager is not configured")
        return self.run_manager.build_run_audit_timeline(
            run_id,
            include_tool_calls=include_tool_calls,
            include_security_actions=include_security_actions,
            limit=limit,
        )

    def export_run_audit_timeline(
        self,
        run_id: str,
        *,
        export_format: str = "json",
        include_tool_calls: bool = True,
        include_security_actions: bool = True,
        limit: int = 2000,
    ) -> dict[str, Any]:
        if self.run_manager is None:
            raise ValueError("Run manager is not configured")
        return self.run_manager.export_run_audit_timeline(
            run_id,
            export_format=export_format,
            include_tool_calls=include_tool_calls,
            include_security_actions=include_security_actions,
            limit=limit,
        )

    def list_run_issues(self, run_id: str, limit: int = 200) -> list[dict[str, Any]]:
        if self.run_manager is None:
            raise ValueError("Run manager is not configured")
        run = self.run_manager.get_run(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        return self.run_manager.list_run_issues(run_id=run_id, limit=limit)

    def list_run_artifacts(
        self,
        run_id: str,
        *,
        issue_id: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        if self.run_manager is None:
            raise ValueError("Run manager is not configured")
        run = self.run_manager.get_run(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        return self.run_manager.list_run_artifacts(
            run_id=run_id,
            issue_id=issue_id,
            limit=limit,
        )

    def run_health(
        self,
        *,
        user_id: str | None = None,
        agent_id: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        if self.run_manager is None:
            raise ValueError("Run manager is not configured")
        return self.run_manager.get_run_health(
            user_id=user_id,
            agent_id=agent_id,
            limit=limit,
        )

    @staticmethod
    def _assert_agent_owner(*, agent: Agent, user_id: str) -> None:
        owner = str(agent.user_id or "").strip()
        actor = str(user_id or "").strip()
        if not owner or not actor or owner != actor:
            raise ValueError(f"Agent ownership mismatch for agent: {agent.id}")
