-- 任务起点水位：固化每个委托步骤开始执行的起点事件 ID 与会话范围。
-- start_event_id 是 L1（起点后全部消息）与 L2（起点前有限窗口）的分界依据，
-- conversation_scope_json 固定为步骤创建时解析出的平台会话范围，
-- 历史查询参数必须来自这里，而不是由 Runtime 从当前事件临时推导。
ALTER TABLE delegated_task
    ADD COLUMN start_event_id VARCHAR(255) NOT NULL DEFAULT '',
    ADD COLUMN conversation_scope_json VARCHAR(512) NOT NULL DEFAULT '';

-- L0 当前事件：每次 LangGraph 执行前把当前入站事件写入这里。
-- 即使历史查询接口失败，Runtime 仍可读取当前事件继续推理，保证当前事件不可丢失。
CREATE TABLE IF NOT EXISTS delegated_task_current_event (
    task_id VARCHAR(64) NOT NULL,
    workflow_id VARCHAR(64) NOT NULL,
    step_key VARCHAR(128) NOT NULL DEFAULT '',
    conversation_scope_json VARCHAR(512) NOT NULL DEFAULT '',
    event_id VARCHAR(255) NOT NULL,
    event_type VARCHAR(64) NOT NULL DEFAULT '',
    sender_id VARCHAR(255) NOT NULL DEFAULT '',
    text TEXT NOT NULL,
    occurred_at DATETIME(6) NOT NULL,
    payload_json LONGTEXT NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (task_id),
    CONSTRAINT fk_delegated_task_current_event_task
        FOREIGN KEY (task_id) REFERENCES delegated_task(id),
    INDEX idx_delegated_task_current_event_workflow (workflow_id, step_key, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
