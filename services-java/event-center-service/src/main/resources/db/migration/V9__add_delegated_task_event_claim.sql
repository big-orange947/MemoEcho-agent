CREATE TABLE IF NOT EXISTS delegated_task_event_claim (
    task_id VARCHAR(64) NOT NULL,
    event_id VARCHAR(255) NOT NULL,
    user_id VARCHAR(128) NOT NULL,
    claim_status VARCHAR(16) NOT NULL,
    claim_token VARCHAR(64) NOT NULL,
    lease_until TIMESTAMP(6) NULL,
    created_at TIMESTAMP(6) NOT NULL,
    updated_at TIMESTAMP(6) NOT NULL,
    PRIMARY KEY (task_id, event_id),
    INDEX idx_delegated_task_event_claim_lease (claim_status, lease_until)
);
