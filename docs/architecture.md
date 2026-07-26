# 架构设计

## 总体概览

Memo Echo Agent 采用分层的混合架构：

1. Connector 接入层
2. 事件骨干层
3. Agent Runtime 层
4. Tool 执行层
5. 结构化与语义存储层

## Java 微服务层

### connector-service

- 接收来自 NapCat 和后续其他连接器的原始事件
- 将不同平台的原始载荷统一转换为 `UnifiedEvent`
- 把标准化事件推送到事件骨干层

### event-center-service

- 做事件幂等和去重
- 持久化入站事件记录
- 把事件发布到异步通道
- 接收 Agent 执行结果
- 保存审计日志
- 接收主控台自然语言命令，完成鉴权、基础风险审计和可靠转发，但不直接解析业务任务

### schedule-service

- 创建和查询日程
- 支持按用户、群组、时间范围查询

### task-service

- 创建和更新任务
- 支持优先级、状态、今日计划查询

### file-service

- 管理附件元数据
- 建立源文件和存储对象的映射
- 缓存文本抽取、OCR 等解析结果

### notification-service

- 负责私聊、群聊和提醒消息的发送
- 作为 Agent 结果回传的统一出口

### user-context-service

- 存储用户、联系人和群组上下文
- 存放用户偏好和策略设置

## Python Agent Runtime 层

### Router

负责把事件路由到预定义场景：

- `chat_summary`
- `schedule_extract`
- `task_plan`
- `file_analysis`
- `social_reply`
- `group_ops`

主控台命令会先进入 Router Agent。Router Agent 需要根据用户命令和可用联系人列表判断目标会话、任务类型和执行模式；只有明确出现群聊语义时才选择群聊，否则优先选择私聊。

### Planner

负责把 route 转换成执行计划。计划可以是：

- 单 Agent 直接处理
- 主从协作
- 多步骤编排

### Orchestrator

- 构建任务上下文
- 执行计划
- 调用 Agent 和 Tool
- 聚合中间结果
- 生成最终回复和写回动作
- 对主控台委托任务执行 ReAct 式工具循环，并在任务满足完成条件时调用结束任务工具

### 领域 Agent

- Inbox Agent
- Schedule Agent
- Work Agent
- File Agent
- Social Agent
- GroupOps Agent

每个 Agent 只负责自己的边界领域，并返回结构化结果。

## 写操作边界

Agent 本身不能直接写数据库或调用外部系统。

所有写操作都必须通过 Tool 层完成，这样可以保证：

- 权限边界清晰
- 外部副作用可审计
- 回退和测试更容易控制

## 推荐部署流

```text
NapCat -> connector-service -> event-center-service -> agent runtime
       -> tool calls -> Java services -> notification-service -> NapCat
```

当前仓库里的最小可运行版本采用两段式接入：

1. `qq-napcat-connector` 只负责标准化事件并提交给 `event-center-service`
2. `event-center-service` 负责幂等去重，并通过 dispatch adapter 把事件送到 Python Runtime

这里的 dispatch adapter 目前还是 HTTP 转发，占位真实 MQ。

后续替换顺序建议：

1. `event-center-service` 内部增加 Outbox
2. 接入 RocketMQ / RabbitMQ / Kafka 之一
3. Python Runtime 改为订阅 MQ，而不是暴露同步处理入口
