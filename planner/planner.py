from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any


@dataclass(frozen=True)
class PlanStep:
    id: int
    description: str
    depends_on: list[int] = field(default_factory=list)
    kind: str = "general"
    requires_tools: bool = False
    objective: str = ""
    expected_output: str = ""
    hints: dict[str, Any] = field(default_factory=dict)


class Planner:
    _URL_RE = re.compile(r"https?://[^\s)\]>]+", re.IGNORECASE)

    def create_plan(self, task: str, strategy: str) -> list[PlanStep]:
        normalized_task = " ".join(str(task or "").split()).strip()
        lowered = normalized_task.lower()
        urls = self._extract_urls(normalized_task)

        if strategy == "simple":
            return [
                self._step(
                    1,
                    "Generate a direct, high-signal answer for the request.",
                    kind="answer_direct",
                    requires_tools=False,
                    objective="Answer user request directly.",
                    expected_output="One concise final answer.",
                    hints={"task": normalized_task},
                )
            ]

        if urls and any(token in lowered for token in ("summarize", "summary", "tl;dr", "brief")):
            return [
                self._step(
                    1,
                    "Analyze user intent and define summary focus.",
                    kind="analyze_request",
                    objective="Extract topic, scope and output style.",
                    expected_output="Intent brief with summary goals.",
                    hints={"task": normalized_task},
                ),
                self._step(
                    2,
                    "Fetch source content from provided URLs.",
                    depends_on=[1],
                    kind="fetch_source",
                    requires_tools=True,
                    objective="Collect source material required for summarization.",
                    expected_output="Fetch blueprint with URL targets.",
                    hints={"urls": urls[:5], "task": normalized_task},
                ),
                self._step(
                    3,
                    "Extract core facts and claims from source material.",
                    depends_on=[2],
                    kind="extract_facts",
                    objective="Derive facts suitable for final summary.",
                    expected_output="Structured bullet facts with references.",
                    hints={"task": normalized_task},
                ),
                self._step(
                    4,
                    "Compose concise summary and key takeaways.",
                    depends_on=[3],
                    kind="summarize",
                    objective="Synthesize extracted facts into final digest.",
                    expected_output="Summary outline + prioritized takeaways.",
                    hints={"task": normalized_task, "style": "summary"},
                ),
            ]

        compare_targets = self._extract_compare_targets(normalized_task)
        if strategy == "complex" and len(compare_targets) >= 2:
            steps: list[PlanStep] = [
                self._step(
                    1,
                    "Define comparison criteria and success conditions.",
                    kind="analyze_request",
                    objective="Create explicit evaluation framework.",
                    expected_output="Criteria checklist.",
                    hints={"task": normalized_task},
                )
            ]
            next_id = 2
            branch_ids: list[int] = []
            for target in compare_targets[:4]:
                branch_ids.append(next_id)
                steps.append(
                    self._step(
                        next_id,
                        f"Collect evidence for target: {target}.",
                        depends_on=[1],
                        kind="subtask_execution",
                        requires_tools=self._text_requires_tools(target),
                        objective=f"Build evidence set for {target}.",
                        expected_output="Target-specific findings.",
                        hints={"target": target, "task": normalized_task},
                    )
                )
                next_id += 1
            steps.append(
                self._step(
                    next_id,
                    "Merge target findings into a side-by-side comparison.",
                    depends_on=branch_ids,
                    kind="merge_results",
                    objective="Unify parallel findings into a coherent comparison.",
                    expected_output="Merged comparison matrix.",
                    hints={"task": normalized_task},
                )
            )
            next_id += 1
            steps.append(
                self._step(
                    next_id,
                    "Verify recommendation against criteria and constraints.",
                    depends_on=[next_id - 1],
                    kind="verify",
                    objective="Check consistency and produce final recommendation.",
                    expected_output="Validation checklist with recommendation.",
                    hints={"task": normalized_task},
                )
            )
            return steps

        if strategy == "tool":
            return [
                self._step(
                    1,
                    "Clarify intent and choose tool strategy.",
                    kind="analyze_request",
                    objective="Determine required data/tooling before execution.",
                    expected_output="Tool strategy brief.",
                    hints={"task": normalized_task},
                ),
                self._step(
                    2,
                    "Prepare tool execution blueprint.",
                    depends_on=[1],
                    kind="tool_query",
                    requires_tools=True,
                    objective="Specify tool calls and required arguments.",
                    expected_output="Tool execution blueprint.",
                    hints={"task": normalized_task, "urls": urls[:5]},
                ),
                self._step(
                    3,
                    "Validate tool outputs for completeness and quality.",
                    depends_on=[2],
                    kind="verify",
                    objective="Detect missing fields or inconsistent output.",
                    expected_output="Verification report.",
                    hints={"task": normalized_task},
                ),
                self._step(
                    4,
                    "Synthesize verified outputs into final response.",
                    depends_on=[3],
                    kind="synthesize",
                    objective="Convert validated outputs into user-facing answer.",
                    expected_output="Final response outline.",
                    hints={"task": normalized_task},
                ),
            ]

        if strategy == "complex":
            parts = self._split_compound_parts(normalized_task)
            if len(parts) >= 2:
                steps = [
                    self._step(
                        1,
                        "Decompose request into independently executable subtasks.",
                        kind="decompose_subtasks",
                        objective="Break request into clear work units.",
                        expected_output="Ordered subtask list.",
                        hints={"task": normalized_task, "parts": parts[:5]},
                    )
                ]
                branch_ids: list[int] = []
                next_id = 2
                for part in parts[:5]:
                    branch_ids.append(next_id)
                    steps.append(
                        self._step(
                            next_id,
                            f"Execute subtask: {part}",
                            depends_on=[1],
                            kind="subtask_execution",
                            requires_tools=self._text_requires_tools(part),
                            objective="Complete subtask with evidence.",
                            expected_output="Subtask artifact.",
                            hints={"task": normalized_task, "subtask": part},
                        )
                    )
                    next_id += 1
                steps.append(
                    self._step(
                        next_id,
                        "Merge all subtask outputs into one coherent result.",
                        depends_on=branch_ids,
                        kind="merge_results",
                        objective="Resolve conflicts and produce coherent synthesis.",
                        expected_output="Merged artifact with resolved conflicts.",
                        hints={"task": normalized_task},
                    )
                )
                next_id += 1
                steps.append(
                    self._step(
                        next_id,
                        "Verify merged result against original request and constraints.",
                        depends_on=[next_id - 1],
                        kind="verify",
                        objective="Ensure final output satisfies requested intent.",
                        expected_output="Verification checklist.",
                        hints={"task": normalized_task},
                    )
                )
                return steps

            return [
                self._step(
                    1,
                    "Analyze requirements, constraints, and success criteria.",
                    kind="analyze_request",
                    objective="Define execution boundaries and target quality.",
                    expected_output="Requirements brief.",
                    hints={"task": normalized_task},
                ),
                self._step(
                    2,
                    "Gather supporting evidence and intermediate findings.",
                    depends_on=[1],
                    kind="subtask_execution",
                    requires_tools=self._text_requires_tools(normalized_task),
                    objective="Collect necessary evidence for synthesis.",
                    expected_output="Evidence bundle.",
                    hints={"task": normalized_task, "urls": urls[:5]},
                ),
                self._step(
                    3,
                    "Synthesize evidence into structured answer draft.",
                    depends_on=[2],
                    kind="synthesize",
                    objective="Create structured answer with rationale.",
                    expected_output="Draft answer artifact.",
                    hints={"task": normalized_task},
                ),
                self._step(
                    4,
                    "Validate draft against constraints and expected output.",
                    depends_on=[3],
                    kind="verify",
                    objective="Prevent omissions and logical conflicts.",
                    expected_output="Validation report.",
                    hints={"task": normalized_task},
                ),
            ]

        return [
            self._step(
                1,
                "Analyze request and determine execution approach.",
                kind="analyze_request",
                objective="Frame task and constraints.",
                expected_output="Execution brief.",
                hints={"task": normalized_task},
            ),
            self._step(
                2,
                "Execute main reasoning path and gather evidence.",
                depends_on=[1],
                kind="subtask_execution",
                requires_tools=self._text_requires_tools(normalized_task),
                objective="Produce core findings for answer.",
                expected_output="Reasoning artifact.",
                hints={"task": normalized_task, "urls": urls[:5]},
            ),
            self._step(
                3,
                "Verify output quality and finalize concise response.",
                depends_on=[2],
                kind="verify",
                objective="Ensure response correctness and clarity.",
                expected_output="Validation + final answer outline.",
                hints={"task": normalized_task},
            ),
        ]

    def _step(
        self,
        step_id: int,
        description: str,
        *,
        depends_on: list[int] | None = None,
        kind: str,
        requires_tools: bool = False,
        objective: str = "",
        expected_output: str = "",
        hints: dict[str, Any] | None = None,
    ) -> PlanStep:
        return PlanStep(
            id=max(1, int(step_id)),
            description=str(description).strip() or f"Plan step {step_id}",
            depends_on=list(depends_on or []),
            kind=str(kind or "general").strip().lower() or "general",
            requires_tools=bool(requires_tools),
            objective=str(objective or "").strip(),
            expected_output=str(expected_output or "").strip(),
            hints=dict(hints or {}),
        )

    def _extract_urls(self, task: str) -> list[str]:
        if not task:
            return []
        seen: set[str] = set()
        result: list[str] = []
        for item in self._URL_RE.findall(task):
            cleaned = item.strip().rstrip(".,;)")
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            result.append(cleaned)
        return result

    @staticmethod
    def _split_compound_parts(task: str) -> list[str]:
        if not task:
            return []
        normalized = str(task).replace("\n", " ")
        pieces = re.split(r"\b(?:and|then|after|before|also|plus)\b", normalized, flags=re.IGNORECASE)
        result: list[str] = []
        for item in pieces:
            text = item.strip(" .,:;\t")
            if text:
                result.append(text)
        return result

    @staticmethod
    def _extract_compare_targets(task: str) -> list[str]:
        if not task:
            return []
        text = str(task)
        vs_parts = re.split(r"\bvs\.?\b|\bversus\b", text, flags=re.IGNORECASE)
        if len(vs_parts) >= 2:
            result = [part.strip(" .,:;\t") for part in vs_parts if part.strip(" .,:;\t")]
            return result
        if "compare" in text.lower() and " and " in text.lower():
            _, tail = text.lower().split("compare", 1)
            candidates = [item.strip(" .,:;\t") for item in tail.split(" and ") if item.strip(" .,:;\t")]
            if len(candidates) >= 2:
                return candidates
        return []

    @staticmethod
    def _text_requires_tools(text: str) -> bool:
        lowered = str(text or "").lower()
        return any(
            token in lowered
            for token in (
                "search",
                "find",
                "fetch",
                "download",
                "url",
                "website",
                "http",
                "file",
                "folder",
                "read",
                "write",
                "python",
                "script",
                "command",
            )
        )
