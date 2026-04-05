from __future__ import annotations

from email.message import EmailMessage
import hashlib
import json
import os
import re
import smtplib
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

SUPPORTED_NEWS_OUTBOUND_CHANNELS: tuple[str, ...] = ("webhook", "email", "telegram")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_outbound_channel(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in SUPPORTED_NEWS_OUTBOUND_CHANNELS:
        return ""
    return normalized


def normalize_outbound_policy_rows(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not rows:
        return []
    normalized_rows: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        channel = normalize_outbound_channel(row.get("channel"))
        if not channel:
            continue
        targets: list[str] = []
        raw_targets = row.get("targets")
        if isinstance(raw_targets, list):
            for item in raw_targets:
                target = str(item or "").strip()
                if not target or target in targets:
                    continue
                targets.append(target)
                if len(targets) >= 20:
                    break
        max_targets_raw = row.get("max_targets")
        try:
            max_targets = int(max_targets_raw)
        except Exception:
            max_targets = 3
        normalized_rows.append(
            {
                "channel": channel,
                "topic": str(row.get("topic") or "*").strip() or "*",
                "is_enabled": bool(row.get("is_enabled", row.get("enabled", True))),
                "max_targets": max(1, min(max_targets, 20)),
                "targets": targets,
                "options": row.get("options") if isinstance(row.get("options"), dict) else {},
            }
        )
    return normalized_rows


def _digest_hash(digest: dict[str, Any]) -> str:
    payload = json.dumps(digest, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _digest_summary_text(topic: str, digest: dict[str, Any]) -> str:
    header = f"News Digest: {topic}"
    summary = str(digest.get("summary") or "").strip()
    lines: list[str] = [header, "", summary] if summary else [header]
    sections = digest.get("sections")
    if isinstance(sections, list):
        for section in sections[:6]:
            if not isinstance(section, dict):
                continue
            headline = str(section.get("headline") or "Update").strip() or "Update"
            confidence = str(section.get("confidence") or "unknown").strip().lower() or "unknown"
            refs = section.get("source_refs")
            first_url = ""
            if isinstance(refs, list):
                for ref in refs:
                    if not isinstance(ref, dict):
                        continue
                    candidate = str(ref.get("url") or "").strip()
                    if candidate:
                        first_url = candidate
                        break
            lines.append(f"- {headline} [{confidence}]")
            if first_url:
                lines.append(f"  {first_url}")
    return "\n".join(item for item in lines if item).strip()


def _digest_payload(topic: str, digest: dict[str, Any], *, digest_hash: str) -> dict[str, Any]:
    metrics = digest.get("metrics") if isinstance(digest.get("metrics"), dict) else {}
    sections = digest.get("sections") if isinstance(digest.get("sections"), list) else []
    return {
        "topic": str(topic or "").strip(),
        "summary": str(digest.get("summary") or "").strip(),
        "sections": sections,
        "top_links": list(digest.get("top_links") or [])[:20],
        "metrics": metrics,
        "citation_policy": digest.get("citation_policy") if isinstance(digest.get("citation_policy"), dict) else {},
        "digest_hash": digest_hash,
    }


class NewsDigestOutboundDispatcher:
    def __init__(
        self,
        *,
        webhook_timeout_sec: float = 8.0,
        smtp_host: str | None = None,
        smtp_port: int | None = None,
        smtp_username: str | None = None,
        smtp_password: str | None = None,
        smtp_from: str | None = None,
        telegram_bot_token: str | None = None,
    ) -> None:
        self.webhook_timeout_sec = max(1.0, float(webhook_timeout_sec))
        self.smtp_host = str(smtp_host or os.getenv("AMARYLLIS_NEWS_SMTP_HOST", "")).strip() or None
        smtp_port_raw = smtp_port if smtp_port is not None else os.getenv("AMARYLLIS_NEWS_SMTP_PORT", "587")
        try:
            self.smtp_port = int(smtp_port_raw)
        except Exception:
            self.smtp_port = 587
        self.smtp_username = str(smtp_username or os.getenv("AMARYLLIS_NEWS_SMTP_USERNAME", "")).strip() or None
        self.smtp_password = str(smtp_password or os.getenv("AMARYLLIS_NEWS_SMTP_PASSWORD", "")).strip() or None
        self.smtp_from = str(smtp_from or os.getenv("AMARYLLIS_NEWS_SMTP_FROM", "")).strip() or None
        self.telegram_bot_token = (
            str(telegram_bot_token or os.getenv("AMARYLLIS_NEWS_TELEGRAM_BOT_TOKEN", "")).strip() or None
        )

    def dispatch(
        self,
        *,
        topic: str,
        digest: dict[str, Any],
        policy_rows: list[dict[str, Any]] | None,
        channels: list[str] | None = None,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        normalized_rows = normalize_outbound_policy_rows(policy_rows)
        if not normalized_rows:
            return {
                "dry_run": bool(dry_run),
                "digest_hash": _digest_hash(digest),
                "channels": [],
                "events": [],
                "summary": {
                    "channels_considered": 0,
                    "channels_sent": 0,
                    "attempted_targets": 0,
                    "delivered_targets": 0,
                    "failed_targets": 0,
                    "skipped_targets": 0,
                },
            }
        requested_channels = {normalize_outbound_channel(item) for item in (channels or [])}
        requested_channels.discard("")
        digest_hash = _digest_hash(digest)
        payload = _digest_payload(topic=topic, digest=digest, digest_hash=digest_hash)
        plain_text = _digest_summary_text(topic=topic, digest=digest)
        channel_reports: list[dict[str, Any]] = []
        events: list[dict[str, Any]] = []

        considered = 0
        sent_channels = 0
        attempted_targets = 0
        delivered_targets = 0
        failed_targets = 0
        skipped_targets = 0

        for row in normalized_rows:
            channel = str(row.get("channel") or "")
            if requested_channels and channel not in requested_channels:
                continue
            considered += 1
            enabled = bool(row.get("is_enabled", True))
            max_targets = max(1, int(row.get("max_targets") or 1))
            targets = [str(item).strip() for item in row.get("targets", []) if str(item).strip()]
            options = row.get("options") if isinstance(row.get("options"), dict) else {}
            report: dict[str, Any] = {
                "channel": channel,
                "enabled": enabled,
                "max_targets": max_targets,
                "target_count": len(targets),
                "results": [],
            }
            if not enabled:
                report["status"] = "skipped_disabled"
                channel_reports.append(report)
                continue
            if not targets:
                report["status"] = "skipped_no_targets"
                channel_reports.append(report)
                continue

            selected_targets = targets[:max_targets]
            dropped_count = max(0, len(targets) - len(selected_targets))
            if dropped_count > 0:
                report["dropped_targets"] = dropped_count
            sent_channels += 1

            for target in selected_targets:
                attempted_targets += 1
                result = self._deliver_target(
                    channel=channel,
                    target=target,
                    topic=topic,
                    digest_payload=payload,
                    digest_text=plain_text,
                    options=options,
                    dry_run=bool(dry_run),
                )
                report["results"].append(result)
                event_payload = {
                    "channel": channel,
                    "target": target,
                    "status": str(result.get("status") or ""),
                    "detail": result.get("detail"),
                    "digest_hash": digest_hash,
                    "metadata": {
                        "dry_run": bool(dry_run),
                        "http_status": result.get("http_status"),
                    },
                }
                events.append(event_payload)
                status = str(result.get("status") or "")
                if status.startswith("delivered"):
                    delivered_targets += 1
                elif status.startswith("skipped"):
                    skipped_targets += 1
                else:
                    failed_targets += 1

            result_statuses = {
                str(item.get("status") or "")
                for item in report["results"]
                if isinstance(item, dict)
            }
            if result_statuses and all(status.startswith("delivered") for status in result_statuses):
                report["status"] = "delivered"
            elif result_statuses and any(status.startswith("delivered") for status in result_statuses):
                report["status"] = "partial"
            elif result_statuses and all(status.startswith("skipped") for status in result_statuses):
                report["status"] = "skipped"
            else:
                report["status"] = "failed"
            channel_reports.append(report)

        return {
            "dry_run": bool(dry_run),
            "digest_hash": digest_hash,
            "channels": channel_reports,
            "events": events,
            "summary": {
                "channels_considered": considered,
                "channels_sent": sent_channels,
                "attempted_targets": attempted_targets,
                "delivered_targets": delivered_targets,
                "failed_targets": failed_targets,
                "skipped_targets": skipped_targets,
            },
        }

    def _deliver_target(
        self,
        *,
        channel: str,
        target: str,
        topic: str,
        digest_payload: dict[str, Any],
        digest_text: str,
        options: dict[str, Any],
        dry_run: bool,
    ) -> dict[str, Any]:
        if channel == "webhook":
            return self._deliver_webhook(target=target, digest_payload=digest_payload, options=options, dry_run=dry_run)
        if channel == "email":
            return self._deliver_email(target=target, topic=topic, digest_text=digest_text, options=options, dry_run=dry_run)
        if channel == "telegram":
            return self._deliver_telegram(target=target, digest_text=digest_text, options=options, dry_run=dry_run)
        return {"target": target, "status": "failed_unsupported_channel", "detail": f"unsupported channel: {channel}"}

    def _deliver_webhook(
        self,
        *,
        target: str,
        digest_payload: dict[str, Any],
        options: dict[str, Any],
        dry_run: bool,
    ) -> dict[str, Any]:
        parsed = urlparse(target)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return {
                "target": target,
                "status": "failed_invalid_target",
                "detail": "webhook target must be a valid http(s) URL",
            }
        if dry_run:
            return {"target": target, "status": "delivered_dry_run", "detail": "webhook payload validated (dry-run)"}
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "User-Agent": "amaryllis-news-delivery/1.0",
        }
        custom_headers = options.get("headers")
        if isinstance(custom_headers, dict):
            for key, value in custom_headers.items():
                header_key = str(key or "").strip()
                header_value = str(value or "").strip()
                if header_key and header_value:
                    headers[header_key] = header_value
        body = json.dumps(digest_payload, ensure_ascii=False).encode("utf-8")
        request = Request(url=target, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=self.webhook_timeout_sec) as response:
                status = int(getattr(response, "status", response.getcode()))
        except HTTPError as exc:
            return {
                "target": target,
                "status": "failed_http_error",
                "detail": str(exc.reason),
                "http_status": int(getattr(exc, "code", 0) or 0),
            }
        except URLError as exc:
            return {
                "target": target,
                "status": "failed_network_error",
                "detail": str(exc.reason),
            }
        except Exception as exc:
            return {
                "target": target,
                "status": "failed_runtime_error",
                "detail": str(exc),
            }
        if 200 <= status < 300:
            return {
                "target": target,
                "status": "delivered",
                "detail": "webhook accepted payload",
                "http_status": status,
            }
        return {
            "target": target,
            "status": "failed_http_status",
            "detail": f"unexpected webhook response status: {status}",
            "http_status": status,
        }

    def _deliver_email(
        self,
        *,
        target: str,
        topic: str,
        digest_text: str,
        options: dict[str, Any],
        dry_run: bool,
    ) -> dict[str, Any]:
        if not _EMAIL_RE.match(target):
            return {
                "target": target,
                "status": "failed_invalid_target",
                "detail": "email target must be a valid email address",
            }
        if dry_run:
            return {"target": target, "status": "delivered_dry_run", "detail": "email payload validated (dry-run)"}
        if not self.smtp_host or not self.smtp_from:
            return {
                "target": target,
                "status": "skipped_config_missing",
                "detail": "smtp host/from is not configured",
            }
        subject = str(options.get("subject") or f"News Digest: {topic}").strip() or f"News Digest: {topic}"
        use_ssl = bool(options.get("use_ssl"))
        use_starttls = bool(options.get("use_starttls", not use_ssl))
        message = EmailMessage()
        message["From"] = self.smtp_from
        message["To"] = target
        message["Subject"] = subject
        message.set_content(digest_text)
        try:
            if use_ssl:
                with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=self.webhook_timeout_sec) as client:
                    if self.smtp_username and self.smtp_password:
                        client.login(self.smtp_username, self.smtp_password)
                    client.send_message(message)
            else:
                with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=self.webhook_timeout_sec) as client:
                    if use_starttls:
                        client.starttls()
                    if self.smtp_username and self.smtp_password:
                        client.login(self.smtp_username, self.smtp_password)
                    client.send_message(message)
        except Exception as exc:
            return {
                "target": target,
                "status": "failed_runtime_error",
                "detail": str(exc),
            }
        return {"target": target, "status": "delivered", "detail": "email delivered"}

    def _deliver_telegram(
        self,
        *,
        target: str,
        digest_text: str,
        options: dict[str, Any],
        dry_run: bool,
    ) -> dict[str, Any]:
        chat_id = str(target or "").strip()
        if not chat_id:
            return {
                "target": target,
                "status": "failed_invalid_target",
                "detail": "telegram target must be a non-empty chat_id",
            }
        if dry_run:
            return {"target": target, "status": "delivered_dry_run", "detail": "telegram payload validated (dry-run)"}
        if not self.telegram_bot_token:
            return {
                "target": target,
                "status": "skipped_config_missing",
                "detail": "telegram bot token is not configured",
            }
        disable_preview = bool(options.get("disable_web_preview", True))
        payload = urlencode(
            {
                "chat_id": chat_id,
                "text": digest_text[:4000],
                "disable_web_page_preview": "true" if disable_preview else "false",
            }
        ).encode("utf-8")
        request = Request(
            url=f"https://api.telegram.org/bot{self.telegram_bot_token}/sendMessage",
            data=payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "amaryllis-news-delivery/1.0",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.webhook_timeout_sec) as response:
                status = int(getattr(response, "status", response.getcode()))
                raw = response.read().decode("utf-8", errors="ignore")
        except HTTPError as exc:
            return {
                "target": target,
                "status": "failed_http_error",
                "detail": str(exc.reason),
                "http_status": int(getattr(exc, "code", 0) or 0),
            }
        except URLError as exc:
            return {
                "target": target,
                "status": "failed_network_error",
                "detail": str(exc.reason),
            }
        except Exception as exc:
            return {
                "target": target,
                "status": "failed_runtime_error",
                "detail": str(exc),
            }
        if not (200 <= status < 300):
            return {
                "target": target,
                "status": "failed_http_status",
                "detail": f"unexpected telegram response status: {status}",
                "http_status": status,
            }
        try:
            payload_json = json.loads(raw or "{}")
        except Exception:
            payload_json = {}
        if payload_json.get("ok") is False:
            return {
                "target": target,
                "status": "failed_provider_rejected",
                "detail": str(payload_json.get("description") or "telegram rejected request"),
                "http_status": status,
            }
        return {
            "target": target,
            "status": "delivered",
            "detail": "telegram message delivered",
            "http_status": status,
        }
