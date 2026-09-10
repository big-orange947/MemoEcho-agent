# 会话值守 / 监视 / 上报

本文档说明 Memo Echo v2 的**会话级策略**:谁能自动回复、谁只被静默记录、重要消息如何上报给上游。

设计目标:默认什么都不做(不记录、不回复、不上报),由人显式按会话开启。

---

## 1. 三个开关

策略存在 `conversations` 表上,每个会话一行。**默认全关**。

| 开关 | 取值 | 默认 | 含义 |
|---|---|---|---|
| `monitor` | 0/1 | 0 | **总开关**。关闭 ⇒ 该会话的消息不落库、不写记忆、不上报、不回复 |
| `reply_mode` | off / draft / auto | off | off 不回复;draft 生成草稿待确认;auto 自动回复 |
| `alert_enabled` | 0/1 | 0 | 命中"重要消息"规则时投进上报队列 |

**蕴含关系**:`reply_mode≠off` 或 `alert_enabled=1` ⇒ 自动打开 `monitor`(不监视就没有数据)。
API 会在返回值里用 `implied` 字段明确告知"顺手打开了哪个开关",不静默改语义。

其余可调项:

| 字段 | 默认 | 说明 |
|---|---|---|
| `persona` | 空 | 会话人设 / **值守注意事项**(如"别答应晚上十点后的活动"),注入系统提示 |
| `alert_keywords` | `[]` | 上报关键词(如 `["急事","改时间"]`) |
| `require_human_confirmation` | 1 | 拿不准时是否必须请示号主 |
| `digest_window_seconds` | 1800 | 攒批记忆:消息静止多久触发总结 |
| `digest_max_messages` | 20 | 攒批记忆:攒够多少条触发总结 |
| `allowed_tools` | `[]` | 工具授权;空 = 按会话类型取默认(群聊默认不给 `send_qq_message`) |

---

## 2. 怎么配置

### 2.1 命令行(人直接操作,推荐日常使用)

```powershell
# 看所有会话及其开关
uv run python scripts/set_policy.py list

# 只监视不回复(静默收集)
uv run python scripts/set_policy.py set --qq 2597164807 --monitor

# 开启自动回复 + 注意事项
uv run python scripts/set_policy.py set --qq 2597164807 --reply auto --note "别答应晚上十点后的活动"

# 群聊: 监视 + 上报 + 关键词
uv run python scripts/set_policy.py set --group 123456 --monitor --alert --keywords 急事,改时间

# 一键回到静默
uv run python scripts/set_policy.py set --qq 2597164807 --off
```

### 2.2 HTTP API(前端 / 上游主 agent)

```http
# 按三元组定位(或创建)会话 —— 默认全关的会话没有消息,只能这样找到它
POST /api/conversations/resolve
{"platform":"qq","chat_type":"private","external_id":"2597164807","title":"小号"}

# 修改配置
PATCH /api/conversations/{id}
{"reply_mode":"auto","persona":"别答应晚上十点后的活动"}
→ {"conversation":{...}, "changed":{"reply_mode":["off","auto"]}, "implied":["monitor"]}
```

### 2.3 主 agent 派发(自然语言配置)

主 agent 听到"开始自动回复与 XXX 的会话,注意事项是别提钱"后,翻译成一次 dispatch:

```http
POST /api/dispatch
{
  "caller": "main-agent",
  "kind": "configure",
  "target": {"platform":"qq","chat_type":"private","external_id":"2597164807"},
  "policy": {"reply_mode":"auto","note":"别提钱"}
}
```

`kind=configure` **不产生任何对外消息**,只改配置 + 写审计 + 推送 SSE。
`note` 是 `persona` 的别名,两个都接受。

---

## 3. 消息怎么被处理(分流规则)

**权威规则:显式指令 > 会话策略。** 人明确要求做的事,不受开关约束。

| 事件来源 | 是否受策略约束 |
|---|---|
| 主 agent 派发(task / handle_message) | **不受** |
| 桌面端 / 前端指令、定时唤醒(有活动目标时) | **不受** |
| QQ 对方发来的消息、群聊消息 | 受 |
| 号主手机自己发的消息(message_sent) | 受(监视时记录,永不回复) |

| monitor | reply_mode | 对方消息 | 自发消息 | 群未 @ |
|---|---|---|---|---|
| 0 | – | 仅审计 | 仅审计 | 仅审计 |
| 1 | off | 落库 + 报道评估 | 落库 | 落库 |
| 1 | auto | 落库 + 回复 | 落库 | 落库 |
| 1 | draft | 落库 + 草稿 | 落库 | 落库 |

**任务授权态**:显式指令在某会话建了目标(goal)之后,该会话在任务结束前可自由交流
—— 否则"帮我约 km 打游戏"在未开启自动回复的会话里根本推进不下去。
目标完成/放弃即恢复静默。

---

## 4. 重要消息怎么上报

流水线(逐级加成本,默认最省):

1. **规则初筛**(零模型成本,对监视中的消息全量跑):
   @号主/机器人、`alert_keywords`、疑问请求语气、白名单联系人(`configs.alert_contacts`);
2. **快模型复核**(每条候选一批,合并调用): 判定 `urgent` / `normal` / `digest` + 一句话摘要;
   - 日配额 `alert_llm_daily_budget`(默认 200 次/天)封顶;
   - **配额耗尽或复核失败 → 退化为纯规则**(消息照常上报,只是不再做模型判断)。
     原则:宁可多报一条,也不能漏掉急事;
3. **入队**:候选进 `report_queue`,由上游消费。

### 4.1 上报送到哪里(sink)

上报的**出口**由配置决定,默认只入队(等上游来取):

| 配置 | 行为 |
|---|---|
| `MEMO_ECHO_ALERT_SINKS=db`(默认) | 只入队。上游 agent 用下面的接口来取 |
| `MEMO_ECHO_ALERT_SINKS=db,qq` | **额外转发到指定 QQ 会话**(本机自闭环,不依赖上游) |

启用 qq 出口时还要指定转发目标:

```powershell
# 转发到自己的另一个号
$env:MEMO_ECHO_ALERT_FORWARD_TARGET = "private:1234567"
# 或转发到专用通知群
$env:MEMO_ECHO_ALERT_FORWARD_TARGET = "group:987654321"
# 单会话每小时最多主动报几条(不加急的;急事与请示不受限,默认 10)
$env:MEMO_ECHO_ALERT_MAX_PER_HOUR = "10"
```

**关键语义: 启用 qq 出口后,本地就是队列的消费者** ——
记录被本地认领并投递,上游就取不到了。这是刻意的:
否则同一件事会被报两遍(本地一条、上游一条)。要交给上游,把 `alert_sinks` 改回 `db` 即可。

通知的样子(急事与请示单独成条,普通消息合并摘要,防刷屏):

```
【急】km
对方七点要走，问今晚是否还来
原话: 你今晚还来不来？我七点就得走了
命中: keyword:今晚

【待查看 · 3 条】
1. 班群 · 张三
   明天组会改到四点
2. 辅导员
   奖学金材料周五截止
```

### 4.2 上游怎么消费(类消息中间件)

```http
# 认领一批(带租约;幂等,至少一次投递)
POST /api/reports/claim
{"limit":10, "lane":"urgent", "lease_seconds":120, "claimed_by":"main-agent"}
→ {"items":[{"id":"...","lane":"urgent","payload":{"summary":"..."},"attempts":1}], "count":1}

# 处理完确认(或决定不报)
POST /api/reports/{id}/ack
POST /api/reports/{id}/drop   {"reason":"不重要"}

# 长轮询: 挂起等新消息(拿不到就等,拿到立刻返回)
GET /api/reports/subscribe?timeout=25

# 查看队列 / 统计
GET /api/reports?status=pending
GET /api/reports/stats
```

队列语义(对齐真 MQ):

- **至少一次**:认领带租约,到期未 ack 自动回队列重新投递;
- **死信**:认领超过 5 次进 `dead`,可查可重放;
- **幂等**:同一消息重复处理不会重复入队;
- **TTL**:已处理的记录超期自动清理。

lane 含义:

| lane | 用途 |
|---|---|
| `urgent` | 立即上报(模型判断时间敏感) |
| `normal` | 重要但不紧急 |
| `question` | **HITL 请示**:agent 拿不准,回来问号主 |
| `digest` | 攒起来一起看 |

将来接入真 MQ(RocketMQ/RabbitMQ/Kafka)时,替换的是 sink 实现,
`report_queue` 的 claim/ack 语义可原样映射,业务代码不用改。

---

## 5. 人工介入(HITL)

agent 替号主交涉时,遇到**只有号主能拍板**的事(对方改期、涉及承诺/钱/对外形象、
需要号主才知道的现实信息),会调用 `escalate_to_owner` 工具:

- 问题进上报队列的 `question` 通道(与重要消息同一出口);
- 目标进度标记为"等待号主指示"(桌面端进度卡可见),**仍是进行中**,不会被丢掉;
- 号主的答复(不管从哪个入口来)作为新消息回到会话,agent 从 checkpoint 恢复继续推进。

同一问题重复请示会幂等去重,不会刷屏。

---

## 6. 长期记忆:攒批写入

长期记忆**不再逐条写入** —— 那会把记忆碎成一堆"嗯""好的"。

- 只有 `monitor=1` 的会话参与;
- 触发条件:待处理消息数 ≥ `digest_max_messages`(20) 或 消息静止超过 `digest_window_seconds`(1800);
- 调度器每 60 秒扫一次,把该总结的会话各总结一轮(fast 模型);
- **值得记才写**:模型认为这批没价值就一条不写,但处理水位线照常推进;
- 进度存在 `memory_batches` 表(游标 + 水位线),服务重启不丢。

---

## 7. 工具权限

每个会话能用哪些工具由策略决定,**两层拦截**:

1. `reason` 只把授权工具 bind 给模型(未授权的模型根本看不到);
2. `act` 拒绝执行未授权的调用并写审计(防模型幻觉或历史残留);
3. 高危工具内部再校验一次(`send_qq_message` 有 `high_risk` 标签)。

默认集:

- **私聊**:全部工具;
- **群聊**:不给 `send_qq_message`(群聊人数多、不可控,被诱导替号主发消息的风险最高)。
  需要时用 `allowed_tools: ["send_qq_message"]` 显式授权。

新工具只要打上 `high_risk` 标签(`tool.tags = [HIGH_RISK_TAG]`),群聊默认就会被拦下。

---

## 8. 成本

| 环节 | 模型调用 |
|---|---|
| 未监视的会话 | 0 |
| 监视落库 | 0 |
| 上报规则初筛 | 0 |
| 上报快模型复核 | 每批候选 1 次 fast(日配额封顶,超额退化为纯规则) |
| 攒批记忆总结 | 每批 1 次 fast |
| 自动回复 | 每条消息 1 次主模型(仅 `reply_mode=auto`) |

此外,checkpoint 历史会按 `history_max_messages` 裁剪(保留最近窗口,
不动数据库里的完整历史),避免长会话的 prompt 无界增长。

---

## 9. 运维与排障

### 9.1 冒烟: 验证两条"只跑过假模型"的路径

攒批记忆与上报复核都依赖模型的 JSON 输出。单元测试用假模型只能验证接线,
验证不了"提示词在真模型上产不产得出能解析的东西"。**它们的失效都是静默的**:
攒批解析失败时业务上等同于"这批没值得记的",水位线照常推进,那批消息再也不会被总结。

```powershell
uv run python scripts/smoke_llm_paths.py
```

它用**生产代码本身**真调一次模型,输出每项的 PASS/FAIL 与模型原始结果,
不碰数据库、不发任何消息。**改动这两个提示词后请跑一次**(提示词一改,
真模型上的输出格式就可能变)。

### 9.2 健康度接口: 看"有没有静默的记忆缺失"

```http
GET /api/memory/health
```

返回:

| 字段 | 含义 |
|---|---|
| `enabled` / `init_error` | 记忆功能是否启用、初始化失败原因 |
| `summary.calls` | 总结器调用次数 |
| `summary.empty` | 模型判定"没值得记的"(正常) |
| `summary.unparsed` | **有输出但解析不出**(可疑 —— 提示词或模型出问题) |
| `summary.last_unparsed_sample` | 上一次解析失败的原始输出(排查用) |
| `batches[]` | 各会话的待处理条数、上次状态与错误 |

关注 `unparsed` 与 `batches[].pending_count` 一直涨的会话。
