CREATE TABLE IF NOT EXISTS user_model_profile (
    id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL,
    name VARCHAR(255) NOT NULL,
    description VARCHAR(1000) NOT NULL DEFAULT '',
    enabled BOOLEAN NOT NULL,
    provider VARCHAR(64) NOT NULL,
    base_url VARCHAR(512) NOT NULL DEFAULT '',
    api_key VARCHAR(512) NOT NULL DEFAULT '',
    model VARCHAR(255) NOT NULL DEFAULT '',
    temperature DOUBLE,
    max_tokens INT,
    supported_routes VARCHAR(1000) NOT NULL DEFAULT '',
    is_default BOOLEAN NOT NULL,
    priority INT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_digest_batch (
    id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL,
    platform VARCHAR(64) NOT NULL,
    chat_type VARCHAR(32) NOT NULL,
    chat_id VARCHAR(255) NOT NULL,
    aggregation_key VARCHAR(512) NOT NULL,
    source_event_ids_json CLOB NOT NULL,
    message_count INT NOT NULL,
    summary CLOB NOT NULL,
    happened CLOB NOT NULL DEFAULT '',
    action_items CLOB NOT NULL DEFAULT '',
    next_step CLOB NOT NULL DEFAULT '',
    period_started_at TIMESTAMP,
    period_ended_at TIMESTAMP,
    generated_at TIMESTAMP NOT NULL
);

ALTER TABLE conversation_digest_batch ADD COLUMN IF NOT EXISTS happened CLOB NOT NULL DEFAULT '';
ALTER TABLE conversation_digest_batch ADD COLUMN IF NOT EXISTS action_items CLOB NOT NULL DEFAULT '';
ALTER TABLE conversation_digest_batch ADD COLUMN IF NOT EXISTS next_step CLOB NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_digest_user_generated
    ON conversation_digest_batch(user_id, generated_at);

CREATE INDEX IF NOT EXISTS idx_user_model_profile_user_id ON user_model_profile (user_id);
CREATE INDEX IF NOT EXISTS idx_user_model_profile_updated_at ON user_model_profile (updated_at);

CREATE TABLE IF NOT EXISTS event_record (
    event_id VARCHAR(255) PRIMARY KEY,
    owner_user_id VARCHAR(128) NOT NULL DEFAULT 'local-user',
    payload_json CLOB NOT NULL,
    received_at TIMESTAMP NOT NULL,
    processing_status VARCHAR(64) NOT NULL,
    processing_summary CLOB NOT NULL,
    resolved_route VARCHAR(128) NOT NULL DEFAULT '',
    write_back_status VARCHAR(64) NOT NULL,
    need_human_confirmation BOOLEAN NOT NULL,
    processed_at TIMESTAMP,
    reply_draft CLOB NOT NULL,
    execution_trace_json CLOB,
    last_action VARCHAR(64) NOT NULL DEFAULT '',
    last_action_note CLOB NOT NULL,
    last_action_at TIMESTAMP,
    inbox_status VARCHAR(32) NOT NULL DEFAULT 'NEW',
    inbox_updated_at TIMESTAMP,
      snoozed_until TIMESTAMP,
      message_origin VARCHAR(32) NOT NULL DEFAULT 'EXTERNAL'
);

ALTER TABLE event_record ADD COLUMN IF NOT EXISTS owner_user_id VARCHAR(128) NOT NULL DEFAULT 'local-user';
ALTER TABLE event_record ADD COLUMN IF NOT EXISTS message_origin VARCHAR(32) NOT NULL DEFAULT 'EXTERNAL';

CREATE INDEX IF NOT EXISTS idx_event_record_received_at ON event_record (received_at);
CREATE INDEX IF NOT EXISTS idx_event_record_inbox_status ON event_record (inbox_status);
CREATE INDEX IF NOT EXISTS idx_event_record_owner_user_id ON event_record (owner_user_id);

CREATE TABLE IF NOT EXISTS agent_dispatch_retry (
    event_id VARCHAR(255) PRIMARY KEY,
    status VARCHAR(32) NOT NULL,
    attempt_count INT NOT NULL,
    next_attempt_at TIMESTAMP,
    last_error VARCHAR(2000) NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dispatch_retry_due
    ON agent_dispatch_retry(status, next_attempt_at);

CREATE TABLE IF NOT EXISTS platform_connection (
    id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL,
    name VARCHAR(255) NOT NULL,
    platform VARCHAR(64) NOT NULL,
    connector VARCHAR(64) NOT NULL,
    enabled BOOLEAN NOT NULL,
    connector_base_url VARCHAR(512) NOT NULL DEFAULT '',
    credential_ciphertext VARCHAR(2000) NOT NULL DEFAULT '',
    account_id VARCHAR(128) NOT NULL DEFAULT '',
    account_name VARCHAR(255) NOT NULL DEFAULT '',
    health VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN',
    health_message VARCHAR(1000) NOT NULL DEFAULT '',
    last_checked_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_platform_connection_user_id ON platform_connection (user_id);
CREATE INDEX IF NOT EXISTS idx_platform_connection_platform ON platform_connection (platform);

CREATE TABLE IF NOT EXISTS local_user (
    id VARCHAR(64) PRIMARY KEY,
    username VARCHAR(128) NOT NULL UNIQUE,
    display_name VARCHAR(255) NOT NULL DEFAULT '',
    password_hash VARCHAR(1000) NOT NULL,
    enabled BOOLEAN NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_local_user_username ON local_user (username);

CREATE TABLE IF NOT EXISTS conversation_profile (
    id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL,
    name VARCHAR(255) NOT NULL,
    description CLOB NOT NULL,
    enabled BOOLEAN NOT NULL,
    platform VARCHAR(64) NOT NULL DEFAULT '',
    account_id VARCHAR(128) NOT NULL DEFAULT '',
    scene VARCHAR(64) NOT NULL DEFAULT '',
    chat_type VARCHAR(64) NOT NULL DEFAULT '',
    chat_ids_json CLOB NOT NULL,
    target_user_ids_json CLOB NOT NULL,
    supported_routes_json CLOB NOT NULL,
    trigger_mode VARCHAR(64) NOT NULL DEFAULT '',
    trigger_keywords_json CLOB NOT NULL,
    persona_mode VARCHAR(64) NOT NULL DEFAULT '',
    system_prompt CLOB NOT NULL,
    skill_reference VARCHAR(1000) NOT NULL DEFAULT '',
    skill_references_json CLOB NOT NULL,
    model_profile_id VARCHAR(64) NOT NULL DEFAULT '',
    preferred_route VARCHAR(128) NOT NULL DEFAULT '',
    reply_mode VARCHAR(64) NOT NULL DEFAULT '',
    reply_delay_seconds_min INT,
    reply_delay_seconds_max INT,
    allowed_tools_json CLOB NOT NULL,
    require_human_confirmation BOOLEAN NOT NULL,
    priority INT NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    notification_mode VARCHAR(64) NOT NULL DEFAULT 'AUTO',
    notification_keywords_json CLOB NOT NULL,
    digest_window_seconds INT,
    digest_max_messages INT,
    include_urgent_in_digest BOOLEAN NOT NULL DEFAULT FALSE,
    max_reply_chars INT NOT NULL DEFAULT 24,
    split_long_reply BOOLEAN NOT NULL DEFAULT TRUE,
    split_reply_chance_percent INT NOT NULL DEFAULT 33,
    private_history_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    history_max_messages INT NOT NULL DEFAULT 12,
    history_max_chars INT NOT NULL DEFAULT 2000,
    history_training_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    review_mode VARCHAR(64) NOT NULL DEFAULT 'STRICT_HANDOFF',
    knowledge_base_sources_json CLOB NOT NULL DEFAULT '[]',
    profile_context_json CLOB NOT NULL DEFAULT '{}'
);

-- 兼容开发环境已有的 H2 数据库；新字段均有默认值，不影响旧设定集。
ALTER TABLE conversation_profile ADD COLUMN IF NOT EXISTS max_reply_chars INT NOT NULL DEFAULT 24;
ALTER TABLE conversation_profile ADD COLUMN IF NOT EXISTS split_long_reply BOOLEAN NOT NULL DEFAULT TRUE;
ALTER TABLE conversation_profile ADD COLUMN IF NOT EXISTS split_reply_chance_percent INT NOT NULL DEFAULT 33;
ALTER TABLE conversation_profile ADD COLUMN IF NOT EXISTS private_history_enabled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE conversation_profile ADD COLUMN IF NOT EXISTS history_max_messages INT NOT NULL DEFAULT 12;
ALTER TABLE conversation_profile ADD COLUMN IF NOT EXISTS history_max_chars INT NOT NULL DEFAULT 2000;
ALTER TABLE conversation_profile ADD COLUMN IF NOT EXISTS history_training_enabled BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE conversation_profile ADD COLUMN IF NOT EXISTS review_mode VARCHAR(64) NOT NULL DEFAULT 'STRICT_HANDOFF';
ALTER TABLE conversation_profile ADD COLUMN IF NOT EXISTS knowledge_base_sources_json CLOB NOT NULL DEFAULT '[]';
ALTER TABLE conversation_profile ADD COLUMN IF NOT EXISTS profile_context_json CLOB NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_conversation_profile_user_id ON conversation_profile (user_id);
CREATE INDEX IF NOT EXISTS idx_conversation_profile_match ON conversation_profile (user_id, enabled, platform, chat_type);
CREATE INDEX IF NOT EXISTS idx_conversation_profile_priority ON conversation_profile (priority, updated_at);

CREATE TABLE IF NOT EXISTS secure_asset (
    id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL,
    name VARCHAR(255) NOT NULL,
    type VARCHAR(64) NOT NULL,
    description VARCHAR(2000) NOT NULL DEFAULT '',
    content_type VARCHAR(255) NOT NULL DEFAULT 'text/plain',
    payload_ciphertext CLOB NOT NULL,
    usage_policy VARCHAR(32) NOT NULL DEFAULT 'REUSABLE',
    remaining_uses INT,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL,
    last_used_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_secure_asset_user_updated ON secure_asset (user_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_secure_asset_user_enabled ON secure_asset (user_id, enabled);

CREATE TABLE IF NOT EXISTS memory_candidate (
    id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL,
    subject VARCHAR(255) NOT NULL,
    predicate_name VARCHAR(128) NOT NULL,
    fact_value CLOB NOT NULL,
    scope_type VARCHAR(32) NOT NULL,
    platform VARCHAR(64) NOT NULL DEFAULT '',
    scene VARCHAR(64) NOT NULL DEFAULT '',
    chat_type VARCHAR(32) NOT NULL DEFAULT '',
    chat_id VARCHAR(255) NOT NULL DEFAULT '',
    source_event_ids_json CLOB NOT NULL,
    source_actor_type VARCHAR(32) NOT NULL,
    fact_authority VARCHAR(32) NOT NULL,
    confidence DOUBLE NOT NULL,
    status VARCHAR(32) NOT NULL,
    rejection_reason VARCHAR(2000) NOT NULL DEFAULT '',
    first_seen_at TIMESTAMP NOT NULL,
    last_seen_at TIMESTAMP NOT NULL,
    expires_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_memory_user_status_updated
    ON memory_candidate (user_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_memory_user_scope
    ON memory_candidate (user_id, scope_type, platform, chat_type, chat_id);
CREATE INDEX IF NOT EXISTS idx_memory_user_expires
    ON memory_candidate (user_id, expires_at);

CREATE TABLE IF NOT EXISTS conversation_cognition_card (
    id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL,
    platform VARCHAR(64) NOT NULL,
    chat_type VARCHAR(32) NOT NULL,
    chat_id VARCHAR(255) NOT NULL,
    version INT NOT NULL,
    relationship_json CLOB NOT NULL,
    preferred_address_json CLOB NOT NULL,
    counterparty_traits_json CLOB NOT NULL,
    owner_expression_habits_json CLOB NOT NULL,
    counterparty_expression_habits_json CLOB NOT NULL,
    background_summary_json CLOB NOT NULL,
    current_progress_json CLOB NOT NULL,
    known_facts_json CLOB NOT NULL,
    recent_topics_json CLOB NOT NULL,
    open_questions_json CLOB NOT NULL,
    source_event_ids_json CLOB NOT NULL,
    source_message_count INT NOT NULL,
    status VARCHAR(32) NOT NULL,
    analyzed_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_cognition_card_scope
    ON conversation_cognition_card (user_id, platform, chat_type, chat_id);
CREATE INDEX IF NOT EXISTS idx_cognition_card_user_updated
    ON conversation_cognition_card (user_id, updated_at);

CREATE TABLE IF NOT EXISTS delegated_task (
    id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL,
    task_type VARCHAR(64) NOT NULL,
    status VARCHAR(64) NOT NULL,
    original_command CLOB NOT NULL,
    source_execution_id VARCHAR(160) NULL,
    target_query VARCHAR(255) NOT NULL DEFAULT '',
    platform VARCHAR(64) NOT NULL DEFAULT '',
    chat_type VARCHAR(32) NOT NULL DEFAULT '',
    chat_id VARCHAR(255) NOT NULL DEFAULT '',
    target_name VARCHAR(255) NOT NULL DEFAULT '',
    objective CLOB NOT NULL,
    success_criteria CLOB NOT NULL,
    deadline_text VARCHAR(255) NOT NULL DEFAULT '',
    confidence DOUBLE NOT NULL,
    clarification_question CLOB NOT NULL,
    requires_confirmation BOOLEAN NOT NULL DEFAULT TRUE,
    execution_mode VARCHAR(32) NOT NULL DEFAULT 'AUTO_COMPLETE',
    progress_summary CLOB NOT NULL DEFAULT '',
    state_json CLOB NOT NULL DEFAULT '{}',
    last_event_id VARCHAR(255) NOT NULL DEFAULT '',
    started_at TIMESTAMP NULL,
    completed_at TIMESTAMP NULL,
    completion_report CLOB NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL,
    updated_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_delegated_task_user_created
    ON delegated_task (user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_delegated_task_user_status
    ON delegated_task (user_id, status, updated_at);
CREATE INDEX IF NOT EXISTS idx_delegated_task_conversation_active
    ON delegated_task (user_id, platform, chat_type, chat_id, status, updated_at);
CREATE UNIQUE INDEX IF NOT EXISTS uk_delegated_task_source_target
    ON delegated_task (user_id, source_execution_id, platform, chat_type, chat_id);
