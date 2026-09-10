# -*- coding: utf-8 -*-
"""请示(HITL)测试: agent 拿不准时把问题交给号主,而不是擅自拍板。

覆盖:
- 请示进上报队列的 question 通道(与重要消息同一出口);
- 目标是"进行中"而不是被误判为完成(否则任务会被悄悄丢掉);
- 进度写成"等待指示"(桌面端/审计可见);
- 同一问题重复请示不刷屏(幂等)。

不联网、不依赖真实 LLM。
"""
from __future__ import annotations

import pytest


@pytest.fixture()
def env(temp_data_dir):
    from app.db import init_db

    init_db()
    return None


class TestEscalate:
    @pytest.mark.asyncio
    async def test_question_enters_report_queue(self, env):
        """请示走的是上报队列的 question 通道 —— 上游/前端从同一处消费。"""
        from app import reports as reports_service
        from app.services import conversations as conversations_service
        from app.tools.escalate import escalate_to_owner

        conversation_id = conversations_service.ensure_conversation("qq", "private", "10001")

        result = await escalate_to_owner.ainvoke(
            {"question": "km 说八点不行,改九点?", "options": "① 同意 ② 改天"},
            {"configurable": {"thread_id": conversation_id}},
        )
        assert "已请示" in result

        items = reports_service.list_reports(lane=reports_service.LANE_QUESTION)
        assert len(items) == 1
        assert items[0]["payload"]["summary"] == "km 说八点不行,改九点?"
        assert items[0]["payload"]["kind"] == "hitl_question"
        assert items[0]["status"] == reports_service.STATUS_PENDING

    @pytest.mark.asyncio
    async def test_goal_stays_active_with_waiting_progress(self, env):
        """请示后目标仍是进行中,进度标记为等待指示(不能被当成完成丢掉)。"""
        from app.services import conversations as conversations_service
        from app.services import goals as goals_service
        from app.tools.escalate import escalate_to_owner

        conversation_id = conversations_service.ensure_conversation("qq", "private", "10002")
        goals_service.create_goal(conversation_id, "帮我约 km 晚上八点打游戏")

        await escalate_to_owner.ainvoke(
            {"question": "km 说八点没空,改九点行吗?"},
            {"configurable": {"thread_id": conversation_id}},
        )

        goal = goals_service.get_active_goal(conversation_id)
        assert goal is not None
        assert goal["status"] == "active"
        assert "等待号主指示" in goal["progress"]

    @pytest.mark.asyncio
    async def test_repeated_question_not_duplicated(self, env):
        """同一个问题重复请示不会刷屏(幂等键)。"""
        from app import reports as reports_service
        from app.services import conversations as conversations_service
        from app.tools.escalate import escalate_to_owner

        conversation_id = conversations_service.ensure_conversation("qq", "private", "10003")
        config = {"configurable": {"thread_id": conversation_id}}

        await escalate_to_owner.ainvoke({"question": "要不要答应周六帮他搬家?"}, config)
        second = await escalate_to_owner.ainvoke({"question": "要不要答应周六帮他搬家?"}, config)

        assert "已登记过" in second
        assert len(reports_service.list_reports(lane=reports_service.LANE_QUESTION)) == 1

    @pytest.mark.asyncio
    async def test_empty_question_rejected(self, env):
        from app.tools.escalate import escalate_to_owner

        result = await escalate_to_owner.ainvoke(
            {"question": "   "}, {"configurable": {"thread_id": "conv-x"}}
        )
        assert "错误" in result

    @pytest.mark.asyncio
    async def test_missing_conversation_context(self, env):
        """拿不到会话上下文时不静默失败 —— 要明确告诉模型。"""
        from app.tools.escalate import escalate_to_owner

        result = await escalate_to_owner.ainvoke({"question": "这事咋办"})
        assert "错误" in result
