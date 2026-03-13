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
