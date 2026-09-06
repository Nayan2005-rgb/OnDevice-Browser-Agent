"""SQLite schema definitions for persistent agent sessions & durable actions.

Stores only privacy-safe metadata — never screenshots, PII, or biometrics.
Milestone 5A: sessions / plans / events (schema v1).
Milestone 5B: durable confirmations, deliveries, leases, lifecycle (schema v2).
"""

from __future__ import annotations

SCHEMA_VERSION = 2

CREATE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id TEXT PRIMARY KEY,
    plan_id TEXT,
    status TEXT NOT NULL,
    current_step_index INTEGER NOT NULL DEFAULT 0,
    plan_version INTEGER NOT NULL DEFAULT 1,
    replan_count INTEGER NOT NULL DEFAULT 0,
    tab_id INTEGER,
    window_id INTEGER,
    last_page_signature TEXT,
    last_url_signature TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    pause_reason TEXT,
    intervention_reason TEXT,
    recovery_status TEXT,
    operator_replan_approval INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    goal TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    performance_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_sessions_status ON agent_sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_tab ON agent_sessions(tab_id);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON agent_sessions(updated_at DESC);

CREATE TABLE IF NOT EXISTS task_plans (
    plan_id TEXT PRIMARY KEY,
    session_id TEXT,
    goal TEXT NOT NULL,
    status TEXT NOT NULL,
    current_step_index INTEGER NOT NULL DEFAULT 0,
    plan_version INTEGER NOT NULL DEFAULT 1,
    replan_count INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    tab_id INTEGER,
    window_id INTEGER,
    payload_json TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES agent_sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_plans_session ON task_plans(session_id);

CREATE TABLE IF NOT EXISTS session_events (
    event_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    timestamp REAL NOT NULL,
    event_type TEXT NOT NULL,
    plan_version INTEGER,
    step_index INTEGER,
    safe_metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (session_id) REFERENCES agent_sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_events_session_ts
    ON session_events(session_id, timestamp ASC);

CREATE TABLE IF NOT EXISTS action_records (
    record_id TEXT PRIMARY KEY,
    session_id TEXT,
    plan_id TEXT,
    step_id TEXT,
    confirmation_id TEXT,
    lifecycle_id TEXT,
    status TEXT NOT NULL,
    action_type TEXT,
    safe_description TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (session_id) REFERENCES agent_sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_actions_session ON action_records(session_id);

CREATE TABLE IF NOT EXISTS replan_previews (
    preview_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL UNIQUE,
    plan_id TEXT NOT NULL,
    plan_version INTEGER NOT NULL,
    proposed_version INTEGER NOT NULL,
    reason TEXT,
    page_change_level TEXT,
    created_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    payload_json TEXT NOT NULL,
    FOREIGN KEY (session_id) REFERENCES agent_sessions(session_id)
);
"""

# Milestone 5B tables — applied after CREATE_SCHEMA_SQL (idempotent).
CREATE_SCHEMA_V2_SQL = """
CREATE TABLE IF NOT EXISTS confirmations (
    confirmation_id TEXT PRIMARY KEY,
    session_id TEXT,
    plan_id TEXT,
    step_id TEXT,
    lifecycle_id TEXT,
    tab_id INTEGER,
    window_id INTEGER,
    status TEXT NOT NULL,
    category TEXT,
    reason TEXT,
    task TEXT,
    action_type TEXT,
    safe_description TEXT,
    safe_target_json TEXT NOT NULL DEFAULT '{}',
    action_payload_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    consumed INTEGER NOT NULL DEFAULT 0,
    execution_id TEXT,
    performance_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_confirmations_status ON confirmations(status);
CREATE INDEX IF NOT EXISTS idx_confirmations_session ON confirmations(session_id);
CREATE INDEX IF NOT EXISTS idx_confirmations_plan ON confirmations(plan_id);
CREATE INDEX IF NOT EXISTS idx_confirmations_tab ON confirmations(tab_id);
CREATE INDEX IF NOT EXISTS idx_confirmations_expiry ON confirmations(expires_at);
CREATE INDEX IF NOT EXISTS idx_confirmations_lifecycle ON confirmations(lifecycle_id);

CREATE TABLE IF NOT EXISTS action_deliveries (
    execution_id TEXT PRIMARY KEY,
    confirmation_id TEXT,
    lifecycle_id TEXT,
    session_id TEXT,
    plan_id TEXT,
    step_id TEXT,
    tab_id INTEGER,
    window_id INTEGER,
    status TEXT NOT NULL,
    action_type TEXT,
    task TEXT,
    category TEXT,
    action_payload_json TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    claimed_at REAL,
    lease_until REAL,
    executed_at REAL,
    recovery_reason TEXT,
    result_json TEXT NOT NULL DEFAULT '{}',
    performance_json TEXT NOT NULL DEFAULT '{}',
    idempotency_key TEXT,
    updated_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_deliveries_status ON action_deliveries(status);
CREATE INDEX IF NOT EXISTS idx_deliveries_session ON action_deliveries(session_id);
CREATE INDEX IF NOT EXISTS idx_deliveries_plan ON action_deliveries(plan_id);
CREATE INDEX IF NOT EXISTS idx_deliveries_confirmation ON action_deliveries(confirmation_id);
CREATE INDEX IF NOT EXISTS idx_deliveries_tab ON action_deliveries(tab_id);
CREATE INDEX IF NOT EXISTS idx_deliveries_lease ON action_deliveries(lease_until);
CREATE INDEX IF NOT EXISTS idx_deliveries_expiry ON action_deliveries(expires_at);
CREATE INDEX IF NOT EXISTS idx_deliveries_lifecycle ON action_deliveries(lifecycle_id);

CREATE TABLE IF NOT EXISTS action_lifecycles (
    lifecycle_id TEXT PRIMARY KEY,
    session_id TEXT,
    plan_id TEXT,
    step_id TEXT,
    confirmation_id TEXT,
    execution_id TEXT,
    tab_id INTEGER,
    window_id INTEGER,
    state TEXT NOT NULL,
    task TEXT,
    action_type TEXT,
    risk_level TEXT,
    category TEXT,
    recovery_reason TEXT,
    verification_status TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    timeline_json TEXT NOT NULL DEFAULT '[]',
    performance_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_lifecycles_status ON action_lifecycles(state);
CREATE INDEX IF NOT EXISTS idx_lifecycles_session ON action_lifecycles(session_id);
CREATE INDEX IF NOT EXISTS idx_lifecycles_plan ON action_lifecycles(plan_id);
CREATE INDEX IF NOT EXISTS idx_lifecycles_step ON action_lifecycles(step_id);
CREATE INDEX IF NOT EXISTS idx_lifecycles_execution ON action_lifecycles(execution_id);
CREATE INDEX IF NOT EXISTS idx_lifecycles_tab ON action_lifecycles(tab_id);

CREATE TABLE IF NOT EXISTS action_lifecycle_events (
    event_id TEXT PRIMARY KEY,
    lifecycle_id TEXT NOT NULL,
    session_id TEXT,
    plan_id TEXT,
    step_id TEXT,
    action_id TEXT,
    execution_id TEXT,
    confirmation_id TEXT,
    from_state TEXT,
    to_state TEXT NOT NULL,
    reason TEXT,
    timestamp REAL NOT NULL,
    safe_metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_life_events_lifecycle
    ON action_lifecycle_events(lifecycle_id, timestamp ASC);
CREATE INDEX IF NOT EXISTS idx_life_events_session
    ON action_lifecycle_events(session_id, timestamp ASC);
CREATE INDEX IF NOT EXISTS idx_life_events_execution
    ON action_lifecycle_events(execution_id);

CREATE TABLE IF NOT EXISTS execution_records (
    execution_id TEXT PRIMARY KEY,
    confirmation_id TEXT,
    lifecycle_id TEXT,
    session_id TEXT,
    tab_id INTEGER,
    status TEXT NOT NULL,
    idempotency_key TEXT,
    report_status TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    safe_result_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_exec_records_confirmation
    ON execution_records(confirmation_id);
CREATE INDEX IF NOT EXISTS idx_exec_records_idempotency
    ON execution_records(idempotency_key);
CREATE INDEX IF NOT EXISTS idx_exec_records_session
    ON execution_records(session_id);

CREATE INDEX IF NOT EXISTS idx_actions_plan ON action_records(plan_id);
CREATE INDEX IF NOT EXISTS idx_actions_step ON action_records(step_id);
CREATE INDEX IF NOT EXISTS idx_actions_confirmation ON action_records(confirmation_id);
CREATE INDEX IF NOT EXISTS idx_actions_lifecycle ON action_records(lifecycle_id);
CREATE INDEX IF NOT EXISTS idx_actions_status ON action_records(status);
"""

ACTIVE_SESSION_STATUSES = (
    "created",
    "running",
    "paused",
    "waiting_for_confirmation",
    "waiting_for_browser",
    "requires_user_intervention",
    "recovering",
)

# Unfinished delivery / lifecycle statuses that need crash recovery attention
INTERRUPTIBLE_DELIVERY_STATUSES = (
    "approved",
    "waiting_for_browser",
    "claimed",
    "executing",
)

INTERRUPTIBLE_LIFECYCLE_STATES = (
    "requires_confirmation",
    "approved",
    "waiting_for_extension",
    "waiting_for_browser",
    "claimed",
    "executing",
    "recovery_required",
)
