# =============================================================================
# batches.py - 攒批记忆(长期记忆的唯一写入入口)
# -----------------------------------------------------------------------------
# 为什么需要攒批:
#   逐条写记忆会把长期记忆碎成一堆"嗯""好的" —— 随便三言两语就是一条 EVENT,
#   检索时噪声远大于信息。正确做法是:消息先攒着,攒够一批(或对方不说话了)
#   让 agent 总结一次,只把**值得长期记住**的东西写进去;一批里没价值就一条不写。
#
# 与 Doppel 的分工(读 D:\project\Doppel\doppel_memory\batch.py 得出的契约):
#   · 历史由**宿主**提供(ScopedHistoryReader):Doppel 不碰我们的 messages 表;
#   · 检查点由**宿主**持久化(本项目的 memory_batches 表):
#     BatchRunResult.committable_checkpoint 只有在"这一轮没有任何错误"时才非空,
#     宿主据此决定要不要推进水位线;为空 = 下次重读同一批(至少一次语义)。
#   · 分页有硬校验(GuardedHistoryReader):
#       非空页必须返回**非空且与入参不同**的 next_cursor,否则抛
#       HistoryReaderContractError;has_more=True 必须带消息;单页不得超过 limit。
#     本模块的游标是 (created_at, id) 复合键 —— 同一条消息在分页边界上
#     也不会被读两遍或漏掉,且每次读都严格向后推进。
#   · 任务(MemoryBatchTask)只产出 MemoryProposal,**不写库**;
#     写库由 Doppel 的 ProposalWriter 负责(含幂等去重与 scope 授权)。
#
# 水位线(两层,都落在 memory_batches 表):
#   cursor           —— Doppel BatchCheckpoint 的续读游标(复合键 "created_at|id")
#   last_message_at  —— 已处理到的消息时间(人可读的水位线,用于触发判定)
#   cursor 为空时(首次运行、或任务语义变更被重置)退回用 last_message_at 做
#   **严格大于**的下界,两者不会互相矛盾。改历史选择语义时请升 TASK_VERSION,
#   并清空对应会话的 cursor(见 _checkpoint_of 的版本检查)。
#
# 触发条件(见 is_due):
#   · conversations.monitor=1 才参与;
#   · 待处理条数 ≥ digest_max_messages(默认 20)—— 聊得热闹就先总结;
#   · 或 消息已静止超过 digest_window_seconds(默认 1800)—— 对方不说了就收尾。
#
# 降级策略(与 app/memory.py 一致):
#   记忆是增强能力,不是主链路。Doppel 未安装/未启用/总结失败都只记录状态,
#   绝不抛给调度循环 —— 攒批失败 = 这一批不写,下次扫描重试。
# =============================================================================

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence

from . import memory as memory_layer
from .config import get_settings
from .db import get_connection
from .services import policy as policy_service

# Doppel 是可选依赖(与 app/memory.py 同一套判断): 没装时本模块仍可导入,
# 只提供"纯宿主侧"的逻辑(触发判定/游标读写),实际攒批直接跳过。
try:
    from doppel_memory import (
        BatchCheckpoint,
        BatchProposalPlan,
        ChatMessage,
        HistoryPage,
        HistoryWindow,
        MemoryProposal,
    )

    _DOPPEL_AVAILABLE = True
except ImportError:  # pragma: no cover - 取决于运行环境是否安装
    BatchCheckpoint = None  # type: ignore[assignment]
    BatchProposalPlan = None  # type: ignore[assignment]
    ChatMessage = None  # type: ignore[assignment]
    HistoryPage = None  # type: ignore[assignment]
    HistoryWindow = None  # type: ignore[assignment]
    MemoryProposal = None  # type: ignore[assignment]
    _DOPPEL_AVAILABLE = False


# ---------------------------------------------------------------------------
# 任务标识
# ---------------------------------------------------------------------------
# Doppel 把检查点绑定到 (task_name, task_version, checkpoint_schema_version):
# 三者变了就必须迁移或重置检查点。我们把它一并存进 memory_batches.metadata,
# 这样"改了总结语义却沿用旧游标"这种事故能被发现并重置。
TASK_NAME = "memo-echo.conversation-digest"
TASK_VERSION = "1"
CHECKPOINT_SCHEMA_VERSION = 1

# 单页条数(Doppel 的 GuardedHistoryReader 默认上限是 2000,这里取小值:
# 我们真正的批很小,页大只是浪费一次全表扫描)
PAGE_SIZE = 200
# 单轮最多总结多少条消息。存在上限的原因:
#   服务停机很久后可能积压成千上万条消息,一口气全塞给 LLM 既慢又浪费 token,
#   而且中途失败就整批重来。超过上限时本轮只处理最早的一批,游标照常推进,
#   下一次扫描接着处理 —— 进度不会丢。
MAX_MESSAGES_PER_RUN = 1000
# 单条消息喂给总结器时的截断长度(防止一条超长消息吃掉整个上下文)
MAX_MESSAGE_CHARS = 500
# 一次总结最多产出多少条记忆(LLM 偶尔会过度输出,这里兜底)
MAX_NOTES_PER_RUN = 20
# 单条记忆内容长度上限
MAX_NOTE_CHARS = 200


# ---------------------------------------------------------------------------
# 总结器接口(可注入)
# ---------------------------------------------------------------------------
class BatchNote:
    """总结器产出的一条记忆候选。

    刻意用与 Doppel 无关的轻量结构:
      · 总结器是"业务逻辑",不该被 Doppel 的数据模型绑住(将来换记忆后端只改本文件);
      · 测试可以注入假实现,完全不联网。

    后四个字段是**给"事实过期/冲突"用的**(见 app/consolidation.py):
      · topic_key   —— 这条说的是哪个"槽位"(课表 / 口味偏好 / 周末安排)。
                       同一个槽位上的新旧说法才能被比对、被替换;留空 = 一次性的事。
      · revision_kind —— assertion 普通陈述 / correction 明确订正 / retraction 撤回。
                       只有聊天记录里**明说了改了**才算 correction —— 这是 Doppel
                       的硬要求(不能凭"更新"就覆盖旧事实,那是在替号主改口供)。
      · temporal_status —— current 现在成立 / planned 将来的安排 / historical 已过去。
                       现在与将来是**不同槽位**,不能互相替换。
      · memory_type —— fact/state/preference/relationship/plan/commitment/episode。
    """

    __slots__ = (
        "content",
        "kind",
        "actor",
        "importance",
        "tags",
        "topic_key",
        "revision_kind",
        "temporal_status",
        "memory_type",
    )

    def __init__(
        self,
        content: str,
        *,
        kind: str = "fact",
        actor: str = "contact",
        importance: float = 0.5,
        tags: Sequence[str] = (),
        topic_key: str = "",
        revision_kind: str = "assertion",
        temporal_status: str = "unknown",
        memory_type: str = "",
    ) -> None:
        self.content = str(content).strip()[:MAX_NOTE_CHARS]
        self.kind = str(kind or "fact").strip().lower()
        self.actor = str(actor or "contact").strip().lower()
        self.importance = min(1.0, max(0.0, float(importance)))
        self.tags = tuple(str(tag) for tag in tags if str(tag).strip())
        self.topic_key = normalize_topic_key(topic_key)
        self.revision_kind = normalize_revision_kind(revision_kind)
        self.temporal_status = normalize_temporal_status(temporal_status)
        # 没给类型时按 kind 推一个: kind 是我们自己的粗分类,Doppel 要的是它那套
        self.memory_type = normalize_memory_type(memory_type or self.kind)


# 槽位名归一化: 模型每次可能写"课表""课程表""上课时间" ——
# 不做同义归并(那需要词典),但至少统一大小写与空白、去掉标点,
# 让"同一批里写法一致"的情况能稳定命中同一个槽位。
_TOPIC_STRIP = re.compile(r"[\s,，。、;；:：\"'“”‘’()（）\[\]【】]+")


def normalize_topic_key(raw: Any) -> str:
    """槽位标识归一化(空 = 没有槽位)。"""
    text = _TOPIC_STRIP.sub("", str(raw or "").strip().lower())
    return text[:40]


_REVISION_KINDS = {"assertion", "correction", "retraction"}
_TEMPORAL_STATUSES = {"current", "planned", "historical", "unknown"}
_MEMORY_TYPES = {
    "fact", "state", "episode", "preference", "relationship", "plan", "commitment",
    # 我们自己的 kind 取值 → Doppel 类型名的兜底映射(见下)
    "relation", "style", "event",
}
# 我们总结器的 kind 与 Doppel 的 personal_memory_type 不是一套词表,
# 这里做一次显式映射: 猜错的代价是"不会被合并/替换"(保守),不是错误替换。
_MEMORY_TYPE_ALIASES = {
    "relation": "relationship",
    "style": "preference",
    "event": "episode",
}


def normalize_revision_kind(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    return value if value in _REVISION_KINDS else "assertion"


def normalize_temporal_status(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    return value if value in _TEMPORAL_STATUSES else "unknown"


def normalize_memory_type(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    value = _MEMORY_TYPE_ALIASES.get(value, value)
    return value if value in _MEMORY_TYPES else "fact"


class Summarizer(Protocol):
    """把一批消息总结成若干条值得长期记住的记忆(异步,便于 await LLM)。"""

    async def __call__(
        self,
        conversation: Mapping[str, Any],
        messages: Sequence[Mapping[str, Any]],
    ) -> Sequence[BatchNote]: ...


# 默认总结器: 用 fast 模型。返回 (会话, 消息列表) -> 记忆列表,可以为空列表。
SummarizerFn = Callable[
    [Mapping[str, Any], Sequence[Mapping[str, Any]]],
    Awaitable[Sequence[BatchNote]],
]


# ---------------------------------------------------------------------------
# 游标编解码(created_at|id 复合键)
# ---------------------------------------------------------------------------
def encode_cursor(created_at: str, message_id: str) -> str:
    """把 (消息时间, 消息 ID) 编成游标字符串。

    为什么是复合键而不是"只按时间":同一毫秒里可能有多条消息(批量导入、
    平台补推),只比时间会导致边界消息被重复读取或跳过。加上 ID 后,
    (created_at, id) 在会话内是严格全序 —— 这正是增量读取需要的稳定游标。

    格式 "created_at|id":ID 部分为空表示"只比时间(严格大于)",
    用于首次运行时的水位线(见 MessageHistoryReader.read)。
    """
    return f"{created_at}|{message_id}"


def decode_cursor(cursor: str) -> tuple[str, str]:
    """拆开游标,返回 (消息时间, 消息 ID);格式非法时按"只比时间"处理。"""
    at, _, message_id = str(cursor or "").partition("|")
    return at, message_id


def _as_text(value: datetime) -> str:
    """datetime → 与 messages 表一致的 UTC ISO 文本(保证字符串比较 == 时间比较)。

    注意这是一个**约定**:messages.created_at 全部由 recorder/conversations
    以 datetime.now(timezone.utc).isoformat() 写入,格式统一才能用 SQL 的
    字符串比较做时间比较(现有的 list_messages 的 ORDER BY created_at 也依赖它)。
    """
    return value.astimezone(timezone.utc).isoformat()


def _parse_time(value: Any) -> datetime | None:
    """把库里的时间文本解析成带时区的 datetime;空值/脏数据返回 None。"""
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 历史读取器(实现 Doppel 的 ScopedHistoryReader)
# ---------------------------------------------------------------------------
# actor 的 SQL 表达式 —— 必须与 app/memory.py 的 _actor_of 逐条对应:
#   outbound / assistant → agent;system → system;其余 → contact
# 写成 SQL 而不是读出来再过滤,是因为分页必须发生在过滤**之后**:
# 若先 LIMIT 再过滤,被过滤掉的行会让游标卡住(下一页又读到它们),
# 触发 Doppel 的 "cursor must advance" 契约错误。
# tests/test_batches.py 里有一致性测试,防止两处映射漂移。
_ACTOR_SQL = (
    "CASE WHEN source = 'outbound' OR role = 'assistant' THEN 'agent'"
    " WHEN source = 'system' OR role = 'system' THEN 'system'"
    " ELSE 'contact' END"
)


class MessageHistoryReader:
    """从本项目的 messages 表读某个会话的历史(只读、精确 scope、旧→新)。

    契约要点(Doppel 的 GuardedHistoryReader 会逐条校验):
      · 只返回水位线**之后**的消息 —— 水位线 = 游标,游标为空时退回 last_message_at;
      · 非空页必须给出"非空且变化"的 next_cursor;
      · 单页条数不超过 limit;has_more=True 时必须带消息。
    """

    def __init__(
        self,
        conversation: Mapping[str, Any],
        *,
        watermark: str = "",
        page_size: int = PAGE_SIZE,
    ) -> None:
        """conversation 是 conversations 表的行(dict);watermark 是已处理到的消息时间。"""
        self._conversation = dict(conversation)
        self._watermark = str(watermark or "")
        self.page_size = max(1, int(page_size))

    @property
    def conversation_id(self) -> str:
        return str(self._conversation.get("id") or "")

    @property
    def scope(self) -> Any:
        """记忆命名空间(与写入用的 scope 必须完全一致,Doppel 会比对)。"""
        return memory_layer.build_scope(self._conversation)

    async def read(
        self,
        *,
        cursor: str = "",
        limit: int = 500,
        actors: set[str] | None = None,
        time_from: datetime | None = None,
        time_to: datetime | None = None,
    ) -> Any:
        """读一页历史。

        time_from 用**严格大于**语义(与水位线一致):窗口起点就是上次处理到的
        那条消息的时间,若按"大于等于"会把这条消息再读一遍,导致重复总结。
        time_to 是**含**上界(在窗口内),保证一次运行看到的是冻结的一段历史 ——
        运行期间新到的消息留给下一轮。
        """
        limit = int(limit)
        if limit <= 0:
            return HistoryPage(messages=[], next_cursor=cursor, has_more=False)

        at, message_id = decode_cursor(cursor or self._watermark)
        if time_from is not None:
            start = _as_text(time_from)
            if start > at:  # 只取"更靠后"的下界,两个下界不会互相打架
                at, message_id = start, ""

        conditions = ["conversation_id = ?"]
        params: list[Any] = [self.conversation_id]
        if at:
            if message_id:
                conditions.append("(created_at > ? OR (created_at = ? AND id > ?))")
                params.extend([at, at, message_id])
            else:
                # 只给到时间(首次运行的水位线):严格大于,避免把水位线那条再读一遍
                conditions.append("created_at > ?")
                params.append(at)
        if actors:
            wanted = sorted(str(actor) for actor in actors)
            conditions.append(
                f"({_ACTOR_SQL}) IN ({','.join('?' * len(wanted))})"
            )
            params.extend(wanted)
        if time_to is not None:
            conditions.append("created_at <= ?")
            params.append(_as_text(time_to))

        # 多取一条判断还有没有下一页 —— 这样 has_more 永远是准的
        params.append(limit + 1)
        rows = get_connection().execute(
            "SELECT id, role, source, content, created_at, goal_id FROM messages"
            f" WHERE {' AND '.join(conditions)}"
            " ORDER BY created_at ASC, id ASC LIMIT ?",
            tuple(params),
        ).fetchall()

        has_more = len(rows) > limit
        rows = rows[:limit]
        messages = [self._to_chat_message(dict(row)) for row in rows]
        # 空页原样返回入参游标(不能返回 ""——那会把已推进的水位线抹掉);
        # 非空页用最后一条的复合键,严格大于入参游标(契约要求)。
        next_cursor = encode_cursor(str(rows[-1]["created_at"]), str(rows[-1]["id"])) if rows else cursor
        return HistoryPage(messages=messages, next_cursor=next_cursor, has_more=has_more)

    @staticmethod
    def _to_chat_message(row: Mapping[str, Any]) -> Any:
        """messages 表的行 → Doppel ChatMessage。

        只映射总结需要的字段;role/source/goal_id 塞进 raw ——
        Doppel 的 HistoryPage 会做一次 model_dump/model_validate 往返,
        raw 是 dict,能原样带过校验。
        注意: **不要**把 v2 的 content_parts 塞进 ChatMessage.parts ——
        两者的结构不同(Doppel 的 ContentPart 要求 text/media/metadata 至少有一个非空),
        塞进去会在校验时报错,整页被判定为"非法页"。
        """
        return ChatMessage.of(
            memory_layer._actor_of(str(row.get("role") or ""), str(row.get("source") or "")),
            str(row.get("content") or ""),
            str(row.get("created_at") or ""),
            message_id=str(row.get("id") or ""),
            raw={
                "role": str(row.get("role") or ""),
                "source": str(row.get("source") or ""),
                "goal_id": str(row.get("goal_id") or ""),
            },
        )


# ---------------------------------------------------------------------------
# 攒批任务(实现 Doppel 的 MemoryBatchTask)
# ---------------------------------------------------------------------------
class ConversationDigestTask:
    """把一段历史攒批总结成记忆候选(只提议,不写库)。"""

    name = TASK_NAME
    version = TASK_VERSION
    checkpoint_schema_version = CHECKPOINT_SCHEMA_VERSION

    def __init__(
        self,
        conversation: Mapping[str, Any],
        summarizer: SummarizerFn,
        *,
        page_size: int = PAGE_SIZE,
        max_messages: int = MAX_MESSAGES_PER_RUN,
    ) -> None:
        self._conversation = dict(conversation)
        self._summarizer = summarizer
        self._page_size = max(1, int(page_size))
        self._max_messages = max(1, int(max_messages))

    async def propose(self, context: Any) -> Any:
        """读完整批 → 交给总结器 → 产出 proposals + 下一轮检查点。

        无论总结出几条(包括 0 条),都要返回 next_checkpoint:
        没价值 = 不写记忆,但**游标必须推进**,否则这批消息会被永远重读。

        注意分页与"单轮上限"的配合: 每页只读到"剩余额度"为止,游标永远
        对应当前页最后一条消息 —— 这样即便因为上限提前收工,游标也不会
        越过那些还没总结的消息(越过了就永远丢了)。
        """
        cursor = context.checkpoint.cursor
        collected: list[Any] = []
        while len(collected) < self._max_messages:
            page = await context.history.read(
                cursor=cursor,
                limit=min(self._page_size, self._max_messages - len(collected)),
                time_from=context.window.start,
                time_to=context.window.end,
            )
            collected.extend(page.messages)
            cursor = page.next_cursor
            if not page.has_more:
                break

        rows = [_message_payload(message) for message in collected]

        proposals: list[Any] = []
        if rows:
            notes = list(await self._summarizer(self._conversation, rows))
            proposals = self._build_proposals(context, rows, notes)

        return BatchProposalPlan(
            proposals=proposals,
            next_checkpoint=BatchCheckpoint(
                cursor=cursor,
                metadata={
                    "last_message_at": rows[-1]["created_at"] if rows else "",
                    "message_count": len(rows),
                    "task_version": self.version,
                },
            ),
        )

    def _build_proposals(
        self,
        context: Any,
        rows: Sequence[Mapping[str, Any]],
        notes: Sequence[BatchNote],
    ) -> list[Any]:
        """把总结出来的记忆候选变成 Doppel 的 MemoryProposal。

        幂等键取自"这批消息的首尾 ID":同一批消息重复总结(进程崩溃后重跑、
        检查点没能提交)不会写出重复记忆 —— 这是逐条写入时代没有的保证。
        """
        first_id = str(rows[0].get("id") or "")
        last_id = str(rows[-1].get("id") or "")
        chain = [f"message:{row['id']}" for row in rows if row.get("id")]
        proposals: list[Any] = []
        for index, note in enumerate(notes[:MAX_NOTES_PER_RUN]):
            if not note.content:
                continue
            proposals.append(
                MemoryProposal(
                    scope=context.scope,
                    content=note.content,
                    kind=note.kind,
                    actor=note.actor,
                    importance=note.importance,
                    # personal-memory 标签是 Doppel 整理/治理的**筛选依据**
                    # (ConsolidationRunner 只读带这个标签的记录,见
                    #  doppel_memory/consolidation.py 的 _read_all)。
                    # 少了它,记忆就永远不参与过期/冲突整理。
                    tags=list(
                        dict.fromkeys(["personal-memory", note.memory_type, *note.tags])
                    ),
                    processor=self.name,
                    processor_version=self.version,
                    idempotency_key=f"{TASK_NAME}:{first_id}:{last_id}:{index}",
                    derived_chain=chain,
                    # 记在"这一批结束"的时刻:语义是"到这时为止我们是这么理解的"
                    created_at=context.window.end,
                    metadata={
                        "conversation_id": str(self._conversation.get("id") or ""),
                        "message_count": len(rows),
                        "first_message_id": first_id,
                        "last_message_id": last_id,
                        # ---- 事实过期/冲突所需的槽位元数据 ----
                        # 字段名刻意与 Doppel 的 personal-memory 管线一致
                        # (见 doppel_memory/intelligence.py 的 metadata 组装):
                        # 整理器按 topic_key 分组、按 revision_kind 决定能不能替换,
                        # 名字写错就等于"这两条永远不会被比对"。
                        "personal_memory_type": note.memory_type,
                        "topic_key": note.topic_key,
                        "revision_kind": note.revision_kind,
                        "temporal_status": note.temporal_status,
                        "subject": note.actor,
                    },
                )
            )
        return proposals


def _message_payload(message: Any) -> dict[str, Any]:
    """ChatMessage → 总结器看到的普通字典(与具体记忆后端解耦)。

    raw 里带着 role/source,是总结器判断"谁说的"以及排障时的依据
    (Doppel 的 ChatMessage 只保留归一化后的 actor)。
    """
    raw = dict(getattr(message, "raw", None) or {})
    message_id = str(getattr(message, "message_id", "") or "")
    return {
        "id": message_id or str(getattr(message, "identity_key", "") or ""),
        "actor": str(getattr(message, "actor", "") or ""),
        "content": str(getattr(message, "text", "") or ""),
        "created_at": _as_text(message.at) if getattr(message, "at", None) else "",
        "role": str(raw.get("role") or ""),
        "source": str(raw.get("source") or ""),
    }


# ---------------------------------------------------------------------------
# 进度(宿主侧 checkpoint,落在 memory_batches 表)
# ---------------------------------------------------------------------------
def load_progress(conversation_id: str) -> dict[str, Any]:
    """读取该会话的攒批进度(没有记录时返回空字典)。"""
    row = get_connection().execute(
        "SELECT * FROM memory_batches WHERE conversation_id=?", (conversation_id,)
    ).fetchone()
    return dict(row) if row is not None else {}


def list_progress(limit: int = 50) -> list[dict[str, Any]]:
    """列出各会话的攒批进度(按最近运行时间倒序)。

    用途: 状态页/排障 —— "哪些会话还积压着、上一次为什么失败"。
    只返回跑过攒批的会话(没跑过的没有进度行,自然是"还没到总结条件")。
    """
    rows = get_connection().execute(
        "SELECT * FROM memory_batches ORDER BY last_run_at DESC LIMIT ?",
        (max(1, int(limit)),),
    ).fetchall()
    return [dict(row) for row in rows]


def save_progress(
    conversation_id: str,
    *,
    status: str,
    error: str = "",
    cursor: str | None = None,
    last_message_at: str | None = None,
    pending_count: int | None = None,
    metadata: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> None:
    """写入攒批进度。

    cursor / last_message_at / pending_count / metadata 传 None 表示"保持原值":
    失败时不推进水位线,但 last_run_at / last_status / last_error 要更新 ——
    否则排障时看不出"扫描跑过但失败了"。
    """
    conn = get_connection()
    existing = conn.execute(
        "SELECT conversation_id FROM memory_batches WHERE conversation_id=?",
        (conversation_id,),
    ).fetchone()
    values: dict[str, Any] = {
        "last_run_at": _as_text(now or datetime.now(timezone.utc)),
        "last_status": str(status),
        "last_error": str(error)[:500],
    }
    if cursor is not None:
        values["cursor"] = str(cursor)
    if last_message_at is not None:
        values["last_message_at"] = str(last_message_at)
    if pending_count is not None:
        values["pending_count"] = int(pending_count)
    if metadata is not None:
        values["metadata"] = json.dumps(dict(metadata), ensure_ascii=False, sort_keys=True)

    if existing is None:
        # 首次记录: 未提供的列用默认值
        columns = ["conversation_id", *values]
        placeholders = ",".join("?" * len(columns))
        conn.execute(
            f"INSERT INTO memory_batches ({','.join(columns)}) VALUES ({placeholders})",
            (conversation_id, *values.values()),
        )
    else:
        assignments = ",".join(f"{column}=?" for column in values)
        conn.execute(
            f"UPDATE memory_batches SET {assignments} WHERE conversation_id=?",
            (*values.values(), conversation_id),
        )
    conn.commit()


def _checkpoint_of(progress: Mapping[str, Any]) -> tuple[str, str]:
    """从进度行取出 (cursor, watermark)。

    版本检查: 进度里记录的 task_version 与当前不一致时,游标作废
    (总结语义变了,旧游标对应的"已处理"范围不再可信),退回水位线重读。
    水位线本身仍然有效 —— 它只表示"这些消息写进库了",与语义无关。
    """
    cursor = str(progress.get("cursor") or "")
    watermark = str(progress.get("last_message_at") or "")
    if cursor:
        try:
            stored = json.loads(str(progress.get("metadata") or "") or "{}")
        except json.JSONDecodeError:
            stored = {}
        version = str(stored.get("task_version") or "")
        if version and version != TASK_VERSION:
            print(f"[batches] 任务版本从 {version} 变为 {TASK_VERSION},重置游标")
            cursor = ""
    return cursor, watermark


def pending_of(conversation_id: str, watermark: str) -> tuple[int, str]:
    """返回 (水位线之后的待处理条数, 最新一条消息时间)。

    一次查询同时拿到两个值: 攒批的触发判定既要"攒了多少",也要"多久没动静了"。
    """
    row = get_connection().execute(
        "SELECT COUNT(*) AS pending, MAX(created_at) AS last_at FROM messages"
        " WHERE conversation_id=? AND created_at > ?",
        (conversation_id, str(watermark or "")),
    ).fetchone()
    pending = int(row["pending"] or 0) if row is not None else 0
    last_at = str(row["last_at"] or "") if row is not None else ""
    return pending, last_at


# ---------------------------------------------------------------------------
# 触发判定
# ---------------------------------------------------------------------------
def is_due(
    conversation: Mapping[str, Any],
    progress: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """判断某个会话现在是否该做一次攒批总结,返回 (是否, 原因)。

    原因字符串会进日志/返回摘要,便于回答"这个会话为什么没总结"。
    判定顺序(先命中先返回):
      1. 没开 monitor → 不参与(那本来就没在收集消息);
      2. 没有待处理消息 → 没什么可总结的;
      3. 待处理条数 ≥ digest_max_messages → 攒够了,先总结(不必等对方停下来);
      4. 距**最后一条消息**已静止 ≥ digest_window_seconds → 对方不说了,收尾。
    参数 now 可注入(测试用),缺省取当前 UTC 时间。
    """
    moment = now or datetime.now(timezone.utc)
    policy = policy_service.normalize_policy(dict(conversation))
    if not policy["monitor"]:
        return False, "not-monitored"

    progress = progress or {}
    _cursor, watermark = _checkpoint_of(progress)
    pending, last_at = pending_of(str(conversation.get("id") or ""), watermark)
    if pending <= 0:
        return False, "no-pending"

    max_messages = max(1, int(policy["digest_max_messages"]))
    if pending >= max_messages:
        return True, f"count>={max_messages}"

    window_seconds = max(1, int(policy["digest_window_seconds"]))
    last_message_at = _parse_time(last_at)
    if last_message_at is not None:
        idle = (moment - last_message_at).total_seconds()
        if idle >= window_seconds:
            return True, f"idle>={window_seconds}s"
    return False, "waiting"


def list_monitored_conversations() -> list[dict[str, Any]]:
    """列出开启监视的会话。

    用 policy.normalize_policy 判定而不是 SQL 里写 monitor=1:
    库里可能是 0/1、"true"/"false" 等历史写法,判定规则只该有一份
    (services/policy.py 的 _as_bool_int)。
    """
    rows = get_connection().execute(
        "SELECT * FROM conversations ORDER BY updated_at DESC"
    ).fetchall()
    return [
        dict(row)
        for row in rows
        if policy_service.normalize_policy(dict(row))["monitor"]
    ]


# ---------------------------------------------------------------------------
# 执行入口
# ---------------------------------------------------------------------------
def _window_start(progress: Mapping[str, Any], conversation: Mapping[str, Any], now: datetime) -> datetime:
    """本轮历史窗口的起点(Doppel 要求带时区,且不得晚于终点)。

    规则: 有水位线就用水位线(那是"上次处理到哪"),没有水位线就用**纪元**
    —— 即"从头开始读"。

    为什么不用会话的 created_at 当兜底: 它会**丢消息**。历史回填/桌面端导入
    的消息,时间戳可能早于会话行的创建时间,一旦拿 created_at 当下界,
    这些消息会被静默跳过(测试 test_run_cap_does_not_skip_messages 就是
    用早于会话创建时间的消息抓出这个问题的)。窗口真正的约束力在**上界**
    (冻结本轮可见范围),下界由游标/水位线承担。
    """
    watermark = _parse_time(progress.get("last_message_at"))
    if watermark is not None:
        # 水位线比"现在"还晚(时钟回拨/脏数据)时夹到 now,避免 window 非法
        return min(watermark, now)
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


async def summarize_conversation(
    conversation: Mapping[str, Any],
    progress: Mapping[str, Any] | None = None,
    *,
    now: datetime | None = None,
    summarizer: SummarizerFn | None = None,
    client: Any = None,
) -> dict[str, Any]:
    """对单个会话跑一轮攒批总结,返回本次处理摘要。

    返回 {"conversation_id", "status", "written", "messages", "error"}:
      status: ok(总结出并写入) / empty(没值得记的,但游标已推进) / error(本轮失败,
              水位线不推进,下次重试) / skipped(记忆未启用)
      written: 实际写入的记忆条数(Doppel 去重后 status=created/updated 的条数)
    """
    conversation_id = str(conversation.get("id") or "")
    progress = progress or {}
    moment = now or datetime.now(timezone.utc)
    result: dict[str, Any] = {
        "conversation_id": conversation_id,
        "status": "skipped",
        "written": 0,
        "messages": 0,
        "error": "",
    }
    if not _DOPPEL_AVAILABLE:
        result["error"] = "Doppel 未安装"
        return result

    if client is None:
        client = await memory_layer.get_client()
    if client is None:
        result["error"] = "记忆功能未启用"
        return result
    cursor, watermark = _checkpoint_of(progress)
    scope = memory_layer.build_scope(dict(conversation))
    window = HistoryWindow(
        start=_window_start(progress, conversation, moment),
        end=moment,
    )
    reader = MessageHistoryReader(dict(conversation), watermark=watermark, page_size=PAGE_SIZE)
    task = ConversationDigestTask(
        dict(conversation),
        summarizer or _llm_summarize,
        page_size=PAGE_SIZE,
        max_messages=MAX_MESSAGES_PER_RUN,
    )
    checkpoint = BatchCheckpoint(
        cursor=cursor,
        schema_version=CHECKPOINT_SCHEMA_VERSION,
        metadata={"watermark": watermark, "task_version": TASK_VERSION},
    )

    try:
        run = await client.run_batch_task(
            task,
            scope,
            window,
            checkpoint=checkpoint,
            history=reader,
            run_id=f"{TASK_NAME}:{conversation_id}:{int(moment.timestamp())}",
        )
    except Exception as exc:  # noqa: BLE001 - 攒批失败只能降级,不能打断调度
        result["status"] = "error"
        result["error"] = f"{type(exc).__name__}: {exc}"
        save_progress(conversation_id, status="error", error=result["error"], now=moment)
        print(f"[batches] 会话 {conversation_id} 攒批异常(已降级): {result['error']}")
        return result

    errors = [error.message for error in (run.errors or [])]

    if run.committable_checkpoint is None:
        # 有错误(读写/校验/写入失败): 水位线**不动**,下次重读同一批。
        # 这是 Doppel 的设计意图 —— 检查点只有在"没有错误"时才可提交。
        result["status"] = "error"
        result["error"] = "; ".join(errors) or "检查点不可提交"
        pending, _last_at = pending_of(conversation_id, watermark)
        save_progress(
            conversation_id,
            status="error",
            error=result["error"],
            pending_count=pending,
            now=moment,
        )
        print(f"[batches] 会话 {conversation_id} 攒批失败(保留水位线): {result['error']}")
        return result

    next_checkpoint = run.committable_checkpoint
    metadata = dict(next_checkpoint.metadata or {})
    last_message_at = str(metadata.get("last_message_at") or watermark)
    written = sum(1 for item in run.write_results if item.accepted)
    messages_read = int(getattr(run, "history_messages_read", 0) or 0)
    # 待处理数按**推进后**的水位线算,列里留的是真实积压(排障时不用再猜)
    pending, _last_at = pending_of(conversation_id, last_message_at)
    result["written"] = written
    result["messages"] = messages_read
    result["status"] = "ok" if written else "empty"
    result["error"] = "; ".join(errors)

    save_progress(
        conversation_id,
        status=result["status"],
        error=result["error"],
        cursor=str(next_checkpoint.cursor or ""),
        last_message_at=last_message_at,
        pending_count=pending,
        metadata={
            "task_name": TASK_NAME,
            "task_version": TASK_VERSION,
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "window_start": window.start.isoformat(),
            "window_end": window.end.isoformat(),
            "message_count": metadata.get("message_count", 0),
        },
        now=moment,
    )

    # 写过记忆就登记这个会话,交给整理环节处理"过期/冲突"(见 app/consolidation.py)。
    # 为什么在这里登记而不是让调度器扫全部会话: 没写过记忆的会话没有可整理的,
    # 而会话数可能很多 —— 用一张表标出"有记忆的会话",调度时只扫这些。
    if written:
        from . import consolidation

        consolidation.register_scope(conversation_id, str(getattr(scope, "scope_key", "") or ""))
    return result


async def run_due_batches(
    now: datetime | None = None,
    *,
    summarizer: SummarizerFn | None = None,
) -> dict[str, Any]:
    """扫描所有监视中的会话,把"该总结"的会话各跑一轮。

    这是对外入口(调度器每 60 秒调用一次,见 app/scheduler.py)。
    返回摘要,便于测试与排障:
      {"enabled": bool, "checked": n, "runs": [每个会话的结果...], "written": 总数}

    设计要点:
      · 单会话异常只影响它自己(status=error),不打断其余会话的扫描;
      · 单个会话一轮只跑一次(并发调用请自行串行化 —— 调度器用"上一轮没跑完就跳过")。
    """
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise ValueError("now 必须带时区(攒批窗口要求绝对时间)")

    conversations = list_monitored_conversations()
    summary: dict[str, Any] = {
        "enabled": False,
        "checked": len(conversations),
        "runs": [],
        "written": 0,
    }
    if not conversations:
        return summary

    client = await memory_layer.get_client()
    if client is None:
        # 记忆未启用/Doppel 不可用: 纯降级,连进度都不动
        return summary
    summary["enabled"] = True

    for conversation in conversations:
        conversation_id = str(conversation.get("id") or "")
        progress = load_progress(conversation_id)
        due, reason = is_due(conversation, progress, moment)
        if not due:
            continue
        try:
            outcome = await summarize_conversation(
                conversation,
                progress,
                now=moment,
                summarizer=summarizer,
                client=client,
            )
        except Exception as exc:  # noqa: BLE001 - 单会话失败不能影响其它会话
            outcome = {
                "conversation_id": conversation_id,
                "status": "error",
                "written": 0,
                "messages": 0,
                "error": f"{type(exc).__name__}: {exc}",
            }
            save_progress(
                conversation_id,
                status="error",
                error=outcome["error"],
                now=moment,
            )
        outcome["reason"] = reason
        summary["runs"].append(outcome)
        summary["written"] += int(outcome["written"])

    return summary


# ---------------------------------------------------------------------------
# 默认总结器(fast 模型 + JSON 输出)
# ---------------------------------------------------------------------------
# 模型实例做模块级缓存: 每次扫描都新建 ChatOpenAI 会重复建 HTTP 客户端。
_model: Any = None


def _fast_model() -> Any:
    """惰性构造 fast 模型(与 main.py 的 llm_factory(fast=True) 同样的构造)。

    为什么不复用 main.py 的工厂: 它是 create_app 的局部函数,不在
    agent/runtime.py 的全局访问点里;攒批由调度器驱动,与图执行无关,
    自己持有一个轻量模型即可(且整进程只建一次)。
    """
    global _model
    if _model is None:
        from langchain_openai import ChatOpenAI

        settings = get_settings()
        _model = ChatOpenAI(
            model=settings.fast_model_name,
            temperature=0.2,   # 总结要稳,不要发挥
            timeout=settings.llm_timeout_seconds,
            api_key=settings.api_key or None,
            base_url=settings.base_url or None,
        )
    return _model


# 总结用的系统提示。刻意保守: "宁可不记,也别记流水账" ——
# 这正是本功能存在的理由(逐条写入会把记忆碎成一堆"嗯""好的")。
_SUMMARY_SYSTEM_PROMPT = """你是长期记忆整理器。输入是一段聊天记录,请提炼出**值得长期记住**的少量事实,供以后的对话参考。

值得记:
- 对方(contact)的稳定信息: 身份、职业、习惯、偏好、重要日期、与他人的关系;
- 号主/你(agent)明确做出的约定与承诺: 答应了什么、约在什么时候;
- 明确的计划与安排(时间、地点、要做的事)。

不要记:
- 寒暄、表情、语气词、口头禅(如"嗯""好的""哈哈"),以及一次性的闲聊;
- 不确定的猜测、玩笑、反话;
- 纯粹的即时状态("现在在吃饭")除非它明显会成为长期事实。

规则:
- 没价值就返回空数组 [],**不要凑数**;
- 每条一句话,自包含(不依赖上下文也能读懂),不要加引号前缀;
- 最多 5 条;已经无法从记录中确认的内容不要写。

只输出 JSON 数组,不要任何解释文字。元素格式:
{"content": "记忆内容", "kind": "fact|relation|style|event", "actor": "contact|agent|owner", "importance": 0.0~1.0,
 "slot": "槽位名或空串", "revision": "assertion|correction|retraction", "temporal": "current|planned|historical"}

字段说明:
- actor: contact=对方说的, agent=机器人自己说的/承诺的, owner=号主;
- slot(槽位): **同一件会反复出现的事**用一个固定的短名字,比如"课表""口味偏好""每周安排""称呼";
  一次性的事件留空字符串。新旧说法只有落在同一个 slot 上才能被比对和替换 ——
  写歪了就会出现"课表改到四点了"和"课表是三点"两条互相打架的记忆;
- revision: 只有聊天记录里**明说改了/换/取消/不是...了**才写 correction 或 retraction,
  其余一律 assertion。**不能因为"这条更新"就写 correction** —— 那是替号主改口供;
- temporal: current=现在成立的事实/偏好, planned=将来的安排, historical=已经过去的一次性事件。
  现在的偏好与将来的安排是两回事,不要互相覆盖。"""


def _slot_hint_prompt(slots: Sequence[str]) -> str:
    """把该会话已有的槽位名附在提示词后面,让模型尽量复用而不是另造一个。

    为什么值得多花这几行 token: 槽位名是**字符串精确匹配**的 ——
    同一件事这批评成"课表"、下批评成"课程安排",两条记忆就永远不会被比对,
    过期的那条会一直躺在检索结果里。
    """
    if not slots:
        return ""
    return (
        "\n\n该会话已有的槽位(能用就用这些,别另造同义的新名字): "
        + "、".join(slots[:20])
    )


def _build_digest_prompt(messages: Sequence[Mapping[str, Any]]) -> str:
    """把一批消息渲染成提示词(带说话人与时间,便于判断事实权威与时效)。"""
    actor_label = {"contact": "对方", "agent": "机器人", "system": "系统", "owner": "号主"}
    lines: list[str] = []
    for message in messages:
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        content = content[:MAX_MESSAGE_CHARS]
        who = actor_label.get(str(message.get("actor") or ""), str(message.get("actor") or "未知"))
        when = str(message.get("created_at") or "")[:16]
        lines.append(f"[{when} {who}] {content}")
    return "聊天记录(旧→新):\n" + "\n".join(lines)


def parse_notes(text: Any) -> list[BatchNote]:
    """解析模型输出成 BatchNote 列表(解析失败/格式不符 = 不记,不抛异常)。

    容错: 模型偶发在 JSON 外套一层解释文字或代码块 —— 取最外层的 [...] 再解析。
    """
    notes, _unparsed = parse_notes_with_diagnostics(text)
    return notes


# ---------------------------------------------------------------------------
# 总结器健康度(防"静默丢批次")
# ---------------------------------------------------------------------------
# 为什么需要: 解析失败时返回空列表 → 业务上等于"这批没值得记的" →
# 水位线照常推进 → 那批消息**永远不再被总结**。
# 也就是说"提示词在真模型上不工作"这种故障，表现是完全静默的记忆缺失。
# 这里把"模型给了输出但解析不出东西"单独计数并打日志,
# 供冒烟脚本与健康检查发现。
_SUMMARY_STATS: dict[str, int] = {"calls": 0, "empty": 0, "unparsed": 0}
_LAST_UNPARSED_SAMPLE = ""


def parse_notes_with_diagnostics(text: Any) -> tuple[list[BatchNote], bool]:
    """解析模型输出;第二个返回值表示"有输出但解析不出内容"(可疑)。

    三种情况要分清:
      · 空输出 / 明确返回 []  → (空列表, False)  模型认为没价值,正常;
      · 合法 JSON 但字段缺失  → (空列表, False)  同上(内容为空的条目会被跳过);
      · 有输出但压根不是 JSON  → (空列表, True)  **可疑**: 提示词或模型出问题了。
    """
    global _LAST_UNPARSED_SAMPLE
    raw = str(text or "").strip()
    if not raw:
        return [], False
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = raw.split("\n", 1)[1] if "\n" in raw else ""
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end <= start:
        _LAST_UNPARSED_SAMPLE = raw[:200]
        return [], True
    try:
        parsed = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        _LAST_UNPARSED_SAMPLE = raw[:200]
        return [], True
    if not isinstance(parsed, list):
        _LAST_UNPARSED_SAMPLE = raw[:200]
        return [], True

    notes: list[BatchNote] = []
    for item in parsed[:MAX_NOTES_PER_RUN]:
        if isinstance(item, str):
            notes.append(BatchNote(item))
            continue
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or item.get("fact") or "").strip()
        if not content:
            continue
        try:
            importance = float(item.get("importance", 0.5))
        except (TypeError, ValueError):
            importance = 0.5
        notes.append(
            BatchNote(
                content,
                kind=str(item.get("kind") or "fact"),
                actor=str(item.get("actor") or "contact"),
                importance=importance,
                tags=item.get("tags") or (),
                topic_key=item.get("slot") or item.get("topic_key") or "",
                revision_kind=item.get("revision") or item.get("revision_kind") or "assertion",
                temporal_status=item.get("temporal") or item.get("temporal_status") or "unknown",
                memory_type=item.get("memory_type") or "",
            )
        )
    return notes, False


def summary_health() -> dict[str, Any]:
    """总结器健康度(排障/监控用): 调用次数、空结果、解析失败次数与样本。"""
    return {
        **_SUMMARY_STATS,
        "last_unparsed_sample": _LAST_UNPARSED_SAMPLE,
        "unparsed_ratio": (
            round(_SUMMARY_STATS["unparsed"] / _SUMMARY_STATS["calls"], 3)
            if _SUMMARY_STATS["calls"]
            else 0.0
        ),
    }


def reset_summary_stats() -> None:
    """重置统计(测试用)。"""
    global _LAST_UNPARSED_SAMPLE
    _SUMMARY_STATS.update(calls=0, empty=0, unparsed=0)
    _LAST_UNPARSED_SAMPLE = ""


async def _llm_summarize(
    conversation: Mapping[str, Any],
    messages: Sequence[Mapping[str, Any]],
) -> list[BatchNote]:
    """默认总结器: 用 fast 模型把一批消息总结成记忆候选。"""
    from langchain_core.messages import HumanMessage, SystemMessage

    prompt = _build_digest_prompt(messages)
    # 已有槽位提示: 让模型复用同一个槽位名(字符串精确匹配,写歪了就永远比不了)
    prompt += _slot_hint_prompt(await memory_layer.known_topic_keys(conversation))
    response = await _fast_model().ainvoke(
        [SystemMessage(content=_SUMMARY_SYSTEM_PROMPT), HumanMessage(content=prompt)]
    )
    notes, unparsed = parse_notes_with_diagnostics(getattr(response, "content", ""))

    _SUMMARY_STATS["calls"] += 1
    if unparsed:
        _SUMMARY_STATS["unparsed"] += 1
        print(
            f"[batches] 总结输出无法解析(已按空处理,这批消息不会再总结): "
            f"{_LAST_UNPARSED_SAMPLE[:120]}"
        )
    elif not notes:
        _SUMMARY_STATS["empty"] += 1
    return notes
