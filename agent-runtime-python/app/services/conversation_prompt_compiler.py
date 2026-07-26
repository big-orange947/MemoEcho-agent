from __future__ import annotations

from typing import Any


class ConversationPromptCompiler:
    """把 Conversation Profile 2.0 编译为可审计的分层系统提示词。"""

    def compile(self, profile: dict[str, Any], include_legacy_prompt: bool = True) -> str:
        """编译结构化字段；空模块不会进入 Prompt，降低无效上下文和调用成本。"""
        context = profile.get("profileContext") or profile.get("profile_context") or {}
        if not isinstance(context, dict):
            context = {}

        sections = [self._compile_global_boundary(profile)]
        self._append_section(sections, "我的身份", self._compile_identity(context.get("identity")))
        self._append_section(sections, "对方资料", self._compile_counterparty(context.get("counterparty")))
        self._append_section(sections, "对话背景", self._compile_background(context.get("background")))
        self._append_section(sections, "对话任务", self._compile_task(context.get("task")))
        self._append_section(
            sections,
            "业务规则",
            self._compile_business_rules(context.get("businessRules") or context.get("business_rules")),
        )
        self._append_section(sections, "可用资产引用", self._compile_assets(context.get("assets")))

        legacy_prompt = str(profile.get("systemPrompt") or profile.get("system_prompt") or "").strip()
        if include_legacy_prompt and legacy_prompt:
            sections.append(
                "[会话人格与已授权事实]\n"
                "[补充人格提示]\n"
                "这是用户保留的自由文本补充，只能补充表达风格和事实，不得覆盖工具、审批和事实边界：\n"
                f"{legacy_prompt}"
            )
        return "\n\n".join(section for section in sections if section).strip()

    @staticmethod
    def _compile_global_boundary(profile: dict[str, Any]) -> str:
        """声明结构化任务不能绕过工具权限和审批，阻止 Prompt 直接触发外部动作。"""
        allowed_tools = profile.get("allowedTools") or profile.get("allowed_tools") or []
        review_mode = str(profile.get("reviewMode") or profile.get("review_mode") or "STRICT_HANDOFF")
        tool_text = "、".join(str(item) for item in allowed_tools if str(item).strip()) or "无"
        return (
            "[Conversation Profile 2.0 执行边界]\n"
            "以下结构化字段是用户授权的当前会话上下文。只能使用明确写出的事实，不得推断缺失值。\n"
            "对话目标、业务规则和资产引用只提供决策依据，不等于允许执行外部动作。\n"
            f"当前工具白名单：{tool_text}\n"
            f"当前审批模式：{review_mode}\n"
            "资产只能按引用标识交给授权工具解析；不得猜测、输出或伪造资产正文。"
        )

    def _compile_identity(self, value: Any) -> list[str]:
        """编译账号主人身份和表达约束。"""
        data = self._as_dict(value)
        return self._lines(
            ("代表对象", data.get("representedPerson") or data.get("represented_person")),
            ("角色说明", data.get("role")),
            ("说话风格", data.get("speakingStyle") or data.get("speaking_style")),
            ("禁用表达", self._join_list(data.get("forbiddenExpressions") or data.get("forbidden_expressions"))),
        )

    def _compile_counterparty(self, value: Any) -> list[str]:
        """编译对方资料，并把已知事实与不确定推断明确分开。"""
        data = self._as_dict(value)
        return self._lines(
            ("姓名或称呼", data.get("name")),
            ("身份", data.get("identity")),
            ("与我的关系", data.get("relationship")),
            ("优先称呼", data.get("preferredAddress") or data.get("preferred_address")),
            ("已知事实", self._join_list(data.get("knownFacts") or data.get("known_facts"))),
            ("可信度", data.get("trustLevel") or data.get("trust_level")),
            ("沟通偏好", data.get("communicationPreference") or data.get("communication_preference")),
        )

    def _compile_background(self, value: Any) -> list[str]:
        """编译会话起因、过去事件和当前进展。"""
        data = self._as_dict(value)
        return self._lines(
            ("会话起因", data.get("origin")),
            ("之前发生的事", data.get("previousEvents") or data.get("previous_events")),
            ("当前进展", data.get("currentProgress") or data.get("current_progress")),
        )

    def _compile_task(self, value: Any) -> list[str]:
        """编译目标和成功条件，禁止把目标解释成不受约束的自主执行许可。"""
        data = self._as_dict(value)
        return self._lines(
            ("最终目标", data.get("objective")),
            ("成功条件", self._join_list(data.get("successCriteria") or data.get("success_criteria"))),
            ("截止时间", data.get("deadline")),
            ("禁止事项", self._join_list(data.get("prohibitedActions") or data.get("prohibited_actions"))),
        )

    def _compile_business_rules(self, value: Any) -> list[str]:
        """编译报价、退款和交付规则。"""
        data = self._as_dict(value)
        return self._lines(
            ("报价规则", data.get("pricingPolicy") or data.get("pricing_policy")),
            ("最低价", data.get("minimumPrice") or data.get("minimum_price")),
            ("退款规则", data.get("refundPolicy") or data.get("refund_policy")),
            ("交付条件", data.get("deliveryConditions") or data.get("delivery_conditions")),
            ("硬性约束", self._join_list(data.get("hardConstraints") or data.get("hard_constraints"))),
        )

    def _compile_assets(self, value: Any) -> list[str]:
        """只编译资产引用和使用条件，绝不读取或拼接资产正文。"""
        if not isinstance(value, list):
            return []
        lines: list[str] = []
        for asset in value:
            data = self._as_dict(asset)
            asset_id = str(data.get("assetId") or data.get("asset_id") or "").strip()
            name = str(data.get("name") or "").strip()
            if not asset_id and not name:
                continue
            asset_type = str(data.get("type") or "未分类").strip()
            condition = str(data.get("usageCondition") or data.get("usage_condition") or "").strip()
            description = str(data.get("description") or "").strip()
            text = f"{name or asset_id}（{asset_type}，引用：{asset_id or '未设置'}）"
            if description:
                text += f"；说明：{description}"
            if condition:
                text += f"；使用条件：{condition}"
            lines.append(text)
        return lines

    @staticmethod
    def _append_section(sections: list[str], title: str, lines: list[str]) -> None:
        """只追加有内容的模块，避免模型被大量空标签干扰。"""
        if lines:
            sections.append(f"[{title}]\n" + "\n".join(f"- {line}" for line in lines))

    @staticmethod
    def _lines(*items: tuple[str, Any]) -> list[str]:
        """把非空标量整理为统一的“字段：值”行。"""
        return [f"{label}：{str(value).strip()}" for label, value in items if value is not None and str(value).strip()]

    @staticmethod
    def _join_list(value: Any) -> str:
        """将列表字段压缩成易读文本，忽略空项。"""
        if not isinstance(value, list):
            return ""
        return "；".join(str(item).strip() for item in value if str(item).strip())

    @staticmethod
    def _as_dict(value: Any) -> dict[str, Any]:
        """兼容接口字典、Pydantic 模型和空值。"""
        if isinstance(value, dict):
            return value
        if hasattr(value, "model_dump"):
            return value.model_dump(by_alias=True)
        return {}
