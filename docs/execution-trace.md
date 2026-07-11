# Agent 执行轨迹

事件中心会为每次成功获得 Runtime 响应的事件保存一份脱敏执行轨迹，供后续工作台展示“这条消息为什么会这样处理”。

`GET /internal/events/{eventId}` 的 `executionTrace` 字段包含：

- `executionId`：Runtime 执行 ID
- `route`：最终路由
- `summary`：Runtime 摘要
- `writeBackActions`：回写动作类型
- `steps`：每个 Agent 的名称、状态、工具名称、下一步动作和人工确认标记

执行轨迹不会保存以下内容：

- 工具参数，例如消息内容、文件路径或请求体
- `structured_result`
- 模型配置和 API Key
- 系统提示词、用户自定义提示词

这样可以让前端展示 Agent 的工作过程，同时避免把用户密钥、私密文件内容或完整提示词暴露到事件查询接口中。
