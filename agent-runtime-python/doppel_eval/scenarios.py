"""Knowledge scenes for Doppel shadow evaluation.

Each scene is a deterministic event sequence plus structural expectations.
Expectations use logical message ids ``{case_id}:m{seq}`` which replay
mapping can translate to real event ids.  These scenes mirror the classes
that Matter most for a personal-memory agent: correction vs conflict,
speaker attribution, agent-output isolation, noise filtering, temporal
lifecycle and cross-conversation sharing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from doppel_eval.events import EventFactory, SceneClock, SyntheticEvent

#: Canonical owner QQ id used by all scenes.
SELF_ID = "10001"


@dataclass(frozen=True)
class SceneExpectation:
    """Structural expectation; assertion logic arrives with the evaluator."""

    memory_expected: bool = True
    subject: str = "owner"  # owner | contact:<id> | agent | none
    claim: str = ""  # full natural memory text written as gold
    claim_contains: str = ""  # substring asserted in query hits
    source_message_ids: list[str] = field(default_factory=list)
    forbidden_claims: list[str] = field(default_factory=list)
    query: str = ""
    query_lexical: str = ""  # keyword-style query that must recall the memory
    query_now_offset_minutes: int = 0
    expected_evidence: list[str] = field(default_factory=list)
    evidence_may_span_hits: bool = False
    forbidden_evidence: list[str] = field(default_factory=list)
    temporal_status: str = ""  # current | planned | history | ""
    ambiguous: bool | None = None
    expected_count_status: str = ""
    expected_count: int | None = None
    expected_distinct_event_keys: int | None = None


@dataclass(frozen=True)
class Scene:
    case_id: str
    category: str
    description: str
    events: list[SyntheticEvent] = field(default_factory=list)
    expectations: list[SceneExpectation] = field(default_factory=list)

    @property
    def base_time(self) -> datetime:
        return datetime(2026, 5, 1, 9, 0, tzinfo=UTC)


def _clock(scene: Scene) -> SceneClock:
    return SceneClock(scene.base_time)


def build_scene(case_id: str) -> Scene:
    builder = _BUILDERS.get(case_id)
    if builder is None:
        raise KeyError(f"unknown scene: {case_id}")
    return builder()


def build_all_scenes() -> list[Scene]:
    scenes: list[Scene] = []
    seen: set[str] = set()
    for builder in _BUILDERS.values():
        scene = builder()
        if scene.case_id in seen:
            continue  # alias keys may point at the same builder
        seen.add(scene.case_id)
        scenes.append(scene)
    return scenes


def _stable_preference() -> Scene:
    clock = SceneClock(Scene("", "", "").base_time)
    factory = EventFactory(
        self_id=SELF_ID, clock=clock, case_id="stable-preference-repeat"
    )
    events = [
        factory.contact("晚上一起吃饭？", offset=0),
        factory.owner("可以，别放香菜", offset=2),
        factory.contact("好，还有什么忌口吗", offset=5),
        factory.owner("我不喜欢吃香菜，其他都行", offset=7),
        factory.agent_reply("收到，已记住您不吃香菜。", offset=8),
        factory.contact("那点菜了哈", offset=12),
        factory.owner("嗯嗯，上次说过了我不吃香菜", offset=14),
    ]
    expectations = [
        SceneExpectation(
            memory_expected=True,
            subject="owner",
            claim="我不喜欢吃香菜",
            claim_contains="香菜",
            source_message_ids=["m2", "m4", "m7"],
            forbidden_claims=["已记住您不吃香菜", "点菜了哈"],
            query="我不吃什么东西？",
            query_lexical="不吃香菜",
            query_now_offset_minutes=60,
            expected_evidence=["m2", "m4", "m7"],
            forbidden_evidence=["m5"],
            temporal_status="current",
        )
    ]
    return Scene(
        case_id="stable-preference-repeat",
        category="stable_fact",
        description="重复三次的稳定偏好应合并为一条记忆并绑定多条证据；agent 的'记住了'不应成为事实",
        events=events,
        expectations=expectations,
    )


def _noise_only() -> Scene:
    clock = SceneClock(Scene("", "", "").base_time)
    factory = EventFactory(self_id=SELF_ID, clock=clock, case_id="noise-only")
    events = [
        factory.contact("哈哈", offset=0),
        factory.owner("好的", offset=1),
        factory.contact("在吗", offset=3),
        factory.owner("嗯", offset=4),
        factory.contact("戳一戳", offset=5, sender_id="contact-001"),
        factory.group_member(
            "[表情]",
            chat_id="group-001",
            offset=6,
            sender_id="contact-002",
            sender_name="群友B",
        ),
        factory.owner("明天再聊，睡了", offset=9),
        factory.agent_reply("好的，晚安~", offset=10),
    ]
    expectations = [
        SceneExpectation(
            memory_expected=False,
            subject="none",
            claim_contains="",
            forbidden_claims=["哈哈", "好的", "在吗", "今晚聊"],
            query="我今晚有什么安排？",
            query_now_offset_minutes=30,
            expected_evidence=[],
            forbidden_evidence=["m2"],
            temporal_status="",
        )
    ]
    return Scene(
        case_id="noise-only",
        category="noise",
        description="纯噪声会话（哈哈/好的/在吗/戳一戳/表情）不应形成任何长期记忆",
        events=events,
        expectations=expectations,
    )


def _explicit_correction() -> Scene:
    clock = SceneClock(Scene("", "", "").base_time)
    factory = EventFactory(self_id=SELF_ID, clock=clock, case_id="explicit-correction")
    events = [
        factory.contact("你现在住哪个城市？", offset=0),
        factory.owner("我在杭州工作", offset=2),
        factory.contact("杭州啊，那挺近的", offset=5),
        factory.owner("不是杭州，我刚才说错了，我是苏州的", offset=9),
    ]
    expectations = [
        SceneExpectation(
            memory_expected=True,
            subject="owner",
            claim="我住在苏州",
            claim_contains="苏州",
            source_message_ids=["m4"],
            forbidden_claims=["我住在杭州"],
            query="我住在哪个城市？",
            query_lexical="苏州",
            query_now_offset_minutes=120,
            expected_evidence=["m4"],
            # The old statement remains valid provenance for an explicit
            # correction; it must not survive as an active current claim.
            forbidden_evidence=[],
            temporal_status="current",
        )
    ]
    return Scene(
        case_id="explicit-correction",
        category="correction",
        description="显式纠正（'我说错了'）应替换旧值，旧记忆降级为历史；查询当前时不得召回旧城市",
        events=events,
        expectations=expectations,
    )


def _subject_retraction() -> Scene:
    clock = SceneClock(Scene("", "", "").base_time)
    factory = EventFactory(self_id=SELF_ID, clock=clock, case_id="subject-retraction")
    events = [
        factory.contact("你能吃花生吗？", offset=0),
        factory.group_member(
            "他花生过敏，聚餐别点花生",
            chat_id="group-001",
            offset=3,
            sender_id="contact-003",
            sender_name="群友C",
        ),
        factory.owner("我没有花生过敏，过敏的是我朋友", offset=6),
    ]
    expectations = [
        SceneExpectation(
            memory_expected=True,
            subject="owner",
            claim="我本人没有花生过敏",
            claim_contains="花生",
            source_message_ids=["m3"],
            forbidden_claims=["他花生过敏"],
            query="我对花生过敏吗？",
            query_lexical="花生",
            query_now_offset_minutes=60,
            expected_evidence=["m3"],
            forbidden_evidence=["m2"],
            temporal_status="current",
        )
    ]
    return Scene(
        case_id="subject-retraction",
        category="role_attribution",
        description="事实主体纠正：群友说的过敏是别人的，号主本人无过敏；不得把群友说法记到号主名下",
        events=events,
        expectations=expectations,
    )


def _unmarked_conflict() -> Scene:
    clock = SceneClock(Scene("", "", "").base_time)
    factory = EventFactory(self_id=SELF_ID, clock=clock, case_id="unmarked-conflict")
    events = [
        factory.owner("我最喜欢的颜色是蓝色", offset=0),
        factory.contact("蓝色挺好看", offset=3),
        factory.owner("我最喜欢的颜色是绿色", offset=20),
    ]
    expectations = [
        SceneExpectation(
            memory_expected=True,
            subject="owner",
            claim="我最喜欢的颜色是蓝色",
            claim_contains="蓝",
            source_message_ids=["m1"],
            forbidden_claims=[],
            query="我最喜欢的颜色是什么？",
            query_lexical="蓝色",
            query_now_offset_minutes=40,
            expected_evidence=["m1"],
            forbidden_evidence=[],
            temporal_status="current",
            ambiguous=True,
        )
    ]
    return Scene(
        case_id="unmarked-conflict",
        category="conflict",
        description="无显式纠正标记的相反说法应保留为开放冲突（ambiguous），不得静默覆盖旧值",
        events=events,
        expectations=expectations,
    )


def _temporary_trip() -> Scene:
    clock = SceneClock(Scene("", "", "").base_time)
    factory = EventFactory(self_id=SELF_ID, clock=clock, case_id="temporary-trip")
    events = [
        factory.owner("我一直住在上海", offset=0),
        factory.contact("你下周有空吗", offset=30),
        factory.owner("我要临时去北京出差两个月，下周三走", offset=35),
        factory.contact("那回来再说", offset=38),
    ]
    expectations = [
        SceneExpectation(
            memory_expected=True,
            subject="owner",
            claim="我住在上海",
            claim_contains="上海",
            source_message_ids=["m1"],
            forbidden_claims=["北京"],
            query="我现在住在哪个城市？",
            query_lexical="上海",
            query_now_offset_minutes=80,
            expected_evidence=["m1"],
            forbidden_evidence=["m3"],
            temporal_status="current",
        )
    ]
    return Scene(
        case_id="temporary-trip",
        category="temporal",
        description="临时出差（带明确结束语义的计划）不得覆盖长期居住地",
        events=events,
        expectations=expectations,
    )


def _agent_output() -> Scene:
    clock = SceneClock(Scene("", "", "").base_time)
    factory = EventFactory(self_id=SELF_ID, clock=clock, case_id="agent-output")
    events = [
        factory.contact("你平时喜欢吃什么？", offset=0),
        factory.agent_reply("我其实很喜欢吃香菜，顿顿都要", offset=2),
        factory.contact("你居然是香菜爱好者", offset=4),
        factory.agent_reply("是啊，我的设定就是香菜爱好者呢", offset=5),
    ]
    expectations = [
        SceneExpectation(
            memory_expected=False,
            subject="agent",
            claim_contains="香菜",
            source_message_ids=[],
            forbidden_claims=["喜欢吃香菜", "香菜爱好者"],
            query="我喜欢吃香菜吗？",
            query_now_offset_minutes=30,
            expected_evidence=[],
            forbidden_evidence=["m2", "m4"],
            temporal_status="",
        )
    ]
    return Scene(
        case_id="agent-output",
        category="authority",
        description="agent 自动回复中的'事实'（即使从号主账号发出）不得成为号主记忆或强化号主事实",
        events=events,
        expectations=expectations,
    )


def _group_speaker_isolation() -> Scene:
    clock = SceneClock(Scene("", "", "").base_time)
    factory = EventFactory(
        self_id=SELF_ID, clock=clock, case_id="group-speaker-isolation"
    )
    events = [
        factory.group_member(
            "我住上海，最近房租涨了",
            chat_id="group-001",
            offset=0,
            sender_id="contact-002",
            sender_name="群友B",
        ),
        factory.group_member(
            "我住苏州",
            chat_id="group-001",
            offset=2,
            sender_id="contact-003",
            sender_name="群友C",
        ),
        factory.owner("我住杭州，你们都好近", chat_id="group-001", offset=5),
    ]
    expectations = [
        SceneExpectation(
            memory_expected=True,
            subject="owner",
            claim="我住在杭州",
            claim_contains="杭州",
            source_message_ids=["m3"],
            forbidden_claims=["上海", "苏州"],
            query="我住在哪个城市？",
            query_lexical="杭州",
            query_now_offset_minutes=30,
            expected_evidence=["m3"],
            forbidden_evidence=["m1", "m2"],
            temporal_status="current",
        )
    ]
    return Scene(
        case_id="group-speaker-isolation",
        category="scope_isolation",
        description="群聊中他人自述（上海/苏州）不得归到号主名下；同一群内说话人归属必须正确",
        events=events,
        expectations=expectations,
    )


def _cross_conversation_shared() -> Scene:
    clock = SceneClock(Scene("", "", "").base_time)
    factory = EventFactory(
        self_id=SELF_ID, clock=clock, case_id="cross-conversation-shared"
    )
    events = [
        factory.owner(
            "我现在在上海做后端开发",
            chat_type="private",
            chat_id="contact-001",
            offset=0,
        ),
        factory.contact(
            "挺忙的吧", chat_type="private", chat_id="contact-001", offset=3
        ),
        factory.owner(
            "最近在带一个 Java 项目",
            chat_type="private",
            chat_id="contact-002",
            offset=10,
        ),
        factory.contact(
            "项目忙吗", chat_type="private", chat_id="contact-002", offset=12
        ),
    ]
    expectations = [
        SceneExpectation(
            memory_expected=True,
            subject="owner",
            claim="我在上海做后端开发",
            claim_contains="上海",
            source_message_ids=["m1"],
            forbidden_claims=[],
            query="我在哪里工作？",
            query_lexical="上海",
            query_now_offset_minutes=30,
            expected_evidence=["m1"],
            forbidden_evidence=[],
            temporal_status="current",
        )
    ]
    return Scene(
        case_id="cross-conversation-shared",
        category="scope_hierarchy",
        description="号主在不同私聊中陈述的职业/常住信息应提升到 user scope 共享，且两个会话互相独立",
        events=events,
        expectations=expectations,
    )


def _temporal_lifecycle() -> Scene:
    clock = SceneClock(Scene("", "", "").base_time)
    factory = EventFactory(self_id=SELF_ID, clock=clock, case_id="temporal-lifecycle")
    events = [
        factory.contact("下周开会要准备什么？", offset=0),
        factory.owner("我下周三去北京开会", offset=2),
        factory.contact("北京有什么好吃的", offset=5),
        factory.owner("开完会就回来了，别安排太满", offset=8),
        factory.owner("下周的会取消了，改成线上", offset=60 * 24 * 2),  # 两天后
    ]
    expectations = [
        SceneExpectation(
            memory_expected=True,
            subject="owner",
            claim="下周会议取消并改为线上",
            claim_contains="线上",
            source_message_ids=["m5"],
            forbidden_claims=[],
            query="我下周要去哪开会？",
            query_lexical="线上",
            query_now_offset_minutes=(60 * 24 * 2) + 30,
            expected_evidence=["m5"],
            # The cancelled plan is relevant provenance for the revision. The
            # contract is that it no longer survives as an active plan.
            forbidden_evidence=[],
            temporal_status="historical",
        )
    ]
    return Scene(
        case_id="temporal-lifecycle",
        category="temporal",
        description="计划（下周去北京）→ 显式取消并改为线上：旧计划不得继续作为活跃计划召回",
        events=events,
        expectations=expectations,
    )


def _replay_idempotence() -> Scene:
    clock = SceneClock(Scene("", "", "").base_time)
    factory = EventFactory(self_id=SELF_ID, clock=clock, case_id="replay-idempotence")
    events = [
        factory.owner("我养了一只猫，叫年糕", offset=0),
        factory.contact("猫猫好可爱", offset=2),
        factory.owner("年糕是一只橘猫", offset=4),
    ]
    expectations = [
        SceneExpectation(
            memory_expected=True,
            subject="owner",
            claim="我养了一只猫叫年糕",
            claim_contains="年糕",
            source_message_ids=["m1", "m3"],
            forbidden_claims=[],
            query="我的猫叫什么？",
            query_lexical="年糕",
            query_now_offset_minutes=30,
            expected_evidence=["m1", "m3"],
            evidence_may_span_hits=True,
            forbidden_evidence=[],
            temporal_status="current",
        )
    ]
    return Scene(
        case_id="replay-idempotence",
        category="idempotence",
        description="同一数据集重放两次不得重复创建记忆（消息 ID 稳定 + 幂等写入）",
        events=events,
        expectations=expectations,
    )


def _two_distinct_trips() -> Scene:
    factory = EventFactory(
        self_id=SELF_ID,
        clock=SceneClock(Scene("", "", "").base_time),
        case_id="travel-count-two-distinct",
    )
    events = [
        factory.owner("去年国庆我去杭州旅游了五天，还逛了西湖", offset=0),
        factory.contact("杭州秋天挺舒服的", offset=3),
        factory.owner("今年五月我又去成都旅行了一周，吃了很多火锅", offset=10),
    ]
    return Scene(
        case_id="travel-count-two-distinct",
        category="episode_count",
        description="两次不同时间和地点的已完成旅行应形成两个稳定 event_key，计数为 2",
        events=events,
        expectations=[
            SceneExpectation(
                memory_expected=True,
                subject="owner",
                claim_contains="杭州",
                query="我一共旅行了几次？",
                expected_count_status="exact",
                expected_count=2,
                expected_distinct_event_keys=2,
            )
        ],
    )


def _same_trip_repeated() -> Scene:
    factory = EventFactory(
        self_id=SELF_ID,
        clock=SceneClock(Scene("", "", "").base_time),
        case_id="travel-count-repeat-same",
    )
    events = [
        factory.owner("去年国庆我去杭州旅游了五天", offset=0),
        factory.contact("你在杭州去了哪里？", offset=3),
        factory.owner("还是去年国庆那次杭州旅行，我去了西湖和灵隐寺", offset=8),
    ]
    return Scene(
        case_id="travel-count-repeat-same",
        category="episode_count",
        description="同一次杭州旅行被重复描述时可绑定多条证据，但只能计数一次",
        events=events,
        expectations=[
            SceneExpectation(
                memory_expected=True,
                subject="owner",
                claim_contains="杭州",
                query="我一共旅行了几次？",
                expected_count_status="exact",
                expected_count=1,
                expected_distinct_event_keys=1,
                expected_evidence=["m1", "m3"],
            )
        ],
    )


def _cancelled_trip_not_counted() -> Scene:
    factory = EventFactory(
        self_id=SELF_ID,
        clock=SceneClock(Scene("", "", "").base_time),
        case_id="travel-count-cancelled-plan",
    )
    events = [
        factory.owner("我计划下个月去北京旅行四天", offset=0),
        factory.contact("机票订好了吗？", offset=3),
        factory.owner("北京行程已经取消了，我最后没有去成", offset=8),
    ]
    return Scene(
        case_id="travel-count-cancelled-plan",
        category="episode_count",
        description="计划后明确取消且未成行，不得生成已完成 episode，旅行计数为 0",
        events=events,
        expectations=[
            SceneExpectation(
                memory_expected=False,
                subject="owner",
                claim_contains="北京",
                query="我一共旅行了几次？",
                expected_count_status="exact",
                expected_count=0,
                expected_distinct_event_keys=0,
            )
        ],
    )


def build_e2e_scenes() -> list[Scene]:
    """Contract scenes plus LLM-only extraction/count scenes."""
    return [
        *build_all_scenes(),
        _two_distinct_trips(),
        _same_trip_repeated(),
        _cancelled_trip_not_counted(),
    ]


def _kebab(name: str) -> str:
    return name.lstrip("_").replace("_", "-")


_BUILDERS: dict[str, callable] = {
    _kebab(scene.__name__): scene
    for scene in (
        _stable_preference,
        _noise_only,
        _explicit_correction,
        _subject_retraction,
        _unmarked_conflict,
        _temporary_trip,
        _agent_output,
        _group_speaker_isolation,
        _cross_conversation_shared,
        _temporal_lifecycle,
        _replay_idempotence,
    )
}
# case ids may differ from builder names; keep an explicit alias table.
_BUILDERS.update({builder().case_id: builder for builder in _BUILDERS.values()})
