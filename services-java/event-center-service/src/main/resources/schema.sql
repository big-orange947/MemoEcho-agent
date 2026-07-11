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

CREATE INDEX IF NOT EXISTS idx_user_model_profile_user_id ON user_model_profile (user_id);
CREATE INDEX IF NOT EXISTS idx_user_model_profile_updated_at ON user_model_profile (updated_at);

CREATE TABLE IF NOT EXISTS event_record (
    event_id VARCHAR(255) PRIMARY KEY,
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
    snoozed_until TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_event_record_received_at ON event_record (received_at);
CREATE INDEX IF NOT EXISTS idx_event_record_inbox_status ON event_record (inbox_status);

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
    include_urgent_in_digest BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS idx_conversation_profile_user_id ON conversation_profile (user_id);
CREATE INDEX IF NOT EXISTS idx_conversation_profile_match ON conversation_profile (user_id, enabled, platform, chat_type);
CREATE INDEX IF NOT EXISTS idx_conversation_profile_priority ON conversation_profile (priority, updated_at);
