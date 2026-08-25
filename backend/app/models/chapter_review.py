"""整章并行审校模型层。

定义 SceneReviewPacket / ChapterReviewCase / ChapterRepairPlan / ChapterRepairOrder，
支撑"整章草稿并行审校 + FBI 总案台集中修复"方案。
"""
from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# SceneReviewPacket — 单场景并行审校结果
# ---------------------------------------------------------------------------


class SceneReviewPacket(BaseModel):
    """单场景并行审校后的结构化结果。

    不提交状态，不改正文，只描述：
    - 该场景声称了什么
    - 该场景违反了什么
    - 该场景可能如何修复
    - 该场景影响哪些其他场景
    """

    scene_index: int
    text_hash: str = ""
    candidate_text: str = ""

    # 场景合同快照
    scene_contract: dict = Field(default_factory=dict)

    # 状态声明
    opening_state_claims: dict = Field(default_factory=dict)
    ending_state_claims: dict = Field(default_factory=dict)
    state_delta: dict = Field(default_factory=dict)

    # 事实声明
    worldview_claims: list[dict] = Field(default_factory=list)
    timeline_claims: list[dict] = Field(default_factory=list)
    character_state_claims: dict = Field(default_factory=dict)
    object_ownership_claims: list[dict] = Field(default_factory=list)
    foreshadowing_claims: list[dict] = Field(default_factory=list)

    # 质量门结果
    quality_gate_report: dict = Field(default_factory=dict)
    blocking_violations: list[dict] = Field(default_factory=list)
    advisory_violations: list[dict] = Field(default_factory=list)

    # 可修复性分类
    repairability: Literal[
        "clean",
        "auto_fixable",
        "cross_scene_fixable",
        "contract_repair_required",
        "human_review_required",
    ] = "clean"

    # 跨场景依赖
    dependencies: list[int] = Field(default_factory=list)
    affected_scenes: list[int] = Field(default_factory=list)

    # 审校元数据
    unavailable: bool = False
    unavailable_reason: str = ""
    review_version: int = 1


# ---------------------------------------------------------------------------
# ChapterReviewCase — FBI 总案台接收的全章案件
# ---------------------------------------------------------------------------


class ChapterReviewCase(BaseModel):
    """FBI 总案台接收的全章案件。"""

    project_id: str
    chapter_number: int
    chapter_initial_state: dict = Field(default_factory=dict)
    chapter_outline_contract: dict = Field(default_factory=dict)
    existing_worldview: dict = Field(default_factory=dict)

    scene_packets: list[SceneReviewPacket] = Field(default_factory=list)

    chapter_draft_hash: str = ""
    review_round: int = 0
    review_case_file: dict = Field(default_factory=dict)
    case_file_deltas: list[dict] = Field(default_factory=list)

    # 元数据
    case_id: str = ""
    created_at: str = ""

    def compute_case_id(self) -> str:
        """生成确定性 case_id。"""
        raw = f"{self.project_id}:{self.chapter_number}:{self.chapter_draft_hash}:{self.review_round}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# ChapterRepairOrder — 具体修复命令
# ---------------------------------------------------------------------------


class ChapterRepairOrder(BaseModel):
    """全章修复计划中的单条修复命令。"""

    order_id: str = ""
    target_scenes: list[int] = Field(default_factory=list)
    owner_scene: int | None = None

    repair_type: Literal[
        "local_patch",
        "contract_completion_patch",
        "scene_rewrite",
        "cross_scene_alignment",
        "contract_patch",
        "manual_review",
    ] = "local_patch"
    repair_domain: str = ""
    repair_lane: str = ""
    repair_strength: str = ""
    allowed_max_strength: str = ""
    candidate_attempt_budget: int = 1
    repair_brief: dict = Field(default_factory=dict)
    read_scope: list[str] = Field(default_factory=list)
    write_scope: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)

    priority: Literal["critical", "high", "medium", "low"] = "medium"
    reason: str = ""
    instruction: str = ""

    source_violation_ids: list[str] = Field(default_factory=list)
    source_goal_ids: list[str] = Field(default_factory=list)
    violation_details: list[dict] = Field(default_factory=list)
    expected_after_repair: dict = Field(default_factory=dict)

    # 执行状态
    status: Literal["pending", "running", "succeeded", "failed", "skipped"] = "pending"
    result_text_hash: str = ""
    repair_audit: dict = Field(default_factory=dict)
    work_unit_id: str = ""
    tool_commands: list[dict] = Field(default_factory=list)


class ToolCommand(BaseModel):
    """Deterministic text operation produced by FBI review/blueprint planning."""

    command_id: str = ""
    operation: Literal[
        "replace_exact",
        "delete_exact",
        "insert_before_anchor",
        "insert_after_anchor",
        "replace_span",
        "replace_literal",
        "insert_anchor",
        "insert_transition_anchor",
        "insert_time_anchor",
        "insert_pressure_cost",
        "insert_conflict_beat",
        "insert_hook_beat",
        "insert_micro_payoff",
        "delete_span",
        "rewrite_window",
        "split_paragraph",
        "merge_paragraphs",
        "normalize_punctuation",
        "normalize_structure_words",
        "replace_tier1_ai_flavor_terms",
        "cleanup_ai_flavor_window",
        "trim_discourse_window",
        "vary_sentence_shape",
        "vary_sentence_length_window",
        "insert_functional_breathing_paragraph",
        "smooth_abrupt_shift",
        "rewrite_voice_window",
        "ensure_required_spans",
        "suppress_false_positive",
        "mark_needs_human",
        "mark_unrepairable_by_tool",
        # 附录4问题16修复：LLM 创作型重写操作，用于 requires_llm=True 的修复路径
        "llm_creative_rewrite",
    ] = "replace_span"
    scene_index: int | None = None
    target_span: str = ""
    replacement: str = ""
    # Phase H deterministic patch protocol. Old target_span/replacement fields
    # remain for compatibility, but executable FBI tool commands should fill
    # these exact-patch fields before reaching ToolExecutor.
    anchor_text: str = ""
    anchor_occurrence: int = 1
    before_context: str = ""
    after_context: str = ""
    span_start: int | None = None
    span_end: int | None = None
    old_text: str = ""
    new_text: str = ""
    required_unique_anchor: bool = True
    expected_metric_delta: dict = Field(default_factory=dict)
    guards: dict = Field(default_factory=dict)
    before_span: str = ""
    after_span: str = ""
    window_start: str = ""
    window_end: str = ""
    paragraph_index: int | None = None
    merge_with_next: bool = False
    replace_all: bool = False
    max_replacements: int = 1
    rationale: str = ""
    repair_family: str = ""
    evidence: dict = Field(default_factory=dict)
    protection_policy: dict = Field(default_factory=dict)
    fallback: dict = Field(default_factory=dict)
    source_issue_ids: list[str] = Field(default_factory=list)
    preconditions: dict = Field(default_factory=dict)
    postconditions: dict = Field(default_factory=dict)


class ToolCommandBatch(BaseModel):
    """Commands that should be applied as one atomic work-unit candidate."""

    batch_id: str = ""
    source_blueprint_id: str = ""
    source_work_unit_id: str = ""
    commands: list[ToolCommand] = Field(default_factory=list)
    target_scenes: list[int] = Field(default_factory=list)
    source_order_ids: list[str] = Field(default_factory=list)
    acceptance_criteria: list[dict] = Field(default_factory=list)


class RepairWorkUnit(BaseModel):
    """A repair worker's edit window, independent from issue category."""

    work_unit_id: str = ""
    local_id: str = ""
    target_scenes: list[int] = Field(default_factory=list)
    owner_scene: int | None = None
    source_order_ids: list[str] = Field(default_factory=list)
    source_violation_ids: list[str] = Field(default_factory=list)
    source_goal_ids: list[str] = Field(default_factory=list)
    read_context_spans: list[dict] = Field(default_factory=list)
    diagnostic_evidence_spans: list[dict] = Field(default_factory=list)
    write_anchor: dict = Field(default_factory=dict)
    placement_status: str = ""
    edit_window: dict = Field(default_factory=dict)
    edit_window_id: str = ""
    base_scene_hash: str = ""
    compound_issue_ids: list[str] = Field(default_factory=list)
    compound_issue_families: list[str] = Field(default_factory=list)
    merge_policy: str = "single_patch"
    expected_metric_deltas: list[dict] = Field(default_factory=list)
    protection_boundary: dict = Field(default_factory=dict)
    max_delta_chars: int | None = None
    blueprint_source: str = ""
    issue_summary: str = ""
    tool_batch: ToolCommandBatch = Field(default_factory=ToolCommandBatch)
    dependencies: list[str] = Field(default_factory=list)
    validator_snapshot: dict = Field(default_factory=dict)
    status: Literal["pending", "running", "succeeded", "failed", "skipped"] = "pending"
    result_audit: dict = Field(default_factory=dict)


class EditWindow(BaseModel):
    """Frozen-base edit window used by the FBI staged patch chain."""

    window_id: str = ""
    scene_index: int | None = None
    base_text_hash: str = ""
    issue_ids: list[str] = Field(default_factory=list)
    issue_families: list[str] = Field(default_factory=list)
    paragraph_index: int | None = None
    sentence_index: int | None = None
    old_text: str = ""
    before_context: str = ""
    after_context: str = ""
    anchor_text: str = ""
    window_start: int | None = None
    window_end: int | None = None
    merge_policy: str = "compound_patch"
    risk_level: str = "normal"


class EditWindowCase(BaseModel):
    """Phase U-A: 同窗口问题聚合的最小单位。

    一个 EditWindowCase = 一个正文窗口 + 多个审查问题 + 一个统一修订目标。
    在蓝图生成前完成聚合，禁止同窗口多个独立 work unit 串行修改。
    """

    window_id: str = ""
    scene_index: int | None = None
    base_text_hash: str = ""
    window_start: int | None = None
    window_end: int | None = None
    window_text: str = ""
    issue_ids: list[str] = Field(default_factory=list)
    metrics: list[str] = Field(default_factory=list)
    issue_families: list[str] = Field(default_factory=list)
    source_order_ids: list[str] = Field(default_factory=list)
    protected_context: dict = Field(default_factory=dict)
    review_notes: list[str] = Field(default_factory=list)
    compound_blueprint: dict = Field(default_factory=dict)
    blocking_level: str = "blocking"


class FrozenBaseRepairSession(BaseModel):
    """A repair run where every tool works against the same base revision."""

    session_id: str = ""
    case_id: str = ""
    review_round: int = 0
    base_scene_texts: dict[int, str] = Field(default_factory=dict)
    base_scene_hashes: dict[int, str] = Field(default_factory=dict)
    work_units: list[RepairWorkUnit] = Field(default_factory=list)
    patches: list[dict] = Field(default_factory=list)
    merge_result: dict = Field(default_factory=dict)
    apply_result: dict = Field(default_factory=dict)
    trace: dict = Field(default_factory=dict)


class FBIReviewBlueprintWorkUnit(BaseModel):
    """Agent-facing blueprint unit before it is compiled into tool commands.

    This model is intentionally not executable. ToolExecutor may only receive
    ToolCommandBatch after protocol validation and WorkUnitBuilder compilation.
    """

    local_id: str = ""
    target_scenes: list[int] = Field(default_factory=list)
    owner_scene: int | None = None
    source_issue_ids: list[str] = Field(default_factory=list)
    source_order_ids: list[str] = Field(default_factory=list)
    problem_summary: str = ""
    repair_goal: str = ""
    operation_plan: list[dict] = Field(default_factory=list)
    locator: dict = Field(default_factory=dict)
    constraints: dict = Field(default_factory=dict)
    protection_policy: dict = Field(default_factory=dict)
    acceptance_criteria: list[dict] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)
    max_delta_chars: int | None = None


class FBIReviewBlueprint(BaseModel):
    """Review blueprint produced by FBIReviewBlueprintAgent.

    It is a planning artifact for validators/builders/audits, not the direct
    input of ToolExecutor.
    """

    blueprint_id: str = ""
    case_id: str = ""
    work_units: list[FBIReviewBlueprintWorkUnit] = Field(default_factory=list)
    context_trace: list[dict] = Field(default_factory=list)
    planning_trace: list[dict] = Field(default_factory=list)
    status: Literal["empty", "ready", "degraded", "invalid"] = "empty"


class RevisionBlueprint(BaseModel):
    """FBI review/blueprint output consumed by deterministic repair workers."""

    blueprint_id: str = ""
    case_id: str = ""
    work_units: list[RepairWorkUnit] = Field(default_factory=list)
    planning_trace: list[dict] = Field(default_factory=list)
    validator_snapshot: dict = Field(default_factory=dict)
    completion_summary: dict = Field(default_factory=dict)
    status: Literal["empty", "ready", "degraded"] = "empty"


# ---------------------------------------------------------------------------
# ChapterRepairPlan — FBI 总案台输出的全章修复计划
# ---------------------------------------------------------------------------


class ChapterRepairPlan(BaseModel):
    """FBI 总案台输出的全章修复计划。"""

    case_id: str = ""
    status: Literal[
        "clean",
        "needs_repair",
        "needs_contract_repair",
        "needs_human_review",
        "failed",
    ] = "clean"

    orders: list[ChapterRepairOrder] = Field(default_factory=list)
    global_notes: list[str] = Field(default_factory=list)

    # 去重后的问题摘要
    total_violations: int = 0
    blocking_violations: int = 0
    cross_scene_conflicts: int = 0
    execution_layers: list[list[str]] = Field(default_factory=list)
    execution_scope_trace: list[dict] = Field(default_factory=list)
    repair_strategy_summary: dict = Field(default_factory=dict)
    repair_execution_summary: dict = Field(default_factory=dict)
    revision_blueprint: RevisionBlueprint = Field(default_factory=RevisionBlueprint)
    work_units: list[RepairWorkUnit] = Field(default_factory=list)
    repair_goals: list[dict] = Field(default_factory=list)

    # 审计
    review_round: int = 0
    created_at: str = ""

    def is_clean(self) -> bool:
        return self.status == "clean"

    def get_orders_for_scene(self, scene_index: int) -> list[ChapterRepairOrder]:
        """获取指定场景的修复命令。"""
        return [o for o in self.orders if scene_index in o.target_scenes]

    def get_parallel_groups(self) -> list[list[ChapterRepairOrder]]:
        """将修复命令按 target_scenes 互不重叠原则分组，同组可并行。"""
        groups: list[list[ChapterRepairOrder]] = []
        for order in self.orders:
            if order.status in ("succeeded", "skipped"):
                continue
            placed = False
            for group in groups:
                occupied = set()
                for o in group:
                    occupied.update(o.target_scenes)
                if not set(order.target_scenes) & occupied:
                    group.append(order)
                    placed = True
                    break
            if not placed:
                groups.append([order])
        return groups
