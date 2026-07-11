# Skill 管理与前端配置接口

这份文档描述的是当前已经落地的两块能力：

1. GitHub skill 安装链路
2. 前端会话设定页所需的配置辅助接口

## 一、设计目标

当前实现坚持一个原则：

- Python runtime 不在处理消息时临时联网下载 skill
- 所有 GitHub skill 都必须先安装到本地缓存目录
- runtime 只读取本地 `skill.json`

这样做的好处是：

- 运行时更稳定
- 不会把远程网络波动直接带进消息处理链路
- skill 来源和安装结果更容易排障
- 后续做 skill 审核、版本锁定和启停状态也更自然

## 二、当前目录约定

`event-center-service` 现在使用下面两个目录：

- 内置 skill 目录：`agent-runtime-python/skills`
- 已安装 skill 缓存目录：`agent-runtime-python/skills-installed`

默认配置在：

[application.yml](D:/project/memo_echo-agent/services-java/event-center-service/src/main/resources/application.yml)

对应配置类：

[SkillStoreProperties.java](D:/project/memo_echo-agent/services-java/event-center-service/src/main/java/com/memoecho/eventcenter/config/SkillStoreProperties.java)

## 三、GitHub skill 安装流程

### 1. 前端发起安装请求

```http
POST /internal/skills/install/github
Content-Type: application/json
```

请求体示例：

```json
{
  "reference": "github://demo-owner/demo-repo/personas/reliable-assistant"
}
```

当前也支持显式指定分支或标签：

```json
{
  "reference": "github://demo-owner/demo-repo@main/personas/reliable-assistant"
}
```

### 2. 后端下载 skill.json

Java 侧会把 `github://...` 引用解析成 GitHub raw 地址，然后下载描述文件：

- `github://owner/repo/path/to/skill-dir`
- 会被转换成：
- `https://raw.githubusercontent.com/owner/repo/main/path/to/skill-dir/skill.json`

下载器实现位置：

[GithubRawSkillDescriptorDownloader.java](D:/project/memo_echo-agent/services-java/event-center-service/src/main/java/com/memoecho/eventcenter/service/GithubRawSkillDescriptorDownloader.java)

### 3. 后端把 skill 写入本地缓存目录

安装完成后会写入：

```text
agent-runtime-python/skills-installed/github/{owner}/{repo}/{ref}/{path}/skill.json
agent-runtime-python/skills-installed/github/{owner}/{repo}/{ref}/{path}/origin.json
```

其中：

- `skill.json` 是 runtime 真正读取的标准描述文件
- `origin.json` 用于记录安装来源，方便后续做升级、卸载和排障

核心实现位置：

[SkillCatalogApplicationService.java](D:/project/memo_echo-agent/services-java/event-center-service/src/main/java/com/memoecho/eventcenter/service/SkillCatalogApplicationService.java)

## 四、Python runtime 如何读取已安装 GitHub skill

runtime 侧已经不再把 `github://...` 一律视为未解析。

现在的规则是：

1. 如果引用是本地 `skills/...`
   - 直接去 `skills/` 下找
2. 如果引用是 `github://...`
   - 不联网
   - 直接去 `skills-installed/github/...` 下找安装后的 `skill.json`

实现位置：

[resolver.py](D:/project/memo_echo-agent/agent-runtime-python/app/skills/resolver.py)

这意味着当前链路已经变成：

- 前端安装 GitHub skill
- Java 下载并落盘
- Python runtime 直接本地解析
- 会话设定里继续保留 `github://...` 原始引用即可

## 五、前端可直接使用的接口

### 1. 查询全部 skill

```http
GET /internal/skills
```

作用：

- 返回内置 skill
- 返回已安装 GitHub skill
- 前端可直接用来渲染 skill 选择器

### 2. 安装 GitHub skill

```http
POST /internal/skills/install/github
Content-Type: application/json
```

作用：

- 把远程 skill 安装到本地缓存目录

### 3. 预览 skill 是否能生效

```http
POST /internal/skills/resolve-preview
Content-Type: application/json
```

请求体示例：

```json
{
  "route": "social_reply",
  "skillReferences": [
    "skills/personas/reliable-assistant",
    "github://demo-owner/demo-repo/personas/reliable-assistant"
  ]
}
```

返回体会分成两块：

- `resolvedSkills`
- `unresolvedSkillReferences`

前端可以直接提示：

- 哪些 skill 当前已经安装并且 route 匹配
- 哪些 skill 还没安装或者当前 route 不适用

### 4. 查询会话设定页所需全部枚举和 skill 列表

```http
GET /internal/conversation-profiles/configuration
```

返回内容包括：

- `supportedPlatforms`
- `supportedScenes`
- `chatTypes`
- `triggerModes`
- `replyModes`
- `personaModes`
- `supportedRoutes`
- `availableTools`
- `availableSkills`

这个接口的目标就是让前端不要再硬编码：

- route 名字
- tool 名字
- personaMode 枚举
- triggerMode 枚举
- replyMode 枚举

## 六、当前支持的引用写法

### 本地 skill

- `skills/personas/reliable-assistant`
- `local://personas/reliable-assistant`
- `skills/.../skill.json`

### GitHub skill

- `github://owner/repo/path/to/skill-dir`
- `github://owner/repo@main/path/to/skill-dir`

## 七、当前还没做的部分

这一版还没有实现下面这些能力：

- GitHub skill 升级
- GitHub skill 卸载
- GitHub skill 启停状态
- skill 版本锁定到 commit sha
- skill 安装审核
- skill 签名校验
- 前端 skill 市场页

## 八、推荐前端接法

前端后面做会话设定页时，建议按这个顺序：

1. 页面初始化先调用 `/internal/conversation-profiles/configuration`
2. skill 选择框如果需要更多细节，再调用 `/internal/skills`
3. 用户填入 GitHub skill 引用时，先调 `/internal/skills/install/github`
4. 保存设定前，再调 `/internal/skills/resolve-preview`
5. 最后把 `skillReferences` 存进会话设定

这样前端体验会比较顺：

- 先安装
- 再预览
- 再保存
