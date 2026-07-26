CREATE TABLE IF NOT EXISTS schedule_item (
    id VARCHAR(64) PRIMARY KEY,
    source_event_id VARCHAR(255) NOT NULL UNIQUE,
    platform VARCHAR(64) NOT NULL,
    chat_id VARCHAR(255) NOT NULL,
    sender_id VARCHAR(255) NOT NULL,
    title VARCHAR(500) NOT NULL,
    start_time TIMESTAMP(6) NOT NULL,
    end_time TIMESTAMP(6),
    location VARCHAR(1000),
    content CLOB NOT NULL,
    participants CLOB,
    confidence VARCHAR(64),
    created_at TIMESTAMP(6) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_schedule_start_time ON schedule_item (start_time);
CREATE INDEX IF NOT EXISTS idx_schedule_chat_start ON schedule_item (chat_id, start_time);
CREATE INDEX IF NOT EXISTS idx_schedule_sender_start ON schedule_item (sender_id, start_time);
