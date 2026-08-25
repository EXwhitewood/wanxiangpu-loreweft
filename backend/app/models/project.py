import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


GenreProfileId = Literal[
    "general",
    "xianxia",
    "mystery",
    "romance",
    "scifi",
    "historical",
]


class ProjectBase(BaseModel):
    name: str
    description: str = ""
    genre: str = ""
    word_count_target: int = 100000


class ProjectCreate(ProjectBase):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=1000)
    genre: str = Field(default="", max_length=100)
    word_count_target: int = Field(default=100000, ge=10000, le=5000000)
    genre_profile_id: GenreProfileId | None = None

    @field_validator("name", "description", "genre", mode="before")
    @classmethod
    def normalize_text_fields(cls, value: object) -> str:
        if value is None:
            return ""
        return str(value).strip()

    @field_validator("genre_profile_id", mode="before")
    @classmethod
    def normalize_genre_profile_id(cls, value: object) -> object:
        if isinstance(value, str):
            value = value.strip().lower()
            return value or None
        return value


class ProjectResponse(ProjectBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    current_chapter: int = 1
    total_words: int = 0
    outline_data: dict | None = None
    core_data: dict | None = None
    outline_index: dict | None = None
    outline_version: int = 0

    model_config = {"from_attributes": True}


class ProjectSummary(ProjectBase):
    """Lightweight project-list contract; large narrative documents stay on detail APIs."""

    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    current_chapter: int = 1
    total_words: int = 0

    model_config = {"from_attributes": True}
