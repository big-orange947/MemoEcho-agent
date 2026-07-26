from __future__ import annotations

from typing import Any


class SendSecureAssetTool:
    """安全资产发送底层适配器；真正的 Agent 入口由 LangChain @tool 暴露。"""

    name = "send_secure_asset"

    def __init__(self, event_center_client: Any, connector_client: Any) -> None:
        """注入资产解析服务和平台发送服务，避免 Agent 直接接触底层 HTTP。"""
        self.event_center_client = event_center_client
        self.connector_client = connector_client

    async def send(
        self,
        *,
        asset_id: str,
        user_id: str,
        chat_type: str,
        chat_id: str,
        allowed_asset_ids: list[str],
    ) -> dict[str, Any]:
        """校验当前 Profile 授权范围，解析资产后发送；返回值不包含资产正文。"""
        asset_id = str(asset_id or "").strip()
        user_id = str(user_id or "").strip()
        chat_type = str(chat_type or "").strip()
        chat_id = str(chat_id or "").strip()
        allowed_asset_ids = {
            str(item).strip()
            for item in (allowed_asset_ids or [])
            if str(item).strip()
        }

        if not asset_id or asset_id not in allowed_asset_ids:
            raise PermissionError("secure asset is not authorized by current conversation profile")
        if chat_type not in {"private", "group"}:
            raise ValueError("chat_type must be private or group")
        if not chat_id:
            raise ValueError("chat_id is required")

        asset = await self.event_center_client.resolve_secure_asset(asset_id, user_id)
        response = await self._send_resolved_asset(asset, chat_type, int(chat_id))
        return {
            "status": str(response.get("status") or "unknown"),
            "assetId": asset_id,
            "assetType": str(asset.get("type") or "OTHER"),
        }

    async def _send_resolved_asset(
        self,
        asset: dict[str, Any],
        chat_type: str,
        chat_id: int,
    ) -> dict[str, Any]:
        """按资产类型转换为 OneBot 消息段；敏感正文只在本函数调用期间保留。"""
        content = str(asset.get("content") or "").strip()
        content_type = str(asset.get("contentType") or "text/plain").lower()
        asset_type = str(asset.get("type") or "OTHER").upper()
        if not content:
            raise ValueError("resolved secure asset content is empty")

        if content_type.startswith("image/") or asset_type in {"PAYMENT_CODE", "IMAGE"}:
            segments = [{"type": "image", "data": {"file": self._normalize_image_source(content)}}]
            return await self._send(chat_type, chat_id, segments=segments)

        if asset_type == "FILE" and self._is_remote_url(content):
            # Connector 暂未统一文件上传接口，远程文件先按下载地址发送。
            return await self._send(chat_type, chat_id, message=content)

        # 卡密、敏感文本和其他短内容按纯文本发送，但禁止写入日志或工具返回值。
        return await self._send(chat_type, chat_id, message=content)

    async def _send(
        self,
        chat_type: str,
        chat_id: int,
        message: str | None = None,
        segments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """把规范化后的消息转交给 QQ 私聊或群聊接口。"""
        if chat_type == "group":
            return await self.connector_client.send_group_message(chat_id, message, segments)
        return await self.connector_client.send_private_message(chat_id, message, segments)

    @staticmethod
    def _normalize_image_source(content: str) -> str:
        """把 data URL 转成 NapCat 支持的 base64 协议，其他 URL 或路径保持原样。"""
        if content.startswith("data:image/") and "," in content:
            return "base64://" + content.split(",", 1)[1]
        return content

    @staticmethod
    def _is_remote_url(content: str) -> bool:
        """识别可直接作为聊天文本发送的 HTTP(S) 文件地址。"""
        return content.lower().startswith(("http://", "https://"))
