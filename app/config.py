# =============================================================================
# config.py - 配置加载
# -----------------------------------------------------------------------------
# 所有可调参数集中在这里,优先级: 环境变量 > YAML 文件 > 代码默认值。
# 用 pydantic-settings 的好处:
#   - 字段有类型,启动时就会校验,配置写错立刻报错而不是运行到一半才炸;
#   - 环境变量自动映射(如 APP_MODEL_NAME 对应 model_name),无需手写解析。
#
# 典型启动方式:
#   $env:OPENAI_API_KEY="sk-xxx" ; $env:OPENAI_BASE_URL="https://api.deepseek.com"
#   uv run python -m app.main serve
# =============================================================================

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置对象。整个应用通过 `get_settings()` 获取同一份实例。"""

    # ---------------------------------------------------------------- 基础
    # 数据文件存放目录(数据库、日志都放这里,方便整体备份/清理)
    data_dir: Path = Path("data")

    # ---------------------------------------------------------------- 服务
    # 桌面端 API 监听地址
    api_host: str = "127.0.0.1"
    api_port: int = 8000

    # ---------------------------------------------------------------- LLM
    # OpenAI 兼容接口的模型名。fast 通道用于轻量判断(如意图识别),
    # 主通道用于对话生成。
    model_name: str = "deepseek-chat"
    fast_model_name: str = "deepseek-chat"
    # LLM 请求超时(秒)。国内模型偶尔慢,给足余量
    llm_timeout_seconds: float = 120.0
    # 凭据与接口地址。用 validation_alias 对齐业界标准变量名
    # (OPENAI_API_KEY / OPENAI_BASE_URL),这样 .env 与命令行环境变量写法
    # 与 langchain-openai 的习惯一致,迁移零成本。
    # 注意: 这两个值由 main.py 显式传给 ChatOpenAI,不依赖系统环境变量。
    api_key: str = Field(default="", validation_alias="OPENAI_API_KEY")
    base_url: str = Field(default="", validation_alias="OPENAI_BASE_URL")

    # ---------------------------------------------------------------- 会话
    # 注入给 LLM 的历史消息条数上限(控制 token 成本)
    history_max_messages: int = 30

    # ---------------------------------------------------------------- QQ 桥
    # NapCat 服务地址。默认本机 3011(与 launcher-user.bat 一致)
    napcat_base_url: str = "http://127.0.0.1:3011"
    # 机器人自身 QQ 号(用于区分"对方消息"与"自己发出的消息",以及群聊 @ 判定)
    bot_qq: str = ""
    # 是否启动 QQ 桥(纯桌面端开发时可以关掉)
    enable_napcat: bool = True
    # 群聊是否只在被 @ 时才回应(默认 True: 避免机器人在群里喧宾夺主)
    qq_group_require_at: bool = True
    # webhook 是否异步处理:
    #   True  —— 立刻回 200(NapCat 不会判超时重推),图在后台跑。生产用这个。
    #   False —— 等图跑完再回(便于本地调试与测试断言),但慢图会让 NapCat 超时。
    webhook_async: bool = True

    # ---------------------------------------------------------------- 安全
    # 桌面端 API 的本地访问令牌(留空=本机免鉴权)
    api_token: str = ""

    # ---------------------------------------------------------------- 记忆(Doppel)
    # 是否启用长期记忆。关掉则完全不走记忆链路(Doppel 未安装时也自动降级)。
    doppel_enabled: bool = True
    # 存储后端(sqlite / in_memory;Doppel 的 postgres 需要额外依赖)
    doppel_backend: str = "sqlite"
    # 记忆数据库文件名(放在 data_dir 下,与业务库分离,便于单独备份/重建)
    doppel_db_name: str = "doppel.sqlite3"
    # 号主标识(scope 的 user_id)—— **多租户隔离的关键**:
    # 一台实例服务多个号主时,这个值不同则记忆完全隔离。
    owner_user_id: str = ""
    # 机器人标识(scope 的 agent_id)—— 多机器人场景下隔离用;留空则回退到 bot_qq。
    # 说明: 与 bot_qq 分开是刻意的 —— bot_qq 是"登录的号",
    # agent_id 是"记忆归属的身份",将来一个号跑多个 agent 时能区分。
    agent_id: str = ""
    # 每次检索注入的长期记忆条数上限(控制 prompt 体积)
    memory_recall_limit: int = 6

    # ---------------------------------------------------------------- 上报成本
    # 重要消息复核的每日预算(次/天)。复核用快模型,一天最多调用这么多次;
    # 超限后自动退化为纯规则(消息照常上报,只是不再做模型判断)。
    # 设为 0 表示不限(不推荐: 失控的群会烧钱)。
    alert_llm_daily_budget: int = 200
    # 是否用快模型复核上报候选。关掉 = 完全走确定性判定(信号→事件→上下文),
    # 模型成本为零 —— 判定层本身已经能挑出排期变更/截止/金钱这类事件,
    # 复核只是"最后一道过滤",不是必需。
    alert_review_enabled: bool = True

    # 订阅式通知(上游 agent 消费)长轮询单次最长等待秒数。
    # 上游用 GET /api/reports/subscribe 挂起等待新消息,避免空转轮询。
    notify_poll_seconds: int = 25

    # ---------------------------------------------------------------- 上报出口
    # 上报消息的投递渠道(逗号分隔):
    #   db —— 只入队,等上游来 claim(默认;"什么都不做"的安全选项)
    #   qq —— 额外转发到 alert_forward_target 指定的会话(本机自闭环,不依赖上游)
    # 注意: 启用 qq 后,**本地就是队列的消费者** —— 上游不会再看到这些记录
    # (否则同一件事会被报两遍)。要交给上游就把这里改回 db。
    alert_sinks: str = "db"
    # qq 出口的转发目标: "private:123456" / "group:789012" / "123456"(默认私聊)
    alert_forward_target: str = ""
    # 单会话每小时最多主动上报多少条(normal/digest;urgent 与请示不受限)。
    # 防话痨群刷屏;超限的**暂缓**而不是丢弃,下一轮继续送。
    alert_max_per_hour: int = 10

    # ---------------------------------------------------------------- 存储保留
    # 监视中的群聊会持续写入,不设上界迟早把磁盘啃满(而且是静默地啃)。
    # 删数据是危险动作,所以规则是"宁可不删,不可误删"(详见 app/retention.py):
    #   · 消息只在**已总结进长期记忆**之后才可能被删(没有水位线就一条不删);
    #   · 每个会话始终保留最近 message_keep_min 条(上下文安全垫);
    #   · 有进行中目标的会话跳过。
    retention_enabled: bool = True
    # 消息保留天数(0 = 永久保留,不清理)
    message_retention_days: int = 90
    # 每个会话无条件保留的最近消息条数
    message_keep_min: int = 200
    # 事件审计保留天数(0 = 永久)
    event_retention_days: int = 30
    # 外部调度记录保留天数(只删已结束的)
    dispatch_retention_days: int = 30
    # 定时唤醒记录保留天数(只删已触发/已取消的)
    schedule_retention_days: int = 30
    # 已处理的上报记录保留天数
    report_retention_days: int = 30
    # 休眠超过这么多天的会话,其 checkpoint 只保留最新一份(0 = 不精简)
    checkpoint_retention_days: int = 30
    # 清理扫描间隔(小时)。默认 24 小时 —— 这是维护动作,不需要频繁跑。
    retention_interval_hours: int = 24

    model_config = SettingsConfigDict(
        # 允许从 .env 文件读取(与 local-env.ps1 二选一,二选一即可)
        env_file=".env",
        env_file_encoding="utf-8",
        # 环境变量前缀,如 MEMO_ECHO_MODEL_NAME 对应 model_name
        env_prefix="MEMO_ECHO_",
        extra="ignore",
    )


# 模块级单例: 首次访问时构建,之后复用。
# 用 functools.lru_cache 保证整个进程只有一份配置,避免多处实例不一致。
_settings: Settings | None = None


def get_settings() -> Settings:
    """返回全局配置单例。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
