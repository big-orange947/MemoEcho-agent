from __future__ import annotations

from pydantic import BaseModel, Field


class SkillPromptFragments(BaseModel):
    system: str = ""

    model_config = {
        "populate_by_name": True,
    }


class SkillToolPolicy(BaseModel):
    allow: list[str] = []

    model_config = {
        "populate_by_name": True,
    }


class SkillModelHints(BaseModel):
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, alias="maxTokens")

    model_config = {
        "populate_by_name": True,
    }


class SkillDescriptor(BaseModel):
    id: str
    name: str
    version: str = "1.0.0"
    type: str = "prompt"
    description: str = ""
    source: str = "local"
    raw_reference: str = Field(default="", alias="rawReference")
    applicable_routes: list[str] = Field(default_factory=list, alias="applicableRoutes")
    prompt_fragments: SkillPromptFragments = Field(default_factory=SkillPromptFragments, alias="promptFragments")
    tool_policy: SkillToolPolicy = Field(default_factory=SkillToolPolicy, alias="toolPolicy")
    model_hints: SkillModelHints = Field(default_factory=SkillModelHints, alias="modelHints")

    model_config = {
        "populate_by_name": True,
    }
