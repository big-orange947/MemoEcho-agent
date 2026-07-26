-- Schedule Service 初始结构。保留 IF NOT EXISTS 以兼容已有本地日程库。
CREATE TABLE IF NOT EXISTS schedule_item (
    id VARCHAR(64) NOT NULL,
    source_event_id VARCHAR(255) NOT NULL,
    platform VARCHAR(64) NOT NULL,
    chat_id VARCHAR(255) NOT NULL,
    sender_id VARCHAR(255) NOT NULL,
    title VARCHAR(500) NOT NULL,
    start_time DATETIME(6) NOT NULL,
    end_time DATETIME(6),
    location VARCHAR(1000),
    content TEXT NOT NULL,
    participants TEXT,
    confidence VARCHAR(64),
    created_at DATETIME(6) NOT NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_schedule_source_event_id (source_event_id),
    KEY idx_schedule_start_time (start_time),
    KEY idx_schedule_chat_start (chat_id, start_time),
    KEY idx_schedule_sender_start (sender_id, start_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
