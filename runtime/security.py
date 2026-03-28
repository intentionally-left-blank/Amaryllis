from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Protocol
from uuid import uuid4

from runtime.auth import AuthContext
from storage.database import Database


class TelemetrySink(Protocol):
    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        ...


@dataclass(frozen=True)
class LocalIdentity:
    key_id: str
    algorithm: str
    created_at: str
    secret_b64: str

    @property
    def secret_bytes(self) -> bytes:
        return base64.b64decode(self.secret_b64.encode("utf-8"))

    @property
    def fingerprint(self) -> str:
        digest = hashlib.sha256(self.secret_bytes).hexdigest()
        return digest[:24]

    def to_payload(self) -> dict[str, str]:
        return {
            "key_id": self.key_id,
            "algorithm": self.algorithm,
            "created_at": self.created_at,
            "secret_b64": self.secret_b64,
        }

    @staticmethod
    def from_payload(raw: dict[str, Any], *, fallback_created_at: str) -> LocalIdentity | None:
        key_id = str(raw.get("key_id", "")).strip()
        algorithm = str(raw.get("algorithm", "HMAC-SHA256")).strip() or "HMAC-SHA256"
        created_at = str(raw.get("created_at", "")).strip() or fallback_created_at
        secret_b64 = str(raw.get("secret_b64", "")).strip()
        if not key_id or not secret_b64:
            return None
        return LocalIdentity(
            key_id=key_id,
            algorithm=algorithm,
            created_at=created_at,
            secret_b64=secret_b64,
        )


class LocalIdentityManager:
    DEFAULT_HISTORY_LIMIT = 8

    def __init__(self, identity_path: Path) -> None:
        self.logger = logging.getLogger("amaryllis.security.identity")
        self.identity_path = Path(identity_path)
        self.identity_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._identity, self._history = self._load_or_create_bundle()

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def info(self) -> dict[str, Any]:
        with self._lock:
            identity = self._identity
            history_count = len(self._history)
        return {
            "key_id": identity.key_id,
            "algorithm": identity.algorithm,
            "created_at": identity.created_at,
            "fingerprint": identity.fingerprint,
            "history_count": history_count,
        }

    def sign(
        self,
        *,
        action: str,
        payload: dict[str, Any],
        request_id: str | None,
        actor: str | None,
    ) -> dict[str, Any]:
        with self._lock:
            identity = self._identity
        now = self._utc_now()
        nonce = str(uuid4())
        payload_hash = self._payload_hash(payload)
        signable = {
            "key_id": identity.key_id,
            "algorithm": identity.algorithm,
            "action": action,
            "timestamp": now,
            "nonce": nonce,
            "request_id": request_id or "",
            "actor": actor or "",
            "payload_hash": payload_hash,
        }
        signature = self._sign_payload(signable, identity=identity)
        return {
            **signable,
            "signature": signature,
        }

    def verify(self, receipt: dict[str, Any], payload: dict[str, Any]) -> bool:
        required = {
            "key_id",
            "algorithm",
            "action",
            "timestamp",
            "nonce",
            "request_id",
            "actor",
            "payload_hash",
            "signature",
        }
        if any(key not in receipt for key in required):
            return False
        key_id = str(receipt.get("key_id") or "").strip()
        with self._lock:
            identity = self._find_identity_by_key_id(key_id)
        if identity is None:
            return False
        if str(receipt.get("algorithm")) != identity.algorithm:
            return False
        expected_hash = self._payload_hash(payload)
        if str(receipt.get("payload_hash")) != expected_hash:
            return False
        copy = {
            "key_id": str(receipt.get("key_id")),
            "algorithm": str(receipt.get("algorithm")),
            "action": str(receipt.get("action")),
            "timestamp": str(receipt.get("timestamp")),
            "nonce": str(receipt.get("nonce")),
            "request_id": str(receipt.get("request_id")),
            "actor": str(receipt.get("actor")),
            "payload_hash": str(receipt.get("payload_hash")),
        }
        expected_signature = self._sign_payload(copy, identity=identity)
        return hmac.compare_digest(expected_signature, str(receipt.get("signature")))

    def rotate(self, *, max_history: int | None = None) -> dict[str, Any]:
        with self._lock:
            previous = self._identity
            next_identity = self._generate_identity()
            limit = max(0, int(max_history if max_history is not None else self.DEFAULT_HISTORY_LIMIT))
            history = [previous]
            for item in self._history:
                if item.key_id == previous.key_id or item.key_id == next_identity.key_id:
                    continue
                history.append(item)
            if limit > 0:
                history = history[:limit]
            else:
                history = []
            self._persist_bundle(current=next_identity, history=history)
            self._identity = next_identity
            self._history = history
            return {
                "previous": self._identity_info(previous),
                "current": self._identity_info(next_identity),
                "history_count": len(history),
            }

    def _load_or_create_bundle(self) -> tuple[LocalIdentity, list[LocalIdentity]]:
        now = self._utc_now()
        if self.identity_path.exists():
            try:
                raw = json.loads(self.identity_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    current_raw = raw.get("current")
                    history_raw = raw.get("history")
                    if isinstance(current_raw, dict):
                        current = LocalIdentity.from_payload(current_raw, fallback_created_at=now)
                        if current is not None:
                            history: list[LocalIdentity] = []
                            if isinstance(history_raw, list):
                                for item in history_raw:
                                    if not isinstance(item, dict):
                                        continue
                                    parsed = LocalIdentity.from_payload(item, fallback_created_at=now)
                                    if parsed is None or parsed.key_id == current.key_id:
                                        continue
                                    history.append(parsed)
                            self._persist_bundle(current=current, history=history)
                            return current, history

                    legacy = LocalIdentity.from_payload(raw, fallback_created_at=now)
                    if legacy is not None:
                        self._persist_bundle(current=legacy, history=[])
                        return legacy, []
            except Exception as exc:
                self.logger.warning("identity_load_failed path=%s error=%s", self.identity_path, exc)

        identity = self._generate_identity()
        self._persist_bundle(current=identity, history=[])
        return identity, []

    def _generate_identity(self) -> LocalIdentity:
        return LocalIdentity(
            key_id=str(uuid4()),
            algorithm="HMAC-SHA256",
            created_at=self._utc_now(),
            secret_b64=base64.b64encode(os.urandom(32)).decode("utf-8"),
        )

    def _persist_bundle(self, *, current: LocalIdentity, history: list[LocalIdentity]) -> None:
        payload = {
            "current": current.to_payload(),
            "history": [item.to_payload() for item in history],
        }
        self._write_file_atomic(payload)

    def _write_file_atomic(self, payload: dict[str, Any]) -> None:
        temp_path = self.identity_path.with_suffix(f"{self.identity_path.suffix}.tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, self.identity_path)
        try:
            os.chmod(self.identity_path, 0o600)
        except Exception:
            self.logger.debug("identity_chmod_failed path=%s", self.identity_path)

    @staticmethod
    def _identity_info(identity: LocalIdentity) -> dict[str, Any]:
        return {
            "key_id": identity.key_id,
            "algorithm": identity.algorithm,
            "created_at": identity.created_at,
            "fingerprint": identity.fingerprint,
        }

    def _find_identity_by_key_id(self, key_id: str) -> LocalIdentity | None:
        if key_id == self._identity.key_id:
            return self._identity
        for item in self._history:
            if key_id == item.key_id:
                return item
        return None

    @staticmethod
    def _payload_hash(payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _sign_payload(self, payload: dict[str, Any], *, identity: LocalIdentity) -> str:
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        digest = hmac.new(
            identity.secret_bytes,
            encoded.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")


class SecurityManager:
    def __init__(
        self,
        *,
        identity_manager: LocalIdentityManager,
        database: Database,
        telemetry: TelemetrySink | None = None,
    ) -> None:
        self.logger = logging.getLogger("amaryllis.security.manager")
        self.identity_manager = identity_manager
        self.database = database
        self.telemetry = telemetry

    def identity_info(self) -> dict[str, Any]:
        return self.identity_manager.info()

    def rotate_identity(
        self,
        *,
        actor: str | None,
        request_id: str | None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        rotation = self.identity_manager.rotate()
        payload = {
            "reason": str(reason or "").strip() or "manual_rotation",
            "previous_key_id": str(rotation.get("previous", {}).get("key_id", "")),
            "current_key_id": str(rotation.get("current", {}).get("key_id", "")),
        }
        receipt = self.identity_manager.sign(
            action="identity_rotate",
            payload=payload,
            request_id=request_id,
            actor=actor,
        )
        self.database.add_security_audit_event(
            event_type="identity_rotation",
            action="identity_rotate",
            actor=actor,
            request_id=request_id,
            target_type="identity",
            target_id=payload["current_key_id"],
            status="succeeded",
            details=payload,
            signature=receipt,
        )
        self._emit(
            "security_identity_rotated",
            {
                "actor": actor,
                "request_id": request_id,
                "previous_key_id": payload["previous_key_id"],
                "current_key_id": payload["current_key_id"],
            },
        )
        return {
            "rotation": rotation,
            "action_receipt": receipt,
        }

    def signed_action(
        self,
        *,
        action: str,
        payload: dict[str, Any],
        request_id: str | None,
        actor: str | None,
        target_type: str | None = None,
        target_id: str | None = None,
        event_type: str = "signed_action",
        status: str = "succeeded",
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        receipt = self.identity_manager.sign(
            action=action,
            payload=payload,
            request_id=request_id,
            actor=actor,
        )
        self.database.add_security_audit_event(
            event_type=event_type,
            action=action,
            actor=actor,
            request_id=request_id,
            target_type=target_type,
            target_id=target_id,
            status=status,
            details=details or {},
            signature=receipt,
        )
        self._emit(
            "security_signed_action",
            {
                "action": action,
                "actor": actor,
                "request_id": request_id,
                "target_type": target_type,
                "target_id": target_id,
                "status": status,
            },
        )
        return receipt

    def list_audit_events(
        self,
        *,
        limit: int = 200,
        event_type: str | None = None,
        action: str | None = None,
        status: str | None = None,
        actor: str | None = None,
        request_id: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.database.list_security_audit_events(
            limit=limit,
            event_type=event_type,
            action=action,
            status=status,
            actor=actor,
            request_id=request_id,
            target_type=target_type,
            target_id=target_id,
        )

    def record_authenticated_request(
        self,
        *,
        auth_context: AuthContext,
        request_id: str | None,
        path: str,
        method: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.database.record_auth_token_activity(
            token_fingerprint=auth_context.token_id,
            user_id=auth_context.user_id,
            scopes=sorted(auth_context.scopes),
            request_id=request_id,
            path=path,
            method=method,
            metadata=metadata,
        )
        self._emit(
            "security_auth_success",
            {
                "request_id": request_id,
                "user_id": auth_context.user_id,
                "token_id": auth_context.token_id,
                "path": path,
                "method": method,
            },
        )

    def list_auth_token_activity(
        self,
        *,
        limit: int = 200,
        user_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.database.list_auth_token_activity(limit=limit, user_id=user_id)

    def audit_access_denied(
        self,
        *,
        denial_type: str,
        request_id: str | None,
        actor: str | None,
        path: str,
        method: str,
        message: str,
        status_code: int,
        scopes: list[str] | None = None,
    ) -> None:
        normalized = str(denial_type or "").strip().lower()
        if normalized == "authentication_error":
            event_type = "authn_fail"
            action = "authentication_denied"
        else:
            event_type = "authz_deny"
            action = "authorization_denied"
        details = {
            "path": path,
            "method": method,
            "message": message,
            "status_code": int(status_code),
            "scopes": sorted(set(scopes or [])),
        }
        self.database.add_security_audit_event(
            event_type=event_type,
            action=action,
            actor=actor,
            request_id=request_id,
            target_type="http_endpoint",
            target_id=path,
            status="failed",
            details=details,
            signature={},
        )
        self._emit(
            "security_access_denied",
            {
                "event_type": event_type,
                "actor": actor,
                "request_id": request_id,
                "path": path,
                "method": method,
                "status_code": int(status_code),
            },
        )

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.telemetry is None:
            return
        try:
            self.telemetry.emit(event_type=event_type, payload=payload)
        except Exception:
            self.logger.debug("security_telemetry_emit_failed event=%s", event_type)
