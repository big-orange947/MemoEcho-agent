CREATE TABLE IF NOT EXISTS delegated_workflow_step_dispatch (
    id BIGINT NOT NULL AUTO_INCREMENT,
    workflow_id VARCHAR(64) NOT NULL,
    step_key VARCHAR(120) NOT NULL,
    activation_version BIGINT NOT NULL,
    task_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'PENDING',
    attempt_count INT NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMP(6) NOT NULL,
    lease_until TIMESTAMP(6) NULL,
    last_error VARCHAR(1000) NULL,
    created_at TIMESTAMP(6) NOT NULL,
    updated_at TIMESTAMP(6) NOT NULL,
    completed_at TIMESTAMP(6) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_workflow_step_activation (workflow_id, step_key, activation_version),
    KEY idx_workflow_step_dispatch_due (status, next_attempt_at, lease_until)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
