-- 会话代理任务的运行状态。任务定义仍保存在 Profile 中，运行状态按会话独立持久化。
CREATE TABLE IF NOT EXISTS conversation_proxy_task_state (
    profile_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(128) NOT NULL,
    platform VARCHAR(64) NOT NULL DEFAULT '',
    chat_type VARCHAR(32) NOT NULL DEFAULT '',
    chat_id VARCHAR(255) NOT NULL,
    objective_hash VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL,
    completion_summary TEXT NOT NULL,
    completion_reason TEXT NOT NULL,
    completion_evidence_json TEXT NOT NULL,
    requested_at DATETIME(6) NULL,
    decided_at DATETIME(6) NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (profile_id, chat_id),
    KEY idx_proxy_task_user_status (user_id, status, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
