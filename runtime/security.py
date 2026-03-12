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


class LocalIdentityManager:
    def __init__(self, identity_path: Path) -> None:
        self.logger = logging.getLogger("amaryllis.security.identity")
        self.identity_path = Path(identity_path)
        self.identity_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._identity: LocalIdentity = self._load_or_create()
        try:
            os.chmod(self.identity_path, 0o600)
        except Exception:
            self.logger.debug("identity_chmod_failed path=%s", self.identity_path)

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def info(self) -> dict[str, Any]:
        with self._lock:
            identity = self._identity
        return {
            "key_id": identity.key_id,
            "algorithm": identity.algorithm,
            "created_at": identity.created_at,
            "fingerprint": identity.fingerprint,
        }

    def sign(
        self,
        *,
        action: str,
        payload: dict[str, Any],
        request_id: str | None,
        actor: str | None,
    ) -> dict[str, Any]:
        now = self._utc_now()
        nonce = str(uuid4())
        payload_hash = self._payload_hash(payload)
        signable = {
            "key_id": self._identity.key_id,
            "algorithm": self._identity.algorithm,
            "action": action,
            "timestamp": now,
            "nonce": nonce,
            "request_id": request_id or "",
            "actor": actor or "",
            "payload_hash": payload_hash,
        }
        signature = self._sign_payload(signable)
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
        if str(receipt.get("key_id")) != self._identity.key_id:
            return False
        if str(receipt.get("algorithm")) != self._identity.algorithm:
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
        expected_signature = self._sign_payload(copy)
        return hmac.compare_digest(expected_signature, str(receipt.get("signature")))

    def _load_or_create(self) -> LocalIdentity:
        if self.identity_path.exists():
            try:
                raw = json.loads(self.identity_path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    key_id = str(raw.get("key_id", "")).strip()
                    algorithm = str(raw.get("algorithm", "HMAC-SHA256")).strip() or "HMAC-SHA256"
                    created_at = str(raw.get("created_at", "")).strip() or self._utc_now()
                    secret_b64 = str(raw.get("secret_b64", "")).strip()
                    if key_id and secret_b64:
                        return LocalIdentity(
                            key_id=key_id,
                            algorithm=algorithm,
                            created_at=created_at,
                            secret_b64=secret_b64,
                        )
            except Exception as exc:
                self.logger.warning("identity_load_failed path=%s error=%s", self.identity_path, exc)

        secret_b64 = base64.b64encode(os.urandom(32)).decode("utf-8")
        created_at = self._utc_now()
        key_id = str(uuid4())
        identity = LocalIdentity(
            key_id=key_id,
            algorithm="HMAC-SHA256",
            created_at=created_at,
            secret_b64=secret_b64,
        )
        payload = {
            "key_id": identity.key_id,
            "algorithm": identity.algorithm,
            "created_at": identity.created_at,
            "secret_b64": identity.secret_b64,
        }
        self.identity_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(self.identity_path, 0o600)
        except Exception:
            self.logger.debug("identity_chmod_failed path=%s", self.identity_path)
        return identity

    @staticmethod
    def _payload_hash(payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def _sign_payload(self, payload: dict[str, Any]) -> str:
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        digest = hmac.new(
            self._identity.secret_bytes,
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

    def signed_action(
        self,
        *,
        action: str,
        payload: dict[str, Any],
        request_id: str | None,
        actor: str | None,
        target_type: str | None = None,
        target_id: str | None = None,
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
            event_type="signed_action",
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
        action: str | None = None,
        status: str | None = None,
        actor: str | None = None,
    ) -> list[dict[str, Any]]:
        return self.database.list_security_audit_events(
            limit=limit,
            action=action,
            status=status,
            actor=actor,
        )

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.telemetry is None:
            return
        try:
            self.telemetry.emit(event_type=event_type, payload=payload)
        except Exception:
            self.logger.debug("security_telemetry_emit_failed event=%s", event_type)
