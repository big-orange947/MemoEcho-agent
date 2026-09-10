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
    # 机器人自身 QQ 号(用于区分"对方消息"和"自己发出的消息")
    bot_qq: str = ""
    # 是否启动 QQ 桥(纯桌面端开发时可以关掉)
    enable_napcat: bool = True

    # ---------------------------------------------------------------- 安全
    # 桌面端 API 的本地访问令牌(留空=本机免鉴权)
    api_token: str = ""

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
