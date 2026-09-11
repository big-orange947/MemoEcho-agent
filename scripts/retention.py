# -*- coding: utf-8 -*-
"""存储清理命令行工具(先看清楚,再决定删不删)。

用途:
  监视中的群聊会持续写入 —— 这个脚本用来查看存储占用、预览将要删除的内容,
  确认无误后再真正执行。默认 **dry-run(只看不删)**,要删必须显式 --apply。

为什么默认不删:
  删数据不可逆。默认 dry-run 让"看一眼"永远是安全的 ——
  想删的人不会嫌多打一个参数,但不小心敲错的人会感谢这个默认值。

用法:
  # 看存储占用与行数
  uv run python scripts/retention.py stats

  # 预览会删什么(默认,不执行)
  uv run python scripts/retention.py plan

  # 真正执行清理
  uv run python scripts/retention.py apply

  # 临时改保留天数预览(不写配置)
  uv run python scripts/retention.py plan --message-days 30 --keep-min 50
"""
from __future__ import annotations

import argparse
import asyncio
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def _print_policy(plan: dict) -> None:
    policy = plan["policy"]
    print("保留策略:")
    print(f"  消息     {policy['messages_days'] or '永久'} 天(每会话保底 {policy['messages_keep_min']} 条)")
    print(f"  事件审计 {policy['events_days'] or '永久'} 天")
    print(f"  调度记录 {policy['dispatches_days'] or '永久'} 天")
    print(f"  定时唤醒 {policy['schedules_days'] or '永久'} 天")
    print(f"  上报记录 {policy['reports_days'] or '永久'} 天")
    print(f"  checkpoint 精简 {policy['checkpoints_days'] or '关闭'} 天")
    print()


def _print_plan(plan: dict) -> None:
    _print_policy(plan)
    messages = plan["messages"]
    print(f"消息: 可删 {messages['candidates']} 条")
    for item in messages["conversations"][:20]:
        print(
            f"  · {item['label']}(共 {item['total']} 条,可删 {item['deletable']},"
            f"保底 {item['kept_floor']},水位线 {item['watermark'] or '无'})"
        )
    if len(messages["conversations"]) > 20:
        print(f"  ...还有 {len(messages['conversations']) - 20} 个会话")
    for item in messages["skipped"][:10]:
        label = item.get("label") or ""
        print(f"  [跳过] {label} {item['reason']}")
    if len(messages["skipped"]) > 10:
        print(f"  ...还有 {len(messages['skipped']) - 10} 条跳过原因")
    print()

    for key, name in (
        ("events", "事件审计"),
        ("dispatches", "调度记录"),
        ("schedules", "定时唤醒"),
        ("reports", "上报记录"),
    ):
        section = plan[key]
        note = f"({section.get('note')})" if section.get("note") else ""
        print(f"{name}: 可删 {section['candidates']} 条 {note}")
    checkpoints = plan.get("checkpoints") or {}
    print(f"checkpoint: 可精简 {checkpoints.get('dormant_threads', 0)} 个休眠会话 {checkpoints.get('note', '')}")
    print()
    print(f"合计可删 {plan['total_candidates']} 行")


def cmd_stats(args) -> int:
    from app import retention
    from app.db import init_db

    init_db()
    stats = retention.storage_overview()
    print("数据库文件:")
    for name, info in stats["files"].items():
        print(f"  {name:<20} {info['human']:>10}")
    print(f"  {'合计':<20} {stats['total_human']:>10}")
    print()
    print("主要表行数:")
    for table, count in stats["rows"].items():
        shown = f"{count}" if count >= 0 else "表不存在"
        print(f"  {table:<20} {shown:>10}")
    return 0


async def _plan(args) -> int:
    from app.db import init_db
    from app import retention

    init_db()
    plan = await retention.plan()
    _print_plan(plan)
    print()
    print("(以上为预览。要真正执行请加 --apply)")
    return 0


async def _apply(args) -> int:
    from app.db import init_db
    from app import retention

    init_db()
    plan = await retention.plan()
    print("=" * 60)
    print("将要删除的内容:")
    print("=" * 60)
    _print_plan(plan)
    print()
    if plan["total_candidates"] == 0 and not (plan.get("checkpoints") or {}).get("dormant_threads"):
        print("没有需要清理的内容。")
        return 0

    result = await retention.apply()
    print("=" * 60)
    print("已删除:")
    for table, count in result["deleted"].items():
        if count:
            print(f"  {table:<20} {count:>8}")
    if result.get("checkpoints_pruned"):
        print(f"  {'checkpoint 精简':<20} {result['checkpoints_pruned']:>8} 个会话")
    print(f"  {'合计':<20} {result['total_deleted']:>8} 行")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="存储清理(默认只看不删)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_stats = sub.add_parser("stats", help="查看存储占用与行数")
    p_stats.set_defaults(func=cmd_stats)

    p_plan = sub.add_parser("plan", help="预览会删什么(默认,不执行)")
    p_plan.set_defaults(func=lambda args: asyncio.run(_plan(args)))

    p_apply = sub.add_parser("apply", help="执行清理")
    p_apply.set_defaults(func=lambda args: asyncio.run(_apply(args)))

    args = parser.parse_args()
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
