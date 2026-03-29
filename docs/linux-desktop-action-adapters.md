# Desktop Action Adapters (Linux + macOS Staging)

## Purpose

Provide policy-gated desktop control primitives for "Amaryllis on PC" workflows:

- notifications
- clipboard read/write
- app launch
- window list
- window focus/close

## Tool Contract

Tool name: `desktop_action`

Supported actions:

- `notify`
- `clipboard_read`
- `clipboard_write`
- `app_launch`
- `window_list`
- `window_focus`
- `window_close`

Request fields:

- `action` (required)
- `title` (optional, for `notify`)
- `message` (optional, for `notify`)
- `text` (optional; required for `clipboard_write`)
- `target` (optional; required for `app_launch`)
  - required for `app_launch`, `window_focus`, `window_close`
- `timeout_sec` (optional)
- `metadata` (optional)

## Runtime Behavior

- Linux hosts use `LinuxDesktopActionAdapter`.
- macOS hosts use `MacOSDesktopActionAdapter` (staging parity surface).
- other hosts use `StubDesktopActionAdapter` for non-supported platforms.

Command usage on Linux:

- notifications: `notify-send`
- clipboard write: `wl-copy` -> `xclip` -> `xsel`
- clipboard read: `wl-paste` -> `xclip` -> `xsel`
- app launch: `gtk-launch` (desktop id) or `xdg-open` fallback
- window list: `wmctrl -l`
- window focus: `wmctrl -ia <window_id>`
- window close: `wmctrl -ic <window_id>`

When required system commands are missing, tool returns `status=unavailable` with explicit reason.

Command usage on macOS (staging):

- notifications: `osascript` (`display notification`)
- clipboard write: `pbcopy`
- clipboard read: `pbpaste`
- app launch: `open` (`-b <bundle_id>` for bundle ids, `-a <app>` for app names, direct path/url passthrough)
- window list/focus/close: `osascript` (application-process-level control)

Each result includes metadata rollback guidance (`metadata.rollback_hint`) so UI and audit can expose recovery steps.

## Policy and Trust Boundary

- Registered as `risk_level=medium`, `approval_mode=conditional`.
- Conditional approval applies to mutating actions:
- `notify`
- `clipboard_write`
- `app_launch`
- `window_focus`
- `window_close`
- Existing autonomy and isolation guardrails remain active.

## Invocation

Use existing invoke surface:

- `POST /mcp/tools/desktop_action/invoke`

Example payload:

```json
{
  "user_id": "user-001",
  "session_id": "session-001",
  "arguments": {
    "action": "clipboard_read"
  }
}
```

## Release/Nightly Gate

Contract gate:
- `scripts/release/desktop_action_rollback_gate.py`

The gate validates:
- desktop tool registration and policy metadata,
- rollback-hint coverage for mutating actions,
- terminal receipt rollback metadata persistence.
