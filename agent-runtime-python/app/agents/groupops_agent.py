from __future__ import annotations

import re
from typing import Any

from app.agents.base import BaseAgent
from app.schemas.results import AgentResult, ToolCallRecord
from app.schemas.tasks import AgentTaskContext
from app.tools.registry import ToolRegistry


class GroupOpsAgent(BaseAgent):
    """把明确的群管理请求转换为只读查询或待审批写操作。"""

    name = "groupops"

    def __init__(self, tools: ToolRegistry) -> None:
        self.tools = tools

    async def run(self, task_context: AgentTaskContext, action: str) -> AgentResult:
        """处理单次群操作；写动作只创建审批单，绝不在本函数中直接执行。"""
        event = task_context.event
        if event.platform != "qq" or event.chat_type != "group":
            return self._result(task_context, "群管理功能只能在当前 QQ 群会话中使用", status="blocked")

        parsed = self._parse_operation(task_context)
        if parsed is None:
            return self._result(
                task_context,
                "没有识别到明确的群管理动作，请说明要查询或修改的项目",
                status="needs_clarification",
            )

        operation_type = parsed.pop("operation_type")
        operation = parsed["action"]
        if operation_type == "read":
            return await self._run_read_operation(task_context, parsed)
        if not self._is_trusted_requester(task_context):
            return self._result(
                task_context,
                "群管理请求已忽略，只有当前登录账号本人可以发起管理操作",
                status="blocked",
            )
        return await self._prepare_write_operation(task_context, parsed)

    @staticmethod
    def _is_trusted_requester(task_context: AgentTaskContext) -> bool:
        """只信任登录 QQ 本人或内部控制面事件，群内其他成员不能借自然语言驱动管理工具。"""
        event = task_context.event
        sent_by_account_owner = bool(event.self_id) and str(event.sender.id) == str(event.self_id)
        sent_by_control_plane = task_context.metadata.get("trusted_control_plane") is True
        return sent_by_account_owner or sent_by_control_plane

    async def _run_read_operation(
        self,
        task_context: AgentTaskContext,
        parsed: dict[str, Any],
    ) -> AgentResult:
        """执行只读查询并生成简短群内回复。"""
        if "query_qq_group" not in task_context.allowed_tools:
            return self._result(task_context, "当前会话没有开放群信息查询权限", status="blocked")
        arguments = {"action": parsed["action"], "group_id": int(task_context.event.chat_id)}
        response = await self._invoke_tool(task_context, "query_qq_group", arguments)
        reply = self._format_read_reply(parsed["action"], response)
        return AgentResult(
            task_id=task_context.task_id,
            agent=self.name,
            status="success",
            structured_result={
                "operationType": "READ",
                "groupOperation": parsed["action"],
                "platformResult": response,
            },
            reply_draft=reply,
            tool_calls=[ToolCallRecord(tool="query_qq_group", arguments=arguments)],
        )

    async def _prepare_write_operation(
        self,
        task_context: AgentTaskContext,
        parsed: dict[str, Any],
    ) -> AgentResult:
        """检查特权工具后创建审批单；Skill 和自然语言都不能跳过该步骤。"""
        if "manage_qq_group" not in task_context.allowed_tools:
            return self._result(
                task_context,
                "已识别到群管理修改请求，但当前设定没有开放 manage_qq_group 特权",
                status="blocked",
            )
        arguments = {
            **{key: value for key, value in parsed.items() if key != "action"},
            "action": parsed["action"],
            "group_id": int(task_context.event.chat_id),
            "event_id": task_context.event.event_id,
            "requester_id": task_context.event.sender.id,
        }
        proposal = await self._invoke_tool(
            task_context,
            "manage_qq_group",
            arguments,
            idempotency_key=f"group-operation:{task_context.event.event_id}:{parsed['action']}",
        )
        return AgentResult(
            task_id=task_context.task_id,
            agent=self.name,
            status="confirmation_required",
            structured_result={
                "operationType": "WRITE",
                "groupOperation": parsed["action"],
                "approval": proposal,
                "handoffRequired": True,
                "handoffReason": "群管理写操作必须由用户在客户端确认",
            },
            reply_draft="群管理操作已生成审批单，确认前不会执行",
            tool_calls=[ToolCallRecord(tool="manage_qq_group", arguments=arguments)],
            need_confirmation=True,
            next_actions=["在客户端核对目标群、目标成员和动作参数后确认"],
        )

    def _parse_operation(self, task_context: AgentTaskContext) -> dict[str, Any] | None:
        """用确定性规则解析有限动作，避免模型自由生成 NapCat action 或参数。"""
        event = task_context.event
        text = self._clean_text(event.text or "")
        lowered = text.lower()
        slash = self._parse_slash_command(text)
        if slash is not None:
            return slash

        if any(keyword in lowered for keyword in ("群信息", "群资料", "group info")):
            return {"operation_type": "read", "action": "group_info"}
        if any(keyword in lowered for keyword in ("群成员", "成员列表", "group members")):
            return {"operation_type": "read", "action": "member_list"}
        if any(keyword in lowered for keyword in ("禁言列表", "谁被禁言", "mute list")):
            return {"operation_type": "read", "action": "shut_list"}
        if any(keyword in lowered for keyword in ("精华消息列表", "查看精华", "essence list")):
            return {"operation_type": "read", "action": "essence_list"}
        if any(keyword in lowered for keyword in ("群文件列表", "查看群文件", "group files")):
            return {"operation_type": "read", "action": "file_list"}
        if any(keyword in lowered for keyword in ("群公告", "查看公告", "group notices")) and not any(
            keyword in lowered for keyword in ("发布", "发送", "新增", "publish")
        ):
            return {"operation_type": "read", "action": "notice_list"}

        if "解除全员禁言" in text or "取消全员禁言" in text or "whole mute off" in lowered:
            return {"operation_type": "write", "action": "whole_mute", "enable": False}
        if "全员禁言" in text or "whole mute on" in lowered:
            return {"operation_type": "write", "action": "whole_mute", "enable": True}

        target_user_id = self._target_user_id(task_context)
        if any(keyword in text for keyword in ("解除禁言", "取消禁言")) or "unmute" in lowered:
            if target_user_id is None:
                return None
            return {"operation_type": "write", "action": "unmute_member", "target_user_id": target_user_id}
        if "禁言" in text or re.search(r"\bmute\b", lowered):
            duration = self._parse_duration_seconds(text)
            if target_user_id is None or duration is None:
                return None
            return {
                "operation_type": "write",
                "action": "mute_member",
                "target_user_id": target_user_id,
                "duration_seconds": duration,
            }

        match = re.search(r"(?:设置|修改)群名(?:为|成)?\s*(.+)$", text)
        if match:
            return {"operation_type": "write", "action": "set_group_name", "text": match.group(1).strip()}
        match = re.search(r"(?:设置|修改).{0,20}群名片(?:为|成)?\s*(.+)$", text)
        if match and target_user_id is not None:
            return {
                "operation_type": "write",
                "action": "set_member_card",
                "target_user_id": target_user_id,
                "text": match.group(1).strip(),
            }
        match = re.search(r"(?:发布|发送|新增)群公告\s*(.+)$", text)
        if match:
            return {"operation_type": "write", "action": "publish_notice", "text": match.group(1).strip()}

        message_id = self._first_number(text)
        if any(keyword in text for keyword in ("取消精华", "删除精华")) and message_id:
            return {"operation_type": "write", "action": "delete_essence", "message_id": message_id}
        if any(keyword in text for keyword in ("设为精华", "设置精华")) and message_id:
            return {"operation_type": "write", "action": "set_essence", "message_id": message_id}
        if any(keyword in text for keyword in ("踢出", "移出群聊", "踢人")) and target_user_id is not None:
            return {
                "operation_type": "write",
                "action": "kick_member",
                "target_user_id": target_user_id,
                "reject_add_request": "禁止再加群" in text,
            }
        if "取消管理员" in text and target_user_id is not None:
            return {
                "operation_type": "write",
                "action": "set_admin",
                "target_user_id": target_user_id,
                "enable": False,
            }
        if "设置管理员" in text and target_user_id is not None:
            return {
                "operation_type": "write",
                "action": "set_admin",
                "target_user_id": target_user_id,
                "enable": True,
            }
        return None

    @staticmethod
    def _parse_slash_command(text: str) -> dict[str, Any] | None:
        """解析适合联调和高级用户使用的明确 `/group` 命令。"""
        match = re.fullmatch(r"/group\s+(info|members|notices|mutes|essence|files)", text, re.IGNORECASE)
        if not match:
            return None
        actions = {
            "info": "group_info",
            "members": "member_list",
            "notices": "notice_list",
            "mutes": "shut_list",
            "essence": "essence_list",
            "files": "file_list",
        }
        return {"operation_type": "read", "action": actions[match.group(1).lower()]}

    @staticmethod
    def _clean_text(text: str) -> str:
        """移除 CQ @ 片段和多余空白，保留用户真正的操作描述。"""
        without_cq = re.sub(r"\[CQ:at,qq=\d+\]", " ", text)
        return re.sub(r"\s+", " ", without_cq).strip()

    @staticmethod
    def _target_user_id(task_context: AgentTaskContext) -> int | None:
        """优先读取结构化 @，并排除机器人自身；找不到时才读取文本 QQ 号。"""
        event = task_context.event
        for mention in event.mentions:
            if mention != event.self_id and str(mention).isdigit():
                return int(mention)
        candidates = re.findall(r"(?<!\d)([1-9]\d{4,11})(?!\d)", event.text or "")
        return int(candidates[0]) if candidates else None

    @staticmethod
    def _parse_duration_seconds(text: str) -> int | None:
        """把秒、分钟、小时、天转换成 NapCat 使用的秒数，并限制在三十天内。"""
        match = re.search(r"(\d+)\s*(秒|分钟|分|小时|天)", text)
        if not match:
            return None
        value = int(match.group(1))
        multipliers = {"秒": 1, "分钟": 60, "分": 60, "小时": 3600, "天": 86400}
        return min(value * multipliers[match.group(2)], 2_592_000)

    @staticmethod
    def _first_number(text: str) -> int | None:
        """提取精华消息操作使用的消息 ID。"""
        match = re.search(r"(?<!\d)(\d+)(?!\d)", text)
        return int(match.group(1)) if match else None

    @staticmethod
    def _format_read_reply(action: str, response: dict[str, Any]) -> str:
        """把 NapCat 只读结果压缩成适合 QQ 的短回复。"""
        if response.get("status") != "ok":
            return f"群信息查询失败：{response.get('message') or 'NapCat 未返回成功状态'}"
        data = response.get("data")
        if action == "group_info" and isinstance(data, dict):
            name = data.get("group_name") or data.get("groupName") or "当前群"
            count = data.get("member_count") or data.get("memberCount") or "未知"
            maximum = data.get("max_member_count") or data.get("maxMemberCount") or "未知"
            return f"{name}，当前 {count}/{maximum} 人"
        if isinstance(data, list):
            labels = {
                "member_list": "群成员",
                "notice_list": "群公告",
                "shut_list": "禁言成员",
                "essence_list": "精华消息",
                "file_list": "群文件",
            }
            return f"查询到 {len(data)} 条{labels.get(action, '群数据')}"
        return "群信息查询完成"

    def _result(
        self,
        task_context: AgentTaskContext,
        reply: str,
        status: str,
    ) -> AgentResult:
        """构造不调用工具时的统一结果。"""
        return AgentResult(
            task_id=task_context.task_id,
            agent=self.name,
            status=status,
            structured_result={"groupOperationStatus": status},
            reply_draft=reply,
        )
