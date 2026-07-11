from app.agents.file_agent import FileAgent
from app.agents.groupops_agent import GroupOpsAgent
from app.agents.inbox_agent import InboxAgent
from app.agents.inbox_dispatch_agent import InboxDispatchAgent
from app.agents.schedule_agent import ScheduleAgent
from app.agents.social_agent import SocialAgent
from app.agents.work_agent import WorkAgent
from app.clients.llm_service import LlmServiceClient
from app.services.slow_channel_buffer import SlowChannelBuffer
from app.tools.registry import ToolRegistry


def build_agent_registry(
    tools: ToolRegistry,
    slow_channel_buffer: SlowChannelBuffer,
    llm_client: LlmServiceClient | None = None,
) -> dict[str, object]:
    # 这个函数的作用是统一构建运行时可调度的 agent 实例，避免在多个地方重复装配。
    return {
        "inbox_dispatch": InboxDispatchAgent(tools, slow_channel_buffer),
        "inbox": InboxAgent(tools),
        "schedule": ScheduleAgent(tools, llm_client=llm_client),
        "work": WorkAgent(tools, llm_client=llm_client),
        "file": FileAgent(tools),
        "social": SocialAgent(tools, llm_client=llm_client),
        "groupops": GroupOpsAgent(tools),
    }
