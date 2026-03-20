from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
VALID_AUTONOMY_LEVELS: tuple[str, ...] = ("l0", "l1", "l2", "l3", "l4", "l5")
VALID_RISK_LEVELS: tuple[str, ...] = ("low", "medium", "high", "critical")
VALID_APPROVAL_SCOPES: tuple[str, ...] = ("request", "session", "user", "global")


class AutonomyPolicyPackError(ValueError):
    pass


@dataclass(frozen=True)
class AutonomyRule:
    allow: bool
    requires_approval: bool
    reason: str | None = None
    approval_scope: str | None = None
    approval_ttl_sec: int | None = None


@dataclass(frozen=True)
class AutonomyPolicyPack:
    schema_version: int
    pack: str
    description: str
    levels: dict[str, dict[str, AutonomyRule]]
    source_path: Path

    def rule(self, *, level: str, risk_level: str) -> AutonomyRule:
        try:
            return self.levels[level][risk_level]
        except KeyError as exc:  # pragma: no cover - defensive
            raise AutonomyPolicyPackError(
                f"Policy pack '{self.pack}' missing level={level} risk={risk_level} rule"
            ) from exc


def default_policy_pack_path(project_root: Path | None = None) -> Path:
    root = project_root.resolve() if project_root is not None else Path(__file__).resolve().parents[1]
    return (root / "policies" / "autonomy" / "default.json").resolve()


def load_autonomy_policy_pack(path: Path) -> AutonomyPolicyPack:
    source_path = path.resolve()
    payload = _load_json_object(source_path)

    schema_version = _as_int(payload.get("schema_version"), key="schema_version")
    if schema_version != SCHEMA_VERSION:
        raise AutonomyPolicyPackError(
            f"Autonomy policy pack '{source_path}' has unsupported schema_version={schema_version}; "
            f"expected {SCHEMA_VERSION}"
        )

    pack = str(payload.get("pack") or "").strip()
    if not pack:
        raise AutonomyPolicyPackError("Autonomy policy pack field 'pack' must be non-empty")

    description = str(payload.get("description") or "").strip()
    rules_raw = payload.get("rules")
    if not isinstance(rules_raw, dict):
        raise AutonomyPolicyPackError("Autonomy policy pack field 'rules' must be an object")

    levels: dict[str, dict[str, AutonomyRule]] = {}
    for level in VALID_AUTONOMY_LEVELS:
        level_rules_raw = rules_raw.get(level)
        if not isinstance(level_rules_raw, dict):
            raise AutonomyPolicyPackError(f"Autonomy policy pack missing rules for level '{level}'")
        level_rules: dict[str, AutonomyRule] = {}
        for risk in VALID_RISK_LEVELS:
            rule_raw = level_rules_raw.get(risk)
            if not isinstance(rule_raw, dict):
                raise AutonomyPolicyPackError(
                    f"Autonomy policy pack missing rule for level='{level}' risk='{risk}'"
                )
            level_rules[risk] = _parse_rule(rule_raw, level=level, risk=risk)
        levels[level] = level_rules

    return AutonomyPolicyPack(
        schema_version=schema_version,
        pack=pack,
        description=description,
        levels=levels,
        source_path=source_path,
    )


def _parse_rule(payload: dict[str, Any], *, level: str, risk: str) -> AutonomyRule:
    allow = payload.get("allow")
    requires_approval = payload.get("requires_approval")
    if not isinstance(allow, bool):
        raise AutonomyPolicyPackError(
            f"Autonomy policy rule level='{level}' risk='{risk}' field 'allow' must be boolean"
        )
    if not isinstance(requires_approval, bool):
        raise AutonomyPolicyPackError(
            f"Autonomy policy rule level='{level}' risk='{risk}' field 'requires_approval' must be boolean"
        )

    if not allow and requires_approval:
        raise AutonomyPolicyPackError(
            f"Autonomy policy rule level='{level}' risk='{risk}' cannot require approval when allow=false"
        )

    reason_raw = payload.get("reason")
    reason = None
    if reason_raw is not None:
        reason = str(reason_raw).strip()
        if not reason:
            reason = None

    if not allow and not reason:
        raise AutonomyPolicyPackError(
            f"Autonomy policy rule level='{level}' risk='{risk}' must provide non-empty reason when allow=false"
        )

    approval_scope: str | None = None
    approval_ttl_sec: int | None = None
    if requires_approval:
        approval_scope_raw = str(payload.get("approval_scope") or "").strip().lower()
        if approval_scope_raw not in VALID_APPROVAL_SCOPES:
            raise AutonomyPolicyPackError(
                f"Autonomy policy rule level='{level}' risk='{risk}' has invalid approval_scope='{approval_scope_raw}'"
            )
        approval_scope = approval_scope_raw
        approval_ttl_sec = _as_int(
            payload.get("approval_ttl_sec"),
            key=f"rules.{level}.{risk}.approval_ttl_sec",
            min_value=1,
        )

    return AutonomyRule(
        allow=allow,
        requires_approval=requires_approval,
        reason=reason,
        approval_scope=approval_scope,
        approval_ttl_sec=approval_ttl_sec,
    )


def _load_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise AutonomyPolicyPackError(f"Autonomy policy pack not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AutonomyPolicyPackError(f"Invalid JSON in autonomy policy pack {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise AutonomyPolicyPackError(f"Autonomy policy pack {path} must contain a JSON object")
    return payload


def _as_int(value: Any, *, key: str, min_value: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise AutonomyPolicyPackError(f"Autonomy policy pack field '{key}' must be integer") from exc
    if min_value is not None and parsed < min_value:
        raise AutonomyPolicyPackError(f"Autonomy policy pack field '{key}' must be >= {min_value}")
    return parsed
