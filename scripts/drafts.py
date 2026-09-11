# -*- coding: utf-8 -*-
"""待确认草稿的命令行工具(看 / 发 / 丢)。

背景: `reply_mode=draft` 的会话里,agent 拟好的回复**不会**自己发出去,
而是进上报队列的 draft 通道等人确认。本脚本就是"人"的那个入口。

为什么走 HTTP 而不是直接读库:
  发送必须由**持有 NapCat 连接的那个进程**完成(只有它有到 QQ 的通道)。
  另起一个进程读库再发,等于伪造一条连接 —— 消息会发不出去,或发出两条。
  所以本脚本是运行中服务的瘦客户端(同 scripts/smoke_*.py 的做法)。

用法:
  # 看有哪些待确认草稿
  uv run python scripts/drafts.py list

  # 看全文
  uv run python scripts/drafts.py show <id>

  # 原样发出 / 改一改再发
  uv run python scripts/drafts.py send <id>
  uv run python scripts/drafts.py send <id> --text "八点半行吗?"

  # 不合适,丢弃(不发,但留记录)
  uv run python scripts/drafts.py drop <id> --reason "太生硬"

  服务不在默认地址时: --base http://127.0.0.1:8000
  配了 MEMO_ECHO_API_TOKEN 时自动带上(也可用 --token)。
"""
from __future__ import annotations

import argparse
import io
import os
import sys

import httpx

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def _headers(args) -> dict[str, str]:
    token = args.token or os.environ.get("MEMO_ECHO_API_TOKEN") or ""
    return {"X-API-Token": token} if token else {}


def _client(args) -> httpx.Client:
    return httpx.Client(base_url=args.base.rstrip("/"), timeout=60, headers=_headers(args))


def _preview(text: str, width: int = 40) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= width else text[: width - 1] + "…"


def cmd_list(args) -> int:
    params = {"lane": "draft"}
    if not args.all:
        params["status"] = "pending"
    with _client(args) as client:
        response = client.get("/api/reports", params=params)
        response.raise_for_status()
        items = response.json()

    if not items:
        print("没有待确认的草稿。")
        return 0

    print(f"共 {len(items)} 条:")
    for item in items:
        payload = item.get("payload") or {}
        text = _preview(payload.get("text") or payload.get("summary") or "")
        print(f"  {item['id']}  [{item.get('status')}]  {item.get('created_at', '')[:19]}")
        print(f"      {text}")
    print()
    print("发出: scripts/drafts.py send <id>   丢弃: scripts/drafts.py drop <id>")
    return 0


def cmd_show(args) -> int:
    with _client(args) as client:
        response = client.get("/api/reports", params={"lane": "draft"})
        response.raise_for_status()
        items = [item for item in response.json() if item["id"] == args.id]

    if not items:
        print(f"没有这条草稿: {args.id}", file=sys.stderr)
        return 1
    item = items[0]
    payload = item.get("payload") or {}
    print(f"ID:     {item['id']}")
    print(f"状态:   {item.get('status')}")
    print(f"会话:   {item.get('conversation_id')}")
    print(f"创建:   {item.get('created_at')}")
    print("-" * 60)
    print(payload.get("text") or payload.get("summary") or "(空)")
    return 0


def cmd_send(args) -> int:
    body = {"text": args.text} if args.text else {}
    with _client(args) as client:
        response = client.post(f"/api/reports/{args.id}/send", json=body)
    if response.status_code != 200:
        detail = ""
        try:
            detail = response.json().get("detail") or ""
        except Exception:  # noqa: BLE001 - 非 JSON 响应时保留原文
            detail = response.text
        print(f"发送失败({response.status_code}): {detail}", file=sys.stderr)
        print("记录未被标记完成,修好后可以重试。", file=sys.stderr)
        return 1

    result = response.json()
    print(f"已发出(会话 {result.get('conversation_id')},消息 {result.get('message_id') or '-'})")
    return 0


def cmd_drop(args) -> int:
    with _client(args) as client:
        response = client.post(f"/api/reports/{args.id}/drop", json={"reason": args.reason})
    if response.status_code != 200:
        print(f"操作失败({response.status_code}): {response.text}", file=sys.stderr)
        return 1
    print("已丢弃(记录保留在队列里,可在审计中查到)。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="待确认草稿: 看 / 发 / 丢")
    parser.add_argument("--base", default=os.environ.get("MEMO_ECHO_API_BASE") or "http://127.0.0.1:8000")
    parser.add_argument("--token", default="", help="API token(默认读 MEMO_ECHO_API_TOKEN)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="列出待确认草稿")
    p_list.add_argument("--all", action="store_true", help="连已处理的也列出来")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="看某条草稿的全文")
    p_show.add_argument("id")
    p_show.set_defaults(func=cmd_show)

    p_send = sub.add_parser("send", help="把草稿发给对方")
    p_send.add_argument("id")
    p_send.add_argument("--text", default="", help="改一改再发(不传则用草稿原文)")
    p_send.set_defaults(func=cmd_send)

    p_drop = sub.add_parser("drop", help="丢弃草稿(不发)")
    p_drop.add_argument("id")
    p_drop.add_argument("--reason", default="")
    p_drop.set_defaults(func=cmd_drop)

    args = parser.parse_args()
    try:
        return int(args.func(args) or 0)
    except httpx.ConnectError:
        print("连不上服务(脚本需要运行中的 Runtime，它才持有 QQ 通道)。", file=sys.stderr)
        print(f"地址: {args.base}  可用 --base 指定", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
