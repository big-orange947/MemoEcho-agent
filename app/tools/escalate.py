# =============================================================================
# tools/escalate.py - 请示工具(人工介入 / HITL)
# -----------------------------------------------------------------------------
# 需求来源: "有些不确定的,或者是跟现实背景相关的是需要 human in the loop"。
#
# 为什么需要它:
#   agent 替号主跟别人交涉时,会遇到"只有号主本人才能拍板"的事 ——
#   对方提出改期、涉及承诺/金额/对外形象、或信息不足无法判断。
#   此时**擅自答应或拒绝都是错的**:答应了可能替号主背下他不想背的事,
#   拒绝了又可能毁掉一桩本可以成的事。
#   正确动作是: 停下来,把问题抛回给号主。
#
# 实现要点:
#   · 请示进的是**上报队列**的 question 通道(与重要消息同一条出口),
#     所以上游 agent / 前端能从同一个地方拿到,不用另建一套通知机制;
#   · 同时把目标进度改成"等待指示",桌面端进度卡与审计都看得见;
#   · 号主的答复会作为新消息回到本会话,agent 从 checkpoint 恢复继续推进 ——
#     这条闭环不需要额外机制,复用的是既有的"新事件唤醒"能力。
# =============================================================================

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from .. import reports as reports_service
from ..services import goals as goals_service

# 成功请示的返回前缀。act 节点靠它判断"这一轮该停下等号主了" ——
# 用常量而不是在 act 里重写一遍字符串,避免两处漂移后静默失效。
RESULT_PREFIX = "已请示号主"
ERROR_PREFIX = "错误:"


@tool
def escalate_to_owner(question: str, options: str = "", config: RunnableConfig = None) -> str:
    """遇到拿不准、或需要号主本人才能决定的事时,把问题交给号主。

    什么时候该用(重要):
      · 对方提出改期、改条件,而你不知道号主能不能接受;
      · 涉及承诺、钱、对外形象(如"帮我答应下来""替我道歉");
      · 缺少只有号主知道的现实信息(行程、偏好、底线)。

    什么时候不该用: 目标范围内你自己能定的常规事(约个时间、传个话)—— 直接做,
    做完汇报,不要事事请示。

    question: 要说给号主听的问题(一句话说清背景,别让号主猜)
    options:  可选项(可选),例如 "① 九点可以 ② 改天"
    返回:     登记结果。调用后本轮交涉暂停,等号主答复。
    """
    question = (question or "").strip()
    if not question:
        return "错误: 请示内容为空"

    conversation_id = str((config or {}).get("configurable", {}).get("thread_id") or "") if config else ""
    if not conversation_id:
        return "错误: 无法确定当前会话,请示未登记"

    # ---- 1. 进上报队列的请求通道 ----
    # 用队列而不是直接推送: 与"重要消息上报"走同一个出口,
    # 上游 agent / 前端只需消费一处;离线也不会丢。
    result = reports_service.enqueue(
        lane=reports_service.LANE_QUESTION,
        conversation_id=conversation_id,
        payload={
            "summary": question[:200],
            "options": (options or "")[:200],
            "kind": "hitl_question",
        },
        dedup_key=f"question:{conversation_id}:{question[:40]}",
        status=reports_service.STATUS_PENDING,
    )

    # ---- 2. 目标进度标记为"等待指示" ----
    # 桌面端进度卡与审计据此可见"这个任务卡在等人拍板",而不是静默卡住。
    goal = goals_service.get_active_goal(conversation_id)
    if goal:
        goals_service.update_goal_status(
            goal_id=str(goal.get("id") or ""),
            status="active",
            progress=f"等待号主指示: {question[:20]}",
        )

    duplicated = " (同一问题已登记过)" if result.get("duplicated") else ""
    return f"{RESULT_PREFIX},等待答复{duplicated}。本轮先不回复对方,等号主给出指示后再继续。"


def create_escalate_tools() -> list[Any]:
    """返回请示类工具列表。"""
    return [escalate_to_owner]
