from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PlanStep:
    id: int
    description: str


class Planner:
    def create_plan(self, task: str, strategy: str) -> list[PlanStep]:
        if strategy == "simple":
            return [PlanStep(id=1, description="Generate a direct response using the selected model.")]

        if strategy == "tool":
            return [
                PlanStep(id=1, description="Identify if a tool call is required."),
                PlanStep(id=2, description="Execute the tool and capture output."),
                PlanStep(id=3, description="Generate final answer based on tool result."),
            ]

        if "summarize" in task.lower() and "http" in task.lower():
            return [
                PlanStep(id=1, description="Fetch webpage content via tool."),
                PlanStep(id=2, description="Extract relevant text."),
                PlanStep(id=3, description="Summarize key points."),
            ]

        return [
            PlanStep(id=1, description="Break task into manageable steps."),
            PlanStep(id=2, description="Execute reasoning and optional tools."),
            PlanStep(id=3, description="Verify and return concise output."),
        ]
