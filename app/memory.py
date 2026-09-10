# =============================================================================
# memory.py - 长期记忆适配层(对接 Doppel)
# -----------------------------------------------------------------------------
# 为什么需要适配层:
#   Memo Echo 与 Doppel 的模型很接近,但不是同一个:
#     · v2 的会话标识是 (platform, chat_type, external_id);
#       Doppel 的要 5 元组 (user_id, agent_id, platform, chat_type, chat_id)
#       —— 其中 user_id/agent_id 承担**多租户隔离**职责(不同号主、不同机器人互不可见)。
#     · v2 的消息在 messages 表(带 role/source),Doppel 的是 ChatMessage(带 actor)。
#     · Doppel 全异步,v2 的工具层同步。
#   适配层把这些差异收口在一个文件里 —— Doppel 将来改 API,只需改这里。
#
# 多租户隔离(本模块最重要的职责):
#   代理场景下,同一实例可能服务多个号主/多个机器人。scope 的五元组保证:
#     · 不同 agent_id(机器人号)的记忆不串;
#     · 不同 user_id(号主)的记忆不串;
#     · 不同 chat_id(联系人)的私聊记忆不串。
#   这是"代理账号"这类应用最不能出错的地方,所以 scope 的构造集中在这里。
#
# 降级策略(重要):
#   记忆是**增强能力**,不是主链路。Doppel 不可用(未安装/初始化失败/查询异常)
#   时必须降级为"没有记忆",绝不能因此让对话失败。
# =============================================================================

from __future__ import annotations

from datetime import datetime
from typing import Any

from .config import get_settings

# Doppel 是可选依赖: 装了才启用记忆能力。
# 用 try 包住 import —— 没装时模块仍可导入,只是 enabled=False。
try:
    from doppel_memory import ChatMessage, DoppelClient, MemoryScope

    _DOPPEL_AVAILABLE = True
except ImportError:  # pragma: no cover - 取决于运行环境是否安装
    ChatMessage = None  # type: ignore[assignment]
    DoppelClient = None  # type: ignore[assignment]
    MemoryScope = None  # type: ignore[assignment]
    _DOPPEL_AVAILABLE = False


# ---------------------------------------------------------------------------
# 单例客户端
# ---------------------------------------------------------------------------
_client: Any = None
_init_error: str = ""


def is_enabled() -> bool:
    """记忆功能是否可用(装了 Doppel + 配置开启 + 初始化成功)。"""
    return _DOPPEL_AVAILABLE and get_settings().doppel_enabled


async def get_client() -> Any:
    """返回 Doppel 客户端单例(惰性初始化)。

    首次调用时创建客户端并打开数据库。初始化失败会记录原因并返回 None ——
    调用方据此降级(不做记忆),而不是抛异常打断对话。

    注意: 必须在异步上下文里调用(Doppel 的 SQLite 后端是异步的)。
    """
    global _client, _init_error

    if _client is not None:
        return _client
    if not is_enabled():
        return None

    settings = get_settings()
    try:
        _client = DoppelClient(
            backend=settings.doppel_backend,
            database=str(settings.data_dir / settings.doppel_db_name),
        )
        print(f"[memory] Doppel 已启用 (backend={settings.doppel_backend}, db={settings.doppel_db_name})")
    except Exception as exc:  # noqa: BLE001 - 初始化失败要降级,不能影响主流程
        _init_error = f"{type(exc).__name__}: {exc}"
        print(f"[memory] Doppel 初始化失败,记忆功能降级: {_init_error}")
        _client = None

    return _client


async def close_client() -> None:
    """关闭客户端(应用退出时调用)。"""
    global _client
    if _client is not None:
        try:
            await _client.close()
        except Exception:  # noqa: BLE001 - 退出时的清理失败无需打扰用户
            pass
        _client = None


def init_error() -> str:
    """返回初始化失败原因(供状态页排障)。"""
    return _init_error


# ---------------------------------------------------------------------------
# scope 构造(多租户隔离的关键)
# ---------------------------------------------------------------------------
def build_scope(conversation: dict[str, Any]) -> Any:
    """把 v2 的会话信息映射成 Doppel 的 MemoryScope。

    映射关系:
      user_id    ← 配置里的 owner_user_id(被代理的号主)
      agent_id   ← 会话所属的机器人 QQ 号(多机器人场景下隔离用)
      platform   ← 会话平台(qq / desktop)
      chat_type  ← 会话类型(private / group / thread)
      chat_id    ← 会话的 external_id(对方 QQ / 群号 / 桌面线程 ID)

    为什么 chat_id 用 external_id:
      它才是"跟谁在聊"的稳定标识;conversation.id 是内部 uuid,
      换库/重建后会变,不适合当长期记忆的命名空间。
    """
    settings = get_settings()
    return MemoryScope(
        user_id=settings.owner_user_id or "local-owner",
        agent_id=settings.agent_id or settings.bot_qq or "memo-echo",
        platform=str(conversation.get("platform") or "qq"),
        chat_type=str(conversation.get("chat_type") or "private"),
        chat_id=str(conversation.get("external_id") or conversation.get("id") or ""),
    )


# ---------------------------------------------------------------------------
# 写入
# ---------------------------------------------------------------------------
def _actor_of(role: str, source: str, is_self: bool = False) -> str:
    """把 v2 的消息角色映射成 Doppel 的 actor。

    映射依据(影响 Doppel 的"事实权威"判定):
      assistant / outbound → agent(机器人说的话)
      user / inbound       → contact(联系人说的话)
      system               → system
    """
    if is_self or source == "outbound" or role == "assistant":
        return "agent"
    if source == "system" or role == "system":
        return "system"
    return "contact"


async def remember_message(
    conversation: dict[str, Any],
    *,
    role: str,
    content: str,
    created_at: str = "",
    message_id: str = "",
    source: str = "inbound",
    parts: list[dict[str, Any]] | None = None,
) -> bool:
    """把一条对话消息写入长期记忆。

    ⚠️ 生产链路**不再调用本函数**:逐条写会把记忆碎成一堆"嗯""好的"。
    长期记忆统一由攒批总结写入(见 app/batches.py: 攒够条数或消息静止后
    总结一批再写)。本函数保留给"批量导入/回填"这类一次性场景
    (app/memory.remember_batch 与 scripts/backfill_memory.py 仍在用)。

    返回是否写入成功。任何异常都降级为 False(不抛出)——
    记忆是增强能力,不能因为它失败就让对话流程崩掉。
    """
    client = await get_client()
    if client is None or not content.strip():
        return False

    try:
        scope = build_scope(conversation)
        message = ChatMessage.of(
            _actor_of(role, source),
            content,
            created_at or datetime.now().astimezone().isoformat(),
            message_id=message_id,
            parts=parts or None,   # v2 的内容段可原样传入(Doppel 有对应的 parts)
        )
        result = await client.ingest(scope, message)
        # Doppel 的写入结果带状态: created/updated/duplicate/...
        # duplicate 不算失败(幂等),这里统一按成功处理。
        return True
    except Exception as exc:  # noqa: BLE001 - 记忆写入失败必须降级
        print(f"[memory] 写入失败(已降级): {type(exc).__name__}: {exc}")
        return False


async def remember_batch(
    conversation: dict[str, Any],
    messages: list[dict[str, Any]],
) -> dict[str, Any]:
    """批量写入历史消息(用于冷启动/历史回填)。

    返回 {"accepted": n, "skipped": n, "error": str}。
    """
    client = await get_client()
    if client is None:
        return {"accepted": 0, "skipped": 0, "error": "记忆功能未启用"}

    try:
        scope = build_scope(conversation)
        chat_messages = [
            ChatMessage.of(
                _actor_of(str(m.get("role") or ""), str(m.get("source") or "")),
                str(m.get("content") or ""),
                str(m.get("created_at") or ""),
                message_id=str(m.get("id") or ""),
            )
            for m in messages
            if str(m.get("content") or "").strip()
        ]
        if not chat_messages:
            return {"accepted": 0, "skipped": 0, "error": ""}

        result = await client.ingest_messages(scope, chat_messages)
        return {"accepted": int(result.get("accepted", 0)), "skipped": int(result.get("skipped", 0)), "error": ""}
    except Exception as exc:  # noqa: BLE001
        return {"accepted": 0, "skipped": 0, "error": f"{type(exc).__name__}: {exc}"}


# ---------------------------------------------------------------------------
# 检索
# ---------------------------------------------------------------------------
async def recall(
    conversation: dict[str, Any],
    query: str,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """检索该会话相关的长期记忆。

    返回简化后的结果列表: [{"fact": ..., "actor": ..., "authority": ..., "at": ...}]
    检索失败或无结果都返回空列表(调用方按"没有记忆"处理)。
    """
    client = await get_client()
    if client is None or not query.strip():
        return []

    try:
        scope = build_scope(conversation)
        hits = await client.recall(query, [scope], limit=limit)
        return [_simplify(hit) for hit in hits]
    except Exception as exc:  # noqa: BLE001 - 检索失败降级为空
        print(f"[memory] 检索失败(已降级): {type(exc).__name__}: {exc}")
        return []


def _simplify(hit: Any) -> dict[str, Any]:
    """把 Doppel 的 RecallResult 压成 v2 内部用的精简字典。

    只保留喂给 LLM 需要的字段:
      fact      记忆内容
      actor     谁说的(owner/contact/agent)—— 决定可信度
      authority 事实权威等级
      at        时间(用于时序判断)
      score     相关度(有则带上)
    """
    return {
        "fact": getattr(hit, "fact", "") or "",
        "actor": getattr(hit, "actor", "") or "",
        "authority": str(getattr(hit, "authority", "") or ""),
        "at": str(getattr(hit, "at", "") or ""),
        "memory_id": getattr(hit, "memory_id", "") or "",
        "score": getattr(hit, "score", None),
    }


def format_for_prompt(hits: list[dict[str, Any]]) -> str:
    """把检索结果格式化成可拼进 system prompt 的文本块。

    设计要点:
      · 标注说话人(owner=号主自己说 / contact=联系人说 / agent=机器人说过),
        让模型知道每条记忆的来源与可信度,而不是当成一律为真;
      · 带上时间,便于处理"事实会过期"的场景(如课表变更);
      · 空结果返回空串(调用方据此不拼这一段)。
    """
    if not hits:
        return ""

    actor_label = {
        "owner": "号主",
        "human_self": "号主",
        "contact": "对方",
        "peer": "对方",
        "agent": "我(机器人)",
        "system": "系统",
    }

    lines: list[str] = []
    for hit in hits:
        fact = (hit.get("fact") or "").strip()
        if not fact:
            continue
        who = actor_label.get(hit.get("actor") or "", hit.get("actor") or "未知")
        when = (hit.get("at") or "")[:16]  # 取到分钟即可
        prefix = f"[{who}{(' ' + when) if when else ''}]"
        lines.append(f"- {prefix} {fact}")

    if not lines:
        return ""

    return (
        "以下是你记得的、与当前对话相关的长期记忆"
        "(括号标注了是谁说的与时间,可信度不同,请自行判断):\n"
        + "\n".join(lines)
    )
