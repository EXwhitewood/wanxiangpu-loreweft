"""方案2+方案4 联动测试：统一上下文数据层 + 审查官分层骨架。

覆盖：
- 方案2：UnifiedContextBuilder 5个数据层方法真实对接（不再返回占位）
- 方案2：三路接入（主编预加载/writer补充/审查官补充）
- 方案4：ISSUE_CONTEXT_TEMPLATE（A2）覆盖12种issue类型
- 方案4：PATCH_VALIDATION_RULES（A3）验证patch合规性
- 方案4：审查官6层分层注入（Part B）
- 方案4：闸门②接入patch验证
"""
from __future__ import annotations

import pytest

from app.models.fbi_diagnosis import FBIReviewDiagnosisSet
from app.services.fbi.blueprint_route_registry import (
    ISSUE_CONTEXT_TABLE,
    PATCH_VALIDATION_RULES,
    get_issue_context_template,
    get_patch_validation_rules,
    validate_patch,
)
from app.services.unified_context_builder import UnifiedContextBuilder


# ---------------------------------------------------------------------------
# 方案2：UnifiedContextBuilder 数据层方法（无 db 时的兜底行为）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_editor_context_without_db_returns_safe_defaults():
    """验证2：无 db 时 build_editor_context 不抛异常，返回安全默认值。"""
    import uuid
    ucb = UnifiedContextBuilder()
    # 用合法 UUID（不存在），测试不抛异常
    fake_pid = str(uuid.uuid4())
    ctx = await ucb.build_editor_context(fake_pid, 1, db=None)

    assert hasattr(ctx, "core")
    assert hasattr(ctx, "recent_summaries")
    assert hasattr(ctx, "outline")
    assert hasattr(ctx, "lookahead")
    # outline 无 db 时应返回兜底（chapter_number 存在）
    assert ctx.outline.get("chapter_number") == 1


@pytest.mark.asyncio
async def test_build_writer_context_without_db_returns_safe_defaults():
    """验证2：无 db 时 build_writer_context 不抛异常。"""
    import uuid
    ucb = UnifiedContextBuilder()
    fake_pid = str(uuid.uuid4())
    ctx = await ucb.build_writer_context(fake_pid, 1, db=None)

    assert hasattr(ctx, "core")
    assert hasattr(ctx, "shell")
    assert hasattr(ctx, "outline")
    assert hasattr(ctx, "contracts")
    assert hasattr(ctx, "style")
    assert ctx.contracts == []  # 无数据时返回空列表


@pytest.mark.asyncio
async def test_build_reviewer_context_without_db_returns_safe_defaults():
    """验证2：无 db 时 build_reviewer_context 不抛异常。"""
    import uuid
    ucb = UnifiedContextBuilder()
    fake_pid = str(uuid.uuid4())
    ctx = await ucb.build_reviewer_context(
        fake_pid, 1, candidate_text="test", issues=[{"id": "i1"}]
    )

    assert hasattr(ctx, "writer_context")
    assert ctx.candidate_text == "test"
    assert len(ctx.issues) == 1
    assert ctx.repair_history == []  # 无数据时返回空列表


# ---------------------------------------------------------------------------
# 方案4 A2：ISSUE_CONTEXT_TEMPLATE
# ---------------------------------------------------------------------------


def test_issue_context_table_covers_12_issue_types():
    """验证4：ISSUE_CONTEXT_TABLE 覆盖12种issue类型。"""
    expected_types = {
        "fact_conflict", "low_conflict_density", "clue_provenance_error",
        "causal_chain_error", "ai_punctuation_artifact", "uniform_sentence_streak_max",
        "abstraction_over_budget", "weak_curiosity_engine", "vague_attribution_count",
        "not_but_pattern", "tier1_hit_count", "sentence_length_variance",
    }
    assert expected_types.issubset(set(ISSUE_CONTEXT_TABLE.keys()))


def test_get_issue_context_template_returns_correct_template():
    """验证4：get_issue_context_template 返回正确的模板。"""
    tmpl = get_issue_context_template("fact_conflict")
    assert "must_read" in tmpl
    assert "must_output" in tmpl
    assert "constraints" in tmpl
    assert "人物完整卡" in tmpl["must_read"]
    assert "old_text" in tmpl["must_output"]
    assert any("patch" in c for c in tmpl["constraints"])


def test_get_issue_context_template_default_for_unknown_type():
    """验证4：未知issue类型返回默认模板。"""
    tmpl = get_issue_context_template("unknown_type_xyz")
    assert "must_read" in tmpl
    assert "must_output" in tmpl
    assert "constraints" in tmpl
    # 默认模板应包含候选正文和场景合同
    assert "候选正文" in tmpl["must_read"]


def test_get_issue_context_template_empty_string_returns_default():
    """验证4：空字符串返回默认模板。"""
    tmpl = get_issue_context_template("")
    assert "must_read" in tmpl
    assert "候选正文" in tmpl["must_read"]


def test_each_issue_template_has_required_fields():
    """验证4：每个模板都有 must_read/must_output/constraints 三个字段。"""
    for issue_type, tmpl in ISSUE_CONTEXT_TABLE.items():
        # IssueContextTemplate 是 dataclass，用属性访问
        tmpl_dict = tmpl.to_dict() if hasattr(tmpl, "to_dict") else dict(tmpl)
        assert "must_read" in tmpl_dict, f"{issue_type} 缺 must_read"
        assert "must_output" in tmpl_dict, f"{issue_type} 缺 must_output"
        assert "constraints" in tmpl_dict, f"{issue_type} 缺 constraints"
        assert isinstance(tmpl_dict["must_read"], list)
        assert isinstance(tmpl_dict["must_output"], list)
        assert isinstance(tmpl_dict["constraints"], list)
        assert len(tmpl_dict["must_read"]) > 0
        assert len(tmpl_dict["must_output"]) > 0


# ---------------------------------------------------------------------------
# 方案4 A3：PATCH_VALIDATION_RULES
# ---------------------------------------------------------------------------


def test_patch_validation_rules_has_required_fields():
    """验证4：PATCH_VALIDATION_RULES 包含所有必需规则。"""
    rules = get_patch_validation_rules()
    assert "min_length" in rules
    assert "max_length" in rules
    assert "must_have_anchor" in rules
    assert "pov_check" in rules
    assert "meta_term_check" in rules
    assert "iron_rule_check" in rules
    assert "fact_preservation" in rules
    assert rules["min_length"] == 20
    assert rules["max_length"] == 2000


def test_validate_patch_passes_valid_patch():
    """验证4：合规patch通过验证。"""
    patch = {
        "old_text": "原始文本",
        "new_text": "凤溪拿起银线草仔细端详，这是足够长的修复文本",
        "anchor_text": "锚点文本",
    }
    ok, errors = validate_patch(patch, pov_character="凤溪")
    assert ok is True
    assert errors == []


def test_validate_patch_fails_too_short():
    """验证4：new_text 过短时验证失败。"""
    patch = {
        "old_text": "原",
        "new_text": "短",  # 不足20字
        "anchor_text": "锚点",
    }
    ok, errors = validate_patch(patch)
    assert ok is False
    assert any("长度" in e or "length" in e.lower() for e in errors)


def test_validate_patch_fails_meta_term():
    """验证4：new_text 含meta术语时验证失败。"""
    patch = {
        "old_text": "原始文本",
        "new_text": "这一章节的段落需要修改为更好的内容",
        "anchor_text": "锚点",
    }
    ok, errors = validate_patch(patch)
    assert ok is False
    assert any("meta" in e.lower() or "术语" in e for e in errors)


def test_validate_patch_fails_missing_anchor():
    """验证4：缺少anchor_text时验证失败。"""
    patch = {
        "old_text": "原始文本",
        "new_text": "这是足够长的修复文本，超过二十个字符",
        "anchor_text": "",  # 空anchor
    }
    ok, errors = validate_patch(patch)
    assert ok is False
    assert any("anchor" in e.lower() for e in errors)


def test_validate_patch_fails_pov_mismatch():
    """验证4：new_text 不含POV角色名时验证失败。"""
    patch = {
        "old_text": "原始文本",
        "new_text": "他拿起银线草仔细端详着这株罕见的灵药植物",
        "anchor_text": "锚点",
    }
    ok, errors = validate_patch(patch, pov_character="凤溪")
    assert ok is False
    assert any("POV" in e or "pov" in e.lower() for e in errors)


def test_validate_patch_accumulates_multiple_errors():
    """验证4：多个错误同时存在时全部报告。"""
    patch = {
        "old_text": "",
        "new_text": "短",  # 过短
        "anchor_text": "",  # 缺anchor
    }
    ok, errors = validate_patch(patch, pov_character="凤溪")
    assert ok is False
    assert len(errors) >= 2  # 至少2个错误


# ---------------------------------------------------------------------------
# 方案4 Part B：审查官6层分层注入
# ---------------------------------------------------------------------------


def test_session_user_prompt_has_6_layer_structure():
    """验证4：审查官 user prompt 包含6层分层标记。"""
    from app.agents.fbi.review_blueprint_agent import FBIReviewBlueprintAgent

    agent = FBIReviewBlueprintAgent()

    # 构造最小context
    from app.models.chapter_review import ChapterReviewCase
    case = ChapterReviewCase(
        project_id="test-project",
        chapter_number=1,
        chapter_outline_contract={"chapter_number": 1, "title": "测试章节"},
        existing_worldview={
            "iron_rules": [{"name": "铁则1", "description": "测试铁则"}],
            "foreshadowing": [{"name": "伏笔1", "status": "active"}],
        },
        chapter_initial_state={"established_facts": ["事实1"]},
    )

    context = {
        "case": case,
        "scene_texts": {0: "这是候选正文。"},
        "scene_contract": {"pov_character": "凤溪", "goal": "测试目标"},
        "character_cards": [{"name": "凤溪", "role": "主角"}],
        "repair_history": [{"issue_id": "i1", "attempt": 1, "status": "failed"}],
    }

    annotated = [
        {
            "issue_id": "issue_1",
            "type": "fact_conflict",
            "metric": "fact_conflict",
            "detail": "事实冲突",
            "target_span": "冲突文本",
            "fbi_diagnosis": {"issue_family": "fact", "diagnosis_id": "d1"},
        }
    ]

    # 构造最小 diagnosis_set mock
    from unittest.mock import MagicMock
    diagnosis_set = MagicMock()

    prompt = agent._build_session_user_prompt(context, annotated, diagnosis_set)

    # 验证6层标记存在
    assert "[层1" in prompt or "[层1 -" in prompt
    assert "[层2" in prompt or "[层2 -" in prompt
    assert "[层3" in prompt or "[层3 -" in prompt
    assert "[层4" in prompt or "[层4 -" in prompt
    assert "[层5" in prompt or "[层5 -" in prompt
    assert "[层6" in prompt or "[层6 -" in prompt


def test_session_user_prompt_includes_issue_context_template():
    """验证4：user prompt 包含 ISSUE_CONTEXT_TEMPLATE 注入。"""
    from app.agents.fbi.review_blueprint_agent import FBIReviewBlueprintAgent

    agent = FBIReviewBlueprintAgent()
    from app.models.chapter_review import ChapterReviewCase
    from unittest.mock import MagicMock

    case = ChapterReviewCase(project_id="test", chapter_number=1)
    context = {"case": case, "scene_texts": {}, "scene_contract": {}}
    annotated = [
        {
            "issue_id": "i1",
            "type": "fact_conflict",
            "metric": "fact_conflict",
            "detail": "测试",
            "fbi_diagnosis": {"issue_family": "fact"},
        }
    ]
    diagnosis_set = MagicMock()

    prompt = agent._build_session_user_prompt(context, annotated, diagnosis_set)

    # 应包含 ISSUE_CONTEXT_TEMPLATE 的注入
    assert "上下文模板" in prompt
    assert "fact_conflict" in prompt
    assert "必读" in prompt or "must_read" in prompt


def test_session_user_prompt_layer_order_correct():
    """验证4：层序按注意力优先级排列（层1在前，层6在后）。"""
    from app.agents.fbi.review_blueprint_agent import FBIReviewBlueprintAgent
    from app.models.chapter_review import ChapterReviewCase
    from unittest.mock import MagicMock

    agent = FBIReviewBlueprintAgent()
    case = ChapterReviewCase(
        project_id="test",
        chapter_number=1,
        chapter_outline_contract={"chapter_number": 1},
        existing_worldview={"iron_rules": [{"name": "r1"}]},
        chapter_initial_state={"established_facts": ["f1"]},
    )
    context = {
        "case": case,
        "scene_texts": {0: "正文"},
        "scene_contract": {"pov_character": "凤溪"},
        "character_cards": [{"name": "凤溪"}],
        "repair_history": [{"issue_id": "i1", "status": "failed"}],
    }
    annotated = [{"issue_id": "i1", "type": "fact_conflict", "fbi_diagnosis": {}}]
    diagnosis_set = MagicMock()

    prompt = agent._build_session_user_prompt(context, annotated, diagnosis_set)

    # 层1 应在层4之前，层4应在层6之前
    layer1_pos = prompt.find("[层1")
    layer4_pos = prompt.find("[层4")
    layer6_pos = prompt.find("[层6")

    assert layer1_pos < layer4_pos < layer6_pos


# ---------------------------------------------------------------------------
# 方案4 A3：闸门②接入patch验证
# ---------------------------------------------------------------------------


def test_gate2_rejects_patch_with_meta_term():
    """验证4：闸门②拒绝含meta术语的patch。"""
    from app.agents.fbi.review_blueprint_agent import FBIReviewBlueprintAgent
    from app.models.chapter_review import RepairWorkUnit

    agent = FBIReviewBlueprintAgent()
    work_units_args = {
        "work_units": [
            {
                "operation": "replace_exact",
                "old_text": "原始文本",
                "new_text": "这一章节的段落需要修改为更好的内容",
                "anchor_text": "锚点",
                "source_issue_ids": ["issue_1"],
            }
        ]
    }
    llm_issues = [{"issue_id": "issue_1"}]
    det_work_units: list[RepairWorkUnit] = []
    context = {"scene_contract": {"pov_character": "凤溪"}}

    result = agent._validate_gate2(
        work_units_args,
        llm_issues,
        det_work_units,
        diagnosis_set=FBIReviewDiagnosisSet(),
        context=context,
    )

    assert result["passed"] is False
    assert any("meta" in e.lower() or "术语" in e for e in result["errors"])


def test_gate2_accepts_valid_patch():
    """验证4：闸门②接受合规patch。"""
    from app.agents.fbi.review_blueprint_agent import FBIReviewBlueprintAgent
    from app.models.chapter_review import RepairWorkUnit

    agent = FBIReviewBlueprintAgent()
    work_units_args = {
        "work_units": [
            {
                "operation": "replace_exact",
                "old_text": "原始文本",
                "new_text": "凤溪拿起银线草，仔细端详着这株罕见的灵药",
                "anchor_text": "锚点",
                "source_issue_ids": ["issue_1"],
            }
        ]
    }
    llm_issues = [{"issue_id": "issue_1"}]
    det_work_units: list[RepairWorkUnit] = []
    context = {"scene_contract": {"pov_character": "凤溪"}}

    result = agent._validate_gate2(
        work_units_args,
        llm_issues,
        det_work_units,
        diagnosis_set=FBIReviewDiagnosisSet(),
        context=context,
    )

    assert result["passed"] is True
    assert result["errors"] == []


def test_gate2_skip_delete_exact_from_length_check():
    """验证4：delete_exact 操作跳过new_text长度校验。"""
    from app.agents.fbi.review_blueprint_agent import FBIReviewBlueprintAgent
    from app.models.chapter_review import RepairWorkUnit

    agent = FBIReviewBlueprintAgent()
    work_units_args = {
        "work_units": [
            {
                "operation": "delete_exact",
                "old_text": "要删除的文本",
                "new_text": "",  # delete 允许空new_text
                "source_issue_ids": ["issue_1"],
            }
        ]
    }
    llm_issues = [{"issue_id": "issue_1"}]
    det_work_units: list[RepairWorkUnit] = []

    result = agent._validate_gate2(
        work_units_args,
        llm_issues,
        det_work_units,
        diagnosis_set=FBIReviewDiagnosisSet(),
        context={},
    )

    # delete_exact 不应因长度校验失败
    assert not any("长度" in e or "length" in e.lower() for e in result["errors"])
