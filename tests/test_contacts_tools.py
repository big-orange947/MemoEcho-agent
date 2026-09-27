# =============================================================================
# tests/test_contacts_tools.py - 联系人工具(别名 + QQ 通讯录)
# -----------------------------------------------------------------------------
# 这组用例盯的是一件真出过问题的事: 号主说"帮我问一下 km"时,工具必须能自己
# 把"km"落到 QQ 号上 —— 而不是回一句"请提供对方的 QQ 号"让模型去反问号主。
#
# NapCat 没起时不联网: 桥是假的(见 FakeBridge)。
# =============================================================================

from __future__ import annotations

import json
from typing import Any

import pytest

from app.tools import contacts as contacts_tools

# 假通讯录: 备注名优先,昵称作为补充;刻意放一个重名的群,验证"不猜"
ROSTER: dict[str, Any] = {
    "ok": True,
    "error": "",
    "bot": {"user_id": "3969785168", "nickname": "Memo Echo"},
    "friends": [
        {"user_id": 2597164807, "nickname": "km", "remark": ""},
        {"user_id": 10001, "nickname": "某人", "remark": "小号"},
        {"user_id": 3969785168, "nickname": "Memo Echo", "remark": ""},  # 自己
        {"user_id": 20002, "nickname": "km老师", "remark": "王老师"},
    ],
    "groups": [
        {"group_id": 983214567, "group_name": "计科三班", "member_count": 42},
        {"group_id": 111222333, "group_name": "km 的群", "member_count": 8},
    ],
}


class FakeBridge:
    """假 QQ 桥: 只实现联系人读取。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    async def get_contacts(self) -> dict[str, Any]:
        return self.payload


@pytest.fixture()
def bridge(monkeypatch):
    """把全局 QQ 桥换成假的; 返回"装桥"函数。"""
    from app.agent import runtime as runtime_module

    def install(payload: dict[str, Any]) -> None:
        monkeypatch.setattr(runtime_module, "_bridge", FakeBridge(payload))

    return install


@pytest.fixture(autouse=True)
def empty_aliases(temp_data_dir):
    """默认不配别名(走通讯录那条路);需要别名的用例自己再写。"""
    from app.db import init_db
    from app.services import configs as configs_service

    init_db()  # 这组用例不建 app,表要自己建
    configs_service.set_config("contact_aliases", "{}")


def _parse(text: str) -> dict[str, Any]:
    """工具命中时返回的是 JSON,解析出来断言。"""
    return json.loads(text)


class TestResolveContact:
    async def test_resolves_nickname_from_roster(self, bridge):
        """核心: 仅凭 NapCat 返回的昵称就能定位 —— 不需要事先配别名。"""
        bridge(ROSTER)
        target = _parse(await contacts_tools.resolve_contact.ainvoke({"name": "km"}))
        assert target == {
            "platform": "qq",
            "chat_type": "private",
            "external_id": "2597164807",
            "title": "km",
        }

    async def test_resolves_remark_and_group(self, bridge):
        """备注名优先于昵称;群名也能解析(群聊同样要能"找得到")。"""
        bridge(ROSTER)
        friend = _parse(await contacts_tools.resolve_contact.ainvoke({"name": "小号"}))
        assert friend["external_id"] == "10001" and friend["title"] == "小号"

        group = _parse(await contacts_tools.resolve_contact.ainvoke({"name": "计科三班"}))
        assert group["chat_type"] == "group" and group["external_id"] == "983214567"

    async def test_ambiguous_name_returns_candidates(self, bridge):
        """"km" 同时能匹配 km/km老师/km 的群 ⇒ 给候选,不猜一个发出去。"""
        bridge(ROSTER)
        text = await contacts_tools.resolve_contact.ainvoke({"name": "k"})
        assert "多个可能" in text
        assert "2597164807" in text and "20002" in text
        with pytest.raises(json.JSONDecodeError):
            _parse(text)  # 没命中就不该给出可直接发送的目标

    async def test_unknown_name_lists_known(self, bridge):
        """查不到要给出"我手上有谁",让模型能据此判断,而不是空手去问号主。"""
        bridge(ROSTER)
        text = await contacts_tools.resolve_contact.ainvoke({"name": "不存在的人"})
        assert "查不到" in text and "小号" in text

    async def test_alias_beats_roster(self, bridge):
        """手配的别名最权威: 号主改了对应关系,工具必须听他的。"""
        from app.services import configs as configs_service

        configs_service.set_config(
            "contact_aliases",
            json.dumps({"km": {"chat_type": "private", "external_id": "88888", "title": "km(小号)"}}),
        )
        bridge(ROSTER)
        target = _parse(await contacts_tools.resolve_contact.ainvoke({"name": "km"}))
        assert target["external_id"] == "88888"

    async def test_napcat_down_tells_what_to_do(self, bridge):
        """NapCat 没起: 说清楚原因与下一步,不要让模型去编号码或反问 QQ 号。"""
        bridge({"ok": False, "error": "无法连接 NapCat(127.0.0.1:3011)", "friends": [], "groups": []})
        text = await contacts_tools.resolve_contact.ainvoke({"name": "km"})
        assert "3011" in text
        assert "NapCat" in text
        # 旧实现的坑: 提示模型"请直接询问对方的 QQ 号"
        assert "QQ 号" not in text.replace("QQ 号。", "")  # 允许出现在"直接给出 QQ 号"里

    async def test_bot_itself_is_not_a_contact(self, bridge):
        """机器人自己不该被解析成可联系对象(否则会出现"给自己发消息")。"""
        bridge(ROSTER)
        text = await contacts_tools.resolve_contact.ainvoke({"name": "Memo Echo"})
        assert "查不到" in text


class TestListContacts:
    async def test_lists_friends_and_groups(self, bridge):
        bridge(ROSTER)
        text = await contacts_tools.list_contacts.ainvoke({})
        assert "[好友] km qq=2597164807" in text
        assert "[好友] 小号(某人) qq=10001" in text  # 备注名 + 昵称都给出
        assert "[群] 计科三班 group=983214567" in text
        assert "3969785168" not in text  # 自己不在清单里

    async def test_napcat_down(self, bridge):
        bridge({"ok": False, "error": "连接失败", "friends": [], "groups": []})
        text = await contacts_tools.list_contacts.ainvoke({})
        assert "无法读取联系人" in text and "NapCat" in text
