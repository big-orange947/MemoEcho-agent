from __future__ import annotations

from pydantic import BaseModel, Field


class ResolvedUserModelProfile(BaseModel):
    id: str
    user_id: str = Field(alias="userId")
    name: str
    provider: str = "OPENAI_COMPATIBLE"
    base_url: str = Field(default="", alias="baseUrl")
    api_key: str = Field(default="", alias="apiKey")
    model: str = ""
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, alias="maxTokens")
    supported_routes: list[str] = Field(default_factory=list, alias="supportedRoutes")
    is_default: bool = Field(default=False, alias="isDefault")
    priority: int = 0

    model_config = {
        "populate_by_name": True,
    }


class UserModelProfileResolveResult(BaseModel):
    matched: bool = False
    reason: str = ""
    profile: ResolvedUserModelProfile | None = None

    model_config = {
        "populate_by_name": True,
    }
