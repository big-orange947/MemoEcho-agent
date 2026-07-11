# 事件持久化

`event-center-service` 现在默认使用 `JdbcEventRecordRepository` 持久化事件，内存仓库只保留给单元测试或轻量联调使用。

`event_record` 表保存：

- 标准事件 `payload_json`
- Runtime 处理状态、路由、回写状态和草稿
- 用户操作审计信息
- 收件箱状态与稍后处理时间
- 脱敏后的 Agent 执行轨迹 `execution_trace_json`

实现采用“先 `UPDATE`，无记录再 `INSERT`”的写入方式，因此可运行于当前 H2 本地数据库，也不依赖 H2 专属 `MERGE` 语法，后续切换 MySQL 时无需修改业务仓储逻辑。

默认本地 H2 文件位于 `event-center-service/data/event-center-db`。如果切换到 MySQL，只需修改 `spring.datasource` 配置并提供 MySQL JDBC 驱动；表结构由 `schema.sql` 初始化。
