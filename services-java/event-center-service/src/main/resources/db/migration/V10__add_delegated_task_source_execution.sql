ALTER TABLE delegated_task
    ADD COLUMN source_execution_id VARCHAR(160) NULL;

CREATE UNIQUE INDEX uk_delegated_task_source_target
    ON delegated_task (user_id, source_execution_id, platform, chat_type, chat_id);
