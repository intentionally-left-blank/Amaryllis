#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Validate Linux desktop-action surface and rollback-hint contract "
            "(docs + adapter behavior + runtime policy/receipt checks)."
        )
    )
    parser.add_argument(
        "--desktop-doc",
        default="docs/linux-desktop-action-adapters.md",
        help="Path to desktop action adapter documentation.",
    )
    parser.add_argument(
        "--token",
        default="dev-token",
        help="Auth token used for runtime checks.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional JSON report output path.",
    )
    return parser.parse_args()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _resolve_path(repo_root: Path, raw: str) -> Path:
    path = Path(str(raw)).expanduser()
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def _shutdown_app(app: object) -> None:
    services = getattr(getattr(app, "state", None), "services", None)
    if services is None:
        return
    try:
        services.automation_scheduler.stop()
        if services.memory_consolidation_worker is not None:
            services.memory_consolidation_worker.stop()
        if services.backup_scheduler is not None:
            services.backup_scheduler.stop()
        services.agent_run_manager.stop()
        services.database.close()
        services.vector_store.persist()
    except Exception:
        pass


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _is_json_response(headers: dict[str, Any]) -> bool:
    return str(headers.get("content-type") or "").startswith("application/json")


class _Completed:
    def __init__(self, *, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = int(returncode)
        self.stdout = str(stdout)
        self.stderr = str(stderr)


class _PopenResult:
    def __init__(self, pid: int) -> None:
        self.pid = int(pid)


class _LinuxHarness:
    def __init__(self) -> None:
        self.clipboard_value = ""
        self._next_pid = 2000

    def which(self, name: str) -> str | None:
        mapping = {
            "notify-send": "/usr/bin/notify-send",
            "wl-copy": "/usr/bin/wl-copy",
            "wl-paste": "/usr/bin/wl-paste",
            "xdg-open": "/usr/bin/xdg-open",
            "gtk-launch": "/usr/bin/gtk-launch",
            "wmctrl": "/usr/bin/wmctrl",
        }
        return mapping.get(str(name))

    def run(self, command: list[str], **kwargs: Any) -> _Completed:
        cmd = list(command)
        if not cmd:
            return _Completed(returncode=1, stderr="empty command")
        if cmd[:1] == ["/usr/bin/notify-send"]:
            return _Completed(returncode=0)
        if cmd[:1] == ["/usr/bin/wl-copy"]:
            self.clipboard_value = str(kwargs.get("input") or "")
            return _Completed(returncode=0)
        if cmd[:2] == ["/usr/bin/wl-paste", "--no-newline"]:
            return _Completed(returncode=0, stdout=self.clipboard_value)
        if cmd[:2] == ["/usr/bin/wmctrl", "-l"]:
            return _Completed(returncode=0, stdout="0x03e00007  0 host Terminal\n")
        if cmd[:2] == ["/usr/bin/wmctrl", "-ia"]:
            return _Completed(returncode=0)
        if cmd[:2] == ["/usr/bin/wmctrl", "-ic"]:
            return _Completed(returncode=0)
        return _Completed(returncode=1, stderr=f"unsupported command: {' '.join(cmd)}")

    def popen(self, command: list[str], **kwargs: Any) -> _PopenResult:
        _ = (command, kwargs)
        self._next_pid += 1
        return _PopenResult(pid=self._next_pid)


def main() -> int:
    args = _parse_args()
    repo_root = Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    desktop_doc = _resolve_path(repo_root, str(args.desktop_doc))
    token = str(args.token).strip() or "dev-token"

    checks: list[dict[str, Any]] = []

    def add_check(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    if desktop_doc.exists():
        text = desktop_doc.read_text(encoding="utf-8")
        add_check("desktop_doc_exists", True, str(desktop_doc))
        add_check("desktop_doc_tool_name", "desktop_action" in text, "tool name documented")
        add_check(
            "desktop_doc_actions",
            all(
                action in text
                for action in (
                    "notify",
                    "clipboard_read",
                    "clipboard_write",
                    "app_launch",
                    "window_list",
                    "window_focus",
                    "window_close",
                )
            ),
            "all desktop actions documented",
        )
        add_check(
            "desktop_doc_policy_guardrails",
            "approval_mode=conditional" in text or "approval_mode" in text,
            "policy guardrails documented",
        )
        add_check(
            "desktop_doc_rollback_hint",
            "rollback_hint" in text and "metadata.rollback_hint" in text,
            "rollback hints documented",
        )
    else:
        add_check("desktop_doc_exists", False, f"missing: {desktop_doc}")

    try:
        from tools.desktop_action_adapter import (  # noqa: PLC0415
            DesktopActionRequest,
            LinuxDesktopActionAdapter,
            _rollback_hint_for_action,
        )

        harness = _LinuxHarness()
        adapter = LinuxDesktopActionAdapter(
            which_resolver=harness.which,
            run_command=harness.run,
            popen_command=harness.popen,
        )
        describe = adapter.describe()
        add_check(
            "adapter_linux_kind",
            str(describe.get("kind")) == "linux",
            f"kind={describe.get('kind')}",
        )
        add_check(
            "adapter_linux_supports_real_desktop",
            bool(describe.get("supports_real_desktop")),
            f"supports_real_desktop={describe.get('supports_real_desktop')}",
        )

        mutating_cases = [
            {"action": "notify", "message": "gate"},
            {"action": "clipboard_write", "text": "gate-text"},
            {"action": "app_launch", "target": "org.gnome.Nautilus.desktop"},
            {"action": "window_focus", "target": "0x03e00007"},
            {"action": "window_close", "target": "0x03e00007"},
        ]
        for case in mutating_cases:
            request = DesktopActionRequest.from_arguments(case)
            result = adapter.execute(request)
            metadata = dict(result.metadata or {})
            rollback_hint = str(metadata.get("rollback_hint") or "").strip()
            add_check(
                f"adapter_mutating_{request.action}_ok",
                bool(result.ok),
                f"status={result.status}",
            )
            add_check(
                f"adapter_mutating_{request.action}_hint_present",
                bool(rollback_hint),
                f"rollback_hint={rollback_hint}",
            )
            add_check(
                f"adapter_mutating_{request.action}_mutating_flag",
                bool(metadata.get("mutating")) is True,
                f"mutating={metadata.get('mutating')}",
            )
            add_check(
                f"adapter_mutating_{request.action}_hint_not_read_only",
                "read-only" not in rollback_hint.lower(),
                f"rollback_hint={rollback_hint}",
            )

        read_request = DesktopActionRequest.from_arguments({"action": "clipboard_read"})
        read_result = adapter.execute(read_request)
        read_metadata = dict(read_result.metadata or {})
        add_check(
            "adapter_read_clipboard_ok",
            bool(read_result.ok),
            f"status={read_result.status}",
        )
        add_check(
            "adapter_read_clipboard_mutating_flag_false",
            bool(read_metadata.get("mutating")) is False,
            f"mutating={read_metadata.get('mutating')}",
        )
        add_check(
            "adapter_read_clipboard_hint_present",
            bool(str(read_metadata.get("rollback_hint") or "").strip()),
            f"rollback_hint={read_metadata.get('rollback_hint')}",
        )

        focus_hint_1 = _rollback_hint_for_action(action="window_focus", target="0x03e00007")
        focus_hint_2 = _rollback_hint_for_action(action="window_focus", target="0x03e00007")
        add_check(
            "adapter_hint_determinism_window_focus",
            str(focus_hint_1) == str(focus_hint_2),
            f"hint_1={focus_hint_1}",
        )
    except Exception as exc:  # pragma: no cover - fallback diagnostics for CI
        add_check("adapter_contract_execution", False, f"{type(exc).__name__}: {exc}")

    tmp_dir = tempfile.TemporaryDirectory(prefix="amaryllis-desktop-action-rollback-gate-")
    support_dir = Path(tmp_dir.name) / "support"
    app = None

    os.environ["AMARYLLIS_AUTH_ENABLED"] = "true"
    os.environ["AMARYLLIS_AUTH_TOKENS"] = json.dumps(
        {
            token: {"user_id": "desktop-user", "scopes": ["user"]},
            "desktop-admin-token": {"user_id": "desktop-admin", "scopes": ["admin", "user"]},
            "desktop-other-token": {"user_id": "desktop-other", "scopes": ["user"]},
            "desktop-service-token": {"user_id": "desktop-service", "scopes": ["service"]},
        },
        ensure_ascii=False,
    )
    os.environ["AMARYLLIS_SUPPORT_DIR"] = str(support_dir)
    os.environ["AMARYLLIS_MEMORY_CONSOLIDATION_ENABLED"] = "false"
    os.environ["AMARYLLIS_MCP_ENDPOINTS"] = ""
    os.environ["AMARYLLIS_SECURITY_PROFILE"] = "production"
    os.environ["AMARYLLIS_COGNITION_BACKEND"] = "deterministic"
    os.environ["AMARYLLIS_AUTOMATION_ENABLED"] = "false"
    os.environ["AMARYLLIS_BACKUP_ENABLED"] = "false"
    os.environ["AMARYLLIS_BACKUP_RESTORE_DRILL_ENABLED"] = "false"
    os.environ["AMARYLLIS_REQUEST_TRACE_LOGS_ENABLED"] = "false"

    try:
        from fastapi.testclient import TestClient  # noqa: PLC0415
        from runtime.server import create_app  # noqa: PLC0415

        app = create_app()
        with TestClient(app) as client:
            tools_resp = client.get("/tools", headers=_auth(token))
            add_check("runtime_tools_endpoint_ok", tools_resp.status_code == 200, f"status={tools_resp.status_code}")
            tools_payload = tools_resp.json() if _is_json_response(dict(tools_resp.headers)) else {}
            items = tools_payload.get("items") if isinstance(tools_payload, dict) else []
            desktop_tool = None
            if isinstance(items, list):
                desktop_tool = next(
                    (item for item in items if isinstance(item, dict) and str(item.get("name") or "") == "desktop_action"),
                    None,
                )
            add_check("runtime_desktop_tool_registered", desktop_tool is not None, "desktop_action is registered")
            if isinstance(desktop_tool, dict):
                add_check(
                    "runtime_desktop_tool_risk_level",
                    str(desktop_tool.get("risk_level") or "") == "medium",
                    f"risk_level={desktop_tool.get('risk_level')}",
                )
                add_check(
                    "runtime_desktop_tool_approval_mode",
                    str(desktop_tool.get("approval_mode") or "") == "conditional",
                    f"approval_mode={desktop_tool.get('approval_mode')}",
                )

            read_session = "desktop-rollback-gate-read"
            read_invoke_resp = client.post(
                "/mcp/tools/desktop_action/invoke",
                headers=_auth(token),
                json={
                    "user_id": "desktop-user",
                    "session_id": read_session,
                    "arguments": {"action": "clipboard_read"},
                },
            )
            add_check(
                "runtime_desktop_read_invoke_ok",
                read_invoke_resp.status_code == 200,
                f"status={read_invoke_resp.status_code}",
            )
            read_invoke_payload = (
                read_invoke_resp.json() if _is_json_response(dict(read_invoke_resp.headers)) else {}
            )
            read_result = read_invoke_payload.get("result") if isinstance(read_invoke_payload, dict) else {}
            adapter_result = (read_result or {}).get("result") if isinstance(read_result, dict) else {}
            metadata = dict((adapter_result or {}).get("metadata") or {})
            add_check(
                "runtime_desktop_read_rollback_hint_present",
                bool(str(metadata.get("rollback_hint") or "").strip()),
                f"rollback_hint={metadata.get('rollback_hint')}",
            )
            add_check(
                "runtime_desktop_read_mutating_flag_false",
                bool(metadata.get("mutating")) is False,
                f"mutating={metadata.get('mutating')}",
            )

            write_invoke_resp = client.post(
                "/mcp/tools/desktop_action/invoke",
                headers=_auth(token),
                json={
                    "user_id": "desktop-user",
                    "session_id": "desktop-rollback-gate-write",
                    "arguments": {"action": "clipboard_write", "text": "gate"},
                },
            )
            add_check(
                "runtime_desktop_write_requires_permission",
                write_invoke_resp.status_code == 403,
                f"status={write_invoke_resp.status_code}",
            )
            write_payload = write_invoke_resp.json() if _is_json_response(dict(write_invoke_resp.headers)) else {}
            write_error_type = str((write_payload.get("error") or {}).get("type") or "").strip()
            add_check(
                "runtime_desktop_write_permission_denied_type",
                write_error_type == "permission_denied",
                f"error_type={write_error_type}",
            )

            receipts_resp = client.get(
                "/tools/actions/terminal",
                headers=_auth(token),
                params={"tool_name": "desktop_action", "session_id": read_session, "limit": 20},
            )
            add_check(
                "runtime_desktop_terminal_receipts_ok",
                receipts_resp.status_code == 200,
                f"status={receipts_resp.status_code}",
            )
            receipts_payload = receipts_resp.json() if _is_json_response(dict(receipts_resp.headers)) else {}
            receipt_items = receipts_payload.get("items") if isinstance(receipts_payload, dict) else []
            add_check(
                "runtime_desktop_terminal_receipt_present",
                isinstance(receipt_items, list) and len(receipt_items) >= 1,
                f"receipt_count={len(receipt_items) if isinstance(receipt_items, list) else 'n/a'}",
            )
            if isinstance(receipt_items, list) and receipt_items:
                first = receipt_items[0] if isinstance(receipt_items[0], dict) else {}
                add_check(
                    "runtime_desktop_terminal_receipt_rollback_hint_present",
                    bool(str(first.get("rollback_hint") or "").strip()),
                    f"rollback_hint={first.get('rollback_hint')}",
                )
    except Exception as exc:  # pragma: no cover - fallback diagnostics for CI
        add_check("runtime_gate_execution", False, f"{type(exc).__name__}: {exc}")
    finally:
        if app is not None:
            _shutdown_app(app)
        tmp_dir.cleanup()

    failed = [item for item in checks if not bool(item.get("ok"))]
    report = {
        "generated_at": _utc_now_iso(),
        "suite": "desktop_action_rollback_gate_v1",
        "summary": {
            "status": "pass" if not failed else "fail",
            "checks_total": len(checks),
            "checks_failed": len(failed),
            "desktop_doc": str(desktop_doc),
        },
        "checks": checks,
    }

    output_raw = str(args.output or "").strip()
    if output_raw:
        output_path = _resolve_path(repo_root, output_raw)
        _write_json(output_path, report)

    if failed:
        print("[desktop-action-rollback-gate] FAILED")
        for item in failed:
            print(f"- {item.get('name')}: {item.get('detail')}")
        return 1

    print(f"[desktop-action-rollback-gate] OK checks={len(checks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
