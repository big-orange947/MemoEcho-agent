# 桌面 Agent 命令接口

## 目标

桌面客户端不能直接访问 Python Runtime。所有自然语言任务必须先经过 `event-center` 校验 JWT，再转换为标准事件、持久化并派发给 Orchestrator。

## 调用链路

1. 客户端调用 `POST /internal/workspace/commands`。
2. `event-center` 从 Bearer Token 解析当前用户。
3. 后端生成 `platform=desktop`、`eventType=desktop_command` 的标准事件。
4. Python Router 只接受白名单中的 `requestedRoute`。
5. Runtime 使用事件里的用户 ID 解析该用户自己的模型配置和会话设定。
6. 结果压缩为稳定 DTO 返回客户端，同时完整事件和执行轨迹继续保存在事件中心。

## 请求示例

```http
POST /internal/workspace/commands
Authorization: Bearer <jwt>
Content-Type: application/json
```

```json
{
  "prompt": "帮我规划今天的工作",
  "requestedRoute": "task_plan"
}
```

`requestedRoute` 可以留空，由 Router 根据文本自动判断。当前允许：

- `social_reply`
- `chat_summary`
- `task_plan`
- `schedule_extract`
- `file_analysis`
- `message_dispatch`
- `group_ops`

## 响应说明

响应包含命令 ID、真实 route、最终纯文本回复、参与执行的 Agent、后续动作和人工确认标记。Runtime 不可用时仍返回结构化失败结果，客户端不会出现无响应状态。

桌面命令使用 `platform=desktop`，因此 Orchestrator 不会把结果自动回写到 QQ 或其他聊天平台。
