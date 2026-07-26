# 上下文与记忆可信度设计

本文说明 Memo Echo 如何构造可信的聊天上下文，以及后续长期记忆应该遵守的边界。核心原则是：**原始事件是事实来源，模型摘要只是可失效的派生视图。**

## 1. 当前已落地的可信时间线

### 1.1 参与者身份

每条统一事件使用 `actorType` 标记真实来源：

| actorType | 含义 | 可否作为本人风格样本 | 事实权限 |
| --- | --- | --- | --- |
| `OWNER` | 账号主人亲自发送 | 可以 | 本人明确陈述 |
| `CONTACT` | 联系人或群成员发送 | 不可以 | 对方陈述，不能直接当成客观事实 |
| `AGENT` | Agent 代理发送 | 不可以 | 仅用于衔接上下文，不能证明本人做过或承诺过某事 |
| `SYSTEM` | 平台通知或系统事件 | 不可以 | 仅按系统事件处理 |

新事件优先相信 `actorType`。只有旧数据缺少该字段时，才回退到 `senderId`、`selfId` 和 `messageOrigin` 推断。

### 1.2 消息关联

一条 Agent 回复从生成到平台回流会携带以下标识：

- `clientMessageId`：Runtime 每次发送生成的客户端幂等 ID。
- `platformMessageId`：NapCat/QQ 返回的平台消息 ID。
- `correlationId`：指向触发本次回复的入站事件 ID。
- `sequence`：平台消息序号，用于同一秒内稳定排序。

Connector 在调用 NapCat 前登记出站消息，拿到 `message_id` 后建立精确映射。Webhook 回流时，优先按平台消息 ID 恢复 `AGENT` 身份和关联 ID。

### 1.3 排序和去重

Event Center 构建时间线时按以下顺序稳定排序：

1. 平台消息时间；
2. 平台序号；
3. Event Center 接收时间；
4. 事件 ID。

去重优先级为：

1. `clientMessageId`；
2. `platformMessageId`；
3. `correlationId + sequence + 规范化文本`；
4. 旧数据才使用短时间同文本回显兜底。

如果真实 Agent Webhook 已经按 `correlationId` 回流，Event Center 不再生成同一条合成回复。这样既保留平台真实消息，又不会在上下文中出现两遍。

## 2. Runtime 上下文窗口

Runtime 不维护第二份聊天数据库，始终从 Event Center 读取时间线。当前上下文分为两种窗口：

- **当前会话窗口**：即使未授权读取归档历史，也保留最近 30 分钟内的连续对话，避免逐条失忆。
- **授权历史窗口**：用户在会话设定中允许读取历史后，可扩大消息和字符预算；普通回复仍会隔离已经结束的旧会话。

用户明确提到“之前、上次、还记得”等回顾意图时，可以跨过会话间隔读取旧记录。Skill 多轮任务会扩大当前窗口，但不会绕过用户对归档历史的授权。

每条注入模型的消息还包含 `factAuthority`：

- `human_self`：本人亲自表达；
- `peer_statement`：对方表达；
- `agent_output`：历史 Agent 输出。

Prompt 编译器和审查链应明确告诉模型：`agent_output` 只能维持对话连续性，不能作为真实身份、付款、承诺、库存或现实状态的证据。

## 3. 上下文压力控制

需要，但不应默认把所有聊天先总结一遍。短对话直接使用原文最可靠；过早摘要会丢失否定词、说话双方和未完成条件。

Runtime 现已按会话设定中的消息数和字符预算自动判断是否需要压缩。未超限时继续使用完整原文；超限时采用“较早派生摘录 + 最近原文”的窗口，不会先把所有聊天交给模型总结。

当前压缩结果遵守以下约束：

- 最近消息保持原文、角色、时间、来源和附件解析结果，不参与摘要；
- 较早消息按 `OWNER`、`CONTACT`、`AGENT` 身份生成短摘录，不能把 Agent 代发内容写成用户事实；
- 派生项固定标记 `messageOrigin=DERIVED_SUMMARY`、`actorType=SYSTEM` 和 `factAuthority=derived_summary`；
- 派生项保存 `sourceEventIds`、来源数量、时间范围和摘要版本；
- SocialAgent 可用派生项维持话题连续性，但 ReviewAgent 和 ContextReviewAgent 会将其标为低权威，不能单独据此批准现实事实；
- 摘录不写入 Event Center，也不建立独立缓存。每轮均从原始事件重建，因此源消息删除、更正或身份修复后会自然失效。

后续只有在以下场景确实需要更强语义压缩时，才考虑增加模型摘要：

- 原始消息超过模型上下文预算的 60%；
- 当前连续会话超过 80 条消息；
- 存在多个已结束话题，且当前问题只与其中一部分相关；
- Skill 工作流需要保留较长周期状态。

当前上下文采用四层结构：

1. **最近原文**：最近 12 到 24 轮，绝不摘要；
2. **开放状态**：正在确认的问题、待付款、待交付、待回复；
3. **已验证事实**：带来源事件 ID 的结构化事实；
4. **较早派生摘录**：仅在超过窗口预算时注入，并始终标记为低权威。

若后续增加持久化语义摘要，仍必须保存 `sourceEventIds`、生成时间和版本，并在原始事件删除、更正或身份修复后明确失效。当前无缓存实现不承担这类一致性风险。

## 4. 长期记忆策略

长期记忆不能等同于“把聊天全部向量化”。建议使用候选、验证、固化三阶段：

### 4.1 候选记忆

从聊天中提取可能稳定的信息，但暂不直接进入高可信 Prompt。例如：称呼偏好、沟通习惯、长期项目、经常使用的地点。

每条候选至少记录：

- `subject`、`predicate`、`value`；
- `sourceEventIds`；
- `actorType` 和 `factAuthority`；
- `confidence`；
- `firstSeenAt`、`lastSeenAt`；
- `expiresAt`；
- `status`：`CANDIDATE`、`VERIFIED`、`REJECTED`、`EXPIRED`。

### 4.2 验证规则

- 本人明确陈述可获得较高初始可信度；
- 对方单方面陈述只能作为低可信候选；
- Agent 自己生成的内容不得提升可信度；
- 多次独立的本人陈述可以提升可信度；
- 身份、联系方式、付款、地址、承诺等高风险信息必须由用户确认；
- 临时状态必须设置过期时间，不能永久保存。

### 4.3 按会话类型隔离

同一个人在不同关系中的表达风格和事实用途不同。长期记忆至少区分：

- 全局本人资料；
- 平台级资料；
- 会话级资料；
- 场景级风格，例如朋友、工作、交易、群管理。

检索时优先使用当前会话记忆，其次使用当前场景记忆，最后才是全局记忆，避免把工作口吻带入朋友聊天。

### 4.4 已落地的持久化闭环

Event Center 现已使用 `memory_candidate` 表保存候选长期记忆，并提供以下状态流转：

```text
CANDIDATE -> VERIFIED
          -> REJECTED
VERIFIED  -> EXPIRED（超过 expiresAt 后查询时自动失效）
```

当前约束如下：

- Runtime 只能提交 `OWNER + human_self` 且带原始事件 ID 的候选，不能替用户确认事实；
- Runtime 查询只返回 `VERIFIED` 且未过期的记录，`CANDIDATE` 永远不会进入模型；
- 桌面用户可以创建、修改候选，执行确认、拒绝和删除；已确认记录不可静默改写，需要删除后重新建立候选；
- 所有增删改查均按 `userId` 隔离；会话查询再按 `GLOBAL`、`PLATFORM`、`SCENE`、`CONVERSATION` 作用域过滤；
- SocialAgent 与 ReviewAgent 使用同一组已确认记忆，提示中保留 `memoryId`，执行日志只记录 ID 而不复制记忆正文；
- Runtime 会把本次实际注入的记忆 ID 返回给 Event Center，并持久化到 `executionTrace.verifiedMemoryIds`；ID 会去重且最多保留 100 个，便于定位某次回复使用了哪些记忆；
- Event Center 暂时不可用时，Runtime 降级为无长期记忆继续回复，不把基础聊天链路一起阻断。

主要接口：

| 调用方 | 方法与路径 | 用途 |
| --- | --- | --- |
| Runtime | `POST /internal/memories/runtime/candidates` | 提交可信来源的候选记忆 |
| Runtime | `GET /internal/memories/runtime/verified` | 按当前事件作用域读取已确认记忆 |
| Desktop | `GET /internal/memories/candidates` | 浏览当前用户的候选与已确认记录 |
| Desktop | `POST /internal/memories/candidates` | 手工建立候选记忆 |
| Desktop | `PUT /internal/memories/candidates/{id}` | 修改尚未确认的候选 |
| Desktop | `POST /internal/memories/candidates/{id}/verify` | 用户确认事实 |
| Desktop | `POST /internal/memories/candidates/{id}/reject` | 用户拒绝候选并记录原因 |
| Desktop | `DELETE /internal/memories/candidates/{id}` | 删除记录 |

自动候选提取按会话默认关闭。用户可在会话设定中单独开启长期记忆候选授权；提取器只接受 `OWNER` 原始发言并异步生成 `CANDIDATE`，不会采集联系人消息、Agent 代发消息，也不会自动确认候选。

## 5. 防止“出戏”的执行顺序

回复链路应保持以下优先级：

1. 系统安全边界和审批策略；
2. 当前会话任务、业务规则和已验证资产；
3. 当前会话最近原文；
4. 当前会话 Profile 与 Skill；
5. 经来源校验的长期记忆；
6. 外部知识检索结果；
7. 模型常识。

审查 Agent 重点检查：

- 回答中的事实能否在 Prompt、Skill、知识库、聊天原文或已验证记忆中找到来源；
- 是否弄反“我”和“对方”；
- 是否把 Agent 历史输出当成本人事实；
- 是否跨会话泄露了不该共享的信息；
- 是否符合 QQ 短消息风格，而不是输出说明文或舞台动作。

缺少必要事实时，不应编造。低风险闲聊可以改写为自然的追问；涉及现实执行或高风险信息时进入人工接管。

## 6. 会话开放状态

Runtime 会在每次执行 Agent 前，根据可信时间线重建 `ConversationOpenState`。它与桌面端按需生成的“代理进度”完全分离，不能被 UI 摘要反向污染。

当前状态只回答三个问题：

- 当前轮到 `AGENT`、`OWNER` 还是 `PEER`；
- 我方最后一次回复之后，有哪些对方消息仍未回应；
- Event Center 是否已经明确标记某条消息需要人工确认。

状态包含 `status`、`responsibleParty`、`sourceEventIds` 和 `pendingItems`。每个待处理项保留原始事件 ID、参与者、时间和原文，SocialAgent 与 ReviewAgent 使用同一份状态证据。状态服务不会通过“付款”“交付”等关键词猜测业务进度；后续需要业务状态时，应由专门的结构化工作流依据工具结果更新。

状态目前按需重建而不单独持久化。这样可以避免数据库中的派生状态与原始事件发生漂移，也便于身份修复或事件纠错后立即得到新结果。

## 7. 后续实现顺序

1. 已完成客户端候选记忆确认、拒绝、删除、来源追溯和单次执行使用记录审计；
2. 已完成会话级长期记忆授权，以及仅从 `OWNER` 原始发言生成 `CANDIDATE` 的异步提取器；
3. 已完成基于消息数和字符预算的上下文压力控制，并为较早派生摘录保留来源与低权威标记；
4. 下一步可增加上下文窗口审计视图，展示本轮保留了哪些原文、压缩了哪些来源；
5. 最后再接入向量检索，向量只负责召回，不能决定事实真伪。
