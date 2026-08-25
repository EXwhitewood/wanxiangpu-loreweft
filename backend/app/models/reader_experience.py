from pydantic import BaseModel, Field


class ReaderExperienceScores(BaseModel):
    continue_reading: int = Field(default=0, ge=0, le=10)
    clarity: int = Field(default=0, ge=0, le=10)
    cognitive_load: int = Field(default=0, ge=0, le=10)
    emotional_engagement: int = Field(default=0, ge=0, le=10)
    pacing: int = Field(default=0, ge=0, le=10)


class ReaderExperienceReport(BaseModel):
    schema_version: int = 1
    status: str = "ok"
    scores: ReaderExperienceScores = Field(default_factory=ReaderExperienceScores)
    confusions: list[str] = Field(default_factory=list)
    drop_points: list[str] = Field(default_factory=list)
    advisories: list[dict] = Field(default_factory=list)
    degraded: bool = False
    error: str = ""
