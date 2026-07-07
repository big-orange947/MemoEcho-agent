# 协议设计

本文档描述 Java 微服务层与 Python Agent Runtime 之间的核心通信契约。

## 1. 通信原则

### 1.1 分层边界

- Java 层负责接入、事件标准化、业务存储、消息回传和基础设施
- Python 层负责路由、规划、Agent 调度和工具编排
- Python 不能直接操作数据库
- Java 不直接承担 Agent 决策逻辑

### 1.2 调用方式

第一阶段建议采用两种通信方式并存：

1. 同步 HTTP
   适合实时消息处理、即时回复、简单查询
2. 异步 MQ
   适合文件分析、批量摘要、多步骤规划

当前仓库先定义同步协议，异步协议后续补充。

## 2. Python Runtime 对外接口

### 2.1 处理统一事件

`POST /v1/events/handle`

用途：

- 接收 Java 层已经标准化后的 `UnifiedEvent`
- 完成 route 判断、执行规划、Agent 调度和结果聚合
- 返回结构化结果和最终回复草稿

请求体：

```json
{
  "eventId": "evt-001",
  "platform": "qq",
  "scene": "life",
  "eventType": "message",
  "chatType": "group",
  "chatId": "138178088",
  "sender": {
    "id": "2597164807",
    "name": "freeze",
    "role": "owner"
  },
  "text": "今天下午14:00在A01-N105举办分享会",
  "attachments": [],
  "mentions": [],
  "timestamp": "2026-07-06T10:00:00+08:00",
  "rawPayload": {}
}
```

响应体：

```json
{
  "execution_id": "uuid",
  "status": "success",
  "route": "schedule_extract",
  "summary": "Plan executed in single_agent mode with 1 step(s).",
  "results": [
    {
      "task_id": "uuid",
      "agent": "schedule",
      "status": "success",
      "structured_result": {
        "title": "今天下午14:00在A01-N105举办分享会"
      },
      "reply_draft": "已提取出一条候选日程。",
      "tool_calls": [],
      "next_actions": [],
      "need_confirmation": false
    }
  ],
  "final_reply": "已提取出一条候选日程。",
  "write_back_actions": []
}
```

## 3. Java -> Python 输入契约

### 3.1 Java 层必须保证

在把事件发给 Python 前，Java 层必须已经完成：

1. 平台身份识别
2. 事件去重标识生成
3. 原始消息到统一协议的字段映射
4. 基础安全过滤
5. 附件元数据补齐

### 3.2 Java 层不应提前做的事

- 不做复杂意图判断
- 不做多 Agent 执行规划
- 不把业务逻辑散落在 connector 里

## 4. Python -> Java 写回契约

Python Runtime 不直接落库，必须通过 Tool 层调用 Java 服务。

第一阶段建议约定以下写回动作：

- `create_schedule`
- `create_task`
- `save_summary`
- `send_group_message`
- `send_private_message`

推荐方式：

1. Python 内部 Tool 调用 Java HTTP API
2. Tool 返回统一执行结果
3. Orchestrator 决定是否把结果纳入最终回复

## 5. Route 约定

当前支持以下 route：

- `chat_summary`
- `schedule_extract`
- `task_plan`
- `file_analysis`
- `social_reply`
- `group_ops`

说明：

- route 由 Router 输出
- Planner 根据 route 生成执行计划
- 后续允许新增 route，但必须先更新共享协议和文档

## 6. 执行模式约定

第一阶段建议保留以下执行模式：

- `suggest_only`
  只生成建议，不直接执行高风险动作
- `safe_auto_execute`
  允许低风险写操作自动执行
- `confirm_required`
  需要人工确认后再执行

## 7. 错误处理约定

### 7.1 Python Runtime 错误返回

建议统一返回：

```json
{
  "execution_id": "uuid",
  "status": "failed",
  "route": "file_analysis",
  "summary": "Execution failed during file analysis.",
  "results": [],
  "final_reply": "本次处理失败，请稍后重试。",
  "write_back_actions": []
}
```

### 7.2 回退策略

- 向量检索失败时，回退结构化查询
- 文件解析失败时，不阻断纯文本消息分析
- 辅助 Agent 失败时，主任务尽量继续

## 8. 第一阶段推荐链路

```text
NapCat -> connector-service -> UnifiedEvent
       -> POST /v1/events/handle
       -> OrchestratorResult
       -> notification-service 回传
```

