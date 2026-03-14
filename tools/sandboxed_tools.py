from __future__ import annotations

import ast
import contextlib
import html
import io
import re
import signal
from pathlib import Path
from typing import Any

import httpx


RESULT_PATTERN = re.compile(
    r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>'
)

MAX_FILESYSTEM_READ_BYTES = 1_048_576
MAX_FILESYSTEM_WRITE_BYTES = 262_144
MAX_PYTHON_OUTPUT_CHARS = 20_000
DEFAULT_PYTHON_TIMEOUT_SEC = 8
DEFAULT_PYTHON_MAX_CODE_CHARS = 4_000

FORBIDDEN_PYTHON_NAMES = {
    "__import__",
    "compile",
    "delattr",
    "eval",
    "exec",
    "getattr",
    "globals",
    "help",
    "input",
    "locals",
    "open",
    "setattr",
    "vars",
}


class SandboxTimeoutError(RuntimeError):
    pass


def execute_builtin_tool(
    *,
    name: str,
    arguments: dict[str, Any],
    allow_network: bool,
    allowed_roots: list[str],
    filesystem_allow_write: bool,
    max_timeout_sec: int,
    max_code_chars: int,
) -> Any:
    normalized = str(name or "").strip().lower()
    if normalized == "filesystem":
        return _filesystem_handler(
            arguments=arguments,
            allowed_roots=allowed_roots,
            allow_write=filesystem_allow_write,
        )
    if normalized == "web_search":
        return _web_search_handler(arguments=arguments, allow_network=allow_network)
    if normalized == "python_exec":
        return _python_exec_handler(
            arguments=arguments,
            max_timeout_sec=max_timeout_sec,
            max_code_chars=max_code_chars,
        )
    raise ValueError(f"Unsupported builtin tool for sandbox: {name}")


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


def _safe_path(raw_path: str, roots: list[Path]) -> Path:
    incoming = Path(str(raw_path or "").strip()).expanduser()
    candidate = incoming if incoming.is_absolute() else roots[0] / incoming
    resolved = candidate.resolve()
    for root in roots:
        try:
            resolved.relative_to(root)
            return resolved
        except Exception:
            continue
    allowed = ", ".join(str(item) for item in roots)
    raise PermissionError(f"Path is outside sandbox roots: {resolved}. allowed_roots={allowed}")


def _filesystem_handler(
    *,
    arguments: dict[str, Any],
    allowed_roots: list[str],
    allow_write: bool,
) -> dict[str, Any]:
    roots = _normalize_roots(allowed_roots)
    action = str(arguments.get("action", "")).strip().lower()
    path_raw = str(arguments.get("path", ".")).strip()
    target = _safe_path(path_raw, roots)

    if target.exists() and target.is_symlink():
        raise PermissionError(f"Symlinks are not allowed in sandbox: {target}")

    if action == "list":
        if not target.exists() or not target.is_dir():
            raise FileNotFoundError(f"Directory not found: {target}")
        items = [item.name for item in sorted(target.iterdir())]
        return {"items": items}

    if action == "read":
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(f"File not found: {target}")
        size = target.stat().st_size
        if size > MAX_FILESYSTEM_READ_BYTES:
            raise ValueError(f"File is too large to read ({size} > {MAX_FILESYSTEM_READ_BYTES} bytes)")
        return {"content": target.read_text(encoding="utf-8")}

    if action == "write":
        if not allow_write:
            raise PermissionError("filesystem write is disabled in sandbox")
        content = str(arguments.get("content", ""))
        payload = content.encode("utf-8")
        if len(payload) > MAX_FILESYSTEM_WRITE_BYTES:
            raise ValueError(
                f"Content is too large to write ({len(payload)} > {MAX_FILESYSTEM_WRITE_BYTES} bytes)"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"written": True, "path": str(target)}

    raise ValueError("Unsupported action. Use one of: list, read, write")


def _web_search_handler(*, arguments: dict[str, Any], allow_network: bool) -> dict[str, Any]:
    if not allow_network:
        raise PermissionError("Network access is blocked by sandbox policy")
    query = str(arguments.get("query", "")).strip()
    limit = int(arguments.get("limit", 5))
    if not query:
        raise ValueError("query is required")

    response = httpx.get("https://duckduckgo.com/html/", params={"q": query}, timeout=15.0)
    response.raise_for_status()

    matches = RESULT_PATTERN.findall(response.text)
    results: list[dict[str, str]] = []
    for href, title_html in matches[: max(1, limit)]:
        title = html.unescape(re.sub(r"<[^>]+>", "", title_html)).strip()
        results.append({"title": title, "url": href})
    return {"query": query, "results": results}


def _python_exec_handler(
    *,
    arguments: dict[str, Any],
    max_timeout_sec: int,
    max_code_chars: int,
) -> dict[str, Any]:
    code = str(arguments.get("code", "")).strip()
    timeout = int(arguments.get("timeout", DEFAULT_PYTHON_TIMEOUT_SEC))
    effective_timeout = max(1, min(timeout, max(1, max_timeout_sec)))
    effective_max_chars = max(100, max_code_chars)

    if not code:
        raise ValueError("code is required")
    if len(code) > effective_max_chars:
        raise ValueError(f"code is too large ({len(code)} > {effective_max_chars})")

    tree = ast.parse(code, mode="exec")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            raise ValueError("import statements are not allowed in sandboxed python_exec")
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_PYTHON_NAMES:
            raise ValueError(f"forbidden symbol in sandboxed python_exec: {node.id}")
        if isinstance(node, ast.Attribute) and str(node.attr).startswith("__"):
            raise ValueError("dunder attribute access is not allowed in sandboxed python_exec")

    safe_builtins: dict[str, Any] = {
        "abs": abs,
        "all": all,
        "any": any,
        "bool": bool,
        "dict": dict,
        "enumerate": enumerate,
        "float": float,
        "int": int,
        "len": len,
        "list": list,
        "max": max,
        "min": min,
        "print": print,
        "range": range,
        "reversed": reversed,
        "round": round,
        "set": set,
        "sorted": sorted,
        "str": str,
        "sum": sum,
        "tuple": tuple,
        "zip": zip,
    }

    stdout_buffer = io.StringIO()
    stderr_buffer = io.StringIO()
    returncode = 0

    def _timeout_handler(_signum: int, _frame: Any) -> None:
        raise SandboxTimeoutError(f"python_exec timed out after {effective_timeout} seconds")

    old_handler = None
    alarm_supported = hasattr(signal, "SIGALRM")
    try:
        if alarm_supported:
            old_handler = signal.getsignal(signal.SIGALRM)
            signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(effective_timeout)

        with contextlib.redirect_stdout(stdout_buffer), contextlib.redirect_stderr(stderr_buffer):
            exec(compile(tree, "<sandbox-python-exec>", "exec"), {"__builtins__": safe_builtins}, {})
    except Exception as exc:
        returncode = 1
        stderr_buffer.write(str(exc))
    finally:
        if alarm_supported:
            signal.alarm(0)
            if old_handler is not None:
                signal.signal(signal.SIGALRM, old_handler)

    stdout = _truncate(stdout_buffer.getvalue())
    stderr = _truncate(stderr_buffer.getvalue())
    truncated = (
        len(stdout_buffer.getvalue()) > MAX_PYTHON_OUTPUT_CHARS
        or len(stderr_buffer.getvalue()) > MAX_PYTHON_OUTPUT_CHARS
    )
    return {
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": truncated,
    }


def _truncate(value: str) -> str:
    if len(value) <= MAX_PYTHON_OUTPUT_CHARS:
        return value
    return value[:MAX_PYTHON_OUTPUT_CHARS] + "\n...[truncated]..."
