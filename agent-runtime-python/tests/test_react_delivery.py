from __future__ import annotations

import unittest

from app.agents.social_agent import SocialAgent
from app.schemas.delegated_tasks import DelegatedTaskActionInput
from app.schemas.events import Sender, UnifiedEvent
from app.schemas.tasks import AgentTaskContext
from app.tools.registry import ToolRegistry
from app.workflows.delegated_task_graph import DelegatedTaskWorkflow


class DisabledLlmClient:
    """Keep workflow tests deterministic without a remote model."""

    def is_enabled(self, model_profile=None) -> bool:
        return False


class ExplodingLlmClient:
    """Fail if a reviewed ReAct candidate is generated a second time."""

    def is_enabled(self, model_profile=None) -> bool:
        return True

    async def generate_reply(self, *args, **kwargs) -> str:
        raise AssertionError("reviewed ReAct candidate must not be regenerated")


def build_action_input() -> DelegatedTaskActionInput:
    """Build a minimal main-console task action input."""
    return DelegatedTaskActionInput.model_validate(
        {
            "task": {
                "id": "delegated-react-001",
                "objective": "Confirm tutoring time with the contact",
                "successCriteria": "The contact explicitly accepts the time",
                "targetName": "km",
                "createdAt": "2026-07-24T10:00:00+08:00",
            },
            "history": [],
            "event": {
                "eventId": "qq:message:private:react-001",
                "eventType": "message",
                "text": "The proposed time works for me",
                "sentAt": "2026-07-24T10:05:00+08:00",
                "direction": "INBOUND",
                "actorType": "CONTACT",
                "messageOrigin": "EXTERNAL",
            },
        }
    )


class ReactDeliveryTest(unittest.IsolatedAsyncioTestCase):
    """Protect reviewed ReAct decisions from legacy reply paths."""

    def setUp(self) -> None:
        self.workflow = DelegatedTaskWorkflow(DisabledLlmClient())
        self.action_input = build_action_input()

    async def test_react_send_plan_is_not_overridden_by_legacy_rules(self) -> None:
        selected = (await self.workflow._select_runtime_action(
            {
                "action_input": self.action_input,
                "evaluation": {
                    "reactManaged": True,
                    "requestedTool": "send_qq_message",
                    "messageInstruction": "Sounds good, see you tomorrow evening",
                    "reason": "contact accepted the proposal",
                },
            }
        ))["selected_action"]

        self.assertEqual("SEND_MESSAGE", selected["action"])
        self.assertEqual("Sounds good, see you tomorrow evening", selected["messageInstruction"])

    async def test_react_completion_plan_maps_to_completion_action(self) -> None:
        selected = (await self.workflow._select_runtime_action(
            {
                "action_input": self.action_input,
                "evaluation": {
                    "reactManaged": True,
                    "requestedTool": "complete_delegated_task",
                    "messageInstruction": "Confirmed, see you tomorrow evening",
                    "reason": "success condition is met",
                },
            }
        ))["selected_action"]

        self.assertEqual("SEND_AND_COMPLETE", selected["action"])
        self.assertEqual("Confirmed, see you tomorrow evening", selected["messageInstruction"])

    def test_finalized_react_action_preserves_reviewed_candidate(self) -> None:
        result = self.workflow._finalize_action(
            {
                "action_input": self.action_input,
                "evaluation": {
                    "reactManaged": True,
                    "progressSummary": "Time confirmed",
                    "completionReport": "Tutoring time confirmed",
                    "knownFacts": ["contact accepted the time"],
                    "pendingConditions": [],
                    "evidence": ["The proposed time works for me"],
                    "evidenceEventIds": ["qq:message:private:react-001"],
                    "toolArguments": {"reactIteration": 1},
                },
                "selected_action": {
                    "action": "SEND_AND_COMPLETE",
                    "reason": "success condition is met",
                    "messageInstruction": "Confirmed, see you tomorrow evening",
                },
                "previous_state": {},
                "timeline": [],
                "react_trace": [{"tool": "complete_delegated_task"}],
                "review_decision": "APPROVE",
                "review_feedback": "",
            }
        )["result"]

        self.assertEqual("complete_delegated_task", result.requested_tool)
        self.assertTrue(result.tool_arguments["reactManaged"])
        self.assertEqual(
            "Confirmed, see you tomorrow evening",
            result.tool_arguments["finalCandidateMessage"],
        )

    async def test_social_agent_sends_reviewed_react_candidate_without_regeneration(self) -> None:
        event = UnifiedEvent(
            eventId="qq:message:private:react-direct-001",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="3807050597",
            sender=Sender(id="3807050597", name="km", role=None),
            text="The proposed time works for me",
            attachments=[],
            mentions=[],
            timestamp="2026-07-24T10:05:00+08:00",
            rawPayload={},
        )
        context = AgentTaskContext(
            task_id="react-direct-001",
            route="social_reply",
            event=event,
            metadata={
                "delegated_task": {"id": "delegated-react-001"},
                "delegated_task_action": {
                    "action": "SEND_AND_COMPLETE",
                    "toolArguments": {
                        "reactManaged": True,
                        "finalCandidateMessage": "Confirmed, see you tomorrow evening",
                    },
                },
            },
        )

        result = await SocialAgent(ToolRegistry(), ExplodingLlmClient()).run(context, "draft_reply")

        self.assertEqual("Confirmed, see you tomorrow evening", result.reply_draft)
        self.assertTrue(result.structured_result["directCandidate"])
        self.assertFalse(result.structured_result["llmUsed"])
