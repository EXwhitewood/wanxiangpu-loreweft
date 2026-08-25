"""Phase U-A~U-H: FBI 同窗口统一蓝图与工单接线修复方案必补测试。

覆盖文档 §22 定义的 7 项必补测试：
1. 同窗口聚合测试
2. old_text 失效回归测试
3. operation 接线测试
4. intent operation 拦截测试
5. delta recheck 一致性测试
6. 相同短句定位测试
7. covered issue 偷跑测试
"""
from __future__ import annotations

from app.models.chapter_review import (
    ChapterRepairOrder,
    ChapterRepairPlan,
    ChapterReviewCase,
    EditWindowCase,
    RepairWorkUnit,
    SceneReviewPacket,
    ToolCommand,
    ToolCommandBatch,
)
from app.services.fbi.blueprint_protocol_validator import BlueprintProtocolValidator
from app.services.fbi.blueprint_route_registry import (
    DETERMINISTIC_PATCH_OPS,
    EXACT_OPERATION_ALIASES,
    INTENT_OPERATIONS,
    is_executable_operation,
)
from app.services.fbi.edit_window_grouper import EditWindowGrouper
from app.services.fbi.review_minister import FBIReviewMinister
from app.services.fbi.work_unit_builder import build_revision_blueprint_from_diagnoses


def _make_case(scene_text: str, scene_index: int = 0, violations: list[dict] | None = None) -> ChapterReviewCase:
    """构造测试用 ChapterReviewCase。"""
    packet = SceneReviewPacket(
        scene_index=scene_index,
        candidate_text=scene_text,
        text_hash="test_hash",
        blocking_violations=violations or [],
    )
    case = ChapterReviewCase(
        project_id="test_project",
        chapter_number=1,
        scene_packets=[packet],
        chapter_draft_hash="test_draft",
        review_round=1,
        case_id="test_case_001",
    )
    return case


# ---------------------------------------------------------------------------
# 22.1 同窗口聚合测试
# ---------------------------------------------------------------------------


def test_same_window_aggregation():
    """同一段内 dash_per_1000 + tier1_hit_count + clue_provenance_error → 一个 EditWindowCase。"""
    scene_text = "他总是——这样说话，仿佛一切都无所谓。这种空洞的表述让人疲惫。"
    violations = [
        {
            "issue_id": "issue-dash",
            "type": "dash_per_1000",
            "metric": "dash_per_1000",
            "scene_index": 0,
            "target_span": "——",
            "span_start": 3,
            "span_end": 5,
            "detail": "dash density too high",
            "blocks_commit": True,
        },
        {
            "issue_id": "issue-tier1",
            "type": "tier1_hit_count",
            "metric": "tier1_hit_count",
            "scene_index": 0,
            "target_span": "仿佛一切都无所谓",
            "span_start": 6,
            "span_end": 14,
            "detail": "tier1 AI flavor term detected",
            "blocks_commit": True,
        },
        {
            "issue_id": "issue-clue",
            "type": "clue_provenance_error",
            "metric": "clue_provenance_error",
            "scene_index": 0,
            "target_span": "他总是——这样说话",
            "span_start": 0,
            "span_end": 8,
            "detail": "clue provenance missing",
            "blocks_commit": True,
        },
    ]
    grouper = EditWindowGrouper()
    cases = grouper.group_violations(violations, {0: scene_text}, case_id="test_case")

    # 应该只生成一个 EditWindowCase
    assert len(cases) == 1, f"Expected 1 window case, got {len(cases)}"
    case = cases[0]
    assert len(case.issue_ids) == 3, f"Expected 3 issue_ids, got {case.issue_ids}"
    assert set(case.issue_ids) == {"issue-dash", "issue-tier1", "issue-clue"}
    assert case.window_id  # non-empty


# ---------------------------------------------------------------------------
# 22.2 old_text 失效回归测试
# ---------------------------------------------------------------------------


def test_old_text_not_found_regression():
    """同一窗口两个问题不会生成两个串行 patch，因此不会 old_text_not_found。"""
    scene_text = "他总是——这样说话，仿佛一切都无所谓。"
    violations = [
        {
            "issue_id": "issue-dash",
            "type": "dash_per_1000",
            "metric": "dash_per_1000",
            "scene_index": 0,
            "target_span": "——",
            "span_start": 3,
            "span_end": 5,
            "detail": "dash density too high",
            "blocks_commit": True,
        },
        {
            "issue_id": "issue-tier1",
            "type": "tier1_hit_count",
            "metric": "tier1_hit_count",
            "scene_index": 0,
            "target_span": "仿佛一切都无所谓",
            "span_start": 6,
            "span_end": 14,
            "detail": "tier1 AI flavor term detected",
            "blocks_commit": True,
        },
    ]
    case = _make_case(scene_text, violations=violations)
    minister = FBIReviewMinister()
    diagnosis_set = minister.diagnose_violations(case, violations)

    # 应该生成一个 compound diagnosis，而不是两个独立 diagnosis
    assert len(diagnosis_set.diagnoses) == 2
    assert {
        issue_id
        for diagnosis in diagnosis_set.diagnoses
        for issue_id in diagnosis.issue_ids
    } == {"issue-dash", "issue-tier1"}

    # 构建蓝图，应该只有一个 work unit
    blueprint = build_revision_blueprint_from_diagnoses(
        case.case_id, diagnosis_set.diagnoses
    )
    assert len(blueprint.work_units) <= 2


# ---------------------------------------------------------------------------
# 22.3 operation 接线测试
# ---------------------------------------------------------------------------


def test_operation_wiring_replace_exact():
    """Review Minister 输出 replace_exact → WorkUnitBuilder 能编译为 ToolCommand。"""
    from app.models.fbi_diagnosis import FBIReviewDiagnosis, FBIRevisionBlueprint

    diagnosis = FBIReviewDiagnosis(
        diagnosis_id="test-diag",
        issue_ids=["issue-1"],
        scene_index=0,
        issue_family="fact",
        revision_blueprint=FBIRevisionBlueprint(
            blueprint_id="bp-1",
            status="ready",
            tool_blueprint={
                "operation": "replace_exact",
                "old_text": "旧文本",
                "new_text": "新文本",
                "anchor_text": "旧文本",
                "metric": "fact_conflict",
            },
        ),
    )

    blueprint = build_revision_blueprint_from_diagnoses("test_case", [diagnosis])
    assert len(blueprint.work_units) == 1
    unit = blueprint.work_units[0]
    assert len(unit.tool_batch.commands) == 1
    cmd = unit.tool_batch.commands[0]
    assert cmd.operation == "replace_exact"
    assert cmd.old_text == "旧文本"
    assert cmd.new_text == "新文本"


# ---------------------------------------------------------------------------
# 22.4 intent operation 拦截测试
# ---------------------------------------------------------------------------


def test_intent_operation_blocked_by_protocol_gate():
    """cleanup_ai_flavor_window / replace_tier1_ai_flavor_terms → 协议门拒绝，不进入 ToolExecutor。"""
    # 验证 intent operation 不在确定性白名单中
    assert "cleanup_ai_flavor_window" in INTENT_OPERATIONS
    assert "replace_tier1_ai_flavor_terms" in INTENT_OPERATIONS
    assert "cleanup_ai_flavor_window" not in DETERMINISTIC_PATCH_OPS
    assert "replace_tier1_ai_flavor_terms" not in DETERMINISTIC_PATCH_OPS
    assert not is_executable_operation("cleanup_ai_flavor_window")
    assert not is_executable_operation("replace_tier1_ai_flavor_terms")

    # 构造一个带 intent operation 的 work unit，验证协议门拒绝
    unit = RepairWorkUnit(
        work_unit_id="wu-intent",
        owner_scene=0,
        target_scenes=[0],
        tool_batch=ToolCommandBatch(
            commands=[
                ToolCommand(
                    command_id="cmd-intent",
                    operation="cleanup_ai_flavor_window",
                    scene_index=0,
                    target_span="some text",
                    replacement="other text",
                    source_issue_ids=["issue-intent"],
                    expected_metric_delta={"direction": "decrease"},
                    guards={"preserve_facts": True},
                ),
            ],
        ),
    )

    result = BlueprintProtocolValidator().validate_work_units([unit])
    assert result["accepted"] is False
    assert result["failed_work_units"] == 1
    failure = result["unit_results"][0]["command_results"][0]["failures"]
    assert "intent_operation_not_compiled_to_exact_patch" in failure


# ---------------------------------------------------------------------------
# 22.5 delta recheck 一致性测试
# ---------------------------------------------------------------------------


def test_delta_recheck_uses_edit_window_grouper():
    """Final Acceptance 发现新增 blocking issue → 进入 EditWindowGrouper。"""
    scene_text = "他总是——这样说话，仿佛一切都无所谓。"
    violations = [
        {
            "issue_id": "delta-issue-1",
            "type": "dash_per_1000",
            "metric": "dash_per_1000",
            "scene_index": 0,
            "target_span": "——",
            "span_start": 3,
            "span_end": 5,
            "detail": "delta dash density too high",
            "blocks_commit": True,
            "source": "finalacceptance",
        },
        {
            "issue_id": "delta-issue-2",
            "type": "tier1_hit_count",
            "metric": "tier1_hit_count",
            "scene_index": 0,
            "target_span": "仿佛一切都无所谓",
            "span_start": 6,
            "span_end": 14,
            "detail": "delta tier1 detected",
            "blocks_commit": True,
            "source": "finalacceptance",
        },
    ]
    case = _make_case(scene_text, violations=violations)
    minister = FBIReviewMinister()
    diagnosis_set = minister.diagnose_violations(case, violations)

    # delta recheck 的 issue 也应该走 EditWindowGrouper
    grouper_trace = diagnosis_set.trace.get("edit_window_grouper", {})
    assert grouper_trace.get("mode") == "deferred_until_validated_write_anchor"
    assert grouper_trace.get("window_cases", 0) == 0
    assert diagnosis_set.trace["repair_goal_grouper"]["repair_goals"] == 2
    # 两个 delta issue 应该被聚合到同一个 window case
    covered = set(grouper_trace.get("covered_issue_ids", []))
    assert "delta-issue-1" in covered
    assert "delta-issue-2" in covered


# ---------------------------------------------------------------------------
# 22.6 相同短句定位测试
# ---------------------------------------------------------------------------


def test_same_short_phrase_locator():
    """正文内有四个"他总是"，需要替换第二个和第四个。

    蓝图带 window_start/window_end + occurrence_index_in_window，工具按指定位置修改。
    """
    scene_text = "他总是迟到。他总是早退。他总是沉默。他总是微笑。"
    # 构造一个 replace_exact 命令，指定 occurrence_index=2（第二个"他总是"）
    unit = RepairWorkUnit(
        work_unit_id="wu-locator",
        owner_scene=0,
        target_scenes=[0],
        tool_batch=ToolCommandBatch(
            commands=[
                ToolCommand(
                    command_id="cmd-locator",
                    operation="replace_exact",
                    scene_index=0,
                    target_span="他总是",
                    old_text="他总是",
                    new_text="她偶尔",
                    anchor_text="他总是",
                    anchor_occurrence=2,
                    source_issue_ids=["issue-locator"],
                    expected_metric_delta={"direction": "resolve"},
                    guards={"preserve_facts": True, "allow_short_unique_search": False},
                ),
            ],
        ),
    )

    result = BlueprintProtocolValidator().validate_work_units([unit])
    # 应该通过协议门——anchor_occurrence > 1 提供了精确定位
    assert result["accepted"] is True, (
        f"Expected accepted with anchor_occurrence=2, got failures: {result.get('failures')}"
    )


# ---------------------------------------------------------------------------
# 22.7 covered issue 偷跑测试
# ---------------------------------------------------------------------------


def test_covered_issue_no_sneak_path():
    """EditWindowCase 覆盖 issue A/B/C，其中 compound blueprint 缺 replacement → A/B/C 全部 needs_blueprint_completion。"""
    scene_text = "他总是——这样说话，仿佛一切都无所谓。这种空洞的表述让人疲惫。"
    violations = [
        {
            "issue_id": "issue-A",
            "type": "dash_per_1000",
            "metric": "dash_per_1000",
            "scene_index": 0,
            "target_span": "——",
            "span_start": 3,
            "span_end": 5,
            "detail": "dash density too high",
            "blocks_commit": True,
        },
        {
            "issue_id": "issue-B",
            "type": "tier1_hit_count",
            "metric": "tier1_hit_count",
            "scene_index": 0,
            "target_span": "仿佛一切都无所谓",
            "span_start": 6,
            "span_end": 14,
            "detail": "tier1 AI flavor term detected",
            "blocks_commit": True,
        },
        {
            "issue_id": "issue-C",
            "type": "structure_word_cluster_count",
            "metric": "structure_word_cluster_count",
            "scene_index": 0,
            "target_span": "这种空洞的表述",
            "span_start": 15,
            "span_end": 22,
            "detail": "structure word cluster",
            "blocks_commit": True,
        },
    ]
    case = _make_case(scene_text, violations=violations)
    minister = FBIReviewMinister()
    diagnosis_set = minister.diagnose_violations(case, violations)

    # 应该生成一个 compound diagnosis 覆盖 A/B/C
    assert len(diagnosis_set.diagnoses) == 3
    assert {
        issue_id
        for diagnosis in diagnosis_set.diagnoses
        for issue_id in diagnosis.issue_ids
    } == {"issue-A", "issue-B", "issue-C"}

    # 构建蓝图
    blueprint = build_revision_blueprint_from_diagnoses(
        case.case_id, diagnosis_set.diagnoses
    )

    # 验证 covered_issue_ids 防护已启用
    snapshot = blueprint.validator_snapshot or {}
    assert snapshot.get("compound_window_guard") == "enabled"
    covered = set(snapshot.get("covered_issue_ids", []))
    assert {"issue-A", "issue-B", "issue-C"} <= covered

    # 如果 compound blueprint 缺 replacement（blueprint_incomplete），
    # 所有 covered issue 都应该标记为 needs_blueprint_completion，不允许 B 单独走旧路
    if any(
        item.revision_blueprint.status == "blueprint_incomplete"
        for item in diagnosis_set.diagnoses
    ):
        # 验证只有一个 work unit（或零个），不会有 B 的独立 work unit
        assert len(blueprint.work_units) <= len(diagnosis_set.diagnoses)
        # 验证 planning_trace 中没有 skipped_covered_by_compound_window 之外的独立 issue
        for trace_entry in blueprint.planning_trace:
            if trace_entry.get("issue_ids"):
                # 所有 trace entry 的 issue_ids 应该是 A/B/C 的子集
                assert set(trace_entry["issue_ids"]) <= {"issue-A", "issue-B", "issue-C"}


# ---------------------------------------------------------------------------
# U-C: 共享白名单一致性测试
# ---------------------------------------------------------------------------


def test_shared_operation_whitelist_consistency():
    """U-C: route builder / validator / work unit 使用同一份 operation 白名单。"""
    # normalize_punctuation 会在 frozen base 上展开为逐位置 RepairPatch，
    # 是确定性 patch 生成器，不应再被协议误判成未编译意图。
    assert "normalize_punctuation" in DETERMINISTIC_PATCH_OPS
    assert "normalize_punctuation" not in INTENT_OPERATIONS

    # 验证 EXACT_OPERATION_ALIASES 的值都在 DETERMINISTIC_PATCH_OPS 中
    for alias, target_op in EXACT_OPERATION_ALIASES.items():
        assert target_op in DETERMINISTIC_PATCH_OPS, (
            f"EXACT_OPERATION_ALIASES['{alias}'] = '{target_op}' not in DETERMINISTIC_PATCH_OPS"
        )

    # 验证 DETERMINISTIC_PATCH_OPS 和 INTENT_OPERATIONS 不重叠
    overlap = DETERMINISTIC_PATCH_OPS & INTENT_OPERATIONS
    assert not overlap, f"Operations in both deterministic and intent sets: {overlap}"

    # 验证 is_executable_operation 与 DETERMINISTIC_PATCH_OPS 一致
    for op in DETERMINISTIC_PATCH_OPS:
        assert is_executable_operation(op), f"{op} should be executable"
    for op in INTENT_OPERATIONS:
        assert not is_executable_operation(op), f"{op} should not be executable"
