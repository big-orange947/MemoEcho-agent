CREATE TABLE delegated_workflow (
    id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(128) NOT NULL,
    source_execution_id VARCHAR(160),
    original_command TEXT NOT NULL,
    title VARCHAR(255) NOT NULL DEFAULT '',
    workflow_type VARCHAR(32) NOT NULL,
    status VARCHAR(32) NOT NULL,
    plan_json LONGTEXT NOT NULL,
    facts_json LONGTEXT NOT NULL,
    progress_summary TEXT NOT NULL,
    failure_reason TEXT NOT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    completed_at DATETIME(6),
    CONSTRAINT uk_delegated_workflow_execution UNIQUE (user_id, source_execution_id),
    INDEX idx_delegated_workflow_user_status (user_id, status, updated_at)
);

ALTER TABLE delegated_task
    ADD COLUMN workflow_id VARCHAR(64),
    ADD COLUMN step_key VARCHAR(128) NOT NULL DEFAULT '',
    ADD COLUMN step_order INT NOT NULL DEFAULT 0,
    ADD COLUMN step_role VARCHAR(32) NOT NULL DEFAULT 'ACTION',
    ADD COLUMN step_instruction TEXT NULL,
    ADD COLUMN depends_on_json LONGTEXT NULL,
    ADD COLUMN required_facts_json LONGTEXT NULL,
    ADD COLUMN produces_facts_json LONGTEXT NULL,
    ADD COLUMN result_json LONGTEXT NULL,
    ADD COLUMN activation_version BIGINT NOT NULL DEFAULT 0,
    ADD CONSTRAINT fk_delegated_task_workflow
        FOREIGN KEY (workflow_id) REFERENCES delegated_workflow(id),
    ADD CONSTRAINT uk_delegated_task_workflow_step UNIQUE (workflow_id, step_key),
    ADD INDEX idx_delegated_task_workflow_status (workflow_id, status, step_order);
