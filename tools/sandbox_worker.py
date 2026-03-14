from __future__ import annotations

import builtins
import contextlib
import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import resource
except Exception:  # pragma: no cover
    resource = None  # type: ignore[assignment]

from tools.sandboxed_tools import execute_builtin_tool


def _normalize_roots(raw_roots: list[str]) -> list[Path]:
    roots: list[Path] = []
    for item in raw_roots:
        candidate = Path(str(item).strip()).expanduser()
        if not str(candidate):
            continue
        try:
            roots.append(candidate.resolve())
        except Exception:
            continue
    if not roots:
        roots.append(Path.cwd().resolve())
    return roots


def _is_allowed_path(path: Path, roots: list[Path]) -> bool:
    for root in roots:
        try:
            path.relative_to(root)
            return True
        except Exception:
            continue
    return False


def _install_filesystem_guard(*, roots: list[Path], allow_write: bool) -> None:
    original_open = builtins.open

    def _guarded_open(file: Any, mode: str = "r", *args: Any, **kwargs: Any):  # type: ignore[no-untyped-def]
        candidate = Path(file).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        resolved = candidate.resolve()
        if not _is_allowed_path(resolved, roots):
            raise PermissionError(f"Sandbox file access denied: {resolved}")
        normalized_mode = str(mode or "r").lower()
        wants_write = any(flag in normalized_mode for flag in ("w", "a", "x", "+"))
        if wants_write and not allow_write:
            raise PermissionError("Sandbox write access denied")
        return original_open(resolved, mode, *args, **kwargs)

    builtins.open = _guarded_open  # type: ignore[assignment]


def _install_network_guard() -> None:
    class _BlockedSocket:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise PermissionError("Sandbox network access denied")

    def _blocked(*_args: Any, **_kwargs: Any) -> Any:
        raise PermissionError("Sandbox network access denied")

    socket.socket = _BlockedSocket  # type: ignore[assignment]
    socket.create_connection = _blocked  # type: ignore[assignment]
    socket.getaddrinfo = _blocked  # type: ignore[assignment]


def _install_process_guard() -> None:
    def _blocked(*_args: Any, **_kwargs: Any) -> Any:
        raise PermissionError("Sandbox subprocess execution denied")

    subprocess.Popen = _blocked  # type: ignore[assignment]
    subprocess.run = _blocked  # type: ignore[assignment]
    subprocess.call = _blocked  # type: ignore[assignment]
    subprocess.check_call = _blocked  # type: ignore[assignment]
    subprocess.check_output = _blocked  # type: ignore[assignment]
    os.system = _blocked  # type: ignore[assignment]


def _apply_limits(limits: dict[str, Any]) -> None:
    if resource is None:
        return
    cpu_sec = max(1, int(limits.get("max_cpu_sec", 1)))
    memory_mb = max(64, int(limits.get("max_memory_mb", 256)))

    try:
        resource.setrlimit(resource.RLIMIT_CPU, (cpu_sec, cpu_sec))
    except Exception:
        pass
    try:
        mem_bytes = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (mem_bytes, mem_bytes))
    except Exception:
        pass
    try:
        resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    except Exception:
        pass


def _execute_plugin(
    *,
    target: dict[str, Any],
    arguments: dict[str, Any],
    context: dict[str, Any],
) -> Any:
    tool_path = Path(str(target.get("tool_path") or "")).expanduser().resolve()
    if not tool_path.is_file():
        raise RuntimeError(f"Plugin tool.py not found: {tool_path}")
    entrypoint = str(target.get("entrypoint") or "execute").strip() or "execute"
    spec = importlib.util.spec_from_file_location("amaryllis_sandbox_plugin", tool_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Cannot build plugin module spec in sandbox")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fn = getattr(module, entrypoint, None)
    if not callable(fn):
        raise RuntimeError(f"Plugin entrypoint '{entrypoint}' is missing or not callable")
    try:
        return fn(arguments, context)
    except TypeError:
        return fn(arguments)


def _execute_target(payload: dict[str, Any]) -> Any:
    target = payload.get("target")
    if not isinstance(target, dict):
        raise RuntimeError("Missing sandbox target")
    arguments = payload.get("arguments")
    if not isinstance(arguments, dict):
        raise RuntimeError("Sandbox arguments must be an object")

    context = payload.get("context")
    if not isinstance(context, dict):
        context = {}
    limits = payload.get("limits")
    if not isinstance(limits, dict):
        limits = {}

    allow_network = bool(limits.get("allow_network", False))
    allow_write = bool(limits.get("filesystem_allow_write", False))
    allowed_roots = _normalize_roots(
        [str(item) for item in limits.get("allowed_roots", []) if str(item).strip()]
    )
    max_timeout_sec = max(1, int(limits.get("max_timeout_sec", 12)))
    max_code_chars = max(100, int(limits.get("max_code_chars", 4000)))

    _apply_limits(limits)
    _install_filesystem_guard(roots=allowed_roots, allow_write=allow_write)
    _install_process_guard()
    if not allow_network:
        _install_network_guard()

    kind = str(target.get("kind") or "").strip().lower()
    if kind == "builtin":
        name = str(target.get("name") or "").strip()
        return execute_builtin_tool(
            name=name,
            arguments=arguments,
            allow_network=allow_network,
            allowed_roots=[str(item) for item in allowed_roots],
            filesystem_allow_write=allow_write,
            max_timeout_sec=max_timeout_sec,
            max_code_chars=max_code_chars,
        )
    if kind == "plugin":
        return _execute_plugin(
            target=target,
            arguments=arguments,
            context=context,
        )
    raise RuntimeError(f"Unsupported sandbox target kind: {kind}")


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"invalid_json:{exc}"}, ensure_ascii=False), end="")
        return 0
    if not isinstance(payload, dict):
        print(json.dumps({"ok": False, "error": "payload_must_be_object"}, ensure_ascii=False), end="")
        return 0

    try:
        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
            result = _execute_target(payload)
        noise_stdout = stdout_buffer.getvalue().strip()
        noise_stderr = stderr_buffer.getvalue().strip()
        if noise_stdout or noise_stderr:
            raise RuntimeError("sandbox target produced unexpected stdout/stderr noise")
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False), end="")
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": str(exc),
                    "error_type": exc.__class__.__name__,
                },
                ensure_ascii=False,
            ),
            end="",
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
