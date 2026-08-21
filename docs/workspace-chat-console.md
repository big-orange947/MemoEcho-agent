# 主控台对话式工作区设计

> 文档状态：设计定稿，P1 按此实施。本文定义"聊天式主控台 + 工作区管理"的目标形态、数据模型、后端 API、前端结构与分期，供 P1 实现与后续 P2/P3 扩展共用。

## 1. 背景与问题

当前主控台（`desktop-client/src/App.tsx` 的 Dashboard）是"单次命令"形态：

- 一个 textarea + 能力快捷入口，提交后调用 `POST /internal/workspace/commands`。
- 页面只展示**最近一次** `commandResult` 区块，委托任务以卡片网格平铺在下方。
- 后端为**同步**链路：Event Center 鉴权后把事件派发给 Python Runtime，Runtime 跑完整条委托链路后一次性返回，慢时 90 秒以上，前端只能干等。

缺陷：

1. **没有对话**：每次命令是孤立请求，无法多轮追问、无法引用前文。
2. **没有工作区**：任务列表平铺，无法按会话/项目组织，无法切换和恢复上下文。
3. **没有流式**：模型慢时无中间反馈，体验等同于"卡死"。
4. **展示割裂**：委托任务卡片与对话输入分离，用户看不到"这条命令产生了哪个任务、当前执行到哪一步"。

目标形态对标 ChatGPT / Codex 的聊天框 + 工作区管理。**委托任务状态机、工作流 DAG、L0 当前事件、租约与权限边界全部保留不动**，只在其上增加"对话容器"与新的展示层。

## 2. 目标形态

```text
┌────────────┬───────────────────────────────────────────────┬────────────┐
│ 工作区侧栏  │  聊天流                                        │  上下文面板  │
│            │                                               │            │
│ + 新建对话  │  [用户] 帮我和 km 约明天晚上打游戏              │ 联系人      │
│            │  [Agent] 好，我先问 km 几点有空                 │  km / 小号  │
│ 对话 A      │    ┌──────────────────────────┐              │ 会话设定    │
│ 对话 B      │    │ 委托任务（运行中）           │              │ 记忆        │
│ 对话 C      │    │ step1 问 km …  ✅ 已发     │              │ 模型        │
│ 对话 D      │    │ step2 转告小号 …  ⏳ 等待  │              │            │
│            │    └──────────────────────────┘              │            │
│ (归档)      │  [Agent] km 回复 9 点，已转告小号 ✅           │            │
│            │  [用户] 那后天呢？                            │            │
│            │  ▸ 输入框（Enter 发送 / Shift+Enter 换行）      │            │
└────────────┴───────────────────────────────────────────────┴────────────┘
```

## 3. 核心概念与边界

| 概念 | 定义 | 谁创建 | 生命周期 |
|---|---|---|---|
| **工作区对话（Thread）** | 用户与 Agent 之间的对话容器，按主题组织消息 | 用户点击"新建对话"或首次输入时 | 常驻，可归档 |
| **对话消息（Thread Message）** | 一条用户输入或一条 Agent 产出（文本/任务引用/错误） | 用户提交 / Runtime 回填 | 追加，只读 |
| **委托任务（DelegatedTask）** | 一条命令编译出的**执行单元**，绑定一个会话 | Runtime 编译 | 执行完即终态 |
| **父工作流（Workflow）** | 一条命令的完整执行计划与共享事实 | Runtime 规划 | 步骤全完成后终态 |

边界约定：

- **Thread 是对话，Task 是执行**。一条 Thread 消息可产生 0..N 个 Task/Workflow，也可能只是普通问答（不产生任务）。
- **Task/Workflow 不依赖 Thread**：二者照常独立运行（QQ 侧、群聊侧不受主控台线程影响），Thread 只负责把它们的进展**内嵌展示**进对话流。
- **权限不新开**：所有命令仍从 `internal/workspace/commands` 入口经 Java 鉴权；Thread API 同样只接受 JWT 或本地联调的 legacy 用户头。

## 4. 数据模型（MySQL，`memo_echo_event_center`）

### 4.1 `workspace_thread`

| 列 | 类型 | 说明 |
|---|---|---|
| id | CHAR(36) PK | 线程 ID |
| user_id | VARCHAR(64) | 归属用户 |
| title | VARCHAR(200) | 标题，P1 可手动命名，P3 模型自动生成 |
| pinned | TINYINT(1) | 置顶 |
| archived | TINYINT(1) | 归档（软删） |
| created_at / updated_at | DATETIME | |

### 4.2 `workspace_thread_message`

| 列 | 类型 | 说明 |
|---|---|---|
| id | CHAR(36) PK | 消息 ID |
| thread_id | CHAR(36) | 所属线程 |
| user_id | VARCHAR(64) | 归属用户 |
| role | VARCHAR(16) | `user` / `agent` / `system` |
| content | TEXT | 文本正文 |
| status | VARCHAR(24) | `pending` / `streaming` / `done` / `error` / `needs_confirmation` |
| execution_id | VARCHAR(128) | 关联主控台命令 executionId（= commandId） |
| task_id | VARCHAR(36) | 可选：关联的委托任务 |
| workflow_id | VARCHAR(36) | 可选：关联的父工作流 |
| result_json | JSON/TEXT | Agent 结构化结果（results、route、summary） |
| created_at | DATETIME | |

说明：

- `execution_id` 直接复用现有命令的 `commandId`（Java 侧已把 `commandId` 同时作为 `executionId`），保证对话消息与委托链路日志可同键检索。
- `task_id` / `workflow_id` 让前端能把任务卡片内嵌到对应消息下。
- P1 只落库，不做线程级上下文注入（P3）。

## 5. 后端 API

### 5.1 新增（P1）

```text
POST   /internal/workspace/threads                        新建线程（标题可选）
GET    /internal/workspace/threads                        列出线程（置顶/归档过滤）
PATCH  /internal/workspace/threads/{id}                   重命名 / 置顶 / 归档
GET    /internal/workspace/threads/{id}/messages          分页读取消息
POST   /internal/workspace/threads/{id}/messages          发送一条用户消息
GET    /internal/workspace/threads/{id}/messages/{mid}    读取单条消息（含结果）
```

`POST .../messages` 的语义：

1. Java 校验线程归属与用户鉴权。
2. 创建 `role=user` 的消息记录。
3. **沿用现有同步派发**（P1 不做异步）：调用 `WorkspaceCommandApplicationService` 同款链路，把事件派发给 Python Runtime，收到结果后：
   - 写入 `role=agent` 消息（content=finalReply，result_json=结构化结果，task_id/workflow_id 从结果中提取）；
   - 返回 `{ userMessageId, agentMessageId, response }`。
4. 前端在请求返回前显示 pending 状态行，返回后刷新为 `done`。

> P1 故意保留同步：优先打通"多线程 + 消息入流 + 任务内嵌"骨架。流式在 P2 引入，届时本接口语义不变，只是把返回改成 `202 + SSE`。

### 5.2 复用（不改）

- `POST /internal/workspace/commands`：现有命令执行入口保持原样，新线程接口在内部复用其 Service。
- `GET /internal/workspace/commands/delegated/{taskId}`：前端刷新任务卡状态。
- `GET /internal/workspace/commands/delegated-workflows/{id}/runtime`：前端刷新工作流步骤卡状态。
- `WorkspaceEventStreamService`（SSE 基建）：P2 扩展为按 `threadId` 或 `messageId` 订阅，现在不动。

### 5.3 P2（已实现）

```text
GET /internal/workspace/threads/{id}/messages/{mid}/stream   SSE：阶段事件流
```

- `POST .../messages` 改为**异步**：立即落库 user + streaming agent 消息并返回（202 语义），命令在后台线程执行；`commandId` 由线程服务预生成并注入命令链路，保证执行期间可按 `source_execution_id` 轮询进度。
- 进度来源：后台按 commandId 轮询 `delegated_task` / `delegated_workflow` / `delegated_workflow_step_dispatch`，变化时推送 `progress` 事件（任务列表 + 工作流状态）。
- 事件类型：`connected`（快照）/ `processing` / `progress` / `done`（含终态 agent 消息）/ `error`。
- 前端用 `fetch + ReadableStream` 解析 SSE（EventSource 无法携带 Authorization 头）；流中断时回退到 `GET .../messages/{mid}` 读取服务端终态。
- 超时保护：`streaming` 消息超过 15 分钟未完成时在列表读取时自动标记为 error。
- **范围边界**：本阶段是"阶段事件流"（任务创建、步骤激活、状态变化），不是逐 token 文本流。逐 token 需要 Python 侧把 LangGraph 单次 `ainvoke` 拆成可流式出口，属后续阶段。

## 6. 前端结构

### 6.1 布局

```text
<App>
 └─ <WorkspaceLayout>                     # 登录后主框架
     ├─ <ThreadSidebar>                  # 左：新建/列表/归档
     │    ├─ <NewThreadButton>
     │    ├─ <ThreadListItem> * n        # 标题、更新时间、置顶/归档操作
     │    └─ <ArchiveSection>
     ├─ <ChatView>                       # 中：聊天流 + 输入框
     │    ├─ <MessageList>
     │    │    └─ <MessageBubble> * n    # user/agent/system，流式/错误态
     │    │         └─ <TaskCardInline>  # 内嵌委托任务/工作流卡片（复用现有卡）
     │    └─ <Composer>                  # 多行输入框 + 发送
     └─ <ContextPanel>                   # 右：联系人/设定/记忆（P3 充实）
```

### 6.2 交互契约

- 新建对话：点击"+" → 创建空线程 → 聚焦输入框。
- 发送：`Enter` 发送、`Shift+Enter` 换行；发送后追加 user 气泡 + pending agent 气泡。
- 任务内嵌：agent 消息若带 `task_id`/`workflow_id`，在消息下方渲染可展开卡片（复用现有 `DelegatedTaskCard` 视觉与操作）。
- 状态变体：`pending`（等待）、`error`（显示失败原因）、`needs_confirmation`（显示确认按钮）、`done`。
- 空态：无消息时显示能力快捷入口（复用现有 capabilities）。
- 错误态：线程/消息加载失败显示重试；接口 4xx 显示服务端文案。

### 6.3 工程约束

- 现有 `App.tsx`（约 3400 行）拆分为上述组件目录 `src/components/workspace/`；**迁移期间不破坏现有登录、配置、收件箱视图**——新布局作为 Dashboard 的替代视图接入，旧视图可先保留一个开关。
- 状态管理：P1 用 React state + 简单 context（线程列表、当前线程消息、loading/error），不引入额外库。
- API 层：`src/api/client.ts` 新增线程函数，沿用现有 `StoredCredential` 与 JWT 头。

### P3（已实现：多轮追问）

- **线程上下文透传**：`WorkspaceCommandRequest` 增加可选 `threadHistory`（最近 8 条 user/agent 消息，正序、过滤失败/空消息），随命令事件 `rawPayload.threadHistory` 透传 Runtime。
- **Router 上下文推断**：命令本身无联系人时，模型可依据 threadContext 推断上一轮委托对象（"那后天呢？"→ km）。
- **Planner 上下文补全**：规划器依据 threadContext 补全联系人/日期/事项，instruction 必须自包含（实机验证：追问生成"询问km后天安排"工作流，指令引用前文"明天你已回复9点，后天是否也9点"）。
- **范围边界**：线程历史仅用于编译期上下文；线程绑定模型/会话设定、线程级长期记忆仍在后续。

## 7. 分期

### P1（本阶段，按此实现）

- 后端：`workspace_thread` / `workspace_thread_message` 表 + 5 个线程/消息接口 + 复用命令链路写回 agent 消息。
- 前端：`WorkspaceLayout` + 线程侧栏 + 聊天流 + 输入框 + 任务卡片内嵌（同步返回）。
- 验证：Java 单测（线程 CRUD、消息写回、归属校验）；前端 `tsc --noEmit`；手工走通"新建线程 → 发命令 → 任务卡片出现在消息下 → 切换线程 → 恢复历史"。

### P2

- SSE 流式输出 + 工具状态实时展示；`POST .../messages` 改异步 + 202。

### P3

- 线程上下文注入 Runtime（多轮追问"那后天呢？"能正确引用前文）；
- 会话标题自动生成、全文搜索、线程绑定模型/会话设定。

## 8. 风险与决策记录

| 事项 | 决策 |
|---|---|
| Thread 与 Task 的关系 | Thread 是对话容器，Task/Workflow 是执行单元，Task 不依赖 Thread 存在 |
| P1 是否流式 | 否。优先骨架与多线程；同步返回最长可 90s+，前端用 pending 态兜底 |
| 多轮上下文 | P3 再做。P1 的追问只作为新命令处理，不复用前文（避免语义承诺） |
| 与现有 Dashboard 关系 | 新视图替换 Dashboard 的委托入口；旧组件（登录/配置/收件箱）不动 |
| 权限 | 线程接口全部经 `userContextResolver.resolve`（JWT 或 legacy 头） |
| 迁移 | 表新增走 Flyway V15；schema.sql 同步；服务重启不破坏现有数据 |

## 9. P1 任务拆分

| 任务 | 范围 | 产出 |
|---|---|---|
| T1 | Java：Flyway V15 建表 + Repository + `WorkspaceThreadApplicationService` | 线程/消息 CRUD Service + 单测 |
| T2 | Java：`InternalWorkspaceThreadController`（5 个接口）+ 复用命令链路写回 | 控制器 + 集成测试 |
| T3 | 前端：`src/api/client.ts` 线程函数 + `WorkspaceLayout`/侧栏/聊天流/输入框/任务内嵌 | 组件 + `tsc` 通过 |
| T4 | 验证：全量 Java/Python 测试 + 手工闭环（新建→发命令→任务卡→切线程→恢复） | 验收报告 |

T1/T2 同属 Java 后端，串行或同一 agent；T3 前端依赖 T2 的接口契约（先定契约再并行）。
