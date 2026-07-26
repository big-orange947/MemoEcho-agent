from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ConversationSafetyDecision:
    """描述自动回复是否必须中止并交给用户接管。"""

    handoff_required: bool
    reason: str = ""
    summary: str = ""
    progress: str = ""


class ConversationSafetyGuard:
    """以确定性规则拦截交易、隐私、授权和模型臆造，不能被人格提示词覆盖。"""

    TRANSACTION_WORDS = ("付款", "转账", "支付", "微信付", "支付宝", "收款", "价格", "元", "会员")
    UNVERIFIABLE_STATE_WORDS = ("付过去了", "已付款", "已经付款", "到账", "收到了", "已发货", "已签收", "已经办理")
    SENSITIVE_WORDS = ("手机号", "身份证", "银行卡", "验证码", "密码", "住址", "地址发我", "实名")
    COMMITMENT_WORDS = ("我答应", "我确认", "保证", "替你决定", "已经签", "同意购买")
    REAL_WORLD_ACTIONS = ("发货", "退款", "预约", "报名", "下单", "签合同", "开票", "线下", "见面", "到店")
    REAL_WORLD_FACTS = ("库存", "物流", "快递", "订单状态", "到账", "余额", "天气", "具体地址", "营业时间", "账号状态")
    UNCERTAINTY_CUES = ("不确定", "不知道", "不清楚", "可能", "应该是", "大概", "记不清", "需要核实", "需要确认一下")

    def evaluate(
        self,
        incoming_text: str,
        history: list[dict],
        proposed_reply: str = "",
        authorized_context: str = "",
    ) -> ConversationSafetyDecision:
        """同时检查对方消息、近期上下文和候选回复，返回不可绕过的接管决策。"""
        transcript = self._build_transcript(history, incoming_text)
        combined = f"{transcript}\n候选回复：{proposed_reply}".strip()
        reasons: list[str] = []
        if any(word in combined for word in self.UNVERIFIABLE_STATE_WORDS):
            reasons.append("涉及无法由模型核验的现实状态")
        if any(word in combined for word in self.TRANSACTION_WORDS) and not self._category_authorized(self.TRANSACTION_WORDS, authorized_context):
            reasons.append("涉及付款、价格或交易确认")
        if any(word in combined for word in self.SENSITIVE_WORDS) and not self._category_authorized(self.SENSITIVE_WORDS, authorized_context):
            reasons.append("涉及手机号、验证码或其他敏感信息")
        if any(word in proposed_reply for word in self.COMMITMENT_WORDS) and not self._category_authorized(self.COMMITMENT_WORDS, authorized_context):
            reasons.append("候选回复代表用户作出承诺或决定")
        if any(word in proposed_reply for word in self.REAL_WORLD_ACTIONS) and not self._category_authorized(self.REAL_WORLD_ACTIONS, authorized_context):
            reasons.append("候选回复将触发现实世界操作")
        if any(word in proposed_reply for word in self.REAL_WORLD_FACTS) and not self._category_authorized(self.REAL_WORLD_FACTS, authorized_context):
            reasons.append("候选回复涉及模型无法自行核验的现实信息")
        if any(word in proposed_reply for word in self.UNCERTAINTY_CUES):
            reasons.append("模型表达了不确定或需要核实")
        if self._contains_unsupported_number(proposed_reply, combined.removesuffix(f"\n候选回复：{proposed_reply}"), authorized_context):
            reasons.append("候选回复包含上下文中没有依据的新号码或数值")
        if not reasons:
            return ConversationSafetyDecision(False)
        return ConversationSafetyDecision(
            handoff_required=True,
            reason="；".join(dict.fromkeys(reasons)),
            summary=self._build_summary(history, incoming_text),
            progress=self._build_progress(history, incoming_text),
        )

    @staticmethod
    def _category_authorized(words: tuple[str, ...], authorized_context: str) -> bool:
        """只有用户提示词明确覆盖该类信息时才视为授权，系统安全提示不参与授权。"""
        normalized = authorized_context.strip()
        return bool(normalized) and any(word in normalized for word in words)

    @staticmethod
    def _contains_unsupported_number(proposed_reply: str, source_context: str, authorized_context: str) -> bool:
        """阻止模型凭空生成手机号、金额、日期或账号等现实数值。"""
        import re

        numbers = re.findall(r"\d{3,}", proposed_reply)
        evidence = f"{source_context}\n{authorized_context}"
        return any(number not in evidence for number in numbers)

    def _build_transcript(self, history: list[dict], incoming_text: str) -> str:
        """将安全判断所用上下文标注说话方，确保摘要和诊断不会混淆双方陈述。"""
        lines: list[str] = []
        for item in history:
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            speaker = "我" if str(item.get("role", "")) == "self" else "对方"
            lines.append(f"{speaker}：{text}")
        if incoming_text.strip():
            lines.append(f"对方：{incoming_text.strip()}")
        return "\n".join(lines)

    def _build_summary(self, history: list[dict], incoming_text: str) -> str:
        """生成接管卡片的一句话摘要，不让模型在高风险时继续自由发挥。"""
        recent = [
            f"{'我' if str(item.get('role', '')) == 'self' else '对方'}：{str(item.get('text', '')).strip()}"
            for item in history[-4:]
            if str(item.get("text", "")).strip()
        ]
        if incoming_text.strip():
            recent.append(f"对方：{incoming_text.strip()}")
        return "对话涉及交易或个人信息，需要你确认后继续。近期内容：" + " / ".join(recent[-4:])

    def _build_progress(self, history: list[dict], incoming_text: str) -> str:
        """提取聊天推进到了哪一步，帮助用户接管时快速恢复上下文。"""
        transcript = self._build_transcript(history, incoming_text)
        if any(word in transcript for word in ("付过去", "已付款", "转过去")):
            return "对方或本账号声称付款已完成，当前需要核验到账与后续交付信息。"
        if any(word in transcript for word in self.SENSITIVE_WORDS):
            return "对话正在索取个人信息，尚未获得用户授权。"
        return "对话已进入需要用户作出决定或提供授权的阶段。"
