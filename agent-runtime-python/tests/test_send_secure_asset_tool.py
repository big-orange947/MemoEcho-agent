from __future__ import annotations

import unittest

from app.tools.send_secure_asset_tool import SendSecureAssetTool


class DummyEventCenterClient:
    def __init__(self, asset: dict) -> None:
        self.asset = asset
        self.calls: list[tuple[str, str]] = []

    async def resolve_secure_asset(self, asset_id: str, user_id: str) -> dict:
        """记录解密请求并返回测试资产，不模拟真实密文。"""
        self.calls.append((asset_id, user_id))
        return dict(self.asset)


class DummyConnectorClient:
    def __init__(self) -> None:
        self.private_calls: list[dict] = []
        self.group_calls: list[dict] = []

    async def send_private_message(self, user_id, message=None, segments=None):
        """记录私聊发送参数并模拟 NapCat 成功响应。"""
        self.private_calls.append({"user_id": user_id, "message": message, "segments": segments})
        return {"status": "ok"}

    async def send_group_message(self, group_id, message=None, segments=None):
        """记录群聊发送参数并模拟 NapCat 成功响应。"""
        self.group_calls.append({"group_id": group_id, "message": message, "segments": segments})
        return {"status": "ok"}


class SendSecureAssetToolTest(unittest.IsolatedAsyncioTestCase):
    async def test_should_reject_asset_outside_profile_allowlist_before_resolve(self) -> None:
        """越权资产必须在调用解密接口前被拒绝。"""
        event_center = DummyEventCenterClient({"content": "secret"})
        tool = SendSecureAssetTool(event_center, DummyConnectorClient())

        with self.assertRaises(PermissionError):
            await tool.send(
                asset_id="asset-2",
                user_id="freeze",
                chat_type="private",
                chat_id="10001",
                allowed_asset_ids=["asset-1"],
            )

        self.assertEqual([], event_center.calls)

    async def test_should_send_payment_qr_as_image_without_returning_content(self) -> None:
        """收款码应转换为图片段，工具返回值不得包含 base64 正文。"""
        event_center = DummyEventCenterClient(
            {
                "id": "asset-1",
                "type": "PAYMENT_CODE",
                "contentType": "image/png",
                "content": "data:image/png;base64,QUJD",
            }
        )
        connector = DummyConnectorClient()
        tool = SendSecureAssetTool(event_center, connector)

        result = await tool.send(
            asset_id="asset-1",
            user_id="freeze",
            chat_type="private",
            chat_id="10001",
            allowed_asset_ids=["asset-1"],
        )

        self.assertEqual("base64://QUJD", connector.private_calls[0]["segments"][0]["data"]["file"])
        self.assertNotIn("content", result)
        self.assertNotIn("QUJD", str(result))

    async def test_should_send_license_code_as_plain_text(self) -> None:
        """卡密正文只传给 Connector，不出现在工具执行结果中。"""
        event_center = DummyEventCenterClient(
            {
                "id": "asset-code",
                "type": "LICENSE_CODE",
                "contentType": "text/plain",
                "content": "AAAA-BBBB-CCCC",
            }
        )
        connector = DummyConnectorClient()
        tool = SendSecureAssetTool(event_center, connector)

        result = await tool.send(
            asset_id="asset-code",
            user_id="freeze",
            chat_type="private",
            chat_id="10001",
            allowed_asset_ids=["asset-code"],
        )

        self.assertEqual("AAAA-BBBB-CCCC", connector.private_calls[0]["message"])
        self.assertNotIn("AAAA-BBBB-CCCC", str(result))
