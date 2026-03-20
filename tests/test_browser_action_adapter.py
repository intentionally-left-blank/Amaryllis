from __future__ import annotations

import unittest

from tools.browser_action_adapter import (
    BrowserActionRequest,
    BrowserActionResult,
    StubBrowserActionAdapter,
    register_browser_action_tool,
)
from tools.tool_executor import ToolExecutionError, ToolExecutor
from tools.tool_registry import ToolRegistry


class _RecordingAdapter:
    def __init__(self) -> None:
        self.last_request: BrowserActionRequest | None = None

    def describe(self) -> dict[str, object]:
        return {
            "provider": "recording",
            "kind": "test",
            "supports_real_browser": False,
        }

    def execute(self, request: BrowserActionRequest) -> BrowserActionResult:
        self.last_request = request
        return BrowserActionResult(
            ok=True,
            provider="recording",
            action=request.action,
            status="ok",
            message="recorded",
            data={"selector": request.selector},
        )


class BrowserActionAdapterTests(unittest.TestCase):
    def test_register_and_execute_browser_action_with_stub_adapter(self) -> None:
        registry = ToolRegistry()
        registry.load_builtin_tools()
        registered = register_browser_action_tool(
            registry,
            StubBrowserActionAdapter(provider_name="stub-browser-test"),
        )
        self.assertTrue(registered)

        executor = ToolExecutor(registry=registry)
        result = executor.execute(
            "browser_action",
            {
                "action": "extract",
                "selector": "main",
                "timeout_ms": 3000,
            },
        )

        self.assertEqual(result["tool"], "browser_action")
        payload = result["result"]
        self.assertEqual(str(payload.get("status")), "stubbed")
        self.assertEqual(str(payload.get("provider")), "stub-browser-test")
        self.assertEqual(str(payload.get("request", {}).get("action")), "extract")
        self.assertEqual(str(payload.get("request", {}).get("selector")), "main")
        self.assertEqual(str(payload.get("adapter", {}).get("kind")), "stub")

    def test_browser_action_invalid_action_fails_validation(self) -> None:
        registry = ToolRegistry()
        registry.load_builtin_tools()
        register_browser_action_tool(registry, StubBrowserActionAdapter())
        executor = ToolExecutor(registry=registry)

        with self.assertRaises(ToolExecutionError):
            executor.execute("browser_action", {"action": "unknown"})

    def test_register_browser_action_tool_is_idempotent_without_replace(self) -> None:
        registry = ToolRegistry()
        first = register_browser_action_tool(registry, StubBrowserActionAdapter(provider_name="a"))
        second = register_browser_action_tool(registry, StubBrowserActionAdapter(provider_name="b"))
        self.assertTrue(first)
        self.assertFalse(second)

    def test_custom_adapter_receives_typed_request(self) -> None:
        registry = ToolRegistry()
        adapter = _RecordingAdapter()
        register_browser_action_tool(registry, adapter)
        executor = ToolExecutor(registry=registry)

        result = executor.execute(
            "browser_action",
            {
                "action": "wait",
                "wait_ms": 120,
                "selector": "#app",
            },
        )
        self.assertEqual(result["tool"], "browser_action")
        self.assertEqual(str(result["result"].get("status")), "ok")
        self.assertIsNotNone(adapter.last_request)
        assert adapter.last_request is not None
        self.assertEqual(adapter.last_request.action, "wait")
        self.assertEqual(adapter.last_request.wait_ms, 120)
        self.assertEqual(adapter.last_request.selector, "#app")


if __name__ == "__main__":
    unittest.main()
