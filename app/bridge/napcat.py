# =============================================================================
# bridge/napcat.py - QQ 桥(NapCat OneBot HTTP 客户端)
# -----------------------------------------------------------------------------
# 职责: 与 NapCat 通信 —— 把消息发出去、把信息查回来。
#   NapCat 提供的 OneBot 11 HTTP 接口(以 POST /<action> 形式调用):
#     send_private_msg   发私聊消息
#     send_group_msg     发群聊消息
#     get_login_info     查询登录信息
#     get_friend_list    好友列表
#     ...(其余动作统一走 call())
#
# 事件接收不走这里: NapCat 通过 HTTP 上报把事件 POST 到本服务的
# /qq/webhook(见 main.py),解析在 bridge/onebot.py。本模块只管**发送与查询**。
#
# 设计要点:
#   1. call() 是唯一出口 —— 所有动作都经过它,统一处理 retcode/超时/异常;
#   2. 消息内容既支持纯文本,也支持消息段数组(发 @、图片等富媒体时用);
#   3. 失败不抛异常,返回 (成功与否, 错误描述),由调用方决定怎么处理 ——
#      发消息失败不该让整个对话流程崩掉。
# =============================================================================

from __future__ import annotations

import json
from typing import Any

import httpx

from ..config import get_settings


class NapcatBridge:
    """NapCat HTTP 客户端(OneBot 11 动作调用)。"""

    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = settings.napcat_base_url.rstrip("/")
        # 发送消息是交互路径上的操作,超时给短一点(10 秒),
        # 避免 NapCat 卡住时把 agent 流程一起拖死。
        self._client = httpx.AsyncClient(timeout=10.0)

    # ------------------------------------------------------------------ 通用调用
    async def call(self, action: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """调用任意 OneBot 动作,返回响应字典。

        返回结构(统一):
          成功: {"ok": True,  "data": {...}}            —— 已剥离 OneBot 外壳
          失败: {"ok": False, "error": "错误描述"}

        说明: OneBot 响应的 {"status":"ok","retcode":0,"data":{...}} 会被解析成
        ok/data;retcode 非 0 或 HTTP 异常都算失败,便于调用方统一判断。
        """
        url = f"{self.base_url}/{action}"
        try:
            resp = await self._client.post(url, json=params or {})
            payload = resp.json()
        except httpx.TimeoutException:
            return {"ok": False, "error": f"调用 {action} 超时(NapCat 未响应)"}
        except httpx.HTTPError as exc:
            return {"ok": False, "error": f"调用 {action} 网络错误: {type(exc).__name__}"}
        except json.JSONDecodeError:
            return {"ok": False, "error": f"调用 {action} 返回非 JSON(NapCat 配置错误?)"}

        # OneBot 约定: retcode == 0 表示成功(部分实现只用 status 字段)
        retcode = payload.get("retcode")
        status = str(payload.get("status") or "")
        if retcode == 0 or status == "ok":
            return {"ok": True, "data": payload.get("data") or {}}

        # 失败: 优先用 message/wording 字段(OneBot 的错误描述)
        reason = payload.get("message") or payload.get("wording") or f"retcode={retcode}"
        return {"ok": False, "error": str(reason)}

    # ------------------------------------------------------------------ 发消息
    async def send_private_message(self, user_id: str, message: str | list[dict[str, Any]]) -> dict[str, Any]:
        """发送私聊消息。

        message: 纯文本,或 OneBot 消息段数组(发 @/图片等富媒体时用)。
        返回: {"ok": bool, "platform_message_id": str, "error": str}
          platform_message_id 用来识别"这条消息被平台回显"(见 app/repositories)。

        注意: 早期版本这里只返回 bool,调用方没法知道平台消息 ID ——
        结果自己发的消息被平台回显时会被当成新消息再记一遍，历史里出现两条一样的。
        """
        result = await self.call(
            "send_private_msg",
            {"user_id": _to_int(user_id), "message": message},
        )
        if not result["ok"]:
            print(f"[napcat] 私聊发送失败(user={user_id}): {result['error']}")
        return {
            "ok": bool(result["ok"]),
            "platform_message_id": str((result.get("data") or {}).get("message_id") or ""),
            "error": "" if result["ok"] else str(result.get("error") or ""),
        }

    async def send_group_message(self, group_id: str, message: str | list[dict[str, Any]]) -> dict[str, Any]:
        """发送群聊消息。

        message: 纯文本,或 OneBot 消息段数组。
        返回: {"ok": bool, "platform_message_id": str, "error": str}
        """
        result = await self.call(
            "send_group_msg",
            {"group_id": _to_int(group_id), "message": message},
        )
        if not result["ok"]:
            print(f"[napcat] 群聊发送失败(group={group_id}): {result['error']}")
        return {
            "ok": bool(result["ok"]),
            "platform_message_id": str((result.get("data") or {}).get("message_id") or ""),
            "error": "" if result["ok"] else str(result.get("error") or ""),
        }

    # ------------------------------------------------------------------ 查询
    async def get_login_info(self) -> dict[str, Any] | None:
        """查询当前登录的机器人信息(QQ 号/昵称)。"""
        result = await self.call("get_login_info")
        return result["data"] if result["ok"] else None

    async def get_friend_list(self) -> list[dict[str, Any]]:
        """获取好友列表。"""
        result = await self.call("get_friend_list")
        data = result["data"] if result["ok"] else None
        # OneBot 的 data 直接是数组,这里兼容一下包装形态
        if isinstance(data, list):
            return data
        return []

    async def get_group_list(self) -> list[dict[str, Any]]:
        """获取群列表(含群名与人数,用于"通讯录"页展示)。"""
        result = await self.call("get_group_list")
        data = result["data"] if result["ok"] else None
        if isinstance(data, list):
            return data
        return []

    async def get_contacts(self) -> dict[str, Any]:
        """好友 + 群 + 自己的登录信息,一次拿全(供"通讯录"页)。

        返回 {"ok", "error", "bot", "friends": [...], "groups": [...]}。
        **不抛异常**: NapCat 没起时 ok=False,页面照常渲染并提示去启动它 ——
        通讯录是"看看有哪些人"的功能,不该因为没连上就白屏。
        """
        health = await self.health()
        if not health.get("ok"):
            return {
                "ok": False,
                "error": str(health.get("error") or "无法连接 NapCat"),
                "bot": {},
                "friends": [],
                "groups": [],
            }
        friends = await self.get_friend_list()
        groups = await self.get_group_list()
        return {
            "ok": True,
            "error": "",
            "bot": {"user_id": str(health.get("qq") or ""), "nickname": str(health.get("nickname") or "")},
            "friends": friends,
            "groups": groups,
        }

    async def health(self) -> dict[str, Any]:
        """健康检查: 能否连通 NapCat(供状态页/启动脚本使用)。"""
        info = await self.get_login_info()
        if info:
            return {"ok": True, "qq": str(info.get("user_id") or ""), "nickname": info.get("nickname") or ""}
        return {"ok": False, "error": "无法连接 NapCat 或未登录"}

    # ------------------------------------------------------------------ 生命周期
    async def close(self) -> None:
        """关闭底层 HTTP 连接(应用退出时调用)。"""
        await self._client.aclose()


def _to_int(value: str) -> int:
    """把字符串 ID 转成整数(OneBot 要求数值型)。

    非法值(VIP 号等非数字场景极少见)退化为 0,由 NapCat 返回错误,
    不至于在桥内部抛异常。
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
