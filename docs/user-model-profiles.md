# 用户模型配置中心接口文档

## 1. 目标

这套接口用于给 `memo-echo-agent` 提供“用户自带模型配置”能力。

当前版本解决的问题：

- 用户可以配置自己的 `API Key`
- 用户可以配置自己的 `Base URL`
- 用户可以配置自己的 `Model`
- 用户可以按 `route` 给不同 Agent 指定不同模型
- Python runtime 在执行前会先向配置中心解析当前应使用的模型配置
- 如果后端没有命中配置，runtime 会自动回退到环境变量默认配置

当前实现方式：

- 配置中心服务：`services-java/event-center-service`
- 运行时消费方：`agent-runtime-python`
- 当前解析入口：`POST /internal/user-model-profiles/resolve`

## 2. 数据结构

### 2.1 用户模型配置字段

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | `string` | 配置 id |
| `userId` | `string` | 用户标识，当前建议前端先使用固定值，比如 `freeze` 或 `default` |
| `name` | `string` | 配置名称 |
| `description` | `string` | 配置说明 |
| `enabled` | `boolean` | 是否启用 |
| `provider` | `string` | 提供方类型，当前默认 `OPENAI_COMPATIBLE` |
| `baseUrl` | `string` | OpenAI 兼容接口基地址 |
| `apiKey` | `string` | 模型密钥，仅创建、更新、resolve 时会用到 |
| `model` | `string` | 模型名 |
| `temperature` | `number` | 采样温度，范围会被后端规整到 `0 ~ 2` |
| `maxTokens` | `number` | 最大输出 token 数 |
| `supportedRoutes` | `string[]` | 支持的 route 列表；空数组表示全局默认 |
| `isDefault` | `boolean` | 是否为当前用户默认配置 |
| `priority` | `number` | 优先级，数值越大越优先 |

### 2.2 route 使用建议

当前项目里已经存在或建议使用的 route：

- `social_reply`
- `task_plan`
- `schedule_extract`
- `file_analysis`
- `message_dispatch`
- `chat_summary`
- `group_ops`

你后面如果继续加 Agent，可以直接扩充 route 名称，不需要改这套表结构。

## 3. 接口列表

## 3.1 查询全部配置

`GET /internal/user-model-profiles`

说明：

- 返回当前全部用户模型配置
- 返回的是脱敏后的密钥信息

返回示例：

```json
[
  {
    "id": "model-profile-001",
    "userId": "freeze",
    "name": "默认社交模型",
    "description": "用于私聊回复",
    "enabled": true,
    "provider": "OPENAI_COMPATIBLE",
    "baseUrl": "https://api.openai.com/v1",
    "hasApiKey": true,
    "apiKeyMasked": "sk-d****0001",
    "model": "gpt-4o-mini",
    "temperature": 0.7,
    "maxTokens": 2048,
    "supportedRoutes": ["social_reply"],
    "isDefault": true,
    "priority": 8,
    "createdAt": "2026-07-09T00:00:00Z",
    "updatedAt": "2026-07-09T00:10:00Z"
  }
]
```

## 3.2 查询单条配置

`GET /internal/user-model-profiles/{profileId}`

说明：

- 查询单条配置详情
- 同样只返回脱敏后的密钥

## 3.3 创建配置

`POST /internal/user-model-profiles`

请求示例：

```json
{
  "userId": "freeze",
  "name": "默认社交模型",
  "description": "用于私聊回复",
  "enabled": true,
  "provider": "OPENAI_COMPATIBLE",
  "baseUrl": "https://api.openai.com/v1",
  "apiKey": "sk-demo-001",
  "model": "gpt-4o-mini",
  "temperature": 0.7,
  "maxTokens": 2048,
  "supportedRoutes": ["social_reply"],
  "isDefault": true,
  "priority": 8
}
```

说明：

- `supportedRoutes` 为空时表示全局默认
- `isDefault=true` 时，会自动取消该用户其他默认配置

## 3.4 更新配置

`PUT /internal/user-model-profiles/{profileId}`

请求示例：

```json
{
  "userId": "freeze",
  "name": "默认社交模型",
  "provider": "OPENAI_COMPATIBLE",
  "baseUrl": "https://api.openai.com/v1",
  "model": "gpt-4.1-mini",
  "supportedRoutes": ["social_reply"],
  "isDefault": true,
  "priority": 9
}
```

密钥更新规则：

- 不传 `apiKey`：保留旧密钥
- 传 `apiKey`：覆盖旧密钥
- 传 `clearApiKey=true`：清空旧密钥

清空密钥示例：

```json
{
  "userId": "freeze",
  "name": "默认社交模型",
  "clearApiKey": true
}
```

## 3.5 删除配置

`DELETE /internal/user-model-profiles/{profileId}`

## 3.6 解析当前应使用的配置

`POST /internal/user-model-profiles/resolve`

这是 Python runtime 真正会调用的接口。

请求示例：

```json
{
  "userId": "freeze",
  "route": "social_reply"
}
```

返回示例：

```json
{
  "matched": true,
  "reason": "命中 route 定向模型配置",
  "profile": {
    "id": "model-profile-001",
    "userId": "freeze",
    "name": "默认社交模型",
    "provider": "OPENAI_COMPATIBLE",
    "baseUrl": "https://api.openai.com/v1",
    "apiKey": "sk-demo-001",
    "model": "gpt-4o-mini",
    "temperature": 0.4,
    "maxTokens": 1024,
    "supportedRoutes": ["social_reply"],
    "isDefault": true,
    "priority": 10
  }
}
```

未命中示例：

```json
{
  "matched": false,
  "reason": "未命中任何用户模型配置",
  "profile": null
}
```

## 4. 当前解析优先级

当前 `resolve` 的优先级规则是：

1. 只看 `enabled=true`
2. `userId` 必须匹配
3. `supportedRoutes` 命中当前 route，或为空
4. `priority` 越大越优先
5. 绑定了具体 `route` 的配置优先于全局默认
6. `isDefault=true` 优先于普通配置
7. 最后按更新时间兜底

## 5. Python runtime 当前接入方式

当前 `agent-runtime-python` 已经接入这套配置中心，行为如下：

1. 事件进入 orchestrator
2. 路由得到当前 `route`
3. runtime 调用 `/internal/user-model-profiles/resolve`
4. 如果命中配置，则把返回结果放进 `task_context.metadata.resolved_model_profile`
5. `SocialAgent` 调用 LLM 时优先使用这份配置
6. 如果后端没命中配置，或者后端不可用，则回退到环境变量

当前环境变量兜底项仍然有效：

```powershell
$env:OPENAI_API_KEY="你的key"
$env:OPENAI_MODEL="gpt-4o-mini"
$env:OPENAI_BASE_URL="https://api.openai.com/v1"
```

当前 runtime 用户标识默认读取：

```powershell
$env:MEMO_ECHO_RUNTIME_USER_ID="freeze"
```

如果不配，默认会用：

```text
default
```

桌面命令不再依赖这个固定环境变量。`event-center` 会把当前 JWT 用户 ID 写入标准事件，Runtime 优先使用该用户解析模型；环境变量只作为旧平台事件的兼容回退。

## 6. 当前已经实现到什么程度

围绕“用户模型配置中心”，当前已经实现：

- Java 侧用户模型配置 CRUD
- Java 侧模型配置解析接口
- 同一用户默认配置唯一化
- API Key 脱敏展示
- Python runtime 拉取当前 route 的模型配置
- `SocialAgent` 优先使用后端解析出的模型配置
- 后端配置缺失时自动回退到环境变量
- 桌面客户端模型配置 CRUD 页面
- API Key 加密存储和脱敏展示
- JWT 用户级配置隔离
- 桌面命令按当前登录用户解析模型

## 7. 当前还没做的部分

这几项还没做：

- 模型连接可用性测试按钮
- 按会话直接绑定模型配置
- 按 Agent 维度细分模型参数
- 模型调用额度和延迟统计

## 7.1 当前持久化实现

当前已经不是纯内存版了。

`event-center-service` 现在使用：

- `Spring JDBC`
- `H2 文件数据库`

默认数据库地址：

```yaml
spring:
  datasource:
    url: jdbc:h2:file:./data/event-center-db;MODE=MySQL;DB_CLOSE_DELAY=-1;AUTO_SERVER=TRUE
```

说明：

- 数据文件会落在 `event-center-service` 运行目录下的 `data/`
- 应用启动时会自动执行 `schema.sql`
- 当前只持久化 `user_model_profile` 这一张表

表结构初始化文件：

- `services-java/event-center-service/src/main/resources/schema.sql`

## 7.2 本地启动说明

启动 `event-center-service` 前，确保 JDK 21 可用。

PowerShell 示例：

```powershell
$env:JAVA_HOME="D:\java"
$env:Path="D:\java\bin;" + $env:Path
mvn spring-boot:run
```

如果只是跑测试：

```powershell
$env:JAVA_HOME="D:\java"
$env:Path="D:\java\bin;" + $env:Path
mvn test
```

## 8. 下一步建议

建议后续按这个顺序继续：

1. 做前端“模型配置中心”
2. 把模型配置能力扩展到 `WorkAgent`、`ScheduleAgent`
3. 支持“会话设定集”和“模型配置”联动
4. 支持“skill 指定模型”
5. 增加密钥加密存储与多用户隔离
