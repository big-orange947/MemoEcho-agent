from __future__ import annotations

import unittest

from app.agents.social_agent import SocialAgent
from app.schemas.events import Sender, UnifiedEvent
from app.schemas.model_profiles import ResolvedUserModelProfile
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
        self.assertIn("我在，你说。", result.reply_draft)
        self.assertIn("有需要的话我继续帮你。", result.reply_draft)
        self.assertEqual(result.structured_result["replyMode"], "DRAFT_ONLY")
        self.assertIn("warm", result.structured_result["styleTags"])
        self.assertIn("以下是当前会话的人格设定", result.structured_result["effectiveSystemPrompt"])
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

        self.assertIn("收到，我优先处理，稍后给你结果。", result.reply_draft)
        self.assertIn("我会按优先级继续处理。", result.reply_draft)
        self.assertTrue(result.need_confirmation)
        self.assertEqual(result.structured_result["skillReference"], "github://demo/style/professional")
        self.assertIn("professional", result.structured_result["styleTags"])
        self.assertIn("当前会话已绑定以下 skills", result.structured_result["effectiveSystemPrompt"])
        self.assertEqual(result.structured_result["promptSource"], "skill_plus_profile_prompt")

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

        self.assertEqual(result.reply_draft, "这是模型生成的回复草稿。")
        self.assertTrue(result.structured_result["llmUsed"])
        self.assertTrue(result.structured_result["llmEnabled"])
        self.assertEqual(len(llm_client.calls), 1)
        self.assertIn("这些 skills 提供的系统约束如下", llm_client.calls[0]["system_prompt"])
        self.assertIn("回复时保持冷静、可靠、克制", llm_client.calls[0]["system_prompt"])
        self.assertIsNotNone(llm_client.calls[0]["model_profile"])
        self.assertEqual(llm_client.calls[0]["model_profile"].model, "gpt-4o-mini")
        self.assertEqual(result.structured_result["resolvedModelProfile"]["userId"], "freeze")
        self.assertEqual(result.structured_result["promptSource"], "skill_plus_profile_prompt")

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
        self.assertIn("不客气，有需要随时说。", result.reply_draft)


if __name__ == "__main__":
    unittest.main()
