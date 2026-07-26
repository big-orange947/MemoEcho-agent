from __future__ import annotations

import unittest

from app.agents.social_agent import SocialAgent
from app.schemas.events import Sender, UnifiedEvent
from app.schemas.memories import VerifiedMemory
from app.schemas.model_profiles import ResolvedUserModelProfile
from app.schemas.skills import SkillDescriptor
from app.schemas.tasks import AgentTaskContext
from app.tools.registry import ToolRegistry


class DummyLlmClient:
    def __init__(self, enabled: bool = True, reply_text: str = "模型回复", should_fail: bool = False) -> None:
        # 这个构造函数的作用是模拟大模型客户端的三种状态：可用、不可用和调用失败。
        self.enabled = enabled
        self.reply_text = reply_text
        self.should_fail = should_fail
        self.calls: list[dict] = []

    def is_enabled(self, model_profile: ResolvedUserModelProfile | None = None) -> bool:
        # 这个函数的作用是向 SocialAgent 暴露当前测试用模型客户端是否启用。
        return self.enabled

    async def generate_reply(
        self,
        system_prompt: str,
        user_message: str,
        temperature: float = 0.7,
        model_profile: ResolvedUserModelProfile | None = None,
    ) -> str:
        # 这个函数的作用是记录传入的大模型提示词、用户消息和模型配置，并按测试需要返回结果或抛错。
        self.calls.append(
            {
                "system_prompt": system_prompt,
                "user_message": user_message,
                "temperature": temperature,
                "model_profile": model_profile,
            }
        )
        if self.should_fail:
            raise RuntimeError("mock llm failure")
        return self.reply_text


class SocialAgentTest(unittest.IsolatedAsyncioTestCase):
    async def test_should_generate_warm_reply_from_profile_prompt(self) -> None:
        # 这个测试函数的作用是验证命中温和型设定集后，社交回复草稿会带出更柔和的语气。
        agent = SocialAgent(ToolRegistry())
        event = UnifiedEvent(
            eventId="qq:message:private:social-001",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="2597164807",
            sender=Sender(id="10001", name="alice", role=None),
            text="在吗",
            attachments=[],
            mentions=[],
            timestamp="2026-07-09T13:30:00+08:00",
            rawPayload={},
        )
        context = AgentTaskContext(
            task_id="social-run-001",
            route="social_reply",
            event=event,
            allowed_tools=[],
            metadata={
                "conversation_profile_match": {
                    "matched": True,
                    "active": True,
                    "profile": {
                        "personaMode": "PROMPT",
                        "systemPrompt": "你要温柔、亲切、简洁地回应对方。",
                        "replyMode": "DRAFT_ONLY",
                        "preferredRoute": "social_reply",
                        "requireHumanConfirmation": False,
                    },
                }
            },
        )

        result = await agent.run(context, "draft_reply")

        self.assertEqual(result.agent, "social")
        self.assertEqual("我在你说", result.reply_draft)
        self.assertEqual(result.structured_result["replyMode"], "DRAFT_ONLY")
        self.assertIn("warm", result.structured_result["styleTags"])
        self.assertIn("[会话人格与已授权事实]", result.structured_result["effectiveSystemPrompt"])
        self.assertEqual(result.structured_result["promptSource"], "profile_prompt_only")
        self.assertFalse(result.structured_result["llmUsed"])

    async def test_should_generate_urgent_professional_reply(self) -> None:
        # 这个测试函数的作用是验证命中专业型设定集后，紧急消息会得到更偏工作化的回应。
        agent = SocialAgent(ToolRegistry())
        event = UnifiedEvent(
            eventId="qq:message:private:social-002",
            platform="qq",
            scene="work",
            eventType="message",
            chatType="private",
            chatId="2597164807",
            sender=Sender(id="10002", name="bob", role=None),
            text="这个事情很紧急，麻烦马上处理",
            attachments=[],
            mentions=[],
            timestamp="2026-07-09T13:35:00+08:00",
            rawPayload={},
        )
        context = AgentTaskContext(
            task_id="social-run-002",
            route="social_reply",
            event=event,
            allowed_tools=[],
            metadata={
                "conversation_profile_match": {
                    "matched": True,
                    "active": True,
                    "profile": {
                        "personaMode": "SKILL",
                        "systemPrompt": "你要冷静、专业、可靠地回应对方。",
                        "skillReference": "github://demo/style/professional",
                        "replyMode": "AUTO_REPLY",
                        "preferredRoute": "social_reply",
                        "requireHumanConfirmation": True,
                    },
                }
            },
        )

        result = await agent.run(context, "draft_reply")

        self.assertEqual("知道了尽快", result.reply_draft)
        self.assertTrue(result.need_confirmation)
        self.assertEqual(result.structured_result["skillReference"], "github://demo/style/professional")
        self.assertIn("professional", result.structured_result["styleTags"])
        self.assertIn("[Skill 专业方法参考]", result.structured_result["effectiveSystemPrompt"])
        self.assertEqual(result.structured_result["promptSource"], "skill_plus_profile_prompt")

    async def test_should_include_current_vision_result_in_social_reply_prompt(self) -> None:
        """验证实时图片识别结果会注入当前回复提示词，而不是只在后台留下元数据。"""
        llm_client = DummyLlmClient(reply_text="那我明天下午三点过去")
        agent = SocialAgent(ToolRegistry(), llm_client=llm_client)
        event = UnifiedEvent(
            eventId="qq:message:private:social-image-001",
            platform="qq",
            scene="social",
            eventType="message",
            chatType="private",
            chatId="10002",
            sender=Sender(id="10002", name="bob", role=None),
            text="[图片]",
            attachments=[],
            mentions=[],
            timestamp="2026-07-13T10:00:00+08:00",
            rawPayload={},
        )
        context = AgentTaskContext(
            task_id="social-image-001",
            route="social_reply",
            event=event,
            allowed_tools=[],
            metadata={
                "resolved_model_profile": ResolvedUserModelProfile(
                    id="model-1", userId="freeze", name="vision", apiKey="key", model="vision-model"
                ).model_dump(by_alias=True),
                "current_media_analysis": [
                    {
                        "fileName": "notice.png",
                        "status": "VISION_ANALYZED",
                        "summary": "图片内容已由视觉模型识别",
                        "extractedText": "图片里写着会议改到明天下午三点",
                    }
                ],
            },
        )

        result = await agent.run(context, "draft_reply")

        self.assertTrue(result.structured_result["llmUsed"])
        self.assertIn("会议改到明天下午三点", llm_client.calls[0]["system_prompt"])
        self.assertEqual("VISION_ANALYZED", result.structured_result["mediaAnalysis"][0]["status"])
        self.assertFalse(result.structured_result["reactionOnly"])

    async def test_should_only_send_light_reaction_for_sticker_without_starting_topic(self) -> None:
        """验证纯动画表情只产生一个轻回应，模型追问出处时也不会直接发送。"""
        llm_client = DummyLlmClient(reply_text="这个表情好有梗是哪部番哪个模组二创作者做的呀")
        agent = SocialAgent(ToolRegistry(), llm_client=llm_client)
        event = UnifiedEvent(
            eventId="qq:message:private:social-sticker-001",
            platform="qq",
            scene="social",
            eventType="message",
            chatType="private",
            chatId="10002",
            sender=Sender(id="10002", name="bob", role=None),
            text="[动画表情]",
            attachments=[],
            mentions=[],
            timestamp="2026-07-13T20:18:00+08:00",
            rawPayload={"message": [{"type": "mface", "data": {"summary": "[动画表情]"}}]},
        )
        context = AgentTaskContext(
            task_id="social-sticker-001",
            route="social_reply",
            event=event,
            allowed_tools=[],
            metadata={},
        )

        result = await agent.run(context, "draft_reply")

        self.assertTrue(result.structured_result["reactionOnly"])
        self.assertEqual(["哈哈"], result.structured_result["messageParts"])
        self.assertNotIn("哪", result.reply_draft)
        self.assertIn("纯表情轻回应", llm_client.calls[0]["system_prompt"])

    async def test_should_handoff_when_current_image_has_no_vision_result(self) -> None:
        """验证未成功识别的图片不会被模型猜测性回复，而是进入人工接管。"""
        agent = SocialAgent(ToolRegistry(), DummyLlmClient())
        event = UnifiedEvent(
            eventId="qq:message:private:social-image-unavailable",
            platform="qq",
            scene="social",
            eventType="message",
            chatType="private",
            chatId="10002",
            sender=Sender(id="10002", name="bob", role=None),
            text="[图片]",
            attachments=[{"fileType": "image", "fileName": "unknown.png"}],
            mentions=[],
            timestamp="2026-07-13T10:00:00+08:00",
            rawPayload={},
        )
        context = AgentTaskContext(
            task_id="social-image-unavailable",
            route="social_reply",
            event=event,
            allowed_tools=[],
            metadata={
                "current_media_analysis": [
                    {"status": "VISION_UNAVAILABLE", "summary": "图片已收到，但没有可用的视觉模型"}
                ]
            },
        )

        result = await agent.run(context, "draft_reply")

        self.assertTrue(result.need_confirmation)
        self.assertTrue(result.structured_result["handoffRequired"])
        self.assertIn("视觉模型", result.structured_result["handoffReason"])

    async def test_should_apply_sharp_fallback_without_assistant_tone(self) -> None:
        # 这个测试函数的作用是验证未配置模型时，毒舌提示至少不会退回“记录、跟进”等任务助手话术。
        agent = SocialAgent(ToolRegistry())
        style_tags = agent._detect_style_tags("你要毒舌、直接地怼人。")

        reply = agent._build_rule_based_reply("你个二货", style_tags)

        self.assertIn("sharp", style_tags)
        self.assertEqual("你先照照镜子再说。", reply)
        self.assertNotIn("记下", reply)
        self.assertNotIn("跟进", reply)

    async def test_should_use_llm_when_client_is_available(self) -> None:
        # 这个测试函数的作用是验证一旦配置了可用的大模型客户端，SocialAgent 会优先走模型回复链路。
        llm_client = DummyLlmClient(enabled=True, reply_text="这是模型生成的回复草稿。")
        agent = SocialAgent(ToolRegistry(), llm_client=llm_client)
        event = UnifiedEvent(
            eventId="qq:message:private:social-003",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="2597164807",
            sender=Sender(id="10003", name="charlie", role=None),
            text="今天晚上有空吗？",
            attachments=[],
            mentions=[],
            timestamp="2026-07-09T13:40:00+08:00",
            rawPayload={},
        )
        context = AgentTaskContext(
            task_id="social-run-003",
            route="social_reply",
            event=event,
            allowed_tools=[],
            metadata={
                "conversation_profile_match": {
                    "matched": True,
                    "active": True,
                    "profile": {
                        "personaMode": "SKILL",
                        "systemPrompt": "你要自然、简洁地回应对方。",
                        "skillReferences": ["skills/personas/reliable-assistant"],
                        "replyMode": "AUTO_REPLY",
                    },
                },
                "resolved_skills": [
                    {
                        "id": "persona.reliable_assistant",
                        "name": "可靠助理人格",
                        "version": "1.0.0",
                        "type": "persona",
                        "description": "适合私聊回复",
                        "source": "local",
                        "rawReference": "skills/personas/reliable-assistant",
                        "applicableRoutes": ["social_reply"],
                        "promptFragments": {
                            "system": "回复时保持冷静、可靠、克制，优先给出明确答复和下一步。"
                        },
                        "toolPolicy": {
                            "allow": ["send_qq_message"]
                        },
                        "modelHints": {
                            "temperature": 0.4,
                            "maxTokens": 512
                        }
                    }
                ],
                "resolved_model_profile": {
                    "matched": True,
                    "reason": "命中用户默认模型配置",
                    "profile": {
                        "id": "model-profile-001",
                        "userId": "freeze",
                        "name": "默认社交模型",
                        "provider": "OPENAI_COMPATIBLE",
                        "baseUrl": "https://api.openai.com/v1",
                        "apiKey": "sk-demo-001",
                        "model": "gpt-4o-mini",
                        "temperature": 0.4,
                        "maxTokens": 1024,
                        "supportedRoutes": ["social_reply"],
                        "isDefault": True,
                        "priority": 10,
                    },
                },
            },
        )

        result = await agent.run(context, "draft_reply")

        self.assertEqual(result.reply_draft, "这是模型生成的回复草稿")
        self.assertTrue(result.structured_result["llmUsed"])
        self.assertTrue(result.structured_result["llmEnabled"])
        self.assertEqual(len(llm_client.calls), 1)
        self.assertIn("[Skill 专业方法参考]", llm_client.calls[0]["system_prompt"])
        self.assertIn("回复时保持冷静、可靠、克制", llm_client.calls[0]["system_prompt"])
        self.assertIn("不是可以直接照搬的聊天文案", llm_client.calls[0]["system_prompt"])
        self.assertIn("每次最多追问一个", llm_client.calls[0]["system_prompt"])
        self.assertIsNotNone(llm_client.calls[0]["model_profile"])
        self.assertEqual(llm_client.calls[0]["model_profile"].model, "gpt-4o-mini")
        self.assertEqual(result.structured_result["resolvedModelProfile"]["userId"], "freeze")
        self.assertEqual(result.structured_result["promptSource"], "skill_plus_profile_prompt")

    async def test_should_keep_persona_facts_private_and_prioritize_history(self) -> None:
        """验证学校和爱好等人格字段只能影响表达，不能被模型当成对外可说的事实。"""
        llm_client = DummyLlmClient(reply_text="没事，晚点再说")
        agent = SocialAgent(ToolRegistry(), llm_client=llm_client)
        event = UnifiedEvent(
            eventId="qq:message:private:persona-private-001",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="10005",
            sender=Sender(id="10005", name="eve", role=None),
            text="你昨天说什么来着",
            attachments=[],
            mentions=[],
            timestamp="2026-07-13T16:00:00+08:00",
            rawPayload={},
        )
        context = AgentTaskContext(
            task_id="persona-private-001",
            route="social_reply",
            event=event,
            allowed_tools=[],
            history_context=[
                {"role": "self", "senderName": "我", "text": "昨晚说今天要去图书馆"},
            ],
            metadata={
                "conversation_profile_match": {
                    "matched": True,
                    "active": True,
                    "profile": {
                        "personaMode": "PROMPT",
                        "systemPrompt": "你是西南大学软件工程学生，喜欢打篮球，说话自然。",
                        "replyMode": "DRAFT_ONLY",
                    },
                }
            },
        )

        await agent.run(context, "draft_reply")

        prompt = llm_client.calls[0]["system_prompt"]
        self.assertIn("不得因为人格设定主动说", prompt)
        self.assertIn("当前渠道是 QQ 即时私聊", prompt)
        self.assertIn("只回复当前最新消息", prompt)
        self.assertIn("不要输出动作、神态、语气、心理活动或舞台说明", prompt)
        self.assertIn("当前 user 消息由对方发送", prompt)
        self.assertIn("可以在当前话题确实相关时作为事实依据", prompt)
        self.assertIn("不要再次追问‘哪个平台’", prompt)
        self.assertIn("历史中的明确事实优先于人格设定", prompt)
        self.assertLess(prompt.rfind("不得因为人格设定主动说"), prompt.rfind("昨晚说今天要去图书馆"))

    async def test_should_merge_consecutive_peer_messages_into_one_answer_turn(self) -> None:
        """验证对方分多条回答问卷时会合并成一轮，Skill 不能再次询问已经给出的项目。"""
        llm_client = DummyLlmClient(reply_text="想学什么专业")
        agent = SocialAgent(ToolRegistry(), llm_client=llm_client)
        event = UnifiedEvent(
            eventId="qq:message:private:history-turn-001",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="10006",
            sender=Sender(id="10006", name="家长", role=None),
            text="家里正常工薪阶层",
            attachments=[],
            mentions=[],
            timestamp="2026-07-14T16:50:00+08:00",
            rawPayload={},
        )
        context = AgentTaskContext(
            task_id="history-turn-001",
            route="social_reply",
            event=event,
            allowed_tools=[],
            history_context=[
                {"role": "self", "text": "孩子多少分 哪个省 家里情况呢"},
                {"role": "peer", "senderName": "家长", "text": "孩子600分"},
                {"role": "peer", "senderName": "家长", "text": "安徽省"},
                {"role": "peer", "senderName": "家长", "text": "月收入1.5万"},
            ],
            metadata={},
        )

        await agent.run(context, "draft_reply")

        prompt = llm_client.calls[0]["system_prompt"]
        self.assertIn("对方连续补充：孩子600分 / 安徽省 / 月收入1.5万", prompt)
        self.assertIn("必须合并理解，不能只看最后一条", prompt)
        self.assertIn("已经回答过的分数、地区、身份、预算", prompt)

    async def test_should_treat_long_skill_as_method_instead_of_qq_output_template(self) -> None:
        """验证咨询型 Skill 的正文后始终追加 QQ 渠道边界，避免整段问卷直接发给联系人。"""
        agent = SocialAgent(ToolRegistry())
        resolved_skill = {
            "id": "admission.adviser",
            "name": "升学咨询",
            "promptFragments": {
                "system": "先完整询问分数、省份、家庭收入、专业和城市，再输出详细分析报告。"
            },
        }
        prompt = agent._build_effective_system_prompt(
            {
                "personaMode": "SKILL",
                "skillReferences": ["github://demo/admission"],
                "systemPrompt": "像熟人一样聊天",
            },
            [SkillDescriptor.model_validate(resolved_skill)],
        )

        self.assertIn("先完整询问分数、省份", prompt)
        self.assertIn("Skill 决定聊什么，QQ 渠道规则和会话人格决定怎么说", prompt)
        self.assertGreater(prompt.rfind("每次最多追问一个"), prompt.rfind("先完整询问分数、省份"))

    def test_should_scope_relaxed_reply_shape_to_main_console_tasks(self) -> None:
        """验证无固定长度规则只进入主控台委托提示词，设定集继续使用原配置。"""
        agent = SocialAgent(ToolRegistry())

        profile_prompt = agent._build_effective_system_prompt({}, [])
        main_console_prompt = agent._build_effective_system_prompt(
            {},
            [],
            main_console_mode=True,
        )

        self.assertIn("单条长度、分段开关和分段概率", profile_prompt)
        self.assertNotIn("不设固定字符上限", profile_prompt)
        self.assertIn("不设固定字符上限", main_console_prompt)
        self.assertIn("不要为了凑固定长度机械断句", main_console_prompt)

    async def test_should_keep_long_reply_complete_without_automatic_splitting(self) -> None:
        # 这个函数的作用是验证发送层不再按字符数或随机概率机械拆分模型回复。
        agent = SocialAgent(ToolRegistry())
        event_id = next(
            f"social-split-{index}"
            for index in range(100)
            if agent._should_split_reply(f"social-split-{index}")
        )

        message_parts = agent._build_chat_bubbles(
            "\u4f60\u5148\u522b\u6025\uff0c\u665a\u70b9\u6211\u548c\u4f60\u8bf4\u6e05\u695a\u8fd9\u4e8b\u6ca1\u90a3\u4e48\u590d\u6742\u5176\u5b9e\u4e0d\u7528\u62c5\u5fc3",
            event_id,
            main_console_mode=True,
        )

        self.assertEqual(
            message_parts,
            ["\u4f60\u5148\u522b\u6025\uff0c\u665a\u70b9\u6211\u548c\u4f60\u8bf4\u6e05\u695a\u8fd9\u4e8b\u6ca1\u90a3\u4e48\u590d\u6742\u5176\u5b9e\u4e0d\u7528\u62c5\u5fc3"],
        )

    async def test_should_normalize_ellipsis_without_automatic_splitting(self) -> None:
        """验证省略号只被规范为自然停顿，不会触发发送层自动拆分。"""
        agent = SocialAgent(ToolRegistry())

        message_parts = agent._build_chat_bubbles(
            "哈哈被你这么一说还真有点可爱...你加载完了吗",
            "social-semantic-split",
            max_reply_chars=18,
            main_console_mode=True,
        )

        self.assertEqual(message_parts, ["哈哈被你这么一说还真有点可爱，你加载完了吗"])

    async def test_should_split_only_when_model_explicitly_outputs_newlines(self) -> None:
        """验证模型主动用换行表达多条消息时，发送层会保留这些明确边界。"""
        agent = SocialAgent(ToolRegistry())

        message_parts = agent._build_chat_bubbles(
            "好的\n那明晚见",
            "social-explicit-newline",
            max_reply_chars=4,
            main_console_mode=True,
        )

        self.assertEqual(message_parts, ["好的", "那明晚见"])

    async def test_should_keep_complete_clause_when_no_safe_split_point_exists(self) -> None:
        """验证无标点长短语采用软上限，宁可稍长也不能按固定字符数切坏语义。"""
        agent = SocialAgent(ToolRegistry())
        reply = "这个功能加载完成以后就可以直接使用了"

        message_parts = agent._build_chat_bubbles(
            reply,
            "social-soft-limit",
            max_reply_chars=12,
        )

        self.assertEqual(message_parts, [reply])

    async def test_should_keep_short_reply_as_one_chat_bubble(self) -> None:
        # 这个函数的作用是验证短回复不会被无意义地拆分，保证正常聊天节奏。
        agent = SocialAgent(ToolRegistry())

        message_parts = agent._build_chat_bubbles("等会说", "social-short")

        self.assertEqual(message_parts, ["等会说"])

    async def test_should_remove_parenthesized_stage_directions(self) -> None:
        # 这个测试函数的作用是验证模型偶发输出的动作描写会在发送前被硬删除。
        agent = SocialAgent(ToolRegistry())

        message_parts = agent._build_chat_bubbles(
            "（挠头）你这“额”是想说啥呀～",
            "social-stage-direction",
        )

        self.assertEqual(message_parts, ["你这“额”是想说啥呀"])
        self.assertNotIn("挠头", message_parts[0])
        self.assertNotIn("（", message_parts[0])

    async def test_should_keep_parenthesized_facts_without_brackets(self) -> None:
        # 这个测试函数的作用是验证清理括号时不会把价格、期限等真实内容一并删除。
        agent = SocialAgent(ToolRegistry())

        message_parts = agent._build_chat_bubbles(
            "会员（一个月15）还卖吗？",
            "social-parenthesized-fact",
        )

        self.assertEqual(message_parts, ["会员一个月15还卖吗"])

    async def test_should_only_rarely_keep_short_reply_terminal_punctuation(self) -> None:
        """短私聊默认去除句末标点，仅在稳定的低概率抽样命中时保留原始标点。"""
        agent = SocialAgent(ToolRegistry())
        no_punctuation_event = next(
            f"social-terminal-off-{index}"
            for index in range(100)
            if not agent._should_keep_terminal_punctuation(f"social-terminal-off-{index}")
        )
        punctuation_event = next(
            f"social-terminal-on-{index}"
            for index in range(10000)
            if agent._should_keep_terminal_punctuation(f"social-terminal-on-{index}")
        )

        self.assertEqual(agent._build_chat_bubbles("等会说。", no_punctuation_event), ["等会说"])
        self.assertEqual(agent._build_chat_bubbles("等会说。", punctuation_event), ["等会说。"])

    async def test_should_keep_long_reply_in_one_complete_message(self) -> None:
        # 这个测试函数的作用是验证字符数配置不会截断或自动拆分完整的长文本回复。
        agent = SocialAgent(ToolRegistry())

        message_parts = agent._build_chat_bubbles(
            "我先确认一下，晚点给你答复。你别着急，事情能处理好！",
            "social-preserve-content",
            max_reply_chars=8,
            main_console_mode=True,
        )

        self.assertEqual(len(message_parts), 1)
        self.assertIn("事情能处理好", message_parts[0])
        self.assertTrue(all(not part.endswith(("，", "。", "！", "？", ",", ".", "!", "?")) for part in message_parts))

    async def test_should_honor_split_probability_for_short_reply_with_pause(self) -> None:
        """验证设定集链路继续沿用原有拆分概率与停顿清理规则。"""
        agent = SocialAgent(ToolRegistry())

        message_parts = agent._build_chat_bubbles(
            "等下，马上来",
            "social-no-random-split",
            max_reply_chars=16,
            split_reply_chance_percent=0,
        )

        self.assertEqual(message_parts, ["等下马上来"])

    async def test_should_honor_profile_reply_shape_configuration(self) -> None:
        # 这个函数的作用是验证设定集链路继续遵守原有单条长度配置。
        agent = SocialAgent(ToolRegistry())
        message_parts = agent._build_chat_bubbles(
            "\u4f60\u5148\u522b\u6025\uff0c\u665a\u70b9\u6211\u548c\u4f60\u8bf4\u6e05\u695a\u8fd9\u4e8b",
            "social-configured-shape",
            max_reply_chars=8,
            split_long_reply=False,
            split_reply_chance_percent=100,
        )

        self.assertEqual(len(message_parts), 1)
        self.assertEqual(message_parts, ["你先别急，晚点我"])

    async def test_should_fallback_to_rules_when_llm_call_fails(self) -> None:
        # 这个测试函数的作用是验证大模型调用失败时，SocialAgent 仍会回退到本地规则模板，避免整条链路中断。
        llm_client = DummyLlmClient(enabled=True, should_fail=True)
        agent = SocialAgent(ToolRegistry(), llm_client=llm_client)
        event = UnifiedEvent(
            eventId="qq:message:private:social-004",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="2597164807",
            sender=Sender(id="10004", name="david", role=None),
            text="谢谢",
            attachments=[],
            mentions=[],
            timestamp="2026-07-09T13:45:00+08:00",
            rawPayload={},
        )
        context = AgentTaskContext(
            task_id="social-run-004",
            route="social_reply",
            event=event,
            allowed_tools=[],
            metadata={},
        )

        result = await agent.run(context, "draft_reply")

        self.assertFalse(result.structured_result["llmUsed"])
        self.assertEqual(len(llm_client.calls), 1)
        self.assertEqual("不客气有需要随时说", result.reply_draft)

    async def test_should_extract_secure_asset_control_marker_without_exposing_it(self) -> None:
        """验证模型只能用内部控制行申请资产，聊天正文不会泄露控制协议。"""
        visible_reply, asset_requests = SocialAgent._extract_asset_requests(
            "已经确认好了\n[[MEMO_ECHO_USE_ASSET:asset-card-1]]\n"
            "[[MEMO_ECHO_USE_ASSET:asset-card-1]]"
        )

        self.assertEqual("已经确认好了", visible_reply)
        self.assertEqual(["asset-card-1"], asset_requests)

    def test_should_append_only_verified_memory_with_traceable_id(self) -> None:
        """生成层应看到用户已确认事实及其记忆 ID，且提示词明确禁止继续扩写。"""
        memory = VerifiedMemory(
            id="memory-social-001",
            subject="对方",
            predicate="常用称呼",
            value="小明",
            scopeType="CONVERSATION",
            sourceEventIds=["owner-event-001"],
            sourceActorType="OWNER",
            factAuthority="human_self",
            status="VERIFIED",
        )

        prompt = SocialAgent._append_verified_memories("基础提示", [memory])

        self.assertIn("[用户已确认的长期记忆]", prompt)
        self.assertIn("[memory:memory-social-001] 对方 / 常用称呼 / 小明", prompt)
        self.assertIn("不得扩展出未记录的新事实", prompt)

    def test_should_append_persisted_delegated_task_contract(self) -> None:
        """验证委托任务目标、已确认事实和待处理条件会在客户端重启后继续注入提示词。"""
        event = UnifiedEvent(
            eventId="qq:message:private:delegated-001",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="3807050597",
            sender=Sender(id="3807050597", name="km", role=None),
            text="晚上七点可以",
            attachments=[],
            mentions=[],
            timestamp="2026-07-21T14:00:00+08:00",
            rawPayload={},
        )
        context = AgentTaskContext(
            task_id="delegated-task-001",
            route="social_reply",
            event=event,
            allowed_tools=[],
            metadata={
                "delegated_task": {
                    "status": "ACTIVE",
                    "targetName": "km",
                    "objective": "约对方明天晚上打球",
                    "successCriteria": "对方明确接受或拒绝",
                    "deadlineText": "明天",
                    "progressSummary": "对方已经接受，正在确认时间",
                    "stateJson": (
                        '{"knownFacts":["对方明天有空"],'
                        '"pendingConditions":["确认具体时间"]}'
                    ),
                }
            },
        )

        prompt = SocialAgent._append_delegated_task_instruction("基础提示", context)

        self.assertIn("目标联系人：km", prompt)
        self.assertIn("已确认事实：对方明天有空", prompt)
        self.assertIn("仍待处理：确认具体时间", prompt)
        self.assertIn("不得再次询问", prompt)
        self.assertNotIn("尚未开始", prompt)


if __name__ == "__main__":
    unittest.main()
