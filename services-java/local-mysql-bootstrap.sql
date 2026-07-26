-- Memo Echo 本地 MySQL 初始化脚本。
--
-- 这里只创建数据库，不重复维护业务表结构。
-- 三个 Spring Boot 服务启动后会由 Flyway 按 db/migration 中的版本脚本自动建表，
-- 避免手工建表与 Flyway 再次执行 ALTER TABLE 时产生重复列或校验冲突。

CREATE DATABASE IF NOT EXISTS memo_echo_event_center
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

CREATE DATABASE IF NOT EXISTS memo_echo_schedule
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

CREATE DATABASE IF NOT EXISTS memo_echo_task
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

-- 执行完成后可以用下面三条语句确认数据库已经存在。
SHOW DATABASES LIKE 'memo_echo_event_center';
SHOW DATABASES LIKE 'memo_echo_schedule';
SHOW DATABASES LIKE 'memo_echo_task';
