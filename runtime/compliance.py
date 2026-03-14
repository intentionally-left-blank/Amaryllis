from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from runtime.auth import token_fingerprint
from runtime.config import AppConfig
from runtime.security import SecurityManager
from storage.database import Database


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None = None) -> str:
    value = dt or _utc_now()
    return value.isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class ComplianceManager:
    def __init__(
        self,
        *,
        config: AppConfig,
        database: Database,
        security_manager: SecurityManager,
    ) -> None:
        self.logger = logging.getLogger("amaryllis.compliance")
        self.config = config
        self.database = database
        self.security_manager = security_manager

    def sync_secret_inventory(self, *, actor: str | None, request_id: str | None) -> dict[str, Any]:
        rows = self._build_secret_inventory_rows()
        self.database.upsert_secret_inventory_items(rows)
        summary = self._summarize_secret_items(rows)
        receipt = self.security_manager.signed_action(
            action="security_secret_inventory_sync",
            payload={
                "count": len(rows),
                "summary": summary,
            },
            request_id=request_id,
            actor=actor,
            target_type="security_secret_inventory",
            target_id="all",
        )
        return {
            "request_id": request_id,
            "synced_at": _iso(),
            "count": len(rows),
            "summary": summary,
            "items": rows,
            "action_receipt": receipt,
        }

    def list_secret_inventory(
        self,
        *,
        limit: int = 200,
        status: str | None = None,
        provider: str | None = None,
        sync_first: bool = False,
        actor: str | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        if sync_first:
            self.sync_secret_inventory(actor=actor, request_id=request_id)
        items = self.database.list_secret_inventory(limit=limit, status=status, provider=provider)
        return {
            "request_id": request_id,
            "count": len(items),
            "summary": self._summarize_secret_items(items),
            "items": items,
        }

    def start_access_review(
        self,
        *,
        reviewer: str | None,
        summary: str | None,
        stale_days: int | None = None,
        request_id: str | None,
    ) -> dict[str, Any]:
        stale_after_days = max(1, int(stale_days if stale_days is not None else self.config.compliance_access_review_max_age_days))
        stale_before = _utc_now().timestamp() - (stale_after_days * 86400)
        token_activity = self.database.list_auth_token_activity(limit=5000)
        activity_by_token = {str(item.get("token_fingerprint")): item for item in token_activity}
        snapshot_tokens: list[dict[str, Any]] = []
        for spec in self.config.auth_tokens:
            fingerprint = token_fingerprint(spec.token)
            activity = activity_by_token.get(fingerprint)
            last_seen = str(activity.get("last_seen_at")) if isinstance(activity, dict) else None
            last_seen_ts = _parse_iso(last_seen).timestamp() if _parse_iso(last_seen) is not None else None
            is_stale = last_seen_ts is None or last_seen_ts < stale_before
            scopes = sorted({str(scope).strip().lower() for scope in spec.scopes})
            snapshot_tokens.append(
                {
                    "token_fingerprint": fingerprint,
                    "user_id": spec.user_id,
                    "scopes": scopes,
                    "is_admin": "admin" in scopes,
                    "is_service": "service" in scopes,
                    "is_stale": bool(is_stale),
                    "last_seen_at": last_seen,
                    "request_count": int(activity.get("request_count", 0)) if isinstance(activity, dict) else 0,
                }
            )

        known = {str(item.get("token_fingerprint")) for item in snapshot_tokens}
        for activity in token_activity:
            fingerprint = str(activity.get("token_fingerprint") or "")
            if not fingerprint or fingerprint in known:
                continue
            snapshot_tokens.append(
                {
                    "token_fingerprint": fingerprint,
                    "user_id": str(activity.get("user_id") or ""),
                    "scopes": activity.get("scopes", []),
                    "is_admin": "admin" in set(activity.get("scopes", [])),
                    "is_service": "service" in set(activity.get("scopes", [])),
                    "is_stale": False,
                    "last_seen_at": activity.get("last_seen_at"),
                    "request_count": int(activity.get("request_count", 0)),
                    "token_present_in_config": False,
                }
            )
        snapshot_tokens.sort(key=lambda item: (not bool(item.get("is_admin")), str(item.get("user_id"))))

        secrets = self.list_secret_inventory(limit=500, sync_first=True, actor=reviewer, request_id=request_id)
        open_incidents = self.database.list_security_incidents(limit=500, status="open")
        review_id = str(uuid4())
        snapshot = {
            "generated_at": _iso(),
            "stale_after_days": stale_after_days,
            "tokens": snapshot_tokens,
            "token_count": len(snapshot_tokens),
            "stale_token_count": sum(1 for item in snapshot_tokens if bool(item.get("is_stale"))),
            "secret_summary": secrets.get("summary", {}),
            "open_incidents": len(open_incidents),
        }
        self.database.create_access_review(
            review_id=review_id,
            reviewer=reviewer,
            snapshot=snapshot,
            summary=summary,
            metadata={
                "request_id": request_id,
            },
        )
        receipt = self.security_manager.signed_action(
            action="security_access_review_start",
            payload={
                "review_id": review_id,
                "token_count": len(snapshot_tokens),
                "stale_token_count": snapshot.get("stale_token_count"),
            },
            request_id=request_id,
            actor=reviewer,
            target_type="security_access_review",
            target_id=review_id,
        )
        review = self.database.get_access_review(review_id)
        return {
            "review": review,
            "action_receipt": receipt,
        }

    def complete_access_review(
        self,
        *,
        review_id: str,
        reviewer: str | None,
        summary: str | None,
        decisions: dict[str, Any] | None,
        findings: list[dict[str, Any]] | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        success = self.database.complete_access_review(
            review_id=review_id,
            reviewer=reviewer,
            summary=summary,
            decisions=decisions or {},
            findings=findings or [],
            metadata={"request_id": request_id},
        )
        if not success:
            raise ValueError(f"Access review not found: {review_id}")
        receipt = self.security_manager.signed_action(
            action="security_access_review_complete",
            payload={
                "review_id": review_id,
                "findings_count": len(findings or []),
            },
            request_id=request_id,
            actor=reviewer,
            target_type="security_access_review",
            target_id=review_id,
        )
        review = self.database.get_access_review(review_id)
        return {
            "review": review,
            "action_receipt": receipt,
        }

    def get_access_review(self, *, review_id: str) -> dict[str, Any] | None:
        return self.database.get_access_review(review_id)

    def list_access_reviews(self, *, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
        return self.database.list_access_reviews(limit=limit, status=status)

    def list_auth_token_activity(self, *, limit: int = 200, user_id: str | None = None) -> list[dict[str, Any]]:
        return self.database.list_auth_token_activity(limit=limit, user_id=user_id)

    def open_incident(
        self,
        *,
        category: str,
        severity: str,
        title: str,
        description: str,
        owner: str | None,
        actor: str | None,
        request_id: str | None,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        incident_id = str(uuid4())
        normalized_category = str(category or "security").strip().lower() or "security"
        normalized_severity = self._normalize_severity(severity)
        self.database.create_security_incident(
            incident_id=incident_id,
            category=normalized_category,
            severity=normalized_severity,
            status="open",
            title=title,
            description=description,
            owner=owner,
            request_id=request_id,
            metadata=metadata or {},
        )
        self.database.add_security_incident_event(
            incident_id=incident_id,
            event_type="opened",
            actor=actor,
            message=description,
            details={"request_id": request_id},
        )
        receipt = self.security_manager.signed_action(
            action="security_incident_open",
            payload={
                "incident_id": incident_id,
                "category": normalized_category,
                "severity": normalized_severity,
                "title": title,
            },
            request_id=request_id,
            actor=actor,
            target_type="security_incident",
            target_id=incident_id,
        )
        return {
            "incident": self.get_incident(incident_id=incident_id),
            "action_receipt": receipt,
        }

    def acknowledge_incident(
        self,
        *,
        incident_id: str,
        actor: str | None,
        owner: str | None,
        note: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        incident = self.database.get_security_incident(incident_id)
        if incident is None:
            raise ValueError(f"Incident not found: {incident_id}")
        ok = self.database.update_security_incident_fields(
            incident_id,
            status="acknowledged",
            owner=owner or incident.get("owner"),
            acknowledged_at=_iso(),
        )
        if not ok:
            raise ValueError(f"Incident not found: {incident_id}")
        self.database.add_security_incident_event(
            incident_id=incident_id,
            event_type="acknowledged",
            actor=actor,
            message=note or "Incident acknowledged.",
            details={"request_id": request_id},
        )
        receipt = self.security_manager.signed_action(
            action="security_incident_acknowledge",
            payload={"incident_id": incident_id, "owner": owner},
            request_id=request_id,
            actor=actor,
            target_type="security_incident",
            target_id=incident_id,
        )
        return {
            "incident": self.get_incident(incident_id=incident_id),
            "action_receipt": receipt,
        }

    def resolve_incident(
        self,
        *,
        incident_id: str,
        actor: str | None,
        resolution_summary: str | None,
        impact: str | None,
        containment: str | None,
        root_cause: str | None,
        recovery_actions: str | None,
        request_id: str | None,
    ) -> dict[str, Any]:
        incident = self.database.get_security_incident(incident_id)
        if incident is None:
            raise ValueError(f"Incident not found: {incident_id}")
        ok = self.database.update_security_incident_fields(
            incident_id,
            status="resolved",
            resolved_at=_iso(),
            impact=impact,
            containment=containment,
            root_cause=root_cause,
            recovery_actions=recovery_actions,
        )
        if not ok:
            raise ValueError(f"Incident not found: {incident_id}")
        self.database.add_security_incident_event(
            incident_id=incident_id,
            event_type="resolved",
            actor=actor,
            message=resolution_summary or "Incident resolved.",
            details={
                "request_id": request_id,
                "impact": impact,
                "containment": containment,
                "root_cause": root_cause,
                "recovery_actions": recovery_actions,
            },
        )
        receipt = self.security_manager.signed_action(
            action="security_incident_resolve",
            payload={
                "incident_id": incident_id,
                "root_cause": root_cause or "",
            },
            request_id=request_id,
            actor=actor,
            target_type="security_incident",
            target_id=incident_id,
        )
        return {
            "incident": self.get_incident(incident_id=incident_id),
            "action_receipt": receipt,
        }

    def add_incident_note(
        self,
        *,
        incident_id: str,
        actor: str | None,
        message: str,
        request_id: str | None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        incident = self.database.get_security_incident(incident_id)
        if incident is None:
            raise ValueError(f"Incident not found: {incident_id}")
        self.database.add_security_incident_event(
            incident_id=incident_id,
            event_type="note",
            actor=actor,
            message=message,
            details={**(details or {}), "request_id": request_id},
        )
        receipt = self.security_manager.signed_action(
            action="security_incident_note",
            payload={"incident_id": incident_id, "message": message},
            request_id=request_id,
            actor=actor,
            target_type="security_incident",
            target_id=incident_id,
        )
        return {
            "incident": self.get_incident(incident_id=incident_id),
            "action_receipt": receipt,
        }

    def get_incident(self, *, incident_id: str) -> dict[str, Any] | None:
        incident = self.database.get_security_incident(incident_id)
        if incident is None:
            return None
        incident["events"] = self.database.list_security_incident_events(incident_id=incident_id, limit=1000)
        return incident

    def list_incidents(
        self,
        *,
        limit: int = 200,
        status: str | None = None,
        severity: str | None = None,
        category: str | None = None,
    ) -> list[dict[str, Any]]:
        incidents = self.database.list_security_incidents(
            limit=limit,
            status=status,
            severity=severity,
            category=category,
        )
        return incidents

    def compliance_snapshot(self, *, request_id: str | None, actor: str | None) -> dict[str, Any]:
        secret_inventory = self.list_secret_inventory(
            limit=500,
            sync_first=True,
            actor=actor,
            request_id=request_id,
        )
        identity_health = self._identity_rotation_health()
        access_reviews = self.database.list_access_reviews(limit=1)
        latest_review = access_reviews[0] if access_reviews else None
        open_incidents = self.database.list_security_incidents(limit=1000, status="open")
        critical_open_incidents = [item for item in open_incidents if str(item.get("severity")) == "critical"]
        recent_audit_events = self.database.list_security_audit_events(limit=500)
        failed_audit_events = [item for item in recent_audit_events if str(item.get("status")) == "failed"]

        controls = {
            "secrets_no_required_missing": int(secret_inventory["summary"].get("required_missing", 0)) == 0,
            "identity_rotation_not_overdue": not bool(identity_health.get("is_overdue", False)),
            "recent_access_review_present": self._is_access_review_fresh(latest_review),
            "no_open_critical_incidents": len(critical_open_incidents) == 0,
        }
        controls["audit_ready"] = all(bool(value) for value in controls.values())
        checklist = self._control_checklist(
            secret_inventory=secret_inventory,
            identity_health=identity_health,
            latest_review=latest_review,
            open_incidents=open_incidents,
            failed_audit_events=failed_audit_events,
        )

        return {
            "request_id": request_id,
            "generated_at": _iso(),
            "control_framework": "SOC2/ISO27001 baseline",
            "security_profile": self.config.security_profile,
            "controls": controls,
            "checklist": checklist,
            "secret_inventory": secret_inventory,
            "identity_rotation": identity_health,
            "latest_access_review": latest_review,
            "open_incidents_count": len(open_incidents),
            "open_critical_incidents_count": len(critical_open_incidents),
            "recent_audit_events_count": len(recent_audit_events),
            "recent_failed_audit_events_count": len(failed_audit_events),
        }

    def export_evidence_bundle(
        self,
        *,
        actor: str | None,
        request_id: str | None,
        output_name: str | None = None,
        window_days: int = 90,
        event_limit: int = 2000,
    ) -> dict[str, Any]:
        max_window_days = max(1, int(window_days))
        max_event_limit = max(100, min(int(event_limit), 20000))
        snapshot = self.compliance_snapshot(request_id=request_id, actor=actor)
        audit_events = self.database.list_security_audit_events(limit=max_event_limit)
        access_reviews = self.database.list_access_reviews(limit=500)
        incidents = self.database.list_security_incidents(limit=5000)
        for incident in incidents:
            incident_id = str(incident.get("id") or "")
            incident["events"] = self.database.list_security_incident_events(
                incident_id=incident_id,
                limit=1000,
            )
        token_activity = self.database.list_auth_token_activity(limit=2000)
        generated_at = _utc_now()
        generated_iso = _iso(generated_at)
        cutoff_ts = generated_at.timestamp() - (max_window_days * 86400)
        filtered_audit = [
            item
            for item in audit_events
            if (_parse_iso(str(item.get("created_at") or "")) or _utc_now()).timestamp() >= cutoff_ts
        ]
        evidence = {
            "generated_at": generated_iso,
            "window_days": max_window_days,
            "event_limit": max_event_limit,
            "control_framework": "SOC2/ISO27001 baseline",
            "snapshot": snapshot,
            "audit_events": filtered_audit,
            "access_reviews": access_reviews,
            "incidents": incidents,
            "auth_token_activity": token_activity,
            "system": {
                "app_name": self.config.app_name,
                "app_version": self.config.app_version,
                "api_version": self.config.api_version,
                "release_channel": self.config.api_release_channel,
                "security_profile": self.config.security_profile,
            },
        }
        evidence_name = self._normalize_evidence_name(output_name, generated_at=generated_at)
        target = self.config.evidence_dir / evidence_name
        target.parent.mkdir(parents=True, exist_ok=True)
        temp_target = target.with_suffix(f"{target.suffix}.tmp")
        temp_target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp_target, target)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        receipt = self.security_manager.signed_action(
            action="security_evidence_export",
            payload={
                "filename": target.name,
                "sha256": digest,
                "window_days": max_window_days,
                "event_count": len(filtered_audit),
            },
            request_id=request_id,
            actor=actor,
            target_type="security_evidence_bundle",
            target_id=target.name,
        )
        return {
            "request_id": request_id,
            "path": str(target),
            "sha256": digest,
            "size_bytes": int(target.stat().st_size),
            "generated_at": generated_iso,
            "audit_events_count": len(filtered_audit),
            "access_reviews_count": len(access_reviews),
            "incidents_count": len(incidents),
            "action_receipt": receipt,
        }

    def _build_secret_inventory_rows(self) -> list[dict[str, Any]]:
        identity_info = self.security_manager.identity_info()
        secret_rows: list[dict[str, Any]] = []
        secret_rows.extend(
            [
                self._build_secret_row(
                    secret_key="openai_api_key",
                    provider="openai",
                    value=self.config.openai_api_key,
                    is_required=False,
                    last_rotated_at=self.config.openai_api_key_rotated_at,
                    expires_at=self.config.openai_api_key_expires_at,
                    source="env",
                    metadata={"base_url": self.config.openai_base_url},
                ),
                self._build_secret_row(
                    secret_key="anthropic_api_key",
                    provider="anthropic",
                    value=self.config.anthropic_api_key,
                    is_required=False,
                    last_rotated_at=self.config.anthropic_api_key_rotated_at,
                    expires_at=self.config.anthropic_api_key_expires_at,
                    source="env",
                    metadata={"base_url": self.config.anthropic_base_url},
                ),
                self._build_secret_row(
                    secret_key="openrouter_api_key",
                    provider="openrouter",
                    value=self.config.openrouter_api_key,
                    is_required=False,
                    last_rotated_at=self.config.openrouter_api_key_rotated_at,
                    expires_at=self.config.openrouter_api_key_expires_at,
                    source="env",
                    metadata={"base_url": self.config.openrouter_base_url},
                ),
                self._build_secret_row(
                    secret_key="plugin_signing_key",
                    provider="plugins",
                    value=self.config.plugin_signing_key,
                    is_required=self.config.plugin_signing_mode == "strict",
                    last_rotated_at=self.config.plugin_signing_key_rotated_at,
                    expires_at=self.config.plugin_signing_key_expires_at,
                    source="env",
                    metadata={"mode": self.config.plugin_signing_mode},
                ),
                self._build_secret_row(
                    secret_key="runtime_identity_key",
                    provider="runtime",
                    value=str(identity_info.get("fingerprint") or ""),
                    is_required=True,
                    last_rotated_at=str(identity_info.get("created_at") or ""),
                    expires_at=None,
                    source="identity_store",
                    metadata={"key_id": identity_info.get("key_id"), "algorithm": identity_info.get("algorithm")},
                ),
            ]
        )
        return secret_rows

    def _build_secret_row(
        self,
        *,
        secret_key: str,
        provider: str,
        value: str | None,
        is_required: bool,
        last_rotated_at: str | None,
        expires_at: str | None,
        source: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        normalized_value = str(value or "").strip()
        value_present = bool(normalized_value)
        value_fingerprint = token_fingerprint(normalized_value) if value_present else None
        status = self._secret_status(
            is_required=is_required,
            value_present=value_present,
            last_rotated_at=last_rotated_at,
            expires_at=expires_at,
        )
        return {
            "secret_key": secret_key,
            "provider": provider,
            "is_required": bool(is_required),
            "source": source,
            "value_fingerprint": value_fingerprint,
            "value_present": value_present,
            "last_rotated_at": str(last_rotated_at or "").strip() or None,
            "rotation_period_days": self.config.compliance_secret_rotation_max_age_days,
            "expires_at": str(expires_at or "").strip() or None,
            "status": status,
            "metadata": metadata,
        }

    def _secret_status(
        self,
        *,
        is_required: bool,
        value_present: bool,
        last_rotated_at: str | None,
        expires_at: str | None,
    ) -> str:
        if not value_present:
            return "missing" if is_required else "not_configured"
        now = _utc_now()
        expires_dt = _parse_iso(expires_at)
        if expires_dt is not None:
            if expires_dt <= now:
                return "expired"
            days_to_expiry = (expires_dt - now).total_seconds() / 86400.0
            if days_to_expiry <= float(self.config.compliance_secret_expiry_warning_days):
                return "expiring"
        rotated_dt = _parse_iso(last_rotated_at)
        if rotated_dt is None:
            return "rotation_unknown"
        age_days = (now - rotated_dt).total_seconds() / 86400.0
        if age_days > float(self.config.compliance_secret_rotation_max_age_days):
            return "overdue_rotation"
        return "healthy"

    @staticmethod
    def _summarize_secret_items(items: list[dict[str, Any]]) -> dict[str, Any]:
        by_status: dict[str, int] = {}
        required_missing = 0
        for item in items:
            status = str(item.get("status") or "unknown").strip().lower() or "unknown"
            by_status[status] = by_status.get(status, 0) + 1
            if bool(item.get("is_required")) and status in {"missing", "expired", "overdue_rotation"}:
                required_missing += 1
        return {
            "total": len(items),
            "required_missing": required_missing,
            "by_status": by_status,
        }

    def _identity_rotation_health(self) -> dict[str, Any]:
        info = self.security_manager.identity_info()
        created_at = str(info.get("created_at") or "")
        created_dt = _parse_iso(created_at)
        if created_dt is None:
            return {
                "created_at": created_at or None,
                "age_days": None,
                "max_age_days": self.config.compliance_identity_rotation_max_age_days,
                "is_overdue": True,
            }
        age_days = max(0.0, (_utc_now() - created_dt).total_seconds() / 86400.0)
        is_overdue = age_days > float(self.config.compliance_identity_rotation_max_age_days)
        return {
            "created_at": created_at,
            "age_days": round(age_days, 2),
            "max_age_days": self.config.compliance_identity_rotation_max_age_days,
            "is_overdue": is_overdue,
            "fingerprint": info.get("fingerprint"),
            "key_id": info.get("key_id"),
        }

    def _is_access_review_fresh(self, review: dict[str, Any] | None) -> bool:
        if not isinstance(review, dict):
            return False
        completed_at = _parse_iso(str(review.get("completed_at") or review.get("started_at") or ""))
        if completed_at is None:
            return False
        age_days = (_utc_now() - completed_at).total_seconds() / 86400.0
        return age_days <= float(self.config.compliance_access_review_max_age_days)

    def _normalize_severity(self, severity: str | None) -> str:
        normalized = str(severity or "").strip().lower() or "medium"
        if normalized not in {"low", "medium", "high", "critical"}:
            return "medium"
        return normalized

    def _normalize_evidence_name(self, output_name: str | None, *, generated_at: datetime) -> str:
        if output_name:
            candidate = str(output_name).strip()
            if candidate:
                safe = "".join(ch for ch in candidate if ch.isalnum() or ch in {"-", "_", "."})
                if safe and safe.lower().endswith(".json"):
                    return safe
                if safe:
                    return safe + ".json"
        return f"security-evidence-{generated_at.strftime('%Y%m%dT%H%M%SZ')}.json"

    def _control_checklist(
        self,
        *,
        secret_inventory: dict[str, Any],
        identity_health: dict[str, Any],
        latest_review: dict[str, Any] | None,
        open_incidents: list[dict[str, Any]],
        failed_audit_events: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        secret_summary = secret_inventory.get("summary", {}) if isinstance(secret_inventory, dict) else {}
        required_missing = int(secret_summary.get("required_missing", 0))
        secret_by_status = (
            secret_summary.get("by_status", {})
            if isinstance(secret_summary, dict) and isinstance(secret_summary.get("by_status"), dict)
            else {}
        )
        critical_open = sum(1 for item in open_incidents if str(item.get("severity") or "") == "critical")
        review_fresh = self._is_access_review_fresh(latest_review)

        return [
            {
                "control_id": "SOC2-CC6.1",
                "framework": "SOC2",
                "control": "Authentication is mandatory in production.",
                "ok": bool(self.config.security_profile == "production" and self.config.auth_enabled),
                "evidence": {
                    "security_profile": self.config.security_profile,
                    "auth_enabled": self.config.auth_enabled,
                    "token_count": len(self.config.auth_tokens),
                },
            },
            {
                "control_id": "SOC2-CC6.7",
                "framework": "SOC2",
                "control": "Secret inventory has no missing required secrets.",
                "ok": required_missing == 0,
                "evidence": {
                    "required_missing": required_missing,
                    "status_breakdown": secret_by_status,
                },
            },
            {
                "control_id": "ISO27001-A.5.17",
                "framework": "ISO27001",
                "control": "Cryptographic identity rotation is not overdue.",
                "ok": not bool(identity_health.get("is_overdue", False)),
                "evidence": identity_health,
            },
            {
                "control_id": "ISO27001-A.5.18",
                "framework": "ISO27001",
                "control": "Access review is performed within the configured review window.",
                "ok": review_fresh,
                "evidence": {
                    "review_max_age_days": self.config.compliance_access_review_max_age_days,
                    "latest_review_id": None if latest_review is None else latest_review.get("id"),
                    "latest_review_status": None if latest_review is None else latest_review.get("status"),
                },
            },
            {
                "control_id": "SOC2-CC7.2",
                "framework": "SOC2",
                "control": "Critical incidents are tracked and not left open.",
                "ok": critical_open == 0,
                "evidence": {
                    "open_incidents": len(open_incidents),
                    "open_critical_incidents": critical_open,
                },
            },
            {
                "control_id": "SOC2-CC7.3",
                "framework": "SOC2",
                "control": "Audit trail is available with manageable failed-event rate.",
                "ok": len(failed_audit_events) <= 50,
                "evidence": {
                    "failed_audit_events_count": len(failed_audit_events),
                    "threshold": 50,
                },
            },
            {
                "control_id": "ISO27001-A.12.7",
                "framework": "ISO27001",
                "control": "Audit evidence export directory is writable and present.",
                "ok": self.config.evidence_dir.exists() and self.config.evidence_dir.is_dir(),
                "evidence": {
                    "evidence_dir": str(self.config.evidence_dir),
                    "exists": self.config.evidence_dir.exists(),
                },
            },
        ]
