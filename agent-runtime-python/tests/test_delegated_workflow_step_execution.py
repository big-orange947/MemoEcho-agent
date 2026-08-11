from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.memory.manager import MemoryManager
from app.orchestrator.service import OrchestratorService
from app.planner.service import PlannerService
from app.router.service import RouterService
from app.schemas.delegated_workflows import DelegatedWorkflowStepExecutionRequest
from app.services.slow_channel_buffer import SlowChannelBuffer
from app.tools.registry import ToolRegistry


class FakeEventCenterClient:
    """提供固定工作流快照，避免测试依赖真实 Java 服务。"""

    def __init__(self, workflow: dict) -> None:
        # 保存服务端快照和读取记录，便于断言执行前确实完成了版本校验。
        self.workflow = workflow
        self.calls: list[tuple[str, str]] = []
        self.claim_calls: list[tuple[str, str]] = []
        self.claim_results: list[dict] = []
        self.complete_calls: list[tuple[str, str, str]] = []
        self.release_calls: list[tuple[str, str, str]] = []
        self.recover_calls: list[tuple[str, str]] = []
        self.recover_result = True

    async def get_delegated_workflow_runtime(self, user_id: str, workflow_id: str) -> dict:
        """模拟 runtime 专用查询接口并返回当前工作流状态。"""
        self.calls.append((user_id, workflow_id))
        return self.workflow

    async def claim_delegated_task_event(
        self,
        event,
        task_id: str,
        event_id: str,
        lease_seconds: int,
    ) -> dict:
        """模拟成功认领步骤事件，并记录稳定事件标识。"""
        self.claim_calls.append((task_id, event_id))
        if self.claim_results:
            return self.claim_results.pop(0)
        return {"claimed": True, "claimToken": "claim-001", "leaseSeconds": lease_seconds}

    async def recover_dormant_delegated_task_event(
        self,
        event,
        task_id: str,
        event_id: str,
    ) -> bool:
        """模拟恢复旧版本留下的空完成认领，并记录恢复请求。"""
        self.recover_calls.append((task_id, event_id))
        return self.recover_result

    async def complete_delegated_task_event(
        self,
        event,
        task_id: str,
        event_id: str,
        claim_token: str,
    ) -> dict:
        """模拟提交已产生业务效果的步骤事件。"""
        self.complete_calls.append((task_id, event_id, claim_token))
        return {"completed": True}

    async def release_delegated_task_event(
        self,
        event,
        task_id: str,
        event_id: str,
        claim_token: str,
    ) -> dict:
        """模拟释放未产生业务效果的步骤事件。"""
        self.release_calls.append((task_id, event_id, claim_token))
        return {"released": True}


def build_request(activation_version: int = 2) -> DelegatedWorkflowStepExecutionRequest:
    """构造 Java outbox 投递给 Python 的标准步骤执行请求。"""
    return DelegatedWorkflowStepExecutionRequest(
        workflowId="workflow-001",
        stepKey="contact-km",
        activationVersion=activation_version,
        taskId="task-001",
        userId="freeze",
        idempotencyKey=f"workflow-001:contact-km:{activation_version}",
    )


def build_workflow(activation_version: int = 2, step_status: str = "ACTIVE") -> dict:
    """构造包含单个已解析私聊步骤的运行时工作流快照。"""
    return {
        "id": "workflow-001",
        "status": "RUNNING",
        "steps": [
            {
                "stepKey": "contact-km",
                "taskId": "task-001",
                "status": step_status,
                "activationVersion": activation_version,
                "platform": "qq",
                "chatType": "private",
                "chatId": "3807050597",
            }
        ],
    }


def canonical_event_id(activation_version: int = 2) -> str:
    """返回运行时用于事件租约的会话级幂等标识。"""
    return (
        "qq:private:3807050597:client:"
        f"workflow-001:contact-km:{activation_version}"
    )


def build_service(client: FakeEventCenterClient) -> OrchestratorService:
    """创建只用于验证入口校验和事件转换的最小编排服务。"""
    return OrchestratorService(
        router=RouterService(),
        planner=PlannerService(),
        tools=ToolRegistry(),
        memory=MemoryManager(),
        slow_channel_buffer=SlowChannelBuffer(window_seconds=600, max_messages=10),
        event_center_client=client,
    )


class DelegatedWorkflowStepExecutionTest(unittest.IsolatedAsyncioTestCase):
    """验证事务 outbox 到 Python 显式步骤入口的版本栅栏。"""

    async def test_shouldExecuteCurrentActiveStepWithStableIdempotencyEvent(self) -> None:
        """当前激活版本匹配时，应生成一次可幂等追踪的内部事件。"""
        client = FakeEventCenterClient(build_workflow())
        service = build_service(client)
        service.handle_event = AsyncMock(return_value=SimpleNamespace(
            write_back_actions=["delegated_task_action:send_qq_message"],
        ))

        response = await service.execute_delegated_workflow_step(build_request())

        self.assertEqual("executed", response.status)
        self.assertFalse(response.retryable)
        self.assertEqual(["delegated_task_action:send_qq_message"], response.write_back_actions)
        self.assertEqual([("freeze", "workflow-001")], client.calls)
        service.handle_event.assert_awaited_once()
        event = service.handle_event.await_args.args[0]
        self.assertEqual("workflow-001:contact-km:2", event.event_id)
        self.assertEqual("task-001", event.delegated_task_id)
        self.assertEqual("3807050597", event.chat_id)
        self.assertEqual("INTERNAL", event.direction)
        self.assertEqual("workflow-001", event.raw_payload["delegatedWorkflowId"])
        self.assertEqual("contact-km", event.raw_payload["delegatedWorkflowStepKey"])
        self.assertEqual(2, event.raw_payload["activationVersion"])
        self.assertEqual([("task-001", canonical_event_id())], client.claim_calls)
        self.assertEqual(
            [("task-001", canonical_event_id(), "claim-001")],
            client.complete_calls,
        )
        self.assertEqual([], client.release_calls)

    async def test_shouldDeferWhenOrchestratorProducedNoPersistentEffect(self) -> None:
        """步骤仍为同一激活版本且编排器仅跳过时，必须让 outbox 稍后重试。"""
        client = FakeEventCenterClient(build_workflow())
        service = build_service(client)
        service.handle_event = AsyncMock(return_value=SimpleNamespace(
            write_back_actions=["delegated_task_event:skipped"],
        ))

        response = await service.execute_delegated_workflow_step(build_request())

        self.assertEqual("deferred", response.status)
        self.assertEqual("no_persistent_effect", response.reason)
        self.assertTrue(response.retryable)
        self.assertEqual(2, len(client.calls))
        self.assertEqual([("task-001", canonical_event_id())], client.claim_calls)
        self.assertEqual([], client.complete_calls)
        self.assertEqual(
            [("task-001", canonical_event_id(), "claim-001")],
            client.release_calls,
        )

    async def test_shouldRecoverDormantCompletedClaimAndExecuteOnce(self) -> None:
        """旧版本提前完成的空认领应恢复一次，并在重新认领后执行真实副作用。"""
        client = FakeEventCenterClient(build_workflow())
        client.claim_results = [
            {"claimed": False, "status": "COMPLETED"},
            {"claimed": True, "claimToken": "claim-recovered", "leaseSeconds": 120},
        ]
        service = build_service(client)
        service.handle_event = AsyncMock(return_value=SimpleNamespace(
            write_back_actions=["delegated_task_action:send_qq_message"],
        ))

        response = await service.execute_delegated_workflow_step(build_request())

        self.assertEqual("executed", response.status)
        self.assertEqual(
            [("task-001", canonical_event_id()), ("task-001", canonical_event_id())],
            client.claim_calls,
        )
        self.assertEqual([("task-001", canonical_event_id())], client.recover_calls)
        self.assertEqual(
            [("task-001", canonical_event_id(), "claim-recovered")],
            client.complete_calls,
        )
        service.handle_event.assert_awaited_once()

    async def test_shouldNotExecuteWhenDormantCompletedClaimCannotBeRecovered(self) -> None:
        """不满足严格恢复条件的完成认领必须保持跳过，避免重复发送。"""
        client = FakeEventCenterClient(build_workflow())
        client.claim_results = [{"claimed": False, "status": "COMPLETED"}]
        client.recover_result = False
        service = build_service(client)
        service.handle_event = AsyncMock()

        response = await service.execute_delegated_workflow_step(build_request())

        self.assertEqual("deferred", response.status)
        self.assertEqual("event_claim_unavailable", response.reason)
        self.assertTrue(response.retryable)
        self.assertEqual([("task-001", canonical_event_id())], client.claim_calls)
        self.assertEqual([("task-001", canonical_event_id())], client.recover_calls)
        service.handle_event.assert_not_awaited()

    async def test_shouldIgnoreStaleActivationWithoutExecutingOrchestrator(self) -> None:
        """旧版本 outbox 即使延迟抵达，也不得再次发送消息或推进任务。"""
        client = FakeEventCenterClient(build_workflow(activation_version=3))
        service = build_service(client)
        service.handle_event = AsyncMock()

        response = await service.execute_delegated_workflow_step(build_request(activation_version=2))

        self.assertEqual("ignored", response.status)
        self.assertEqual("stale_activation_version", response.reason)
        service.handle_event.assert_not_awaited()
        self.assertEqual([], client.claim_calls)

    async def test_shouldIgnoreStepThatIsNoLongerActive(self) -> None:
        """步骤完成或暂停后，遗留投递必须被确认消费但不能重新执行。"""
        client = FakeEventCenterClient(build_workflow(step_status="COMPLETED"))
        service = build_service(client)
        service.handle_event = AsyncMock()

        response = await service.execute_delegated_workflow_step(build_request())

        self.assertEqual("ignored", response.status)
        self.assertEqual("step_not_active", response.reason)
        service.handle_event.assert_not_awaited()
        self.assertEqual([], client.claim_calls)

    async def test_shouldReleaseClaimWhenOrchestratorRaisesException(self) -> None:
        """编排器异常不能遗留租约，否则同一 outbox 后续重试会永久被拒绝。"""
        client = FakeEventCenterClient(build_workflow())
        service = build_service(client)
        service.handle_event = AsyncMock(side_effect=RuntimeError("boom"))

        with self.assertRaisesRegex(RuntimeError, "boom"):
            await service.execute_delegated_workflow_step(build_request())

        self.assertEqual([], client.complete_calls)
        self.assertEqual(
            [("task-001", canonical_event_id(), "claim-001")],
            client.release_calls,
        )


if __name__ == "__main__":
    unittest.main()
