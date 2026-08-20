from __future__ import annotations

import unittest

from langgraph.checkpoint.memory import MemorySaver

from app.schemas.delegated_tasks import DelegatedTaskActionDecision
from app.schemas.events import Sender, UnifiedEvent
from app.workflows.delegated_execution_graph import DelegatedExecutionGraph


class DummyExecutionClient:
    """固定主链路图测试用的最小 event-center 替身。"""

    def __init__(self) -> None:
        self.current_events: dict[str, dict] = {}
        self.workflow_completions: list[dict] = []
        self.runtime_updates: list[dict] = []
        self.claims: list[dict] = []
        self.completed_claims: list[dict] = []
        self.history_calls: list[dict] = []

    async def upsert_delegated_task_current_event(self, event: UnifiedEvent, task_id: str) -> dict:
        stored = {
            "taskId": task_id,
            "eventId": event.event_id,
            "text": event.text or "",
            "payload": event.model_dump(mode="json", by_alias=True),
        }
        self.current_events[task_id] = stored
        return stored

    async def get_delegated_task_current_event(self, event: UnifiedEvent, task_id: str) -> dict | None:
        return self.current_events.get(task_id)

    async def list_conversation_messages(self, chat_id: str, **kwargs) -> list[dict]:
        self.history_calls.append({"chat_id": chat_id, **kwargs})
        return []

    async def claim_delegated_task_event(self, event, task_id, event_id, lease_seconds=120) -> dict:
        self.claims.append({"taskId": task_id, "eventId": event_id})
        return {"claimed": True, "claimToken": f"claim:{task_id}:{event_id}"}

    async def complete_delegated_task_event(self, event, task_id, event_id, claim_token) -> None:
        self.completed_claims.append({"taskId": task_id, "eventId": event_id, "claimToken": claim_token})

    async def release_delegated_task_event(self, event, task_id, event_id, claim_token) -> None:
        pass

    async def complete_delegated_workflow_step(
        self, event, workflow_id, step_key, *, produced_facts, result_summary, result, artifacts=None, source_event_id=None
    ) -> dict:
        self.workflow_completions.append(
            {
                "workflowId": workflow_id,
                "stepKey": step_key,
                "producedFacts": produced_facts,
                "artifacts": artifacts or [],
                "sourceEventId": source_event_id,
            }
        )
        return {"id": workflow_id, "status": "RUNNING"}

    async def update_delegated_task_runtime(self, event, task_id, **runtime_state) -> dict:
        self.runtime_updates.append({"taskId": task_id, **runtime_state})
        return {"taskId": task_id}


class FixedDecisionWorkflow:
    """固定返回完成决策的委托工作流替身，让测试只关注主链路与检查点。"""

    def __init__(self, action: str = "COMPLETE_TASK") -> None:
        self.action = action
        self.seen_envelope: dict | None = None

    async def decide_action(self, action_input, model_profile):
        self.seen_envelope = action_input.context_envelope
        return DelegatedTaskActionDecision(
            action=self.action,
            reason="对方已明确回复上课时间",
            progressSummary="已获得 km 的回复",
            stateJson="{}",
            lastEventId="",
            completionReport="km 回复七点半",
            requestedTool="complete_delegated_task",
        )


class DelegatedExecutionGraphTest(unittest.IsolatedAsyncioTestCase):
    """验证固定主链路图按 ingest/hydrate/reconcile/select/react/review/persist 推进。"""

    def _task(self) -> dict:
        return {
            "id": "child-task-ask-km",
            "workflowId": "workflow-class-time",
            "stepKey": "ask_km",
            "producesFacts": ["class_time"],
            "conversationScopeJson": '{"platform":"qq","chatType":"private","chatId":"3807050597"}',
            "startedAt": "2026-08-11T18:00:00+08:00",
            "startEventId": "qq:message:private:start",
        }

    def _event(self) -> UnifiedEvent:
        return UnifiedEvent(
            eventId="qq:message:private:km-reply",
            platform="qq",
            scene="life",
            eventType="message",
            chatType="private",
            chatId="3807050597",
            selfId="3969785168",
            sender=Sender(id="3807050597", name="km", role=None),
            text="七点半",
            attachments=[],
            mentions=[],
            timestamp="2026-08-11T18:04:00+08:00",
            rawPayload={"userId": "freeze"},
        )

    async def test_should_run_fixed_chain_and_publish_artifact(self) -> None:
        client = DummyExecutionClient()
        workflow = FixedDecisionWorkflow()
        graph = DelegatedExecutionGraph(
            delegated_task_workflow=workflow,
            event_center_client=client,
        )
        event = self._event()
        task = self._task()

        result = await graph.run(
            event=event,
            task=task,
            model_profile=None,
            claim_token="claim:child-task-ask-km:km-reply",
        )

        # ingest：L0 当前事件已写入。
        self.assertIn("child-task-ask-km", client.current_events)
        self.assertEqual(client.current_events["child-task-ask-km"]["eventId"], "qq:message:private:km-reply")
        # hydrate：历史查询使用步骤会话范围与起点水位。
        self.assertEqual(client.history_calls[0]["chat_id"], "3807050597")
        self.assertIn("2026-08-11T18:00:00", str(client.history_calls[0]["after"]))
        # react：统一上下文携带当前事件。
        self.assertIsNotNone(workflow.seen_envelope)
        timeline_texts = [
            " ".join(str(row.get("text") or "").split())
            for row in (workflow.seen_envelope.get("taskTimeline") or [])
        ]
        self.assertIn("七点半", timeline_texts)
        # persist：发布类型化产物并关闭事件租约。
        self.assertEqual(1, len(client.workflow_completions))
        completion = client.workflow_completions[0]
        self.assertEqual(completion["producedFacts"], {"class_time": "七点半"})
        self.assertEqual(completion["artifacts"][0]["type"], "CLASS_TIME")
        self.assertTrue(completion["sourceEventId"])
        self.assertEqual(len(client.completed_claims), 1)
        # 最终状态。
        self.assertEqual(result.get("route"), "done")
        self.assertTrue(result.get("persisted"))
        self.assertEqual(result.get("decision", {}).get("action"), "COMPLETE_TASK")

    async def test_should_skip_claim_when_token_provided(self) -> None:
        client = DummyExecutionClient()
        graph = DelegatedExecutionGraph(
            delegated_task_workflow=FixedDecisionWorkflow(),
            event_center_client=client,
        )
        await graph.run(
            event=self._event(),
            task=self._task(),
            model_profile=None,
            claim_token="claim-provided-by-orchestrator",
        )
        # 编排层已提前认领，reconcile 节点不得重复抢占。
        self.assertEqual(client.claims, [])

    async def test_should_persist_checkpoint_keyed_by_workflow_id(self) -> None:
        client = DummyExecutionClient()
        saver = MemorySaver()
        graph = DelegatedExecutionGraph(
            delegated_task_workflow=FixedDecisionWorkflow(),
            event_center_client=client,
            checkpointer=saver,
        )
        await graph.run(event=self._event(), task=self._task(), model_profile=None)

        checkpoints = [c for c in saver.list(None)]
        self.assertTrue(checkpoints, "Checkpointer 必须保存至少一个运行快照")
        # thread key 使用 workflowId：thread_id 必须等于工作流 ID。
        thread_ids = {str(getattr(c, "config", {}).get("configurable", {}).get("thread_id", "")) for c in checkpoints}
        self.assertIn("workflow-class-time", thread_ids)


if __name__ == "__main__":
    unittest.main()
