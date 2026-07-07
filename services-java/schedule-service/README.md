# schedule-service

这是 Memo Echo Agent 的最小日程服务，当前目标不是做完整业务，而是先为 Agent 闭环提供稳定写入和查询接口。

## 当前能力

1. 接收结构化日程写入
2. 支持按 `chatId`、`senderId`、`sourceEventId` 过滤查询
3. 先使用内存存储，便于快速联调
4. 后续可以无缝替换为 MySQL 或其他持久化实现

## 主要接口

- `POST /internal/schedules`
- `GET /internal/schedules`
- `GET /actuator-like/health`

## 当前定位

这是第一阶段的最小实现，重点是先把：

`ScheduleAgent -> create_schedule tool -> schedule-service`

这条链打通。

