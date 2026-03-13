from __future__ import annotations

import hashlib
import hmac
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
    source: str = "local"
    risk_level: str = "low"
    approval_mode: str = "none"
    approval_predicate: Callable[[dict[str, Any]], bool] | None = None
    isolation: str = "restricted"


class ToolRegistry:
    def __init__(
        self,
        plugin_signing_key: str | None = None,
        plugin_signing_mode: str = "warn",
    ) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        self.logger = logging.getLogger("amaryllis.tools.registry")
        self.plugin_signing_key = (plugin_signing_key or "").strip() or None
        normalized_mode = str(plugin_signing_mode or "warn").strip().lower()
        if normalized_mode not in {"off", "warn", "strict"}:
            normalized_mode = "warn"
        self.plugin_signing_mode = normalized_mode
        self._plugin_events: list[dict[str, Any]] = []

    def register(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: Callable[[dict[str, Any]], Any],
        source: str = "local",
        risk_level: str = "low",
        approval_mode: str = "none",
        approval_predicate: Callable[[dict[str, Any]], bool] | None = None,
        isolation: str = "restricted",
    ) -> None:
        self._tools[name] = ToolDefinition(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
            source=source,
            risk_level=risk_level,
            approval_mode=approval_mode,
            approval_predicate=approval_predicate,
            isolation=isolation,
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
        self._plugin_events = []
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
                self._record_plugin_event(
                    plugin=item.name,
                    status="skipped",
                    reason=f"manifest_invalid:{exc}",
                    signature_state="manifest_invalid",
                )
                continue

            if not isinstance(manifest, dict):
                self.logger.error("plugin_manifest_invalid plugin=%s error=manifest_must_be_object", item.name)
                self._record_plugin_event(
                    plugin=item.name,
                    status="skipped",
                    reason="manifest_must_be_object",
                    signature_state="manifest_invalid",
                )
                continue

            allowed, signature_state, reason = self._verify_manifest_signature(plugin_name=item.name, manifest=manifest)
            if not allowed:
                self._record_plugin_event(
                    plugin=item.name,
                    status="blocked",
                    reason=reason,
                    signature_state=signature_state,
                )
                continue

            try:
                spec = importlib.util.spec_from_file_location(f"amaryllis_plugin_{item.name}", tool_path)
                if spec is None or spec.loader is None:
                    raise RuntimeError("Cannot build module spec")
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            except Exception as exc:
                self.logger.error("plugin_load_failed plugin=%s error=%s", item.name, exc)
                self._record_plugin_event(
                    plugin=item.name,
                    status="failed",
                    reason=f"load_failed:{exc}",
                    signature_state=signature_state,
                )
                continue

            try:
                if hasattr(module, "register") and callable(module.register):
                    module.register(self, manifest)
                elif hasattr(module, "register_tool") and callable(module.register_tool):
                    module.register_tool(self, manifest)
                else:
                    raise RuntimeError("Plugin must expose register(registry, manifest)")
                self.logger.info("plugin_loaded plugin=%s", item.name)
                self._record_plugin_event(
                    plugin=item.name,
                    status="loaded",
                    reason="ok",
                    signature_state=signature_state,
                )
            except Exception as exc:
                self.logger.error("plugin_register_failed plugin=%s error=%s", item.name, exc)
                self._record_plugin_event(
                    plugin=item.name,
                    status="failed",
                    reason=f"register_failed:{exc}",
                    signature_state=signature_state,
                )

    def plugin_discovery_report(self, limit: int = 200) -> dict[str, Any]:
        rows = self._plugin_events[: max(0, int(limit))]
        summary = {
            "loaded": 0,
            "blocked": 0,
            "failed": 0,
            "skipped": 0,
        }
        for item in rows:
            status = str(item.get("status") or "").strip().lower()
            if status in summary:
                summary[status] += 1
        return {
            "signing_mode": self.plugin_signing_mode,
            "signing_key_configured": self.plugin_signing_key is not None,
            "events": rows,
            "summary": summary,
        }

    def _verify_manifest_signature(
        self,
        plugin_name: str,
        manifest: dict[str, Any],
    ) -> tuple[bool, str, str]:
        signature = manifest.get("signature")
        mode = self.plugin_signing_mode

        if mode == "off":
            return True, "disabled", "signature_check_disabled"

        if self.plugin_signing_key is None:
            if mode == "strict":
                self.logger.error("plugin_signature_unverifiable_no_key plugin=%s", plugin_name)
                return False, "unverifiable_no_key", "strict_mode_requires_signing_key"
            if isinstance(signature, str) and signature.strip():
                self.logger.warning("plugin_signature_present plugin=%s verification=warn_no_key", plugin_name)
                return True, "warn_unverified_no_key", "signature_present_but_no_verification_key"
            self.logger.warning("plugin_signature_missing plugin=%s verification=warn_no_key", plugin_name)
            return True, "warn_missing_no_key", "signature_missing_but_allowed_in_warn_mode"

        if not isinstance(signature, str) or not signature.strip():
            if mode == "strict":
                self.logger.error("plugin_signature_missing plugin=%s", plugin_name)
                return False, "missing", "signature_missing"
            self.logger.warning("plugin_signature_missing plugin=%s verification=warn", plugin_name)
            return True, "warn_missing", "signature_missing_but_allowed_in_warn_mode"

        payload = {key: value for key, value in manifest.items() if key != "signature"}
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        expected = hmac.new(
            self.plugin_signing_key.encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(signature.strip().lower(), expected):
            if mode == "strict":
                self.logger.error("plugin_signature_invalid plugin=%s", plugin_name)
                return False, "invalid", "signature_invalid"
            self.logger.warning("plugin_signature_invalid plugin=%s verification=warn", plugin_name)
            return True, "warn_invalid", "signature_invalid_but_allowed_in_warn_mode"
        return True, "verified", "signature_verified"

    def _record_plugin_event(
        self,
        *,
        plugin: str,
        status: str,
        reason: str,
        signature_state: str,
    ) -> None:
        self._plugin_events.append(
            {
                "plugin": plugin,
                "status": status,
                "reason": reason,
                "signature_state": signature_state,
            }
        )
