# 工作流说明

## 0. 主控台委托任务工作流

主控台输入的自然语言命令先由 Java 侧做鉴权、审计和事件落库，再进入 Python Runtime。任务识别、联系人选择、任务创建和执行计划都由 Agent 侧完成。

```text
Desktop 主控台命令
-> event-center-service 鉴权、审计、落库
-> Python Runtime Workspace Command Handler
-> Router Agent 选择目标会话和任务类型
-> Delegated Task Workflow 创建任务契约
-> ReAct 执行图调用工具
-> send_qq_message / update_delegated_task / finish_delegated_task
-> event-center-service 保存进度和审计记录
```

这个链路避免在前端或 Java 侧用正则硬拆命令。联系人、多目标、私聊和群聊判断都交给 Router Agent，并由工具权限和审查 Agent 做边界控制。

## 1. 消息摘要工作流

```text
NapCat/Webhook
-> qq-napcat-connector
-> UnifiedEvent
-> event-center-service
-> Router: chat_summary
-> Inbox Agent
-> get_recent_messages tool
-> summary result
-> send reply
```

## 2. 日程提取工作流

```text
NapCat/Webhook
-> qq-napcat-connector
-> UnifiedEvent
-> event-center-service
-> Router: schedule_extract
-> Schedule Agent
-> normalize time and location
-> create_schedule tool
-> send confirmation reply
```

## 3. 附件驱动的工作规划工作流

```text
NapCat/Webhook
-> qq-napcat-connector
-> UnifiedEvent with attachment
-> event-center-service
-> Router: file_analysis
-> File Agent
-> extract_file_text tool
-> Work Agent
-> create_task tool
-> send daily plan reply
```

## 4. 多步骤编排工作流

示例请求：

`帮我总结最近群通知，并整理成今天的安排。`

执行计划：

1. Inbox Agent 总结近期消息
2. 如果存在附件，则由 File Agent 解析附件
3. Schedule Agent 提取日程项
4. Work Agent 生成今日计划
5. Notification Tool 返回结果
