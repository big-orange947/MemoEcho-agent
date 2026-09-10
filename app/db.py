# =============================================================================
# db.py - SQLite 存储层
# -----------------------------------------------------------------------------
# 为什么用 SQLite 而不是 MySQL:
#   - 单人本地使用,单文件零运维,备份=拷文件;
#   - Python 标准库 sqlite3 直接可用,不引入 ORM 复杂度;
#   - LangGraph 的 checkpoint 也存 SQLite(由框架管理,见 agent/graph.py)。
#
# 表结构一览(业务数据只有 5 张表,对比 v1 的十几张状态表):
#   conversations  会话(一个 QQ 私聊/群/桌面线程 = 一行)
#   messages       消息历史(双方的聊天记录,agent 上下文来源)
#   goals          目标(可选: 把命令变成"带目标的对话",由 LLM 自主推进)
#   configs        键值配置(用户设置/模型绑定等)
#   events         事件日志(审计与排障)
#
# 连接约定: 本模块只负责"建表 + 提供连接";具体的增删改查放在 services/ 下,
# 每个 service 模块专注一张表,方便按流程阅读。
# =============================================================================

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from .config import get_settings

# 线程本地连接: 每个线程(含 FastAPI 线程池里的工作线程)持有自己的连接,
# 避免"跨线程使用 SQLite 连接"报错。
# 注意: 业务层全部走同步 sqlite3,线程本地化后天然线程安全。
_local = threading.local()

# 全局连接注册表(线程id -> 连接),供 close_connections 统一清理。
_lock = threading.Lock()
_all_connections: dict[int, sqlite3.Connection] = {}


def _connect() -> sqlite3.Connection:
    """创建(或复用)当前线程的数据库连接,并开启必要的 PRAGMA。"""
    conn = getattr(_local, "connection", None)
    if conn is not None:
        return conn

    settings = get_settings()
    # 确保数据目录存在,否则 sqlite3.connect 会直接报错
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db_path: Path = settings.data_dir / "memo-echo.db"

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # 查询结果按列名访问: row["id"]
    # WAL 模式: 读写不互相阻塞,适合"agent 写 + 桌面端读"并发场景
    conn.execute("PRAGMA journal_mode=WAL")
    # 外键约束默认关闭,显式打开保证引用完整性
    conn.execute("PRAGMA foreign_keys=ON")
    # 并发写(agent 与桌面端同时落库)时等待而非立刻报 locked
    conn.execute("PRAGMA busy_timeout=5000")

    _local.connection = conn
    # 登记到全局注册表(进程退出时统一关闭)
    with _lock:
        _all_connections[threading.get_ident()] = conn
    return conn


def get_connection() -> sqlite3.Connection:
    """对外暴露当前线程的连接(供 services 层使用)。"""
    return _connect()


def close_connections() -> None:
    """关闭所有线程的数据库连接(应用退出时调用)。"""
    with _lock:
        conns = list(_all_connections.values())
        _all_connections.clear()
    for conn in conns:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def init_db() -> None:
    """建表。应用启动时调用一次;表已存在则跳过(IF NOT EXISTS)。

    建表后执行一次轻量迁移(_migrate),为老库补齐后续版本新增的列 ——
    避免开发期删库重建,也保证已有数据不丢。
    """
    conn = _connect()
    conn.executescript(
        """
        -- ============================================================
        -- conversations: 会话主表
        -- platform: qq / desktop(桌面端线程)
        -- external_id: QQ 号/群号,或桌面线程的本地 ID
        -- chat_type: private(私聊) / group(群) / thread(桌面)
        -- persona: 该会话绑定的"人设"描述(可选)
        -- ============================================================
        CREATE TABLE IF NOT EXISTS conversations (
            id          TEXT PRIMARY KEY,           -- 内部稳定 ID(uuid)
            platform    TEXT NOT NULL,              -- qq / desktop
            chat_type   TEXT NOT NULL,              -- private / group / thread
            external_id TEXT NOT NULL,              -- 平台侧 ID
            title       TEXT DEFAULT '',            -- 会话标题(桌面端展示)
            persona     TEXT DEFAULT '',            -- 人设/行为约束(含"值守注意事项")
            model_name  TEXT DEFAULT '',            -- 会话级模型绑定(空=全局)
            -- ---- 会话策略(见 services/policy.py): 默认全关 ----
            monitor     INTEGER DEFAULT 0,          -- 是否监视(总开关): 关 ⇒ 不落库/不记忆/不上报/不回复
            reply_mode  TEXT DEFAULT 'off',         -- off 不回复 / draft 草稿待确认 / auto 自动回复
            alert_enabled INTEGER DEFAULT 0,        -- 是否上报重要消息
            alert_keywords TEXT DEFAULT '[]',       -- 上报关键词(JSON 数组)
            require_human_confirmation INTEGER DEFAULT 1,  -- 拿不准时是否必须请示(1=是)
            digest_window_seconds INTEGER DEFAULT 1800,    -- 攒批窗口(秒): 空闲多久触发总结
            digest_max_messages INTEGER DEFAULT 20,        -- 攒批条数上限: 满多少条触发总结
            allowed_tools TEXT DEFAULT '',          -- 允许的工具名(JSON 数组; 空=按会话类型默认)
            created_at  TEXT NOT NULL,
            updated_at  TEXT NOT NULL,
            UNIQUE (platform, chat_type, external_id)  -- 同一会话只存一行
        );

        -- ============================================================
        -- messages: 消息历史(agent 上下文的唯一来源)
        -- role: user(对方/主人) / assistant(agent 自己)
        -- source: inbound(收到的) / outbound(发出的) / system
        -- goal_id: 可选,标记这条消息属于哪个目标(用于进度展示)
        -- ============================================================
        CREATE TABLE IF NOT EXISTS messages (
            id         TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role       TEXT NOT NULL,               -- user / assistant
            source     TEXT NOT NULL,               -- inbound / outbound / system
            content    TEXT NOT NULL,               -- 纯文本内容
            raw_json   TEXT DEFAULT '',             -- 原始平台载荷(排查用)
            goal_id    TEXT DEFAULT '',             -- 关联目标(可空)
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_messages_conv_time
            ON messages (conversation_id, created_at);

        -- ============================================================
        -- goals: 目标(可选)。把命令(如"问km几点上课转告小号")挂到会话上,
        -- agent 自主推进,完成/放弃由 LLM 决定,不再有步骤契约。
        -- status: active / done / abandoned
        -- progress: LLM 维护的一句话进度(桌面端进度卡展示)
        -- ============================================================
        CREATE TABLE IF NOT EXISTS goals (
            id              TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            objective       TEXT NOT NULL,          -- 目标原文
            status          TEXT DEFAULT 'active',
            progress        TEXT DEFAULT '',        -- 一句话进度摘要
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            completed_at    TEXT DEFAULT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_goals_conv ON goals (conversation_id);

        -- ============================================================
        -- configs: 键值配置(用户设置/模型 profile 等)
        -- ============================================================
        CREATE TABLE IF NOT EXISTS configs (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        -- ============================================================
        -- events: 事件审计日志
        -- 每一条进入系统的事件都记一笔(不论是否需要回应)。
        -- 用途:
        --   1. 排障 —— "那条消息到底收到没有/解析成什么了";
        --   2. 复盘 —— 撤回、好友申请这类"仅记录"事件也留痕;
        --   3. 幂等排查 —— 重复推送时能看到同 event_id 出现几次。
        -- should_respond: 1=进入了 agent 流程, 0=仅记录
        -- summary: 人类可读的一句话描述(免去解析 payload 才能看懂)
        -- ============================================================
        CREATE TABLE IF NOT EXISTS events (
            id              TEXT PRIMARY KEY,       -- 事件 ID(与 Event.event_id 一致)
            event_type      TEXT NOT NULL,          -- 事件类别: message/notice/request/instruction/timer…
            source          TEXT DEFAULT '',        -- 来源: qq/desktop/agent/scheduler
            should_respond  INTEGER DEFAULT 1,      -- 是否进入 agent 流程(0/1)
            conversation_id TEXT DEFAULT '',        -- 关联会话(可空)
            summary         TEXT DEFAULT '',        -- 一句话描述(人类可读)
            payload         TEXT DEFAULT '',        -- 原始载荷 JSON 快照
            created_at      TEXT NOT NULL
        );
        -- 注意: events 表的索引在 _migrate() 里创建 ——
        -- 老库的 events 表可能还没有 conversation_id 列,先建索引会报错。

        -- ============================================================
        -- scheduled_events: 定时唤醒(agent 的"等待"工具登记在这里)
        -- agent 说"等 10 分钟再催"时: 写一行 due_at=now+600,status=pending;
        -- 后台 Scheduler 每 1 秒轮询,到期的标记 fired 并发 timer 事件,
        -- 该会话从 checkpoint 恢复继续跑图 —— "时间驱动"能力就靠这张表。
        -- 重启不丢: 服务重启后 pending 记录依然有效。
        -- ============================================================
        CREATE TABLE IF NOT EXISTS scheduled_events (
            id              TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            due_at          TEXT NOT NULL,           -- 到期时间(UTC ISO)
            note            TEXT DEFAULT '',         -- 唤醒提示(如"等小号回复10分钟")
            status          TEXT DEFAULT 'pending',  -- pending / fired / cancelled
            created_at      TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_scheduled_due
            ON scheduled_events (status, due_at);

        -- ============================================================
        -- dispatches: 外部调度任务(主 agent / 其他系统派发进来的活儿)
        -- 场景: 主 agent 让本服务"把这条消息发到某会话"或"围绕这个指令推进"。
        -- 设计要点:
        --   · idempotency_key: 调用方给的幂等键 —— 重复投递不重复执行;
        --   · status: accepted → running → done/failed/expired/busy;
        --   · result: 结构化产物(发出了哪条消息、回复文本、错误原因);
        --   · caller + context: 全量留痕,可追溯"谁让发的这条消息"。
        -- ============================================================
        CREATE TABLE IF NOT EXISTS dispatches (
            task_id         TEXT PRIMARY KEY,
            caller          TEXT DEFAULT '',        -- 调用方标识(审计用)
            kind            TEXT NOT NULL,          -- handle_message / send_message / task / note
            conversation_id TEXT DEFAULT '',        -- 目标会话(可能由三元组解析而来)
            payload         TEXT DEFAULT '',        -- 原始请求 JSON 快照
            status          TEXT DEFAULT 'accepted',-- accepted/running/done/failed/expired/busy
            result          TEXT DEFAULT '',        -- 结果 JSON(产物或错误)
            error           TEXT DEFAULT '',        -- 失败原因(人类可读)
            idempotency_key TEXT DEFAULT '',        -- 幂等键(去重用)
            deadline        TEXT DEFAULT '',        -- 期望完成时间(UTC ISO)
            result_mode     TEXT DEFAULT 'none',    -- poll / callback / none
            callback_url    TEXT DEFAULT '',        -- result_mode=callback 时的回调地址
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL
        );
        -- 幂等键去重(部分唯一索引: 空字符串不参与,允许多条无键任务)
        CREATE UNIQUE INDEX IF NOT EXISTS idx_dispatches_idem
            ON dispatches (idempotency_key) WHERE idempotency_key != '';
        CREATE INDEX IF NOT EXISTS idx_dispatches_status
            ON dispatches (status, created_at);

        -- ============================================================
        -- report_queue: 上报队列(类消息中间件的本地形态)
        -- 场景: 监视中的会话出现"重要消息"时,把候选投进队列;
        --       由**上游 agent 自己来取**(claim),取了之后自行决定要不要报给用户。
        -- 为什么不直接推: 上报的决策权在上游;本服务只负责"发现 + 排队"。
        -- 语义对齐真 MQ:
        --   · 至少一次: claim 带租约,超时自动回 pending 重投;
        --   · 死信: attempts 超限 → dead(可查、可重放);
        --   · 幂等: 同 (conversation_id, dedup_key) 不重复入队。
        -- lane: urgent(立即) / normal(进摘要批) / question(HITL 请示) / digest(批量汇总)
        -- ============================================================
        CREATE TABLE IF NOT EXISTS report_queue (
            id              TEXT PRIMARY KEY,
            lane            TEXT DEFAULT 'normal',   -- urgent / normal / question / digest
            conversation_id TEXT DEFAULT '',         -- 来源会话(可空: 系统级上报)
            message_ids     TEXT DEFAULT '',         -- 来源消息 ID(逗号分隔,可追溯)
            payload         TEXT DEFAULT '',         -- JSON: 摘要/命中规则/原文片段
            dedup_key       TEXT DEFAULT '',         -- 幂等键(同一批消息不重复入队)
            status          TEXT DEFAULT 'pending',  -- pending/claimed/acked/dropped/dead
            attempts        INTEGER DEFAULT 0,       -- 认领次数(超限进 dead)
            claimed_by      TEXT DEFAULT '',         -- 认领者标识(哪个上游 agent)
            lease_expires_at TEXT DEFAULT '',        -- 租约到期时间(过期可被重新认领)
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            acked_at        TEXT DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS idx_report_queue_status
            ON report_queue (status, created_at);
        CREATE INDEX IF NOT EXISTS idx_report_queue_lane
            ON report_queue (lane, status);

        -- ============================================================
        -- memory_batches: 攒批记忆的处理进度(宿主侧 checkpoint)
        -- 背景: 长期记忆不再逐条写入(那样碎成一堆"嗯""好的"),
        --       而是按窗口攒批、由 agent 总结后再写。
        --       Doppel 明确把"调度与 checkpoint 存储"归宿主,这张表就是那份状态。
        -- last_message_at: 上次处理的最后一条消息时间(续读游标)
        -- ============================================================
        CREATE TABLE IF NOT EXISTS memory_batches (
            conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
            cursor          TEXT DEFAULT '',         -- 续读游标(Doppel BatchCheckpoint.cursor)
            last_message_at TEXT DEFAULT '',         -- 已处理到的消息时间(水位线)
            last_run_at     TEXT DEFAULT '',         -- 上次执行时间
            last_status     TEXT DEFAULT '',         -- ok / empty / error
            last_error      TEXT DEFAULT '',
            pending_count   INTEGER DEFAULT 0,       -- 待处理条数(攒批计数)
            metadata        TEXT DEFAULT ''          -- 预留: 窗口起止等
        );
        """
    )
    conn.commit()

    # 建表后补齐老库缺失的列(新增列对已有表不会自动生效)
    _migrate(conn)


# 迁移表: {表名: [(列名, 列定义), ...]}
# 说明: 只用"缺什么补什么"的方式 —— SQLite 的 ADD COLUMN 是廉价操作。
# 如果将来需要改列类型/删列,再引入正式的版本化迁移。
_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    # events 表在 v2 消息模型重构时扩充了审计字段
    "events": [
        ("source", "TEXT DEFAULT ''"),
        ("should_respond", "INTEGER DEFAULT 1"),
        ("conversation_id", "TEXT DEFAULT ''"),
        ("summary", "TEXT DEFAULT ''"),
    ],
    # conversations 表引入会话策略(值守/监视/上报的开关,默认全关)
    "conversations": [
        ("monitor", "INTEGER DEFAULT 0"),
        ("reply_mode", "TEXT DEFAULT 'off'"),
        ("alert_enabled", "INTEGER DEFAULT 0"),
        ("alert_keywords", "TEXT DEFAULT '[]'"),
        ("require_human_confirmation", "INTEGER DEFAULT 1"),
        ("digest_window_seconds", "INTEGER DEFAULT 1800"),
        ("digest_max_messages", "INTEGER DEFAULT 20"),
        ("allowed_tools", "TEXT DEFAULT ''"),
    ],
}


def _migrate(conn: sqlite3.Connection) -> None:
    """为已有表补齐缺失列(幂等: 已存在则跳过)。"""
    for table, columns in _MIGRATIONS.items():
        # PRAGMA table_info 返回该表所有列;老库可能没有这张表(新库刚建好则有)
        existing = {
            row[1]  # 第 1 列是列名
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if not existing:
            continue  # 表不存在(理论上不会,建表语句在上面)

        for column, definition in columns:
            if column in existing:
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    # events 的索引放在补列之后创建(见建表处注释)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_conv_time ON events (conversation_id, created_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_type_time ON events (event_type, created_at)"
    )

    conn.commit()
