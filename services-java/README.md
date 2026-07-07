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
