"""统一审查发现 V2 模型。

所有 checker、Deslop Gate、FBI 复检、人工反馈都输出统一 finding。

关键规则：
- advisory 不进入修复管线
- scene_contract 问题不能送去正文改写
- style_policy 问题必须经风格守门员
- S1 必须阻断
- S4 默认不阻断
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Literal


class ReviewFindingV2(BaseModel):
    """统一审查发现 V2"""
    id: str = ""
    source: str = ""
    type: str = ""
    title: str = ""
    description: str = ""
    severity: Literal["S1", "S2", "S3", "S4"] = "S4"
    blocks_commit: bool = False
    user_visible: bool = True
    repair_scope: Literal[
        "prose_text",
        "scene_contract",
        "chapter_contract",
        "ledger",
        "style_policy",
        "validator_system",
        "advisory",
    ] = "prose_text"
    repair_lane: Literal[
        "deterministic",
        "fbi_fact",
        "fbi_prose",
        "fbi_pacing",
        "fbi_style_guard",
        "fbi_scene_restructure",
        "fbi_ending",
        "manual_only",
        "none",
    ] = "none"
    evidence_spans: list[dict] = Field(default_factory=list)
    repair_intent: dict = Field(default_factory=dict)
    validator: str = ""
    retryable: bool = True
    max_attempts: int = 3

    def is_actionable(self) -> bool:
        """是否可操作（advisory 和 none lane 不可操作）"""
        return self.repair_scope not in ("advisory", "validator_system") and self.repair_lane != "none"

    def needs_text_repair(self) -> bool:
        """是否需要正文修复"""
        return self.repair_scope == "prose_text" and self.repair_lane not in ("none", "manual_only")

    def needs_contract_repair(self) -> bool:
        """是否需要合同修复"""
        return self.repair_scope in ("scene_contract", "chapter_contract")
