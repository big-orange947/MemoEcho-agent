from app.agents.file_agent import FileAgent
from app.agents.groupops_agent import GroupOpsAgent
from app.agents.inbox_agent import InboxAgent
from app.agents.inbox_dispatch_agent import InboxDispatchAgent
from app.agents.schedule_agent import ScheduleAgent
from app.agents.social_agent import SocialAgent
from app.agents.work_agent import WorkAgent
from app.services.slow_channel_buffer import SlowChannelBuffer
from app.tools.registry import ToolRegistry


def build_agent_registry(tools: ToolRegistry, slow_channel_buffer: SlowChannelBuffer) -> dict[str, object]:
    return {
        "inbox_dispatch": InboxDispatchAgent(tools, slow_channel_buffer),
        "inbox": InboxAgent(tools),
        "schedule": ScheduleAgent(tools),
        "work": WorkAgent(tools),
        "file": FileAgent(tools),
        "social": SocialAgent(tools),
        "groupops": GroupOpsAgent(tools),
    }
