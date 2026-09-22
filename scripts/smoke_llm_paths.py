# -*- coding: utf-8 -*-
"""真模型冒烟: 验证两条"只跑过假模型"的路径。

为什么要单独一个脚本:
  单元测试用假模型验证的是**调用与解析的接线**,验证不了"提示词在真模型上
  到底产不产得出能解析的东西"。而这两条路径的失效方式都是**静默**的:
    · 攒批记忆: 解析失败 → 当成"没值得记的" → 水位线推进 → 那批消息永远不再总结
      (逐条写记忆已被移除,攒批是长期记忆的**唯一入口**);
    · 上报复核: 解析失败 → 退化为纯规则(这条有兜底,但会失去"重要性"判断)。
  所以必须真调一次,并留下可读的结论。

本脚本的特点:
  · 用**生产代码本身**(batches._llm_summarize / reports.build_default_reviewer),
    不是复制一份提示词来测 —— 复制品测过了也不代表线上那行代码能跑;
  · 不碰数据库、不碰网络消息、不发任何 QQ 消息,只调用 LLM API;
  · 输出人可读的判定(每项 PASS/FAIL + 模型原始输出摘要),便于人工复核质量。

用法:
  uv run python scripts/smoke_llm_paths.py

退出码: 0 = 全部通过; 1 = 有失败项(便于接入 CI 或手工判断)。
"""
from __future__ import annotations

import asyncio
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PASS = "✅ PASS"
FAIL = "❌ FAIL"
results: list[tuple[str, bool]] = []


def report(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok))
    print(f"{PASS if ok else FAIL}  {name}")
    if detail:
        for line in detail.strip().splitlines():
            print(f"    {line}")
    print()


# ---------------------------------------------------------------------------
# 用例一: 攒批总结(长期记忆的唯一入口)
# ---------------------------------------------------------------------------
# 一段有信息量的对话: 里面有值得长期记住的事实(课表、约定、偏好),
# 也夹杂大量寒暄("哈哈哈""嗯嗯")—— 用来验证提示词能否分辨两者。
CHATTY_DIALOGUE = [
    {"actor": "contact", "content": "哈哈哈哈刚下课", "created_at": "2026-09-10T12:00:00+00:00"},
    {"actor": "agent", "content": "哦哦", "created_at": "2026-09-10T12:01:00+00:00"},
    {"actor": "contact", "content": "我这学期周三晚上的课调到八点半了", "created_at": "2026-09-10T12:02:00+00:00"},
    {"actor": "contact", "content": "对了我不喝冰的，别给我带冰美式", "created_at": "2026-09-10T12:03:00+00:00"},
    {"actor": "agent", "content": "好，记下了，那以后给你带常温的", "created_at": "2026-09-10T12:04:00+00:00"},
    {"actor": "contact", "content": "嗯嗯好的谢谢", "created_at": "2026-09-10T12:05:00+00:00"},
    {"actor": "contact", "content": "周末要不要一起打球", "created_at": "2026-09-10T12:06:00+00:00"},
    {"actor": "agent", "content": "行啊，周六下午吧", "created_at": "2026-09-10T12:07:00+00:00"},
]

# 纯寒暄: 应该一条都不记(验证"宁可不记,也别记流水账")
SMALL_TALK = [
    {"actor": "contact", "content": "哈哈哈", "created_at": "2026-09-10T13:00:00+00:00"},
    {"actor": "agent", "content": "😄", "created_at": "2026-09-10T13:00:10+00:00"},
    {"actor": "contact", "content": "嗯嗯", "created_at": "2026-09-10T13:00:20+00:00"},
    {"actor": "contact", "content": "好的好的", "created_at": "2026-09-10T13:00:30+00:00"},
]

# "先定后改": 用来验证总结器会不会给出槽位、会不会标出明确订正 ——
# 事实过期/冲突整理全靠这两个字段(见 app/consolidation.py)。
SCHEDULE_CHANGE = [
    {"actor": "contact", "content": "我这学期周三晚上有课", "created_at": "2026-09-10T14:00:00+00:00"},
    {"actor": "agent", "content": "好，记下了", "created_at": "2026-09-10T14:00:10+00:00"},
    {"actor": "contact", "content": "对了课表改了，周三那节课调到周五晚上了", "created_at": "2026-09-11T14:00:00+00:00"},
]


async def case_summarizer() -> None:
    from app import batches

    print("=" * 72)
    print("用例一: 攒批记忆总结(长期记忆的唯一入口)")
    print("=" * 72)

    batches.reset_summary_stats()
    conversation = {"id": "smoke", "platform": "qq", "chat_type": "private", "external_id": "1"}

    # -- 1.1 有信息量的对话: 应能提炼出事实,且 JSON 可解析 --
    notes = await batches._llm_summarize(conversation, CHATTY_DIALOGUE)
    health = batches.summary_health()
    detail = "\n".join(f"· [{n.actor}/{n.kind}] {n.content}" for n in notes) or "(无)"
    report(
        "1.1 有信息量的对话 → 产出可解析的记忆",
        len(notes) > 0 and health["unparsed"] == 0,
        f"产出 {len(notes)} 条:\n{detail}",
    )

    # -- 1.2 质量: 该记的记到了(课表 / 冷饮偏好 / 约球) --
    joined = " ".join(n.content for n in notes)
    quality_hits = [
        ("课表变更", any(k in joined for k in ("八点半", "周三", "调"))),
        ("冷饮偏好", any(k in joined for k in ("冰", "常温"))),
        ("约球约定", any(k in joined for k in ("周六", "球"))),
    ]
    missed = [name for name, hit in quality_hits if not hit]
    report(
        "1.2 质量: 关键事实没漏(课表/偏好/约定)",
        not missed,
        "命中: " + "、".join(n for n, h in quality_hits if h) + (f"\n漏掉: {'、'.join(missed)}" if missed else ""),
    )

    # -- 1.3 不该记的没记(寒暄不该出现) --
    noise = [n.content for n in notes if any(k in n.content for k in ("哈哈", "嗯嗯", "谢谢", "😄"))]
    report("1.3 质量: 没有把寒暄记成记忆", not noise, f"可疑条目: {noise}" if noise else "")

    # -- 1.4 纯寒暄: 应返回空(不凑数) --
    batches.reset_summary_stats()
    notes_small = await batches._llm_summarize(conversation, SMALL_TALK)
    health_small = batches.summary_health()
    report(
        "1.4 纯寒暄 → 一条都不记(且不是解析失败)",
        len(notes_small) == 0 and health_small["unparsed"] == 0,
        f"产出 {len(notes_small)} 条;解析失败 {health_small['unparsed']} 次",
    )

    # -- 1.5 槽位与修订标记: 事实过期/冲突整理全靠这两个字段 --
    # 提示词里写了"明说改了才写 correction",但真模型会不会照做只能真跑一次看。
    # 这里用一段"先定后改"的对话验证: 该给的槽位给了、该标的订正标了。
    batches.reset_summary_stats()
    notes_change = await batches._llm_summarize(conversation, SCHEDULE_CHANGE)
    detail_change = "\n".join(
        f"· [{n.actor}/{n.kind}] slot={n.topic_key or '-'} revision={n.revision_kind}"
        f" temporal={n.temporal_status}\n  {n.content}"
        for n in notes_change
    ) or "(无)"
    slotted = [n for n in notes_change if n.topic_key]
    corrected = [n for n in notes_change if n.revision_kind in ("correction", "retraction")]
    report(
        "1.5 槽位/修订: 改了课表 → 至少一条带 slot,且订正被标出来",
        bool(slotted) and bool(corrected),
        f"产出 {len(notes_change)} 条(带槽位 {len(slotted)}、标了订正 {len(corrected)}):\n{detail_change}",
    )


# ---------------------------------------------------------------------------
# 用例二: 上报复核(重要消息判定)
# ---------------------------------------------------------------------------
REVIEW_CANDIDATES = [
    {
        "id": "m1",
        "payload": {
            "text": "你今晚还来不来？我七点就得走了，来不了我就先回了",
            "sender_name": "km",
            "reasons": ["pattern:吗", "keyword:今晚"],
        },
    },
    {
        "id": "m2",
        "payload": {
            "text": "哈哈哈刚看到你发的朋友圈",
            "sender_name": "路人甲",
            "reasons": ["pattern:看"],
        },
    },
    {
        "id": "m3",
        "payload": {
            "text": "明天下午三点的组会改到四点了，会议室还是A301",
            "sender_name": "辅导员",
            "reasons": ["pattern:三点"],
        },
    },
]


async def case_reviewer() -> None:
    from app.reports import build_default_reviewer
    from langchain_openai import ChatOpenAI

    from app.config import get_settings

    print("=" * 72)
    print("用例二: 上报复核(判断哪些消息值得打扰号主)")
    print("=" * 72)

    settings = get_settings()
    factory = lambda fast=True: ChatOpenAI(  # noqa: E731 - 冒烟脚本, 简洁优先
        model=settings.fast_model_name,
        temperature=0.2,
        timeout=settings.llm_timeout_seconds,
        api_key=settings.api_key or None,
        base_url=settings.base_url or None,
    )

    reviewer = build_default_reviewer(factory)
    try:
        verdicts = await reviewer(REVIEW_CANDIDATES)
        error = ""
    except Exception as exc:  # noqa: BLE001 - 冒烟脚本要报告失败而不是崩掉
        verdicts, error = [], f"{type(exc).__name__}: {exc}"

    detail = json.dumps(verdicts, ensure_ascii=False, indent=2) if verdicts else (error or "(空)")
    report("2.1 复核输出可解析(JSON 数组)", bool(verdicts) and not error, detail)

    if not verdicts:
        return

    by_id = {str(v.get("id")): v for v in verdicts}
    report(
        "2.2 覆盖: 每条候选都有判定",
        all(c["id"] in by_id for c in REVIEW_CANDIDATES),
        f"返回 id: {sorted(by_id)}",
    )

    # 有时间压力的那条应比闲聊更"急"
    lanes = {k: str(v.get("lane") or "") for k, v in by_id.items()}
    rank = {"digest": 0, "normal": 1, "urgent": 2}
    ok_priority = rank.get(lanes.get("m1", ""), -1) > rank.get(lanes.get("m2", ""), 99)
    report(
        "2.3 质量: 有紧迫性的(要走了)比闲聊更急",
        ok_priority,
        f"m1(七点就走)= {lanes.get('m1')} vs m2(闲聊)= {lanes.get('m2')}",
    )
    report(
        "2.4 质量: 每条都带摘要",
        all(str(v.get("summary") or "").strip() for v in verdicts),
        "\n".join(f"· {k}: {v.get('summary')}" for k, v in by_id.items()),
    )


# ---------------------------------------------------------------------------
# 用例三: 解析器的"静默失败"防线(不起真实模型,只验判定逻辑)
# ---------------------------------------------------------------------------
def case_parsers() -> None:
    from app import batches
    from app.reports import parse_review_verdicts

    print("=" * 72)
    print("用例三: 解析器的静默失败防线")
    print("=" * 72)

    # 攒批: 有输出但无法解析 → 必须标记为可疑(否则那批消息会被永久跳过而不报警)
    _, unparsed = batches.parse_notes_with_diagnostics("抱歉，我觉得这段对话没什么值得记录的。")
    report("3.1 总结器: 非 JSON 输出被标记为「可疑」", unparsed, "")

    notes, unparsed_ok = batches.parse_notes_with_diagnostics("[]")
    report("3.2 总结器: 明确返回 [] 不算可疑", (not unparsed_ok) and notes == [], "")

    # 上报复核: 解析失败必须抛(由上层退化为纯规则),不能静默返回空
    try:
        parse_review_verdicts("无法判断。")
        raised = False
    except ValueError:
        raised = True
    report("3.3 复核器: 非 JSON 输出抛异常(触发退化)", raised, "")


async def main() -> int:
    print()
    print("真模型冒烟: 两条只跑过假模型的路径")
    print()

    from app.config import get_settings

    settings = get_settings()
    if not settings.api_key:
        print("❌ 没有可用的 API Key(检查 .env 的 OPENAI_API_KEY),无法进行真模型验证。")
        return 1
    print(f"模型: {settings.fast_model_name} @ {settings.base_url}")
    print()

    await case_summarizer()
    await case_reviewer()
    case_parsers()

    print("=" * 72)
    passed = sum(1 for _, ok in results if ok)
    failed = [name for name, ok in results if not ok]
    print(f"结果: {passed}/{len(results)} 通过")
    if failed:
        print("失败项:")
        for name in failed:
            print(f"  · {name}")
    print("=" * 72)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
