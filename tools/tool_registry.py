from __future__ import annotations

import importlib.util
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self.logger = logging.getLogger("amaryllis.tools.registry")

    def register(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: Callable[[dict[str, Any]], Any],
    ) -> None:
        self._tools[name] = ToolDefinition(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
        )

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return sorted(self._tools.keys())

    def openai_schemas(self, selected: list[str] | None = None) -> list[dict[str, Any]]:
        selected_set = set(selected or self._tools.keys())
        schemas: list[dict[str, Any]] = []
        for tool in self._tools.values():
            if tool.name not in selected_set:
                continue
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
            )
        return schemas

    def load_builtin_tools(self) -> None:
        from tools.builtin_tools.filesystem import register as register_filesystem
        from tools.builtin_tools.python_exec import register as register_python_exec
        from tools.builtin_tools.web_search import register as register_web_search

        register_filesystem(self)
        register_web_search(self)
        register_python_exec(self)

    def discover_plugins(self, plugins_dir: Path) -> None:
        plugins_path = Path(plugins_dir)
        if not plugins_path.exists():
            return

        for item in sorted(plugins_path.iterdir()):
            if not item.is_dir():
                continue

            manifest_path = item / "manifest.json"
            tool_path = item / "tool.py"
            if not manifest_path.is_file() or not tool_path.is_file():
                continue

            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception as exc:
                self.logger.error("plugin_manifest_invalid plugin=%s error=%s", item.name, exc)
                continue

            try:
                spec = importlib.util.spec_from_file_location(f"amaryllis_plugin_{item.name}", tool_path)
                if spec is None or spec.loader is None:
                    raise RuntimeError("Cannot build module spec")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            except Exception as exc:
                self.logger.error("plugin_load_failed plugin=%s error=%s", item.name, exc)
                continue

            try:
                if hasattr(module, "register") and callable(module.register):
                    module.register(self, manifest)
                elif hasattr(module, "register_tool") and callable(module.register_tool):
                    module.register_tool(self, manifest)
                else:
                    raise RuntimeError("Plugin must expose register(registry, manifest)")
                self.logger.info("plugin_loaded plugin=%s", item.name)
            except Exception as exc:
                self.logger.error("plugin_register_failed plugin=%s error=%s", item.name, exc)
