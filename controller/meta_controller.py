from __future__ import annotations

import re

class MetaController:
    _URL_RE = re.compile(r"https?://[^\s)>\]]+", re.IGNORECASE)
    _PATH_RE = re.compile(r"(?:^|\s)(?:~?/|/)[^\s]+")

    def choose_strategy(self, user_message: str, tools_available: bool) -> str:
        normalized_text = " ".join(str(user_message or "").strip().split())
        text = normalized_text.lower()
        if not text:
            return "simple"

        tool_score = self._tool_signal_score(text=text, original=normalized_text)
        complexity_score = self._complexity_signal_score(text=text, original=normalized_text)

        if complexity_score >= 4:
            return "complex"
        if complexity_score >= 3 and tool_score >= 2:
            return "complex"

        if tools_available and tool_score >= 2:
            return "tool"
        if tools_available and tool_score >= 1 and complexity_score >= 2:
            return "tool"
        if not tools_available and tool_score >= 2 and complexity_score >= 2:
            return "complex"

        return "simple"

    def _tool_signal_score(self, *, text: str, original: str) -> int:
        score = 0
        keyword_hits = sum(
            1
            for token in (
                "search",
                "find",
                "lookup",
                "scrape",
                "fetch",
                "download",
                "website",
                "url",
                "http",
                "file",
                "folder",
                "directory",
                "read",
                "write",
                "save",
                "python",
                "script",
                "terminal",
                "command",
                "execute",
                "run code",
            )
            if token in text
        )
        score += min(keyword_hits, 4)
        if self._URL_RE.search(original):
            score += 2
        if self._PATH_RE.search(original):
            score += 1
        return score

    def _complexity_signal_score(self, *, text: str, original: str) -> int:
        score = 0
        if len(text) > 700:
            score += 2
        elif len(text) > 350:
            score += 1

        connector_hits = sum(
            text.count(token)
            for token in (" and ", " then ", " after ", " before ", " also ", " plus ", " finally ")
        )
        if connector_hits >= 3:
            score += 2
        elif connector_hits >= 1:
            score += 1

        keyword_hits = sum(
            1
            for token in (
                "plan",
                "analyze",
                "analysis",
                "research",
                "strategy",
                "compare",
                "multi-step",
                "workflow",
                "roadmap",
                "phases",
                "requirements",
                "tradeoff",
            )
            if token in text
        )
        score += min(keyword_hits, 3)

        numbered_steps = len(re.findall(r"\b\d+[\.)]\s*", original))
        if numbered_steps >= 2:
            score += 2
        elif numbered_steps == 1:
            score += 1
        return score
