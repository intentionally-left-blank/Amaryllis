from __future__ import annotations

import hashlib
import hmac
import json
import tempfile
import unittest
from pathlib import Path

from tools.tool_registry import ToolRegistry


PLUGIN_TOOL_CODE = """
def register(registry, manifest):
    name = str(manifest.get('name') or 'plugin_tool')
    registry.register(
        name=name,
        description='plugin test tool',
        input_schema={
            'type': 'object',
            'properties': {},
            'additionalProperties': True,
        },
        handler=lambda arguments: {'ok': True, 'arguments': arguments},
        source='plugin:test',
        risk_level='low',
        approval_mode='none',
    )
""".strip()


class ToolPluginSigningTests(unittest.TestCase):
    def test_strict_mode_blocks_unsigned_plugin(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-plugin-signing-") as tmp:
            plugins_dir = Path(tmp)
            self._write_plugin(
                plugins_dir=plugins_dir,
                plugin_dir_name="unsigned",
                manifest={"name": "unsigned_tool", "version": "1.0.0"},
            )

            registry = ToolRegistry(plugin_signing_key="secret", plugin_signing_mode="strict")
            registry.discover_plugins(plugins_dir)

            self.assertNotIn("unsigned_tool", registry.names())
            report = registry.plugin_discovery_report()
            self.assertEqual(report["signing_mode"], "strict")
            self.assertGreaterEqual(int(report["summary"].get("blocked", 0)), 1)

    def test_warn_mode_allows_unsigned_plugin(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-plugin-signing-") as tmp:
            plugins_dir = Path(tmp)
            self._write_plugin(
                plugins_dir=plugins_dir,
                plugin_dir_name="unsigned_warn",
                manifest={"name": "warn_tool", "version": "1.0.0"},
            )

            registry = ToolRegistry(plugin_signing_key="secret", plugin_signing_mode="warn")
            registry.discover_plugins(plugins_dir)

            self.assertIn("warn_tool", registry.names())
            report = registry.plugin_discovery_report()
            self.assertGreaterEqual(int(report["summary"].get("loaded", 0)), 1)
            events = report.get("events", [])
            self.assertTrue(any(str(item.get("signature_state", "")).startswith("warn_") for item in events))

    def test_strict_mode_accepts_valid_signature(self) -> None:
        with tempfile.TemporaryDirectory(prefix="amaryllis-plugin-signing-") as tmp:
            plugins_dir = Path(tmp)
            secret = "super-secret"
            manifest = {"name": "signed_tool", "version": "1.0.0"}
            signed_manifest = self._sign_manifest(manifest=manifest, key=secret)
            self._write_plugin(
                plugins_dir=plugins_dir,
                plugin_dir_name="signed",
                manifest=signed_manifest,
            )

            registry = ToolRegistry(plugin_signing_key=secret, plugin_signing_mode="strict")
            registry.discover_plugins(plugins_dir)

            self.assertIn("signed_tool", registry.names())
            report = registry.plugin_discovery_report()
            self.assertGreaterEqual(int(report["summary"].get("loaded", 0)), 1)
            self.assertTrue(any(item.get("signature_state") == "verified" for item in report.get("events", [])))

    @staticmethod
    def _write_plugin(
        *,
        plugins_dir: Path,
        plugin_dir_name: str,
        manifest: dict,
    ) -> None:
        target = plugins_dir / plugin_dir_name
        target.mkdir(parents=True, exist_ok=True)
        (target / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        (target / "tool.py").write_text(PLUGIN_TOOL_CODE + "\n", encoding="utf-8")

    @staticmethod
    def _sign_manifest(*, manifest: dict, key: str) -> dict:
        payload = dict(manifest)
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        signature = hmac.new(key.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
        payload["signature"] = signature
        return payload


if __name__ == "__main__":
    unittest.main()
