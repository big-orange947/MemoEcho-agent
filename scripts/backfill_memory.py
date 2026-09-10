# -*- coding: utf-8 -*-
"""历史回填: 把 v2 已有对话导入 Doppel 长期记忆。

用途: 接入记忆功能时,让 Doppel 知道"以前聊过什么",而不是从零开始。

用法:
  uv run python scripts/backfill_memory.py --conversation <会话ID>   # 单个会话
  uv run python scripts/backfill_memory.py --all                      # 全部会话
  uv run python scripts/backfill_memory.py --all --dry-run             # 只看会做什么

幂等: Doppel 按 message_id 去重,重复回填不会产生重复记忆。
"""
from __future__ import annotations

import argparse
import asyncio
import io
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# 保证能 import app.*
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def main() -> int:
    parser = argparse.ArgumentParser(description="把历史对话回填进 Doppel 长期记忆")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--conversation", help="只回填指定会话 ID")
    group.add_argument("--all", action="store_true", help="回填所有会话")
    parser.add_argument("--limit", type=int, default=500, help="每个会话最多回填多少条消息")
    parser.add_argument("--dry-run", action="store_true", help="只统计,不实际写入")
    args = parser.parse_args()

    from app import memory
    from app.db import init_db
    from app.services import conversations as convs

    init_db()

    if not memory.is_enabled():
        print("✗ 记忆功能未启用。请检查:")
        print("  1) 是否安装 Doppel: uv pip install -e D:\\project\\Doppel")
        print("  2) .env 里 MEMO_ECHO_DOPPEL_ENABLED=true")
        err = memory.init_error()
        if err:
            print(f"  初始化错误: {err}")
        return 1

    # 确定要处理的会话列表
    if args.all:
        targets = convs.list_conversations(limit=200)
    else:
        conversation = convs.get_conversation(args.conversation)
        if not conversation:
            print(f"✗ 会话不存在: {args.conversation}")
            return 1
        targets = [conversation]

    print(f"准备回填 {len(targets)} 个会话" + ("(dry-run,不写入)" if args.dry_run else ""))
    print()

    total_accepted = 0
    total_skipped = 0

    for conversation in targets:
        conv_id = conversation["id"]
        messages = convs.list_messages(conv_id, limit=args.limit)
        label = f"{conversation.get('platform')}/{conversation.get('chat_type')}/{conversation.get('external_id')}"

        if not messages:
            print(f"  跳过 {label}: 没有消息")
            continue

        if args.dry_run:
            print(f"  [dry-run] {label}: 将回填 {len(messages)} 条")
            total_accepted += len(messages)
            continue

        result = await memory.remember_batch(conversation, messages)
        if result["error"]:
            print(f"  ✗ {label}: {result['error']}")
            continue

        total_accepted += result["accepted"]
        total_skipped += result["skipped"]
        print(f"  ✓ {label}: 写入 {result['accepted']} 条,跳过 {result['skipped']} 条")

    await memory.close_client()

    print()
    if args.dry_run:
        print(f"dry-run 结束: 预计写入 {total_accepted} 条")
    else:
        print(f"回填完成: 写入 {total_accepted} 条,跳过 {total_skipped} 条(重复)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
