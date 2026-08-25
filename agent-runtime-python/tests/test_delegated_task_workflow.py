from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from app.schemas.delegated_tasks import (
    ConversationCandidate,
    DelegatedTaskActionInput,
    DelegatedTaskCompileRequest,
    DelegatedTaskRuntimeInput,
)
from app.workflows.delegated_task_graph import DelegatedTaskWorkflow


class DisabledLlmClient:
    """模拟未配置模型的环境，用于验证工作流的保守降级行为。"""

    def is_enabled(self, model_profile=None) -> bool:
        """始终关闭模型，确保测试结果不依赖外部 API。"""
        return False


class ToolCallingLlmClient:
    """返回固定 JSON 工具意图，用于验证 Agent 决策和 LangChain 工具校验。"""

    def __init__(self, name: str, arguments: dict) -> None:
        # 这个构造函数的作用是保存模型将选择的工具及参数，并记录实际调用信息。
        self.name = name
        self.arguments = arguments
        self.calls: list[dict] = []

    def is_enabled(self, model_profile=None) -> bool:
        """测试模型始终可用。"""
        return True

    async def generate_reply(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.7,
        model_profile=None,
        *, fast: bool = False,
    ) -> str:
        """模拟文本规划结果；参数仍须由工作流中的 LangChain @tool 校验。"""
        self.calls.append({"systemPrompt": system_prompt, "userMessage": user_message})
        if "情景一致性审查器" in system_prompt:
            return json.dumps(
                {"verdict": "APPROVE", "feedback": "", "revisedCandidateMessage": ""},
                ensure_ascii=False,
            )
        if "COMPLETION_REFLECTION" in system_prompt:
            # 常规工具选择测试并不模拟任务完成，因此完成复核应明确返回“继续执行”。
            return json.dumps(
                {
                    "shouldComplete": False,
                    "reason": "完成条件尚未满足",
                    "progressSummary": "继续推进当前任务",
                    "knownFacts": [],
                    "pendingConditions": ["等待联系人确认"],
                    "evidence": [],
                    "evidenceEventIds": [],
                },
                ensure_ascii=False,
            )

        payload = dict(self.arguments)
        if "messageInstruction" in payload:
            payload["candidateMessage"] = payload.pop("messageInstruction")
        elif "finalMessageInstruction" in payload:
            payload["candidateMessage"] = payload["finalMessageInstruction"]
        return json.dumps({"tool": self.name, **payload}, ensure_ascii=False)


class NativeToolCallingLlmClient:
    """模拟 LangChain 原生 @tool 调用，用于验证主控台优先走工具调用路径。"""

    def __init__(self, name: str, arguments: dict) -> None:
        # 保存模型声明调用的工具，同时记录 choose_tool 和 generate_reply 的调用情况。
        self.name = name
        self.arguments = arguments
        self.choose_tool_calls: list[dict] = []
        self.generate_calls: list[dict] = []

    def is_enabled(self, model_profile=None) -> bool:
        """测试模型始终可用。"""
        return True

    async def choose_tool(
        self,
        system_prompt: str,
        user_message: str,
        tools,
        temperature: float = 0.1,
        model_profile=None,
        *, fast: bool = False,
    ) -> dict:
        """模拟 LangChain 返回的 tool_call，不经过 JSON 文本规划。"""
        self.choose_tool_calls.append(
            {
                "systemPrompt": system_prompt,
                "userMessage": user_message,
                "toolNames": [tool.name for tool in tools],
            }
        )
        return {"name": self.name, "arguments": dict(self.arguments), "raw": ""}

    async def generate_reply(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.7,
        model_profile=None,
        *, fast: bool = False,
    ) -> str:
        """只允许审查节点调用；规划阶段如果调用到这里，测试会通过记录发现。"""
        self.generate_calls.append({"systemPrompt": system_prompt, "userMessage": user_message})
        if "COMPLETION_REFLECTION" in system_prompt:
            # 原生 tool calling 与完成复核是两个独立阶段，测试替身需要同时模拟两者。
            return json.dumps(
                {
                    "shouldComplete": False,
                    "reason": "完成条件尚未满足",
                    "progressSummary": "继续推进当前任务",
                    "knownFacts": [],
                    "pendingConditions": ["等待联系人确认"],
                    "evidence": [],
                    "evidenceEventIds": [],
                },
                ensure_ascii=False,
            )
        return json.dumps(
            {"verdict": "APPROVE", "feedback": "", "revisedCandidateMessage": ""},
            ensure_ascii=False,
        )


class ReActHistoryObservationLlmClient:
    """模拟两轮 ReAct 规划：先观察任务前历史，再基于观察结果决定发送消息。"""

    def __init__(self) -> None:
        # 记录每轮规划载荷，用于验证第二轮确实看到了图内观察的历史消息。
        self.planning_calls = 0
        self.planning_messages: list[str] = []

    def is_enabled(self, model_profile=None) -> bool:
        """测试模型始终可用，确保工作流进入 ReAct 分支。"""
        return True

    async def generate_reply(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.7,
        model_profile=None,
        *, fast: bool = False,
    ) -> str:
        """第一轮请求图内观察，第二轮在观察结果存在时选择发送消息。"""
        if "情景一致性审查器" in system_prompt:
            return json.dumps(
                {"verdict": "APPROVE", "feedback": "", "revisedCandidateMessage": ""},
                ensure_ascii=False,
            )

        if "COMPLETION_REFLECTION" in system_prompt:
            return json.dumps({"shouldComplete": False}, ensure_ascii=False)

        self.planning_calls += 1
        self.planning_messages.append(user_message)
        if self.planning_calls == 1:
            return json.dumps(
                {
                    "tool": "get_task_pre_history",
                    "candidateMessage": "",
                    "reason": "先读取任务创建前的约定",
                    "progressSummary": "需要补充历史背景",
                    "completionReport": "",
                    "knownFacts": [],
                    "pendingConditions": [],
                    "evidenceEventIds": [],
                },
                ensure_ascii=False,
            )

        return json.dumps(
            {
                "tool": "send_qq_message",
                "candidateMessage": "好的，我了解了",
                "reason": "已根据历史确认沟通背景",
                "progressSummary": "已回复，等待对方确认",
                "completionReport": "",
                "knownFacts": ["之前已经约定先确认时间"],
                "pendingConditions": ["等待对方确认"],
                "evidenceEventIds": [],
            },
            ensure_ascii=False,
        )


class PreTaskHistoryEventCenterClient:
    """模拟 Event Center 的任务前历史查询，避免测试依赖真实服务。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def list_conversation_messages(self, chat_id: str, **kwargs) -> list[dict]:
        """记录查询条件，并返回一条由本人发送的任务前消息。"""
        self.calls.append({"chatId": chat_id, **kwargs})
        return [
            {
                "eventId": "history-owner-1",
                "sentAt": "2026-07-22T10:00:00+08:00",
                "direction": "OUTBOUND",
                "actorType": "OWNER",
                "messageOrigin": "USER_MANUAL",
                "text": "我们之前约好先确认家教时间",
            }
        ]


class CompletionReflectionLlmClient:
    """先选择继续回复，再由完成复核节点判断任务是否应该结束。"""

    def __init__(self, completion_decision: dict | None = None) -> None:
        # 允许测试按具体会话注入完成判断，同时保留默认课程预约场景。
        self.calls: list[dict] = []
        self.completion_decision = completion_decision or {
            "shouldComplete": True,
            "reason": "联系人已经明确确认课程时间",
            "progressSummary": "课程时间已确认",
            "completionReport": "已确认今晚七点到九点上课",
            "finalMessageInstruction": "今晚见",
            "knownFacts": ["课程时间为 2026-07-23 晚上七点到九点"],
            "pendingConditions": [],
            "evidence": ["好的 明晚七点到九点见", "好的 那就这么定了"],
            "evidenceEventIds": ["peer-confirm-yesterday", "peer-confirm-today"],
        }

    def is_enabled(self, model_profile=None) -> bool:
        """测试模型始终可用。"""
        return True

    async def generate_reply(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.7,
        model_profile=None,
        *, fast: bool = False,
    ) -> str:
        """模拟三类模型调用：回复审查、完成复核、常规 ReAct 工具选择。"""
        self.calls.append({"systemPrompt": system_prompt, "userMessage": user_message})
        if "情景一致性审查器" in system_prompt:
            return json.dumps(
                {"verdict": "APPROVE", "feedback": "", "revisedCandidateMessage": ""},
                ensure_ascii=False,
            )
        if "COMPLETION_REFLECTION" in system_prompt:
            return json.dumps(self.completion_decision, ensure_ascii=False)
        return json.dumps(
            {
                "tool": "send_qq_message",
                "candidateMessage": "那就这么定了",
                "reason": "联系人正在确认安排",
                "progressSummary": "已准备自然收尾",
                "completionReport": "",
                "knownFacts": [],
                "pendingConditions": ["等待最终确认"],
                "evidenceEventIds": [],
            },
            ensure_ascii=False,
        )


class BrokenCompletionReflectionLlmClient:
    """模拟完成复核模型异常，验证主控台任务不会因此一直卡在代理中。"""

    def __init__(self, candidate: str = "好的 那明晚见") -> None:
        # 这个构造函数用于控制 ReAct 节点先生成的候选回复，同时记录模型调用过程。
        self.candidate = candidate
        self.calls: list[dict] = []

    def is_enabled(self, model_profile=None) -> bool:
        """测试模型始终可用，确保流程会进入 ReAct 与完成复核分支。"""
        return True

    async def generate_reply(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.7,
        model_profile=None,
        *, fast: bool = False,
    ) -> str:
        """模拟三类调用：候选回复、审查通过、完成复核失败。"""
        self.calls.append({"systemPrompt": system_prompt, "userMessage": user_message})
        if "情景一致性审查器" in system_prompt:
            return json.dumps(
                {"verdict": "APPROVE", "feedback": "", "revisedCandidateMessage": ""},
                ensure_ascii=False,
            )
        if "COMPLETION_REFLECTION" in system_prompt:
            return "not-json"
        return json.dumps(
            {
                "tool": "send_qq_message",
                "candidateMessage": self.candidate,
                "reason": "准备回复联系人",
                "progressSummary": "等待联系人最后确认",
                "completionReport": "",
                "knownFacts": [],
                "pendingConditions": ["等待联系人确认"],
                "evidenceEventIds": [],
            },
            ensure_ascii=False,
        )


class DelegatedTaskWorkflowTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        """为每个测试创建一套独立的 LangGraph 工作流。"""
        self.workflow = DelegatedTaskWorkflow(DisabledLlmClient())

    def test_timeline_should_only_keep_current_conversation(self) -> None:
        """同一任务状态混入其他会话时，时间线必须在图入口被隔离。"""
        task = {
            "id": "task-private-km",
            "platform": "qq",
            "chatType": "private",
            "chatId": "3807050597",
            "createdAt": "2026-07-28T10:00:00+08:00",
            "stateJson": json.dumps(
                {
                    "conversationScope": {
                        "platform": "qq",
                        "chatType": "private",
                        "chatId": "3807050597",
                    },
                    "timeline": [
                        {
                            "eventId": "wrong-conversation",
                            "platform": "qq",
                            "chatType": "private",
                            "chatId": "10002",
                            "role": "peer",
                            "text": "小号会话里的内容不应进入 km 的记忆",
                            "at": "2026-07-28T10:01:00+08:00",
                        }
                    ],
                },
                ensure_ascii=False,
            ),
        }
        runtime_input = SimpleNamespace(
            task=task,
            history=[
                {
                    "eventId": "history-wrong-conversation",
                    "platform": "qq",
                    "chatType": "private",
                    "chatId": "10002",
                    "role": "peer",
                    "text": "不能串进当前会话",
                    "receivedAt": "2026-07-28T10:02:00+08:00",
                },
                {
                    "eventId": "history-current-conversation",
                    "platform": "qq",
                    "chatType": "private",
                    "chatId": "3807050597",
                    "role": "peer",
                    "text": "km 会话中的历史消息",
                    "receivedAt": "2026-07-28T10:03:00+08:00",
                },
            ],
            event={
                "eventId": "current-conversation-event",
                "platform": "qq",
                "chatType": "private",
                "chatId": "3807050597",
                "direction": "INBOUND",
                "actorType": "CONTACT",
                "text": "当前消息",
                "receivedAt": "2026-07-28T10:04:00+08:00",
            },
        )

        timeline_state = self.workflow._build_timeline({"runtime_input": runtime_input})

        timeline = timeline_state["timeline"]
        self.assertEqual(("qq", "private", "3807050597"), timeline_state["conversation_scope"])
        self.assertEqual(
            {"history-current-conversation", "current-conversation-event"},
            {row["eventId"] for row in timeline},
        )
        self.assertTrue(all(row["chatId"] == "3807050597" for row in timeline))

    async def test_workspace_router_should_select_multiple_private_contacts_without_group_marker(self) -> None:
        """主控台 Router 应能把一条自然语言命令拆成多个私聊目标。"""
        candidates = [
            ConversationCandidate.model_validate(
                {"platform": "qq", "chatType": "private", "chatId": "3807050597", "chatName": "km"}
            ),
            ConversationCandidate.model_validate(
                {"platform": "qq", "chatType": "private", "chatId": "10002", "chatName": "小号"}
            ),
            ConversationCandidate.model_validate(
                {"platform": "qq", "chatType": "group", "chatId": "777376261", "chatName": "小号、km、哈吉仙"}
            ),
        ]

        result = await self.workflow.resolve_workspace_command_targets(
            "通知一下km和小号，今天晚上七点有课",
            candidates,
        )

        self.assertEqual(["3807050597", "10002"], [item.chat_id for item in result])
        self.assertEqual(["private", "private"], [item.chat_type for item in result])

    async def test_workspace_router_should_match_real_qq_aliases_for_multiple_contacts(self) -> None:
        """真实 QQ 备注和特殊昵称必须同时参与路由，且不能误选同名群聊。"""
        candidates = [
            ConversationCandidate.model_validate(
                {
                    "platform": "qq",
                    "chatType": "private",
                    "chatId": "2597164807",
                    "chatName": "小号",
                    "aliases": ["小号", "freeze", "2597164807"],
                }
            ),
            ConversationCandidate.model_validate(
                {
                    "platform": "qq",
                    "chatType": "private",
                    "chatId": "3807050597",
                    "chatName": "㎞",
                    "aliases": ["㎞", "3807050597"],
                }
            ),
            ConversationCandidate.model_validate(
                {
                    "platform": "qq",
                    "chatType": "group",
                    "chatId": "777376261",
                    "chatName": "小号、㎞、哈吉仙",
                    "aliases": ["小号、㎞、哈吉仙", "777376261"],
                }
            ),
        ]

        result = await self.workflow.resolve_workspace_command_targets(
            "通知一下小号和km明天晚上有课，别忘记了",
            candidates,
        )

        self.assertEqual(["2597164807", "3807050597"], [item.chat_id for item in result])
        self.assertTrue(all(item.chat_type == "private" for item in result))

    async def test_workspace_router_should_resolve_console_course_notice_to_two_private_contacts(self) -> None:
        """主控台真实测试命令必须按出现顺序命中两个私聊，不能误选包含同名成员的群聊。"""
        candidates = [
            ConversationCandidate.model_validate(
                {
                    "platform": "qq",
                    "chatType": "private",
                    "chatId": "3807050597",
                    "chatName": "㎞",
                    "aliases": ["㎞", "km", "刘畅", "3807050597"],
                }
            ),
            ConversationCandidate.model_validate(
                {
                    "platform": "qq",
                    "chatType": "private",
                    "chatId": "2597164807",
                    "chatName": "小号",
                    "aliases": ["小号", "freeze", "2597164807"],
                }
            ),
            ConversationCandidate.model_validate(
                {
                    "platform": "qq",
                    "chatType": "group",
                    "chatId": "777376261",
                    "chatName": "小号、㎞、哈吉仙",
                    "aliases": ["小号、㎞、哈吉仙", "777376261"],
                }
            ),
        ]

        result = await self.workflow.resolve_workspace_command_targets(
            "通知 km 和小号今晚有课",
            candidates,
        )

        self.assertEqual(["3807050597", "2597164807"], [item.chat_id for item in result])
        self.assertEqual(["private", "private"], [item.chat_type for item in result])

    async def test_workspace_router_should_resolve_explicit_contact_alias_before_llm(self) -> None:
        """命令明确给出通讯录备注时，必须优先本地命中，不能依赖模型猜测。"""

        class UnexpectedRouterLlmClient:
            def is_enabled(self, model_profile=None) -> bool:
                return True

            async def generate_reply(self, *args, **kwargs) -> str:
                raise AssertionError("明确联系人不应调用模型路由")

        workflow = DelegatedTaskWorkflow(UnexpectedRouterLlmClient())
        candidates = [
            ConversationCandidate.model_validate(
                {
                    "platform": "qq",
                    "chatType": "private",
                    "chatId": "3807050597",
                    "chatName": "刘畅",
                    "aliases": ["km", "刘畅"],
                }
            )
        ]

        result = await workflow.resolve_workspace_command_targets(
            "问km明天能不能一起吃饭",
            candidates,
        )

        self.assertEqual(["3807050597"], [item.chat_id for item in result])

    async def test_workspace_router_should_select_group_only_with_group_marker(self) -> None:
        """只有命令显式出现群聊语义时，Router 才应把同名群作为目标。"""
        candidates = [
            ConversationCandidate.model_validate(
                {"platform": "qq", "chatType": "private", "chatId": "3807050597", "chatName": "km"}
            ),
            ConversationCandidate.model_validate(
                {"platform": "qq", "chatType": "private", "chatId": "10002", "chatName": "小号"}
            ),
            ConversationCandidate.model_validate(
                {"platform": "qq", "chatType": "group", "chatId": "777376261", "chatName": "小号、km、哈吉仙"}
            ),
        ]

        result = await self.workflow.resolve_workspace_command_targets(
            "帮我在小号、km、哈吉仙群里通知今晚七点有课",
            candidates,
        )

        self.assertEqual(["777376261"], [item.chat_id for item in result])
        self.assertEqual(["group"], [item.chat_type for item in result])

    async def test_workspace_router_should_return_empty_for_unknown_contact(self) -> None:
        """没有命中授权候选时不能臆造联系人，后续编译阶段应进入澄清。"""
        candidates = [
            ConversationCandidate.model_validate(
                {"platform": "qq", "chatType": "private", "chatId": "3807050597", "chatName": "km"}
            )
        ]

        result = await self.workflow.resolve_workspace_command_targets(
            "通知张三今天晚上七点有课",
            candidates,
        )

        self.assertEqual([], result)

    async def test_workspace_router_should_use_thread_context_for_followup(self) -> None:
        """省略联系人的追问（如"那后天呢？"）必须借助线程前文推断目标联系人。"""

        class CapturingRouterLlm:
            def __init__(self) -> None:
                self.user_messages: list[str] = []

            def is_enabled(self, model_profile=None) -> bool:
                return True

            async def generate_reply(self, system_prompt, user_message, temperature=0.7, model_profile=None, *, fast=False) -> str:
                self.user_messages.append(user_message)
                return json.dumps(
                    {
                        "targets": [{"chatId": "3807050597", "chatType": "private", "reason": "前文委托对象"}],
                        "reason": "依据线程前文推断",
                    },
                    ensure_ascii=False,
                )

        llm = CapturingRouterLlm()
        workflow = DelegatedTaskWorkflow(llm)
        candidates = [
            ConversationCandidate.model_validate(
                {"platform": "qq", "chatType": "private", "chatId": "3807050597", "chatName": "km"}
            ),
            ConversationCandidate.model_validate(
                {"platform": "qq", "chatType": "private", "chatId": "2597164807", "chatName": "小号"}
            ),
        ]

        result = await workflow.resolve_workspace_command_targets(
            "那后天呢？",
            candidates,
            thread_context=[
                {"role": "user", "content": "帮我和 km 约明天晚上打游戏"},
                {"role": "agent", "content": "已创建委托工作流，正在执行"},
            ],
        )

        self.assertEqual(["3807050597"], [item.chat_id for item in result])
        # 模型必须看到线程前文，否则"那后天呢？"无法定位联系人。
        self.assertTrue(any("threadContext" in message for message in llm.user_messages))

    async def test_workspace_planner_should_use_thread_context_for_followup(self) -> None:
        """规划器必须把线程前文交给模型，让追问的指令补全为自包含步骤。"""

        class CapturingPlannerLlm:
            def __init__(self) -> None:
                self.calls: list[dict] = []

            def is_enabled(self, model_profile=None) -> bool:
                return True

            async def generate_reply(self, system_prompt, user_message, temperature=0.7, model_profile=None, *, fast=False) -> str:
                self.calls.append({"systemPrompt": system_prompt, "userMessage": user_message})
                return json.dumps(
                    {
                        "title": "约km后天打游戏",
                        "workflowType": "PLAN_EXECUTE",
                        "steps": [
                            {
                                "stepKey": "step_1",
                                "order": 1,
                                "role": "executor",
                                "instruction": "询问 km 后天晚上打游戏的空闲时间",
                                "targetChatType": "private",
                                "targetChatId": "3807050597",
                                "dependsOn": [],
                                "requiredFacts": [],
                                "producesFacts": ["km_available_time"],
                            }
                        ],
                    },
                    ensure_ascii=False,
                )

        llm = CapturingPlannerLlm()
        workflow = DelegatedTaskWorkflow(llm)
        candidates = [
            ConversationCandidate.model_validate(
                {"platform": "qq", "chatType": "private", "chatId": "3807050597", "chatName": "km"}
            )
        ]

        plan = await workflow.plan_workspace_command(
            "那后天呢？",
            candidates,
            thread_context=[
                {"role": "user", "content": "帮我和 km 约明天晚上打游戏"},
                {"role": "agent", "content": "已创建委托工作流，正在执行"},
            ],
        )

        self.assertEqual("step_1", plan.steps[0].step_key)
        self.assertTrue(any("threadContext" in call["userMessage"] for call in llm.calls))
        self.assertTrue(any("threadContext" in call["systemPrompt"] for call in llm.calls))

    def test_should_normalize_langchain_snake_case_completion_arguments(self) -> None:
        """LangChain 默认下划线参数不能导致完成证据在归一化阶段丢失。"""
        result = DelegatedTaskWorkflow._normalize_tool_decision(
            {
                "name": "complete_delegated_task",
                "arguments": {
                    "reason": "联系人已确认",
                    "progress_summary": "预约已确认",
                    "completion_report": "已约定今晚七点到九点上课",
                    "known_facts": ["双方确认时间"],
                    "pending_conditions": [],
                    "evidence": ["好的，今晚见"],
                    "evidence_event_ids": ["peer-confirm"],
                    "final_message_instruction": "今晚见",
                },
            }
        )

        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual("预约已确认", result["progressSummary"])
        self.assertEqual("已约定今晚七点到九点上课", result["completionReport"])
        self.assertEqual(["peer-confirm"], result["evidenceEventIds"])
        self.assertEqual("今晚见", result["messageInstruction"])

    async def test_should_resolve_target_only_from_authorized_conversations(self) -> None:
        """验证自然语言中的联系人只能绑定到服务端下发的授权会话。"""
        request = DelegatedTaskCompileRequest.model_validate(
            {
                "userId": "user-1",
                "command": "帮我和km约一下明天下午打球",
                "conversations": [
                    {
                        "platform": "qq",
                        "chatType": "private",
                        "chatId": "3807050597",
                        "chatName": "km",
                    },
                    {
                        "platform": "qq",
                        "chatType": "private",
                        "chatId": "10002",
                        "chatName": "小号",
                    },
                ],
            }
        )

        result = await self.workflow.compile_task(request)

        self.assertTrue(result.recognized)
        self.assertEqual(result.chat_id, "3807050597")
        self.assertEqual(result.target_name, "km")
        self.assertEqual(result.execution_mode, "AUTO_COMPLETE")
        self.assertIn("明天", result.deadline_text)
        graph_state = json.loads(result.state_json)
        self.assertTrue(graph_state["taskCreatedAt"])
        self.assertEqual("Asia/Shanghai", graph_state["taskTimezone"])
        self.assertIn("任务创建时解析", graph_state["resolvedTimeText"])

    async def test_should_compile_router_resolved_imperative_notification(self) -> None:
        """验证 RouterAgent 已确认联系人后，命令式通知可以直接编译为委托任务。"""
        request = DelegatedTaskCompileRequest.model_validate(
            {
                "userId": "user-1",
                "command": "通知 km 和小号今晚有课",
                "targetResolvedByRouter": True,
                "conversations": [
                    {
                        "platform": "qq",
                        "chatType": "private",
                        "chatId": "3807050597",
                        "chatName": "㎞",
                        "aliases": ["㎞", "km", "3807050597"],
                    }
                ],
            }
        )

        result = await self.workflow.compile_task(request)

        self.assertTrue(result.recognized)
        self.assertEqual("3807050597", result.chat_id)
        self.assertEqual("㎞", result.target_name)
        self.assertEqual("", result.clarification_question)

    async def test_should_resolve_ascii_query_against_qq_compatibility_character_name(self) -> None:
        """QQ 昵称为兼容字符“㎞”时，用户输入普通 km 仍应命中该私聊。"""
        request = DelegatedTaskCompileRequest.model_validate(
            {
                "userId": "user-1",
                "command": "帮我和km预约一下明天家教的时间，晚上七点到九点",
                "conversations": [
                    {
                        "platform": "qq",
                        "chatType": "private",
                        "chatId": "3807050597",
                        "chatName": "㎞",
                    }
                ],
            }
        )

        result = await self.workflow.compile_task(request)

        self.assertEqual("3807050597", result.chat_id)
        self.assertEqual("㎞", result.target_name)
        self.assertEqual("㎞", result.target_query)

    async def test_should_prefer_private_contact_over_group_containing_same_name(self) -> None:
        """联系人姓名同时出现在群名时，点对点委托必须绑定私聊而不是群聊。"""
        request = DelegatedTaskCompileRequest.model_validate(
            {
                "userId": "user-1",
                "command": "帮我和km约一下明天下午的课程",
                "conversations": [
                    {
                        "platform": "qq",
                        "chatType": "group",
                        "chatId": "777376261",
                        "chatName": "哈吉仙、km、freeze",
                        "lastSenderName": "km",
                    },
                    {
                        "platform": "qq",
                        "chatType": "private",
                        "chatId": "3807050597",
                        "chatName": "km",
                    },
                ],
            }
        )

        result = await self.workflow.compile_task(request)

        self.assertTrue(result.recognized)
        self.assertEqual("private", result.chat_type)
        self.assertEqual("3807050597", result.chat_id)
        self.assertEqual("km", result.target_name)

    async def test_should_not_keep_part_of_appointment_verb_in_target_name(self) -> None:
        """验证“km预约”只提取联系人 km，不会生成截图中的“km预”。"""
        request = DelegatedTaskCompileRequest.model_validate(
            {
                "userId": "user-1",
                "command": "帮我跟km预约一下明天家教的时间，帮我约到晚上七点到九点",
                "conversations": [
                    {
                        "platform": "qq",
                        "chatType": "private",
                        "chatId": "3807050597",
                        "chatName": "km",
                    }
                ],
            }
        )

        result = await self.workflow.compile_task(request)

        self.assertEqual("km", result.target_query)
        self.assertEqual("3807050597", result.chat_id)
        self.assertEqual("km", result.target_name)

    async def test_should_bind_group_only_when_command_explicitly_names_group(self) -> None:
        """命令显式写明群聊时才允许从群会话中解析目标。"""
        request = DelegatedTaskCompileRequest.model_validate(
            {
                "userId": "user-1",
                "command": "帮我在项目组群里约一下明天下午开会",
                "conversations": [
                    {
                        "platform": "qq",
                        "chatType": "private",
                        "chatId": "10001",
                        "chatName": "项目组",
                    },
                    {
                        "platform": "qq",
                        "chatType": "group",
                        "chatId": "20001",
                        "chatName": "项目组",
                    },
                ],
            }
        )

        result = await self.workflow.compile_task(request)

        self.assertTrue(result.recognized)
        self.assertEqual("group", result.chat_type)
        self.assertEqual("20001", result.chat_id)

    async def test_should_request_clarification_for_unknown_target(self) -> None:
        """验证命令中的目标不在授权列表时不会猜测 QQ 号或创建虚假联系人。"""
        request = DelegatedTaskCompileRequest.model_validate(
            {
                "userId": "user-1",
                "command": "帮我联系张三确认明天是否参会",
                "conversations": [
                    {
                        "platform": "qq",
                        "chatType": "private",
                        "chatId": "10002",
                        "chatName": "小号",
                    }
                ],
            }
        )

        result = await self.workflow.compile_task(request)

        self.assertTrue(result.recognized)
        self.assertEqual(result.chat_id, "")
        self.assertTrue(result.clarification_question)

    async def test_runtime_writeback_should_not_evaluate_completion_again(self) -> None:
        """验证消息写回后的运行图只记账，不进行第二次自主结束判断。"""
        runtime_input = DelegatedTaskRuntimeInput.model_validate(
            {
                "task": {
                    "objective": "约对方明天下午打球",
                    "successCriteria": "对方明确接受或拒绝",
                    "stateJson": "{}",
                },
                "history": [
                    {
                        "eventId": "old-owner",
                        "sentAt": "2026-07-21T10:00:00+08:00",
                        "direction": "OUTBOUND",
                        "actorType": "OWNER",
                        "messageOrigin": "OWNER",
                        "text": "明天下午打球吗",
                    }
                ],
                "event": {
                    "eventId": "peer-refusal",
                    "sentAt": "2026-07-21T10:01:00+08:00",
                    "direction": "INBOUND",
                    "actorType": "CONTACT",
                    "messageOrigin": "PLATFORM",
                    "text": "明天下午没空，不去了",
                },
                "finalReply": "好吧",
                "writeBackActions": ["qq_write_back_sent:private"],
            }
        )

        result = await self.workflow.evaluate_runtime(runtime_input)

        self.assertEqual(result.status, "ACTIVE")
        self.assertEqual(result.completion_report, "")
        self.assertEqual(result.evidence, [])
        state = json.loads(result.state_json)
        self.assertEqual([item["speaker"] for item in state["timeline"]], ["我", "对方"])
        self.assertEqual(state["timeline"][-1]["eventId"], "peer-refusal")
        self.assertEqual(state["timeline"][-1]["text"], "明天下午没空，不去了")
        self.assertEqual(result.requested_tool, "update_delegated_task")

    async def test_runtime_writeback_should_keep_confirmed_task_active(self) -> None:
        """即使回复文本看似确认，写回记账阶段也不能绕过动作图结束任务。"""
        runtime_input = DelegatedTaskRuntimeInput.model_validate(
            {
                "task": {
                    "objective": "预约明天的家教课程，时间晚上七点到九点",
                    "successCriteria": "对方明确确认课程时间",
                    "stateJson": "{}",
                },
                "history": [
                    {
                        "eventId": "peer-time",
                        "sentAt": "2026-07-22T20:46:00+08:00",
                        "direction": "INBOUND",
                        "actorType": "CONTACT",
                        "messageOrigin": "PLATFORM",
                        "text": "好的，那明晚七点到九点的课就这么定了",
                    }
                ],
                "event": {
                    "eventId": "peer-close",
                    "sentAt": "2026-07-22T20:47:00+08:00",
                    "direction": "INBOUND",
                    "actorType": "CONTACT",
                    "messageOrigin": "PLATFORM",
                    "text": "好的那明天晚上见",
                },
                "finalReply": "好的",
                "writeBackActions": ["qq_write_back_sent:ok"],
            }
        )

        result = await self.workflow.evaluate_runtime(runtime_input)

        self.assertEqual(result.status, "ACTIVE")
        self.assertEqual(result.requested_tool, "update_delegated_task")
        self.assertEqual(result.completion_report, "")
        self.assertEqual(result.evidence, [])

    async def test_runtime_writeback_should_preserve_planned_completion(self) -> None:
        """验证动作图已计划发送并完成时，平台写回成功后不能被 runtime 降级为进行中。"""
        state_json = json.dumps(
            {
                "lastPlannedAction": "SEND_AND_COMPLETE",
                "lastCompletionReport": "对方已确认今晚七点到九点上课，已发送收尾消息",
                "knownFacts": ["对方确认今晚七点到九点上课"],
                "pendingConditions": ["成功预约"],
                "lastEvidence": ["对方：好的，那明晚7点见"],
                "lastEvidenceEventIds": ["peer-confirm"],
                "workingMemory": {
                    "status": "COMPLETED",
                    "progress": "对方已确认，等待平台写回完成态",
                },
                "timeline": [
                    {
                        "eventId": "owner-ask",
                        "at": "2026-07-22T20:40:00+08:00",
                        "speaker": "我",
                        "text": "明晚七点到九点可以吗",
                    },
                    {
                        "eventId": "peer-confirm",
                        "at": "2026-07-22T20:46:00+08:00",
                        "speaker": "对方",
                        "text": "好的，那明晚7点见",
                    },
                ],
            },
            ensure_ascii=False,
        )
        runtime_input = DelegatedTaskRuntimeInput.model_validate(
            {
                "task": {
                    "objective": "预约明天家教时间，晚上七点到九点",
                    "successCriteria": "对方明确确认上课时间",
                    "stateJson": state_json,
                },
                "history": [],
                "event": {
                    "eventId": "agent-close",
                    "sentAt": "2026-07-22T20:47:00+08:00",
                    "direction": "OUTBOUND",
                    "actorType": "AGENT",
                    "messageOrigin": "AGENT",
                    "text": "好 今晚见",
                },
                "finalReply": "好 今晚见",
                "writeBackActions": ["qq_write_back_sent:ok"],
            }
        )

        result = await self.workflow.evaluate_runtime(runtime_input)

        self.assertEqual(result.status, "COMPLETED")
        self.assertEqual(result.requested_tool, "complete_delegated_task")
        self.assertEqual(result.completion_report, "对方已确认今晚七点到九点上课，已发送收尾消息")
        self.assertEqual(result.evidence, ["对方：好的，那明晚7点见"])
        state = json.loads(result.state_json)
        self.assertEqual(state["workingMemory"]["status"], "COMPLETED")
        self.assertEqual(state["pendingConditions"], [])

    async def test_should_not_treat_agent_message_as_peer_confirmation(self) -> None:
        """验证代理自己说“好的”不能作为对方同意或任务完成的证据。"""
        runtime_input = DelegatedTaskRuntimeInput.model_validate(
            {
                "task": {
                    "objective": "约对方明天下午打球",
                    "successCriteria": "对方明确接受或拒绝",
                    "stateJson": json.dumps({"knownFacts": ["已询问对方"]}, ensure_ascii=False),
                },
                "history": [],
                "event": {
                    "eventId": "agent-confirmation",
                    "sentAt": "2026-07-21T10:01:00+08:00",
                    "direction": "OUTBOUND",
                    "actorType": "AGENT",
                    "messageOrigin": "AGENT",
                    "text": "好的，明天下午见",
                },
                "finalReply": "好的，明天下午见",
                "writeBackActions": ["qq_write_back_sent:private"],
            }
        )

        result = await self.workflow.evaluate_runtime(runtime_input)
        state = json.loads(result.state_json)

        self.assertEqual(result.status, "ACTIVE")
        self.assertEqual(result.evidence, [])
        self.assertIn("已询问对方", state["knownFacts"])

    async def test_should_exclude_internal_start_command_from_runtime_timeline(self) -> None:
        """桌面委托启动事件是控制面数据，不得进入联系人对话时间线。"""
        runtime_input = DelegatedTaskRuntimeInput.model_validate(
            {
                "task": {
                    "objective": "约对方明天下午上课",
                    "successCriteria": "对方明确确认课程时间",
                    "stateJson": "{}",
                },
                "history": [
                    {
                        "eventId": "delegated:start:task-1",
                        "eventType": "delegated_task_started",
                        "sentAt": "2026-07-22T10:00:00+08:00",
                        "direction": "INTERNAL",
                        "actorType": "SYSTEM",
                        "messageOrigin": "INTERNAL",
                        "text": "帮我和km约一下明天下午的课程",
                    }
                ],
                "event": {
                    "eventId": "peer-answer",
                    "eventType": "message",
                    "sentAt": "2026-07-22T10:01:00+08:00",
                    "direction": "INBOUND",
                    "actorType": "CONTACT",
                    "messageOrigin": "PLATFORM",
                    "text": "明天下午可以",
                },
                "finalReply": "好",
                "writeBackActions": ["qq_write_back_sent:private"],
            }
        )

        result = await self.workflow.evaluate_runtime(runtime_input)
        state = json.loads(result.state_json)

        self.assertEqual(["peer-answer"], [item["eventId"] for item in state["timeline"]])
        self.assertNotIn("帮我和km约一下明天下午的课程", str(state))

    async def test_action_graph_should_not_send_when_model_is_unavailable(self) -> None:
        """模型不可用时只能保存等待状态，不能以固定模板替用户主动发消息。"""
        action_input = DelegatedTaskActionInput.model_validate(
            {
                "task": {
                    "id": "task-1",
                    "objective": "预约明天晚上的家教课程",
                    "successCriteria": "对方明确确认或拒绝课程时间",
                    "stateJson": "{}",
                },
                "history": [],
                "event": {
                    "eventId": "delegated:start:task-1",
                    "eventType": "delegated_task_started",
                    "sentAt": "2026-07-22T10:00:00+08:00",
                    "direction": "INTERNAL",
                    "actorType": "SYSTEM",
                    "rawPayload": {"messageOrigin": "INTERNAL"},
                    "text": "",
                },
            }
        )

        result = await self.workflow.decide_action(action_input)

        self.assertEqual("WAIT", result.action)
        self.assertEqual("update_delegated_task", result.requested_tool)
        self.assertEqual("", result.message_instruction)

    async def test_action_graph_should_not_reuse_old_history_when_model_is_unavailable(self) -> None:
        """新任务启动且模型不可用时，不得借旧会话或兜底模板产生外发消息。"""
        action_input = DelegatedTaskActionInput.model_validate(
            {
                "task": {
                    "id": "task-new",
                    "objective": "重新预约下周的家教课程",
                    "successCriteria": "对方明确确认新的课程时间",
                    "stateJson": "{}",
                },
                "history": [
                    {
                        "eventId": "old-peer-close",
                        "sentAt": "2026-07-20T21:00:00+08:00",
                        "direction": "INBOUND",
                        "actorType": "CONTACT",
                        "messageOrigin": "PLATFORM",
                        "text": "没问题，那就明天晚上见",
                    }
                ],
                "event": {
                    "eventId": "delegated:start:task-new",
                    "eventType": "delegated_task_started",
                    "sentAt": "2026-07-22T10:00:00+08:00",
                    "direction": "INTERNAL",
                    "actorType": "SYSTEM",
                    "messageOrigin": "INTERNAL",
                    "text": "",
                },
            }
        )

        result = await self.workflow.decide_action(action_input)

        self.assertEqual("WAIT", result.action)
        self.assertEqual("update_delegated_task", result.requested_tool)

    async def test_runtime_should_not_complete_from_old_history_on_startup(self) -> None:
        """运行时写回同样只能由本轮联系人入站消息完成，启动事件仅记录主动开场状态。"""
        runtime_input = DelegatedTaskRuntimeInput.model_validate(
            {
                "task": {
                    "id": "task-new",
                    "objective": "重新预约下周的家教课程",
                    "successCriteria": "对方明确确认新的课程时间",
                    "stateJson": "{}",
                },
                "history": [
                    {
                        "eventId": "old-peer-close",
                        "sentAt": "2026-07-20T21:00:00+08:00",
                        "direction": "INBOUND",
                        "actorType": "CONTACT",
                        "messageOrigin": "PLATFORM",
                        "text": "没问题，那就明天晚上见",
                    }
                ],
                "event": {
                    "eventId": "delegated:start:task-new",
                    "eventType": "delegated_task_started",
                    "sentAt": "2026-07-22T10:00:00+08:00",
                    "direction": "INTERNAL",
                    "actorType": "SYSTEM",
                    "messageOrigin": "INTERNAL",
                    "text": "",
                },
                "finalReply": "老师您好，想和您约一下下周的课程时间",
                "writeBackActions": ["qq_write_back_sent:private"],
            }
        )

        result = await self.workflow.evaluate_runtime(runtime_input)

        self.assertEqual("ACTIVE", result.status)
        self.assertEqual("update_delegated_task", result.requested_tool)
        self.assertEqual("SENT", json.loads(result.state_json)["lastWriteBackStatus"])

    async def test_runtime_should_not_complete_from_old_history_on_unrelated_peer_reply(self) -> None:
        """联系人本轮普通回复不能借用旧历史中的收尾语句误结束当前委托。"""
        runtime_input = DelegatedTaskRuntimeInput.model_validate(
            {
                "task": {
                    "id": "task-new",
                    "objective": "重新预约下周的家教课程",
                    "successCriteria": "对方明确确认新的课程时间",
                    "stateJson": json.dumps({"lastWriteBackStatus": "SENT", "lastPlannedAction": "SEND_MESSAGE"}, ensure_ascii=False),
                },
                "history": [
                    {
                        "eventId": "old-peer-close",
                        "sentAt": "2026-07-20T21:00:00+08:00",
                        "direction": "INBOUND",
                        "actorType": "CONTACT",
                        "messageOrigin": "PLATFORM",
                        "text": "没问题，那就明天晚上见",
                    }
                ],
                "event": {
                    "eventId": "peer-unrelated",
                    "eventType": "message",
                    "sentAt": "2026-07-22T10:03:00+08:00",
                    "direction": "INBOUND",
                    "actorType": "CONTACT",
                    "messageOrigin": "PLATFORM",
                    "text": "你在吗",
                },
                "finalReply": "在的，想和您重新约一下下周的课程时间",
                "writeBackActions": ["qq_write_back_sent:private"],
            }
        )

        result = await self.workflow.evaluate_runtime(runtime_input)

        self.assertEqual("ACTIVE", result.status)
        self.assertEqual("update_delegated_task", result.requested_tool)
        self.assertNotIn("没问题，那就明天晚上见", result.evidence)

    async def test_action_graph_should_not_repeat_kickoff_after_restart(self) -> None:
        """持久化状态已记录首条消息时，重复启动事件只能等待，不能再次联系对方。"""
        action_input = DelegatedTaskActionInput.model_validate(
            {
                "task": {
                    "id": "task-1",
                    "objective": "预约明天晚上的家教课程",
                    "successCriteria": "对方明确确认课程时间",
                    "stateJson": json.dumps({"lastWriteBackStatus": "SENT", "lastPlannedAction": "SEND_MESSAGE"}, ensure_ascii=False),
                },
                "history": [],
                "event": {
                    "eventId": "delegated:start:task-1-retry",
                    "eventType": "delegated_task_started",
                    "sentAt": "2026-07-22T10:02:00+08:00",
                    "direction": "INTERNAL",
                    "actorType": "SYSTEM",
                    "rawPayload": {"messageOrigin": "INTERNAL"},
                    "text": "",
                },
            }
        )

        result = await self.workflow.decide_action(action_input)

        self.assertEqual("WAIT", result.action)
        self.assertEqual("update_delegated_task", result.requested_tool)

    async def test_action_graph_should_continue_only_for_peer_inbound_message(self) -> None:
        """联系人真实回复会推进任务，而代理自己的出站消息只能更新状态并等待。"""
        base_task = {
            "id": "task-1",
            "objective": "预约明天晚上的家教课程",
            "successCriteria": "对方明确确认课程时间",
            "stateJson": json.dumps({"lastWriteBackStatus": "SENT", "lastPlannedAction": "SEND_MESSAGE"}, ensure_ascii=False),
        }
        peer_result = await self.workflow.decide_action(
            DelegatedTaskActionInput.model_validate(
                {
                    "task": base_task,
                    "history": [],
                    "event": {
                        "eventId": "peer-1",
                        "eventType": "message",
                        "sentAt": "2026-07-22T10:03:00+08:00",
                        "direction": "INBOUND",
                        "actorType": "CONTACT",
                        "messageOrigin": "PLATFORM",
                        "text": "明天下午没空，晚上可以",
                    },
                }
            )
        )
        agent_result = await self.workflow.decide_action(
            DelegatedTaskActionInput.model_validate(
                {
                    "task": base_task,
                    "history": [],
                    "event": {
                        "eventId": "agent-1",
                        "eventType": "message",
                        "sentAt": "2026-07-22T10:04:00+08:00",
                        "direction": "OUTBOUND",
                        "actorType": "AGENT",
                        "messageOrigin": "AGENT",
                        "text": "那晚上七点可以吗",
                    },
                }
            )
        )

        # 没有模型明确选择工具时，联系人来信也只能进入等待，不能由程序猜测并代发消息。
        self.assertEqual("WAIT", peer_result.action)
        self.assertEqual("update_delegated_task", peer_result.requested_tool)
        self.assertEqual("WAIT", agent_result.action)

    async def test_action_graph_should_reply_with_merged_timeline_when_current_event_is_stale(self) -> None:
        """当前事件之后已有新联系人消息时，旧事件也要基于完整时间线合并处理。"""
        workflow = DelegatedTaskWorkflow(
            ToolCallingLlmClient(
                "send_qq_message",
                {
                    "reason": "准备回复旧消息",
                    "progressSummary": "收到联系人回复",
                    "messageInstruction": "下午可以",
                },
            )
        )
        result = await workflow.decide_action(
            DelegatedTaskActionInput.model_validate(
                {
                    "task": {
                        "id": "task-stale",
                        "objective": "预约明天家教时间",
                        "successCriteria": "对方明确确认时间",
                        "stateJson": json.dumps(
                            {
                                "lastWriteBackStatus": "SENT",
                                "lastPlannedAction": "SEND_MESSAGE",
                            },
                            ensure_ascii=False,
                        ),
                    },
                    "history": [
                        {
                            "eventId": "peer-older",
                            "eventType": "message",
                            "sentAt": "2026-07-22T10:03:00+08:00",
                            "direction": "INBOUND",
                            "actorType": "CONTACT",
                            "messageOrigin": "PLATFORM",
                            "text": "下午行吗",
                        },
                        {
                            "eventId": "peer-newer",
                            "eventType": "message",
                            "sentAt": "2026-07-22T10:03:05+08:00",
                            "direction": "INBOUND",
                            "actorType": "CONTACT",
                            "messageOrigin": "PLATFORM",
                            "text": "实在不行晚上也可以",
                        },
                    ],
                    "event": {
                        "eventId": "peer-older",
                        "eventType": "message",
                        "sentAt": "2026-07-22T10:03:00+08:00",
                        "direction": "INBOUND",
                        "actorType": "CONTACT",
                        "messageOrigin": "PLATFORM",
                        "text": "下午行吗",
                    },
                }
            )
        )

        self.assertEqual("SEND_MESSAGE", result.action)
        self.assertEqual("send_qq_message", result.requested_tool)
        self.assertEqual("下午可以", result.message_instruction)

    async def test_action_graph_should_prefer_native_langchain_tool_call(self) -> None:
        """支持原生 tool calling 的模型应优先走 LangChain @tool，而不是字符串 JSON 规划。"""
        llm_client = NativeToolCallingLlmClient(
            "send_qq_message",
            {
                "reason": "联系人询问时间，继续推进预约",
                "progress_summary": "准备回复对方可行时间",
                "message_instruction": "可以",
                "known_facts": ["目标是预约明晚七点到九点的家教课程"],
                "pending_conditions": ["等待对方确认"],
            },
        )
        workflow = DelegatedTaskWorkflow(llm_client)
        result = await workflow.decide_action(
            DelegatedTaskActionInput.model_validate(
                {
                    "task": {
                        "id": "task-native-tool",
                        "objective": "预约明天晚上的家教课程",
                        "successCriteria": "对方明确确认时间",
                        "stateJson": json.dumps(
                            {
                                "lastWriteBackStatus": "SENT",
                                "lastPlannedAction": "SEND_MESSAGE",
                            },
                            ensure_ascii=False,
                        ),
                    },
                    "history": [],
                    "event": {
                        "eventId": "event-native-tool",
                        "eventType": "message",
                        "platform": "qq",
                        "chatType": "private",
                        "chatId": "3807050597",
                        "chatName": "km",
                        "senderId": "3807050597",
                        "senderName": "km",
                        "direction": "INBOUND",
                        "actorType": "CONTACT",
                        "messageOrigin": "PLATFORM",
                        "content": "那明晚可以吗",
                        "text": "那明晚可以吗",
                        "sentAt": "2026-07-22T20:10:00+08:00",
                    },
                }
            )
        )

        self.assertEqual("SEND_MESSAGE", result.action)
        self.assertEqual("send_qq_message", result.requested_tool)
        self.assertEqual("可以", result.message_instruction)
        self.assertTrue(llm_client.choose_tool_calls)
        self.assertIn("send_qq_message", llm_client.choose_tool_calls[0]["toolNames"])

    async def test_action_graph_should_complete_from_native_langchain_tool_call(self) -> None:
        """当模型原生调用 complete_delegated_task 时，主控台任务应进入发送并完成路径。"""
        llm_client = NativeToolCallingLlmClient(
            "complete_delegated_task",
            {
                "reason": "联系人已经明确确认明晚时间",
                "completion_report": "已和联系人确认明晚七点到九点见面",
                "final_message_instruction": "那明晚见",
                "known_facts": ["联系人确认明晚七点到九点可以"],
                "pending_conditions": [],
                "evidence_event_ids": ["peer-confirmed-time"],
            },
        )
        workflow = DelegatedTaskWorkflow(llm_client)
        result = await workflow.decide_action(
            DelegatedTaskActionInput.model_validate(
                {
                    "task": {
                        "id": "task-native-complete",
                        "objective": "预约明天晚上的家教课程",
                        "successCriteria": "对方明确确认时间",
                        "stateJson": json.dumps(
                            {
                                "lastWriteBackStatus": "SENT",
                                "lastPlannedAction": "SEND_MESSAGE",
                            },
                            ensure_ascii=False,
                        ),
                    },
                    "history": [
                        {
                            "eventId": "peer-confirmed-time",
                            "eventType": "message",
                            "platform": "qq",
                            "chatType": "private",
                            "chatId": "3807050597",
                            "senderId": "3807050597",
                            "senderName": "km",
                            "direction": "INBOUND",
                            "actorType": "CONTACT",
                            "messageOrigin": "PLATFORM",
                            "text": "好的，明晚七点到九点见",
                            "sentAt": "2026-07-22T20:12:00+08:00",
                        }
                    ],
                    "event": {
                        "eventId": "peer-confirmed-time",
                        "eventType": "message",
                        "platform": "qq",
                        "chatType": "private",
                        "chatId": "3807050597",
                        "chatName": "km",
                        "senderId": "3807050597",
                        "senderName": "km",
                        "direction": "INBOUND",
                        "actorType": "CONTACT",
                        "messageOrigin": "PLATFORM",
                        "content": "好的，明晚七点到九点见",
                        "text": "好的，明晚七点到九点见",
                        "sentAt": "2026-07-22T20:12:00+08:00",
                    },
                }
            )
        )

        self.assertEqual("SEND_AND_COMPLETE", result.action)
        self.assertEqual("complete_delegated_task", result.requested_tool)
        self.assertEqual("那明晚见", result.message_instruction)
        self.assertIn("明晚七点到九点", result.completion_report)
        self.assertIn("complete_delegated_task", llm_client.choose_tool_calls[0]["toolNames"])

    async def test_action_graph_should_infer_peer_inbound_when_direction_is_missing(self) -> None:
        """NapCat 旧事件缺少 direction 时，外部 message 仍必须推动正在进行的委托。"""
        result = await self.workflow.decide_action(
            DelegatedTaskActionInput.model_validate(
                {
                    "task": {
                        "id": "task-1",
                        "objective": "协商明天家教时间",
                        "successCriteria": "双方明确确认时间",
                        "stateJson": json.dumps({"lastWriteBackStatus": "SENT", "lastPlannedAction": "SEND_MESSAGE"}, ensure_ascii=False),
                    },
                    "history": [],
                    "event": {
                        "eventId": "peer-without-direction",
                        "eventType": "message",
                        "sentAt": "2026-07-22T22:40:59+08:00",
                        "messageOrigin": "EXTERNAL",
                        "text": "下午行吗",
                    },
                }
            )
        )

        self.assertEqual("WAIT", result.action)
        self.assertEqual("update_delegated_task", result.requested_tool)

    async def test_action_graph_should_not_treat_agent_echo_as_peer_without_direction(self) -> None:
        """即使 direction 缺失，Agent 自身回显也只能等待，不能形成自动回复循环。"""
        result = await self.workflow.decide_action(
            DelegatedTaskActionInput.model_validate(
                {
                    "task": {
                        "id": "task-1",
                        "objective": "协商明天家教时间",
                        "successCriteria": "双方明确确认时间",
                        "stateJson": json.dumps({"lastWriteBackStatus": "SENT", "lastPlannedAction": "SEND_MESSAGE"}, ensure_ascii=False),
                    },
                    "history": [],
                    "event": {
                        "eventId": "agent-echo-without-direction",
                        "eventType": "message",
                        "sentAt": "2026-07-22T22:40:47+08:00",
                        "actorType": "AGENT",
                        "messageOrigin": "AGENT_AUTO",
                        "text": "明天晚上七点到九点家教，时间OK吗",
                    },
                }
            )
        )

        self.assertEqual("WAIT", result.action)
        self.assertEqual("update_delegated_task", result.requested_tool)

    async def test_action_graph_should_complete_before_generating_another_reply(self) -> None:
        """对方已经明确确认安排时，应直接调用结束工具，不再多发送一条客套回复。"""
        workflow = DelegatedTaskWorkflow(
            ToolCallingLlmClient(
                "complete_delegated_task",
                {
                    "reason": "当前联系人已确认约定",
                    "progressSummary": "双方已确认明晚安排",
                    "completionReport": "已约定明天晚上见面",
                    "outcome": "SUCCESS",
                    "knownFacts": ["对方确认明天晚上见"],
                    "evidence": ["没问题，那就明天晚上见"],
                    "evidenceEventIds": ["peer-close"],
                },
            )
        )
        action_input = DelegatedTaskActionInput.model_validate(
            {
                "task": {
                    "id": "task-1",
                    "objective": "预约明天晚上的家教课程",
                    "successCriteria": "对方明确确认课程时间",
                    "stateJson": json.dumps({"lastWriteBackStatus": "SENT", "lastPlannedAction": "SEND_MESSAGE"}, ensure_ascii=False),
                },
                "history": [],
                "event": {
                    "eventId": "peer-close",
                    "eventType": "message",
                    "sentAt": "2026-07-22T10:05:00+08:00",
                    "direction": "INBOUND",
                    "actorType": "CONTACT",
                    "messageOrigin": "PLATFORM",
                    "text": "没问题，那就明天晚上见",
                },
            }
        )

        result = await workflow.decide_action(action_input)

        self.assertEqual("COMPLETE_TASK", result.action)
        self.assertEqual("complete_delegated_task", result.requested_tool)
        self.assertIn("明天晚上见", result.completion_report)

    async def test_action_graph_should_complete_natural_confirmation_without_direction(self) -> None:
        """旧 NapCat 事件缺少 direction 时，自然口语确认仍应结束任务且不再回复。"""
        workflow = DelegatedTaskWorkflow(
            ToolCallingLlmClient(
                "complete_delegated_task",
                {
                    "reason": "联系人已完成自然语言确认",
                    "progressSummary": "课程时间已确认",
                    "completionReport": "已确认明晚七点到九点上课",
                    "outcome": "SUCCESS",
                    "evidence": ["好的 明晚七点到九点见"],
                    "evidenceEventIds": ["peer-natural-close"],
                },
            )
        )
        action_input = DelegatedTaskActionInput.model_validate(
            {
                "task": {
                    "id": "task-1",
                    "objective": "预约明天家教时间，晚上七点到九点",
                    "successCriteria": "对方明确确认课程时间",
                    "stateJson": json.dumps({"lastWriteBackStatus": "SENT", "lastPlannedAction": "SEND_MESSAGE"}, ensure_ascii=False),
                },
                "history": [
                    {
                        "eventId": "agent-proposal",
                        "eventType": "message",
                        "sentAt": "2026-07-22T22:59:00+08:00",
                        "actorType": "AGENT",
                        "messageOrigin": "AGENT_AUTO",
                        "text": "行 那就明晚七点到九点",
                    }
                ],
                "event": {
                    "eventId": "peer-natural-close",
                    "eventType": "message",
                    "sentAt": "2026-07-22T23:02:00+08:00",
                    "messageOrigin": "EXTERNAL",
                    "text": "好的 明晚七点到九点见",
                },
            }
        )

        result = await workflow.decide_action(action_input)

        self.assertEqual("COMPLETE_TASK", result.action)
        self.assertEqual("complete_delegated_task", result.requested_tool)
        self.assertEqual("已确认明晚七点到九点上课", result.completion_report)

    async def test_action_graph_should_repair_missing_completion_evidence_from_current_peer_message(self) -> None:
        """模型判断任务完成但漏填证据 ID 时，应从当前联系人消息补齐完成证据。"""
        workflow = DelegatedTaskWorkflow(
            ToolCallingLlmClient(
                "complete_delegated_task",
                {
                    "reason": "联系人已经确认时间",
                    "progressSummary": "课程时间已确认",
                    "completionReport": "",
                    "outcome": "SUCCESS",
                    "evidence": [],
                    "evidenceEventIds": [],
                },
            )
        )
        result = await workflow.decide_action(
            DelegatedTaskActionInput.model_validate(
                {
                    "task": {
                        "id": "task-repair-evidence",
                        "objective": "预约明天家教时间，晚上七点到九点",
                        "successCriteria": "对方明确确认课程时间",
                        "stateJson": json.dumps(
                            {
                                "lastWriteBackStatus": "SENT",
                                "lastPlannedAction": "SEND_MESSAGE",
                                "taskCreatedAt": "2026-07-22T20:00:00+08:00",
                                "taskTimezone": "Asia/Shanghai",
                            },
                            ensure_ascii=False,
                        ),
                    },
                    "history": [],
                    "event": {
                        "eventId": "peer-confirm-current",
                        "eventType": "message",
                        "sentAt": "2026-07-22T23:02:00+08:00",
                        "direction": "INBOUND",
                        "actorType": "CONTACT",
                        "messageOrigin": "PLATFORM",
                        "text": "好的 那明晚七点见",
                    },
                }
            )
        )

        self.assertEqual("COMPLETE_TASK", result.action)
        self.assertEqual("complete_delegated_task", result.requested_tool)
        self.assertIn("好的 那明晚七点见", result.evidence)
        graph_state = json.loads(result.state_json)
        self.assertIn("peer-confirm-current", graph_state["lastEvidenceEventIds"])

    async def test_action_graph_should_not_complete_time_counteroffer_question(self) -> None:
        """带时间的反问仍处于协商阶段，不能因为出现“可以”和时间词就提前结束。"""
        result = await self.workflow.decide_action(
            DelegatedTaskActionInput.model_validate(
                {
                    "task": {
                        "id": "task-1",
                        "objective": "预约明天家教时间，晚上七点到九点",
                        "successCriteria": "对方明确确认课程时间",
                        "stateJson": json.dumps({"lastWriteBackStatus": "SENT", "lastPlannedAction": "SEND_MESSAGE"}, ensure_ascii=False),
                    },
                    "history": [],
                    "event": {
                        "eventId": "peer-counteroffer",
                        "eventType": "message",
                        "sentAt": "2026-07-22T22:58:00+08:00",
                        "messageOrigin": "EXTERNAL",
                        "text": "还是晚上七点到九点吧 可以吗",
                    },
                }
            )
        )

        self.assertEqual("WAIT", result.action)
        self.assertEqual("update_delegated_task", result.requested_tool)

    async def test_action_graph_should_not_complete_without_model_tool_call(self) -> None:
        """未配置模型时，即使文本看似收尾也只能继续对话，程序不得猜测任务完成。"""
        result = await self.workflow.decide_action(
            DelegatedTaskActionInput.model_validate(
                {
                    "task": {
                        "id": "task-1",
                        "objective": "预约明天家教时间",
                        "successCriteria": "双方确认课程时间",
                        "stateJson": json.dumps({"lastWriteBackStatus": "SENT", "lastPlannedAction": "SEND_MESSAGE"}, ensure_ascii=False),
                    },
                    "history": [],
                    "event": {
                        "eventId": "peer-looking-complete",
                        "eventType": "message",
                        "sentAt": "2026-07-22T23:05:00+08:00",
                        "direction": "INBOUND",
                        "actorType": "CONTACT",
                        "messageOrigin": "PLATFORM",
                        "text": "好的 明天晚上见",
                    },
                }
            )
        )

        self.assertEqual("WAIT", result.action)
        self.assertEqual("update_delegated_task", result.requested_tool)

    async def test_action_graph_should_wait_when_reflection_returns_invalid_json(self) -> None:
        """完成复核模型不可用时必须停止发送，不能继续追问或猜测完成状态。"""
        workflow = DelegatedTaskWorkflow(BrokenCompletionReflectionLlmClient())
        result = await workflow.decide_action(
            DelegatedTaskActionInput.model_validate(
                {
                    "task": {
                        "id": "task-fallback-complete",
                        "createdAt": "2026-07-22T20:00:00+08:00",
                        "objective": "预约明天家教时间，晚上七点到九点",
                        "successCriteria": "对方明确接受课程时间",
                        "deadlineText": "明天晚上七点到九点",
                        "stateJson": json.dumps(
                            {
                                "lastWriteBackStatus": "SENT",
                                "lastPlannedAction": "SEND_MESSAGE",
                                "taskCreatedAt": "2026-07-22T20:00:00+08:00",
                                "taskTimezone": "Asia/Shanghai",
                                "timeline": [
                                    {
                                        "eventId": "agent-ask",
                                        "at": "2026-07-22T22:50:00+08:00",
                                        "direction": "OUTBOUND",
                                        "actorType": "AGENT",
                                        "messageOrigin": "AGENT_AUTO",
                                        "speaker": "我",
                                        "text": "明晚七点到九点方便吗",
                                    }
                                ],
                            },
                            ensure_ascii=False,
                        ),
                    },
                    "history": [],
                    "event": {
                        "eventId": "peer-fallback-confirm",
                        "eventType": "message",
                        "sentAt": "2026-07-22T23:10:00+08:00",
                        "direction": "INBOUND",
                        "actorType": "CONTACT",
                        "messageOrigin": "PLATFORM",
                        "text": "好的 那明晚七点到九点见",
                    },
                }
            )
        )

        self.assertEqual("WAIT", result.action)
        self.assertEqual("update_delegated_task", result.requested_tool)
        self.assertEqual("", result.message_instruction)
        self.assertIn("完成状态复核", result.reason)

    async def test_action_graph_should_not_fallback_complete_counteroffer_question(self) -> None:
        """完成复核失败时，仍在反问或协商的消息不能被兜底逻辑误判为完成。"""
        workflow = DelegatedTaskWorkflow(BrokenCompletionReflectionLlmClient())
        result = await workflow.decide_action(
            DelegatedTaskActionInput.model_validate(
                {
                    "task": {
                        "id": "task-fallback-question",
                        "createdAt": "2026-07-22T20:00:00+08:00",
                        "objective": "预约明天家教时间，晚上七点到九点",
                        "successCriteria": "对方明确接受课程时间",
                        "deadlineText": "明天晚上七点到九点",
                        "stateJson": json.dumps(
                            {
                                "lastWriteBackStatus": "SENT",
                                "lastPlannedAction": "SEND_MESSAGE",
                                "taskCreatedAt": "2026-07-22T20:00:00+08:00",
                                "taskTimezone": "Asia/Shanghai",
                            },
                            ensure_ascii=False,
                        ),
                    },
                    "history": [],
                    "event": {
                        "eventId": "peer-counter-question",
                        "eventType": "message",
                        "sentAt": "2026-07-22T23:10:00+08:00",
                        "direction": "INBOUND",
                        "actorType": "CONTACT",
                        "messageOrigin": "PLATFORM",
                        "text": "晚上七点到九点可以吗",
                    },
                }
            )
        )

        self.assertEqual("WAIT", result.action)
        self.assertEqual("update_delegated_task", result.requested_tool)

    async def test_action_graph_should_reject_completion_with_stale_evidence(self) -> None:
        """模型若只引用任务创建前的旧联系人消息，程序必须拒绝结束。"""
        workflow = DelegatedTaskWorkflow(
            ToolCallingLlmClient(
                "complete_delegated_task",
                {
                    "reason": "尝试使用旧消息结束",
                    "progressSummary": "模型认为任务完成",
                    "completionReport": "任务已完成",
                    "outcome": "SUCCESS",
                    "evidence": ["旧确认"],
                    "evidenceEventIds": ["old-peer"],
                },
            )
        )
        result = await workflow.decide_action(
            DelegatedTaskActionInput.model_validate(
                {
                    "task": {
                        "id": "task-1",
                        "objective": "预约课程",
                        "successCriteria": "对方确认时间",
                        "stateJson": json.dumps(
                            {
                                "lastWriteBackStatus": "SENT",
                                "lastPlannedAction": "SEND_MESSAGE",
                                "taskCreatedAt": "2026-07-22T21:00:00+08:00",
                                "taskTimezone": "Asia/Shanghai",
                            },
                            ensure_ascii=False,
                        ),
                    },
                    "history": [
                        {
                            "eventId": "old-peer",
                            "eventType": "message",
                            "sentAt": "2026-07-22T20:00:00+08:00",
                            "direction": "INBOUND",
                            "actorType": "CONTACT",
                            "messageOrigin": "PLATFORM",
                            "text": "旧确认",
                        }
                    ],
                    "event": {
                        "eventId": "current-peer",
                        "eventType": "message",
                        "sentAt": "2026-07-22T23:06:00+08:00",
                        "direction": "INBOUND",
                        "actorType": "CONTACT",
                        "messageOrigin": "PLATFORM",
                        "text": "那具体几点",
                    },
                }
            )
        )

        # 旧证据不能证明任务完成，但也不能把失败的结束尝试降级成重复回复。
        self.assertEqual("WAIT", result.action)
        self.assertEqual("update_delegated_task", result.requested_tool)
        self.assertEqual("", result.completion_report)

    async def test_action_graph_should_send_corrected_close_before_cross_day_completion(self) -> None:
        """跨天确认完成时，模型可以要求先发送符合当天口径的收尾消息，再结束主控台任务。"""
        workflow = DelegatedTaskWorkflow(
            ToolCallingLlmClient(
                "complete_delegated_task",
                {
                    "reason": "联系人已经明确确认昨晚协商的安排",
                    "progressSummary": "今晚七点到九点的课程已经确认",
                    "completionReport": "已和联系人约定今晚七点到九点上课",
                    "outcome": "SUCCESS",
                    "knownFacts": ["目标日期为2026-07-23", "课程时间为晚上七点到九点"],
                    "evidence": ["好的 明晚七点到九点见", "好的 那就这么定了"],
                    "evidenceEventIds": ["peer-confirm-yesterday", "peer-confirm-today"],
                    "finalMessageInstruction": "简短回复今晚见，不要继续说成明天晚上",
                },
            )
        )
        result = await workflow.decide_action(
            DelegatedTaskActionInput.model_validate(
                {
                    "task": {
                        "id": "task-send-and-complete",
                        "createdAt": "2026-07-22T20:00:00+08:00",
                        "objective": "预约明天家教时间，晚上七点到九点",
                        "successCriteria": "对方明确确认课程时间",
                        "deadlineText": "明天晚上七点到九点",
                        "stateJson": json.dumps(
                            {
                                "lastWriteBackStatus": "SENT",
                                "lastPlannedAction": "SEND_MESSAGE",
                                "taskCreatedAt": "2026-07-22T20:00:00+08:00",
                                "taskTimezone": "Asia/Shanghai",
                                "resolvedTimeText": "2026-07-23晚上七点到九点（任务创建时解析）",
                                "timeline": [
                                    {
                                        "eventId": "peer-confirm-yesterday",
                                        "at": "2026-07-22T23:02:00+08:00",
                                        "speaker": "对方",
                                        "text": "好的 明晚七点到九点见",
                                        "eventType": "message",
                                    }
                                ],
                            },
                            ensure_ascii=False,
                        ),
                    },
                    "history": [],
                    "event": {
                        "eventId": "peer-confirm-today",
                        "eventType": "message",
                        "sentAt": "2026-07-23T12:33:00+08:00",
                        "direction": "INBOUND",
                        "actorType": "CONTACT",
                        "messageOrigin": "PLATFORM",
                        "text": "好的 那就这么定了",
                    },
                }
            )
        )

        self.assertEqual("SEND_AND_COMPLETE", result.action)
        self.assertEqual("complete_delegated_task", result.requested_tool)
        self.assertEqual("简短回复今晚见，不要继续说成明天晚上", result.message_instruction)
        self.assertIn("今晚七点到九点", result.completion_report)

    async def test_action_graph_should_complete_cross_day_task_from_persisted_timeline(self) -> None:
        """跨天后应恢复已持久化的确认过程，并按创建时固化的日期结束主控台任务。"""
        llm_client = ToolCallingLlmClient(
            "complete_delegated_task",
            {
                "reason": "联系人今天再次明确确认了昨晚协商的安排",
                "progressSummary": "今晚七点到九点的课程已经确认",
                "completionReport": "已和联系人约定今晚七点到九点上课",
                "outcome": "SUCCESS",
                "knownFacts": ["目标日期为2026-07-23", "课程时间为晚上七点到九点"],
                "evidence": ["好的 明晚七点到九点见", "好的 那就这么定了"],
                "evidenceEventIds": ["peer-confirm-yesterday", "peer-confirm-today"],
            },
        )
        workflow = DelegatedTaskWorkflow(llm_client)
        result = await workflow.decide_action(
            DelegatedTaskActionInput.model_validate(
                {
                    "task": {
                        "id": "task-cross-day",
                        "objective": "预约明天家教时间，晚上七点到九点",
                        "successCriteria": "对方明确确认课程时间",
                        "deadlineText": "明天晚上七点到九点",
                        "stateJson": json.dumps(
                            {
                                "lastWriteBackStatus": "SENT",
                                "lastPlannedAction": "SEND_MESSAGE",
                                "taskCreatedAt": "2026-07-22T20:00:00+08:00",
                                "taskTimezone": "Asia/Shanghai",
                                "resolvedTimeText": "2026-07-23晚上七点到九点（任务创建时解析）",
                                "timeline": [
                                    {
                                        "eventId": "peer-confirm-yesterday",
                                        "at": "2026-07-22T23:02:00+08:00",
                                        "speaker": "对方",
                                        "text": "好的 明晚七点到九点见",
                                        "eventType": "message",
                                    }
                                ],
                            },
                            ensure_ascii=False,
                        ),
                    },
                    # 模拟 Java 历史查询窗口没有带回昨晚消息，图仍须从 stateJson 恢复。
                    "history": [],
                    "event": {
                        "eventId": "peer-confirm-today",
                        "eventType": "message",
                        "sentAt": "2026-07-23T12:33:00+08:00",
                        "direction": "INBOUND",
                        "actorType": "CONTACT",
                        "messageOrigin": "PLATFORM",
                        "text": "好的 那就这么定了",
                    },
                }
            )
        )

        self.assertEqual("COMPLETE_TASK", result.action)
        self.assertEqual("complete_delegated_task", result.requested_tool)
        self.assertIn("今晚七点到九点", result.completion_report)
        model_payload = json.loads(llm_client.calls[-1]["userMessage"])["context"]
        self.assertEqual("2026-07-23T12:33:00+08:00", model_payload["currentTime"])
        self.assertIn("2026-07-23", model_payload["resolvedTimeText"])
        # 模型视图不应携带事件 ID，只保留可用于推理的时间、角色和文本证据。
        self.assertEqual(
            ["好的 明晚七点到九点见", "好的 那就这么定了"],
            [item["text"] for item in model_payload["conversationTimeline"]],
        )
        self.assertIn("相对时间表述必须结合消息时间戳", llm_client.calls[-1]["systemPrompt"])


    async def test_should_observe_pre_task_history_inside_react_graph_before_replanning(self) -> None:
        """任务前历史应由图内读取后回灌下一轮规划，不能作为动作泄漏给 Java。"""
        llm_client = ReActHistoryObservationLlmClient()
        history_client = PreTaskHistoryEventCenterClient()
        workflow = DelegatedTaskWorkflow(llm_client, history_client)

        result = await workflow.decide_action(
            DelegatedTaskActionInput.model_validate(
                {
                    "task": {
                        "id": "task-react-history",
                        "objective": "和老师确认明天家教时间",
                        "successCriteria": "对方确认课程时间",
                        "createdAt": "2026-07-22T12:00:00+08:00",
                        "stateJson": json.dumps(
                            {
                                "taskCreatedAt": "2026-07-22T12:00:00+08:00",
                                "historyAccessAllowed": True,
                            },
                            ensure_ascii=False,
                        ),
                    },
                    "history": [],
                    "historyAccessAllowed": True,
                    "event": {
                        "eventId": "current-event",
                        "platform": "qq",
                        "chatType": "private",
                        "chatId": "3807050597",
                        "userId": "user-1",
                        "eventType": "message",
                        "sentAt": "2026-07-22T12:10:00+08:00",
                        "direction": "INBOUND",
                        "actorType": "CONTACT",
                        "messageOrigin": "PLATFORM",
                        "text": "老师在吗",
                    },
                }
            )
        )

        self.assertEqual("SEND_MESSAGE", result.action)
        self.assertEqual("send_qq_message", result.requested_tool)
        self.assertEqual(1, len(history_client.calls))
        self.assertEqual("3807050597", history_client.calls[0]["chatId"])
        self.assertEqual("2026-07-22T12:00:00+08:00", history_client.calls[0]["before"])
        self.assertEqual(2, llm_client.planning_calls)
        self.assertIn("我们之前约好先确认家教时间", llm_client.planning_messages[-1])
        self.assertIn("get_task_pre_history", llm_client.planning_messages[-1])

        state = json.loads(result.state_json)
        self.assertEqual("history-owner-1", state["preTaskHistory"][0]["eventId"])
        self.assertEqual("get_task_pre_history", state["toolObservations"][-1]["tool"])


    async def test_action_graph_should_promote_reviewed_reply_to_completion_when_context_proves_done(self) -> None:
        """常规回复通过审查后，若任务后时间线已证明完成，应提升为结束任务工具。"""
        llm_client = CompletionReflectionLlmClient()
        workflow = DelegatedTaskWorkflow(llm_client)

        result = await workflow.decide_action(
            DelegatedTaskActionInput.model_validate(
                {
                    "task": {
                        "id": "task-reflection-complete",
                        "createdAt": "2026-07-22T20:00:00+08:00",
                        "objective": "预约明天家教时间，晚上七点到九点",
                        "successCriteria": "对方明确接受课程时间",
                        "deadlineText": "明天晚上七点到九点",
                        "stateJson": json.dumps(
                            {
                                "lastWriteBackStatus": "SENT",
                                "lastPlannedAction": "SEND_MESSAGE",
                                "taskCreatedAt": "2026-07-22T20:00:00+08:00",
                                "taskTimezone": "Asia/Shanghai",
                                "timeline": [
                                    {
                                        "eventId": "peer-confirm-yesterday",
                                        "at": "2026-07-22T23:02:00+08:00",
                                        "speaker": "对方",
                                        "text": "好的 明晚七点到九点见",
                                        "eventType": "message",
                                        "direction": "INBOUND",
                                        "actorType": "CONTACT",
                                        "messageOrigin": "PLATFORM",
                                    }
                                ],
                            },
                            ensure_ascii=False,
                        ),
                    },
                    "history": [],
                    "event": {
                        "eventId": "peer-confirm-today",
                        "eventType": "message",
                        "sentAt": "2026-07-23T12:33:00+08:00",
                        "direction": "INBOUND",
                        "actorType": "CONTACT",
                        "messageOrigin": "PLATFORM",
                        "text": "好的 那就这么定了",
                    },
                }
            )
        )

        self.assertIn(result.action, {"COMPLETE_TASK", "SEND_AND_COMPLETE"})
        self.assertEqual("complete_delegated_task", result.requested_tool)
        self.assertEqual("今晚见", result.message_instruction)
        self.assertIn("peer-confirm-today", result.tool_arguments.get("evidenceEventIds", []))
        self.assertTrue(any("COMPLETION_REFLECTION" in item["systemPrompt"] for item in llm_client.calls))

    async def test_action_graph_should_finish_confirmed_game_invitation_without_reopening_topic(self) -> None:
        """双方已接受邀约且联系人明确收尾时，应结束任务而不是再次询问同一安排。"""
        llm_client = CompletionReflectionLlmClient(
            {
                "shouldComplete": True,
                "outcome": "SUCCESS",
                "reason": "双方已经接受同一时间的邀约，联系人随后明确确认见面",
                "progressSummary": "今晚十点的游戏邀约已经确认",
                "completionReport": "已约好今晚十点一起玩游戏",
                "finalMessageInstruction": "",
                "knownFacts": ["双方约定今晚十点一起玩游戏"],
                "pendingConditions": [],
                "evidence": ["今晚十点三角洲 来不来", "好 十点见"],
                "evidenceEventIds": ["peer-invite", "peer-final-confirm"],
            }
        )
        workflow = DelegatedTaskWorkflow(llm_client)

        result = await workflow.decide_action(
            DelegatedTaskActionInput.model_validate(
                {
                    "task": {
                        "id": "task-game-invitation",
                        "createdAt": "2026-07-28T20:10:00+08:00",
                        "objective": "和联系人约今晚十点一起玩游戏",
                        "successCriteria": "双方明确接受今晚十点的邀约",
                        "deadlineText": "今晚十点",
                        "stateJson": json.dumps(
                            {
                                "taskCreatedAt": "2026-07-28T20:10:00+08:00",
                                "taskTimezone": "Asia/Shanghai",
                                "timeline": [
                                    {
                                        "eventId": "peer-invite",
                                        "at": "2026-07-28T20:15:00+08:00",
                                        "speaker": "对方",
                                        "text": "今晚十点三角洲 来不来",
                                        "eventType": "message",
                                        "direction": "INBOUND",
                                        "actorType": "CONTACT",
                                        "messageOrigin": "PLATFORM",
                                    },
                                    {
                                        "eventId": "owner-accept",
                                        "at": "2026-07-28T20:16:00+08:00",
                                        "speaker": "我方",
                                        "text": "可以",
                                        "eventType": "message",
                                        "direction": "OUTBOUND",
                                        "actorType": "ACCOUNT_OWNER",
                                        "messageOrigin": "AGENT",
                                    },
                                ],
                            },
                            ensure_ascii=False,
                        ),
                    },
                    "history": [],
                    "event": {
                        "eventId": "peer-final-confirm",
                        "eventType": "message",
                        "sentAt": "2026-07-28T20:17:00+08:00",
                        "direction": "INBOUND",
                        "actorType": "CONTACT",
                        "messageOrigin": "PLATFORM",
                        "text": "好 十点见",
                    },
                }
            )
        )

        self.assertEqual("COMPLETE_TASK", result.action)
        self.assertEqual("complete_delegated_task", result.requested_tool)
        self.assertEqual("", result.message_instruction)
        self.assertIn("peer-final-confirm", result.tool_arguments.get("evidenceEventIds", []))
        self.assertTrue(any("COMPLETION_REFLECTION" in item["systemPrompt"] for item in llm_client.calls))

    def test_completion_validation_should_accept_persisted_peer_evidence_when_current_event_is_not_peer(self) -> None:
        """重启或回写触发时，只要任务时间线已有联系人证据，就允许主控台任务完成。"""
        workflow = DelegatedTaskWorkflow(DisabledLlmClient())
        evaluation = {
            "status": "COMPLETED",
            "requestedTool": "complete_delegated_task",
            "reason": "联系人已经确认安排",
            "progressSummary": "今晚七点到九点的课程已经确认",
            "completionReport": "已确认今晚七点到九点上课",
            "evidence": ["好的 那明晚七点到九点见", "好 明天晚上见"],
            "evidenceEventIds": ["peer-confirm-1", "peer-confirm-2"],
            "messageInstruction": "今晚见",
        }
        timeline = [
            {
                "eventId": "owner-ask",
                "at": "2026-07-22T22:58:00+08:00",
                "speaker": "我",
                "text": "晚上七点到九点可以吗",
                "eventType": "message",
                "direction": "OUTBOUND",
                "actorType": "OWNER",
                "messageOrigin": "USER_MANUAL",
            },
            {
                "eventId": "peer-confirm-1",
                "at": "2026-07-22T23:02:00+08:00",
                "speaker": "对方",
                "text": "好的 那明晚七点到九点见",
                "eventType": "message",
                "direction": "INBOUND",
                "actorType": "CONTACT",
                "messageOrigin": "PLATFORM",
            },
            {
                "eventId": "peer-confirm-2",
                "at": "2026-07-23T12:33:00+08:00",
                "speaker": "对方",
                "text": "好 明天晚上见",
                "eventType": "message",
                "direction": "INBOUND",
                "actorType": "CONTACT",
                "messageOrigin": "PLATFORM",
            },
        ]

        result = workflow._validate_completion_tool_call(
            evaluation=evaluation,
            timeline=timeline,
            # 当前事件模拟主动收尾消息回写，不是联系人入站消息。
            event={
                "eventId": "agent-close",
                "eventType": "message",
                "sentAt": "2026-07-23T12:34:00+08:00",
                "direction": "OUTBOUND",
                "actorType": "AGENT",
                "messageOrigin": "AGENT",
                "text": "今晚见",
            },
            task={"id": "task-cross-day", "createdAt": "2026-07-22T20:00:00+08:00"},
            previous_state={"taskCreatedAt": "2026-07-22T20:00:00+08:00"},
        )

        self.assertEqual("COMPLETED", result["status"])
        self.assertEqual("complete_delegated_task", result["requestedTool"])
        self.assertEqual(["peer-confirm-1", "peer-confirm-2"], result["evidenceEventIds"])


class CompactPlannerLlm:
    """返回固定单次规划契约，并记录调用是否走 fast 通道。"""

    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    def is_enabled(self, model_profile=None, *, fast=False) -> bool:
        return True

    async def generate_reply(self, system_prompt, user_message, temperature=0.7, model_profile=None, *, fast=False) -> str:
        self.calls.append({"fast": fast, "userMessage": user_message})
        return json.dumps(self.payload, ensure_ascii=False)


def _km_candidate() -> ConversationCandidate:
    return ConversationCandidate.model_validate(
        {"platform": "qq", "chatType": "private", "chatId": "3807050597", "chatName": "km"}
    )


class CompactPlannerTest(unittest.IsolatedAsyncioTestCase):
    """P2b：单次规划（一次 fast 调用替代 target+plan+compile）。"""

    def _valid_payload(self) -> dict:
        return {
            "title": "约游戏",
            "workflowType": "PLAN_EXECUTE",
            "steps": [
                {
                    "stepKey": "step_1",
                    "order": 1,
                    "role": "executor",
                    "instruction": "询问 km 明天晚上几点有空打游戏",
                    "targetChatType": "private",
                    "targetChatId": "3807050597",
                    "dependsOn": [],
                    "requiredFacts": [],
                    "producesFacts": [],
                    "objective": "拿到 km 明天晚上的空闲时间",
                    "successCriteria": "km 明确给出时间",
                }
            ],
        }

    async def test_compact_plan_success_uses_main_channel(self) -> None:
        llm = CompactPlannerLlm(self._valid_payload())
        workflow = DelegatedTaskWorkflow(llm)
        plan = await workflow.plan_workspace_command_compact(
            "帮我和 km 约明天晚上打游戏",
            [_km_candidate()],
        )
        self.assertEqual(len(plan.steps), 1)
        self.assertEqual(plan.steps[0].objective, "拿到 km 明天晚上的空闲时间")
        self.assertEqual(plan.steps[0].success_criteria, "km 明确给出时间")
        # 规划是质量敏感任务，必须走主通道（思考模型）保证契约正确，不用 fast。
        self.assertFalse(llm.calls[0]["fast"])
        # 线程前序要传给模型
        self.assertIn("command", llm.calls[0]["userMessage"])

    async def test_compact_plan_rejects_unauthorized_target(self) -> None:
        from app.workflows.delegated_task_graph import WorkflowPlanningError

        payload = self._valid_payload()
        payload["steps"][0]["targetChatId"] = "99999999"  # 不在白名单
        llm = CompactPlannerLlm(payload)
        workflow = DelegatedTaskWorkflow(llm)
        with self.assertRaises(WorkflowPlanningError):
            await workflow.plan_workspace_command_compact("约游戏", [_km_candidate()])

    async def test_compact_plan_objective_fallback(self) -> None:
        payload = self._valid_payload()
        payload["steps"][0]["objective"] = ""
        payload["steps"][0]["successCriteria"] = ""
        llm = CompactPlannerLlm(payload)
        workflow = DelegatedTaskWorkflow(llm)
        plan = await workflow.plan_workspace_command_compact("约游戏", [_km_candidate()])
        # 契约字段空时用 instruction 兜底
        self.assertEqual(plan.steps[0].objective, plan.steps[0].instruction)
        self.assertTrue(plan.steps[0].success_criteria)

    async def test_compact_plan_empty_steps_rejected(self) -> None:
        from app.workflows.delegated_task_graph import WorkflowPlanningError

        llm = CompactPlannerLlm({"title": "空", "steps": []})
        workflow = DelegatedTaskWorkflow(llm)
        with self.assertRaises(WorkflowPlanningError):
            await workflow.plan_workspace_command_compact("约游戏", [_km_candidate()])


if __name__ == "__main__":
    unittest.main()
