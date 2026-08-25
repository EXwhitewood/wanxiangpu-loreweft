from typing import Literal

from pydantic import BaseModel, Field


APIFormat = Literal["openai_compatible", "anthropic_compatible"]


class APIConfig(BaseModel):
    api_format: APIFormat
    api_key: str
    base_url: str
    model: str


class GlobalSettings(BaseModel):
    openai_compatible: APIConfig | None = None
    anthropic_compatible: APIConfig | None = None


class AgentOverride(BaseModel):
    api_format: APIFormat | None = None
    api_key: str | None = None
    base_url: str | None = None
    model: str | None = None
    skills: list[str] | None = None
    agent_skills: list[str] | None = None
    persona: str | None = None


class AppSettings(BaseModel):
    global_: GlobalSettings = Field(alias="global", default=GlobalSettings())
    agent_overrides: dict[str, AgentOverride] = {}

    model_config = {"populate_by_name": True}
