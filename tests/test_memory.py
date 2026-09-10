# -*- coding: utf-8 -*-
"""长期记忆(Doppel)适配层测试。

覆盖:
- scope 映射(多租户隔离的关键): 会话信息 → Doppel 的 5 元组 scope;
- 隔离性: 不同 agent_id / 不同 chat_id 的记忆互不可见;
- 写入与召回闭环;
- 降级: 未启用/异常时不抛,返回空(记忆是增强,不能拖垮对话);
- prompt 格式化: 带说话人与时间。

注意: 这些测试依赖 Doppel 已安装(uv pip install -e D:\\project\\Doppel)。
未安装时整组跳过,不影响主测试套件的可用性。
"""
from __future__ import annotations

import pytest

from app import memory as memory_layer

# Doppel 未安装时跳过整组测试
pytestmark = pytest.mark.skipif(
    not memory_layer._DOPPEL_AVAILABLE,
    reason="Doppel 未安装(uv pip install -e D:\\project\\Doppel)",
)


@pytest.fixture()
def memory_env(temp_data_dir, monkeypatch):
    """准备记忆环境: 独立数据目录 + 启用记忆 + 固定号主/机器人标识。"""
    monkeypatch.setenv("MEMO_ECHO_DOPPEL_ENABLED", "true")

    from app import config as config_module

    monkeypatch.setattr(config_module, "_settings", None, raising=False)

    yield

    # 清理客户端单例,避免污染其它测试
    import asyncio

    asyncio.get_event_loop_policy().new_event_loop()  # noqa: D102 - 仅为隔离
    memory_layer._client = None


def _conv(
    *,
    external_id: str = "10001",
    platform: str = "qq",
    chat_type: str = "private",
) -> dict:
    """构造一个 v2 形态的会话字典。"""
    return {
        "id": f"conv-{external_id}",
        "platform": platform,
        "chat_type": chat_type,
        "external_id": external_id,
        "persona": "",
    }


# ---------------------------------------------------------------------------
# scope 映射
# ---------------------------------------------------------------------------
class TestScopeMapping:
    def test_scope_fields(self, memory_env, monkeypatch):
        """会话信息应正确映射到 Doppel 的 scope 五元组。"""
        monkeypatch.setenv("MEMO_ECHO_OWNER_USER_ID", "owner-1")
        monkeypatch.setenv("MEMO_ECHO_AGENT_ID", "bot-1")

        from app import config as config_module

        config_module._settings = None

        scope = memory_layer.build_scope(_conv(external_id="88888"))
        assert scope.user_id == "owner-1"
        assert scope.agent_id == "bot-1"
        assert scope.platform == "qq"
        assert scope.chat_type == "private"
        assert scope.chat_id == "88888"

    def test_agent_id_falls_back_to_bot_qq(self, memory_env, monkeypatch):
        """未配 agent_id 时回退到 bot_qq(兼容简单场景)。"""
        monkeypatch.setenv("MEMO_ECHO_AGENT_ID", "")
        monkeypatch.setenv("MEMO_ECHO_BOT_QQ", "3969785168")

        from app import config as config_module

        config_module._settings = None

        scope = memory_layer.build_scope(_conv())
        assert scope.agent_id == "3969785168"

    def test_owner_falls_back_to_local(self, memory_env, monkeypatch):
        """未配 owner_user_id 时用 local-owner 兜底(本机单人使用场景)。"""
        monkeypatch.setenv("MEMO_ECHO_OWNER_USER_ID", "")

        from app import config as config_module

        config_module._settings = None

        scope = memory_layer.build_scope(_conv())
        assert scope.user_id == "local-owner"


# ---------------------------------------------------------------------------
# 写入 / 召回闭环
# ---------------------------------------------------------------------------
class TestWriteAndRecall:
    @pytest.mark.asyncio
    async def test_remember_then_recall(self, memory_env, monkeypatch):
        """写入一条事实后,相关查询应能召回。"""
        monkeypatch.setenv("MEMO_ECHO_OWNER_USER_ID", "owner-test")
        monkeypatch.setenv("MEMO_ECHO_AGENT_ID", "bot-test")
        monkeypatch.setenv("MEMO_ECHO_DOPPEL_DB_NAME", "mem-test.sqlite3")

        from app import config as config_module

        config_module._settings = None

        conv = _conv(external_id="20001")

        ok = await memory_layer.remember_message(
            conv,
            role="user",
            content="我下周三要去北京出差",
            source="inbound",
            message_id="msg-1",
        )
        assert ok is True

        hits = await memory_layer.recall(conv, "出差")
        assert len(hits) >= 1
        # 召回内容应包含关键信息
        combined = " ".join(h["fact"] for h in hits)
        assert "北京" in combined or "出差" in combined

        await memory_layer.close_client()

    @pytest.mark.asyncio
    async def test_duplicate_ingest_is_idempotent(self, memory_env, monkeypatch):
        """同一 message_id 重复写入不应产生重复记忆。"""
        monkeypatch.setenv("MEMO_ECHO_OWNER_USER_ID", "owner-test2")
        monkeypatch.setenv("MEMO_ECHO_AGENT_ID", "bot-test2")
        monkeypatch.setenv("MEMO_ECHO_DOPPEL_DB_NAME", "mem-test2.sqlite3")

        from app import config as config_module

        config_module._settings = None

        conv = _conv(external_id="20002")
        payload = {
            "role": "user",
            "content": "我的生日是三月五号",
            "source": "inbound",
            "message_id": "dup-msg-1",
        }

        await memory_layer.remember_message(conv, **payload)
        await memory_layer.remember_message(conv, **payload)  # 重复

        hits = await memory_layer.recall(conv, "生日")
        # 不管召回几条,内容都不该出现两份完全相同的
        facts = [h["fact"] for h in hits]
        assert len(facts) == len(set(facts)), f"出现重复记忆: {facts}"

        await memory_layer.close_client()


# ---------------------------------------------------------------------------
# 多租户隔离(本模块最重要的保证)
# ---------------------------------------------------------------------------
class TestIsolation:
    @pytest.mark.asyncio
    async def test_different_chats_isolated(self, memory_env, monkeypatch):
        """不同联系人(chat_id)的记忆互不可见。"""
        monkeypatch.setenv("MEMO_ECHO_OWNER_USER_ID", "owner-iso")
        monkeypatch.setenv("MEMO_ECHO_AGENT_ID", "bot-iso")
        monkeypatch.setenv("MEMO_ECHO_DOPPEL_DB_NAME", "mem-iso.sqlite3")

        from app import config as config_module

        config_module._settings = None

        conv_a = _conv(external_id="30001")
        conv_b = _conv(external_id="30002")

        await memory_layer.remember_message(
            conv_a, role="user", content="小号喜欢喝美式咖啡", source="inbound", message_id="iso-a"
        )

        # 从另一个会话查询: 不应该看到 A 的记忆
        hits_b = await memory_layer.recall(conv_b, "咖啡")
        assert hits_b == [], f"跨会话泄漏: {hits_b}"

        # 从原会话查询: 应该能看到
        hits_a = await memory_layer.recall(conv_a, "咖啡")
        assert len(hits_a) >= 1

        await memory_layer.close_client()

    @pytest.mark.asyncio
    async def test_different_agents_isolated(self, memory_env, monkeypatch):
        """不同机器人(agent_id)的记忆互不可见 —— 多机器人共用实例时的关键保证。"""
        monkeypatch.setenv("MEMO_ECHO_OWNER_USER_ID", "owner-multi")
        monkeypatch.setenv("MEMO_ECHO_DOPPEL_DB_NAME", "mem-agent.sqlite3")

        from app import config as config_module

        config_module._settings = None

        conv = _conv(external_id="40001")

        # 机器人 A 写入
        monkeypatch.setenv("MEMO_ECHO_AGENT_ID", "bot-alpha")
        config_module._settings = None
        await memory_layer.remember_message(
            conv, role="user", content="机器人 alpha 记住的秘密", source="inbound", message_id="ag-a"
        )

        # 机器人 B 查询同一会话: 不应该看到 A 的记忆
        monkeypatch.setenv("MEMO_ECHO_AGENT_ID", "bot-beta")
        config_module._settings = None
        hits = await memory_layer.recall(conv, "秘密")
        assert hits == [], f"跨机器人泄漏: {hits}"

        await memory_layer.close_client()


# ---------------------------------------------------------------------------
# 降级
# ---------------------------------------------------------------------------
class TestDegradation:
    @pytest.mark.asyncio
    async def test_disabled_returns_empty(self, memory_env, monkeypatch):
        """记忆未启用时: 写入返回 False、召回返回空,且不抛异常。"""
        monkeypatch.setenv("MEMO_ECHO_DOPPEL_ENABLED", "false")

        from app import config as config_module

        config_module._settings = None

        conv = _conv()
        ok = await memory_layer.remember_message(conv, role="user", content="x", source="inbound")
        assert ok is False
        assert await memory_layer.recall(conv, "x") == []

    @pytest.mark.asyncio
    async def test_empty_content_noop(self, memory_env, monkeypatch):
        """空内容不写入(避免污染记忆)。"""
        monkeypatch.setenv("MEMO_ECHO_OWNER_USER_ID", "owner-empty")
        monkeypatch.setenv("MEMO_ECHO_AGENT_ID", "bot-empty")
        monkeypatch.setenv("MEMO_ECHO_DOPPEL_DB_NAME", "mem-empty.sqlite3")

        from app import config as config_module

        config_module._settings = None

        conv = _conv()
        assert await memory_layer.remember_message(conv, role="user", content="   ", source="inbound") is False
        assert await memory_layer.recall(conv, "") == []

        await memory_layer.close_client()


# ---------------------------------------------------------------------------
# prompt 格式化
# ---------------------------------------------------------------------------
class TestFormatForPrompt:
    def test_empty_hits_returns_empty_string(self):
        assert memory_layer.format_for_prompt([]) == ""

    def test_format_includes_actor_and_time(self):
        hits = [
            {"fact": "km 的课在晚上八点", "actor": "contact", "at": "2026-09-10T20:30:00+08:00"},
            {"fact": "我答应帮他带书", "actor": "agent", "at": "2026-09-11T09:00:00+08:00"},
        ]
        block = memory_layer.format_for_prompt(hits)
        assert "对方" in block          # contact → 对方
        assert "我(机器人)" in block     # agent → 我(机器人)
        assert "km 的课在晚上八点" in block
        assert "2026-09-10" in block    # 带时间

    def test_skips_empty_facts(self):
        hits = [{"fact": "", "actor": "contact"}, {"fact": "有效记忆", "actor": "contact"}]
        block = memory_layer.format_for_prompt(hits)
        assert "有效记忆" in block
        assert block.count("-") == 1    # 只输出了一条
