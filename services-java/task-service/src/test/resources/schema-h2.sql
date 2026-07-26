CREATE TABLE IF NOT EXISTS task_item (
    id VARCHAR(64) PRIMARY KEY,
    source_event_id VARCHAR(255) NOT NULL UNIQUE,
    platform VARCHAR(64) NOT NULL,
    chat_id VARCHAR(255) NOT NULL,
    sender_id VARCHAR(255) NOT NULL,
    title VARCHAR(500) NOT NULL,
    description CLOB NOT NULL,
    due_time TIMESTAMP,
    priority VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    confidence VARCHAR(64),
    created_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_due_time ON task_item(due_time);
CREATE INDEX IF NOT EXISTS idx_task_status_priority ON task_item(status, priority);
CREATE INDEX IF NOT EXISTS idx_task_chat_created ON task_item(chat_id, created_at);
