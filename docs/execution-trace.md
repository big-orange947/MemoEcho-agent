# Agent 执行轨迹

事件中心会为每次成功获得 Runtime 响应的事件保存一份脱敏执行轨迹，供后续工作台展示“这条消息为什么会这样处理”。

`GET /internal/events/{eventId}` 的 `executionTrace` 字段包含：

- `executionId`：Runtime 执行 ID
- `route`：最终路由
- `summary`：Runtime 摘要
- `writeBackActions`：回写动作类型
- `steps`：每个 Agent 的名称、状态、工具名称、下一步动作和人工确认标记
- `verifiedMemoryIds`：本次实际注入 Agent 的已确认长期记忆 ID，仅用于来源审计

执行轨迹不会保存以下内容：

- 工具参数，例如消息内容、文件路径或请求体
- `structured_result`
- 模型配置和 API Key
- 系统提示词、用户自定义提示词
- 长期记忆正文、谓词和值

这样可以让前端展示 Agent 的工作过程，同时避免把用户密钥、私密文件内容或完整提示词暴露到事件查询接口中。

桌面客户端在“消息空间 > 等待接管”中提供“查看记忆依据”入口。客户端先按 `eventId` 读取执行轨迹，再用 `verifiedMemoryIds` 关联当前用户可见的长期记忆；用户可以继续进入来源证据弹窗，查看有限半径内的原始聊天窗口。若记忆已删除、过期或不属于当前账户，界面只显示该记录当前不可见，不会绕过服务端权限读取正文。
