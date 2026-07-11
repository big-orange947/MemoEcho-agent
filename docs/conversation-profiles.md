# 会话设定集

会话设定集是 `Memo Echo Agent` 的一层运行时策略配置。

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

## 下一步建议

下一步最适合接的是两块：

1. 前端配置页
   - 直接增删改查这些设定
   - 展示当前会话命中的 profile
   - 展示 `resolvedSkills`、`unresolved_skill_references`、`allowedTools`

2. GitHub skill 安装链路
   - 把 `github://...` 从“仅记录未解析”升级为“可安装 skill 源”
   - 增加 skill 清单、缓存目录、版本号、启停状态
   - 增加 skill 审核策略，避免前端一键加载任意危险能力
