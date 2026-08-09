from __future__ import annotations

import unittest
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

    async def get_delegated_workflow_runtime(self, user_id: str, workflow_id: str) -> dict:
        """模拟 runtime 专用查询接口并返回当前工作流状态。"""
        self.calls.append((user_id, workflow_id))
        return self.workflow


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
        service.handle_event = AsyncMock()

        response = await service.execute_delegated_workflow_step(build_request())

        self.assertEqual("executed", response.status)
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

    async def test_shouldIgnoreStaleActivationWithoutExecutingOrchestrator(self) -> None:
        """旧版本 outbox 即使延迟抵达，也不得再次发送消息或推进任务。"""
        client = FakeEventCenterClient(build_workflow(activation_version=3))
        service = build_service(client)
        service.handle_event = AsyncMock()

        response = await service.execute_delegated_workflow_step(build_request(activation_version=2))

        self.assertEqual("ignored", response.status)
        self.assertEqual("stale_activation_version", response.reason)
        service.handle_event.assert_not_awaited()

    async def test_shouldIgnoreStepThatIsNoLongerActive(self) -> None:
        """步骤完成或暂停后，遗留投递必须被确认消费但不能重新执行。"""
        client = FakeEventCenterClient(build_workflow(step_status="COMPLETED"))
        service = build_service(client)
        service.handle_event = AsyncMock()

        response = await service.execute_delegated_workflow_step(build_request())

        self.assertEqual("ignored", response.status)
        self.assertEqual("step_not_active", response.reason)
        service.handle_event.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
