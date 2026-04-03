from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


MIGRATIONS: list[Migration] = [
    Migration(
        version=1,
        name="initial_schema",
        sql="""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS episodic_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            agent_id TEXT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS semantic_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            text TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS user_memory (
            user_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (user_id, key)
        );

        CREATE TABLE IF NOT EXISTS agents (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            system_prompt TEXT NOT NULL,
            model TEXT,
            tools_json TEXT NOT NULL,
            user_id TEXT,
            created_at TEXT NOT NULL
        );
        """,
    ),
    Migration(
        version=2,
        name="memory_v2_layers",
        sql="""
        ALTER TABLE episodic_memory ADD COLUMN session_id TEXT;
        ALTER TABLE episodic_memory ADD COLUMN kind TEXT NOT NULL DEFAULT 'interaction';
        ALTER TABLE episodic_memory ADD COLUMN confidence REAL NOT NULL DEFAULT 1.0;
        ALTER TABLE episodic_memory ADD COLUMN importance REAL NOT NULL DEFAULT 0.5;
        ALTER TABLE episodic_memory ADD COLUMN fingerprint TEXT;
        ALTER TABLE episodic_memory ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE episodic_memory ADD COLUMN superseded_by INTEGER;

        ALTER TABLE semantic_memory ADD COLUMN kind TEXT NOT NULL DEFAULT 'fact';
        ALTER TABLE semantic_memory ADD COLUMN confidence REAL NOT NULL DEFAULT 0.8;
        ALTER TABLE semantic_memory ADD COLUMN importance REAL NOT NULL DEFAULT 0.5;
        ALTER TABLE semantic_memory ADD COLUMN fingerprint TEXT;
        ALTER TABLE semantic_memory ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1;
        ALTER TABLE semantic_memory ADD COLUMN superseded_by INTEGER;

        ALTER TABLE user_memory ADD COLUMN confidence REAL NOT NULL DEFAULT 0.9;
        ALTER TABLE user_memory ADD COLUMN importance REAL NOT NULL DEFAULT 0.7;
        ALTER TABLE user_memory ADD COLUMN source TEXT;

        CREATE TABLE IF NOT EXISTS working_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'note',
            confidence REAL NOT NULL DEFAULT 0.5,
            importance REAL NOT NULL DEFAULT 0.5,
            is_active INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL,
            UNIQUE(user_id, session_id, key)
        );

        CREATE TABLE IF NOT EXISTS memory_extractions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            agent_id TEXT,
            session_id TEXT,
            source_role TEXT NOT NULL,
            source_text TEXT NOT NULL,
            extracted_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS memory_conflicts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            layer TEXT NOT NULL,
            key TEXT NOT NULL,
            previous_value TEXT,
            incoming_value TEXT,
            resolution TEXT NOT NULL,
            confidence_prev REAL,
            confidence_new REAL,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_episodic_user_agent_time
            ON episodic_memory(user_id, agent_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_episodic_user_session_time
            ON episodic_memory(user_id, session_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_semantic_user_time
            ON semantic_memory(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_working_user_session
            ON working_memory(user_id, session_id, updated_at);
        CREATE INDEX IF NOT EXISTS idx_conflicts_user_time
            ON memory_conflicts(user_id, created_at);
        """,
    ),
    Migration(
        version=3,
        name="agent_runs_work_mode",
        sql="""
        CREATE TABLE IF NOT EXISTS agent_runs (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            session_id TEXT,
            input_message TEXT NOT NULL,
            status TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            max_attempts INTEGER NOT NULL DEFAULT 2,
            cancel_requested INTEGER NOT NULL DEFAULT 0,
            result_json TEXT,
            error_message TEXT,
            checkpoints_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_agent_runs_user_created
            ON agent_runs(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_agent_runs_agent_created
            ON agent_runs(agent_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_agent_runs_status_updated
            ON agent_runs(status, updated_at);
        """,
    ),
    Migration(
        version=4,
        name="automation_layer_v1",
        sql="""
        CREATE TABLE IF NOT EXISTS automations (
            id TEXT PRIMARY KEY,
            agent_id TEXT NOT NULL,
            user_id TEXT NOT NULL,
            session_id TEXT,
            message TEXT NOT NULL,
            interval_sec INTEGER NOT NULL,
            is_enabled INTEGER NOT NULL DEFAULT 1,
            next_run_at TEXT NOT NULL,
            last_run_at TEXT,
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_automations_enabled_next_run
            ON automations(is_enabled, next_run_at);
        CREATE INDEX IF NOT EXISTS idx_automations_user_created
            ON automations(user_id, created_at);

        CREATE TABLE IF NOT EXISTS automation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            automation_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            message TEXT NOT NULL,
            run_id TEXT,
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_automation_events_automation_time
            ON automation_events(automation_id, created_at);
        """,
    ),
    Migration(
        version=5,
        name="automation_schedule_v2",
        sql="""
        ALTER TABLE automations ADD COLUMN schedule_type TEXT NOT NULL DEFAULT 'interval';
        ALTER TABLE automations ADD COLUMN schedule_json TEXT NOT NULL DEFAULT '{}';
        ALTER TABLE automations ADD COLUMN timezone TEXT NOT NULL DEFAULT 'UTC';
        """,
    ),
    Migration(
        version=6,
        name="automation_inbox_escalation_v3",
        sql="""
        ALTER TABLE automations ADD COLUMN consecutive_failures INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE automations ADD COLUMN escalation_level TEXT NOT NULL DEFAULT 'none';

        CREATE TABLE IF NOT EXISTS inbox_items (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            category TEXT NOT NULL,
            severity TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT NOT NULL,
            source_type TEXT,
            source_id TEXT,
            run_id TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            is_read INTEGER NOT NULL DEFAULT 0,
            requires_action INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_inbox_user_created
            ON inbox_items(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_inbox_user_read_created
            ON inbox_items(user_id, is_read, created_at);
        CREATE INDEX IF NOT EXISTS idx_inbox_source
            ON inbox_items(source_type, source_id, created_at);
        """,
    ),
    Migration(
        version=7,
        name="security_audit_events_v1",
        sql="""
        CREATE TABLE IF NOT EXISTS security_audit_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            action TEXT,
            actor TEXT,
            request_id TEXT,
            target_type TEXT,
            target_id TEXT,
            status TEXT NOT NULL DEFAULT 'succeeded',
            details_json TEXT NOT NULL DEFAULT '{}',
            signature_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_security_audit_time
            ON security_audit_events(created_at);
        CREATE INDEX IF NOT EXISTS idx_security_audit_action_status
            ON security_audit_events(action, status, created_at);
        CREATE INDEX IF NOT EXISTS idx_security_audit_actor
            ON security_audit_events(actor, created_at);
        CREATE INDEX IF NOT EXISTS idx_security_audit_request
            ON security_audit_events(request_id, created_at);
        """,
    ),
    Migration(
        version=8,
        name="agent_runs_reliability_v2",
        sql="""
        ALTER TABLE agent_runs ADD COLUMN stop_reason TEXT;
        ALTER TABLE agent_runs ADD COLUMN failure_class TEXT;
        ALTER TABLE agent_runs ADD COLUMN budget_json TEXT NOT NULL DEFAULT '{}';
        ALTER TABLE agent_runs ADD COLUMN metrics_json TEXT NOT NULL DEFAULT '{}';

        CREATE INDEX IF NOT EXISTS idx_agent_runs_failure_class
            ON agent_runs(failure_class, updated_at);
        CREATE INDEX IF NOT EXISTS idx_agent_runs_stop_reason
            ON agent_runs(stop_reason, updated_at);
        """,
    ),
    Migration(
        version=9,
        name="automation_reliability_v4",
        sql="""
        ALTER TABLE automations ADD COLUMN lease_owner TEXT;
        ALTER TABLE automations ADD COLUMN lease_expires_at TEXT;
        ALTER TABLE automations ADD COLUMN backoff_until TEXT;
        ALTER TABLE automations ADD COLUMN circuit_open_until TEXT;
        ALTER TABLE automations ADD COLUMN last_dispatch_key TEXT;

        CREATE INDEX IF NOT EXISTS idx_automations_lease_expiry
            ON automations(lease_expires_at, next_run_at);
        CREATE INDEX IF NOT EXISTS idx_automations_backoff
            ON automations(backoff_until, next_run_at);
        CREATE INDEX IF NOT EXISTS idx_automations_circuit
            ON automations(circuit_open_until, next_run_at);

        CREATE TABLE IF NOT EXISTS automation_dispatches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            automation_id TEXT NOT NULL,
            dispatch_key TEXT NOT NULL,
            source TEXT NOT NULL,
            run_id TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(automation_id, dispatch_key)
        );

        CREATE INDEX IF NOT EXISTS idx_automation_dispatches_automation_time
            ON automation_dispatches(automation_id, created_at);
        """,
    ),
    Migration(
        version=10,
        name="agent_run_issues_work_mode_v3",
        sql="""
        CREATE TABLE IF NOT EXISTS agent_run_issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            issue_id TEXT NOT NULL,
            issue_order INTEGER NOT NULL DEFAULT 0,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            depends_on_json TEXT NOT NULL DEFAULT '[]',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            UNIQUE(run_id, issue_id)
        );

        CREATE INDEX IF NOT EXISTS idx_agent_run_issues_run_order
            ON agent_run_issues(run_id, issue_order, updated_at);
        CREATE INDEX IF NOT EXISTS idx_agent_run_issues_status_updated
            ON agent_run_issues(status, updated_at);
        """,
    ),
    Migration(
        version=11,
        name="agent_run_issue_artifacts_v1",
        sql="""
        CREATE TABLE IF NOT EXISTS agent_run_issue_artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            issue_id TEXT NOT NULL,
            artifact_key TEXT NOT NULL,
            artifact_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(run_id, issue_id, artifact_key)
        );

        CREATE INDEX IF NOT EXISTS idx_agent_run_issue_artifacts_run_updated
            ON agent_run_issue_artifacts(run_id, updated_at);
        CREATE INDEX IF NOT EXISTS idx_agent_run_issue_artifacts_issue_updated
            ON agent_run_issue_artifacts(run_id, issue_id, updated_at);
        """,
    ),
    Migration(
        version=12,
        name="agent_run_tool_calls_v1",
        sql="""
        CREATE TABLE IF NOT EXISTS agent_run_tool_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            arguments_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL,
            result_json TEXT,
            error_message TEXT,
            attempt INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(run_id, idempotency_key)
        );

        CREATE INDEX IF NOT EXISTS idx_agent_run_tool_calls_run_updated
            ON agent_run_tool_calls(run_id, updated_at);
        CREATE INDEX IF NOT EXISTS idx_agent_run_tool_calls_run_status
            ON agent_run_tool_calls(run_id, status, updated_at);
        """,
    ),
    Migration(
        version=13,
        name="persistence_foreign_keys_v1",
        sql="""
        PRAGMA foreign_keys=OFF;

        DELETE FROM agent_run_issues
        WHERE run_id NOT IN (SELECT id FROM agent_runs);
        DELETE FROM agent_run_issue_artifacts
        WHERE (run_id, issue_id) NOT IN (SELECT run_id, issue_id FROM agent_run_issues);
        DELETE FROM agent_run_tool_calls
        WHERE run_id NOT IN (SELECT id FROM agent_runs);
        DELETE FROM automation_events
        WHERE automation_id NOT IN (SELECT id FROM automations);
        DELETE FROM automation_dispatches
        WHERE automation_id NOT IN (SELECT id FROM automations);

        ALTER TABLE agent_run_issues RENAME TO agent_run_issues_old;
        CREATE TABLE agent_run_issues (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            issue_id TEXT NOT NULL,
            issue_order INTEGER NOT NULL DEFAULT 0,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            depends_on_json TEXT NOT NULL DEFAULT '[]',
            attempt_count INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            payload_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            UNIQUE(run_id, issue_id),
            FOREIGN KEY(run_id) REFERENCES agent_runs(id) ON DELETE CASCADE ON UPDATE CASCADE
        );
        INSERT INTO agent_run_issues (
            id,
            run_id,
            issue_id,
            issue_order,
            title,
            status,
            depends_on_json,
            attempt_count,
            last_error,
            payload_json,
            created_at,
            updated_at,
            started_at,
            finished_at
        )
        SELECT
            id,
            run_id,
            issue_id,
            issue_order,
            title,
            status,
            depends_on_json,
            attempt_count,
            last_error,
            payload_json,
            created_at,
            updated_at,
            started_at,
            finished_at
        FROM agent_run_issues_old;
        DROP TABLE agent_run_issues_old;
        CREATE INDEX IF NOT EXISTS idx_agent_run_issues_run_order
            ON agent_run_issues(run_id, issue_order, updated_at);
        CREATE INDEX IF NOT EXISTS idx_agent_run_issues_status_updated
            ON agent_run_issues(status, updated_at);

        ALTER TABLE agent_run_issue_artifacts RENAME TO agent_run_issue_artifacts_old;
        CREATE TABLE agent_run_issue_artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            issue_id TEXT NOT NULL,
            artifact_key TEXT NOT NULL,
            artifact_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(run_id, issue_id, artifact_key),
            FOREIGN KEY(run_id, issue_id) REFERENCES agent_run_issues(run_id, issue_id) ON DELETE CASCADE ON UPDATE CASCADE
        );
        INSERT INTO agent_run_issue_artifacts (
            id,
            run_id,
            issue_id,
            artifact_key,
            artifact_json,
            created_at,
            updated_at
        )
        SELECT
            id,
            run_id,
            issue_id,
            artifact_key,
            artifact_json,
            created_at,
            updated_at
        FROM agent_run_issue_artifacts_old;
        DROP TABLE agent_run_issue_artifacts_old;
        CREATE INDEX IF NOT EXISTS idx_agent_run_issue_artifacts_run_updated
            ON agent_run_issue_artifacts(run_id, updated_at);
        CREATE INDEX IF NOT EXISTS idx_agent_run_issue_artifacts_issue_updated
            ON agent_run_issue_artifacts(run_id, issue_id, updated_at);

        ALTER TABLE agent_run_tool_calls RENAME TO agent_run_tool_calls_old;
        CREATE TABLE agent_run_tool_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            arguments_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL,
            result_json TEXT,
            error_message TEXT,
            attempt INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(run_id, idempotency_key),
            FOREIGN KEY(run_id) REFERENCES agent_runs(id) ON DELETE CASCADE ON UPDATE CASCADE
        );
        INSERT INTO agent_run_tool_calls (
            id,
            run_id,
            idempotency_key,
            tool_name,
            arguments_json,
            status,
            result_json,
            error_message,
            attempt,
            created_at,
            updated_at
        )
        SELECT
            id,
            run_id,
            idempotency_key,
            tool_name,
            arguments_json,
            status,
            result_json,
            error_message,
            attempt,
            created_at,
            updated_at
        FROM agent_run_tool_calls_old;
        DROP TABLE agent_run_tool_calls_old;
        CREATE INDEX IF NOT EXISTS idx_agent_run_tool_calls_run_updated
            ON agent_run_tool_calls(run_id, updated_at);
        CREATE INDEX IF NOT EXISTS idx_agent_run_tool_calls_run_status
            ON agent_run_tool_calls(run_id, status, updated_at);

        ALTER TABLE automation_events RENAME TO automation_events_old;
        CREATE TABLE automation_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            automation_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            message TEXT NOT NULL,
            run_id TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(automation_id) REFERENCES automations(id) ON DELETE CASCADE ON UPDATE CASCADE
        );
        INSERT INTO automation_events (
            id,
            automation_id,
            event_type,
            message,
            run_id,
            created_at
        )
        SELECT
            id,
            automation_id,
            event_type,
            message,
            run_id,
            created_at
        FROM automation_events_old;
        DROP TABLE automation_events_old;
        CREATE INDEX IF NOT EXISTS idx_automation_events_automation_time
            ON automation_events(automation_id, created_at);

        ALTER TABLE automation_dispatches RENAME TO automation_dispatches_old;
        CREATE TABLE automation_dispatches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            automation_id TEXT NOT NULL,
            dispatch_key TEXT NOT NULL,
            source TEXT NOT NULL,
            run_id TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(automation_id, dispatch_key),
            FOREIGN KEY(automation_id) REFERENCES automations(id) ON DELETE CASCADE ON UPDATE CASCADE
        );
        INSERT INTO automation_dispatches (
            id,
            automation_id,
            dispatch_key,
            source,
            run_id,
            created_at
        )
        SELECT
            id,
            automation_id,
            dispatch_key,
            source,
            run_id,
            created_at
        FROM automation_dispatches_old;
        DROP TABLE automation_dispatches_old;
        CREATE INDEX IF NOT EXISTS idx_automation_dispatches_automation_time
            ON automation_dispatches(automation_id, created_at);

        PRAGMA foreign_keys=ON;
        """,
    ),
    Migration(
        version=14,
        name="agent_run_leases_v1",
        sql="""
        ALTER TABLE agent_runs ADD COLUMN lease_owner TEXT;
        ALTER TABLE agent_runs ADD COLUMN lease_token TEXT;
        ALTER TABLE agent_runs ADD COLUMN lease_expires_at TEXT;

        CREATE INDEX IF NOT EXISTS idx_agent_runs_lease_expiry
            ON agent_runs(lease_expires_at, status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_agent_runs_lease_owner
            ON agent_runs(lease_owner, status, updated_at);
        """,
    ),
    Migration(
        version=15,
        name="security_operations_baseline_v1",
        sql="""
        CREATE TABLE IF NOT EXISTS security_auth_token_activity (
            token_fingerprint TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            scopes_json TEXT NOT NULL DEFAULT '[]',
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            last_request_id TEXT,
            last_path TEXT,
            last_method TEXT,
            request_count INTEGER NOT NULL DEFAULT 0,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE INDEX IF NOT EXISTS idx_security_auth_token_last_seen
            ON security_auth_token_activity(last_seen_at);
        CREATE INDEX IF NOT EXISTS idx_security_auth_token_user
            ON security_auth_token_activity(user_id, last_seen_at);

        CREATE TABLE IF NOT EXISTS security_secret_inventory (
            secret_key TEXT PRIMARY KEY,
            provider TEXT NOT NULL,
            is_required INTEGER NOT NULL DEFAULT 1,
            source TEXT NOT NULL DEFAULT 'env',
            value_fingerprint TEXT,
            value_present INTEGER NOT NULL DEFAULT 0,
            last_rotated_at TEXT,
            rotation_period_days INTEGER NOT NULL DEFAULT 90,
            expires_at TEXT,
            status TEXT NOT NULL DEFAULT 'unknown',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_security_secret_status
            ON security_secret_inventory(status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_security_secret_provider
            ON security_secret_inventory(provider, updated_at);

        CREATE TABLE IF NOT EXISTS security_access_reviews (
            id TEXT PRIMARY KEY,
            reviewer TEXT,
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            summary TEXT,
            snapshot_json TEXT NOT NULL DEFAULT '{}',
            decisions_json TEXT NOT NULL DEFAULT '{}',
            findings_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_security_access_reviews_status
            ON security_access_reviews(status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_security_access_reviews_started
            ON security_access_reviews(started_at);

        CREATE TABLE IF NOT EXISTS security_incidents (
            id TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            severity TEXT NOT NULL,
            status TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            owner TEXT,
            opened_at TEXT NOT NULL,
            acknowledged_at TEXT,
            resolved_at TEXT,
            impact TEXT,
            containment TEXT,
            root_cause TEXT,
            recovery_actions TEXT,
            request_id TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_security_incidents_status
            ON security_incidents(status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_security_incidents_severity
            ON security_incidents(severity, updated_at);
        CREATE INDEX IF NOT EXISTS idx_security_incidents_opened
            ON security_incidents(opened_at);

        CREATE TABLE IF NOT EXISTS security_incident_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            incident_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            actor TEXT,
            message TEXT NOT NULL,
            details_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            FOREIGN KEY(incident_id) REFERENCES security_incidents(id) ON DELETE CASCADE ON UPDATE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_security_incident_events_time
            ON security_incident_events(incident_id, created_at);
        """,
    ),
    Migration(
        version=16,
        name="terminal_action_receipts_v1",
        sql="""
        CREATE TABLE IF NOT EXISTS terminal_action_receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL DEFAULT 'tool_invoke',
            tool_name TEXT NOT NULL,
            actor TEXT,
            user_id TEXT,
            session_id TEXT,
            request_id TEXT,
            permission_id TEXT,
            status TEXT NOT NULL DEFAULT 'succeeded',
            risk_level TEXT NOT NULL DEFAULT 'medium',
            policy_level TEXT,
            rollback_hint TEXT,
            arguments_json TEXT NOT NULL DEFAULT '{}',
            result_json TEXT,
            error_message TEXT,
            details_json TEXT NOT NULL DEFAULT '{}',
            action_receipt_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_terminal_action_receipts_time
            ON terminal_action_receipts(created_at);
        CREATE INDEX IF NOT EXISTS idx_terminal_action_receipts_tool_status
            ON terminal_action_receipts(tool_name, status, created_at);
        CREATE INDEX IF NOT EXISTS idx_terminal_action_receipts_actor
            ON terminal_action_receipts(actor, created_at);
        CREATE INDEX IF NOT EXISTS idx_terminal_action_receipts_user
            ON terminal_action_receipts(user_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_terminal_action_receipts_request
            ON terminal_action_receipts(request_id, created_at);
        """,
    ),
    Migration(
        version=17,
        name="filesystem_patch_previews_v1",
        sql="""
        CREATE TABLE IF NOT EXISTS filesystem_patch_previews (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            actor TEXT,
            session_id TEXT,
            request_id TEXT,
            path TEXT NOT NULL,
            target_path TEXT NOT NULL,
            after_content TEXT NOT NULL,
            before_exists INTEGER NOT NULL DEFAULT 0,
            before_sha256 TEXT,
            before_size INTEGER,
            after_sha256 TEXT NOT NULL,
            after_size INTEGER NOT NULL,
            diff_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending',
            expires_at TEXT NOT NULL,
            approved_at TEXT,
            applied_at TEXT,
            approval_actor TEXT,
            consumed_request_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_fs_patch_previews_user_status_created
            ON filesystem_patch_previews(user_id, status, created_at);
        CREATE INDEX IF NOT EXISTS idx_fs_patch_previews_session_created
            ON filesystem_patch_previews(session_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_fs_patch_previews_expiry
            ON filesystem_patch_previews(status, expires_at);
        """,
    ),
    Migration(
        version=18,
        name="automation_mission_policy_v1",
        sql="""
        ALTER TABLE automations ADD COLUMN mission_policy_json TEXT NOT NULL DEFAULT '{}';
        """,
    ),
    Migration(
        version=19,
        name="supervisor_graphs_checkpoint_store_v1",
        sql="""
        CREATE TABLE IF NOT EXISTS supervisor_graphs (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            status TEXT NOT NULL,
            objective TEXT NOT NULL,
            graph_json TEXT NOT NULL DEFAULT '{}',
            checkpoint_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            launched_at TEXT,
            finished_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_supervisor_graphs_user_updated
            ON supervisor_graphs(user_id, updated_at);
        CREATE INDEX IF NOT EXISTS idx_supervisor_graphs_status_updated
            ON supervisor_graphs(status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_supervisor_graphs_created
            ON supervisor_graphs(created_at);
        """,
    ),
    Migration(
        version=20,
        name="provider_sessions_v1",
        sql="""
        CREATE TABLE IF NOT EXISTS provider_sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            display_name TEXT,
            session_type TEXT NOT NULL DEFAULT 'reference',
            credential_ref TEXT NOT NULL,
            credential_fingerprint TEXT,
            scopes_json TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'active',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            expires_at TEXT,
            revoked_at TEXT,
            revoked_reason TEXT,
            last_used_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_provider_sessions_user_updated
            ON provider_sessions(user_id, updated_at);
        CREATE INDEX IF NOT EXISTS idx_provider_sessions_provider_status
            ON provider_sessions(provider, status, updated_at);
        CREATE INDEX IF NOT EXISTS idx_provider_sessions_user_provider_status
            ON provider_sessions(user_id, provider, status, updated_at);
        """,
    ),
    Migration(
        version=21,
        name="news_items_v1",
        sql="""
        CREATE TABLE IF NOT EXISTS news_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            topic TEXT NOT NULL,
            source TEXT NOT NULL,
            canonical_id TEXT NOT NULL,
            url TEXT NOT NULL,
            title TEXT NOT NULL,
            excerpt TEXT,
            author TEXT,
            published_at TEXT NOT NULL,
            ingested_at TEXT NOT NULL,
            raw_score REAL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(user_id, topic, source, canonical_id, published_at)
        );

        CREATE INDEX IF NOT EXISTS idx_news_items_user_topic_time
            ON news_items(user_id, topic, ingested_at DESC);
        CREATE INDEX IF NOT EXISTS idx_news_items_source_time
            ON news_items(source, ingested_at DESC);
        CREATE INDEX IF NOT EXISTS idx_news_items_user_source_time
            ON news_items(user_id, source, ingested_at DESC);
        """,
    ),
]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_migration_sql(conn: sqlite3.Connection, sql: str) -> None:
    statements = [item.strip() for item in sql.split(";") if item.strip()]
    for statement in statements:
        try:
            conn.execute(statement)
        except sqlite3.OperationalError as exc:
            message = str(exc).lower()
            if "duplicate column name" in message or "already exists" in message:
                continue
            raise
    conn.commit()


def apply_migrations(conn: sqlite3.Connection) -> list[int]:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )
    conn.commit()

    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    applied_versions = {int(row[0]) for row in rows}
    newly_applied: list[int] = []

    for migration in sorted(MIGRATIONS, key=lambda item: item.version):
        if migration.version in applied_versions:
            continue

        _run_migration_sql(conn, migration.sql)
        conn.execute(
            "INSERT INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
            (migration.version, migration.name, _utc_now()),
        )
        conn.commit()
        newly_applied.append(migration.version)

    return newly_applied
