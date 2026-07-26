-- 工作台自然语言委托任务。创建阶段只保存草案和确认状态，外部操作由受控执行器处理。
CREATE TABLE IF NOT EXISTS delegated_task (
    id VARCHAR(64) NOT NULL,
    user_id VARCHAR(128) NOT NULL,
    task_type VARCHAR(64) NOT NULL,
    status VARCHAR(64) NOT NULL,
    original_command TEXT NOT NULL,
    target_query VARCHAR(255) NOT NULL DEFAULT '',
    platform VARCHAR(64) NOT NULL DEFAULT '',
    chat_type VARCHAR(32) NOT NULL DEFAULT '',
    chat_id VARCHAR(255) NOT NULL DEFAULT '',
    target_name VARCHAR(255) NOT NULL DEFAULT '',
    objective TEXT NOT NULL,
    success_criteria TEXT NOT NULL,
    deadline_text VARCHAR(255) NOT NULL DEFAULT '',
    confidence DOUBLE NOT NULL,
    clarification_question TEXT NOT NULL,
    requires_confirmation BOOLEAN NOT NULL DEFAULT TRUE,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    KEY idx_delegated_task_user_created (user_id, created_at),
    KEY idx_delegated_task_user_status (user_id, status, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
