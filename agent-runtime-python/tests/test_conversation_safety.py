from app.services.conversation_safety import ConversationSafetyGuard


def test_transaction_context_requires_handoff():
    """付款确认属于必须由用户接管的高风险场景。"""
    guard = ConversationSafetyGuard()
    decision = guard.evaluate("付过去了", [{"text": "一个月15，一年50"}], "收到了")
    assert decision.handoff_required is True
    assert "现实状态" in decision.reason


def test_hallucinated_phone_request_is_blocked():
    """即使输入没有手机号，模型候选回复主动索要手机号也必须被拦截。"""
    guard = ConversationSafetyGuard()
    decision = guard.evaluate("付过去了", [], "手机号发我一下")
    assert decision.handoff_required is True
    assert "敏感信息" in decision.reason


def test_low_risk_small_talk_can_continue():
    """普通闲聊不应被安全规则误拦截。"""
    guard = ConversationSafetyGuard()
    decision = guard.evaluate("明天一起吃饭吗", [], "可以啊")
    assert decision.handoff_required is False


def test_explicit_prompt_information_is_authorized():
    """用户提示词明确提供并授权的联系方式不应重复拦截。"""
    guard = ConversationSafetyGuard()
    decision = guard.evaluate(
        "联系方式是什么",
        [],
        "手机号是13800138000",
        authorized_context="客户询问时可以提供手机号13800138000",
    )
    assert decision.handoff_required is False


def test_unverifiable_state_cannot_be_authorized_by_generic_payment_prompt():
    """允许讨论付款方式不代表模型有权确认现实中的到账状态。"""
    guard = ConversationSafetyGuard()
    decision = guard.evaluate(
        "付过去了",
        [],
        "收到了",
        authorized_context="确定购买后可以使用微信付款",
    )
    assert decision.handoff_required is True
    assert "现实状态" in decision.reason


def test_uncertain_model_answer_requires_handoff():
    """模型明确表示不确定时不得把猜测作为用户回复发出。"""
    guard = ConversationSafetyGuard()
    decision = guard.evaluate("店里几点关门", [], "应该是晚上九点")
    assert decision.handoff_required is True
    assert "不确定" in decision.reason
