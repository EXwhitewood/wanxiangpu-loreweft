from pydantic import BaseModel, Field


class FileSkillManifest(BaseModel):
    name: str
    display_name: str = ""
    description: str = ""
    permission: str = "readonly"
    version: str = "1"
    tags: list[str] = Field(default_factory=list)
    path: str = ""


class ToolPermissionDecision(BaseModel):
    allowed: bool
    permission: str
    reason: str = ""
