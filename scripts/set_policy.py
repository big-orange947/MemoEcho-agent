# -*- coding: utf-8 -*-
"""会话值守配置命令行工具(查看 / 设置)。

用途:
  会话策略默认**全关**(不记录、不回复、不上报),上线时需要一个"人直接操作"的
  入口 —— 这个脚本就是它。前端 UI 做好之前,配置会话靠这里;做好之后两者等价
  (走的是同一套服务层与同一张表)。

用法:
  # 列出所有会话及其开设的开关
  uv run python scripts/set_policy.py list

  # 查看某个会话(按三元组,或直接用会话 ID)
  uv run python scripts/set_policy.py show --qq 2597164807
  uv run python scripts/set_policy.py show --conversation <conversation_id>

  # 只监视不回复(静默收集)
  uv run python scripts/set_policy.py set --qq 2597164807 --monitor

  # 开启自动回复,并写"注意事项"(存进会话 persona)
  uv run python scripts/set_policy.py set --qq 2597164807 --reply auto \
      --note "别答应晚上十点后的活动"

  # 群聊: 监视 + 上报,关键词筛"急事""改时间"
  uv run python scripts/set_policy.py set --group 123456 --monitor --alert \
      --keywords 急事,改时间

  # 关掉所有开关(回到静默)
  uv run python scripts/set_policy.py set --qq 2597164807 --off

说明: 本脚本直接操作本地数据库(与运行中的服务共用同一个 data 目录),
      无需服务在线;设置立即生效(服务读的就是这张表)。
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

# 允许脚本直接运行(python scripts/set_policy.py),无需安装包
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def _resolve(args) -> tuple[str, str, str]:
    """把命令行参数解析成会话三元组。"""
    if args.qq:
        return ("qq", "private", str(args.qq))
    if args.group:
        return ("qq", "group", str(args.group))
    if args.desktop:
        return ("desktop", "thread", str(args.desktop))
    raise SystemExit("需要指定会话: --qq <号> / --group <群号> / --desktop <线程ID>")


def _find(args):
    """按 ID 或三元组定位会话,返回会话字典(不存在则 None)。"""
    from app.services import conversations as conversations_service

    if args.conversation:
        return conversations_service.get_conversation(args.conversation)
    platform, chat_type, external_id = _resolve(args)
    return conversations_service.find_conversation(platform, chat_type, external_id)


def _format_row(row: dict, policy: dict) -> str:
    flags = []
    if policy["monitor"]:
        flags.append("监视")
    if policy["reply_mode"] != "off":
        flags.append(f"回复={policy['reply_mode']}")
    if policy["alert_enabled"]:
        flags.append("上报")
    if policy["allowed_tools"]:
        flags.append(f"工具={','.join(policy['allowed_tools'])}")
    state = " / ".join(flags) if flags else "(全关)"
    title = row.get("title") or ""
    where = f"{row.get('chat_type')}:{row.get('external_id')}"
    return f"{row['id'][:12]}…  {where:<24} {state}{('  ' + title) if title else ''}"


def cmd_list(args) -> int:
    from app.services import conversations as conversations_service
    from app.services import policy as policy_service

    rows = conversations_service.list_conversations(limit=args.limit)
    if not rows:
        print("(还没有任何会话)")
        return 0

    print(f"共 {len(rows)} 个会话(按最近活跃排序):\n")
    for row in rows:
        print("  " + _format_row(row, policy_service.normalize_policy(row)))
    print("\n提示: 用 set 子命令开启开关,例如 --qq 123456 --monitor")
    return 0


def cmd_show(args) -> int:
    from app.services import policy as policy_service

    row = _find(args)
    if row is None:
        print("会话不存在(尚未产生过任何事件)。用 set 子命令可直接创建并配置。")
        return 1

    policy = policy_service.normalize_policy(row)
    print(f"会话: {row['id']}")
    print(f"位置: {row.get('platform')} / {row.get('chat_type')} / {row.get('external_id')}")
    print(f"标题: {row.get('title') or '(无)'}")
    print(f"注意事项(persona): {row.get('persona') or '(无)'}")
    print("策略:")
    print(f"  monitor(监视)          : {'开' if policy['monitor'] else '关'}")
    print(f"  reply_mode(回复)       : {policy['reply_mode']}")
    print(f"  alert_enabled(上报)    : {'开' if policy['alert_enabled'] else '关'}")
    print(f"  alert_keywords(关键词) : {policy['alert_keywords'] or '(无)'}")
    print(f"  require_human_confirmation(请示): {'是' if policy['require_human_confirmation'] else '否'}")
    print(f"  攒批: {policy['digest_max_messages']} 条 / {policy['digest_window_seconds']} 秒")
    print(f"  allowed_tools(工具授权): {policy['allowed_tools'] or '(按会话类型默认)'}")
    return 0


def cmd_set(args) -> int:
    from app.services import conversations as conversations_service
    from app.services import policy as policy_service

    row = _find(args)
    if row is None:
        platform, chat_type, external_id = _resolve(args)
        conversation_id = conversations_service.ensure_conversation(platform, chat_type, external_id)
        print(f"已创建会话 {conversation_id}")
    else:
        conversation_id = row["id"]

    changes: dict = {}
    if args.off:
        # 一键回到静默: 三个开关全关
        changes.update(monitor=False, reply_mode="off", alert_enabled=False)
    else:
        if args.monitor:
            changes["monitor"] = True
        if args.no_monitor:
            changes["monitor"] = False
        if args.reply:
            changes["reply_mode"] = args.reply
        if args.alert:
            changes["alert_enabled"] = True
        if args.keywords is not None:
            changes["alert_keywords"] = args.keywords
        if args.tools is not None:
            changes["allowed_tools"] = args.tools
        if args.require_confirmation is not None:
            changes["require_human_confirmation"] = args.require_confirmation

    if changes:
        try:
            result = policy_service.update_policy(conversation_id, **changes)
        except ValueError as exc:
            print(f"✗ 设置失败: {exc}")
            return 1
        if result["changed"]:
            for field, (old, new) in result["changed"].items():
                print(f"  {field}: {old} → {new}")
        else:
            print("  (没有变化)")
        if result["implied"]:
            print(f"  ※ 自动打开: {', '.join(result['implied'])}")

    if args.note is not None:
        changed = conversations_service.update_profile(conversation_id, persona=args.note)
        if changed:
            print(f"  注意事项: {changed['persona'][0]!r} → {changed['persona'][1]!r}")

    if not changes and args.note is None:
        print("没有指定任何改动。可用: --monitor / --reply auto|draft|off / --alert / --keywords / --note / --off")

    print("\n当前状态:")
    updated = conversations_service.get_conversation(conversation_id) or {}
    print("  " + _format_row(updated, policy_service.normalize_policy(updated)))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="会话值守配置(查看/设置)")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_target(p):
        p.add_argument("--conversation", help="直接用会话 ID")
        p.add_argument("--qq", help="私聊 QQ 号")
        p.add_argument("--group", help="群号")
        p.add_argument("--desktop", help="桌面端线程 ID")

    p_list = sub.add_parser("list", help="列出所有会话")
    p_list.add_argument("--limit", type=int, default=50)
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="查看单个会话配置")
    add_target(p_show)
    p_show.set_defaults(func=cmd_show)

    p_set = sub.add_parser("set", help="设置会话配置")
    add_target(p_set)
    p_set.add_argument("--monitor", action="store_true", help="开启监视(记录消息)")
    p_set.add_argument("--no-monitor", action="store_true", help="关闭监视")
    p_set.add_argument("--reply", choices=["off", "draft", "auto"], help="回复模式")
    p_set.add_argument("--alert", action="store_true", help="开启重要消息上报")
    p_set.add_argument("--keywords", help="上报关键词(逗号分隔)")
    p_set.add_argument("--tools", help="工具授权(逗号分隔;群聊默认不给 send_qq_message)")
    p_set.add_argument("--require-confirmation", dest="require_confirmation", type=int, choices=[0, 1])
    p_set.add_argument("--note", help="注意事项(写入会话 persona)")
    p_set.add_argument("--off", action="store_true", help="一键关闭所有开关")
    p_set.set_defaults(func=cmd_set)

    args = parser.parse_args()

    # 确保表结构就绪(首次运行也能直接配置)
    from app.db import init_db

    init_db()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
