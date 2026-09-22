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
#
# 检索的现实(SQLite 后端,实测于 doppel 0.8.3):
#   Doppel 的检索是**字面子串匹配** —— 先试 FTS 短语 AND,失败回落
#   `LIKE '%整串%'`。而我们的调用方拿的是**对方刚说的整句话**
#   ("明天组会几点?"),它永远不可能恰好是某条记忆的子串 ⇒ 检索恒为空,
#   而且失败是静默的(空列表 = "没有记忆")。所以本模块在 recall() 里做了两件事:
#     · 拆词: 把整句拆成若干片段分别检索再合并,让"组会"这类实词能命中;
#     · 兜底: 仍无结果时,取该 scope **最近的记忆** —— 同一会话最近的记忆
#       本来就跟当前话题最相关,总好过一条都不给。
#   语义检索(向量)是 Doppel 更高版本的能力(需要宿主实现 SemanticIndex),
#   不是本适配层能单方面解决的 —— 见 docs/conversation-policy.md §6。
# =============================================================================

from __future__ import annotations

import re
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
        # Doppel 的写入结果带状态: created/updated/duplicate/...
        # duplicate 不算失败(幂等),这里统一按成功处理。
        await client.ingest(scope, message)
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
# ---------------------------------------------------------------------------
# 检索词拆分(应对"字面子串匹配"的检索后端)
# ---------------------------------------------------------------------------
# 全角/标点/空白都当分隔符;中文没有空格,所以按"片段"处理而不是按词。
_SPLIT = re.compile(r"[^\w\u4e00-\u9fff]+")
# 全是这些字的片段没有检索价值("的""了吗""这就要"). 注意: 只用来过滤
# **整段都是虚词**的片段,不做分词 —— 我们没有分词器,也不该在这里造一个。
_STOP_CHARS = frozenset(
    "的了呢吗吧啊呀哦嗯是在有我你他她它们和跟给把被这那个就都也还要不没很太会能可以点几多少什么怎哪时上下中们之与及或者然后所以如果因为"
)
# 不会出现在实词**首尾**的字: 语气助词 + 第一/二人称代词。
# 用来砍掉跨词边界的碎片("们明""午的""的组"这些都不是词,却会占检索名额)。
#
# 这个集合**刻意收得很窄**: 每砍一个字符就可能误杀实词 ——
# "会"砍掉会连"组会/开会/机会"一起砍,"在"砍掉会误伤"现在/存在",
# "要"砍掉会误伤"重要","能"砍掉会误伤"功能"。漏掉一个实词 = 那条记忆找不回来,
# 而多留一个碎片只是多一次本地查询。所以宁可留噪声。
_EDGE_STOP = frozenset("的了呢吗吧啊呀哦嗯们我你他她它")


def query_terms(text: str, *, max_terms: int = 14) -> list[str]:
    """把一句话拆成若干"可能命中"的检索词。

    顺序: 整段 → 二字片段 → 三字片段。中文实词以二字为主("组会""明天""课表"),
    所以二字片段优先于三字片段 —— 否则长消息里那几个三字片段会把名额占满。

    纯本地字符串处理,零模型成本;检索是本地 SQLite 查询,多查几次也不花钱。
    名额给得比较宽(默认 14): 多试几个片段只是多几次本地查询,
    而漏掉一个实词就意味着这条记忆找不回来。真正花时间的是模型,不是这里。
    """
    terms: list[str] = []
    for run in _SPLIT.split(text or ""):
        if not run or all(ch in _STOP_CHARS for ch in run):
            continue
        terms.append(run)
        for size in (2, 3):
            for start in range(len(run) - size + 1):
                gram = run[start : start + size]
                if all(ch in _STOP_CHARS for ch in gram):
                    continue
                if gram[0] in _EDGE_STOP or gram[-1] in _EDGE_STOP:
                    continue      # 跨词边界的碎片,不是词
                terms.append(gram)
    ordered: list[str] = []
    for term in terms:
        if term and term not in ordered:
            ordered.append(term)
    if not ordered:
        return []
    # 顺序: 整段 → 二字片段 → 三字片段(理由见 docstring)
    head = [ordered[0]]
    twos = [item for item in ordered[1:] if len(item) == 2]
    rest = [item for item in ordered[1:] if len(item) != 2]
    return (head + twos + rest)[:max_terms]


# 冲突标记的记忆类型(Doppel 整理器写出来的"这件事有矛盾"的记录)。
# 它不是事实,是给整理/通知环节看的标记 —— 检索时要把它们滤掉。
CONFLICT_KIND = "memory_conflict"


def _dedupe(hits: list[Any]) -> list[Any]:
    """按记忆 ID(退化到内容)去重,保持先后顺序。"""
    seen: set[str] = set()
    unique: list[Any] = []
    for hit in hits:
        key = str(getattr(hit, "memory_id", "") or getattr(hit, "fact", ""))
        if key in seen:
            continue
        seen.add(key)
        unique.append(hit)
    return unique


async def recall(
    conversation: dict[str, Any],
    query: str,
    *,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """检索该会话相关的长期记忆。

    返回简化后的结果列表: [{"fact": ..., "actor": ..., "authority": ..., "at": ...}]
    检索失败或无结果都返回空列表(调用方按"没有记忆"处理)。

    策略见模块头"检索的现实": 拆词多查 + 最近记忆兜底。
    两类结果**先命中后兜底**排序 —— 与查询相关的排在前面。
    """
    client = await get_client()
    if client is None or not (query or "").strip():
        return []

    try:
        scope = build_scope(conversation)
        hits: list[Any] = []
        for term in query_terms(query):
            if len(hits) >= limit:
                break
            hits.extend(await client.recall(term, [scope], limit=limit))
        hits = _dedupe(hits)

        # 兜底: 拆词也没命中时,给"这个会话最近的记忆" ——
        # 宁可给几条相关的旧事,也好过让模型以为什么都不记得。
        if not hits:
            hits = _dedupe(await client.recall("", [scope], limit=limit))

        # 冲突标记是"给整理环节看的记录",不是事实: 它的内容是
        # "Unresolved personal-memory conflict in topic X across N active claims."
        # 混进提示词只会让模型以为号主在说胡话。冲突本身已经通过上报队列
        # 报给号主确认了(见 app/consolidation.py)。
        hits = [hit for hit in hits if str(getattr(hit, "kind", "") or "") != CONFLICT_KIND]
        return [_simplify(hit) for hit in hits[:limit]]
    except Exception as exc:  # noqa: BLE001 - 检索失败降级为空
        print(f"[memory] 检索失败(已降级): {type(exc).__name__}: {exc}")
        return []


async def known_topic_keys(conversation: dict[str, Any], *, limit: int = 200) -> list[str]:
    """该会话已有记忆用过的槽位名(供总结器复用,避免同义新造)。

    槽位名是**字符串精确匹配**的: 同一件事这批评成"课表"、下批评成"课程安排",
    两条记忆就永远不会被比对 —— 过期的那条会一直躺在检索结果里。
    所以每次总结前把已有槽位名带进提示词,让模型尽量复用。

    读的是"最早的一批"(scan 从旧到新): 槽位往往在早期就定下来了,
    后面的记录大多在复用它们。失败返回空列表(提示词少一段而已)。
    """
    client = await get_client()
    if client is None:
        return []
    try:
        page = await client.store.scan(build_scope(conversation), limit=max(1, limit))
    except Exception as exc:  # noqa: BLE001 - 拿不到提示就算了,不影响总结
        print(f"[memory] 读取已有槽位失败(已忽略): {type(exc).__name__}: {exc}")
        return []

    keys: list[str] = []
    for record in getattr(page, "records", []) or []:
        key = str((getattr(record, "metadata", None) or {}).get("topic_key") or "").strip()
        if key and key not in keys:
            keys.append(key)
    return keys


async def consolidate(conversation: dict[str, Any], *, checkpoint: Any = None) -> dict[str, Any]:
    """对某个会话跑一轮"事实过期/冲突"整理(确定性,零模型成本)。

    用的是 Doppel 的 ConsolidationRunner + DeterministicMemoryConsolidator:
      · merge   —— 同一槽位上完全相同的重复说法,合并成一条;
      · correct —— 新说法带**明确的订正标记**(聊天里说了"改了/取消了")时,
                   把旧说法置为 superseded(它就不再被检索出来了);
      · conflict —— 同一槽位上说法互相矛盾、又没有明确订正证据时,
                   保留双方 + 写一条 conflict 标记,并把冲突原样报上去让人确认。

    返回 {"ok", "reason", "operations": {...}, "conflicts": [...], "checkpoint", "complete"}。
    任何失败都返回 ok=False(记忆是增强能力,整理失败不能影响对话)。
    """
    client = await get_client()
    if client is None:
        return {"ok": False, "reason": "记忆功能未启用", "operations": {}, "conflicts": []}

    try:
        from doppel_memory import (
            ConsolidationCheckpoint,
            ConsolidationOperation,
            ConsolidationRunner,
            DeterministicMemoryConsolidator,
        )
    except ImportError as exc:  # pragma: no cover - 取决于 Doppel 版本
        return {"ok": False, "reason": f"当前 Doppel 版本不支持整理: {exc}", "operations": {}, "conflicts": []}

    bound_checkpoint = None
    if checkpoint:
        try:
            bound_checkpoint = (
                checkpoint
                if isinstance(checkpoint, ConsolidationCheckpoint)
                else ConsolidationCheckpoint.model_validate(checkpoint)
            )
        except Exception as exc:  # noqa: BLE001 - 旧检查点不兼容时从头开始,不中断
            print(f"[memory] 整理检查点不可用,将重新开始: {type(exc).__name__}: {exc}")
            bound_checkpoint = None

    try:
        scope = build_scope(conversation)
        runner = ConsolidationRunner(client.store)
        result = await runner.run_once(
            DeterministicMemoryConsolidator(), scope, checkpoint=bound_checkpoint
        )
    except Exception as exc:  # noqa: BLE001 - 整理失败降级,不影响其它流程
        return {
            "ok": False,
            "reason": f"{type(exc).__name__}: {exc}",
            "operations": {},
            "conflicts": [],
        }

    operations: dict[str, int] = {}
    conflicts: list[dict[str, Any]] = []
    # 注意: run.actions 是**执行结果**(ConsolidationActionResult),
    # 冲突的细节(槽位、来源)在**计划**里(run.plan.actions)——
    # 计划里才有 proposal 与 explanation。
    plan_actions = {str(getattr(item, "decision_id", "")): item for item in result.plan.actions}
    for action in result.actions:
        name = str(getattr(action, "operation", "") or "")
        operations[name] = operations.get(name, 0) + 1
        if name != ConsolidationOperation.CONFLICT:
            continue
        planned = plan_actions.get(str(getattr(action, "decision_id", "")))
        proposal = getattr(planned, "proposal", None) if planned is not None else None
        metadata = dict(getattr(proposal, "metadata", None) or {})
        conflict = dict(metadata.get("conflict") or {})
        topic = str(conflict.get("topic_key") or "")
        if not topic:
            # 兜底: 冲突标记的内容里带着槽位名("...conflict in topic X across N...")
            content = str(getattr(proposal, "content", "") or "")
            if " in topic " in content:
                topic = content.split(" in topic ", 1)[1].split(" across ", 1)[0].strip()
        conflicts.append(
            {
                "topic_key": topic,
                "reason": str(getattr(planned, "explanation", "") or "") if planned else "",
                "memory_ids": [
                    str(item.get("memory_id") or "")
                    for item in (conflict.get("source_memories") or [])
                ]
                or [str(item.memory_id) for item in (getattr(planned, "sources", None) or [])],
            }
        )

    return {
        "ok": True,
        "reason": "",
        "operations": operations,
        "conflicts": conflicts,
        "checkpoint": result.committable_checkpoint,
        "complete": result.committable_checkpoint is not None,
        "errors": [f"{item.stage}: {item.error_type}" for item in (result.errors or [])],
    }


def _simplify(hit: Any) -> dict[str, Any]:
    """把 Doppel 的 RecallResult 压成 v2 内部用的精简字典。

    只保留喂给 LLM 需要的字段:
      fact      记忆内容
      actor     谁说的(owner/contact/agent)—— 决定可信度
      authority 事实权威等级
      at        时间(用于时序判断)
      score     相关度(有则带上)

    时间字段名要跟着 Doppel 走: RecallResult 上是 `valid_at`(事实生效时间)与
    `extracted_at`(抽取时间),**没有** `at` —— 之前取 `at` 恒为空,
    于是"课表变更"这类时效信息在提示词里从来没带过时间。
    """
    at = getattr(hit, "valid_at", None) or getattr(hit, "extracted_at", None)
    if hasattr(at, "astimezone"):
        # Doppel 存 UTC;提示词里给人看的时间要转成本机时区 ——
        # 模型要拿它跟"明天下午三点"这类本地时间做比较,差 8 小时会算错。
        at = at.astimezone()
    return {
        "fact": getattr(hit, "fact", "") or "",
        "actor": getattr(hit, "actor", "") or "",
        "authority": str(getattr(hit, "authority", "") or ""),
        "at": at.isoformat() if hasattr(at, "isoformat") else str(at or ""),
        "memory_id": getattr(hit, "memory_id", "") or "",
        "score": getattr(hit, "score", None) or getattr(hit, "similarity", None),
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
