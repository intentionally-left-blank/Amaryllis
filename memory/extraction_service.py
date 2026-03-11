from __future__ import annotations

import re

from memory.models import ExtractionCandidate, ExtractionResult


class ExtractionService:
    def extract(self, text: str) -> ExtractionResult:
        normalized = text.strip()
        lowered = normalized.lower()

        facts: list[ExtractionCandidate] = []
        preferences: list[ExtractionCandidate] = []
        tasks: list[ExtractionCandidate] = []

        # Basic profile fact extraction.
        name_match = re.search(r"\b(?:my name is|i am|i'm)\s+([A-Za-z][A-Za-z0-9_\- ]{1,40})", normalized, re.I)
        if name_match:
            value = name_match.group(1).strip()
            facts.append(
                ExtractionCandidate(
                    kind="fact",
                    text=f"user_name={value}",
                    key="name",
                    value=value,
                    confidence=0.75,
                )
            )

        # Basic preference extraction.
        prefer_match = re.search(r"\b(?:i prefer|my favorite|i like)\s+(.+)$", normalized, re.I)
        if prefer_match:
            value = prefer_match.group(1).strip(" .,!?:;")
            preferences.append(
                ExtractionCandidate(
                    kind="preference",
                    text=f"preference={value}",
                    key="preference",
                    value=value,
                    confidence=0.7,
                )
            )

        # Working-memory task hints.
        task_match = re.search(r"\b(?:todo:|i need to|remind me to)\s+(.+)$", lowered, re.I)
        if task_match:
            task_text = task_match.group(1).strip(" .,!?:;")
            tasks.append(
                ExtractionCandidate(
                    kind="task",
                    text=task_text,
                    key=None,
                    value=task_text,
                    confidence=0.7,
                )
            )

        return ExtractionResult(facts=facts, preferences=preferences, tasks=tasks)
