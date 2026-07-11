from __future__ import annotations

from pydantic import BaseModel, Field


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

    model_config = {
        "populate_by_name": True,
    }


class ConversationProfileMatchResult(BaseModel):
    matched: bool = False
    active: bool = False
    reason: str = ""
    profile: ConversationProfile | None = None
