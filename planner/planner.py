from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PlanStep:
    id: int
    description: str
    depends_on: list[int] = field(default_factory=list)


class Planner:
    def create_plan(self, task: str, strategy: str) -> list[PlanStep]:
        normalized_task = task.lower()

        if strategy == "simple":
            return [PlanStep(id=1, description="Generate a direct response using the selected model.", depends_on=[])]

        if strategy == "tool":
            return [
                PlanStep(id=1, description="Identify if a tool call is required.", depends_on=[]),
                PlanStep(id=2, description="Execute the tool and capture output.", depends_on=[1]),
                PlanStep(id=3, description="Generate final answer based on tool result.", depends_on=[2]),
            ]

        if "summarize" in normalized_task and "http" in normalized_task:
            return [
                PlanStep(id=1, description="Fetch webpage content via tool.", depends_on=[]),
                PlanStep(id=2, description="Extract relevant text.", depends_on=[1]),
                PlanStep(id=3, description="Summarize key points.", depends_on=[2]),
            ]

        if strategy == "complex" and " and " in normalized_task:
            raw_parts = [item.strip(" .,\n\t") for item in task.split(" and ") if item.strip(" .,\n\t")]
            parts = raw_parts[:3]
            if len(parts) >= 2:
                steps: list[PlanStep] = []
                for index, part in enumerate(parts, start=1):
                    steps.append(
                        PlanStep(
                            id=index,
                            description=f"Resolve subtask: {part}",
                            depends_on=[],
                        )
                    )
                merge_depends = [step.id for step in steps]
                steps.append(
                    PlanStep(
                        id=len(steps) + 1,
                        description="Merge all subtask results into one final plan context.",
                        depends_on=merge_depends,
                    )
                )
                return steps

        return [
            PlanStep(id=1, description="Break task into manageable steps.", depends_on=[]),
            PlanStep(id=2, description="Execute reasoning and optional tools.", depends_on=[1]),
            PlanStep(id=3, description="Verify and return concise output.", depends_on=[2]),
        ]
