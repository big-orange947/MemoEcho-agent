# 工作台收件箱接口

`event-center-service` 提供工作台收件箱聚合接口，供后续前端一次获取消息卡片、草稿、待确认状态和收件箱统计。该接口只读取已保存的事件，不会触发 QQ 发送或调用 Agent。

## 获取收件箱

```http
GET /internal/workspace/inbox?inboxStatus=NEW&limit=50
```

参数说明：

| 参数 | 是否必填 | 说明 |
| --- | --- | --- |
| `inboxStatus` | 否 | 可选值：`NEW`、`READ`、`SNOOZED`、`DONE`、`IGNORED`。不传时只显示当前仍需要关注的消息。 |
| `limit` | 否 | 返回卡片数量，默认 `50`，最大 `200`。非法值会回退到默认值。 |

不传 `inboxStatus` 时，接口会排除已完成、已忽略以及尚未到期的稍后处理消息。已到期的稍后处理消息会自动视为 `NEW`。

响应示例：

```json
{
  "generatedAt": "2026-07-10T08:00:00Z",
  "inboxStatusFilter": "NEW",
  "totalCount": 2,
  "newCount": 1,
  "readCount": 1,
  "actionRequiredCount": 1,
  "items": [
    {
      "eventId": "qq:message:private:1001",
      "platform": "qq",
      "chatType": "private",
      "chatId": "2597164807",
      "chatName": "freeze",
      "senderId": "2597164807",
      "senderName": "freeze",
      "text": "下午两点开会",
      "timestamp": "2026-07-10T06:00:00Z",
      "route": "social_reply",
      "processingStatus": "NEEDS_CONFIRMATION",
      "writeBackStatus": "CONFIRM_REQUIRED",
      "replyDraft": "好的，下午两点见。",
      "needHumanConfirmation": true,
      "actionRequired": true,
      "inboxStatus": "NEW",
      "snoozedUntil": null,
      "lastAction": "",
      "lastActionAt": null,
      "notification": {
        "channel": "urgent",
        "priority": "HIGH",
        "triggerReason": "at_self",
        "notifyNow": true,
        "aggregationKey": "qq:group:1098307542",
        "aggregationStatus": "IMMEDIATE",
        "bufferedCount": 0,
        "summaryCandidate": ""
      }
    }
  ]
}
```

## 前端操作衔接

收件箱卡片的 `eventId` 可直接用于以下已有接口：

| 用户操作 | 接口 |
| --- | --- |
| 标为已读 | `POST /internal/events/{eventId}/inbox/read` |
| 标为已处理 | `POST /internal/events/{eventId}/inbox/done` |
| 忽略 | `POST /internal/events/{eventId}/inbox/ignore` |
| 稍后处理 | `POST /internal/events/{eventId}/inbox/snooze` |
| 确认发送草稿 | `POST /internal/events/{eventId}/draft/confirm` |
| 拒绝草稿 | `POST /internal/events/{eventId}/draft/reject` |
| 重试 Agent 处理 | `POST /internal/events/{eventId}/retry` |

当 `actionRequired=true` 时，前端应突出显示该卡片，并按 `writeBackStatus` 提供“确认发送”“拒绝”或“重试”等操作。

## 通知决策字段

`notification` 由 Python Runtime 的双通道分发器产生，并由事件中心持久化到执行轨迹中。Java 服务不重新判断优先级，避免规则漂移。

| 字段 | 说明 |
| --- | --- |
| `channel` | `urgent` 表示即时通道，`normal` 表示慢通道。 |
| `priority` | 当前取值为 `HIGH`、`NORMAL`、`LOW`、`NONE`，供 UI 控制视觉强调程度。 |
| `triggerReason` | 触发即时提醒的原因，例如 `private_chat`、`at_self`、`keyword_notice`；普通消息为 `none`。 |
| `notifyNow` | 是否应立即在工作台显示通知。 |
| `aggregationStatus` | `IMMEDIATE`、`BUFFERED`、`SUMMARY_READY` 或 `SUPPRESSED`。 |
| `bufferedCount` | 该会话慢通道当前归并的消息数量。 |
| `summaryCandidate` | 达到归并条件后生成的摘要候选文本；未触发归并时为空。 |
