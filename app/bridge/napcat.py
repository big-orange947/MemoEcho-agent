# =============================================================================
# bridge/napcat.py - QQ 桥(NapCat OneBot HTTP 客户端)
# -----------------------------------------------------------------------------
# 职责: 与 NapCat 通信,让 Memo Echo 能收发 QQ 消息。
# NapCat 是 QQ 机器人框架,对外提供 OneBot v11 兼容的 HTTP 接口:
#   - POST /send_private_msg   发私聊消息
#   - GET  /get_login_info     查询登录信息
#   - GET  /get_friend_list    好友列表
#
# 事件接收: NapCat 通过 WebSocket/HTTP 推送事件。本桥用"HTTP 事件上报"
# 或"WS 长连接"两种方式之一;这里实现最简单的"启动时注册 + 定时拉取"
# 不适合实时,因此采用 OneBot 的 HTTP 反向 webhook: NapCat 把事件 POST 到
# 本服务的 /qq/webhook 端点(见 api 层)。桥本身只负责"发消息 + 查询"。
#
# 注意: 这只是消息通道,不含任何业务状态 —— 业务全在 agent 层。
# =============================================================================

from __future__ import annotations

from typing import Any

import httpx

from ..config import get_settings


class NapcatBridge:
    """NapCat HTTP 客户端。"""

    def __init__(self) -> None:
        settings = get_settings()
        self.base_url = settings.napcat_base_url
        # 登录态由 NapCat 管理(本地已登录),我们只调用接口
        self._client = httpx.AsyncClient(timeout=10.0)

    # ------------------------------------------------------------------ 消息
    async def send_private_message(self, user_id: str, text: str) -> bool:
        """发送私聊消息。成功返回 True,失败返回 False(并记录日志)。"""
        try:
            resp = await self._client.post(
                f"{self.base_url}/send_private_msg",
                json={"user_id": int(user_id), "message": text},
            )
            data = resp.json()
            # OneBot: retcode 0 表示成功
            return data.get("retcode") == 0
        except Exception as exc:  # noqa: BLE001
            print(f"[napcat] 发送失败: {exc}")
            return False

    # ------------------------------------------------------------------ 查询
    async def get_login_info(self) -> dict[str, Any] | None:
        """查询当前登录的机器人信息。"""
        try:
            resp = await self._client.get(f"{self.base_url}/get_login_info")
            return resp.json().get("data")
        except Exception:  # noqa: BLE001
            return None

    async def get_friend_list(self) -> list[dict[str, Any]]:
        """获取好友列表。"""
        try:
            resp = await self._client.get(f"{self.base_url}/get_friend_list")
            return resp.json().get("data") or []
        except Exception:  # noqa: BLE001
            return []

    # ------------------------------------------------------------------ 生命周期
    async def close(self) -> None:
        """关闭底层 HTTP 连接(应用退出时调用)。"""
        await self._client.aclose()
