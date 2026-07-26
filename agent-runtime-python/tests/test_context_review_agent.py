from __future__ import annotations

import unittest

import httpx

from app.agents.context_review_agent import ContextReviewAgent
from app.clients.public_knowledge_search import PublicKnowledgeSearchClient
from app.schemas.events import Sender, UnifiedEvent
from app.schemas.tasks import AgentTaskContext
from app.tools.registry import ToolRegistry


class SequencedContextLlm:
    """按顺序返回情景审查 JSON，并保留提示词供角色与隐私断言。"""

    def __init__(self, responses: list[object]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def is_enabled(self, model_profile=None) -> bool:
        """测试环境始终视为已经配置模型。"""
        return True

    async def generate_reply(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.0,
        model_profile=None,
    ) -> str:
        """记录每次审查输入，并弹出预置结果。"""
        self.calls.append({"system": system_prompt, "user": user_message})
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return str(response)


class StubSearchClient:
    """模拟受限公共检索，验证 ContextReviewAgent 只在 RETRIEVE 时调用。"""

    def __init__(self, results: list[dict] | None = None) -> None:
        self.results = results or []
        self.queries: list[list[str]] = []

    async def search(self, queries: list[str]) -> list[dict]:
        """保存模型建议的实体查询并返回固定资料。"""
        self.queries.append(list(queries))
        return list(self.results)


def build_context(candidate: str, review_mode: str = "AUTO_REWRITE") -> AgentTaskContext:
    """构造包含双方近期聊天、会话人设和 SocialAgent 草稿的最小上下文。"""
    event = UnifiedEvent(
        eventId="context-review-001",
        platform="qq",
        scene="life",
        eventType="message",
        chatType="private",
        chatId="10001",
        selfId="20002",
        sender=Sender(id="10001", name="朋友"),
        text="刚在打原神 你抽到啥了",
        timestamp="2026-07-13T16:00:00+08:00",
        rawPayload={},
    )
    return AgentTaskContext(
        task_id="context-task",
        route="social_reply",
        event=event,
        history_context=[
            {"role": "peer", "text": "刚在打原神"},
            {"role": "self", "text": "我还没上线"},
        ],
        metadata={
            "conversation_profile_match": {
                "profile": {
                    "personaMode": "PROMPT",
                    "systemPrompt": "像普通大学同学一样短句聊天，不要装客服",
                    "reviewMode": review_mode,
                }
            },
            "resolved_model_profile": {
                "profile": {
                    "id": "model-1",
                    "userId": "user-1",
                    "name": "test",
                    "apiKey": "test-key",
                    "model": "test-model",
                }
            },
            "previous_results": {"social": {"draft": candidate}},
        },
    )


class ContextReviewAgentTest(unittest.IsolatedAsyncioTestCase):
    async def test_active_delegated_task_rewrites_counterproposal_instead_of_handoff(self) -> None:
        """工作台委托收到普通时间反问时应继续自动协商，不能继承设定集的严格接管默认值。"""
        llm = SequencedContextLlm([
            '{"decision":"HANDOFF","reason":"不能确定账号主人下午是否有空",'
            '"rewrittenDraft":"下午具体几点方便",'
            '"searchQueries":[],"checks":{"contextCoherent":true,'
            '"personaAligned":true,"speakerConsistent":true,'
            '"worldKnowledgeConsistent":true,"naturalConversation":true,'
            '"answersLatestMessage":true,"currentStateGrounded":false},'
            '"unsupportedPersonalClaims":["下午有空"],"entityConflicts":[]}'
        ])
        context = build_context("下午可以", review_mode="STRICT_HANDOFF")
        context.event.text = "下午方便吗"
        context.metadata["delegated_task"] = {
            "status": "ACTIVE",
            "targetName": "km",
            "objective": "预约明天家教时间",
            "successCriteria": "双方确认明确时间",
            "deadlineText": "明天",
            "originalCommand": "帮我和km预约明天家教时间",
        }
        context.metadata["delegated_task_action"] = {
            "action": "SEND_MESSAGE",
            "reason": "联系人提出新的时间条件",
            "messageInstruction": "回应联系人最新反问并继续确认时间",
        }
        agent = ContextReviewAgent(ToolRegistry(), llm_client=llm, search_client=StubSearchClient())

        result = await agent.run(context, "review_context")

        self.assertEqual("rewritten", result.status)
        self.assertFalse(result.structured_result.get("handoffRequired", False))
        self.assertEqual("REWRITE", result.structured_result["contextDecision"])
        self.assertIn("任务图本轮受控动作", llm.calls[0]["user"])
        self.assertIn("当前最终审批策略为 AUTO_REWRITE", llm.calls[0]["system"])

    async def test_should_treat_active_delegated_task_time_as_authorized_evidence(self) -> None:
        """委托中明确给出的预约时间必须进入情景审查，不能因历史未提及而转人工。"""
        llm = SequencedContextLlm([
            '{"decision":"APPROVE","reason":"时间来自账号主人明确授权的活动委托",'
            '"rewrittenDraft":"","searchQueries":[],"checks":{'
            '"contextCoherent":true,"personaAligned":true,"speakerConsistent":true,'
            '"worldKnowledgeConsistent":true,"naturalConversation":true,'
            '"answersLatestMessage":true,"currentStateGrounded":true,'
            '"doesNotRepeatAnsweredQuestion":true},'
            '"unsupportedPersonalClaims":[],"entityConflicts":[]}'
        ])
        context = build_context("明晚七点到九点", review_mode="STRICT_HANDOFF")
        context.event.text = "老师你好，明天什么时候上课呢"
        context.metadata["delegated_task"] = {
            "status": "ACTIVE",
            "targetName": "km",
            "objective": "预约明天家教时间，定到晚上七点到九点",
            "successCriteria": "对方明确接受、拒绝或提出无法继续的条件",
            "deadlineText": "明天晚上七点到九点",
            "originalCommand": "帮我和km预约一下明天家教时间，预约到晚上七点到九点，他是我的学生",
        }
        agent = ContextReviewAgent(ToolRegistry(), llm_client=llm, search_client=StubSearchClient())

        result = await agent.run(context, "review_context")

        self.assertEqual("approved", result.status)
        review_input = llm.calls[0]["user"]
        self.assertIn("账号主人明确授权的委托契约", review_input)
        self.assertIn("任务目标：预约明天家教时间，定到晚上七点到九点", review_input)
        self.assertIn("时间要求：明天晚上七点到九点", review_input)
        self.assertIn("原始控制指令属于控制面", review_input)

    async def test_should_describe_persistent_timeout_as_service_unavailable(self) -> None:
        """审查持续超时时必须闭锁，但接管卡片不能误导成候选内容违规。"""
        request = httpx.Request("POST", "https://example.com/v1/chat/completions")
        llm = SequencedContextLlm([httpx.ReadTimeout("timed out", request=request)])
        agent = ContextReviewAgent(ToolRegistry(), llm_client=llm, search_client=StubSearchClient())

        result = await agent.run(build_context("一个月 15"), "review_context")

        self.assertEqual("needs_human", result.status)
        self.assertIn("暂时不可用", result.structured_result["handoffSummary"])
        self.assertTrue(result.structured_result["checks"]["reviewUnavailable"])

    async def test_approve_requires_every_structured_check_to_be_true(self) -> None:
        """模型不能一边返回 APPROVE，一边把当前状态依据标成失败后仍被代码放行。"""
        llm = SequencedContextLlm([
            '{"decision":"APPROVE","reason":"可以衔接",'
            '"rewrittenDraft":"","searchQueries":[],"checks":{'
            '"contextCoherent":true,"personaAligned":true,"speakerConsistent":true,'
            '"worldKnowledgeConsistent":true,"naturalConversation":true,'
            '"answersLatestMessage":true,"currentStateGrounded":false},'
            '"unsupportedPersonalClaims":[],"entityConflicts":[]}'
        ])
        agent = ContextReviewAgent(ToolRegistry(), llm_client=llm, search_client=StubSearchClient())

        result = await agent.run(
            build_context("刚打完原神\n正刷手机呢", review_mode="STRICT_HANDOFF"),
            "review_context",
        )

        self.assertEqual("needs_human", result.status)
        self.assertTrue(result.structured_result["handoffRequired"])
        self.assertIn("currentStateGrounded", result.structured_result["handoffReason"])

    async def test_auto_rewrite_defers_missing_fallback_to_final_review(self) -> None:
        """自动纠偏模式没有情景改写稿时应继续最终纠偏，不能提前生成接管事项。"""
        llm = SequencedContextLlm([
            '{"decision":"HANDOFF","reason":"缺少账号主人的当前状态",'
            '"rewrittenDraft":"","searchQueries":[],"checks":{'
            '"contextCoherent":false,"personaAligned":true,"speakerConsistent":true,'
            '"worldKnowledgeConsistent":true,"naturalConversation":false,'
            '"answersLatestMessage":true,"currentStateGrounded":false},'
            '"unsupportedPersonalClaims":["今天有点累"],"entityConflicts":[]}'
        ])
        agent = ContextReviewAgent(ToolRegistry(), llm_client=llm, search_client=StubSearchClient())

        result = await agent.run(build_context("今天有点累 明晚见"), "review_context")

        self.assertEqual("rewrite_pending", result.status)
        self.assertEqual("REWRITE", result.structured_result["contextDecision"])
        self.assertTrue(result.structured_result["contextRewritePending"])
        self.assertFalse(result.structured_result.get("handoffRequired", False))

    async def test_approve_passes_only_with_complete_consistent_contract(self) -> None:
        """上下文、身份、事实和最新消息检查全部通过时才保留原候选。"""
        llm = SequencedContextLlm([
            '{"decision":"APPROVE","reason":"直接回应当前问题",'
            '"rewrittenDraft":"","searchQueries":[],"checks":{'
            '"contextCoherent":true,"personaAligned":true,"speakerConsistent":true,'
            '"worldKnowledgeConsistent":true,"naturalConversation":true,'
            '"answersLatestMessage":true,"currentStateGrounded":true},'
            '"unsupportedPersonalClaims":[],"entityConflicts":[]}'
        ])
        agent = ContextReviewAgent(ToolRegistry(), llm_client=llm, search_client=StubSearchClient())

        result = await agent.run(build_context("你抽到啥了"), "review_context")

        self.assertEqual("approved", result.status)
        self.assertEqual("APPROVE", result.structured_result["contextDecision"])
        self.assertEqual("你抽到啥了", result.structured_result["reviewedDraft"])

    async def test_rewrites_unsupported_personal_state_without_hardcoded_keywords(self) -> None:
        """个人经历没有历史依据时，情景层应使用模型给出的安全改写而不是继续扮演。"""
        llm = SequencedContextLlm([
            '{"decision":"REWRITE","reason":"账号主人没有抽卡记录",'
            '"rewrittenDraft":"你说的是哪个角色",'
            '"checks":{"contextCoherent":false,"personaAligned":true,'
            '"speakerConsistent":false,"worldKnowledgeConsistent":true,'
            '"naturalConversation":false},'
            '"unsupportedPersonalClaims":["我刚歪了两个钟离"],"entityConflicts":[]}'
        ])
        agent = ContextReviewAgent(ToolRegistry(), llm_client=llm, search_client=StubSearchClient())

        result = await agent.run(
            build_context("哇 珂莱塔\n我刚歪了两个钟离"),
            "review_context",
        )

        self.assertEqual(result.structured_result["contextDecision"], "REWRITE")
        self.assertEqual(result.structured_result["reviewedDraft"], "你说的是哪个角色")
        self.assertTrue(result.structured_result["contextRewritten"])
        self.assertIn("我：我还没上线", llm.calls[0]["user"])
        self.assertIn("对方：刚在打原神", llm.calls[0]["user"])

    async def test_retrieves_only_public_entity_evidence_and_reviews_once_more(self) -> None:
        """实体世界冲突只触发一次受限检索，检索结果必须经过第二轮情景审查。"""
        llm = SequencedContextLlm([
            '{"decision":"RETRIEVE","reason":"需要核对角色所属作品",'
            '"rewrittenDraft":"你说的是另一个游戏里的角色吧",'
            '"searchQueries":["珂莱塔 所属游戏","钟离 所属游戏"],'
            '"checks":{"worldKnowledgeConsistent":false},'
            '"unsupportedPersonalClaims":[],"entityConflicts":["原神与珂莱塔"]}',
            '{"decision":"REWRITE","reason":"角色来自不同作品",'
            '"rewrittenDraft":"珂莱塔不是原神里的吧",'
            '"searchQueries":[],"checks":{"worldKnowledgeConsistent":true},'
            '"unsupportedPersonalClaims":[],"entityConflicts":[]}',
        ])
        search = StubSearchClient([
            {
                "source": "public_web_search",
                "query": "珂莱塔 所属游戏",
                "title": "角色资料",
                "url": "https://example.test/carlotta",
                "content": "珂莱塔是鸣潮中的角色",
            }
        ])
        agent = ContextReviewAgent(ToolRegistry(), llm_client=llm, search_client=search)

        result = await agent.run(build_context("哇 珂莱塔"), "review_context")

        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(search.queries, [["珂莱塔 所属游戏", "钟离 所属游戏"]])
        self.assertTrue(result.structured_result["publicSearchUsed"])
        self.assertEqual(result.structured_result["reviewedDraft"], "珂莱塔不是原神里的吧")
        self.assertIn("珂莱塔是鸣潮中的角色", llm.calls[1]["user"])

    async def test_unconfigured_search_uses_safe_fallback_in_auto_rewrite_mode(self) -> None:
        """没有搜索 Key 时不访问网络，AUTO_REWRITE 可采用不含新事实的备选短句。"""
        llm = SequencedContextLlm([
            '{"decision":"RETRIEVE","reason":"实体关系待核对",'
            '"rewrittenDraft":"你说的是哪个游戏里的",'
            '"searchQueries":["陌生角色 所属作品"],"checks":{},'
            '"unsupportedPersonalClaims":[],"entityConflicts":["陌生角色"]}'
        ])
        agent = ContextReviewAgent(
            ToolRegistry(),
            llm_client=llm,
            search_client=PublicKnowledgeSearchClient(api_key=""),
        )

        result = await agent.run(build_context("这个角色挺强的"), "review_context")

        self.assertEqual(result.structured_result["contextDecision"], "REWRITE")
        self.assertEqual(result.structured_result["reviewedDraft"], "你说的是哪个游戏里的")
        self.assertFalse(result.structured_result["publicSearchUsed"])

    async def test_should_remove_question_that_was_already_answered_in_history(self) -> None:
        """Skill 再次启动问卷时，代码层删除已回答项，只保留尚未推进的问题。"""
        context = build_context("你多少分 哪个省的\n家里能支持你读到博士吗")
        context.event.text = "物化生"
        context.history_context = [
            {
                "role": "self",
                "text": "你多少分 哪个省的 家里做什么的",
                "messageOrigin": "AGENT_AUTO",
                "factAuthority": "agent_output",
            },
            {
                "role": "peer",
                "text": "600 安徽 家里正常工薪阶层",
                "messageOrigin": "EXTERNAL",
                "factAuthority": "peer_statement",
            },
        ]
        llm = SequencedContextLlm([
            '{"decision":"APPROVE","reason":"只询问仍缺少的信息",'
            '"rewrittenDraft":"","searchQueries":[],"checks":{'
            '"contextCoherent":true,"personaAligned":true,"speakerConsistent":true,'
            '"worldKnowledgeConsistent":true,"naturalConversation":true,'
            '"answersLatestMessage":true,"currentStateGrounded":true},'
            '"unsupportedPersonalClaims":[],"entityConflicts":[]}'
        ])
        agent = ContextReviewAgent(ToolRegistry(), llm_client=llm, search_client=StubSearchClient())

        result = await agent.run(context, "review_context")

        self.assertEqual("家里能支持你读到博士吗", result.structured_result["reviewedDraft"])
        self.assertTrue(result.structured_result["contextRewritten"])
        self.assertNotIn(
            "[候选回复，代表我]\n你多少分 哪个省的",
            llm.calls[0]["user"],
        )

    async def test_should_not_approve_when_entire_candidate_repeats_answered_question(self) -> None:
        """整条草稿都是已回答问题时，即使审查模型误报 APPROVE 也不得原样发送。"""
        context = build_context("你多少分 哪个省的", review_mode="STRICT_HANDOFF")
        context.event.text = "物化生"
        context.history_context = [
            {
                "role": "self",
                "text": "你多少分 哪个省的",
                "messageOrigin": "AGENT_AUTO",
                "factAuthority": "agent_output",
            },
            {
                "role": "peer",
                "text": "600 安徽",
                "messageOrigin": "EXTERNAL",
                "factAuthority": "peer_statement",
            },
        ]
        llm = SequencedContextLlm([
            '{"decision":"APPROVE","reason":"误判为可发送",'
            '"rewrittenDraft":"","searchQueries":[],"checks":{'
            '"contextCoherent":true,"personaAligned":true,"speakerConsistent":true,'
            '"worldKnowledgeConsistent":true,"naturalConversation":true,'
            '"answersLatestMessage":true,"currentStateGrounded":true,'
            '"doesNotRepeatAnsweredQuestion":true},'
            '"unsupportedPersonalClaims":[],"entityConflicts":[]}'
        ])
        agent = ContextReviewAgent(ToolRegistry(), llm_client=llm, search_client=StubSearchClient())

        result = await agent.run(context, "review_context")

        self.assertEqual("needs_human", result.status)
        self.assertTrue(result.structured_result["handoffRequired"])
        self.assertIn("doesNotRepeatAnsweredQuestion", result.structured_result["handoffReason"])


class PublicKnowledgeSearchClientTest(unittest.TestCase):
    def test_sanitize_query_removes_private_identifiers_and_long_chat_content(self) -> None:
        """外部查询必须删除联系方式、链接与长数字，并限制长度。"""
        query = (
            "珂莱塔 属于哪个游戏 电话13812345678 邮箱foo@example.com "
            "https://private.example/chat QQ 2597164807 " + "补充" * 40
        )

        sanitized = PublicKnowledgeSearchClient.sanitize_query(query)

        self.assertIn("珂莱塔", sanitized)
        self.assertNotIn("13812345678", sanitized)
        self.assertNotIn("foo@example.com", sanitized)
        self.assertNotIn("private.example", sanitized)
        self.assertNotIn("2597164807", sanitized)
        self.assertLessEqual(len(sanitized), 60)
