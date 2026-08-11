-- 修复旧环境或手工迁移数据库中的空事件游标，并保持运行态字段约束一致。
UPDATE delegated_task
SET last_event_id = ''
WHERE last_event_id IS NULL;

ALTER TABLE delegated_task
    MODIFY COLUMN last_event_id VARCHAR(255) NOT NULL DEFAULT '';
