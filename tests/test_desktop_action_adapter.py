from __future__ import annotations

import unittest

from tools.desktop_action_adapter import (
    DesktopActionRequest,
    DesktopActionResult,
    LinuxDesktopActionAdapter,
    MacOSDesktopActionAdapter,
    StubDesktopActionAdapter,
    create_default_desktop_action_adapter,
    register_desktop_action_tool,
)
from tools.tool_executor import ToolExecutionError, ToolExecutor
from tools.tool_registry import ToolRegistry


class _Completed:
    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class _RecordingAdapter:
    def __init__(self) -> None:
        self.last_request: DesktopActionRequest | None = None

    def describe(self) -> dict[str, object]:
        return {
            "provider": "recording-desktop",
            "kind": "test",
            "supports_real_desktop": False,
        }

    def execute(self, request: DesktopActionRequest) -> DesktopActionResult:
        self.last_request = request
        return DesktopActionResult(
            ok=True,
            provider="recording-desktop",
            action=request.action,
            status="ok",
            data={"target": request.target},
        )


class _PopenResult:
    def __init__(self, pid: int) -> None:
        self.pid = pid


class DesktopActionAdapterTests(unittest.TestCase):
    def test_default_adapter_selector_uses_platform_mapping(self) -> None:
        linux_adapter = create_default_desktop_action_adapter(platform_name="linux")
        self.assertEqual(str(linux_adapter.describe().get("kind")), "linux")

        macos_adapter = create_default_desktop_action_adapter(platform_name="darwin")
        self.assertEqual(str(macos_adapter.describe().get("kind")), "macos")

        fallback_adapter = create_default_desktop_action_adapter(platform_name="win32")
        self.assertEqual(str(fallback_adapter.describe().get("kind")), "stub")

    def test_register_and_execute_desktop_action_with_stub_adapter(self) -> None:
        registry = ToolRegistry()
        registry.load_builtin_tools()
        registered = register_desktop_action_tool(
            registry,
            StubDesktopActionAdapter(provider_name="stub-desktop-test"),
        )
        self.assertTrue(registered)

        executor = ToolExecutor(registry=registry)
        result = executor.execute(
            "desktop_action",
            {
                "action": "notify",
                "message": "hello",
            },
        )

        self.assertEqual(result["tool"], "desktop_action")
        payload = result["result"]
        self.assertEqual(str(payload.get("status")), "stubbed")
        self.assertEqual(str(payload.get("provider")), "stub-desktop-test")
        self.assertEqual(str(payload.get("request", {}).get("action")), "notify")
        self.assertEqual(str(payload.get("adapter", {}).get("kind")), "stub")
        self.assertIn("rollback_hint", payload.get("metadata", {}))

    def test_desktop_action_invalid_action_fails_validation(self) -> None:
        registry = ToolRegistry()
        registry.load_builtin_tools()
        register_desktop_action_tool(registry, StubDesktopActionAdapter())
        executor = ToolExecutor(registry=registry)

        with self.assertRaises(ToolExecutionError):
            executor.execute("desktop_action", {"action": "unknown"})

    def test_register_desktop_action_tool_is_idempotent_without_replace(self) -> None:
        registry = ToolRegistry()
        first = register_desktop_action_tool(registry, StubDesktopActionAdapter(provider_name="a"))
        second = register_desktop_action_tool(registry, StubDesktopActionAdapter(provider_name="b"))
        self.assertTrue(first)
        self.assertFalse(second)

    def test_custom_adapter_receives_typed_request(self) -> None:
        registry = ToolRegistry()
        adapter = _RecordingAdapter()
        register_desktop_action_tool(registry, adapter)
        executor = ToolExecutor(registry=registry)

        result = executor.execute(
            "desktop_action",
            {
                "action": "app_launch",
                "target": "org.gnome.Nautilus.desktop",
                "timeout_sec": 3,
            },
        )
        self.assertEqual(result["tool"], "desktop_action")
        self.assertEqual(str(result["result"].get("status")), "ok")
        self.assertIsNotNone(adapter.last_request)
        assert adapter.last_request is not None
        self.assertEqual(adapter.last_request.action, "app_launch")
        self.assertEqual(adapter.last_request.target, "org.gnome.Nautilus.desktop")
        self.assertEqual(adapter.last_request.timeout_sec, 3)

    def test_linux_notify_returns_unavailable_without_notify_send(self) -> None:
        adapter = LinuxDesktopActionAdapter(
            which_resolver=lambda _: None,
        )
        request = DesktopActionRequest.from_arguments(
            {"action": "notify", "message": "hello"},
        )
        result = adapter.execute(request)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "unavailable")

    def test_linux_clipboard_write_uses_wl_copy(self) -> None:
        calls: list[dict[str, object]] = []

        def _which(name: str) -> str | None:
            if name == "wl-copy":
                return "/usr/bin/wl-copy"
            return None

        def _run(command: list[str], **kwargs: object) -> _Completed:
            calls.append({"command": list(command), **kwargs})
            return _Completed(returncode=0, stdout="", stderr="")

        adapter = LinuxDesktopActionAdapter(
            which_resolver=_which,
            run_command=_run,
        )
        request = DesktopActionRequest.from_arguments(
            {"action": "clipboard_write", "text": "hello"},
        )
        result = adapter.execute(request)
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "succeeded")
        self.assertTrue(calls)
        self.assertEqual(calls[0]["command"], ["/usr/bin/wl-copy"])

    def test_linux_window_list_parses_wmctrl_output(self) -> None:
        sample = (
            "0x03e00007  0 host Terminal\n"
            "0x0420001a  0 host Browser\n"
        )

        def _which(name: str) -> str | None:
            if name == "wmctrl":
                return "/usr/bin/wmctrl"
            return None

        def _run(command: list[str], **kwargs: object) -> _Completed:
            _ = kwargs
            self.assertEqual(command, ["/usr/bin/wmctrl", "-l"])
            return _Completed(returncode=0, stdout=sample, stderr="")

        adapter = LinuxDesktopActionAdapter(
            which_resolver=_which,
            run_command=_run,
        )
        request = DesktopActionRequest.from_arguments({"action": "window_list"})
        result = adapter.execute(request)
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "succeeded")
        windows = result.data.get("windows", [])
        self.assertEqual(len(windows), 2)
        self.assertEqual(str(windows[0].get("title")), "Terminal")
        self.assertIn("rollback_hint", result.metadata)

    def test_linux_window_focus_uses_wmctrl(self) -> None:
        def _which(name: str) -> str | None:
            if name == "wmctrl":
                return "/usr/bin/wmctrl"
            return None

        def _run(command: list[str], **kwargs: object) -> _Completed:
            _ = kwargs
            self.assertEqual(command, ["/usr/bin/wmctrl", "-ia", "0x03e00007"])
            return _Completed(returncode=0, stdout="", stderr="")

        adapter = LinuxDesktopActionAdapter(
            which_resolver=_which,
            run_command=_run,
        )
        request = DesktopActionRequest.from_arguments(
            {"action": "window_focus", "target": "0x03e00007"},
        )
        result = adapter.execute(request)
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "succeeded")
        self.assertIn("rollback_hint", result.metadata)

    def test_linux_window_close_uses_wmctrl(self) -> None:
        def _which(name: str) -> str | None:
            if name == "wmctrl":
                return "/usr/bin/wmctrl"
            return None

        def _run(command: list[str], **kwargs: object) -> _Completed:
            _ = kwargs
            self.assertEqual(command, ["/usr/bin/wmctrl", "-ic", "0x03e00007"])
            return _Completed(returncode=0, stdout="", stderr="")

        adapter = LinuxDesktopActionAdapter(
            which_resolver=_which,
            run_command=_run,
        )
        request = DesktopActionRequest.from_arguments(
            {"action": "window_close", "target": "0x03e00007"},
        )
        result = adapter.execute(request)
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "succeeded")
        self.assertIn("rollback_hint", result.metadata)

    def test_linux_app_launch_prefers_gtk_launch_for_desktop_id(self) -> None:
        popen_calls: list[list[str]] = []

        def _which(name: str) -> str | None:
            if name == "gtk-launch":
                return "/usr/bin/gtk-launch"
            if name == "xdg-open":
                return "/usr/bin/xdg-open"
            return None

        def _popen(command: list[str], **kwargs: object) -> _PopenResult:
            _ = kwargs
            popen_calls.append(list(command))
            return _PopenResult(pid=4242)

        adapter = LinuxDesktopActionAdapter(
            which_resolver=_which,
            popen_command=_popen,
        )
        request = DesktopActionRequest.from_arguments(
            {"action": "app_launch", "target": "org.gnome.Nautilus.desktop"},
        )
        result = adapter.execute(request)
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(popen_calls[0], ["/usr/bin/gtk-launch", "org.gnome.Nautilus.desktop"])
        self.assertEqual(int(result.data.get("pid", 0)), 4242)

    def test_macos_notify_uses_osascript(self) -> None:
        calls: list[dict[str, object]] = []

        def _which(name: str) -> str | None:
            if name == "osascript":
                return "/usr/bin/osascript"
            return None

        def _run(command: list[str], **kwargs: object) -> _Completed:
            calls.append({"command": list(command), **kwargs})
            return _Completed(returncode=0, stdout="", stderr="")

        adapter = MacOSDesktopActionAdapter(
            which_resolver=_which,
            run_command=_run,
        )
        request = DesktopActionRequest.from_arguments(
            {"action": "notify", "title": "Jarvis", "message": "Hi"},
        )
        result = adapter.execute(request)
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "succeeded")
        self.assertTrue(calls)
        command = calls[0]["command"]
        self.assertIsInstance(command, list)
        assert isinstance(command, list)
        self.assertEqual(command[0:2], ["/usr/bin/osascript", "-e"])
        self.assertIn('display notification "Hi"', str(command[2]))

    def test_macos_clipboard_write_uses_pbcopy(self) -> None:
        calls: list[dict[str, object]] = []

        def _which(name: str) -> str | None:
            if name == "pbcopy":
                return "/usr/bin/pbcopy"
            return None

        def _run(command: list[str], **kwargs: object) -> _Completed:
            calls.append({"command": list(command), **kwargs})
            return _Completed(returncode=0, stdout="", stderr="")

        adapter = MacOSDesktopActionAdapter(
            which_resolver=_which,
            run_command=_run,
        )
        request = DesktopActionRequest.from_arguments(
            {"action": "clipboard_write", "text": "hello"},
        )
        result = adapter.execute(request)
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "succeeded")
        self.assertTrue(calls)
        self.assertEqual(calls[0]["command"], ["/usr/bin/pbcopy"])

    def test_macos_app_launch_uses_open_bundle_id(self) -> None:
        popen_calls: list[list[str]] = []

        def _which(name: str) -> str | None:
            if name == "open":
                return "/usr/bin/open"
            return None

        def _popen(command: list[str], **kwargs: object) -> _PopenResult:
            _ = kwargs
            popen_calls.append(list(command))
            return _PopenResult(pid=777)

        adapter = MacOSDesktopActionAdapter(
            which_resolver=_which,
            popen_command=_popen,
        )
        request = DesktopActionRequest.from_arguments(
            {"action": "app_launch", "target": "com.apple.Safari"},
        )
        result = adapter.execute(request)
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(popen_calls[0], ["/usr/bin/open", "-b", "com.apple.Safari"])
        self.assertEqual(int(result.data.get("pid", 0)), 777)

    def test_macos_window_list_parses_process_names(self) -> None:
        def _which(name: str) -> str | None:
            if name == "osascript":
                return "/usr/bin/osascript"
            return None

        def _run(command: list[str], **kwargs: object) -> _Completed:
            _ = kwargs
            self.assertEqual(command[0:2], ["/usr/bin/osascript", "-e"])
            return _Completed(returncode=0, stdout="Finder, Terminal, Safari\n", stderr="")

        adapter = MacOSDesktopActionAdapter(
            which_resolver=_which,
            run_command=_run,
        )
        request = DesktopActionRequest.from_arguments({"action": "window_list"})
        result = adapter.execute(request)
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "succeeded")
        windows = result.data.get("windows", [])
        self.assertEqual(len(windows), 3)
        self.assertEqual(str(windows[0].get("window_id")), "Finder")


if __name__ == "__main__":
    unittest.main()
