# 工作台收件箱状态

每条事件都有独立的 `inboxStatus`，让用户可以管理消息，而不仅是被动查看 Agent 的处理结果。

| 状态 | 含义 | 是否出现在工作台重点摘要 |
| --- | --- | --- |
| `NEW` | 新收到、尚未查看 | 是 |
| `READ` | 已浏览但尚未完成处理 | 是 |
| `SNOOZED` | 延后到指定时间处理 | 到期前否，到期后自动恢复为 `NEW` |
| `DONE` | 用户已处理 | 否 |
| `IGNORED` | 用户明确忽略 | 否 |

## 接口

```text
POST /internal/events/{eventId}/inbox/read
POST /internal/events/{eventId}/inbox/done
POST /internal/events/{eventId}/inbox/ignore
POST /internal/events/{eventId}/inbox/snooze
```

稍后处理请求示例：

```json
{
  "snoozedUntil": "2026-07-10T12:00:00Z"
}
```

事件列表支持 `GET /internal/events?inboxStatus=NEW` 过滤。事件详情和会话消息会返回 `inboxStatus`、`inboxUpdatedAt` 与 `snoozedUntil`，前端可据此实现收件箱筛选和“稍后处理”提醒。
