# Agent Runtime

这个运行时主要负责：

- route 分类
- 执行计划生成
- 领域 Agent 调度
- Tool 调用中介
- 结果聚合
- 主控台命令的 Router Agent 识别、委托任务创建和 ReAct 工具执行
- 社交回复的情景一致性与最终授权双重审查
- 日程意图门控、结构化抽取、确定性时间归一化和安全落库

本地启动：

```bash
uvicorn app.main:app --reload
```

## 主控台命令链路

Desktop Client 的自然语言命令会先经过 Java `event-center-service` 做鉴权、审计和落库，然后转发到 Runtime。Runtime 不信任前端或 Java 传来的自由文本解析结果，而是由 Router Agent 基于命令和联系人候选列表决定：

- 目标是哪个私聊或群聊
- 是否需要拆成多个联系人任务
- 任务适合普通 ReAct、持续委托任务，还是后续的 Plan-and-Execute
- 是否缺少联系人、时间、目标或授权，需要返回澄清

任务创建由 `DelegatedTaskWorkflow` 完成。执行阶段通过工具读取上下文、发送消息、更新进度和结束任务；模型不能绕过工具直接产生外部副作用。

主控台创建的委托任务可以由 Agent 在完成条件满足后调用结束任务工具自动结束。会话设定集属于长期规则，默认不会因为一次对话完成而自动关闭。

## 社交回复审查链路

私聊自动回复按照以下顺序执行：

1. `SocialAgent` 根据当前消息、双方历史、会话提示词、Skill 和用户知识库生成草稿。
2. `ContextReviewAgent` 检查话题连贯性、人设、说话人身份、作品或人物所属世界，以及即时聊天自然度。
3. 当且仅当公共实体事实存在冲突时，情景审查可以执行一次受限公共检索并重新审查。
4. `ReviewAgent` 最后按照闭世界原则检查账号、联系方式、金额、承诺和现实操作授权；只有 `APPROVE` 才允许回写 QQ。

情景审查不会把搜索工具直接交给生成回复的 Agent，也不会把完整私聊发送给搜索服务。外部查询最多两条，且会删除 URL、邮箱、手机号、QQ 号和其他长数字，只保留最多 60 个字符的实体与关系。

## 可选公共检索

公共检索默认只有在配置 Tavily API Key 后才会启用；未配置时不会发起任何外部网络请求。PowerShell 临时配置方式：

```powershell
$env:TAVILY_API_KEY = "tvly-你的Key"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

如需使用兼容代理，可额外设置：

```powershell
$env:TAVILY_SEARCH_ENDPOINT = "https://api.tavily.com/search"
```

会话设定中的 `publicKnowledgeSearchEnabled=false` 可以单独关闭该会话的公共检索。公共检索结果只用于核对公开实体关系，不能作为用户个人经历、当前状态或授权的证据。

## 日程抽取链路

日程消息不会再因为命中“今天、会议、提醒”等单个关键词就直接写入。当前处理顺序如下：

1. `SemanticScheduleIntentClassifier` 可选使用 Embedding 判断普通消息是否具有“创建日程”语义。查询日程只会被识别为查询，不会进入新增链路。
2. `ScheduleExtractor` 确定性解析明确年月日、今天/明天/后天、N 天后、周几、下周几、中文“点/点半”和时间范围。
3. 规则不能唯一确定时，LLM 按固定 JSON 契约输出意图、原始时间表达、ISO 候选和逐字证据。
4. `ScheduleExtractionPipeline` 在本地校验证据、过去时间、非法日期、开始结束顺序、否定表达和置信度。
5. 只有 `intent=CREATE` 且 `candidateStatus=CONFIRMED` 的候选可以调用 `create_schedule`。人工确认开启时，确认前不会产生落库副作用。

LLM 只提交候选，不拥有最终事实决定权。查询、取消、缺少开始时间、时间歧义、无原文证据或已经过去的候选会变成 `REJECTED`、`NEEDS_CLARIFICATION` 或 `DRAFT`。

## 内置日程语义向量门控

运行时默认使用本地 `BAAI/bge-small-zh-v1.5` 中文向量模型，不需要 API Key，也不需要填写 Embedding 配置。首次启动会在后台自动下载并缓存模型，之后直接复用本地缓存；模型加载失败时只跳过语义补充判断，原有关键词路由和结构化 LLM 链路仍可工作。

远程 OpenAI 兼容 Embedding 仅作为高级覆盖项。只有同时提供 `EMBEDDING_BASE_URL`、`EMBEDDING_API_KEY` 和 `EMBEDDING_MODEL` 时才会切换到远程后端，任意一项缺失都会继续使用内置模型。

`SCHEDULE_INTENT_MIN_SCORE` 控制最低相似度，`SCHEDULE_INTENT_MIN_MARGIN` 控制第一名相对第二名至少领先多少。阈值不足时分类器只返回“不确定”，不会强行覆盖原路由。
