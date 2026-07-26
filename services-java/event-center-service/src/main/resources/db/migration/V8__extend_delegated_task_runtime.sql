-- 委托任务运行态由 Java 持久化，Python LangGraph 只提交经过权限边界校验的状态更新。
ALTER TABLE delegated_task
    ADD COLUMN execution_mode VARCHAR(32) NOT NULL DEFAULT 'AUTO_COMPLETE',
    ADD COLUMN progress_summary TEXT NOT NULL,
    ADD COLUMN state_json LONGTEXT NOT NULL,
    ADD COLUMN last_event_id VARCHAR(255) NOT NULL DEFAULT '',
    ADD COLUMN started_at DATETIME(6) NULL,
    ADD COLUMN completed_at DATETIME(6) NULL,
    ADD COLUMN completion_report TEXT NOT NULL;

CREATE INDEX idx_delegated_task_conversation_active
    ON delegated_task (user_id, platform, chat_type, chat_id, status, updated_at);
