# event-center-service

`event-center-service` 位于接入层和 Python Agent Runtime 之间，当前先承担“统一收口、去重、转发、查询”四类职责。

当前版本已经支持：

1. 接收统一事件模型 `UnifiedEvent`
2. 基于 `eventId` 做幂等去重
3. 通过可替换的 dispatch adapter 将事件转发给下游 Agent Runtime
4. 提供事件与会话查询接口，方便本地联调和后续前端接入

目前为了便于本地开发，默认仍然通过 HTTP 转发到 Python Runtime。后续如果切换为 MQ，只需要替换 `AgentRuntimeDispatchClient` 即可，例如：

- RocketMQ producer
- RabbitMQ publisher
- Kafka producer
- Outbox + CDC

## 当前接口

### 事件接口

- `POST /internal/events/ingest`
- `GET /internal/events`
- `GET /internal/events/{eventId}`

### 会话接口

- `GET /internal/conversations/overview`
- `GET /internal/conversations`
  - 可选参数：`platform`、`chatType`、`keyword`、`dispatchMode`、`activeWithinMinutes`
- `GET /internal/conversations/{chatId}/messages`
  - 可选参数：`platform`、`chatType`、`limit`

### 健康检查

- `GET /actuator-like/health`
