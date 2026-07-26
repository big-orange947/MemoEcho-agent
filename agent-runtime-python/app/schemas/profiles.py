from __future__ import annotations

from pydantic import BaseModel, Field


class ProfileIdentity(BaseModel):
    """描述 Agent 所代表的用户身份和稳定表达边界。"""

    represented_person: str = Field(default="", alias="representedPerson")
    role: str = ""
    speaking_style: str = Field(default="", alias="speakingStyle")
    forbidden_expressions: list[str] = Field(default_factory=list, alias="forbiddenExpressions")


class ProfileCounterparty(BaseModel):
    """描述当前聊天对象的关系、已知事实和沟通偏好。"""

    name: str = ""
    identity: str = ""
    relationship: str = ""
    preferred_address: str = Field(default="", alias="preferredAddress")
    known_facts: list[str] = Field(default_factory=list, alias="knownFacts")
    trust_level: str = Field(default="UNKNOWN", alias="trustLevel")
    communication_preference: str = Field(default="", alias="communicationPreference")


class ProfileBackground(BaseModel):
    """描述会话起因、过去事件和当前进度。"""

    origin: str = ""
    previous_events: str = Field(default="", alias="previousEvents")
    current_progress: str = Field(default="", alias="currentProgress")


class ProfileTask(BaseModel):
    """描述代理目标、成功条件、截止时间和禁止事项。"""

    objective: str = ""
    success_criteria: list[str] = Field(default_factory=list, alias="successCriteria")
    deadline: str = ""
    prohibited_actions: list[str] = Field(default_factory=list, alias="prohibitedActions")


class ProfileBusinessRules(BaseModel):
    """描述报价、退款和交付规则，不代表已经授权执行工具。"""

    pricing_policy: str = Field(default="", alias="pricingPolicy")
    minimum_price: str = Field(default="", alias="minimumPrice")
    refund_policy: str = Field(default="", alias="refundPolicy")
    delivery_conditions: str = Field(default="", alias="deliveryConditions")
    hard_constraints: list[str] = Field(default_factory=list, alias="hardConstraints")


class ProfileAssetReference(BaseModel):
    """保存资产仓库引用；敏感资产正文不能进入 Profile 或系统提示词。"""

    asset_id: str = Field(default="", alias="assetId")
    type: str = ""
    name: str = ""
    description: str = ""
    usage_condition: str = Field(default="", alias="usageCondition")


class ProfileMemoryPolicy(BaseModel):
    """描述当前会话是否授权从账号主人真人消息中提取长期记忆候选。"""

    extraction_enabled: bool = Field(default=False, alias="extractionEnabled")


class ConversationProfileContext(BaseModel):
    """Conversation Profile 2.0 的版本化结构化业务上下文。"""

    version: int = 2
    identity: ProfileIdentity = Field(default_factory=ProfileIdentity)
    counterparty: ProfileCounterparty = Field(default_factory=ProfileCounterparty)
    background: ProfileBackground = Field(default_factory=ProfileBackground)
    task: ProfileTask = Field(default_factory=ProfileTask)
    business_rules: ProfileBusinessRules = Field(default_factory=ProfileBusinessRules, alias="businessRules")
    memory_policy: ProfileMemoryPolicy = Field(default_factory=ProfileMemoryPolicy, alias="memoryPolicy")
    assets: list[ProfileAssetReference] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class ConversationProfile(BaseModel):
    id: str
    name: str
    description: str = ""
    enabled: bool = True
    platform: str = ""
    account_id: str = Field(default="", alias="accountId")
    scene: str = ""
    chat_type: str = Field(default="", alias="chatType")
    chat_ids: list[str] = Field(default_factory=list, alias="chatIds")
    target_user_ids: list[str] = Field(default_factory=list, alias="targetUserIds")
    supported_routes: list[str] = Field(default_factory=list, alias="supportedRoutes")
    trigger_mode: str = Field(default="ALWAYS", alias="triggerMode")
    trigger_keywords: list[str] = Field(default_factory=list, alias="triggerKeywords")
    persona_mode: str = Field(default="NONE", alias="personaMode")
    system_prompt: str = Field(default="", alias="systemPrompt")
    skill_reference: str = Field(default="", alias="skillReference")
    skill_references: list[str] = Field(default_factory=list, alias="skillReferences")
    model_profile_id: str = Field(default="", alias="modelProfileId")
    preferred_route: str = Field(default="", alias="preferredRoute")
    reply_mode: str = Field(default="AUTO_REPLY", alias="replyMode")
    reply_delay_seconds_min: int | None = Field(default=None, alias="replyDelaySecondsMin")
    reply_delay_seconds_max: int | None = Field(default=None, alias="replyDelaySecondsMax")
    allowed_tools: list[str] = Field(default_factory=list, alias="allowedTools")
    require_human_confirmation: bool = Field(default=False, alias="requireHumanConfirmation")
    priority: int = 0
    notification_mode: str = Field(default="AUTO", alias="notificationMode")
    notification_keywords: list[str] = Field(default_factory=list, alias="notificationKeywords")
    digest_window_seconds: int | None = Field(default=None, alias="digestWindowSeconds")
    digest_max_messages: int | None = Field(default=None, alias="digestMaxMessages")
    include_urgent_in_digest: bool = Field(default=False, alias="includeUrgentInDigest")
    max_reply_chars: int = Field(default=24, alias="maxReplyChars")
    split_long_reply: bool = Field(default=True, alias="splitLongReply")
    split_reply_chance_percent: int = Field(default=33, alias="splitReplyChancePercent")
    private_history_enabled: bool = Field(default=False, alias="privateHistoryEnabled")
    history_max_messages: int = Field(default=12, alias="historyMaxMessages")
    history_max_chars: int = Field(default=2000, alias="historyMaxChars")
    history_training_enabled: bool = Field(default=False, alias="historyTrainingEnabled")
    review_mode: str = Field(default="STRICT_HANDOFF", alias="reviewMode")
    knowledge_base_sources: list[str] = Field(default_factory=list, alias="knowledgeBaseSources")
    public_knowledge_search_enabled: bool = Field(default=True, alias="publicKnowledgeSearchEnabled")
    profile_context: ConversationProfileContext = Field(
        default_factory=ConversationProfileContext,
        alias="profileContext",
    )

    model_config = {
        "populate_by_name": True,
    }


class ConversationProxyTaskState(BaseModel):
    """保存某个会话任务的持久化运行状态，客户端重启后仍能恢复。"""

    profile_id: str = Field(alias="profileId")
    profile_name: str = Field(default="", alias="profileName")
    platform: str = ""
    chat_type: str = Field(default="", alias="chatType")
    chat_id: str = Field(default="", alias="chatId")
    status: str = "ACTIVE"
    completion_summary: str = Field(default="", alias="completionSummary")
    completion_reason: str = Field(default="", alias="completionReason")
    completion_evidence: list[str] = Field(default_factory=list, alias="completionEvidence")
    requested_at: str | None = Field(default=None, alias="requestedAt")
    decided_at: str | None = Field(default=None, alias="decidedAt")
    updated_at: str | None = Field(default=None, alias="updatedAt")

    model_config = {"populate_by_name": True}


class ConversationProfileMatchResult(BaseModel):
    matched: bool = False
    active: bool = False
    reason: str = ""
    profile: ConversationProfile | None = None
    task_state: ConversationProxyTaskState | None = Field(default=None, alias="taskState")

    model_config = {"populate_by_name": True}
