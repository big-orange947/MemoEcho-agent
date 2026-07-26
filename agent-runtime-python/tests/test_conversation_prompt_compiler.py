from app.services.conversation_prompt_compiler import ConversationPromptCompiler


def test_compile_profile_context_into_layered_prompt() -> None:
    """结构化字段应进入独立分区，业务目标不能被解释成工具授权。"""
    profile = {
        "allowedTools": ["send_qq_message"],
        "reviewMode": "STRICT_HANDOFF",
        "systemPrompt": "像熟人一样简短回复",
        "profileContext": {
            "version": 2,
            "identity": {
                "representedPerson": "freeze",
                "role": "账号主人本人",
                "speakingStyle": "简短自然",
                "forbiddenExpressions": ["我先确认一下"],
            },
            "counterparty": {
                "name": "小号",
                "relationship": "同学",
                "knownFacts": ["对方正在询问会员价格"],
                "trustLevel": "MEDIUM",
            },
            "task": {
                "objective": "完成会员交易",
                "successCriteria": ["价格不低于 40 元"],
                "prohibitedActions": ["未确认付款前交付卡密"],
            },
            "businessRules": {
                "pricingPolicy": "先报价 50 元",
                "minimumPrice": "40 元",
            },
            "assets": [{
                "assetId": "payment-wechat-01",
                "type": "PAYMENT_CODE",
                "name": "微信收款码",
                "usageCondition": "买家确认使用微信且审批通过后",
            }],
        },
    }

    prompt = ConversationPromptCompiler().compile(profile)

    assert "[我的身份]" in prompt
    assert "代表对象：freeze" in prompt
    assert "[对方资料]" in prompt
    assert "已知事实：对方正在询问会员价格" in prompt
    assert "[对话任务]" in prompt
    assert "最低价：40 元" in prompt
    assert "引用：payment-wechat-01" in prompt
    assert "当前工具白名单：send_qq_message" in prompt
    assert "资产只能按引用标识交给授权工具解析" in prompt
    assert "[补充人格提示]" in prompt


def test_compile_omits_empty_sections_and_legacy_prompt_when_disabled() -> None:
    """空结构不应制造无用标签，关闭旧提示时也不能把 systemPrompt 注入模型。"""
    prompt = ConversationPromptCompiler().compile(
        {"profileContext": {"version": 2}, "systemPrompt": "旧提示"},
        include_legacy_prompt=False,
    )

    assert "[我的身份]" not in prompt
    assert "[对话任务]" not in prompt
    assert "旧提示" not in prompt
    assert "Conversation Profile 2.0 执行边界" in prompt
