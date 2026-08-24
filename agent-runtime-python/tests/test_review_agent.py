from __future__ import annotations

import unittest

from app.agents.review_agent import ReviewAgent
from app.schemas.events import Sender, UnifiedEvent
from app.schemas.memories import VerifiedMemory
from app.schemas.tasks import AgentTaskContext
from app.tools.registry import ToolRegistry


class ReviewLlmStub:
    """模拟只返回严格 JSON 的审批模型。"""

    def is_enabled(self, model_profile=None):
        """测试环境始终视为模型已启用。"""
        return True

    async def generate_reply(self, system_prompt, user_message, temperature=0.0, model_profile=None, *, fast=False):
        """返回审批通过结果，具体越界判断由 ReviewAgent 的确定性规则完成。"""
        return '{"decision":"APPROVE","reason":"supported"}'


class CapturingReviewLlmStub(ReviewLlmStub):
    """记录审查提示，验证用户设定中的明确事实确实被声明为可用依据。"""

    def __init__(self) -> None:
        self.system_prompt = ""
        self.user_message = ""

    async def generate_reply(self, system_prompt, user_message, temperature=0.0, model_profile=None, *, fast=False):
        """保存本次调用参数后返回通过，避免测试依赖真实模型。"""
        self.system_prompt = system_prompt
        self.user_message = user_message
        return await super().generate_reply(system_prompt, user_message, temperature, model_profile)


class HandoffReviewLlmStub(ReviewLlmStub):
    """模拟缺少来源证据时的审查结论。"""

    async def generate_reply(self, system_prompt, user_message, temperature=0.0, model_profile=None, *, fast=False):
        return '{"decision":"HANDOFF","reason":"missing source evidence"}'


class RewriteReviewLlmStub(ReviewLlmStub):
    """首次调用返回纠偏文本，第二次调用返回审批通过。"""

    def __init__(self) -> None:
        self.call_count = 0

    async def generate_reply(self, system_prompt, user_message, temperature=0.0, model_profile=None, *, fast=False):
        """按调用顺序模拟纠偏和复审，避免测试依赖提示词的具体措辞。"""
        self.call_count += 1
        if self.call_count == 1:
            return '{"decision":"REWRITE","reason":"remove unsupported detail","rewrittenDraft":"\u4f60\u662f\u60f3\u52a0\u5fae\u4fe1\u5417\uff1f"}'
        return await super().generate_reply(system_prompt, user_message, temperature, model_profile)


class HandoffThenRewriteLlmStub(ReviewLlmStub):
    """模拟审查要求接管后，由纠偏模型改写并通过复审的完整链路。"""

    def __init__(self) -> None:
        self.call_count = 0

    async def generate_reply(self, system_prompt, user_message, temperature=0.0, model_profile=None, *, fast=False):
        """依次返回 HANDOFF、纠偏结果和 APPROVE。"""
        self.call_count += 1
        if self.call_count == 1:
            return '{"decision":"HANDOFF","reason":"missing source evidence"}'
        if self.call_count == 2:
            return '{"rewrittenDraft":"能说一下具体的吗"}'
        return await super().generate_reply(system_prompt, user_message, temperature, model_profile)


class AlwaysRejectRewritesLlmStub(ReviewLlmStub):
    """模拟每次复审都拒绝，用于验证三次纠偏后自动发送最后一个版本。"""

    def __init__(self) -> None:
        self.rewrite_count = 0
        self.review_count = 0

    async def generate_reply(self, system_prompt, user_message, temperature=0.0, model_profile=None, *, fast=False):
        """纠偏调用返回递增草稿，审批调用始终拒绝。"""
        if "自动回复纠偏 Agent" in system_prompt:
            self.rewrite_count += 1
            return '{"rewrittenDraft":"纠偏版本' + str(self.rewrite_count) + '"}'
        self.review_count += 1
        return '{"decision":"HANDOFF","reason":"仍缺少依据"}'


class DelegatedRewriteLlmStub(ReviewLlmStub):
    """模拟主控台委托先安全改写、再通过最终复审。"""

    async def generate_reply(self, system_prompt, user_message, temperature=0.0, model_profile=None, *, fast=False):
        """纠偏阶段删除无依据的私人状态，复审阶段批准任务内协商问句。"""
        if "自动回复纠偏 Agent" in system_prompt:
            return '{"rewrittenDraft":"下午具体几点方便"}'
        return '{"decision":"APPROVE","reason":"属于委托范围内的时间协商"}'


def build_context(candidate: str, prompt: str = "") -> AgentTaskContext:
    """构造包含 SocialAgent 候选回复的最小审批上下文。"""
    event = UnifiedEvent(
        eventId="review-case",
        platform="qq",
        scene="social",
        eventType="message",
        chatType="private",
        chatId="10001",
        sender=Sender(id="10001", name="peer"),
        text="\u5fae\u4fe1\u53f7\u5462",
        timestamp="2026-07-11T23:30:00+08:00",
        rawPayload={},
    )
    return AgentTaskContext(
        task_id="review-task",
        route="social_reply",
        event=event,
        metadata={
            "conversation_profile_match": {"profile": {"systemPrompt": prompt}},
            "previous_results": {"social": {"draft": candidate}},
        },
    )


class ReviewAgentTest(unittest.IsolatedAsyncioTestCase):
    async def test_active_delegated_task_auto_rewrites_instead_of_handoff(self):
        """活动委托应自动纠偏后继续发送，不能因 Profile 缺省为严格模式而卡在接管页。"""
        context = build_context("下午我有空")
        context.event.text = "下午方便吗"
        context.metadata["delegated_task"] = {
            "status": "ACTIVE",
            "targetName": "km",
            "objective": "预约明天家教时间",
            "successCriteria": "双方确认明确时间",
        }
        context.metadata["delegated_task_action"] = {
            "action": "SEND_MESSAGE",
            "reason": "联系人提出新的时间条件",
            "messageInstruction": "回应最新反问并继续确认时间",
        }
        context.metadata["previous_results"]["context_review"] = {
            "contextDecision": "HANDOFF",
            "handoffRequired": True,
            "handoffReason": "不能确定账号主人下午是否有空",
        }

        result = await ReviewAgent(ToolRegistry(), llm_client=DelegatedRewriteLlmStub()).run(
            context,
            "review_reply",
        )

        self.assertFalse(result.need_confirmation)
        self.assertEqual("APPROVE", result.structured_result["reviewDecision"])
        self.assertEqual("下午具体几点方便", result.structured_result["approvedDraft"])

    def test_main_console_approval_does_not_apply_profile_character_limit(self):
        """主控台委托通过审批时保留完整文本，不套用设定集的单条字符上限。"""
        candidate = "这件事我已经和对方确认清楚了，明天晚上七点按约定进行"
        context = build_context(candidate)
        context.metadata["delegated_task"] = {"status": "ACTIVE", "objective": "确认预约时间"}
        context.metadata["conversation_profile_match"]["profile"].update(
            {
                "maxReplyChars": 8,
                "splitLongReply": False,
                "splitReplyChancePercent": 100,
            }
        )

        result = ReviewAgent(ToolRegistry())._approve(
            context,
            {"draft": candidate},
            candidate,
            "测试通过",
        )

        self.assertEqual([candidate], result.structured_result["messageParts"])

    async def test_should_share_active_delegated_task_with_review_agent(self):
        """审批层必须收到委托契约，但不能把工作台命令当成联系人发言。"""
        evidence = ReviewAgent._append_delegated_task_evidence(
            "会话基础约束",
            {
                "status": "ACTIVE",
                "targetName": "km",
                "objective": "预约明天下午的课程",
                "successCriteria": "对方明确确认课程时间",
                "deadlineText": "明天",
                "originalCommand": "帮我和km约一下明天下午的课程",
            },
        )

        self.assertIn("账号主人明确授权的委托契约", evidence)
        self.assertIn("任务目标：预约明天下午的课程", evidence)
        self.assertIn("原始控制指令不是对方发言", evidence)
        self.assertIn("不授权付款、隐私披露、跨会话操作", evidence)

        action_evidence = ReviewAgent._append_delegated_task_evidence(
            evidence,
            {
                "status": "ACTIVE",
                "objective": "预约明天下午的课程",
            },
            {
                "action": "SEND_MESSAGE",
                "reason": "联系人提出新的时间条件",
                "messageInstruction": "回应最新消息并继续协商",
            },
        )
        self.assertIn("任务图本轮受控动作", action_evidence)
        self.assertIn("允许动作：SEND_MESSAGE", action_evidence)

    async def test_prefers_context_review_rewrite_over_original_social_draft(self):
        """最终审批必须审查情景层改写后的草稿，不能继续发送原始错误内容。"""
        context = build_context("我刚抽到两个钟离")
        context.metadata["previous_results"]["context_review"] = {
            "contextDecision": "REWRITE",
            "reviewedDraft": "你说的是哪个角色",
            "publicKnowledge": [],
        }

        result = await ReviewAgent(ToolRegistry(), llm_client=ReviewLlmStub()).run(
            context,
            "review_reply",
        )

        self.assertEqual(result.structured_result["reviewDecision"], "APPROVE")
        self.assertEqual(result.structured_result["approvedDraft"], "你说的是哪个角色")
        self.assertTrue(result.structured_result["rewritten"])

    async def test_final_review_cannot_expand_sticker_reaction_into_new_topic(self):
        """纯表情模式经过情景改写后仍只能发送一个轻回应，不能追问图片出处。"""
        context = build_context("哈哈")
        context.event.text = "[动画表情]"
        context.metadata["previous_results"]["social"] = {
            "draft": "哈哈",
            "reactionOnly": True,
            "mediaAnalysis": [],
        }
        context.metadata["previous_results"]["context_review"] = {
            "contextDecision": "REWRITE",
            "reviewedDraft": "这个表情好有梗是哪部番哪个模组二创作者做的呀",
            "publicKnowledge": [],
        }

        result = await ReviewAgent(ToolRegistry(), llm_client=ReviewLlmStub()).run(
            context,
            "review_reply",
        )

        self.assertEqual(result.structured_result["approvedDraft"], "哈哈")
        self.assertEqual(result.structured_result["messageParts"], ["哈哈"])

    async def test_auto_rewrite_corrects_context_review_handoff_instead_of_escalating(self):
        """自动纠偏模式收到旧版情景接管结果时也必须改写复审，而不是通知用户接管。"""
        context = build_context("随便回一句")
        context.metadata["conversation_profile_match"]["profile"]["reviewMode"] = "AUTO_REWRITE"
        context.metadata["previous_results"]["context_review"] = {
            "contextDecision": "HANDOFF",
            "handoffRequired": True,
            "handoffReason": "缺少账号主人的真实抽卡状态",
        }

        result = await ReviewAgent(ToolRegistry(), llm_client=RewriteReviewLlmStub()).run(
            context,
            "review_reply",
        )

        self.assertFalse(result.need_confirmation)
        self.assertEqual(result.structured_result["reviewDecision"], "APPROVE")
        self.assertEqual(result.structured_result["autoRewriteAttempts"], 1)

    async def test_requires_review_model_even_when_prompt_allows_payment(self):
        """提示词允许支付方式不等于跳过审查；模型不可用时必须接管。"""
        context = build_context("\u884c\uff0c\u600e\u4e48\u4ed8", "\u786e\u5b9a\u8d2d\u4e70\u540e\u8ba9\u5bf9\u65b9\u5fae\u4fe1\u4ed8\u6b3e")
        context.metadata["previous_results"]["social"] = {
            "handoffRequired": True,
            "proposedDraft": "\u884c\uff0c\u600e\u4e48\u4ed8",
        }
        result = await ReviewAgent(ToolRegistry()).run(context, "review_reply")

        self.assertEqual(result.structured_result["reviewDecision"], "HANDOFF")

    async def test_requires_handoff_for_contact_exchange_even_when_payment_is_authorized(self):
        """支付方式授权不等于授权代理索要微信号或新增好友。"""
        context = build_context("\u884c\uff0c\u5fae\u4fe1\u53f7\u591a\u5c11", "\u786e\u5b9a\u8d2d\u4e70\u540e\u8ba9\u5bf9\u65b9\u5fae\u4fe1\u4ed8\u6b3e")
        agent = ReviewAgent(ToolRegistry(), llm_client=HandoffReviewLlmStub())
        result = await agent.run(context, "review_reply")

        self.assertEqual(result.structured_result["reviewDecision"], "HANDOFF")
        self.assertEqual(result.structured_result["handoffReason"], "missing source evidence")

    async def test_auto_rewrite_replaces_out_of_scope_reply(self):
        """无需人工审批时，越界候选应先被纠偏，再通过复审。"""
        context = build_context("\u52a0\u6211\u5fae\u4fe1\u5427\u65b9\u4fbf\u804a")
        context.metadata["conversation_profile_match"]["profile"]["reviewMode"] = "AUTO_REWRITE"
        context.metadata["resolved_model_profile"] = {
            "profile": {
                "id": "review-model",
                "userId": "u1",
                "name": "review",
                "baseUrl": "http://local",
                "apiKey": "test",
                "model": "test-model",
            }
        }
        agent = ReviewAgent(ToolRegistry(), llm_client=RewriteReviewLlmStub())
        result = await agent.run(context, "review_reply")

        self.assertEqual(result.structured_result["reviewDecision"], "APPROVE", result.structured_result)
        self.assertEqual(
            result.structured_result["approvedDraft"],
            "\u4f60\u662f\u60f3\u52a0\u5fae\u4fe1\u5417\uff1f",
        )
        self.assertEqual(result.structured_result["messageParts"], ["\u4f60\u662f\u60f3\u52a0\u5fae\u4fe1\u5417\uff1f"])
        self.assertNotIn("\u6211\u5148\u95ee", result.structured_result["approvedDraft"])
        self.assertTrue(result.structured_result["rewritten"])

    async def test_auto_rewrite_sends_only_when_rewrite_passes_second_review(self):
        """AUTO_REWRITE 必须在改写并复审通过后才发送，不能依赖固定兜底文本。"""
        context = build_context("我的微信是一个不存在的账号")
        context.metadata["conversation_profile_match"]["profile"]["reviewMode"] = "AUTO_REWRITE"
        context.metadata["resolved_model_profile"] = {"profile": {"apiKey": "test", "model": "test-model"}}

        result = await ReviewAgent(ToolRegistry(), llm_client=HandoffThenRewriteLlmStub()).run(
            context, "review_reply"
        )

        self.assertFalse(result.need_confirmation)
        self.assertEqual(result.structured_result["reviewDecision"], "APPROVE")
        self.assertEqual(result.structured_result["approvedDraft"], "能说一下具体的吗")
        self.assertTrue(result.structured_result["autoRewriteApplied"])

    async def test_auto_rewrite_handoffs_after_three_failed_reviews(self):
        """连续三次纠偏仍未通过时转为人工确认，不自动发送（审查不能跳过）。"""
        context = build_context("今天有点累 明晚见")
        context.metadata["conversation_profile_match"]["profile"]["reviewMode"] = "AUTO_REWRITE"
        context.metadata["resolved_model_profile"] = {
            "profile": {"apiKey": "test", "model": "test-model"}
        }
        llm = AlwaysRejectRewritesLlmStub()

        result = await ReviewAgent(ToolRegistry(), llm_client=llm).run(context, "review_reply")

        self.assertTrue(result.need_confirmation)
        self.assertEqual("HANDOFF", result.structured_result["reviewDecision"])
        # 最后一次改写稿作为待确认草稿交给人工，不自动发送
        self.assertEqual("纠偏版本3", result.structured_result.get("proposedDraft"))
        self.assertEqual(3, llm.rewrite_count)

    async def test_auto_rewrite_handoffs_when_review_model_is_unavailable(self):
        """AUTO_REWRITE 的审查模型不可用时必须停止发送，不能伪造兜底回复。"""
        context = build_context("候选回复")
        context.metadata["conversation_profile_match"]["profile"]["reviewMode"] = "AUTO_REWRITE"

        result = await ReviewAgent(ToolRegistry()).run(context, "review_reply")

        self.assertTrue(result.need_confirmation)
        self.assertEqual(result.structured_result["reviewDecision"], "HANDOFF")
        self.assertNotIn("这个我先确认一下", str(result.structured_result))

    def test_formats_history_with_explicit_speakers(self):
        """审批提示和接管卡片必须区分账号主人与联系人，避免对话双方身份颠倒。"""
        history = [
            {"role": "self", "text": "我刚打完一局"},
            {"role": "peer", "text": "你又来啦"},
        ]

        formatted = ReviewAgent._format_history_for_review(history)

        self.assertEqual(formatted, "我：我刚打完一局\n对方：你又来啦")

    async def test_rejects_when_review_model_is_unavailable(self):
        """审批模型不可用时必须闭锁，不能默认放行。"""
        agent = ReviewAgent(ToolRegistry())
        result = await agent.run(build_context("ok"), "review_reply")
        self.assertTrue(result.need_confirmation)
        self.assertIn("\u5ba1\u6279\u6a21\u578b\u4e0d\u53ef\u7528", result.structured_result["handoffReason"])

    async def test_rejects_unapproved_platform_transfer(self):
        """提示词未授权时，不允许自行引导对方切换平台。"""
        agent = ReviewAgent(ToolRegistry(), llm_client=HandoffReviewLlmStub())
        result = await agent.run(build_context("\u52a0\u6211\u5fae\u4fe1\u5427\u65b9\u4fbf\u804a"), "review_reply")
        self.assertTrue(result.need_confirmation)
        self.assertEqual(result.structured_result["handoffReason"], "missing source evidence")

    async def test_rejects_invented_wechat_handle(self):
        """拒绝发送证据中不存在的账号或联系方式。"""
        agent = ReviewAgent(ToolRegistry(), llm_client=HandoffReviewLlmStub())
        result = await agent.run(build_context("\u6211\u7684\u5fae\u4fe1\u662f NetEaseMusicVIP88"), "review_reply")
        self.assertTrue(result.need_confirmation)
        self.assertEqual(result.structured_result["handoffReason"], "missing source evidence")

    async def test_allows_handle_explicitly_authorized_by_prompt(self):
        """提示词明确给出的账号可以通过确定性审查。"""
        agent = ReviewAgent(ToolRegistry(), llm_client=ReviewLlmStub())
        result = await agent.run(
            build_context(
                "\u6211\u7684\u5fae\u4fe1\u662f real_account_88",
                "\u53ef\u4ee5\u63d0\u4f9b\u5fae\u4fe1\u53f7 real_account_88",
            ),
            "review_reply",
        )
        self.assertEqual(result.structured_result["reviewDecision"], "APPROVE")

    async def test_treats_explicit_profile_facts_as_review_evidence(self):
        """会话设定明确写出的商品与价格必须作为审查依据，不能被误判成只有风格作用。"""
        llm = CapturingReviewLlmStub()
        agent = ReviewAgent(ToolRegistry(), llm_client=llm)
        context = build_context("还卖，一个月15", "当前出售网易云会员，一个月15")

        result = await agent.run(context, "review_reply")

        self.assertEqual(result.structured_result["reviewDecision"], "APPROVE")
        self.assertIn("明确写出的事实属于用户授权依据", llm.system_prompt)
        self.assertIn("当前出售网易云会员，一个月15", llm.user_message)
        self.assertIn("只自然询问一项缺失信息", llm.system_prompt)

    def test_should_share_verified_memory_evidence_with_review_agent(self) -> None:
        """审查层必须拿到与生成层一致的已确认事实，避免合法记忆因证据缺失被误拦截。"""
        memory = VerifiedMemory(
            id="memory-review-001",
            subject="商品",
            predicate="月费",
            value="15 元",
            scopeType="CONVERSATION",
            sourceEventIds=["owner-event-002"],
            sourceActorType="OWNER",
            factAuthority="human_self",
            status="VERIFIED",
        )

        evidence = ReviewAgent._append_verified_memory_evidence("授权提示", [memory])

        self.assertIn("[用户已确认的长期记忆证据]", evidence)
        self.assertIn("[memory:memory-review-001] 商品 / 月费 / 15 元", evidence)
        self.assertIn("不能推导或补造", evidence)
