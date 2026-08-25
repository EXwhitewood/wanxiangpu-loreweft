"""修复工单模型。

FBI 不再直接消费散乱 issue，而是消费 RepairOrder。
每个 RepairOrder 是一个可执行、可验证、可回滚的修复任务。
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Literal


class RepairOrder(BaseModel):
    """修复工单"""
    id: str = ""
    issue_ids: list[str] = Field(default_factory=list)
    lane: str = "manual_only"
    target_scope: str = "prose_text"
    target_region: dict | None = None
    operation: Literal[
        "replace_span",
        "insert_after",
        "delete_span",
        "compress_region",
        "clean_punctuation",
        "remove_repetition",
        "repair_time_anchor",
        "remove_forbidden_event",
        "complete_ending_state",
        "complete_contract_item",
        "repair_fact_state",
        "cleanup_ai_style",
        "expand_region",
        "append_tail",
        "insert_bridge_action",
        "insert_bridge_hint",
        "rewrite_window",
        "rewrite_scene",
        "adjust_contract",
        "acknowledge",
    ] = "replace_span"
    instruction: str = ""
    allowed_delta_chars: int = 200
    protected_spans: list[dict] = Field(default_factory=list)
    validator: str = ""
    max_attempts: int = 3
    status: Literal[
        "pending",
        "running",
        "succeeded",
        "failed",
        "skipped",
        "needs_human",
    ] = "pending"
    attempts: int = 0
    result: str = ""
    patches: list[dict] = Field(default_factory=list)

    def is_deterministic(self) -> bool:
        """是否是确定性修复（不需要 LLM）"""
        return self.lane == "deterministic" or self.operation in {
            "acknowledge",
            "adjust_contract",
            "compress_region",
            "clean_punctuation",
            "remove_repetition",
            "repair_time_anchor",
            "remove_forbidden_event",
            "complete_ending_state",
            "complete_contract_item",
            "repair_fact_state",
            "cleanup_ai_style",
            "delete_span",
        }

    def needs_llm(self) -> bool:
        """是否需要 LLM 修复"""
        return self.lane.startswith("fbi_")

    def can_retry(self) -> bool:
        """是否可以重试"""
        return self.attempts < self.max_attempts and self.status in ("pending", "failed")

    def mark_running(self):
        self.status = "running"
        self.attempts += 1

    def mark_succeeded(self, patches: list[dict] | None = None):
        self.status = "succeeded"
        if patches:
            self.patches = patches

    def mark_failed(self, reason: str = ""):
        self.result = reason
        if self.attempts >= self.max_attempts:
            self.status = "needs_human"
        else:
            self.status = "failed"

    def mark_skipped(self, reason: str = ""):
        self.status = "skipped"
        self.result = reason
