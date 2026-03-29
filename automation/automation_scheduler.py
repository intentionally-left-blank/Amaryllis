from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event, Thread
from typing import Any, Protocol
from uuid import uuid4

from agents.agent import Agent
from agents.agent_run_manager import AgentRunManager, AutonomyCircuitBreakerBlockedError
from automation.mission_policy import resolve_mission_policy_overlay
from automation.schedule import compute_next_run_at, normalize_schedule, validate_timezone
from storage.database import Database


class TelemetrySink(Protocol):
    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        ...


class AutomationScheduler:
    def __init__(
        self,
        database: Database,
        run_manager: AgentRunManager,
        poll_interval_sec: float = 2.0,
        batch_size: int = 10,
        escalation_warning_threshold: int = 2,
        escalation_critical_threshold: int = 4,
        escalation_disable_threshold: int = 6,
        lease_ttl_sec: int = 30,
        backoff_base_sec: float = 5.0,
        backoff_max_sec: float = 300.0,
        circuit_failure_threshold: int = 4,
        circuit_open_sec: float = 120.0,
        telemetry: TelemetrySink | None = None,
    ) -> None:
        self.logger = logging.getLogger("amaryllis.automation.scheduler")
        self.database = database
        self.run_manager = run_manager
        self.poll_interval_sec = max(0.5, float(poll_interval_sec))
        self.batch_size = max(1, int(batch_size))
        self.escalation_warning_threshold = max(1, int(escalation_warning_threshold))
        self.escalation_critical_threshold = max(
            self.escalation_warning_threshold,
            int(escalation_critical_threshold),
        )
        self.escalation_disable_threshold = max(
            self.escalation_critical_threshold + 1,
            int(escalation_disable_threshold),
        )
        self.lease_ttl_sec = max(5, int(lease_ttl_sec))
        self.backoff_base_sec = max(1.0, float(backoff_base_sec))
        self.backoff_max_sec = max(self.backoff_base_sec, float(backoff_max_sec))
        self.circuit_failure_threshold = max(1, int(circuit_failure_threshold))
        self.circuit_open_sec = max(1.0, float(circuit_open_sec))
        self.telemetry = telemetry
        self._lease_owner = f"scheduler-{uuid4()}"

        self._thread: Thread | None = None
        self._stop = Event()
        self._started = False

    @staticmethod
    def _utc_now() -> datetime:
        return datetime.now(timezone.utc)

    def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._stop.clear()
        self._thread = Thread(target=self._loop, name="amaryllis-automation-scheduler", daemon=True)
        self._thread.start()
        self.logger.info(
            (
                "automation_scheduler_started poll_interval_sec=%s batch_size=%s "
                "escalation_warning=%s escalation_critical=%s escalation_disable=%s "
                "lease_ttl_sec=%s backoff_base_sec=%s backoff_max_sec=%s "
                "circuit_failure_threshold=%s circuit_open_sec=%s"
            ),
            self.poll_interval_sec,
            self.batch_size,
            self.escalation_warning_threshold,
            self.escalation_critical_threshold,
            self.escalation_disable_threshold,
            self.lease_ttl_sec,
            self.backoff_base_sec,
            self.backoff_max_sec,
            self.circuit_failure_threshold,
            self.circuit_open_sec,
        )

    def stop(self) -> None:
        if not self._started:
            return
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)
        self._thread = None
        self._started = False
        self.logger.info("automation_scheduler_stopped")

    def create_automation(
        self,
        *,
        agent_id: str,
        user_id: str,
        session_id: str | None,
        message: str,
        interval_sec: int | None = None,
        schedule_type: str | None = None,
        schedule: dict[str, Any] | None = None,
        timezone_name: str = "UTC",
        start_immediately: bool = False,
        mission_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self._assert_agent_owner(agent_id=agent_id, user_id=user_id)

        automation_id = str(uuid4())
        raw_schedule = schedule if isinstance(schedule, dict) else {}
        normalized_type, normalized_schedule, normalized_interval = normalize_schedule(
            schedule_type=schedule_type,
            schedule=schedule,
            interval_sec=interval_sec,
        )
        if normalized_type == "watch_fs" and "last_seen_mtime_ns" not in raw_schedule:
            normalized_schedule["last_seen_mtime_ns"] = self._current_watch_cursor(normalized_schedule)
        normalized_timezone = validate_timezone(timezone_name)
        normalized_policy = resolve_mission_policy_overlay(
            policy=mission_policy if isinstance(mission_policy, dict) else {},
            defaults=self._default_mission_policy(),
        )

        now = self._utc_now()
        next_run_at = (
            now.isoformat()
            if start_immediately
            else compute_next_run_at(
                schedule_type=normalized_type,
                schedule=normalized_schedule,
                timezone_name=normalized_timezone,
                now_utc=now,
            )
        )

        with self.database.write_transaction():
            self.database.create_automation(
                automation_id=automation_id,
                agent_id=agent_id,
                user_id=user_id,
                session_id=session_id,
                message=message,
                interval_sec=normalized_interval,
                next_run_at=next_run_at,
                schedule_type=normalized_type,
                schedule=normalized_schedule,
                timezone_name=normalized_timezone,
                mission_policy=normalized_policy,
            )
            self.database.add_automation_event(
                automation_id=automation_id,
                event_type="created",
                message=(
                    f"Automation created "
                    f"(schedule_type={normalized_type}, timezone={normalized_timezone}, "
                    f"policy={normalized_policy.get('profile')})."
                ),
            )
        self._emit(
            "automation_created",
            {
                "automation_id": automation_id,
                "agent_id": agent_id,
                "user_id": user_id,
                "schedule_type": normalized_type,
                "timezone": normalized_timezone,
                "mission_policy_profile": str(normalized_policy.get("profile") or "unknown"),
            },
        )
        created = self.database.get_automation(automation_id)
        assert created is not None
        return created

    def update_automation(
        self,
        automation_id: str,
        *,
        message: str | None = None,
        session_id: str | None = None,
        interval_sec: int | None = None,
        schedule_type: str | None = None,
        schedule: dict[str, Any] | None = None,
        timezone_name: str | None = None,
        mission_policy: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        automation = self.database.get_automation(automation_id)
        if automation is None:
            raise ValueError(f"Automation not found: {automation_id}")

        current_type = str(automation.get("schedule_type", "interval"))
        current_schedule = automation.get("schedule")
        if not isinstance(current_schedule, dict):
            current_schedule = {}
        current_interval = int(automation.get("interval_sec", 300))
        provided_schedule = schedule if isinstance(schedule, dict) else None

        normalized_type, normalized_schedule, normalized_interval = normalize_schedule(
            schedule_type=schedule_type or current_type,
            schedule=schedule if schedule is not None else current_schedule,
            interval_sec=interval_sec if interval_sec is not None else current_interval,
        )
        if normalized_type == "watch_fs" and provided_schedule is not None and "last_seen_mtime_ns" not in provided_schedule:
            normalized_schedule["last_seen_mtime_ns"] = self._current_watch_cursor(normalized_schedule)
        normalized_timezone = validate_timezone(timezone_name or str(automation.get("timezone", "UTC")))

        updates: dict[str, Any] = {
            "interval_sec": normalized_interval,
            "schedule_type": normalized_type,
            "schedule_json": normalized_schedule,
            "timezone": normalized_timezone,
            "last_error": None,
            "consecutive_failures": 0,
            "escalation_level": "none",
            "lease_owner": None,
            "lease_expires_at": None,
            "backoff_until": None,
            "circuit_open_until": None,
        }
        if message is not None:
            updates["message"] = message
        if session_id is not None:
            updates["session_id"] = session_id
        if mission_policy is not None:
            updates["mission_policy_json"] = resolve_mission_policy_overlay(
                policy=mission_policy,
                defaults=self._default_mission_policy(),
            )

        if bool(automation.get("is_enabled", False)):
            updates["next_run_at"] = compute_next_run_at(
                schedule_type=normalized_type,
                schedule=normalized_schedule,
                timezone_name=normalized_timezone,
                now_utc=self._utc_now(),
            )

        with self.database.write_transaction():
            self.database.update_automation_fields(automation_id, **updates)
            self.database.add_automation_event(
                automation_id=automation_id,
                event_type="updated",
                message=(
                    f"Automation updated "
                    f"(schedule_type={normalized_type}, timezone={normalized_timezone}"
                    f"{', policy=' + str(updates['mission_policy_json'].get('profile')) if 'mission_policy_json' in updates else ''})."
                ),
            )
        updated = self.database.get_automation(automation_id)
        assert updated is not None
        return updated

    def list_automations(
        self,
        *,
        user_id: str | None = None,
        agent_id: str | None = None,
        enabled: bool | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        return self.database.list_automations(
            user_id=user_id,
            agent_id=agent_id,
            enabled=enabled,
            limit=limit,
        )

    def get_automation(self, automation_id: str) -> dict[str, Any] | None:
        return self.database.get_automation(automation_id)

    def pause_automation(self, automation_id: str) -> dict[str, Any]:
        automation = self.database.get_automation(automation_id)
        if automation is None:
            raise ValueError(f"Automation not found: {automation_id}")

        with self.database.write_transaction():
            self.database.update_automation_fields(
                automation_id,
                is_enabled=False,
                lease_owner=None,
                lease_expires_at=None,
            )
            self.database.add_automation_event(
                automation_id=automation_id,
                event_type="paused",
                message="Automation paused.",
            )
        updated = self.database.get_automation(automation_id)
        assert updated is not None
        return updated

    def resume_automation(self, automation_id: str) -> dict[str, Any]:
        automation = self.database.get_automation(automation_id)
        if automation is None:
            raise ValueError(f"Automation not found: {automation_id}")

        schedule_type, schedule, _ = self._normalized_schedule_from_row(automation)
        timezone_name = validate_timezone(str(automation.get("timezone", "UTC")))
        next_run_at = compute_next_run_at(
            schedule_type=schedule_type,
            schedule=schedule,
            timezone_name=timezone_name,
            now_utc=self._utc_now(),
        )
        with self.database.write_transaction():
            self.database.update_automation_fields(
                automation_id,
                is_enabled=True,
                next_run_at=next_run_at,
                last_error=None,
                consecutive_failures=0,
                escalation_level="none",
                lease_owner=None,
                lease_expires_at=None,
                backoff_until=None,
                circuit_open_until=None,
            )
            self.database.add_automation_event(
                automation_id=automation_id,
                event_type="resumed",
                message="Automation resumed.",
            )
        updated = self.database.get_automation(automation_id)
        assert updated is not None
        return updated

    def run_now(self, automation_id: str) -> dict[str, Any]:
        automation = self.database.get_automation(automation_id)
        if automation is None:
            raise ValueError(f"Automation not found: {automation_id}")
        self._trigger(automation, source="manual")
        updated = self.database.get_automation(automation_id)
        assert updated is not None
        return updated

    def delete_automation(self, automation_id: str) -> bool:
        automation = self.database.get_automation(automation_id)
        if automation is None:
            return False
        return self.database.delete_automation(automation_id)

    def list_events(self, automation_id: str, limit: int = 100) -> list[dict[str, Any]]:
        return self.database.list_automation_events(automation_id=automation_id, limit=limit)

    def list_inbox_items(
        self,
        *,
        user_id: str | None = None,
        unread_only: bool = False,
        category: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        return self.database.list_inbox_items(
            user_id=user_id,
            unread_only=unread_only,
            category=category,
            limit=limit,
        )

    def set_inbox_item_read(self, item_id: str, is_read: bool) -> dict[str, Any]:
        item = self.database.set_inbox_item_read(item_id=item_id, is_read=is_read)
        if item is None:
            raise ValueError(f"Inbox item not found: {item_id}")
        return item

    def health_snapshot(
        self,
        *,
        user_id: str | None = None,
        limit: int = 500,
    ) -> dict[str, Any]:
        bounded_limit = max(1, min(int(limit), 5000))
        automations = self.database.list_automations(user_id=user_id, limit=2000)
        selected_ids = {str(item["id"]) for item in automations}
        events = self.database.list_recent_automation_events(limit=bounded_limit)
        if user_id:
            events = [event for event in events if str(event.get("automation_id")) in selected_ids]

        now = self._utc_now()
        enabled_count = 0
        warning_count = 0
        critical_count = 0
        lease_active_count = 0
        backoff_active_count = 0
        circuit_open_count = 0
        mission_policy_profiles: dict[str, int] = {}
        for automation in automations:
            if bool(automation.get("is_enabled", False)):
                enabled_count += 1
            level = str(automation.get("escalation_level", "none")).strip().lower()
            if level == "warning":
                warning_count += 1
            elif level == "critical":
                critical_count += 1
            if self._is_future_timestamp(automation.get("lease_expires_at"), now=now):
                lease_active_count += 1
            if self._is_future_timestamp(automation.get("backoff_until"), now=now):
                backoff_active_count += 1
            if self._is_future_timestamp(automation.get("circuit_open_until"), now=now):
                circuit_open_count += 1
            policy = automation.get("mission_policy")
            if isinstance(policy, dict):
                profile = str(policy.get("profile") or "").strip().lower() or "scheduler_default"
            else:
                profile = "scheduler_default"
            mission_policy_profiles[profile] = int(mission_policy_profiles.get(profile, 0)) + 1

        event_breakdown: dict[str, int] = {}
        for event in events:
            event_type = str(event.get("event_type") or "unknown").strip().lower() or "unknown"
            event_breakdown[event_type] = int(event_breakdown.get(event_type, 0)) + 1

        queued_count = int(event_breakdown.get("run_queued", 0))
        error_count = int(event_breakdown.get("run_error", 0))
        blocked_count = int(event_breakdown.get("run_blocked_autonomy_circuit_breaker", 0))
        dedup_count = int(event_breakdown.get("run_deduplicated", 0))
        attempts = queued_count + error_count
        success_rate = float(queued_count / attempts) if attempts > 0 else 1.0
        error_rate = float(error_count / attempts) if attempts > 0 else 0.0

        top_failures = sorted(
            [
                {
                    "automation_id": str(item["id"]),
                    "consecutive_failures": int(item.get("consecutive_failures", 0)),
                    "is_enabled": bool(item.get("is_enabled", False)),
                }
                for item in automations
                if int(item.get("consecutive_failures", 0)) > 0
            ],
            key=lambda row: int(row["consecutive_failures"]),
            reverse=True,
        )[:10]

        return {
            "total_automations": len(automations),
            "enabled_automations": enabled_count,
            "disabled_automations": max(0, len(automations) - enabled_count),
            "escalation": {
                "warning": warning_count,
                "critical": critical_count,
            },
            "runtime_state": {
                "lease_active": lease_active_count,
                "backoff_active": backoff_active_count,
                "circuit_open": circuit_open_count,
            },
            "recent_events": {
                "count": len(events),
                "breakdown": event_breakdown,
            },
            "slo": {
                "sample_size": attempts,
                "run_queue_success_rate": success_rate,
                "run_queue_error_rate": error_rate,
                "run_blocked_by_autonomy_circuit_breaker": blocked_count,
                "deduplicated_dispatches": dedup_count,
            },
            "top_failures": top_failures,
            "scheduler": {
                "poll_interval_sec": self.poll_interval_sec,
                "batch_size": self.batch_size,
                "lease_ttl_sec": self.lease_ttl_sec,
                "backoff_base_sec": self.backoff_base_sec,
                "backoff_max_sec": self.backoff_max_sec,
                "circuit_failure_threshold": self.circuit_failure_threshold,
                "circuit_open_sec": self.circuit_open_sec,
                "mission_policy_profiles": mission_policy_profiles,
            },
        }

    def _normalized_schedule_from_row(self, automation: dict[str, Any]) -> tuple[str, dict[str, Any], int]:
        row_schedule = automation.get("schedule")
        if not isinstance(row_schedule, dict):
            row_schedule = {}
        return normalize_schedule(
            schedule_type=str(automation.get("schedule_type", "interval")),
            schedule=row_schedule,
            interval_sec=int(automation.get("interval_sec", 300)),
        )

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as exc:
                self.logger.exception("automation_scheduler_tick_failed error=%s", exc)
            self._stop.wait(self.poll_interval_sec)

    def _tick(self) -> None:
        now = self._utc_now()
        now_iso = now.isoformat()
        lease_expires_at = (now + timedelta(seconds=self.lease_ttl_sec)).isoformat()
        due_items = self.database.claim_due_automations(
            now_iso=now_iso,
            limit=self.batch_size,
            lease_owner=self._lease_owner,
            lease_expires_at=lease_expires_at,
        )
        if not due_items:
            return
        for automation in due_items:
            automation_id = str(automation.get("id"))
            try:
                self._trigger(automation, source="scheduled")
            except Exception as exc:
                self.logger.error(
                    "automation_trigger_failed automation_id=%s error=%s",
                    automation_id,
                    exc,
                )
            finally:
                try:
                    self.database.release_automation_lease(
                        automation_id=automation_id,
                        lease_owner=self._lease_owner,
                    )
                except Exception as release_exc:
                    self.logger.warning(
                        "automation_lease_release_failed automation_id=%s lease_owner=%s error=%s",
                        automation_id,
                        self._lease_owner,
                        release_exc,
                    )

    def _trigger(self, automation: dict[str, Any], *, source: str) -> None:
        automation_id = str(automation["id"])
        schedule_type, schedule, interval_sec = self._normalized_schedule_from_row(automation)
        timezone_name = validate_timezone(str(automation.get("timezone", "UTC")))
        now = self._utc_now()
        now_iso = now.isoformat()
        user_id = str(automation["user_id"])
        previous_failures = max(0, int(automation.get("consecutive_failures", 0)))
        previous_level = str(automation.get("escalation_level", "none")).strip().lower() or "none"
        mission_policy = self._effective_mission_policy(automation)
        mission_slo = mission_policy["slo"]
        mission_policy_profile = str(mission_policy.get("profile") or "scheduler_default")
        source_name = str(source or "scheduled").strip().lower() or "scheduled"
        scheduled_slot = now_iso if source_name == "manual" else str(automation.get("next_run_at") or now_iso)
        dispatch_key = self._build_dispatch_key(
            source=source_name,
            schedule_type=schedule_type,
            slot_iso=scheduled_slot,
        )

        next_run_at = compute_next_run_at(
            schedule_type=schedule_type,
            schedule=schedule,
            timezone_name=timezone_name,
            now_utc=now,
        )

        dispatch_registered = False
        run_id: str | None = None
        try:
            run_message = str(automation["message"])
            changed_files: list[str] = []
            if schedule_type == "watch_fs":
                changed_files, schedule = self._scan_watch_changes(schedule)
                if source_name != "manual" and not changed_files:
                    self.database.update_automation_fields(
                        automation_id,
                        next_run_at=next_run_at,
                        interval_sec=interval_sec,
                        schedule_type=schedule_type,
                        schedule_json=schedule,
                        timezone=timezone_name,
                        last_dispatch_key=dispatch_key,
                    )
                    self._emit(
                        "automation_watch_idle",
                        {
                            "automation_id": automation_id,
                            "source": source_name,
                        },
                    )
                    return
                if changed_files:
                    run_message = self._build_watch_message(
                        base_message=run_message,
                        changed_files=changed_files,
                    )

            agent_record = self.database.get_agent(str(automation["agent_id"]))
            if agent_record is None:
                raise ValueError(f"Agent not found: {automation['agent_id']}")
            owner = str(agent_record.get("user_id") or "").strip()
            if not owner or owner != user_id:
                raise ValueError(f"Agent ownership mismatch for agent: {automation['agent_id']}")

            stale_before_iso = (
                now - timedelta(seconds=max(float(self.lease_ttl_sec) * 2.0, 30.0))
            ).isoformat()
            dispatch_registered = self.database.register_automation_dispatch(
                automation_id=automation_id,
                dispatch_key=dispatch_key,
                source=source_name,
                run_id=None,
                stale_before_iso=stale_before_iso if source_name == "scheduled" else None,
            )
            if not dispatch_registered:
                with self.database.write_transaction():
                    self.database.update_automation_fields(
                        automation_id,
                        next_run_at=next_run_at,
                        interval_sec=interval_sec,
                        schedule_type=schedule_type,
                        schedule_json=schedule,
                        timezone=timezone_name,
                        last_dispatch_key=dispatch_key,
                    )
                    self.database.add_automation_event(
                        automation_id=automation_id,
                        event_type="run_deduplicated",
                        message=f"Duplicate dispatch skipped ({source_name}) key={dispatch_key}.",
                    )
                self._emit(
                    "automation_run_deduplicated",
                    {
                        "automation_id": automation_id,
                        "source": source_name,
                        "dispatch_key": dispatch_key,
                    },
                )
                return

            try:
                run = self.run_manager.create_run(
                    agent=Agent.from_record(agent_record),
                    user_id=user_id,
                    session_id=automation.get("session_id"),
                    user_message=run_message,
                    run_source="automation",
                )
                run_id = str(run["id"])
            except AutonomyCircuitBreakerBlockedError as exc:
                blocked_reason = ""
                if exc.matched_scopes:
                    blocked_reason = str(exc.matched_scopes[0].get("reason") or "").strip()
                blocked_scope_tokens: list[str] = []
                for scope in exc.matched_scopes:
                    scope_type = str(scope.get("scope_type") or "").strip().lower()
                    if scope_type == "global":
                        blocked_scope_tokens.append("global")
                    elif scope_type == "user":
                        scope_user_id = str(scope.get("scope_user_id") or "").strip()
                        blocked_scope_tokens.append(f"user:{scope_user_id or 'unknown'}")
                    elif scope_type == "agent":
                        scope_agent_id = str(scope.get("scope_agent_id") or "").strip()
                        blocked_scope_tokens.append(f"agent:{scope_agent_id or 'unknown'}")
                    elif scope_type:
                        blocked_scope_tokens.append(scope_type)
                blocked_scope_summary = ", ".join(blocked_scope_tokens) if blocked_scope_tokens else "unknown"

                with self.database.write_transaction():
                    if dispatch_registered and run_id is None:
                        self.database.delete_automation_dispatch(
                            automation_id=automation_id,
                            dispatch_key=dispatch_key,
                        )
                    self.database.update_automation_fields(
                        automation_id,
                        last_run_at=now_iso,
                        next_run_at=next_run_at,
                        last_error=None,
                        interval_sec=interval_sec,
                        schedule_type=schedule_type,
                        schedule_json=schedule,
                        timezone=timezone_name,
                        last_dispatch_key=dispatch_key,
                    )
                    self.database.add_automation_event(
                        automation_id=automation_id,
                        event_type="run_blocked_autonomy_circuit_breaker",
                        message=(
                            "Automation run dispatch paused by autonomy circuit breaker "
                            f"({source_name}; scope={blocked_scope_summary}"
                            f"{'; reason=' + blocked_reason if blocked_reason else ''})."
                        ),
                    )
                self._emit(
                    "automation_run_blocked_autonomy_circuit_breaker",
                    {
                        "automation_id": automation_id,
                        "source": source_name,
                        "dispatch_key": dispatch_key,
                        "scope": blocked_scope_summary,
                        "reason": blocked_reason,
                        "mission_policy_profile": mission_policy_profile,
                    },
                )
                return

            recovered = previous_failures > 0 or previous_level != "none"
            with self.database.write_transaction():
                self.database.update_automation_dispatch_run_id(
                    automation_id=automation_id,
                    dispatch_key=dispatch_key,
                    run_id=run_id,
                )
                self.database.update_automation_fields(
                    automation_id,
                    last_run_at=now_iso,
                    next_run_at=next_run_at,
                    last_error=None,
                    interval_sec=interval_sec,
                    schedule_type=schedule_type,
                    schedule_json=schedule,
                    timezone=timezone_name,
                    consecutive_failures=0,
                    escalation_level="none",
                    backoff_until=None,
                    circuit_open_until=None,
                    last_dispatch_key=dispatch_key,
                )
                if recovered:
                    self.database.add_automation_event(
                        automation_id=automation_id,
                        event_type="recovered",
                        message="Automation recovered after previous failures.",
                        run_id=run_id,
                    )
                self.database.add_automation_event(
                    automation_id=automation_id,
                    event_type="run_queued",
                    message=(
                        f"Automation queued run ({source_name})"
                        if not changed_files
                        else (
                            f"Automation queued run ({source_name}); "
                            f"watcher detected {len(changed_files)} changed files."
                        )
                    ),
                    run_id=run_id,
                )
            if recovered:
                self._notify_recovered(
                    automation_id=automation_id,
                    user_id=user_id,
                    previous_failures=previous_failures,
                )
            if changed_files and source_name != "manual":
                self._notify_watch_triggered(
                    automation_id=automation_id,
                    user_id=user_id,
                    changed_files=changed_files,
                    watch_path=str(schedule.get("path", "")),
                    run_id=run_id,
                )
            self._emit(
                "automation_run_queued",
                {
                    "automation_id": automation_id,
                    "run_id": run_id,
                    "source": source_name,
                    "schedule_type": schedule_type,
                    "dispatch_key": dispatch_key,
                    "mission_policy_profile": mission_policy_profile,
                },
            )
        except Exception as exc:
            error = str(exc)
            failures = previous_failures + 1
            level = self._escalation_level_for_failures(
                failures,
                warning_threshold=int(mission_slo["warning_failures"]),
                critical_threshold=int(mission_slo["critical_failures"]),
            )
            disable_now = failures >= int(mission_slo["disable_failures"])
            backoff_seconds = self._backoff_seconds_for_failures(
                failures,
                base_sec=float(mission_slo["backoff_base_sec"]),
                max_sec=float(mission_slo["backoff_max_sec"]),
            )
            backoff_until = (now + timedelta(seconds=backoff_seconds)).isoformat()
            circuit_open_until = None
            if failures >= int(mission_slo["circuit_failure_threshold"]):
                circuit_open_until = (
                    now + timedelta(seconds=float(mission_slo["circuit_open_sec"]))
                ).isoformat()
            retry_next = compute_next_run_at(
                schedule_type="interval",
                schedule={"interval_sec": max(interval_sec, 30)},
                timezone_name=timezone_name,
                now_utc=now,
            )
            with self.database.write_transaction():
                if dispatch_registered and run_id is None:
                    self.database.delete_automation_dispatch(
                        automation_id=automation_id,
                        dispatch_key=dispatch_key,
                    )
                self.database.update_automation_fields(
                    automation_id,
                    last_error=error,
                    next_run_at=retry_next,
                    interval_sec=interval_sec,
                    schedule_type=schedule_type,
                    schedule_json=schedule,
                    timezone=timezone_name,
                    consecutive_failures=failures,
                    escalation_level=level,
                    backoff_until=backoff_until,
                    circuit_open_until=circuit_open_until,
                    last_dispatch_key=dispatch_key,
                    is_enabled=False if disable_now else bool(automation.get("is_enabled", True)),
                )
                self.database.add_automation_event(
                    automation_id=automation_id,
                    event_type="run_error",
                    message=(
                        f"Automation failed to queue run: {error} "
                        f"(consecutive_failures={failures}, escalation={level}, "
                        f"backoff_sec={backoff_seconds:.2f}, "
                        f"circuit_open_until={circuit_open_until or 'none'})"
                    ),
                )
            should_notify_escalation = (level != previous_level and level != "none") or (
                disable_now and bool(automation.get("is_enabled", True))
            )
            if should_notify_escalation:
                self._notify_escalation(
                    automation_id=automation_id,
                    user_id=user_id,
                    error=error,
                    failures=failures,
                    level=level,
                    disabled=disable_now,
                    mission_policy_profile=mission_policy_profile,
                )
            self._emit(
                "automation_run_error",
                {
                    "automation_id": automation_id,
                    "source": source_name,
                    "error": error,
                    "consecutive_failures": failures,
                    "escalation_level": level,
                    "disabled": disable_now,
                    "backoff_sec": backoff_seconds,
                    "circuit_open_until": circuit_open_until,
                    "dispatch_key": dispatch_key,
                    "mission_policy_profile": mission_policy_profile,
                },
            )
            raise

    def _backoff_seconds_for_failures(self, failures: int, *, base_sec: float, max_sec: float) -> float:
        exponent = max(0, int(failures) - 1)
        value = float(base_sec) * float(2**exponent)
        return float(min(max_sec, value))

    def _assert_agent_owner(self, *, agent_id: str, user_id: str) -> None:
        agent = self.database.get_agent(agent_id)
        if agent is None:
            raise ValueError(f"Agent not found: {agent_id}")
        owner = str(agent.get("user_id") or "").strip()
        actor = str(user_id or "").strip()
        if not owner or not actor or owner != actor:
            raise ValueError(f"Agent ownership mismatch for agent: {agent_id}")

    @staticmethod
    def _build_dispatch_key(*, source: str, schedule_type: str, slot_iso: str) -> str:
        normalized_source = str(source or "scheduled").strip().lower() or "scheduled"
        normalized_schedule_type = str(schedule_type or "interval").strip().lower() or "interval"
        normalized_slot = str(slot_iso or "").strip() or "unknown"
        return f"{normalized_source}:{normalized_schedule_type}:{normalized_slot}"

    @staticmethod
    def _parse_iso_datetime(value: Any) -> datetime | None:
        if value in (None, ""):
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
        except Exception:
            return None

    @classmethod
    def _is_future_timestamp(cls, value: Any, *, now: datetime) -> bool:
        parsed = cls._parse_iso_datetime(value)
        if parsed is None:
            return False
        return parsed > now

    @staticmethod
    def _build_watch_message(base_message: str, changed_files: list[str]) -> str:
        lines = [base_message.strip(), "", "Watcher detected file changes:"]
        for item in changed_files:
            lines.append(f"- {item}")
        return "\n".join(lines).strip()

    @staticmethod
    def _parse_bool(value: Any, default: bool = True) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        normalized = str(value).strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        return default

    def _current_watch_cursor(self, schedule: dict[str, Any]) -> int:
        _, updated = self._scan_watch_changes(schedule)
        try:
            return max(0, int(updated.get("last_seen_mtime_ns", 0)))
        except Exception:
            return 0

    def _scan_watch_changes(self, schedule: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
        watch_path_raw = str(schedule.get("path", "")).strip()
        if not watch_path_raw:
            raise ValueError("watch_fs schedule requires path")

        watch_path = Path(watch_path_raw).expanduser()
        if not watch_path.exists():
            raise ValueError(f"watch_fs path does not exist: {watch_path}")

        recursive = self._parse_bool(schedule.get("recursive", True), default=True)
        pattern = str(schedule.get("glob", "*")).strip() or "*"
        max_changed_files = max(1, int(schedule.get("max_changed_files", 20)))
        last_seen_mtime_ns = max(0, int(schedule.get("last_seen_mtime_ns", 0)))

        if watch_path.is_file():
            candidates = (watch_path,)
        elif recursive:
            candidates = watch_path.rglob(pattern)
        else:
            candidates = watch_path.glob(pattern)

        changed_rows: list[tuple[int, str]] = []
        max_seen = last_seen_mtime_ns
        for item in candidates:
            try:
                if not item.is_file():
                    continue
                stat = item.stat()
            except OSError:
                continue

            mtime_ns = int(getattr(stat, "st_mtime_ns", int(stat.st_mtime * 1_000_000_000)))
            if mtime_ns > max_seen:
                max_seen = mtime_ns
            if mtime_ns <= last_seen_mtime_ns:
                continue

            if watch_path.is_dir():
                try:
                    display = str(item.relative_to(watch_path))
                except Exception:
                    display = str(item)
            else:
                display = item.name
            changed_rows.append((mtime_ns, display))

        changed_rows.sort(key=lambda row: row[0])
        changed_files = [row[1] for row in changed_rows[-max_changed_files:]]
        updated_schedule = dict(schedule)
        updated_schedule["path"] = str(watch_path)
        updated_schedule["poll_sec"] = max(2, int(schedule.get("poll_sec", 10)))
        updated_schedule["recursive"] = recursive
        updated_schedule["glob"] = pattern
        updated_schedule["max_changed_files"] = max_changed_files
        updated_schedule["last_seen_mtime_ns"] = max_seen
        return changed_files, updated_schedule

    def _escalation_level_for_failures(
        self,
        failures: int,
        *,
        warning_threshold: int,
        critical_threshold: int,
    ) -> str:
        if failures >= critical_threshold:
            return "critical"
        if failures >= warning_threshold:
            return "warning"
        return "none"

    def _default_mission_policy(self) -> dict[str, Any]:
        return {
            "profile": "scheduler_default",
            "slo": {
                "warning_failures": int(self.escalation_warning_threshold),
                "critical_failures": int(self.escalation_critical_threshold),
                "disable_failures": int(self.escalation_disable_threshold),
                "backoff_base_sec": float(self.backoff_base_sec),
                "backoff_max_sec": float(self.backoff_max_sec),
                "circuit_failure_threshold": int(self.circuit_failure_threshold),
                "circuit_open_sec": float(self.circuit_open_sec),
            },
        }

    def _effective_mission_policy(self, automation: dict[str, Any]) -> dict[str, Any]:
        raw = automation.get("mission_policy")
        if not isinstance(raw, dict):
            raw = {}
        try:
            return resolve_mission_policy_overlay(
                policy=raw,
                defaults=self._default_mission_policy(),
            )
        except ValueError as exc:
            self.logger.warning(
                "automation_policy_invalid automation_id=%s error=%s",
                str(automation.get("id") or ""),
                exc,
            )
            return self._default_mission_policy()

    def _notify_watch_triggered(
        self,
        *,
        automation_id: str,
        user_id: str,
        changed_files: list[str],
        watch_path: str,
        run_id: str,
    ) -> None:
        title = "Automation watcher triggered"
        preview = ", ".join(changed_files[:3])
        if len(changed_files) > 3:
            preview = f"{preview}, +{len(changed_files) - 3} more"
        body = (
            f"Automation {automation_id} queued a run because files changed in {watch_path}. "
            f"Changed: {preview}."
        )
        self.database.add_inbox_item(
            user_id=user_id,
            category="automation",
            severity="info",
            title=title,
            body=body,
            source_type="automation",
            source_id=automation_id,
            run_id=run_id,
            metadata={
                "changed_files": changed_files,
                "watch_path": watch_path,
            },
            requires_action=False,
        )

    def _notify_escalation(
        self,
        *,
        automation_id: str,
        user_id: str,
        error: str,
        failures: int,
        level: str,
        disabled: bool,
        mission_policy_profile: str,
    ) -> None:
        if disabled:
            title = "Automation disabled after failures"
            severity = "error"
            requires_action = True
            body = (
                f"Automation {automation_id} was disabled after {failures} consecutive failures. "
                f"Latest error: {error}"
            )
        elif level == "critical":
            title = "Automation in critical failure state"
            severity = "error"
            requires_action = True
            body = (
                f"Automation {automation_id} reached critical escalation "
                f"({failures} consecutive failures). Latest error: {error}"
            )
        else:
            title = "Automation warning"
            severity = "warning"
            requires_action = False
            body = (
                f"Automation {automation_id} has {failures} consecutive failures. "
                f"Latest error: {error}"
            )

        self.database.add_inbox_item(
            user_id=user_id,
            category="automation",
            severity=severity,
            title=title,
            body=body,
            source_type="automation",
            source_id=automation_id,
            metadata={
                "consecutive_failures": failures,
                "escalation_level": level,
                "disabled": disabled,
                "mission_policy_profile": mission_policy_profile,
            },
            requires_action=requires_action,
        )

    def _notify_recovered(self, *, automation_id: str, user_id: str, previous_failures: int) -> None:
        self.database.add_inbox_item(
            user_id=user_id,
            category="automation",
            severity="info",
            title="Automation recovered",
            body=(
                f"Automation {automation_id} recovered and resumed normal operation "
                f"after {previous_failures} consecutive failures."
            ),
            source_type="automation",
            source_id=automation_id,
            metadata={
                "previous_failures": previous_failures,
                "status": "recovered",
            },
            requires_action=False,
        )

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.telemetry is None:
            return
        try:
            self.telemetry.emit(event_type, payload)
        except Exception:
            self.logger.debug("automation_telemetry_emit_failed event=%s", event_type)
