-- Conversation Profile 2.0 使用单个版本化 JSON 保存身份、对方、背景、任务、业务规则和资产引用。
-- 权限、审批、知识库和记忆策略继续保留在原有列，避免迁移时产生两套事实来源。
ALTER TABLE conversation_profile
    ADD COLUMN profile_context_json LONGTEXT NULL;

UPDATE conversation_profile
SET profile_context_json = '{"version":2}'
WHERE profile_context_json IS NULL OR profile_context_json = '';

ALTER TABLE conversation_profile
    MODIFY COLUMN profile_context_json LONGTEXT NOT NULL;
