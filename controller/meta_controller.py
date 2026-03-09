from __future__ import annotations


class MetaController:
    def choose_strategy(self, user_message: str, tools_available: bool) -> str:
        text = user_message.lower()

        if tools_available and any(
            token in text
            for token in [
                "search",
                "find",
                "file",
                "read",
                "write",
                "python",
                "website",
                "url",
            ]
        ):
            return "tool"

        if len(text) > 500 or any(token in text for token in ["plan", "analyze", "research", "multi-step"]):
            return "complex"

        return "simple"
