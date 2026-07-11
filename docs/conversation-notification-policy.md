# 会话通知策略

会话设定集新增通知字段，用于覆盖系统默认的双通道分发规则。它们只决定工作台是否提醒与如何归并，不会改变草稿确认、自动回复和工具权限规则。

## 配置字段

通过 `POST /internal/conversation-profiles` 或 `PUT /internal/conversation-profiles/{profileId}` 传入：

```json
{
  "name": "项目群摘要模式",
  "platform": "qq",
  "chatType": "group",
  "chatIds": ["1098307542"],
  "notificationMode": "DIGEST_ONLY",
  "notificationKeywords": ["截止", "发布", "紧急"],
  "digestWindowSeconds": 900,
  "digestMaxMessages": 12,
  "includeUrgentInDigest": true
}
```

| 字段 | 说明 |
| --- | --- |
| `notificationMode` | `AUTO`、`URGENT_ONLY`、`DIGEST_ONLY`、`MUTED`。未传时为 `AUTO`。 |
| `notificationKeywords` | 当前会话额外的重点关键词；在 `AUTO` 和 `URGENT_ONLY` 模式下命中后走即时通道。 |
| `digestWindowSeconds` | 慢通道摘要时间窗口，范围 `60` 至 `86400` 秒。未传则使用运行时默认值。 |
| `digestMaxMessages` | 单次摘要最大消息数，范围 `2` 至 `100`。未传则使用运行时默认值。 |
| `includeUrgentInDigest` | 默认 `false`。开启后，快通道消息会保留在后续摘要的上下文中，但不会由自身触发数量或时间阈值。 |

## 行为定义

| 模式 | 行为 |
| --- | --- |
| `AUTO` | 私聊、@ 自身、内置重点词和自定义重点词即时提醒；其余消息归并。 |
| `URGENT_ONLY` | 仅重点消息即时提醒；普通消息保留在历史中但不生成提醒或摘要。 |
| `DIGEST_ONLY` | 所有消息进入慢通道归并，私聊和 @ 消息也不立即提醒。 |
| `MUTED` | 仅保存事件历史，不产生工作台提醒、摘要或自动回写。 |

通知策略的最终生效结果会出现在工作台收件箱的 `notification` 字段中，便于 UI 展示触发原因和归并状态。被 `MUTED` 或 `URGENT_ONLY` 策略抑制的普通消息会返回 `priority=NONE` 与 `aggregationStatus=SUPPRESSED`，前端不应将其误展示为待生成摘要。
