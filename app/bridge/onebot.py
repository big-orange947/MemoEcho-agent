# =============================================================================
# bridge/onebot.py - OneBot 11 消息/事件解析器
# -----------------------------------------------------------------------------
# 职责: 把 NapCat (OneBot 11) 推送的原始 JSON,翻译成 v2 的内部结构:
#   - parse_content():  消息段数组 → list[ContentPart](平台无关内容段)
#   - build_event():    完整事件载荷 → Event(带分流标记 should_respond)
#
# 为什么单独一个文件:
#   协议细节(NapCat/OneBot 的字段名、段类型)集中在这一个文件里,
#   上层节点只认 ContentPart / Event,将来换平台(微信等)只需新增解析器。
#
# 覆盖范围(NapCat 文档 onebot/segment 与 onebot/event):
#   消息段: text / at / reply / face / mface / dice / rps / poke /
#           image / record / video / file / json / forward / music
#   事件:   post_type = message / message_sent / notice / request / meta_event
#
# 关键设计:
#   1. 未知段类型不丢弃 —— 归为 unknown 并保留原始数据,保证可追溯;
#   2. should_respond 由解析器判定(平台规则),agent 只认这个开关;
#   3. event_id 尽量用平台 message_id,保证重复推送的幂等去重。
# =============================================================================

from __future__ import annotations

import time
from typing import Any

from ..content import ContentPart, PartType, render
from ..events import Event

# ---------------------------------------------------------------------------
# 事件类型(kind)常量 —— 与 OneBot post_type 对齐,并补充 v2 内部类型
# ---------------------------------------------------------------------------
KIND_MESSAGE = "message"            # 别人发来的消息(需要回应)
KIND_MESSAGE_SENT = "message_sent"  # 机器人自己发的回显(绝不回应,防自循环)
KIND_NOTICE = "notice"              # 通知事件(撤回/戳一戳/群变动…)
KIND_REQUEST = "request"            # 请求事件(好友申请/加群申请)
KIND_META = "meta"                  # 元事件(心跳/生命周期)
KIND_UNKNOWN = "unknown"            # 未识别事件


# ---------------------------------------------------------------------------
# 消息段解析
# ---------------------------------------------------------------------------
def parse_content(message: str | list[Any] | None) -> list[ContentPart]:
    """把 OneBot 的 message 字段解析成内容段列表。

    支持两种形态:
      - 字符串: 旧格式/纯文本(直接包成单个 text 段);
      - 数组:   标准 OneBot 消息段数组(逐段解析)。

    未知类型 → ContentPart.unknown_part(保留 raw_type 与 data)。
    """
    # 空消息: 返回空列表(调用方应视为"无内容")
    if message is None:
        return []

    # 形态一: 纯字符串(NapCat 配 messagePostFormat=string 时,或历史数据)
    if isinstance(message, str):
        return [ContentPart.text_part(message)] if message else []

    # 形态二: 段数组
    parts: list[ContentPart] = []
    for segment in message:
        if not isinstance(segment, dict):
            # 数组里混入非字典元素: 记为未知段,不丢数据
            parts.append(ContentPart.unknown_part(type(segment).__name__, {"value": str(segment)}))
            continue

        raw_type = str(segment.get("type") or "")
        data = segment.get("data") or {}
        if not isinstance(data, dict):
            data = {"value": str(data)}

        part = _parse_one_segment(raw_type, data)
        if part is not None:
            parts.append(part)

    return parts


def _parse_one_segment(raw_type: str, data: dict[str, Any]) -> ContentPart | None:
    """解析单个消息段。

    返回 None 表示该段无意义可忽略(目前不产生,保留扩展点)。
    """
    # ---- 文本: 直接取 text ----
    if raw_type == PartType.TEXT:
        text = str(data.get("text") or "")
        return ContentPart(type=PartType.TEXT, text=text, data=dict(data))

    # ---- @ 提及: 核心是 qq 字段(可能是 "all") ----
    if raw_type == PartType.AT:
        qq = str(data.get("qq") or "")
        return ContentPart(
            type=PartType.AT,
            text=f"@{qq}" if qq != "all" else "@全体成员",
            data=dict(data),
        )

    # ---- 引用回复: 指向被回复消息的 id ----
    if raw_type == PartType.REPLY:
        return ContentPart(type=PartType.REPLY, text="", data=dict(data))

    # ---- 表情/骰子/猜拳/戳一戳: 保留原始字段,渲染为占位符 ----
    if raw_type in (PartType.FACE, PartType.MFACE, PartType.DICE, PartType.RPS, PartType.POKE):
        return ContentPart(type=raw_type, text="", data=dict(data))

    # ---- 图片: 接收时通常带 url / file / file_size / summary ----
    if raw_type == PartType.IMAGE:
        return ContentPart(type=PartType.IMAGE, text="", data=dict(data))

    # ---- 语音: file / path / file_size ----
    if raw_type == PartType.RECORD:
        return ContentPart(type=PartType.RECORD, text="", data=dict(data))

    # ---- 视频: file / url / file_size / thumb ----
    if raw_type == PartType.VIDEO:
        return ContentPart(type=PartType.VIDEO, text="", data=dict(data))

    # ---- 文件: 接收时 file 是文件名,另有 file_id / file_size ----
    if raw_type == PartType.FILE:
        return ContentPart(type=PartType.FILE, text="", data=dict(data))

    # ---- JSON 卡片(分享链接等): data.data 是 JSON 字符串或对象 ----
    if raw_type == PartType.JSON:
        return ContentPart(type=PartType.JSON, text="", data=dict(data))

    # ---- 合并转发: id 是转发消息 ID;解析内容时另带 content ----
    if raw_type == PartType.FORWARD:
        return ContentPart(type=PartType.FORWARD, text="", data=dict(data))

    # ---- 音乐分享(仅发送侧;接收时平台常转成 json) ----
    if raw_type == PartType.MUSIC:
        return ContentPart(type=PartType.MUSIC, text="", data=dict(data))

    # ---- 未识别类型: 保留原始类型名与数据,绝不丢弃 ----
    return ContentPart.unknown_part(raw_type, data)


# ---------------------------------------------------------------------------
# 事件解析
# ---------------------------------------------------------------------------
def build_event(body: dict[str, Any], *, bot_qq: str = "", group_require_at: bool = True) -> Event | None:
    """把 OneBot 事件载荷转换成 v2 的内部 Event。

    参数:
      body:              NapCat 推送的原始 JSON
      bot_qq:            机器人自己的 QQ 号(用于判断"是否 @ 了我")
      group_require_at:  群聊是否只在被 @ 时才回应(默认 True,避免刷屏)

    返回:
      Event,或 None(该事件无需进入处理链路,例如心跳)。

    分流规则(summary):
      post_type=message,      private → kind=message, should_respond=True
      post_type=message,      group   → kind=message, should_respond=(被@ 或 不要求@)
      post_type=message_sent         → kind=message_sent, should_respond=False(自发回显)
      post_type=notice               → kind=notice,  should_respond=False(仅审计)
      post_type=request              → kind=request, should_respond=False(仅审计)
      post_type=meta_event           → 返回 None(心跳/生命周期不入库)
    """
    post_type = str(body.get("post_type") or "")

    # ---- 元事件: 心跳/生命周期,不入业务链路 ----
    if post_type == "meta_event":
        return None

    # ---- 消息事件(收/发) ----
    if post_type in ("message", "message_sent"):
        return _build_message_event(body, post_type, bot_qq=bot_qq, group_require_at=group_require_at)

    # ---- 通知事件: 只记录,不主动回应(具体反应策略由上层决定) ----
    if post_type == "notice":
        return _build_notice_event(body)

    # ---- 请求事件(好友申请/加群申请) ----
    if post_type == "request":
        return _build_request_event(body)

    # ---- 其他未知 post_type: 记为 unknown,仅审计 ----
    return Event(
        event_id=_event_id(body, prefix="unknown"),
        source="qq",
        kind=KIND_UNKNOWN,
        should_respond=False,
        platform="qq",
        chat_type="",
        external_id="",
        text="",
        raw=body,
    )


# ---------------------------------------------------------------------------
# 各类型事件的具体构造
# ---------------------------------------------------------------------------
def _build_message_event(
    body: dict[str, Any], post_type: str, *, bot_qq: str, group_require_at: bool
) -> Event:
    """构造消息类事件(message / message_sent)。"""
    message_type = str(body.get("message_type") or "private")
    user_id = str(body.get("user_id") or "")
    group_id = str(body.get("group_id") or "")

    # 内容段解析(两种格式都支持)
    parts = parse_content(body.get("message"))
    text = render(parts)
    if not text:
        # 兜底: 极少数情况 message 为空但 raw_message 有值(如纯表情的类型)
        text = str(body.get("raw_message") or "")

    # 会话定位: 私聊按对方 QQ,群聊按群号
    if message_type == "group":
        chat_type, external_id = "group", group_id
    else:
        chat_type, external_id = "private", user_id

    # 是否要回应:
    #   - 自发回显(message_sent)一律不回应(防止"自己触发自己"的死循环);
    #   - 群聊默认只在被 @ 时回应(可配置关闭);
    #   - 私聊消息一律回应。
    is_self = post_type == "message_sent"
    if is_self:
        should_respond = False
    elif chat_type == "group" and group_require_at:
        should_respond = _is_at_bot(parts, bot_qq)
    else:
        should_respond = True

    # 发送者信息(nickname/card 等)原样保留在 raw,供后续取用
    sender = body.get("sender") or {}

    return Event(
        event_id=_event_id(body, prefix=post_type),
        source="qq",
        kind=KIND_MESSAGE if post_type == "message" else KIND_MESSAGE_SENT,
        should_respond=should_respond,
        platform="qq",
        chat_type=chat_type,
        external_id=external_id,
        content=parts,
        text=text,
        sender_id=user_id,
        sender_name=str(sender.get("card") or sender.get("nickname") or ""),
        is_self=is_self,
        raw=body,
    )


def _build_notice_event(body: dict[str, Any]) -> Event:
    """构造通知类事件(撤回/戳一戳/群成员变动/禁言…)—— 仅审计。"""
    notice_type = str(body.get("notice_type") or "")
    sub_type = str(body.get("sub_type") or "")
    group_id = str(body.get("group_id") or "")
    user_id = str(body.get("user_id") or "")

    # 人类可读描述: 便于在 events 表里直接看懂发生了什么
    summary = _describe_notice(notice_type, sub_type, body)

    return Event(
        event_id=_event_id(body, prefix="notice"),
        source="qq",
        kind=KIND_NOTICE,
        should_respond=False,
        platform="qq",
        chat_type="group" if group_id else "private",
        external_id=group_id or user_id,
        text=summary,
        sender_id=user_id,
        raw=body,
    )


def _build_request_event(body: dict[str, Any]) -> Event:
    """构造请求类事件(好友申请/加群申请)—— 仅审计,是否同意由上层策略决定。"""
    request_type = str(body.get("request_type") or "")
    user_id = str(body.get("user_id") or "")
    group_id = str(body.get("group_id") or "")
    comment = str(body.get("comment") or "")

    label = "好友申请" if request_type == "friend" else "加群申请"
    summary = f"{label}: 来自 {user_id}" + (f"，验证信息: {comment}" if comment else "")

    return Event(
        event_id=_event_id(body, prefix="request"),
        source="qq",
        kind=KIND_REQUEST,
        should_respond=False,
        platform="qq",
        chat_type="group" if group_id else "private",
        external_id=group_id or user_id,
        text=summary,
        sender_id=user_id,
        raw=body,
    )


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------
def _is_at_bot(parts: list[ContentPart], bot_qq: str) -> bool:
    """判断消息里是否 @ 了机器人。

    注意: @全体成员("all")不算 —— 否则机器人在群里会被每条 @全体 唤醒。
    bot_qq 为空时(未配置)保守返回 False(群聊不主动介入)。
    """
    if not bot_qq:
        return False
    return any(
        part.type == PartType.AT and str(part.data.get("qq")) == str(bot_qq)
        for part in parts
    )


def _event_id(body: dict[str, Any], *, prefix: str) -> str:
    """生成事件 ID(用于幂等去重)。

    优先用平台 message_id —— NapCat 重复推送同一消息时 ID 相同,
    上层据此去重,不会重复回复。

    通知/请求类事件通常没有 message_id,则退化为
    "类型 + 关键字段 + 时间戳"的组合(同一事件重推时,时间戳通常相同)。
    """
    message_id = body.get("message_id")
    if message_id is not None:
        return f"qq-{prefix}-{message_id}"

    # 无 message_id: 用关键字段拼一个稳定 ID
    key_parts = [
        str(body.get("notice_type") or body.get("request_type") or prefix),
        str(body.get("sub_type") or ""),
        str(body.get("user_id") or ""),
        str(body.get("group_id") or ""),
        str(body.get("time") or body.get("_time") or int(time.time())),
    ]
    return "qq-" + "-".join(p for p in key_parts if p)


def _describe_notice(notice_type: str, sub_type: str, body: dict[str, Any]) -> str:
    """把通知事件转成一句中文描述(events 表里可读)。"""
    user_id = str(body.get("user_id") or "")
    operator_id = str(body.get("operator_id") or "")
    group_id = str(body.get("group_id") or "")

    if notice_type == "friend_recall":
        return f"好友 {user_id} 撤回了一条消息"
    if notice_type == "group_recall":
        return f"群 {group_id} 中 {user_id} 撤回了一条消息（操作者 {operator_id}）"
    if notice_type == "friend_add":
        return f"新增好友: {user_id}"
    if notice_type == "group_increase":
        return f"群 {group_id} 成员增加: {user_id}（{sub_type}）"
    if notice_type == "group_decrease":
        return f"群 {group_id} 成员减少: {user_id}（{sub_type}）"
    if notice_type == "group_admin":
        return f"群 {group_id} 管理员变动: {user_id}（{sub_type}）"
    if notice_type == "group_ban":
        return f"群 {group_id} 禁言变动: {user_id}（{sub_type}，{body.get('duration', 0)}秒）"
    if notice_type == "group_upload":
        file_info = body.get("file") or {}
        return f"群 {group_id} 文件上传: {file_info.get('name', '')}"
    if notice_type == "group_card":
        return f"群 {group_id} 名片变更: {user_id}"
    if notice_type == "notify":
        if sub_type == "poke":
            return f"戳一戳: {user_id} 戳了 {body.get('target_id', '')}"
        if sub_type == "group_name":
            return f"群 {group_id} 改名: {body.get('name_new', '')}"
        if sub_type == "title":
            return f"群 {group_id} 头衔变更: {body.get('title', '')}"
        if sub_type == "profile_like":
            return f"资料点赞: {user_id}（{body.get('times', 1)} 次）"
        if sub_type == "input_status":
            return f"输入状态: {user_id} {body.get('status_text', '')}"
        return f"通知: {sub_type or notice_type}"
    if notice_type == "essence":
        return f"群 {group_id} 精华消息变动: {user_id}（{sub_type}）"
    if notice_type == "group_msg_emoji_like":
        return f"群 {group_id} 消息表情回应: {user_id}"
    if notice_type == "bot_offline":
        return f"机器人离线: {body.get('message', '')}"

    # 未识别: 保留类型名,便于以后补支持
    return f"通知事件: {notice_type}" + (f"/{sub_type}" if sub_type else "")
