-- Task Service 初始结构。保留 IF NOT EXISTS 以兼容已有本地任务库。
CREATE TABLE IF NOT EXISTS task_item (
    id VARCHAR(64) NOT NULL,
    source_event_id VARCHAR(255) NOT NULL,
    platform VARCHAR(64) NOT NULL,
    chat_id VARCHAR(255) NOT NULL,
    sender_id VARCHAR(255) NOT NULL,
    title VARCHAR(500) NOT NULL,
    description TEXT NOT NULL,
    due_time DATETIME(6),
    priority VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    confidence VARCHAR(64),
    created_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_task_source_event_id (source_event_id),
    KEY idx_task_due_time (due_time),
    KEY idx_task_status_priority (status, priority),
    KEY idx_task_chat_created (chat_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
