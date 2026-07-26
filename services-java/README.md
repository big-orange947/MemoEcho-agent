# Java 服务规划

这个目录预留给 Java 微服务，主要负责：

- Connector 接入
- 事件骨干层
- 日程服务
- 任务服务
- 文件服务
- 通知服务
- 用户上下文服务

Python Runtime 应该依赖服务契约和 API，而不是直接依赖 Java 的实现细节。

当前最小链路已经拆成三段：

1. `qq-napcat-connector`
2. `event-center-service`
3. `schedule-service`

当前还新增了一个工作待办持久化服务：

4. `task-service`

## MySQL 持久化配置

运行数据已经按服务拆分到三个 MySQL 数据库，默认均连接本机 `3306` 端口：

| 服务 | 默认数据库 | 持久化内容 |
| --- | --- | --- |
| `event-center-service` | `memo_echo_event_center` | 用户、连接、设定集、事件、摘要和派发重试任务 |
| `schedule-service` | `memo_echo_schedule` | 自动提取和手动创建的日程 |
| `task-service` | `memo_echo_task` | Agent 规划和用户创建的任务 |

数据库不存在时，拥有建库权限的账号会通过 `createDatabaseIfNotExist=true` 自动创建数据库，
随后由 Flyway 执行各服务 `db/migration` 目录下的版本迁移。当前初始结构为：

- Event Center：`V1__init_event_center_schema.sql`
- Schedule Service：`V1__init_schedule_schema.sql`
- Task Service：`V1__init_task_schema.sql`

首次连接已有的非空数据库时，Flyway 会先登记版本 `0`，再幂等执行 `V1`，不会清空已有数据。
执行记录保存在每个数据库的 `flyway_schema_history` 表中。

密码没有源码默认值，启动服务前必须通过环境变量提供。三个服务使用同一个本机 MySQL
账号时，可以在当前 PowerShell 会话中统一设置：

```powershell
$env:EVENT_CENTER_DB_PASSWORD="你的本机 MySQL 密码"
$env:SCHEDULE_DB_PASSWORD="你的本机 MySQL 密码"
$env:TASK_DB_PASSWORD="你的本机 MySQL 密码"
```

如需修改账号、地址或驱动，可分别使用以下环境变量：

- Event Center：`EVENT_CENTER_DB_URL`、`EVENT_CENTER_DB_USERNAME`、`EVENT_CENTER_DB_PASSWORD`、`EVENT_CENTER_DB_DRIVER`
- Schedule Service：`SCHEDULE_DB_URL`、`SCHEDULE_DB_USERNAME`、`SCHEDULE_DB_PASSWORD`、`SCHEDULE_DB_DRIVER`
- Task Service：`TASK_DB_URL`、`TASK_DB_USERNAME`、`TASK_DB_PASSWORD`、`TASK_DB_DRIVER`

如果使用旧项目的 Docker Compose 配置，其端口映射为 `3307:3306`，需要把三个
`*_DB_URL` 的宿主机端口改为 `3307`。单元测试不会连接本机 MySQL，而是使用
MySQL 兼容模式的内存 H2。

## 数据库结构升级约定

已经执行过的迁移文件不能再修改，否则 Flyway 校验会报告校验和不一致。后续结构变更需要追加新版本，
例如：

```text
src/main/resources/db/migration/
├── V1__init_schema.sql
├── V2__add_task_priority.sql
└── V3__add_event_retry_index.sql
```

版本号必须递增，文件名使用 `V版本号__说明.sql`，其中说明前是两个下划线。迁移脚本应只处理
数据库结构或必要的基础数据，不应删除用户业务数据。
