from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tools.autonomy_policy_pack import (
    VALID_AUTONOMY_LEVELS as _PACK_VALID_AUTONOMY_LEVELS,
    VALID_RISK_LEVELS as _PACK_VALID_RISK_LEVELS,
    default_policy_pack_path,
    load_autonomy_policy_pack,
)


VALID_AUTONOMY_LEVELS: tuple[str, ...] = _PACK_VALID_AUTONOMY_LEVELS
_VALID_RISKS = set(_PACK_VALID_RISK_LEVELS)


def normalize_autonomy_level(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in VALID_AUTONOMY_LEVELS:
        return "l3"
    return normalized


def normalize_risk_level(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in _VALID_RISKS:
        return "medium"
    return normalized


@dataclass(frozen=True)
class AutonomyDecision:
    allow: bool
    requires_approval: bool
    reason: str | None = None
    approval_scope: str | None = None
    approval_ttl_sec: int | None = None


class AutonomyPolicy:
    def __init__(self, level: str = "l3", policy_pack_path: str | Path | None = None) -> None:
        self.level = normalize_autonomy_level(level)
        self.policy_pack_path = self._resolve_policy_pack_path(policy_pack_path)
        self.policy_pack = load_autonomy_policy_pack(self.policy_pack_path)

    @staticmethod
    def _resolve_policy_pack_path(policy_pack_path: str | Path | None) -> Path:
        if policy_pack_path is None:
            return default_policy_pack_path()
        candidate = Path(policy_pack_path).expanduser()
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        return candidate.resolve()

    def evaluate(self, *, tool_name: str, risk_level: str) -> AutonomyDecision:
        normalized_risk = normalize_risk_level(risk_level)
        rule = self.policy_pack.rule(level=self.level, risk_level=normalized_risk)
        reason = self._render_reason(
            template=rule.reason,
            tool_name=tool_name,
            risk_level=normalized_risk,
            allow=rule.allow,
        )
        return AutonomyDecision(
            allow=rule.allow,
            requires_approval=rule.requires_approval,
            reason=reason,
            approval_scope=rule.approval_scope,
            approval_ttl_sec=rule.approval_ttl_sec,
        )

    def _render_reason(
        self,
        *,
        template: str | None,
        tool_name: str,
        risk_level: str,
        allow: bool,
    ) -> str | None:
        if template:
            try:
                return template.format(
                    level=self.level.upper(),
                    tool_name=str(tool_name),
                    risk_level=str(risk_level),
                )
            except Exception:
                return template
        if allow:
            return None
        return (
            f"Autonomy level {self.level.upper()} blocks tool '{tool_name}' "
            f"(risk={risk_level})."
        )

    def describe(self) -> dict[str, Any]:
        rules: dict[str, dict[str, str]] = {}
        for level, level_rules in self.policy_pack.levels.items():
            decision_map: dict[str, str] = {}
            for risk, rule in level_rules.items():
                if not rule.allow:
                    decision_map[risk] = "blocked"
                elif rule.requires_approval:
                    decision_map[risk] = "allow_with_approval"
                else:
                    decision_map[risk] = "allow"
            rules[level] = decision_map
        return {
            "level": self.level,
            "policy_pack": {
                "pack": self.policy_pack.pack,
                "schema_version": self.policy_pack.schema_version,
                "source_path": str(self.policy_pack.source_path),
            },
            "rules": rules,
        }
