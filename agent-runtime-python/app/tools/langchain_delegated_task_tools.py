from __future__ import annotations

from typing import Annotated

from langchain_core.tools import BaseTool, tool
from pydantic import BaseModel, Field


class _TaskProgressInput(BaseModel):
    """主控台委托的通用状态更新参数。"""

    reason: Annotated[str, Field(description="本轮选择该动作的简短依据")]
    # StructuredTool 会按字段名回传参数，直接采用 API 的驼峰字段可避免
    # Pydantic alias 在校验后被 LangChain 过滤。
    progressSummary: Annotated[str, Field(description="面向用户的当前进展摘要")]
    knownFacts: Annotated[list[str], Field(default_factory=list, description="来自可信时间线的已知事实")]
    pendingConditions: Annotated[list[str], Field(default_factory=list, description="任务仍需等待的条件")]


class _SendQqMessageInput(_TaskProgressInput):
    """生成下一条私聊消息的意图参数，而非直接消息内容。"""

    messageInstruction: Annotated[
        str,
        Field(
            description="要发送给对方的最终聊天文本，直接写要说的原话（口语化、自然），"
            "不要写指令、说明或示例，不要提及任务、Agent、工具或内部名称"
        ),
    ]


class _CompleteDelegatedTaskInput(_TaskProgressInput):
    """模型主动结束主控台委托时提交的证据和完成摘要。"""

    completionReport: Annotated[str, Field(description="基于任务目标、时间线和记忆说明为什么现在可以结束")]
    outcome: Annotated[str, Field(description="完成结果，只能是 SUCCESS、REJECTED 或 BLOCKED")]
    evidence: Annotated[list[str], Field(default_factory=list, description="支持完成结论的任务内联系人消息摘要")]
    evidenceEventIds: Annotated[
        list[str],
        Field(description="支持完成结论的任务内联系人消息 eventId 列表"),
    ]
    finalMessageInstruction: Annotated[
        str | None,
        Field(
            default=None,
            description="仅在结束前还需发一句自然收尾消息时填写，直接写要发送的原话，不要写指令或示例"
        ),
    ]


class _TaskPreHistoryInput(BaseModel):
    """读取任务创建前少量历史消息的受控参数。"""

    reason: Annotated[str, Field(description="为什么任务内时间线不足以理解当前会话")]
    queryFocus: Annotated[str | None, Field(default=None, description="本次只需补充的背景焦点")]


def _public_arguments(schema: type[BaseModel], values: dict[str, object]) -> dict[str, object]:
    """校验工具输入，并保留和接口一致的驼峰字段名。"""
    return schema.model_validate(values).model_dump()


@tool("send_qq_message", args_schema=_SendQqMessageInput)
def plan_send_qq_message(**kwargs: object) -> dict[str, object]:
    """任务仍需继续对话时，声明下一条发给当前联系人的消息文本。

    messageInstruction 必须是直接可发送的聊天原话，不是动作指令或示例。
    此工具不直接发送消息。工作流会先调用它完成 LangChain 参数校验，再交由 Java
    工具白名单执行实际发送，因此模型不能越过权限、审查和幂等链路。
    """
    return {"intent": "send_qq_message", "arguments": _public_arguments(_SendQqMessageInput, kwargs)}


@tool("update_delegated_task", args_schema=_TaskProgressInput)
def plan_update_delegated_task(**kwargs: object) -> dict[str, object]:
    """当前无需发消息且任务尚未结束时，保存进度并等待后续事件。

    此工具没有聊天或任务结束副作用，只提供受校验的状态更新意图。
    """
    return {"intent": "update_delegated_task", "arguments": _public_arguments(_TaskProgressInput, kwargs)}


@tool("complete_delegated_task", args_schema=_CompleteDelegatedTaskInput)
def plan_complete_delegated_task(**kwargs: object) -> dict[str, object]:
    """当模型综合任务目标、任务内时间线和记忆后判断任务可以收束时，声明结束主控台委托。

    这个工具只表达“模型主动决定结束”的意图；Python 侧不会用固定词命中来代替该判断。
    调用前必须确认任务创建后的证据足以支持 SUCCESS、REJECTED 或 BLOCKED。若结束前仍需
    发一句自然收尾消息，把要发送的原话写入 finalMessageInstruction（不是指令或示例）。
    """
    return {"intent": "complete_delegated_task", "arguments": _public_arguments(_CompleteDelegatedTaskInput, kwargs)}


@tool("get_task_pre_history", args_schema=_TaskPreHistoryInput)
def plan_get_task_pre_history(**kwargs: object) -> dict[str, object]:
    """任务内时间线不足且用户已授权时，声明读取有限任务前聊天背景。

    真实读取由工作流只读观察节点执行，返回内容只能用于背景理解，不能作为完成证据。
    """
    return {"intent": "get_task_pre_history", "arguments": _public_arguments(_TaskPreHistoryInput, kwargs)}


def delegated_task_action_tools() -> list[BaseTool]:
    """返回只供主控台委托图选择的结构化 LangChain 工具。

    这些工具只承载模型可选择的意图和参数 schema，不在 Python 侧直接执行。真正的
    发消息、读历史、写入进度和结束任务必须继续经过 ToolRegistry、Event Center 的
    授权、证据、幂等和审计链路。
    """
    return [
        plan_send_qq_message,
        plan_update_delegated_task,
        plan_complete_delegated_task,
        plan_get_task_pre_history,
    ]
