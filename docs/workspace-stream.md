# 工作台实时更新流

工作台使用 Server-Sent Events（SSE）订阅指定平台账号的轻量更新。前端建立连接后，无需持续轮询整个收件箱列表。

## 建立订阅

```http
GET /internal/workspace/stream?platform=qq&accountId=3969785168
Accept: text/event-stream
```

订阅按 `platform + accountId` 隔离。当前 QQ 接入中 `accountId` 对应 NapCat 事件的 `selfId`，即机器人登录账号。后续用户账户体系接入后，这个键会与登录用户的连接授权一起校验。

## 事件类型

| SSE 事件名 | 触发时机 |
| --- | --- |
| `connected` | 前端订阅建立成功。 |
| `inbox.updated` | 普通消息处理完成、草稿确认/拒绝、重试、已读/完成/忽略/稍后处理等状态变化。 |
| `digest.ready` | 慢通道时间或数量阈值触发，生成会话摘要。 |

数据示例：

```json
{
  "type": "digest.ready",
  "eventId": "digest:1b0d4e37-8fef-4c22-9cef-7d5d0e81b827",
  "platform": "qq",
  "accountId": "3969785168",
  "chatType": "group",
  "chatId": "1098307542",
  "processingStatus": "DIGEST_READY",
  "inboxStatus": "NEW",
  "actionRequired": false,
  "occurredAt": "2026-07-10T10:00:00Z"
}
```

SSE 消息只携带定位和状态字段。UI 收到后可调用 `GET /internal/workspace/inbox` 或事件详情接口刷新对应卡片，避免在长连接中传输完整消息正文和草稿历史。
