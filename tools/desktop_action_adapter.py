from __future__ import annotations

from dataclasses import dataclass, field, replace
import os
import shutil
import subprocess
import sys
from typing import Any, Protocol

from tools.tool_registry import ToolRegistry

SUPPORTED_DESKTOP_ACTIONS: tuple[str, ...] = (
    "notify",
    "clipboard_read",
    "clipboard_write",
    "app_launch",
    "window_list",
    "window_focus",
    "window_close",
)
MUTATING_DESKTOP_ACTIONS: set[str] = {
    "notify",
    "clipboard_write",
    "app_launch",
    "window_focus",
    "window_close",
}
MAX_TIMEOUT_SEC = 120
MAX_CLIPBOARD_RESPONSE_CHARS = max(
    1_000,
    int(os.getenv("AMARYLLIS_DESKTOP_CLIPBOARD_MAX_RESPONSE_CHARS", "200000")),
)


@dataclass(frozen=True)
class DesktopActionRequest:
    action: str
    title: str | None = None
    message: str | None = None
    text: str | None = None
    target: str | None = None
    timeout_sec: int = 8
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_arguments(cls, arguments: dict[str, Any]) -> "DesktopActionRequest":
        args = arguments if isinstance(arguments, dict) else {}
        action = str(args.get("action", "")).strip().lower()
        if action not in set(SUPPORTED_DESKTOP_ACTIONS):
            allowed = ", ".join(SUPPORTED_DESKTOP_ACTIONS)
            raise ValueError(f"Unsupported desktop action '{action}'. Allowed values: {allowed}.")

        timeout_sec = _normalize_int(
            args.get("timeout_sec"),
            default=8,
            minimum=1,
            maximum=MAX_TIMEOUT_SEC,
        )
        metadata_raw = args.get("metadata")
        metadata = dict(metadata_raw) if isinstance(metadata_raw, dict) else {}

        title = _optional_str(args.get("title"))
        message = _optional_str(args.get("message"))
        text = _optional_str(args.get("text"))
        target = _optional_str(args.get("target"))

        if action == "notify" and not (message or text):
            raise ValueError("notify action requires either 'message' or 'text'")
        if action == "clipboard_write" and text is None:
            raise ValueError("clipboard_write action requires 'text'")
        if action in {"app_launch", "window_focus", "window_close"} and target is None:
            raise ValueError(f"{action} action requires 'target'")

        return cls(
            action=action,
            title=title,
            message=message,
            text=text,
            target=target,
            timeout_sec=timeout_sec,
            metadata=metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "title": self.title,
            "message": self.message,
            "text": self.text,
            "target": self.target,
            "timeout_sec": int(self.timeout_sec),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class DesktopActionResult:
    ok: bool
    provider: str
    action: str
    status: str
    message: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "provider": str(self.provider),
            "action": str(self.action),
            "status": str(self.status),
            "message": self.message,
            "data": dict(self.data),
            "warnings": list(self.warnings),
            "metadata": dict(self.metadata),
        }


class DesktopActionAdapter(Protocol):
    def execute(self, request: DesktopActionRequest) -> DesktopActionResult:
        ...

    def describe(self) -> dict[str, Any]:
        ...


class StubDesktopActionAdapter:
    def __init__(self, provider_name: str = "stub-desktop") -> None:
        self.provider_name = str(provider_name or "stub-desktop").strip() or "stub-desktop"

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "kind": "stub",
            "platform": sys.platform,
            "actions": list(SUPPORTED_DESKTOP_ACTIONS),
            "supports_real_desktop": False,
        }

    def execute(self, request: DesktopActionRequest) -> DesktopActionResult:
        return DesktopActionResult(
            ok=True,
            provider=self.provider_name,
            action=request.action,
            status="stubbed",
            message="Desktop adapter stub executed. No real desktop provider configured.",
            data={
                "echo": request.to_dict(),
                "capabilities": self.describe(),
            },
            warnings=[
                "desktop_action is currently running in stub mode",
            ],
            metadata={"stub": True},
        )


class LinuxDesktopActionAdapter:
    def __init__(
        self,
        provider_name: str = "linux-desktop",
        *,
        which_resolver: Any = None,
        run_command: Any = None,
        popen_command: Any = None,
    ) -> None:
        self.provider_name = str(provider_name or "linux-desktop").strip() or "linux-desktop"
        self._which = which_resolver or shutil.which
        self._run = run_command or subprocess.run
        self._popen = popen_command or subprocess.Popen

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "kind": "linux",
            "platform": sys.platform,
            "actions": list(SUPPORTED_DESKTOP_ACTIONS),
            "commands": {
                "notify_send": bool(self._which("notify-send")),
                "wl_copy": bool(self._which("wl-copy")),
                "wl_paste": bool(self._which("wl-paste")),
                "xclip": bool(self._which("xclip")),
                "xsel": bool(self._which("xsel")),
                "xdg_open": bool(self._which("xdg-open")),
                "gtk_launch": bool(self._which("gtk-launch")),
                "wmctrl": bool(self._which("wmctrl")),
            },
            "supports_real_desktop": True,
        }

    def execute(self, request: DesktopActionRequest) -> DesktopActionResult:
        try:
            if request.action == "notify":
                result = self._notify(request)
                return self._attach_action_context(result, request)
            if request.action == "clipboard_read":
                result = self._clipboard_read(request)
                return self._attach_action_context(result, request)
            if request.action == "clipboard_write":
                result = self._clipboard_write(request)
                return self._attach_action_context(result, request)
            if request.action == "app_launch":
                result = self._app_launch(request)
                return self._attach_action_context(result, request)
            if request.action == "window_list":
                result = self._window_list(request)
                return self._attach_action_context(result, request)
            if request.action == "window_focus":
                result = self._window_focus(request)
                return self._attach_action_context(result, request)
            if request.action == "window_close":
                result = self._window_close(request)
                return self._attach_action_context(result, request)
            return self._attach_action_context(self._failed(request.action, "unsupported_action"), request)
        except subprocess.TimeoutExpired:
            return self._attach_action_context(
                self._failed(request.action, f"desktop command timeout ({request.timeout_sec}s)"),
                request,
            )
        except Exception as exc:
            return self._attach_action_context(self._failed(request.action, str(exc)), request)

    def _notify(self, request: DesktopActionRequest) -> DesktopActionResult:
        command = self._which("notify-send")
        if not command:
            return self._unavailable(request.action, "notify-send is not available on this host")
        title = request.title or "Amaryllis"
        body = request.message or request.text or ""
        completed = self._run(
            [command, title, body],
            capture_output=True,
            text=True,
            timeout=request.timeout_sec,
            check=False,
        )
        if int(completed.returncode) != 0:
            return self._failed(request.action, (completed.stderr or "").strip() or "notify-send failed")
        return DesktopActionResult(
            ok=True,
            provider=self.provider_name,
            action=request.action,
            status="succeeded",
            data={
                "title": title,
                "message": body,
                "command": "notify-send",
            },
        )

    def _clipboard_write(self, request: DesktopActionRequest) -> DesktopActionResult:
        text = str(request.text or "")
        command = self._clipboard_write_command()
        if command is None:
            return self._unavailable(
                request.action,
                "clipboard write command is unavailable (expected wl-copy, xclip, or xsel)",
            )
        completed = self._run(
            command,
            capture_output=True,
            text=True,
            input=text,
            timeout=request.timeout_sec,
            check=False,
        )
        if int(completed.returncode) != 0:
            return self._failed(request.action, (completed.stderr or "").strip() or "clipboard write failed")
        return DesktopActionResult(
            ok=True,
            provider=self.provider_name,
            action=request.action,
            status="succeeded",
            data={
                "written_chars": len(text),
                "command": list(command),
            },
        )

    def _clipboard_read(self, request: DesktopActionRequest) -> DesktopActionResult:
        command = self._clipboard_read_command()
        if command is None:
            return self._unavailable(
                request.action,
                "clipboard read command is unavailable (expected wl-paste, xclip, or xsel)",
            )
        completed = self._run(
            command,
            capture_output=True,
            text=True,
            timeout=request.timeout_sec,
            check=False,
        )
        if int(completed.returncode) != 0:
            return self._failed(request.action, (completed.stderr or "").strip() or "clipboard read failed")
        content = str(completed.stdout or "")
        truncated = False
        if len(content) > MAX_CLIPBOARD_RESPONSE_CHARS:
            content = content[:MAX_CLIPBOARD_RESPONSE_CHARS]
            truncated = True
        return DesktopActionResult(
            ok=True,
            provider=self.provider_name,
            action=request.action,
            status="succeeded",
            data={
                "content": content,
                "truncated": truncated,
                "command": list(command),
            },
        )

    def _app_launch(self, request: DesktopActionRequest) -> DesktopActionResult:
        target = str(request.target or "").strip()
        if not target:
            return self._failed(request.action, "target is required")
        command = self._launch_command(target)
        if command is None:
            return self._unavailable(
                request.action,
                "app launch command is unavailable (expected xdg-open or gtk-launch)",
            )
        process = self._popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return DesktopActionResult(
            ok=True,
            provider=self.provider_name,
            action=request.action,
            status="succeeded",
            data={
                "target": target,
                "pid": int(getattr(process, "pid", 0) or 0),
                "command": list(command),
            },
        )

    def _window_list(self, request: DesktopActionRequest) -> DesktopActionResult:
        command = self._which("wmctrl")
        if not command:
            return self._unavailable(request.action, "wmctrl is not available on this host")
        completed = self._run(
            [command, "-l"],
            capture_output=True,
            text=True,
            timeout=request.timeout_sec,
            check=False,
        )
        if int(completed.returncode) != 0:
            return self._failed(request.action, (completed.stderr or "").strip() or "wmctrl failed")
        windows = _parse_wmctrl_lines(str(completed.stdout or ""))
        return DesktopActionResult(
            ok=True,
            provider=self.provider_name,
            action=request.action,
            status="succeeded",
            data={
                "windows": windows,
                "count": len(windows),
                "command": [command, "-l"],
            },
        )

    def _window_focus(self, request: DesktopActionRequest) -> DesktopActionResult:
        target = str(request.target or "").strip()
        if not target:
            return self._failed(request.action, "target is required")
        command = self._which("wmctrl")
        if not command:
            return self._unavailable(request.action, "wmctrl is not available on this host")
        completed = self._run(
            [command, "-ia", target],
            capture_output=True,
            text=True,
            timeout=request.timeout_sec,
            check=False,
        )
        if int(completed.returncode) != 0:
            return self._failed(request.action, (completed.stderr or "").strip() or "wmctrl focus failed")
        return DesktopActionResult(
            ok=True,
            provider=self.provider_name,
            action=request.action,
            status="succeeded",
            data={
                "target": target,
                "command": [command, "-ia", target],
            },
        )

    def _window_close(self, request: DesktopActionRequest) -> DesktopActionResult:
        target = str(request.target or "").strip()
        if not target:
            return self._failed(request.action, "target is required")
        command = self._which("wmctrl")
        if not command:
            return self._unavailable(request.action, "wmctrl is not available on this host")
        completed = self._run(
            [command, "-ic", target],
            capture_output=True,
            text=True,
            timeout=request.timeout_sec,
            check=False,
        )
        if int(completed.returncode) != 0:
            return self._failed(request.action, (completed.stderr or "").strip() or "wmctrl close failed")
        return DesktopActionResult(
            ok=True,
            provider=self.provider_name,
            action=request.action,
            status="succeeded",
            data={
                "target": target,
                "command": [command, "-ic", target],
            },
        )

    def _clipboard_write_command(self) -> list[str] | None:
        wl_copy = self._which("wl-copy")
        if wl_copy:
            return [wl_copy]
        xclip = self._which("xclip")
        if xclip:
            return [xclip, "-selection", "clipboard"]
        xsel = self._which("xsel")
        if xsel:
            return [xsel, "--clipboard", "--input"]
        return None

    def _clipboard_read_command(self) -> list[str] | None:
        wl_paste = self._which("wl-paste")
        if wl_paste:
            return [wl_paste, "--no-newline"]
        xclip = self._which("xclip")
        if xclip:
            return [xclip, "-selection", "clipboard", "-o"]
        xsel = self._which("xsel")
        if xsel:
            return [xsel, "--clipboard", "--output"]
        return None

    def _launch_command(self, target: str) -> list[str] | None:
        if _looks_like_desktop_id(target):
            gtk_launch = self._which("gtk-launch")
            if gtk_launch:
                return [gtk_launch, target]
        xdg_open = self._which("xdg-open")
        if xdg_open:
            return [xdg_open, target]
        return None

    def _unavailable(self, action: str, reason: str) -> DesktopActionResult:
        return DesktopActionResult(
            ok=False,
            provider=self.provider_name,
            action=action,
            status="unavailable",
            message=reason,
            warnings=[reason],
            metadata={"platform": sys.platform},
        )

    def _failed(self, action: str, reason: str) -> DesktopActionResult:
        return DesktopActionResult(
            ok=False,
            provider=self.provider_name,
            action=action,
            status="failed",
            message=reason,
            metadata={"platform": sys.platform},
        )

    def _attach_action_context(
        self,
        result: DesktopActionResult,
        request: DesktopActionRequest,
    ) -> DesktopActionResult:
        rollback_hint = _rollback_hint_for_action(
            action=request.action,
            target=request.target,
        )
        metadata = dict(result.metadata)
        metadata["rollback_hint"] = rollback_hint
        metadata["mutating"] = request.action in MUTATING_DESKTOP_ACTIONS
        return replace(result, metadata=metadata)


class MacOSDesktopActionAdapter:
    def __init__(
        self,
        provider_name: str = "macos-desktop",
        *,
        which_resolver: Any = None,
        run_command: Any = None,
        popen_command: Any = None,
    ) -> None:
        self.provider_name = str(provider_name or "macos-desktop").strip() or "macos-desktop"
        self._which = which_resolver or shutil.which
        self._run = run_command or subprocess.run
        self._popen = popen_command or subprocess.Popen

    def describe(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "kind": "macos",
            "platform": sys.platform,
            "actions": list(SUPPORTED_DESKTOP_ACTIONS),
            "commands": {
                "osascript": bool(self._which("osascript")),
                "pbcopy": bool(self._which("pbcopy")),
                "pbpaste": bool(self._which("pbpaste")),
                "open": bool(self._which("open")),
            },
            "supports_real_desktop": True,
            "staging_surface": True,
        }

    def execute(self, request: DesktopActionRequest) -> DesktopActionResult:
        try:
            if request.action == "notify":
                result = self._notify(request)
                return self._attach_action_context(result, request)
            if request.action == "clipboard_read":
                result = self._clipboard_read(request)
                return self._attach_action_context(result, request)
            if request.action == "clipboard_write":
                result = self._clipboard_write(request)
                return self._attach_action_context(result, request)
            if request.action == "app_launch":
                result = self._app_launch(request)
                return self._attach_action_context(result, request)
            if request.action == "window_list":
                result = self._window_list(request)
                return self._attach_action_context(result, request)
            if request.action == "window_focus":
                result = self._window_focus(request)
                return self._attach_action_context(result, request)
            if request.action == "window_close":
                result = self._window_close(request)
                return self._attach_action_context(result, request)
            return self._attach_action_context(self._failed(request.action, "unsupported_action"), request)
        except subprocess.TimeoutExpired:
            return self._attach_action_context(
                self._failed(request.action, f"desktop command timeout ({request.timeout_sec}s)"),
                request,
            )
        except Exception as exc:
            return self._attach_action_context(self._failed(request.action, str(exc)), request)

    def _notify(self, request: DesktopActionRequest) -> DesktopActionResult:
        osascript = self._which("osascript")
        if not osascript:
            return self._unavailable(request.action, "osascript is not available on this host")
        title = request.title or "Amaryllis"
        body = request.message or request.text or ""
        script = (
            f'display notification "{_escape_applescript_string(body)}" '
            f'with title "{_escape_applescript_string(title)}"'
        )
        completed = self._run(
            [osascript, "-e", script],
            capture_output=True,
            text=True,
            timeout=request.timeout_sec,
            check=False,
        )
        if int(completed.returncode) != 0:
            return self._failed(request.action, (completed.stderr or "").strip() or "osascript notify failed")
        return DesktopActionResult(
            ok=True,
            provider=self.provider_name,
            action=request.action,
            status="succeeded",
            data={
                "title": title,
                "message": body,
                "command": [osascript, "-e", script],
            },
        )

    def _clipboard_write(self, request: DesktopActionRequest) -> DesktopActionResult:
        pbcopy = self._which("pbcopy")
        if not pbcopy:
            return self._unavailable(request.action, "pbcopy is not available on this host")
        text = str(request.text or "")
        completed = self._run(
            [pbcopy],
            capture_output=True,
            text=True,
            input=text,
            timeout=request.timeout_sec,
            check=False,
        )
        if int(completed.returncode) != 0:
            return self._failed(request.action, (completed.stderr or "").strip() or "pbcopy failed")
        return DesktopActionResult(
            ok=True,
            provider=self.provider_name,
            action=request.action,
            status="succeeded",
            data={
                "written_chars": len(text),
                "command": [pbcopy],
            },
        )

    def _clipboard_read(self, request: DesktopActionRequest) -> DesktopActionResult:
        pbpaste = self._which("pbpaste")
        if not pbpaste:
            return self._unavailable(request.action, "pbpaste is not available on this host")
        completed = self._run(
            [pbpaste],
            capture_output=True,
            text=True,
            timeout=request.timeout_sec,
            check=False,
        )
        if int(completed.returncode) != 0:
            return self._failed(request.action, (completed.stderr or "").strip() or "pbpaste failed")
        content = str(completed.stdout or "")
        truncated = False
        if len(content) > MAX_CLIPBOARD_RESPONSE_CHARS:
            content = content[:MAX_CLIPBOARD_RESPONSE_CHARS]
            truncated = True
        return DesktopActionResult(
            ok=True,
            provider=self.provider_name,
            action=request.action,
            status="succeeded",
            data={
                "content": content,
                "truncated": truncated,
                "command": [pbpaste],
            },
        )

    def _app_launch(self, request: DesktopActionRequest) -> DesktopActionResult:
        target = str(request.target or "").strip()
        if not target:
            return self._failed(request.action, "target is required")
        open_command = self._which("open")
        if not open_command:
            return self._unavailable(request.action, "open is not available on this host")

        command = self._launch_command(open_command, target)
        process = self._popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return DesktopActionResult(
            ok=True,
            provider=self.provider_name,
            action=request.action,
            status="succeeded",
            data={
                "target": target,
                "pid": int(getattr(process, "pid", 0) or 0),
                "command": list(command),
            },
        )

    def _window_list(self, request: DesktopActionRequest) -> DesktopActionResult:
        osascript = self._which("osascript")
        if not osascript:
            return self._unavailable(request.action, "osascript is not available on this host")
        script = 'tell application "System Events" to get name of every application process whose background only is false'
        completed = self._run(
            [osascript, "-e", script],
            capture_output=True,
            text=True,
            timeout=request.timeout_sec,
            check=False,
        )
        if int(completed.returncode) != 0:
            return self._failed(
                request.action,
                (completed.stderr or "").strip() or "osascript window listing failed",
            )

        apps = _parse_macos_application_list(str(completed.stdout or ""))
        windows = [
            {
                "window_id": app_name,
                "desktop": None,
                "host": "macos",
                "title": app_name,
            }
            for app_name in apps
        ]
        return DesktopActionResult(
            ok=True,
            provider=self.provider_name,
            action=request.action,
            status="succeeded",
            data={
                "windows": windows,
                "count": len(windows),
                "command": [osascript, "-e", script],
                "mode": "app_processes",
            },
        )

    def _window_focus(self, request: DesktopActionRequest) -> DesktopActionResult:
        target = str(request.target or "").strip()
        if not target:
            return self._failed(request.action, "target is required")
        osascript = self._which("osascript")
        if not osascript:
            return self._unavailable(request.action, "osascript is not available on this host")
        script = f'tell application "{_escape_applescript_string(target)}" to activate'
        completed = self._run(
            [osascript, "-e", script],
            capture_output=True,
            text=True,
            timeout=request.timeout_sec,
            check=False,
        )
        if int(completed.returncode) != 0:
            return self._failed(request.action, (completed.stderr or "").strip() or "window focus failed")
        return DesktopActionResult(
            ok=True,
            provider=self.provider_name,
            action=request.action,
            status="succeeded",
            data={
                "target": target,
                "command": [osascript, "-e", script],
            },
        )

    def _window_close(self, request: DesktopActionRequest) -> DesktopActionResult:
        target = str(request.target or "").strip()
        if not target:
            return self._failed(request.action, "target is required")
        osascript = self._which("osascript")
        if not osascript:
            return self._unavailable(request.action, "osascript is not available on this host")
        escaped = _escape_applescript_string(target)
        script = (
            'tell application "System Events" to tell process '
            f'"{escaped}" to if (count of windows) > 0 then close front window'
        )
        completed = self._run(
            [osascript, "-e", script],
            capture_output=True,
            text=True,
            timeout=request.timeout_sec,
            check=False,
        )
        if int(completed.returncode) != 0:
            return self._failed(request.action, (completed.stderr or "").strip() or "window close failed")
        return DesktopActionResult(
            ok=True,
            provider=self.provider_name,
            action=request.action,
            status="succeeded",
            data={
                "target": target,
                "command": [osascript, "-e", script],
            },
        )

    def _launch_command(self, open_command: str, target: str) -> list[str]:
        if target.startswith("/") or target.startswith("~") or "://" in target or target.endswith(".app"):
            return [open_command, target]
        if _looks_like_bundle_id(target):
            return [open_command, "-b", target]
        return [open_command, "-a", target]

    def _unavailable(self, action: str, reason: str) -> DesktopActionResult:
        return DesktopActionResult(
            ok=False,
            provider=self.provider_name,
            action=action,
            status="unavailable",
            message=reason,
            warnings=[reason],
            metadata={"platform": sys.platform},
        )

    def _failed(self, action: str, reason: str) -> DesktopActionResult:
        return DesktopActionResult(
            ok=False,
            provider=self.provider_name,
            action=action,
            status="failed",
            message=reason,
            metadata={"platform": sys.platform},
        )

    def _attach_action_context(
        self,
        result: DesktopActionResult,
        request: DesktopActionRequest,
    ) -> DesktopActionResult:
        rollback_hint = _rollback_hint_for_action(
            action=request.action,
            target=request.target,
        )
        metadata = dict(result.metadata)
        metadata["rollback_hint"] = rollback_hint
        metadata["mutating"] = request.action in MUTATING_DESKTOP_ACTIONS
        return replace(result, metadata=metadata)


def create_default_desktop_action_adapter(*, platform_name: str | None = None) -> DesktopActionAdapter:
    normalized = str(platform_name or sys.platform).strip().lower()
    if normalized.startswith("linux"):
        return LinuxDesktopActionAdapter()
    if normalized.startswith("darwin"):
        return MacOSDesktopActionAdapter()
    return StubDesktopActionAdapter(provider_name=f"stub-desktop-{normalized}")


def register_desktop_action_tool(
    registry: ToolRegistry,
    adapter: DesktopActionAdapter,
    *,
    tool_name: str = "desktop_action",
    replace_existing: bool = False,
) -> bool:
    if registry.get(tool_name) is not None and not replace_existing:
        return False

    def _handler(arguments: dict[str, Any]) -> dict[str, Any]:
        request = DesktopActionRequest.from_arguments(arguments or {})
        result = adapter.execute(request)
        result = _enrich_result_with_action_context(result=result, request=request)
        payload = result.to_dict()
        payload["adapter"] = adapter.describe()
        payload["request"] = request.to_dict()
        return payload

    registry.register(
        name=tool_name,
        description=(
            "Desktop action adapter (Linux primary + macOS staging, stub fallback) "
            "for notifications, clipboard, app launch, and window controls under policy guardrails."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": list(SUPPORTED_DESKTOP_ACTIONS),
                },
                "title": {"type": "string"},
                "message": {"type": "string"},
                "text": {"type": "string"},
                "target": {"type": "string"},
                "timeout_sec": {"type": "integer", "minimum": 1, "maximum": MAX_TIMEOUT_SEC},
                "metadata": {"type": "object", "additionalProperties": True},
            },
            "required": ["action"],
            "additionalProperties": True,
        },
        handler=_handler,
        source="local",
        risk_level="medium",
        approval_mode="conditional",
        approval_predicate=lambda args: str(args.get("action", "")).strip().lower() in MUTATING_DESKTOP_ACTIONS,
        isolation="desktop_local",
    )
    return True


def _looks_like_desktop_id(value: str) -> bool:
    target = str(value or "").strip()
    if not target:
        return False
    return "/" not in target and "://" not in target and " " not in target


def _looks_like_bundle_id(value: str) -> bool:
    target = str(value or "").strip()
    if not target:
        return False
    return "." in target and "/" not in target and "://" not in target and " " not in target


def _parse_wmctrl_lines(payload: str) -> list[dict[str, Any]]:
    windows: list[dict[str, Any]] = []
    for line in str(payload or "").splitlines():
        raw = line.strip()
        if not raw:
            continue
        parts = raw.split(maxsplit=3)
        if not parts:
            continue
        window = {
            "window_id": parts[0],
            "desktop": parts[1] if len(parts) > 1 else None,
            "host": parts[2] if len(parts) > 2 else None,
            "title": parts[3] if len(parts) > 3 else "",
        }
        windows.append(window)
    return windows


def _parse_macos_application_list(payload: str) -> list[str]:
    apps: list[str] = []
    for token in str(payload or "").replace("\n", ",").split(","):
        app_name = token.strip()
        if not app_name:
            continue
        if app_name in apps:
            continue
        apps.append(app_name)
    return apps


def _escape_applescript_string(value: str) -> str:
    return str(value or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _rollback_hint_for_action(*, action: str, target: str | None) -> str:
    normalized = str(action or "").strip().lower()
    if normalized == "notify":
        return "Send a follow-up notification clarifying or correcting the previous message."
    if normalized == "clipboard_write":
        return (
            "If needed, restore previous clipboard content by writing back the saved value "
            "(capture it with clipboard_read before mutating)."
        )
    if normalized == "app_launch":
        return "Close the launched application/window if unintended."
    if normalized == "window_focus":
        target_hint = f" (target: {target})" if str(target or "").strip() else ""
        return f"Refocus the previously active window if focus changed unexpectedly{target_hint}."
    if normalized == "window_close":
        target_hint = f" (target: {target})" if str(target or "").strip() else ""
        return f"Reopen the closed application/window from launcher or session restore{target_hint}."
    if normalized in {"clipboard_read", "window_list"}:
        return "Read-only action; no rollback required."
    return "Review action impact and restore prior desktop state if needed."


def _enrich_result_with_action_context(
    *,
    result: DesktopActionResult,
    request: DesktopActionRequest,
) -> DesktopActionResult:
    rollback_hint = _rollback_hint_for_action(
        action=request.action,
        target=request.target,
    )
    metadata = dict(result.metadata)
    metadata["rollback_hint"] = rollback_hint
    metadata["mutating"] = request.action in MUTATING_DESKTOP_ACTIONS
    return replace(result, metadata=metadata)


def _optional_str(value: Any) -> str | None:
    normalized = str(value).strip() if value not in (None, "") else ""
    return normalized or None


def _normalize_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = int(default)
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed
