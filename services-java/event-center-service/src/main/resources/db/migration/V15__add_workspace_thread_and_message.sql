-- 主控台对话式工作区：线程与消息。
-- workspace_thread 是对话容器；workspace_thread_message 是线程内的消息，
-- 每一条 agent 消息关联一次主控台命令的 executionId（= commandId）与
-- 可能产生的委托任务/父工作流 ID，供前端把任务卡片内嵌进对话流。
CREATE TABLE IF NOT EXISTS workspace_thread (
    id VARCHAR(36) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    title VARCHAR(200) NOT NULL DEFAULT '',
    pinned TINYINT(1) NOT NULL DEFAULT 0,
    archived TINYINT(1) NOT NULL DEFAULT 0,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    INDEX idx_workspace_thread_user (user_id, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS workspace_thread_message (
    id VARCHAR(36) NOT NULL,
    thread_id VARCHAR(36) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    role VARCHAR(16) NOT NULL,
    content TEXT NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'done',
    execution_id VARCHAR(128) NOT NULL DEFAULT '',
    task_id VARCHAR(36) NULL,
    workflow_id VARCHAR(36) NULL,
    result_json LONGTEXT NULL,
    created_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_workspace_thread_message_thread
        FOREIGN KEY (thread_id) REFERENCES workspace_thread(id),
    INDEX idx_workspace_thread_message_thread (thread_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;