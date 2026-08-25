"""主编系统诊断追踪模型。

每次章节生成都记录完整 trace，能定位失败到底发生在合同、Writer、质量门、FBI、LLM 哪一层。

V2 增强字段：
- writer_input_packets: 每场景的 WriterInputPacket 快照
- v2_compile_status: V2 编译是否成功
- v2_repair_outcomes: FBI V2 修复结果
- scene_text_hashes: 每场景正文 hash，用于 issue 版本绑定
"""
from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Literal


class PreGenerationMetrics(BaseModel):
    """生成前诊断指标"""
    decision_context_token_estimate: int = 0
    chapter_plan_contract_item_count: int = 0
    scene_contract_item_counts: list[int] = Field(default_factory=list)
    per_scene_must_show_counts: list[int] = Field(default_factory=list)
    per_scene_forbidden_counts: list[int] = Field(default_factory=list)
    per_scene_writer_input_char_counts: list[int] = Field(default_factory=list)
    per_scene_writer_visible_ledger_items: list[int] = Field(default_factory=list)


class PostGenerationMetrics(BaseModel):
    """生成后诊断指标"""
    text_char_count: int = 0
    explanation_density: float = 0.0
    dash_count: int = 0
    new_term_count: int = 0
    definition_sentence_count: int = 0
    quality_finding_count: int = 0
    fbi_auto_repair_rounds: int = 0
    fbi_patches_per_round: list[int] = Field(default_factory=list)
    fbi_empty_response_count: int = 0
    fbi_no_op_count: int = 0
    final_human_workbench: bool = False


class SceneWriterInputSnapshot(BaseModel):
    """单场景 WriterInputPacket 快照"""
    scene_index: int = 0
    v2_used: bool = False
    must_include: list[str] = Field(default_factory=list)
    must_avoid: list[str] = Field(default_factory=list)
    active_capabilities: list[str] = Field(default_factory=list)
    activation_reasons: dict[str, str] = Field(default_factory=dict)
    source_trace: dict[str, list[str]] = Field(default_factory=dict)
    writer_input_enrichment: dict = Field(default_factory=dict)
    hard_max_chars: int = 0
    target_chars: int = 0
    ending_state: str = ""
    soft_hints: list[str] = Field(default_factory=list)
    visible_facts: list[str] = Field(default_factory=list)
    degraded_items: list[str] = Field(default_factory=list)
    budget_check: dict = Field(default_factory=dict)


class SceneRepairOutcome(BaseModel):
    """单场景修复结果"""
    scene_index: int = 0
    v2_status: Literal["v2_resolved", "v2_degraded", "v2_failed", "legacy_fallback", "skipped"] = "skipped"
    total_orders: int = 0
    succeeded_orders: int = 0
    failed_orders: int = 0
    needs_human: list[str] = Field(default_factory=list)
    legacy_fallback: bool = False
    failure_reasons: list[str] = Field(default_factory=list)
    text_hash_before: str = ""
    text_hash_after: str = ""


class LayerTrace(BaseModel):
    """单层追踪记录"""
    layer: Literal[
        "decision_context",
        "editor_planning",
        "context_compile",
        "chapter_plan",
        "v2_compile",
        "scene_contract",
        "writer_input",
        "chapter_writer",
        "scene_alignment",
        "core_generation",
        "word_count_gate",
        "forbidden_gate",
        "quality_gate",
        "fbi_repair",
        "fbi_v2_repair",
        "llm_call",
        "ledger_writeback",
    ]
    status: Literal["ok", "degraded", "failed", "skipped"]
    duration_ms: int = 0
    detail: str = ""
    error: str = ""
    # 方案16：DAG 可观测性字段
    node_traces: dict[str, list[dict]] = Field(default_factory=dict)
    phase_trace: list[dict] = Field(default_factory=list)
    skip_reason: str = ""


class EditorTrace(BaseModel):
    """主编系统完整追踪记录"""
    trace_id: str = ""
    project_id: str = ""
    chapter_number: int = 0
    execution_id: str = ""
    created_at: str = ""
    pre_metrics: PreGenerationMetrics = Field(default_factory=PreGenerationMetrics)
    post_metrics: PostGenerationMetrics = Field(default_factory=PostGenerationMetrics)
    layers: list[LayerTrace] = Field(default_factory=list)
    final_status: Literal["completed", "degraded", "failed", "cancelled"] = "completed"
    final_error: str = ""
    # V2 诊断增强
    v2_compile_status: Literal["ok", "degraded", "failed", "skipped"] = "skipped"
    v2_compile_error: str = ""
    writer_input_snapshots: list[SceneWriterInputSnapshot] = Field(default_factory=list)
    repair_outcomes: list[SceneRepairOutcome] = Field(default_factory=list)
    scene_text_hashes: list[str] = Field(default_factory=list)


class EditorTraceQuery(BaseModel):
    """追踪查询参数"""
    project_id: str | None = None
    chapter_number: int | None = None
    execution_id: str | None = None
    final_status: str | None = None
    v2_compile_status: str | None = None
    limit: int = 20
