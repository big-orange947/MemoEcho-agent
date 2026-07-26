# 会话设定集

会话设定集是 `Memo Echo Agent` 的一层运行时策略配置。

涉及收款码、卡密、交付文件等敏感内容时，不要把正文写进 `systemPrompt`。应使用
[安全资产库](secure-assets.md) 保存正文，并在 Profile 2.0 中只绑定资产引用和使用条件。

它不直接替代某个具体 Agent，而是在消息进入 Python runtime 之前，先决定：

- 这条消息属于哪个会话范围
- 是否命中某条特定设定
- 命中后优先走哪个 route
- 是否允许自动回复
- 是否必须人工确认
- 是否加载某个 skill / prompt / 模型配置
- 当前会话允许暴露哪些工具

## 当前实现位置

- Java 配置与匹配服务：
  [InternalConversationProfileController.java](D:/project/memo_echo-agent/services-java/event-center-service/src/main/java/com/memoecho/eventcenter/controller/InternalConversationProfileController.java)
- Python runtime 接入：
  [service.py](D:/project/memo_echo-agent/agent-runtime-python/app/orchestrator/service.py)

## 当前支持的字段

| 字段 | 作用 |
| --- | --- |
| `name` | 设定名称 |
| `description` | 设定说明 |
| `enabled` | 是否启用 |
| `platform` | 平台范围，例如 `qq` |
| `accountId` | 当前接入账号 id，用于区分同平台下的不同账号 |
| `scene` | 场景范围，例如 `life` / `work` |
| `chatType` | 会话类型，`private` / `group` |
| `chatIds` | 生效会话 id 列表，空表示全部 |
| `targetUserIds` | 生效发送者 id 列表，空表示全部 |
| `supportedRoutes` | 允许命中的 route 列表，空表示不限 |
| `triggerMode` | 触发条件 |
| `triggerKeywords` | 关键词触发列表 |
| `personaMode` | 人设模式，决定 `systemPrompt` / `skillReferences` 如何注入 |
| `systemPrompt` | 会话级人格或行为提示词 |
| `skillReference` | 单个 skill 引用，兼容旧字段 |
| `skillReferences` | 多个 skill 引用列表，建议前端使用这个字段 |
| `modelProfileId` | 当前会话绑定的模型配置 id |
| `preferredRoute` | 命中后优先走的 runtime 路由 |
| `replyMode` | 自动回复模式 |
| `replyDelaySecondsMin` | 自动回复最小延迟秒数 |
| `replyDelaySecondsMax` | 自动回复最大延迟秒数 |
| `allowedTools` | 当前会话允许暴露的工具白名单 |
| `requireHumanConfirmation` | 是否要求人工确认 |
| `priority` | 多条设定冲突时的优先级 |

## personaMode 语义

| 值 | 当前行为 |
| --- | --- |
| `NONE` | 不注入会话级 prompt，也不注入 skill prompt |
| `PROMPT` | 只使用 `systemPrompt` |
| `SKILL` | 优先使用 `skillReferences`，`systemPrompt` 作为补充 |
| 空值 | 兼容旧数据，按“有 prompt 用 prompt，有 skill 用 skill”的混合模式处理 |

这意味着：

- 想手写一个“像谁说话”的人格，使用 `PROMPT`
- 想挂载某个可复用人格 skill，使用 `SKILL`
- 想临时关闭会话人格干预，使用 `NONE`

## 多会话设定如何区分

当前已经支持“同一个用户有多条不同会话设定”。

运行时会按下面顺序匹配：

1. 先按范围过滤
   - `platform`
   - `accountId`
   - `scene`
   - `chatType`
   - `chatIds`
   - `targetUserIds`
   - `supportedRoutes`
2. 再按触发条件判断是否激活
   - `triggerMode`
   - `triggerKeywords`
   - `atSelf`
   - `senderRole`
3. 如果多条都命中，则按“更具体优先 + `priority` 更高优先”选出最终 profile

你可以这样配：

- 会话 A：某个私聊，挂“老师沟通风格” skill
- 会话 B：另一个私聊，挂“朋友口吻” prompt
- 会话 C：某个工作群，只允许 `work_agent`
- 会话 D：某个生活群，只允许 `social_reply`

这些设定之间不会互相覆盖，最终由匹配规则选出最合适的一条。

## 当前支持的触发模式

| 值 | 说明 |
| --- | --- |
| `ALWAYS` | 命中范围即激活 |
| `AT_SELF_ONLY` | 只有明确 @ 机器人时激活 |
| `KEYWORD_ONLY` | 只有正文命中关键词时激活 |
| `AT_SELF_OR_KEYWORD` | 满足 @ 或关键词任一即可 |
| `ADMIN_OR_AT_SELF` | 发送者是群主/管理员，或明确 @ 机器人 |
| `MANUAL_ONLY` | 永不自动激活，仅用于前端展示或手动选择 |

## 当前支持的回复模式

| 值 | 说明 |
| --- | --- |
| `AUTO_REPLY` | 命中且激活时允许自动回复 |
| `DRAFT_ONLY` | 只生成草稿，不自动发送 |
| `SILENT` | 不自动回复 |

## 当前 skill 加载机制

当前这版已经不再只是保存 `skillReference` 字符串，而是已经有一层真实生效的运行时解析逻辑。

### 运行顺序

1. `event-center-service` 返回命中的 `skillReference` / `skillReferences`
2. Python runtime 在进入具体 Agent 之前，先交给 `SkillResolver` 解析
3. 解析成功的 skill 会写入 `resolved_skills`
4. `resolved_skills` 会继续影响：
   - Agent 的 `system prompt`
   - 当前会话允许暴露的工具白名单
   - Agent 输出中的 `resolvedSkills` 字段，供前端直接展示

### 当前支持的引用方式

| 引用写法 | 当前状态 |
| --- | --- |
| `skills/personas/reliable-assistant` | 已支持，本地目录引用 |
| `local://personas/reliable-assistant` | 已支持，本地别名引用 |
| `skills/.../skill.json` | 已支持，直接指向描述文件 |
| `github://owner/repo/path` | 当前仅标记为未解析，不执行远程加载 |

### 当前本地 skill 的结构

当前本地 skill 使用 `skill.json` 作为描述文件，核心字段包括：

| 字段 | 作用 |
| --- | --- |
| `id` | skill 唯一标识 |
| `name` | skill 名称 |
| `type` | skill 类型，例如 `persona` / `work` / `schedule` |
| `description` | skill 描述 |
| `applicableRoutes` | 允许作用的 route 列表 |
| `promptFragments.system` | 注入给 Agent 的系统约束 |
| `toolPolicy.allow` | 该 skill 允许使用的工具 |
| `modelHints` | 预留给后续模型参数融合的提示 |

### 当前注入规则

当会话命中 profile 后，运行时会这样处理：

1. 先按 `supportedRoutes` 和当前 route 过滤 skill
2. 再把 skill 的 `promptFragments.system` 拼进 Agent 的 system prompt
3. 如果 profile 同时还有 `systemPrompt`：
   - `personaMode=SKILL` 时，skill 约束优先，`systemPrompt` 作为补充
   - `personaMode=PROMPT` 时，只用 `systemPrompt`
   - `personaMode=NONE` 时，两边都不注入
4. 如果 profile 配了 `allowedTools`，再和 skill 的 `toolPolicy.allow` 做交集

这意味着当前已经能做到：

- 同一个会话挂不同人格 skill
- 不同会话限制不同工具
- skill 只在指定 route 生效
- Agent 输出里回带 `resolvedSkills`

### 为什么当前不直接执行 GitHub skill

这是刻意收敛的第一阶段实现，不是遗漏。

当前只做“描述符加载”，不做“远程代码执行”，主要是为了：

- 先把会话配置、prompt 拼装、工具约束这条主链路跑通
- 避免过早引入远程仓库拉取、缓存、签名校验、沙箱执行
- 让前端先拿到“这个会话实际加载了什么人格 / 限制了什么工具”的可解释结果

所以现在的策略是：

- 本地 skill：立即可用
- GitHub skill：先保留引用，并写入 `unresolved_skill_references`
- 后续再补 skill 安装、缓存、审核、启停

## Python runtime 当前联动行为

当前接入方式如下：

1. runtime 收到事件
2. 先做一次预路由，得到初始 route
3. 把 `event + 初始 route` 一起发给 `event-center-service` 的 `/match`
4. 如果匹配到激活中的设定，并且 `preferredRoute` 非空，则覆盖默认路由
5. 如果 profile 配置了 `modelProfileId`，则继续解析当前会话绑定的模型配置
6. 如果 profile 配置了 `skillReferences`，则继续解析本地 skill 描述
7. 如果 profile 配置了 `allowedTools`，则运行时只给当前 Agent 暴露白名单工具
8. 如果 profile 配置了 `replyDelaySecondsMin/Max`，则自动回复前会生成稳定延迟
9. 如果 `requireHumanConfirmation=true`，则把执行模式切到 `confirm_required`
10. 如果 `replyMode=DRAFT_ONLY` 或 `SILENT`，则不自动回写到 QQ

也就是说，这一版已经支持：

- 某些私聊只生成草稿
- 某些群聊只有 @ 你才自动回复
- 某些工作群命中通知关键词后要求人工确认
- 某个指定会话绑定 skill / prompt / 模型配置
- 某个指定会话只允许使用有限工具
- 某个指定会话的自动回复可以带延迟窗口
- `personaMode` 已经真正影响 prompt 注入方式

## 当前回写策略说明

当前 `write_back_actions` 会显式记录策略执行结果，便于前端直接展示：

- `qq_write_back_sent:ok`
- `qq_write_back_delayed:2s`
- `qq_write_back_skipped:draft_only`
- `qq_write_back_skipped:silent`
- `qq_write_back_skipped:confirm_required`
- `qq_write_back_skipped:profile_inactive`

这部分已经不再是“静默跳过”，而是会把策略命中结果直接暴露给上层 UI。

## 默认种子数据

`event-center-service` 启动后，如果仓储为空，会自动写入 3 条演示设定：

1. `QQ 私聊默认草稿模式`
   - 私聊
   - 永远命中
   - 走 `social_reply`
   - `replyMode=DRAFT_ONLY`
2. `群聊 @我 时即时响应`
   - 群聊
   - 仅当明确 @ 机器人
   - 走 `social_reply`
   - `replyMode=AUTO_REPLY`
3. `工作群通知监控模式`
   - `scene=work`
   - 群聊
   - 命中通知类关键词或 @ 机器人
   - 走 `chat_summary`
   - `requireHumanConfirmation=true`

如果不想自动注入，配置：

```yaml
event-center:
  conversation-profiles:
    seed-defaults: false
```

## 接口列表

### 1. 查询全部设定

```http
GET /internal/conversation-profiles
```

### 2. 查询单条设定

```http
GET /internal/conversation-profiles/{id}
```

### 3. 创建设定

```http
POST /internal/conversation-profiles
Content-Type: application/json
```

### 4. 更新设定

```http
PUT /internal/conversation-profiles/{id}
Content-Type: application/json
```

### 5. 删除设定

```http
DELETE /internal/conversation-profiles/{id}
```

### 6. 匹配设定

```http
POST /internal/conversation-profiles/match
Content-Type: application/json
```

## 匹配接口示例

请求：

```json
{
  "platform": "qq",
  "accountId": "3969785168",
  "scene": "life",
  "chatType": "private",
  "chatId": "2597164807",
  "senderId": "10001",
  "senderRole": "",
  "route": "social_reply",
  "text": "紧急，麻烦马上回复我",
  "atSelf": false
}
```

返回：

```json
{
  "matched": true,
  "active": true,
  "reason": "命中会话范围且满足触发条件",
  "profile": {
    "id": "profile-001",
    "name": "重要联系人自动回",
    "accountId": "3969785168",
    "preferredRoute": "social_reply",
    "replyMode": "AUTO_REPLY",
    "skillReferences": ["skills/personas/reliable-assistant"],
    "modelProfileId": "model-profile-001",
    "allowedTools": ["send_qq_message"]
  }
}
```

## Conversation Profile 2.0

Profile 2.0 将原来集中在 `systemPrompt` 的身份、关系和业务信息拆成结构化上下文。自由文本人格仍保留，用于兼容旧设定和补充难以结构化的表达要求，但不再承担工具授权和业务状态管理。

### 字段职责

| 模块 | API 字段 | 作用 |
| --- | --- | --- |
| 我的身份 | `profileContext.identity` | 代表对象、角色、说话风格和禁用表达 |
| 对方资料 | `profileContext.counterparty` | 对方身份、关系、称呼、已知事实、可信度和沟通偏好 |
| 对话背景 | `profileContext.background` | 会话起因、之前发生的事情和当前进展 |
| 对话任务 | `profileContext.task` | 最终目标、成功条件、截止时间和禁止事项 |
| 业务规则 | `profileContext.businessRules` | 报价、最低价、退款、交付条件和硬约束 |
| 可用资产 | `profileContext.assets` | 只保存资产 ID、类型、名称和使用条件，不保存资产正文 |
| 知识来源 | `knowledgeBaseSources`、`skillReferences` | 继续使用原有知识库和 Skill 解析链路 |
| 工具权限 | `allowedTools` | 工具白名单；任务和资产不能自动扩大权限 |
| 审批策略 | `reviewMode`、`requireHumanConfirmation` | 决定自动纠偏还是转人工，以及动作是否确认 |
| 记忆策略 | `privateHistoryEnabled`、`historyTrainingEnabled`、`profileContext.memoryPolicy` | 分别控制历史读取、个人 Skill 样本授权和长期记忆候选提取 |

### 创建示例

下面只展示 2.0 相关字段。实际请求仍需包含会话范围、触发模式和回复策略等既有字段。

```json
{
  "name": "网易云会员交易",
  "systemPrompt": "像本人一样简短自然地聊天",
  "allowedTools": ["send_qq_message", "send_asset"],
  "reviewMode": "STRICT_HANDOFF",
  "requireHumanConfirmation": true,
  "knowledgeBaseSources": ["C:/memo-echo/knowledge/product-rules.md"],
  "profileContext": {
    "version": 2,
    "identity": {
      "representedPerson": "freeze",
      "role": "网易云会员卖家",
      "speakingStyle": "短句、自然、不使用客服腔",
      "forbiddenExpressions": ["我先确认一下", "我会跟进"]
    },
    "counterparty": {
      "name": "小号",
      "identity": "潜在买家",
      "relationship": "首次交易",
      "preferredAddress": "你",
      "knownFacts": ["想购买一个月会员"],
      "trustLevel": "MEDIUM",
      "communicationPreference": "直接沟通价格和交付"
    },
    "background": {
      "origin": "对方询问网易云会员",
      "previousEvents": "已告知月卡和年卡价格",
      "currentProgress": "等待对方确认套餐"
    },
    "task": {
      "objective": "在规则范围内完成交易",
      "successCriteria": ["确认套餐", "确认付款", "完成交付"],
      "deadline": "2026-07-20T20:00:00+08:00",
      "prohibitedActions": ["不得虚构联系方式", "不得在未到账时交付"]
    },
    "businessRules": {
      "pricingPolicy": "月卡 15 元，年卡 50 元",
      "minimumPrice": "15 元",
      "refundPolicy": "未交付可退款，交付后按商品规则处理",
      "deliveryConditions": "确认到账后才能交付",
      "hardConstraints": ["不得低于最低价"]
    },
    "memoryPolicy": {
      "extractionEnabled": true
    },
    "assets": [
      {
        "assetId": "asset-payment-001",
        "type": "PAYMENT_QR",
        "name": "微信收款码",
        "description": "当前账号的微信收款码",
        "usageCondition": "买家明确确认购买后，经审批发送"
      }
    ]
  }
}
```

### Prompt 编译顺序

Python Runtime 的 `ConversationPromptCompiler` 按以下顺序拼接系统上下文：

1. 执行边界、工具白名单和审批模式。
2. 我的身份和对方资料。
3. 对话背景和对话任务。
4. 业务规则和资产引用。
5. 旧版自由文本人格补充。
6. SocialAgent 再追加 Skill、QQ 短消息协议、历史上下文和知识检索片段。

空模块不会进入最终 Prompt。所有缺失事实都保持未知，不允许模型自行补全；资产正文只能由已授权工具按 `assetId` 读取。

## 长期记忆候选

长期记忆候选与“读取近期历史”“历史消息用于个人 Skill”是三项独立授权：

| 授权 | 控制字段 | 用途 |
| --- | --- | --- |
| 读取近期历史 | `privateHistoryEnabled` | 为当前一轮回复补充短期上下文 |
| 个人 Skill 样本 | `historyTrainingEnabled` | 提炼账号主人的表达风格 |
| 长期记忆候选 | `profileContext.memoryPolicy.extractionEnabled` | 从账号主人明确说出的稳定事实中生成待确认候选 |

只有三项授权分别开启时，对应能力才会工作；开启其中一项不会隐式开启另外两项。

### 自动提取边界

Runtime 只在以下条件同时成立时异步提取长期记忆候选：

1. 当前会话命中了已启用的 Profile。
2. `memoryPolicy.extractionEnabled=true`。
3. 当前事件已被 Event Center 标记为 `OWNER`，即消息确实来自账号主人。
4. 当前路由解析到了可用的 LLM 配置。
5. 文本包含账号主人明确陈述、未来仍可能有用的稳定事实。

问题、命令、临时状态、玩笑、猜测、第三方陈述、密码和验证码等敏感信息不会被提取。Agent 代发消息也不会作为候选来源。

### 状态与可信度

- 自动抽取结果固定写入 `CANDIDATE`，不会直接成为已确认记忆。
- Event Center 会按来源 `eventId` 回查事件归属、`actorType=OWNER` 和 `messageOrigin=USER_MANUAL`，不信任 Runtime 自报的来源标签。
- 只有用户在桌面端确认后的 `VERIFIED` 记录，才能按平台、场景或会话作用域进入 Agent 上下文。
- 完全相同的候选事实会合并来源事件并更新最后出现时间，避免重复卡片。
- 同一属性出现不同值时不会覆盖旧事实，也不能绕过冲突流程直接确认。
- 用户选择“保留已确认值”时，新候选进入 `REJECTED`；选择“采用候选值”时，旧值进入 `SUPERSEDED`，新值进入 `VERIFIED`。两步由同一数据库事务完成。
- 候选来源按需读取，每个来源事件只返回有限半径内的聊天上下文；多段窗口会按事件 ID 去重，缺失或越权来源会单独标记。
- 桌面端“长期记忆”入口会显示待确认数量；打开后可查看来源、编辑、确认、处理冲突、拒绝或删除。
