-- 安全资产仓库：payload_ciphertext 只保存 AES-GCM 密文，Profile 中仅保存资产 ID 引用。
CREATE TABLE IF NOT EXISTS secure_asset (
    id VARCHAR(64) NOT NULL,
    user_id VARCHAR(128) NOT NULL,
    name VARCHAR(255) NOT NULL,
    type VARCHAR(64) NOT NULL,
    description VARCHAR(2000) NOT NULL,
    content_type VARCHAR(255) NOT NULL,
    payload_ciphertext LONGTEXT NOT NULL,
    usage_policy VARCHAR(32) NOT NULL,
    remaining_uses INT,
    enabled BOOLEAN NOT NULL,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    last_used_at DATETIME(6),
    PRIMARY KEY (id),
    KEY idx_secure_asset_user_updated (user_id, updated_at),
    KEY idx_secure_asset_user_enabled (user_id, enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
