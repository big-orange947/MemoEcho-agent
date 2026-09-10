# -*- coding: utf-8 -*-
"""会话策略(值守 / 监视 / 上报)与工具权限测试。

覆盖三件事:
1. 策略读写与校验(默认全关、蕴含关系、非法值被拒);
2. 分流判定 decide() 的完整决策表(这是"要不要回/要不要记"的唯一入口);
3. 工具权限: 按会话类型解析可用工具 + 越权调用被拒。

不依赖真实 LLM / 网络。
"""
from __future__ import annotations

import pytest

from app.events import Event, EventKind, EventSource
from app.services import conversations as conversations_service
from app.services import policy as policy_service


@pytest.fixture()
def env(temp_data_dir):
    """建好表 + 返回策略服务(数据目录由 temp_data_dir 隔离)。"""
    from app.db import init_db

    init_db()
    return policy_service


def _event(
    *,
    kind: str = EventKind.MESSAGE,
    source: str = EventSource.QQ,
    should_respond: bool = True,
    text: str = "你好",
) -> Event:
    return Event.from_text(
        text,
        source=source,
        kind=kind,
        platform="qq",
        chat_type="private",
        external_id="10001",
        conversation_id="conv-policy",
        should_respond=should_respond,
    )


# ---------------------------------------------------------------------------
# 策略读写
# ---------------------------------------------------------------------------
class TestPolicyReadWrite:
    def test_defaults_are_all_off(self, env):
        """默认全关: 不监视、不回复、不上报。"""
        conversations_service.ensure_conversation("qq", "private", "10001")
        conv_id = conversations_service.ensure_conversation("qq", "private", "10001")

        policy = env.get_policy(conv_id)
        assert policy["monitor"] == 0
        assert policy["reply_mode"] == "off"
        assert policy["alert_enabled"] == 0
        assert policy["require_human_confirmation"] == 1
        assert policy["allowed_tools"] == []

    def test_missing_conversation_returns_defaults(self, env):
        """会话不存在时返回默认策略,不抛异常(查询路径要稳)。"""
        policy = env.get_policy("no-such-conversation")
        assert policy["monitor"] == 0 and policy["reply_mode"] == "off"

    def test_update_and_read_back(self, env):
        conv_id = conversations_service.ensure_conversation("qq", "private", "10001")
        result = env.update_policy(conv_id, monitor=1, reply_mode="auto", alert_keywords=["急事", "改时间"])

        assert result["policy"]["monitor"] == 1
        assert result["policy"]["reply_mode"] == "auto"
        assert result["policy"]["alert_keywords"] == ["急事", "改时间"]
        # 读回来的要和写进去的一致
        assert env.get_policy(conv_id)["reply_mode"] == "auto"

    def test_monitor_implied_by_reply(self, env):
        """开启回复会自动打开监视,并在 implied 里回报(不静默改语义)。"""
        conv_id = conversations_service.ensure_conversation("qq", "private", "10001")
        result = env.update_policy(conv_id, reply_mode="auto")

        assert result["policy"]["monitor"] == 1
        assert "monitor" in result["implied"]

    def test_monitor_implied_by_alert(self, env):
        conv_id = conversations_service.ensure_conversation("qq", "private", "10001")
        result = env.update_policy(conv_id, alert_enabled=1)

        assert result["policy"]["monitor"] == 1
        assert "monitor" in result["implied"]

    def test_reply_mode_validated(self, env):
        conv_id = conversations_service.ensure_conversation("qq", "private", "10001")
        with pytest.raises(ValueError):
            env.update_policy(conv_id, reply_mode="always")

    def test_unknown_field_rejected(self, env):
        conv_id = conversations_service.ensure_conversation("qq", "private", "10001")
        with pytest.raises(ValueError):
            env.update_policy(conv_id, nonsense=1)

    def test_update_missing_conversation_raises(self, env):
        with pytest.raises(ValueError):
            env.update_policy("no-such-conversation", monitor=1)

    def test_keywords_accept_list_and_text(self, env):
        """关键词既能收 JSON 数组,也能收逗号/换行分隔的手写文本。"""
        conv_id = conversations_service.ensure_conversation("qq", "private", "10001")
        assert env.update_policy(conv_id, alert_keywords="急事,改时间")["policy"]["alert_keywords"] == ["急事", "改时间"]
        assert env.update_policy(conv_id, alert_keywords=["a", "b"])["policy"]["alert_keywords"] == ["a", "b"]

    def test_update_is_audited(self, env):
        """策略变更必须留痕(谁把什么开关从什么改成了什么)。"""
        from app.services import eventlog

        conv_id = conversations_service.ensure_conversation("qq", "private", "10001")
        env.update_policy(conv_id, reply_mode="auto")

        events = eventlog.list_events(conv_id, limit=10)
        assert any("策略变更" in (e.get("summary") or "") for e in events)


# ---------------------------------------------------------------------------
# 分流判定(决策表)
# ---------------------------------------------------------------------------
class TestDecide:
    def test_default_off_ignores_platform_message(self, env):
        """平台消息 + 默认全关 → 忽略。"""
        decision, reason = env.decide(_event(), {"monitor": 0, "reply_mode": "off"})
        assert decision == env.DECISION_IGNORE
        assert reason == "monitor-off"

    def test_monitor_records_without_reply(self, env):
        """监视开启但回复关闭 → 只记录。"""
        decision, _ = env.decide(_event(), {"monitor": 1, "reply_mode": "off"})
        assert decision == env.DECISION_RECORD

    def test_reply_mode_auto_replies(self, env):
        decision, reason = env.decide(_event(), {"monitor": 1, "reply_mode": "auto"})
        assert decision == env.DECISION_REPLY
        assert reason == "reply-mode-auto"

    def test_explicit_instruction_overrides_policy(self, env):
        """主 agent 派发的指令优先级最高 —— 即使会话全关也要执行。"""
        event = _event(source=EventSource.AGENT)
        decision, reason = env.decide(event, {"monitor": 0, "reply_mode": "off"})
        assert decision == env.DECISION_REPLY
        assert reason == "explicit-instruction"

    def test_desktop_command_overrides_policy(self, env):
        event = _event(source=EventSource.DESKTOP)
        decision, _ = env.decide(event, {"monitor": 0, "reply_mode": "off"})
        assert decision == env.DECISION_REPLY

    def test_task_authorization_allows_conversation(self, env):
        """任务授权态(有进行中的目标): 未开自动回复也能交流。"""
        decision, reason = env.decide(
            _event(), {"monitor": 0, "reply_mode": "off"}, has_active_goal=True
        )
        assert decision == env.DECISION_REPLY
        assert reason == "task-authorization"

    def test_self_sent_message_only_recorded(self, env):
        """号主手机自发消息: 永不回复,但监视时要记录(修上下文缺失)。"""
        event = _event(kind=EventKind.MESSAGE_SENT, should_respond=False)
        decision, _ = env.decide(event, {"monitor": 1, "reply_mode": "auto"})
        assert decision == env.DECISION_RECORD

    def test_timer_needs_context(self, env):
        """定时唤醒: 没有目标、也没开自动回复时忽略(残留计划不打扰)。"""
        event = _event(kind=EventKind.TIMER, source=EventSource.SCHEDULER)
        assert env.decide(event, {"monitor": 0, "reply_mode": "off"})[0] == env.DECISION_IGNORE
        assert env.decide(event, {"monitor": 0, "reply_mode": "auto"})[0] == env.DECISION_REPLY
        assert env.decide(event, {"monitor": 0, "reply_mode": "off"}, has_active_goal=True)[0] == env.DECISION_REPLY


# ---------------------------------------------------------------------------
# 工具权限
# ---------------------------------------------------------------------------
REGISTRY = {
    "send_qq_message": {"high_risk"},
    "resolve_contact": set(),
    "wait": set(),
}


class TestToolPermissions:
    def test_private_chat_gets_all_tools(self):
        """私聊默认不限制(现有转告/帮问流程不受影响)。"""
        assert policy_service.resolve_allowed_tools({"chat_type": "private"}, REGISTRY) == set(REGISTRY)

    def test_group_chat_cannot_send_by_default(self):
        """群聊默认不给发消息工具(被诱导替号主说话的风险最高)。"""
        allowed = policy_service.resolve_allowed_tools({"chat_type": "group"}, REGISTRY)
        assert "send_qq_message" not in allowed
        assert allowed == {"resolve_contact", "wait"}

    def test_high_risk_tag_is_honored(self):
        """新工具只要打了 high_risk 标签,群聊默认就拒绝 —— 不用改名字表。"""
        registry = {"some_new_tool": {"high_risk"}, "wait": set()}
        assert policy_service.resolve_allowed_tools({"chat_type": "group"}, registry) == {"wait"}

    def test_name_fallback_still_applies(self):
        """漏打标签的工具仍被名字表拦住(兜底)。"""
        registry = {"send_qq_message": set(), "wait": set()}
        assert policy_service.resolve_allowed_tools({"chat_type": "group"}, registry) == {"wait"}

    def test_explicit_grant_wins(self):
        """显式授权覆盖默认集(群里也能开)。"""
        conv = {"chat_type": "group", "allowed_tools": '["send_qq_message"]'}
        assert policy_service.resolve_allowed_tools(conv, REGISTRY) == {"send_qq_message"}

    def test_unknown_names_ignored(self):
        conv = {"chat_type": "private", "allowed_tools": '["send_qq_message", "not_a_tool"]'}
        assert policy_service.resolve_allowed_tools(conv, REGISTRY) == {"send_qq_message"}

    def test_tool_allowed_matches_resolver(self):
        group = {"chat_type": "group"}
        assert policy_service.tool_allowed(group, "send_qq_message", {"high_risk"}) is False
        assert policy_service.tool_allowed(group, "wait") is True
        assert policy_service.tool_allowed({"chat_type": "private"}, "send_qq_message") is True
