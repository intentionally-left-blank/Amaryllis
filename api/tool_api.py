from __future__ import annotations

import difflib
import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path as FilePath
from typing import Any

from fastapi import APIRouter, Path, Query, Request
from pydantic import BaseModel, Field

from runtime.auth import assert_owner, auth_context_from_request, resolve_user_id
from runtime.errors import AmaryllisError, NotFoundError, PermissionDeniedError, ProviderError, ValidationError
from tools.builtin_tools.filesystem import (
    ALLOWED_ROOTS as FILESYSTEM_ALLOWED_ROOTS,
    MAX_READ_BYTES as FILESYSTEM_MAX_READ_BYTES,
    MAX_WRITE_BYTES as FILESYSTEM_MAX_WRITE_BYTES,
)
from tools.tool_executor import PermissionRequiredError, ToolBudgetLimitError

router = APIRouter(tags=["tools"])
TERMINAL_ACTION_TOOL_NAMES: set[str] = {
    "python_exec",
    "desktop_action",
    "terminal_exec",
    "shell_exec",
    "bash_exec",
    "sh_exec",
    "zsh_exec",
}
FILESYSTEM_PATCH_PREVIEW_DEFAULT_TTL_SEC = 900
FILESYSTEM_PATCH_PREVIEW_MIN_TTL_SEC = 60
FILESYSTEM_PATCH_PREVIEW_MAX_TTL_SEC = 86_400
FILESYSTEM_PATCH_PREVIEW_MAX_DIFF_LINES = 1200
FILESYSTEM_PATCH_PREVIEW_MAX_DIFF_CHARS = 80_000


def _request_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", ""))


def _sign_action(
    request: Request,
    *,
    action: str,
    payload: dict[str, Any],
    actor: str | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    event_type: str = "signed_action",
    status: str = "succeeded",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    services = request.app.state.services
    try:
        return services.security_manager.signed_action(
            action=action,
            payload=payload,
            request_id=_request_id(request),
            actor=actor,
            target_type=target_type,
            target_id=target_id,
            event_type=event_type,
            status=status,
            details=details,
        )
    except Exception:
        return {}


def _normalized_risk_level(raw: str | None) -> str:
    value = str(raw or "").strip().lower()
    if value not in {"low", "medium", "high", "critical"}:
        return "medium"
    return value


def _is_high_risk(risk_level: str) -> bool:
    return risk_level in {"high", "critical"}


def _rollback_hint_for_tool(
    tool_name: str,
    risk_level: str,
    *,
    arguments: dict[str, Any] | None = None,
) -> str:
    name = str(tool_name or "").strip().lower()
    if name == "python_exec":
        return "Review stdout/stderr and revert any filesystem changes introduced by executed code."
    if name == "desktop_action":
        action = str((arguments or {}).get("action", "")).strip().lower()
        if action == "notify":
            return "Send a follow-up notification clarifying or correcting the previous message."
        if action == "clipboard_write":
            return (
                "If needed, restore previous clipboard content by writing back a saved value "
                "captured before mutation."
            )
        if action == "app_launch":
            return "Close the launched application/window if unintended."
        if action == "window_focus":
            return "Refocus the previously active window if focus changed unexpectedly."
        if action == "window_close":
            return "Reopen the closed application/window from launcher or session restore."
        return "Read-only desktop action; no rollback required."
    if name == "filesystem":
        return "Revert changed files from VCS or restore from backup snapshot."
    if risk_level == "critical":
        return "Trigger incident flow, disable related automation, and rollback affected resources."
    return "Review action impact and rollback changed resources from audit trail metadata."


def _is_terminal_action_tool(tool_name: str) -> bool:
    return str(tool_name or "").strip().lower() in TERMINAL_ACTION_TOOL_NAMES


def _terminal_action_context(
    request: Request,
    *,
    tool_name: str,
    risk_level: str,
    action_class: str,
    actor: str | None,
    session_id: str | None,
    permission_id: str | None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not _is_terminal_action_tool(tool_name):
        return None
    services = request.app.state.services
    normalized = _normalized_risk_level(risk_level)
    policy_level = str(services.config.autonomy_level)
    policy = {
        "autonomy_level": policy_level,
        "approval_enforcement_mode": str(services.config.tool_approval_enforcement),
        "isolation_profile": str(services.config.tool_isolation_profile),
    }
    return {
        "terminal_action": True,
        "tool_name": tool_name,
        "high_risk": _is_high_risk(normalized),
        "risk_level": normalized,
        "action_class": str(action_class or "user_initiated"),
        "policy_level": policy_level,
        "policy": policy,
        "rollback_hint": _rollback_hint_for_tool(
            tool_name=tool_name,
            risk_level=normalized,
            arguments=arguments,
        ),
        "actor": actor,
        "session_id": session_id,
        "permission_id": permission_id,
    }


def _high_risk_context(
    request: Request,
    *,
    tool_name: str,
    risk_level: str,
    action_class: str,
    actor: str | None,
    session_id: str | None,
    permission_id: str | None,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    normalized = _normalized_risk_level(risk_level)
    if not _is_high_risk(normalized):
        return None

    services = request.app.state.services
    policy_level = str(services.config.autonomy_level)
    policy = {
        "autonomy_level": policy_level,
        "approval_enforcement_mode": str(services.config.tool_approval_enforcement),
        "isolation_profile": str(services.config.tool_isolation_profile),
    }
    rollback_hint = _rollback_hint_for_tool(
        tool_name=tool_name,
        risk_level=normalized,
        arguments=arguments,
    )
    return {
        "high_risk": True,
        "risk_level": normalized,
        "action_class": str(action_class or "user_initiated"),
        "policy_level": policy_level,
        "policy": policy,
        "rollback_hint": rollback_hint,
        "actor": actor,
        "session_id": session_id,
        "permission_id": permission_id,
    }


class MCPInvokeRequest(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)
    permission_id: str | None = None
    user_id: str | None = None
    session_id: str | None = None


class FilesystemPatchPreviewRequest(BaseModel):
    path: str = Field(..., min_length=1)
    content: str = Field(default="")
    user_id: str | None = None
    session_id: str | None = None
    ttl_sec: int = Field(
        default=FILESYSTEM_PATCH_PREVIEW_DEFAULT_TTL_SEC,
        ge=FILESYSTEM_PATCH_PREVIEW_MIN_TTL_SEC,
        le=FILESYSTEM_PATCH_PREVIEW_MAX_TTL_SEC,
    )


class FilesystemPatchApplyRequest(BaseModel):
    permission_id: str | None = None


class ToolGuardrailsDebugResponse(BaseModel):
    request_id: str
    approval_enforcement_mode: str
    autonomy_policy: dict[str, Any] = Field(default_factory=dict)
    isolation_policy: dict[str, Any]
    sandbox: dict[str, Any] = Field(default_factory=dict)
    budget: dict[str, Any]
    plugin_signing: dict[str, Any] = Field(default_factory=dict)


def _iso_utc_after(seconds: int) -> str:
    ttl = max(1, int(seconds))
    return (datetime.now(timezone.utc) + timedelta(seconds=ttl)).isoformat()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _filesystem_roots_for_request(request: Request) -> list[FilePath]:
    services = request.app.state.services
    roots: list[FilePath] = []

    if bool(getattr(services.config, "tool_sandbox_enabled", False)):
        for item in getattr(services.config, "tool_sandbox_allowed_roots", ()):
            raw = str(item or "").strip()
            if not raw:
                continue
            try:
                roots.append(FilePath(raw).expanduser().resolve())
            except Exception:
                continue

    if not roots:
        for item in FILESYSTEM_ALLOWED_ROOTS:
            raw = str(item or "").strip()
            if not raw:
                continue
            try:
                roots.append(FilePath(raw).expanduser().resolve())
            except Exception:
                continue

    if not roots:
        roots = [FilePath.cwd().resolve()]
    return roots


def _safe_filesystem_path_for_request(request: Request, raw_path: str) -> tuple[FilePath, list[FilePath]]:
    roots = _filesystem_roots_for_request(request)
    incoming = FilePath(str(raw_path or "").strip() or ".").expanduser()
    candidate = incoming.resolve() if incoming.is_absolute() else (roots[0] / incoming).resolve()
    for root in roots:
        try:
            candidate.relative_to(root)
            return candidate, roots
        except Exception:
            continue
    allowed = ", ".join(str(item) for item in roots)
    raise ValidationError(f"Path is outside allowed roots: {candidate}. allowed_roots={allowed}")


def _display_path_for_roots(path: FilePath, roots: list[FilePath]) -> str:
    for root in roots:
        try:
            return str(path.relative_to(root))
        except Exception:
            continue
    return str(path)


def _read_filesystem_preview_baseline(target: FilePath) -> tuple[bool, str, int]:
    if target.exists() and target.is_symlink():
        raise ValidationError(f"Symlinks are not allowed: {target}")
    if not target.exists():
        return False, "", 0
    if not target.is_file():
        raise ValidationError(f"Target is not a file: {target}")
    size = int(target.stat().st_size)
    if size > FILESYSTEM_MAX_READ_BYTES:
        raise ValidationError(f"File is too large to read ({size} > {FILESYSTEM_MAX_READ_BYTES} bytes)")
    try:
        content = target.read_text(encoding="utf-8")
    except Exception as exc:
        raise ValidationError(f"Failed to read file as utf-8: {target}. error={exc}") from exc
    return True, content, size


def _build_structured_diff(*, path_label: str, before: str, after: str) -> dict[str, Any]:
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    raw_lines = list(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=f"{path_label}:before",
            tofile=f"{path_label}:after",
            lineterm="\n",
        )
    )

    added_lines = 0
    removed_lines = 0
    for line in raw_lines:
        if line.startswith("+++ ") or line.startswith("--- "):
            continue
        if line.startswith("+"):
            added_lines += 1
        elif line.startswith("-"):
            removed_lines += 1

    selected_lines: list[str] = []
    char_count = 0
    truncated = False
    for line in raw_lines:
        if len(selected_lines) >= FILESYSTEM_PATCH_PREVIEW_MAX_DIFF_LINES:
            truncated = True
            break
        if char_count + len(line) > FILESYSTEM_PATCH_PREVIEW_MAX_DIFF_CHARS:
            truncated = True
            break
        selected_lines.append(line)
        char_count += len(line)
    if truncated:
        selected_lines.append("... [diff truncated]\n")

    return {
        "format": "unified",
        "text": "".join(selected_lines),
        "truncated": truncated,
        "line_count_total": len(raw_lines),
        "line_count_preview": len(selected_lines),
        "summary": {
            "changed": before != after,
            "before_lines": len(before.splitlines()),
            "after_lines": len(after.splitlines()),
            "added_lines": added_lines,
            "removed_lines": removed_lines,
        },
    }


def _sanitize_patch_preview(item: dict[str, Any]) -> dict[str, Any]:
    payload = dict(item)
    payload.pop("after_content", None)
    return payload


def _load_patch_preview(
    request: Request,
    *,
    preview_id: str,
    include_after_content: bool = False,
) -> dict[str, Any]:
    services = request.app.state.services
    item = services.database.get_filesystem_patch_preview(
        preview_id=preview_id,
        include_after_content=include_after_content,
    )
    if item is None:
        raise NotFoundError(f"Filesystem patch preview not found: {preview_id}")
    return item


@router.get("/tools")
def list_tools(request: Request) -> dict[str, Any]:
    services = request.app.state.services
    items = []
    for tool in services.tool_registry.list():
        items.append(
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "source": tool.source,
                "risk_level": tool.risk_level,
                "approval_mode": tool.approval_mode,
                "isolation": tool.isolation,
            }
        )
    items.sort(key=lambda item: item["name"])
    return {
        "items": items,
        "count": len(items),
        "request_id": _request_id(request),
    }


@router.get("/tools/permissions/prompts")
def list_permission_prompts(
    request: Request,
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    rows = services.tool_executor.list_permission_prompts(status=status, limit=limit)
    if not auth.is_admin:
        rows = [item for item in rows if str(item.get("user_id") or "") == auth.user_id]
    return {
        "items": rows,
        "count": len(rows),
        "request_id": _request_id(request),
    }


@router.post("/tools/permissions/prompts/{prompt_id}/approve")
def approve_permission_prompt(
    request: Request,
    prompt_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    try:
        prompts = services.tool_executor.list_permission_prompts(status=None, limit=2000)
        match = next((item for item in prompts if str(item.get("id") or "") == prompt_id), None)
        if match is None:
            raise NotFoundError(f"Permission prompt not found: {prompt_id}")
        assert_owner(
            owner_user_id=str(match.get("user_id") or ""),
            auth=auth,
            resource_name="tool_permission_prompt",
            resource_id=prompt_id,
        )
        item = services.tool_executor.approve_permission_prompt(prompt_id=prompt_id)
        receipt = _sign_action(
            request,
            action="tool_permission_approve",
            payload={"prompt_id": prompt_id},
            actor=auth.user_id,
            target_type="tool_permission_prompt",
            target_id=prompt_id,
        )
        return {
            "prompt": item,
            "action_receipt": receipt,
            "request_id": _request_id(request),
        }
    except NotFoundError:
        _sign_action(
            request,
            action="tool_permission_approve",
            payload={"prompt_id": prompt_id},
            actor=auth.user_id,
            target_type="tool_permission_prompt",
            target_id=prompt_id,
            status="failed",
            details={"error": f"Permission prompt not found: {prompt_id}"},
        )
        raise
    except ValueError as exc:
        _sign_action(
            request,
            action="tool_permission_approve",
            payload={"prompt_id": prompt_id},
            actor=auth.user_id,
            target_type="tool_permission_prompt",
            target_id=prompt_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise NotFoundError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        _sign_action(
            request,
            action="tool_permission_approve",
            payload={"prompt_id": prompt_id},
            actor=auth.user_id,
            target_type="tool_permission_prompt",
            target_id=prompt_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc


@router.post("/tools/permissions/prompts/{prompt_id}/deny")
def deny_permission_prompt(
    request: Request,
    prompt_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    try:
        prompts = services.tool_executor.list_permission_prompts(status=None, limit=2000)
        match = next((item for item in prompts if str(item.get("id") or "") == prompt_id), None)
        if match is None:
            raise NotFoundError(f"Permission prompt not found: {prompt_id}")
        assert_owner(
            owner_user_id=str(match.get("user_id") or ""),
            auth=auth,
            resource_name="tool_permission_prompt",
            resource_id=prompt_id,
        )
        item = services.tool_executor.deny_permission_prompt(prompt_id=prompt_id)
        receipt = _sign_action(
            request,
            action="tool_permission_deny",
            payload={"prompt_id": prompt_id},
            actor=auth.user_id,
            target_type="tool_permission_prompt",
            target_id=prompt_id,
        )
        return {
            "prompt": item,
            "action_receipt": receipt,
            "request_id": _request_id(request),
        }
    except NotFoundError:
        _sign_action(
            request,
            action="tool_permission_deny",
            payload={"prompt_id": prompt_id},
            actor=auth.user_id,
            target_type="tool_permission_prompt",
            target_id=prompt_id,
            status="failed",
            details={"error": f"Permission prompt not found: {prompt_id}"},
        )
        raise
    except ValueError as exc:
        _sign_action(
            request,
            action="tool_permission_deny",
            payload={"prompt_id": prompt_id},
            actor=auth.user_id,
            target_type="tool_permission_prompt",
            target_id=prompt_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise NotFoundError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        _sign_action(
            request,
            action="tool_permission_deny",
            payload={"prompt_id": prompt_id},
            actor=auth.user_id,
            target_type="tool_permission_prompt",
            target_id=prompt_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ProviderError(str(exc)) from exc


@router.get("/mcp/tools")
def list_mcp_tools(request: Request) -> dict[str, Any]:
    services = request.app.state.services
    items = []
    for tool in services.tool_registry.list():
        items.append(
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
                "source": tool.source,
                "risk_level": tool.risk_level,
                "approval_mode": tool.approval_mode,
                "isolation": tool.isolation,
            }
        )
    items.sort(key=lambda item: item["name"])
    return {
        "items": items,
        "count": len(items),
    }


@router.get("/tools/actions/terminal")
def list_terminal_action_receipts(
    request: Request,
    status: str | None = Query(default=None),
    tool_name: str | None = Query(default=None),
    actor: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    request_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    requested_user = str(user_id or "").strip() or None
    if not auth.is_admin:
        if requested_user and requested_user != auth.user_id:
            raise PermissionDeniedError("Access denied to terminal action receipts for requested user.")
        requested_user = auth.user_id
    items = services.database.list_terminal_action_receipts(
        limit=limit,
        tool_name=tool_name,
        status=status,
        actor=actor,
        user_id=requested_user,
        session_id=session_id,
        request_id=request_id,
    )
    if not auth.is_admin:
        items = [
            item
            for item in items
            if str(item.get("actor") or "") == auth.user_id or str(item.get("user_id") or "") == auth.user_id
        ]
    return {
        "items": items,
        "count": len(items),
        "request_id": _request_id(request),
    }


@router.post("/tools/actions/filesystem/patches/preview")
def create_filesystem_patch_preview(
    payload: FilesystemPatchPreviewRequest,
    request: Request,
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    request_id = _request_id(request)
    effective_user_id = resolve_user_id(request_user_id=payload.user_id, auth=auth)

    target, roots = _safe_filesystem_path_for_request(request, payload.path)
    before_exists, before_content, before_size = _read_filesystem_preview_baseline(target)
    after_content = str(payload.content or "")
    after_size = len(after_content.encode("utf-8"))
    if after_size > FILESYSTEM_MAX_WRITE_BYTES:
        raise ValidationError(
            f"Content is too large to write ({after_size} > {FILESYSTEM_MAX_WRITE_BYTES} bytes)"
        )

    path_label = _display_path_for_roots(target, roots)
    diff = _build_structured_diff(path_label=path_label, before=before_content, after=after_content)
    summary = diff.get("summary", {})
    if isinstance(summary, dict):
        summary["before_exists"] = before_exists
        summary["before_bytes"] = before_size if before_exists else 0
        summary["after_bytes"] = after_size
        summary["path"] = path_label

    preview = services.database.create_filesystem_patch_preview(
        user_id=effective_user_id,
        actor=auth.user_id,
        session_id=payload.session_id,
        request_id=request_id,
        path=path_label,
        target_path=str(target),
        after_content=after_content,
        before_exists=before_exists,
        before_sha256=_sha256_text(before_content) if before_exists else None,
        before_size=before_size if before_exists else None,
        after_sha256=_sha256_text(after_content),
        after_size=after_size,
        diff=diff,
        expires_at=_iso_utc_after(payload.ttl_sec),
    )
    receipt = _sign_action(
        request,
        action="filesystem_patch_preview",
        payload={
            "preview_id": str(preview.get("id") or ""),
            "path": path_label,
        },
        actor=auth.user_id,
        target_type="filesystem_patch_preview",
        target_id=str(preview.get("id") or ""),
        details={
            "user_id": effective_user_id,
            "session_id": payload.session_id,
            "changed": bool(summary.get("changed", False)),
        },
    )
    return {
        "preview": _sanitize_patch_preview(preview),
        "action_receipt": receipt,
        "request_id": request_id,
    }


@router.get("/tools/actions/filesystem/patches")
def list_filesystem_patch_previews(
    request: Request,
    status: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    requested_user = str(user_id or "").strip() or None
    if not auth.is_admin:
        if requested_user and requested_user != auth.user_id:
            raise PermissionDeniedError("Access denied to filesystem patch previews for requested user.")
        requested_user = auth.user_id
    items = services.database.list_filesystem_patch_previews(
        user_id=requested_user,
        session_id=session_id,
        status=status,
        limit=limit,
        include_after_content=False,
    )
    return {
        "items": [_sanitize_patch_preview(item) for item in items],
        "count": len(items),
        "request_id": _request_id(request),
    }


@router.get("/tools/actions/filesystem/patches/{preview_id}")
def get_filesystem_patch_preview(
    request: Request,
    preview_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    auth = auth_context_from_request(request)
    item = _load_patch_preview(request, preview_id=preview_id, include_after_content=False)
    assert_owner(
        owner_user_id=str(item.get("user_id") or ""),
        auth=auth,
        resource_name="filesystem_patch_preview",
        resource_id=preview_id,
    )
    return {
        "preview": _sanitize_patch_preview(item),
        "request_id": _request_id(request),
    }


@router.post("/tools/actions/filesystem/patches/{preview_id}/approve")
def approve_filesystem_patch_preview(
    request: Request,
    preview_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    request_id = _request_id(request)
    item = _load_patch_preview(request, preview_id=preview_id, include_after_content=False)
    assert_owner(
        owner_user_id=str(item.get("user_id") or ""),
        auth=auth,
        resource_name="filesystem_patch_preview",
        resource_id=preview_id,
    )
    try:
        approved = services.database.approve_filesystem_patch_preview(
            preview_id=preview_id,
            actor=auth.user_id,
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    receipt = _sign_action(
        request,
        action="filesystem_patch_approve",
        payload={"preview_id": preview_id},
        actor=auth.user_id,
        target_type="filesystem_patch_preview",
        target_id=preview_id,
    )
    return {
        "preview": _sanitize_patch_preview(approved),
        "action_receipt": receipt,
        "request_id": request_id,
    }


@router.post("/tools/actions/filesystem/patches/{preview_id}/apply")
def apply_filesystem_patch_preview(
    payload: FilesystemPatchApplyRequest,
    request: Request,
    preview_id: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    request_id = _request_id(request)
    preview = _load_patch_preview(request, preview_id=preview_id, include_after_content=True)
    assert_owner(
        owner_user_id=str(preview.get("user_id") or ""),
        auth=auth,
        resource_name="filesystem_patch_preview",
        resource_id=preview_id,
    )
    status = str(preview.get("status") or "").strip().lower()
    if status != "approved":
        raise ValidationError(f"Filesystem patch preview must be approved before apply: {preview_id}")

    target_path = str(preview.get("target_path") or "").strip()
    if not target_path:
        raise ValidationError(f"Filesystem patch preview has empty target_path: {preview_id}")
    target, _ = _safe_filesystem_path_for_request(request, target_path)

    current_exists, current_content, _current_size = _read_filesystem_preview_baseline(target)
    expected_exists = bool(preview.get("before_exists"))
    if current_exists != expected_exists:
        raise ValidationError("Target file baseline changed. Re-create patch preview before apply.")
    if expected_exists:
        expected_sha = str(preview.get("before_sha256") or "").strip()
        if not expected_sha:
            raise ValidationError("Patch preview baseline hash is missing.")
        current_sha = _sha256_text(current_content)
        if current_sha != expected_sha:
            raise ValidationError("Target file content changed. Re-create patch preview before apply.")

    try:
        result = services.tool_executor.execute(
            name="filesystem",
            arguments={
                "action": "write",
                "path": str(target),
                "content": str(preview.get("after_content") or ""),
            },
            request_id=request_id,
            user_id=str(preview.get("user_id") or "").strip() or None,
            session_id=str(preview.get("session_id") or "").strip() or None,
            permission_id=payload.permission_id,
            action_class="user_initiated",
        )
        applied = services.database.mark_filesystem_patch_preview_applied(
            preview_id=preview_id,
            consumed_request_id=request_id,
        )
        receipt = _sign_action(
            request,
            action="filesystem_patch_apply",
            payload={
                "preview_id": preview_id,
                "path": str(preview.get("path") or ""),
            },
            actor=auth.user_id,
            target_type="filesystem_patch_preview",
            target_id=preview_id,
            details={
                "permission_id": payload.permission_id,
                "session_id": str(preview.get("session_id") or "").strip() or None,
            },
        )
        return {
            "result": result,
            "preview": _sanitize_patch_preview(applied),
            "action_receipt": receipt,
            "request_id": request_id,
        }
    except PermissionRequiredError as exc:
        _sign_action(
            request,
            action="filesystem_patch_apply",
            payload={
                "preview_id": preview_id,
                "path": str(preview.get("path") or ""),
            },
            actor=auth.user_id,
            target_type="filesystem_patch_preview",
            target_id=preview_id,
            status="failed",
            details={
                "error": str(exc),
                "prompt_id": getattr(exc, "prompt_id", None),
                "permission_id": payload.permission_id,
            },
        )
        raise PermissionDeniedError(str(exc)) from exc
    except ToolBudgetLimitError as exc:
        _sign_action(
            request,
            action="filesystem_patch_apply",
            payload={
                "preview_id": preview_id,
                "path": str(preview.get("path") or ""),
            },
            actor=auth.user_id,
            target_type="filesystem_patch_preview",
            target_id=preview_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise PermissionDeniedError(str(exc)) from exc
    except ValueError as exc:
        _sign_action(
            request,
            action="filesystem_patch_apply",
            payload={
                "preview_id": preview_id,
                "path": str(preview.get("path") or ""),
            },
            actor=auth.user_id,
            target_type="filesystem_patch_preview",
            target_id=preview_id,
            status="failed",
            details={"error": str(exc)},
        )
        raise ValidationError(str(exc)) from exc


@router.get("/debug/tools/guardrails", response_model=ToolGuardrailsDebugResponse)
def debug_tool_guardrails(
    request: Request,
    user_id: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    scope_request_id: str | None = Query(default=None),
    scopes_limit: int = Query(default=20, ge=1, le=200),
    top_tools_limit: int = Query(default=5, ge=1, le=20),
) -> ToolGuardrailsDebugResponse:
    services = request.app.state.services
    snapshot = services.tool_executor.debug_guardrails(
        request_id=scope_request_id,
        user_id=user_id,
        session_id=session_id,
        scopes_limit=scopes_limit,
        top_tools_limit=top_tools_limit,
    )
    return ToolGuardrailsDebugResponse(
        request_id=_request_id(request),
        approval_enforcement_mode=str(snapshot.get("approval_enforcement_mode", "prompt_and_allow")),
        autonomy_policy=dict(snapshot.get("autonomy_policy", {})),
        isolation_policy=dict(snapshot.get("isolation_policy", {})),
        sandbox=dict(snapshot.get("sandbox", {})),
        budget=dict(snapshot.get("budget", {})),
        plugin_signing=dict(snapshot.get("plugin_signing", {})),
    )


@router.get("/debug/tools/mcp-health")
def debug_mcp_health(request: Request) -> dict[str, Any]:
    services = request.app.state.services
    registry = getattr(services, "mcp_registry", None)
    if registry is None:
        return {
            "enabled": False,
            "health": {"items": [], "count": 0},
            "request_id": _request_id(request),
        }
    return {
        "enabled": True,
        "health": registry.debug_health(),
        "request_id": _request_id(request),
    }


@router.post("/mcp/tools/{tool_name}/invoke")
def invoke_mcp_tool(
    payload: MCPInvokeRequest,
    request: Request,
    tool_name: str = Path(..., min_length=1),
) -> dict[str, Any]:
    services = request.app.state.services
    auth = auth_context_from_request(request)
    request_id = _request_id(request)
    effective_user_id = resolve_user_id(request_user_id=payload.user_id, auth=auth)
    tool = services.tool_registry.get(tool_name)
    if tool is None:
        raise NotFoundError(f"Tool not found: {tool_name}")
    action_class = "user_initiated"

    terminal_action = _terminal_action_context(
        request,
        tool_name=tool_name,
        risk_level=str(getattr(tool, "risk_level", "medium")),
        action_class=action_class,
        actor=auth.user_id,
        session_id=payload.session_id,
        permission_id=payload.permission_id,
        arguments=payload.arguments,
    )
    high_risk_action = _high_risk_context(
        request,
        tool_name=tool_name,
        risk_level=str(getattr(tool, "risk_level", "medium")),
        action_class=action_class,
        actor=auth.user_id,
        session_id=payload.session_id,
        permission_id=payload.permission_id,
        arguments=payload.arguments,
    )
    event_type = "high_risk_action_receipt" if high_risk_action is not None else "signed_action"
    sign_details: dict[str, Any] = {
        "session_id": payload.session_id,
        "permission_id": payload.permission_id,
        "action_class": action_class,
    }
    if high_risk_action is not None:
        sign_details.update(high_risk_action)

    executor_request_id = request_id
    executor_session_id = payload.session_id
    if payload.permission_id:
        try:
            prompt_id = str(payload.permission_id).strip()
            prompts = services.tool_executor.list_permission_prompts(status=None, limit=2000)
            prompt = next((item for item in prompts if str(item.get("id") or "") == prompt_id), None)
            if isinstance(prompt, dict):
                prompt_owner = str(prompt.get("user_id") or "").strip()
                if prompt_owner and prompt_owner != effective_user_id and not auth.is_admin:
                    raise PermissionDeniedError("Permission prompt ownership mismatch.")
                scope = str(prompt.get("scope") or "").strip().lower()
                scope_value = str(prompt.get("scope_value") or "").strip()
                if scope == "request" and scope_value and scope_value != "none":
                    executor_request_id = scope_value
                if scope == "session" and (not executor_session_id or not str(executor_session_id).strip()):
                    if scope_value and scope_value != "none":
                        executor_session_id = scope_value
        except PermissionDeniedError:
            raise
        except Exception:
            executor_request_id = request_id
            executor_session_id = payload.session_id

    sign_details["session_id"] = executor_session_id
    if terminal_action is not None:
        terminal_action["session_id"] = executor_session_id
        sign_details["terminal_action"] = terminal_action

    def _failure_details(message: str, *, prompt_id: str | None = None) -> dict[str, Any]:
        details = dict(sign_details)
        details["error"] = message
        if prompt_id:
            details["prompt_id"] = prompt_id
        return details

    def _record_terminal_receipt(
        *,
        status: str,
        receipt: dict[str, Any] | None,
        details: dict[str, Any],
        result_payload: Any = None,
        error_message: str | None = None,
    ) -> None:
        if terminal_action is None:
            return
        try:
            services.database.add_terminal_action_receipt(
                action="tool_invoke",
                tool_name=tool_name,
                actor=auth.user_id,
                user_id=effective_user_id,
                session_id=executor_session_id,
                request_id=request_id,
                permission_id=payload.permission_id,
                status=status,
                risk_level=str(terminal_action.get("risk_level") or "medium"),
                policy_level=str(terminal_action.get("policy_level") or ""),
                rollback_hint=str(terminal_action.get("rollback_hint") or ""),
                arguments=payload.arguments,
                result=result_payload,
                error_message=error_message,
                details=details,
                action_receipt=receipt or {},
            )
        except Exception:
            pass

    try:
        result = services.tool_executor.execute(
            name=tool_name,
            arguments=payload.arguments,
            request_id=executor_request_id,
            user_id=effective_user_id,
            session_id=executor_session_id,
            permission_id=payload.permission_id,
            action_class=action_class,
        )
        receipt = _sign_action(
            request,
            action="tool_invoke",
            payload={
                "tool_name": tool_name,
                "arguments": payload.arguments,
            },
            actor=auth.user_id,
            target_type="tool",
            target_id=tool_name,
            event_type=event_type,
            details=sign_details,
        )
        _record_terminal_receipt(
            status="succeeded",
            receipt=receipt,
            details=dict(sign_details),
            result_payload=result,
        )
        response = {
            "result": result,
            "action_receipt": receipt,
            "request_id": request_id,
        }
        if high_risk_action is not None:
            response["high_risk_action"] = high_risk_action
        return response
    except PermissionRequiredError as exc:
        failure_details = _failure_details(str(exc), prompt_id=getattr(exc, "prompt_id", None))
        receipt = _sign_action(
            request,
            action="tool_invoke",
            payload={
                "tool_name": tool_name,
                "arguments": payload.arguments,
            },
            actor=auth.user_id,
            target_type="tool",
            target_id=tool_name,
            event_type=event_type,
            status="failed",
            details=failure_details,
        )
        _record_terminal_receipt(
            status="failed",
            receipt=receipt,
            details=failure_details,
            error_message=str(exc),
        )
        raise PermissionDeniedError(str(exc)) from exc
    except ToolBudgetLimitError as exc:
        failure_details = _failure_details(str(exc))
        receipt = _sign_action(
            request,
            action="tool_invoke",
            payload={
                "tool_name": tool_name,
                "arguments": payload.arguments,
            },
            actor=auth.user_id,
            target_type="tool",
            target_id=tool_name,
            event_type=event_type,
            status="failed",
            details=failure_details,
        )
        _record_terminal_receipt(
            status="failed",
            receipt=receipt,
            details=failure_details,
            error_message=str(exc),
        )
        raise PermissionDeniedError(str(exc)) from exc
    except ValueError as exc:
        failure_details = _failure_details(str(exc))
        receipt = _sign_action(
            request,
            action="tool_invoke",
            payload={
                "tool_name": tool_name,
                "arguments": payload.arguments,
            },
            actor=auth.user_id,
            target_type="tool",
            target_id=tool_name,
            event_type=event_type,
            status="failed",
            details=failure_details,
        )
        _record_terminal_receipt(
            status="failed",
            receipt=receipt,
            details=failure_details,
            error_message=str(exc),
        )
        raise ValidationError(str(exc)) from exc
    except AmaryllisError:
        raise
    except Exception as exc:
        failure_details = _failure_details(str(exc))
        receipt = _sign_action(
            request,
            action="tool_invoke",
            payload={
                "tool_name": tool_name,
                "arguments": payload.arguments,
            },
            actor=auth.user_id,
            target_type="tool",
            target_id=tool_name,
            event_type=event_type,
            status="failed",
            details=failure_details,
        )
        _record_terminal_receipt(
            status="failed",
            receipt=receipt,
            details=failure_details,
            error_message=str(exc),
        )
        raise ProviderError(str(exc)) from exc
