# 工作台草稿操作接口

工作台不直接调用 NapCat，而是调用 `event-center-service` 的事件操作接口。这样草稿来源、用户操作、发送结果都会留在同一条事件记录中。

## 接口

### 确认发送草稿

`POST /internal/events/{eventId}/draft/confirm`

请求体可为空；传入 `message` 时会覆盖 Agent 生成的草稿后再发送。

```json
{
  "message": "你好，我已经看到了，下午两点见。",
  "note": "手动调整语气后确认"
}
```

成功后事件状态变为：

- `processingStatus: MANUALLY_SENT`
- `writeBackStatus: SENT`
- `lastAction: CONFIRMED`

群聊中如果原消息明确 @ 了机器人，确认发送会继续 @ 原消息发送者。

### 拒绝草稿

`POST /internal/events/{eventId}/draft/reject`

```json
{
  "reason": "这条消息需要自己处理"
}
```

不会发送外部消息。事件状态变为 `DRAFT_REJECTED`，并保留草稿与拒绝原因。

### 重新执行事件

`POST /internal/events/{eventId}/retry`

仅允许处理失败、发送失败或尚未完成处理的事件重试。它会重新请求 Python Agent Runtime，并以最新的路由、草稿和回写策略刷新事件状态。

## 前端展示字段

查询 `GET /internal/events/{eventId}` 时，工作台可直接使用：

- `replyDraft`：可编辑的草稿文本
- `needHumanConfirmation`：是否显示“确认发送/拒绝”按钮
- `processingStatus` 与 `writeBackStatus`：展示处理进度
- `lastAction`、`lastActionNote`、`lastActionAt`：展示最后一次人工操作记录
