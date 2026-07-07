# 工作流说明

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
