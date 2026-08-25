from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# 基础类型
# ---------------------------------------------------------------------------


class TextSpan(BaseModel):
    """文本区间，用字符偏移量表示。"""

    start: int = Field(..., description="起始字符偏移（含）")
    end: int = Field(..., description="结束字符偏移（不含）")
    label: str = Field(default="", description="区间标签")


# ---------------------------------------------------------------------------
# 修复问题
# ---------------------------------------------------------------------------


class RepairIssue(BaseModel):
    """FBI 层级违规在修复流水线中的标准化表示。"""

    issue_id: str
    source_layer: Literal[
        "hard_correctness",
        "narrative_proposition",
        "consistency",
        "foreshadowing",
        "style",
        "reader_experience",
        "literary_quality",
        "commercial_pacing",
        "fact_boundary",
        "knowledge_boundary",
        "plausibility",
        "narration_credibility",
        "ai_flavor",
        "ai_discourse",
        "character_voice",
        "voice_fingerprint",
        "custom",
    ]
    violation_type: str
    severity: Literal["info", "minor", "major", "blocking"]
    repairability: Literal["auto", "assisted", "rewrite_required", "manual_only"]
    span: TextSpan | None = None
    evidence: str = ""
    repair_goal: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    blocks_commit: bool = True
    scope: Literal["prose_text", "scene_contract", "outline_plan", "style_profile", "advisory"] = "prose_text"
    repairable_by_text: bool = True
    repairable_by_contract: bool = False
    classification: str = ""
    authority_source: str = ""
    recommended_route: str = ""
    obligation_id: str = ""
    obligation_scope: Literal["", "scene", "chapter"] = ""
    obligation_owner_scene: int | None = None
    obligation_binding_status: str = ""
    obligation_ownership_explicit: bool = False


# ---------------------------------------------------------------------------
# 修复上下文
# ---------------------------------------------------------------------------


class RepairContextBundle(BaseModel):
    """修复执行时所需的全部上下文快照。"""

    scene_contract: dict = Field(default_factory=dict)
    fact_contract: dict = Field(default_factory=dict)
    scene_credibility_contract: dict = Field(default_factory=dict)
    propositions: list[dict] = Field(default_factory=list)
    character_cards: list[dict] = Field(default_factory=list)
    style_profile: dict = Field(default_factory=dict)
    style_frozen_dimensions: list[str] = Field(default_factory=list)
    scene_provenance: dict = Field(default_factory=dict)
    outline_beat: dict = Field(default_factory=dict)
    foreshadowing_ops: list[dict] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 修复策略
# ---------------------------------------------------------------------------


class FBIRepairPolicy(BaseModel):
    """单次修复任务的策略控制参数。"""

    mode: Literal["off", "shadow", "assist", "auto", "enforce"] = "assist"
    patch_mode: Literal["patch_only", "scoped_rewrite", "allow_scene_rewrite"] = "patch_only"
    allow_scene_rewrite: bool = False
    max_attempts: int = 3
    max_changed_ratio: float = 0.3
    preserve_facts: bool = True
    preserve_style_freeze: bool = True
    auto_recheck: bool = True
    frontend_workbench_enabled: bool = False
    repair_memory_enabled: bool = False


# ---------------------------------------------------------------------------
# 修复案件
# ---------------------------------------------------------------------------


class FBIRepairCase(BaseModel):
    """FBI 修复案件——一次完整修复流程的顶层实体。"""

    case_id: str
    project_id: str
    chapter_id: str | None = None
    scene_id: str | None = None
    workflow_execution_id: str | None = None
    source: Literal[
        "quality_gate",
        "scene_recovery",
        "frontend_workbench",
        "inline_generation",
        "manual_user_request",
    ]
    status: Literal[
        "pending",
        "classifying",
        "repairing",
        "validating",
        "resolved",
        "partial",
        "failed",
        "needs_human",
        "needs_rewrite_approval",
    ]
    base_text_hash: str = ""
    current_text_hash: str = ""
    issues: list[RepairIssue] = Field(default_factory=list)
    context_bundle: RepairContextBundle = Field(default_factory=RepairContextBundle)
    policy: FBIRepairPolicy = Field(default_factory=FBIRepairPolicy)
    created_at: str = ""
    updated_at: str = ""


# ---------------------------------------------------------------------------
# 修复意图
# ---------------------------------------------------------------------------


class RepairIntent(BaseModel):
    """由分类器生成的修复意图，指导具体修复操作。"""

    intent_id: str
    case_id: str
    issue_ids: list[str] = Field(default_factory=list)
    repair_class: Literal[
        "fact_alignment",
        "causal_alignment",
        "prose_de_ai",
        "paragraph_reconstruction",
        "style_preservation",
        "voice_alignment",
        "voice_reconstruction",
        "pacing_enhancement",
        "reader_hook",
        "scene_restructure",
        "knowledge_boundary",
        "cognitive_boundary",
        "length_compress",
        "length_expand",
        "ending_completion",
        "contract_budget_adjust",
    ]
    scope: Literal["phrase", "sentence", "paragraph", "multi_paragraph", "scene"]
    allowed_operations: list[
        Literal[
            "replace_phrase",
            "rewrite_sentence",
            "rewrite_paragraph",
            "insert_bridge",
            "delete_redundancy",
            "reorder_micro_beats",
            "scene_rewrite",
            "append_tail",
            "compress_paragraph",
            "expand_paragraph",
            "adjust_contract",
            "replace_tier1_ai_flavor_terms",
            "cleanup_ai_flavor_window",
            "trim_discourse_window",
            "vary_sentence_shape",
            "vary_sentence_length_window",
            "insert_transition_anchor",
            "insert_time_anchor",
            "insert_pressure_cost",
            "insert_conflict_beat",
            "insert_hook_beat",
            "insert_micro_payoff",
            "insert_functional_breathing_paragraph",
            "smooth_abrupt_shift",
            "rewrite_voice_window",
            "split_paragraph",
            "normalize_punctuation",
            "normalize_structure_words",
            "replace_exact",
            "delete_exact",
            "insert_before_anchor",
            "insert_after_anchor",
        ]
    ] = Field(default_factory=list)
    must_preserve: list[str] = Field(default_factory=list)
    must_avoid: list[str] = Field(default_factory=list)
    source_of_truth_refs: list[str] = Field(default_factory=list)
    style_constraints: dict = Field(default_factory=dict)
    target_checker_ids: list[str] = Field(default_factory=list)
    evidence: str = ""
    repair_goal: str = ""
    target_span: str = ""


# ---------------------------------------------------------------------------
# 修复补丁
# ---------------------------------------------------------------------------


class RepairPatch(BaseModel):
    """一次具体的文本替换操作。"""

    patch_id: str
    case_id: str
    intent_id: str
    base_text_hash: str
    span: TextSpan
    original_text: str
    replacement_text: str
    changed_chars: int = 0
    strategy: str = ""
    resolves_issue_ids: list[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "low"
    self_audit: dict = Field(default_factory=dict)
    may_affect_issue_ids: list[str] = Field(default_factory=list)
    touches_protected_span_ids: list[str] = Field(default_factory=list)
    requires_style_guard: bool = False
    requires_full_recheck: bool = False


# ---------------------------------------------------------------------------
# 修复结果
# ---------------------------------------------------------------------------


class RepairOutcome(BaseModel):
    """修复流程的最终输出。"""

    case_id: str
    status: Literal[
        "resolved",
        "partial",
        "failed",
        "needs_human",
        "needs_rewrite_approval",
    ]
    new_text: str = ""
    new_text_hash: str = ""
    applied_patches: list[RepairPatch] = Field(default_factory=list)
    contract_patches: list[dict] = Field(default_factory=list)
    resolved_issue_ids: list[str] = Field(default_factory=list)
    unresolved_issue_ids: list[str] = Field(default_factory=list)
    new_issue_ids: list[str] = Field(default_factory=list)
    validation_report: dict = Field(default_factory=dict)
    user_visible_summary: str = ""
    internal_debug: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 已解决问题记录
# ---------------------------------------------------------------------------


class ResolvedIssueRecord(BaseModel):
    """已解决问题的冻结记录。"""
    issue_id: str
    case_id: str
    run_id: str = ""
    round_index: int = 0
    resolved_by_specialist: str = ""
    patch_ids: list[str] = Field(default_factory=list)
    pre_text_hash: str = ""
    post_text_hash: str = ""
    validation_checker_ids: list[str] = Field(default_factory=list)
    validation_report: dict = Field(default_factory=dict)
    resolved_at: str = ""


# ---------------------------------------------------------------------------
# 受保护文本片段
# ---------------------------------------------------------------------------


class ProtectedSpan(BaseModel):
    """受保护文本片段——已通过验收的修复结果对应文本不得被后续低优先级专家修改。"""
    span_id: str
    case_id: str
    start: int
    end: int
    text_hash: str = ""
    reason: Literal[
        "resolved_issue",
        "style_frozen",
        "fact_anchor",
        "user_locked",
        "upstream_contract",
    ]
    owner_issue_ids: list[str] = Field(default_factory=list)
    allowed_touch_by: list[str] = Field(default_factory=list)
    expires_at_round: int | None = None


# ---------------------------------------------------------------------------
# 冻结判定
# ---------------------------------------------------------------------------


class FreezeDecision(BaseModel):
    """冻结判定结果。"""
    patch_id: str
    allowed: bool
    blocked_by_span_ids: list[str] = Field(default_factory=list)
    reason: str = ""


# ---------------------------------------------------------------------------
# 自动修复运行状态
# ---------------------------------------------------------------------------


class AutoRepairRunState(BaseModel):
    """自动修复运行状态——三轮状态机的持久化表示。"""
    run_id: str
    case_id: str
    project_id: str
    chapter_id: str | None = None
    scene_id: str | None = None
    status: Literal[
        "pending",
        "normalizing",
        "planning",
        "repairing",
        "validating",
        "rechecking",
        "freezing",
        "resolved",
        "partial",
        "needs_workbench",
        "failed",
    ] = "pending"
    round_index: int = 0
    max_rounds: int = 3
    current_text_hash: str = ""
    unresolved_issue_ids: list[str] = Field(default_factory=list)
    resolved_issue_ids: list[str] = Field(default_factory=list)
    protected_spans: list[ProtectedSpan] = Field(default_factory=list)
    last_failure_reason: str = ""
    created_at: str = ""
    updated_at: str = ""


# ---------------------------------------------------------------------------
# 风格守门员决策
# ---------------------------------------------------------------------------


class StyleGuardDecision(BaseModel):
    """风格守门员决策。"""
    patch_id: str
    decision: Literal["approve", "reject", "needs_rewrite"] = "approve"
    violated_dimensions: list[str] = Field(default_factory=list)
    reason: str = ""
    suggested_constraints: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 单轮修复结果
# ---------------------------------------------------------------------------


class RepairRoundResult(BaseModel):
    """单轮修复结果。"""
    round_index: int
    status: Literal["success", "partial", "failed"] = "failed"
    patches_applied: int = 0
    issues_resolved: list[str] = Field(default_factory=list)
    issues_remaining: list[str] = Field(default_factory=list)
    new_issues_found: list[str] = Field(default_factory=list)
    freeze_records: list[ResolvedIssueRecord] = Field(default_factory=list)
    failure_reason: str = ""


# ---------------------------------------------------------------------------
# 修复尝试
# ---------------------------------------------------------------------------


class RepairAttempt(BaseModel):
    """单次 LLM 调用尝试的记录。"""

    model_config = ConfigDict(protected_namespaces=())

    attempt_id: str
    case_id: str
    attempt_index: int = 0
    specialist: str = ""
    model_name: str = ""
    prompt_trace_id: str = ""
    status: Literal["pending", "running", "completed", "failed"] = "pending"
    failure_reason: str = ""
    input_hash: str = ""
    output_hash: str = ""
    patch_count: int = 0
    created_at: str = ""
    completed_at: str = ""


# ---------------------------------------------------------------------------
# 修复审计
# ---------------------------------------------------------------------------


class RepairAudit(BaseModel):
    """修复后审计记录。"""

    audit_id: str
    case_id: str
    attempt_id: str = ""
    audit_type: Literal[
        "patch_validation",
        "targeted_recheck",
        "full_recheck",
        "style_guard",
        "fact_preservation",
    ] = "patch_validation"
    status: Literal["passed", "failed", "warning"] = "passed"
    report: dict = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def compute_text_hash(text: str) -> str:
    """计算文本的短哈希，格式为 'sha256:' + 前 16 位 hex。"""
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:16]}"


# Violation.source -> RepairIssue.source_layer 映射
_VIOLATION_SOURCE_TO_LAYER: dict[str, str] = {
    "hard_correctness": "hard_correctness",
    "consistency": "consistency",
    "fcip": "foreshadowing",
    "critic": "style",
    "deterministic": "hard_correctness",
    "narrative_contract": "narrative_proposition",
    "style_quality": "style",
    "reader_experience": "reader_experience",
    "narrative_experience": "reader_experience",
    "literary_quality": "literary_quality",
    "mode_fit": "style",
    "style_experience_conflict": "style",
    "commercial_pacing": "commercial_pacing",
    "scene_credibility": "custom",
}

_SCENE_CREDIBILITY_TYPE_TO_LAYER: dict[str, str] = {
    "fact_boundary_conflict": "fact_boundary",
    "knowledge_boundary_violation": "knowledge_boundary",
    "plausibility_break": "plausibility",
    "memory_plausibility_break": "plausibility",
    "narration_explanation_artifact": "narration_credibility",
    "explanatory_punctuation_artifact": "narration_credibility",
}

# Violation.severity -> RepairIssue.severity 映射
_SEVERITY_MAP: dict[str, str] = {
    "critical": "blocking",
    "high": "major",
    "medium": "minor",
    "low": "info",
}

# Violation.suggested_strategy -> RepairIssue.repairability 映射
_STRATEGY_TO_REPAIRABILITY: dict[str, str] = {
    "patch_text": "auto",
    "patch_text_by_proposition": "auto",
    "patch_literary_quality": "assisted",
    "rewrite_scene": "rewrite_required",
    "rewrite_scene_with_fact_contract": "rewrite_required",
    "rewrite_scene_with_experience_contract": "rewrite_required",
    "repair_contract": "manual_only",
    "repair_experience_contract": "assisted",
    "style_experience_negotiation": "assisted",
    "contract_budget_adjust": "auto",
    "validator_retry": "auto",
    "manual_review": "manual_only",
}


def violation_to_repair_issue(violation: dict, project_id: str) -> RepairIssue:
    """将 QualityGate 输出的 Violation 字典转换为 RepairIssue。

    参数:
        violation: Violation TypedDict 的字典形式
        project_id: 所属项目 ID，用于生成唯一 issue_id
    """
    vtype = violation.get("type", "unknown")
    source = violation.get("source", "deterministic")
    severity = violation.get("severity", "medium")
    strategy = violation.get("suggested_strategy", "manual_review")

    # Preserve an upstream issue_id when the workflow/orchestrator already
    # normalized the issue. This keeps controller outcomes addressable by the
    # same ids that the auto-repair loop and frontend workbench track.
    issue_id = str(violation.get("issue_id") or "").strip()
    if not issue_id:
        raw = f"{project_id}:{vtype}:{violation.get('violation_id', '')}"
        issue_id = hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]

    # 映射 source_layer
    if source == "scene_credibility":
        source_layer = _SCENE_CREDIBILITY_TYPE_TO_LAYER.get(vtype, "custom")
    else:
        source_layer = _VIOLATION_SOURCE_TO_LAYER.get(source, "custom")

    # 映射 severity
    mapped_severity = _SEVERITY_MAP.get(severity, "minor")

    # 映射 repairability
    repairability = _STRATEGY_TO_REPAIRABILITY.get(strategy, "manual_only")

    # 解析 target_span -> TextSpan
    span: TextSpan | None = None
    target_span_str = violation.get("target_span")
    if target_span_str and isinstance(target_span_str, str) and ":" in target_span_str:
        try:
            parts = target_span_str.split(":", 1)
            span = TextSpan(start=int(parts[0]), end=int(parts[1]))
        except (ValueError, IndexError):
            pass

    # 提取 evidence
    evidence_raw = violation.get("evidence", {})
    if isinstance(evidence_raw, dict):
        evidence = evidence_raw.get("detail", "") or violation.get("detail", "")
    else:
        evidence = str(evidence_raw) if evidence_raw else violation.get("detail", "")

    return RepairIssue(
        issue_id=issue_id,
        source_layer=source_layer,
        violation_type=vtype,
        severity=mapped_severity,
        repairability=repairability,
        span=span,
        evidence=evidence,
        repair_goal=violation.get("expected_behavior", ""),
        acceptance_criteria=[],
        blocks_commit=violation.get("blocks_commit", True),
        scope=violation.get("scope", "prose_text"),
        repairable_by_text=violation.get("repairable_by_text", True),
        repairable_by_contract=violation.get("repairable_by_contract", False),
        classification=violation.get("classification", ""),
        authority_source=violation.get("authority_source", ""),
        recommended_route=violation.get("recommended_route", ""),
        obligation_id=violation.get("obligation_id", ""),
        obligation_scope=violation.get("obligation_scope", ""),
        obligation_owner_scene=violation.get("obligation_owner_scene"),
        obligation_binding_status=violation.get("obligation_binding_status", ""),
        obligation_ownership_explicit=bool(violation.get("obligation_ownership_explicit", False)),
    )
