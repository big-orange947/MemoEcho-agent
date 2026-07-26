# QQ 群管理安全设计

GroupOpsAgent 只处理当前 QQ 群，不能通过消息参数跨群操作，也不能把模型生成的原始 Action 直接转发给 NapCat。

## 权限层级

| 等级 | 能力 | 默认行为 |
|---|---|---|
| 只读 | 群信息、成员、公告、禁言、精华、文件列表 | 可自动执行 |
| 中风险 | 单人禁言、解除禁言、群名片、精华消息 | 必须人工确认 |
| 高风险 | 全员禁言、群名、公告、踢人、管理员变更 | 必须输入包含动作和目标的完整确认短语 |
| 禁止 | 退群、解散群、任意 Action、删除群文件 | 当前版本不开放 |

`manage_qq_group` 是特权工具。会话设定没有在 `allowedTools` 中明确列出它时，Runtime 会默认移除该工具；Skill 的工具策略只能收窄权限，不能授予它。

## 写操作链路

1. GroupOpsAgent 先验证控制消息来自当前登录 QQ 本人，再使用确定性规则识别动作和参数；普通群成员不能发起写操作。
2. ManageQqGroupTool 在 Runtime 内部生成五分钟有效的一次性审批令牌，不调用 NapCat，也不把令牌写入 Agent 结果。
3. 桌面端展示群号、目标成员、动作、风险和确认短语。
4. Event Center 校验事件属于当前登录用户后，按事件调用 `POST /v1/group-operations/approve-event/{eventId}`。
5. Runtime 按事件查找并消费内部令牌，再调用 Connector 固定白名单接口。
6. 准备、拒绝、过期和执行结果写入 `data/group-operations-audit.jsonl`。

审批请求示例：

```json
{
  "confirmationText": "确认执行"
}
```

高风险动作必须使用审批单返回的完整 `confirmationPhrase`。短语不一致、审批过期、重复提交或事件不属于当前用户都会被拒绝；一次性令牌不会离开 Python Runtime。
