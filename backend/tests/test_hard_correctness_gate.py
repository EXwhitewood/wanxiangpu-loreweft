"""硬正确性门控测试。

使用抽象实体占位符（角色A/B、物品X、地点Y、组织Z、事件E），
遵循反污染原则，不引用任何具体作品角色名或道具名。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.narrative_proposition import (
    AuditReport,
    AuditViolation,
    ExtractionResult,
    FactContract,
    NarrativeEntity,
    NarrativePredicate,
    NarrativeProposition,
)
from app.services.quality_gate import QualityGate, _is_absence_only_authority_fact
from app.agents.scene_validator import SceneValidator


def test_missing_context_is_not_an_authority_fact():
    assert _is_absence_only_authority_fact(
        "已知道具上有符号，但上下文未提及另一件道具。"
    )
    assert _is_absence_only_authority_fact(
        "The compressed context does not provide an earlier mention of the item."
    )
    assert not _is_absence_only_authority_fact(
        "The item was explicitly destroyed in the previous scene."
    )


@pytest.mark.asyncio
async def test_consistency_discards_conflict_based_only_on_missing_context(gate):
    validator_result = {
        "consistency": {
            "conflicts": [{
                "fact": "已知道具上有符号，但上下文未提及另一件道具。",
                "text_claim": "角色手中的另一件道具发亮。",
                "category": "identity_conflict",
                "severity": "critical",
            }],
        },
    }

    with patch(
        "app.agents.scene_validator.SceneValidator.execute",
        new_callable=AsyncMock,
        return_value=validator_result,
    ):
        result = await gate._run_consistency(
            "角色手中的另一件道具发亮。",
            {"scene_id": "scene_1"},
            {},
            {},
            {},
            [],
            "hash",
            0,
        )

    assert result["violations"] == []


@pytest.mark.asyncio
async def test_consistency_discards_ungrounded_duplicate_passage_claim(gate):
    text = (
        "角色A感到寒冷，看见光点正在飞散。\n\n"
        "光点被拉回身体，角色A随后才睁开眼睛。"
    )
    validator_result = {
        "consistency": {
            "conflicts": [{
                "fact": "正文有两段几乎完全相同的描述（从“感到寒冷”到“睁开眼睛”重复）。",
                "text_claim": "第二段重复叙述了第一段。",
                "authority_source": "current_text",
                "evidence_spans": ["感到寒冷", "睁开眼睛"],
                "category": "internal_conflict",
                "severity": "critical",
            }],
        },
    }

    with patch(
        "app.agents.scene_validator.SceneValidator.execute",
        new_callable=AsyncMock,
        return_value=validator_result,
    ):
        result = await gate._run_consistency(
            text,
            {"scene_id": "scene_1"},
            {},
            {},
            {},
            [],
            "hash",
            0,
        )

    assert result["violations"] == []
    rejection = result["raw_report"]["evidence_rejections"][0]
    assert rejection["reason"] == "duplicate_claim_lacks_two_similar_verbatim_passages"
    assert rejection["passed"] is False


@pytest.mark.asyncio
async def test_consistency_keeps_duplicate_claim_with_two_similar_verbatim_passages(gate):
    first = "角色A把钥匙放在桌上，然后退到门边，等待对方开口。"
    second = "角色A再次把钥匙放在桌上，然后退到门边，等待对方开口。"
    text = f"{first}\n\n一段过渡。\n\n{second}"
    validator_result = {
        "consistency": {
            "conflicts": [{
                "fact": "同一放置钥匙事件被重复叙述两次。",
                "text_claim": "两段文字重复了同一事件。",
                "authority_source": "current_text",
                "evidence_spans": [first, second],
                "category": "internal_conflict",
                "severity": "critical",
                "suggestion": "删除其中一段。",
            }],
        },
    }

    with patch(
        "app.agents.scene_validator.SceneValidator.execute",
        new_callable=AsyncMock,
        return_value=validator_result,
    ):
        result = await gate._run_consistency(
            text,
            {"scene_id": "scene_1"},
            {},
            {},
            {},
            [],
            "hash",
            0,
        )

    assert len(result["violations"]) == 1
    violation = result["violations"][0]
    assert violation["type"] == "internal_conflict"
    assert violation["target_span"] == second
    audit = violation["evidence"]["consistency_evidence_audit"]
    assert audit["passed"] is True
    assert audit["best_similarity"] >= 0.72


@pytest.mark.asyncio
async def test_consistency_discards_identity_conflict_without_declared_verbatim_evidence(gate):
    text = "角色A先认出伙伴，随后问他们是谁。"
    validator_result = {
        "consistency": {
            "conflicts": [{
                "fact": "角色A已认出伙伴。",
                "text_claim": "角色A后来表示不认识伙伴。",
                "category": "identity_conflict",
                "severity": "critical",
            }],
        },
    }

    with patch(
        "app.agents.scene_validator.SceneValidator.execute",
        new_callable=AsyncMock,
        return_value=validator_result,
    ):
        result = await gate._run_consistency(
            text, {"scene_id": "scene_1"}, {}, {}, {}, [], "hash", 0,
        )

    assert result["violations"] == []
    rejection = result["raw_report"]["evidence_rejections"][0]
    assert rejection["reason"] == "hard_conflict_lacks_declared_verbatim_evidence"


@pytest.mark.asyncio
async def test_consistency_discards_state_change_explained_by_transition_evidence(gate):
    earlier = "角色A逐一喊出了伙伴的称呼。"
    transition = "灵魂重塑的代价是记忆消失。"
    later = "角色A问：你们是谁？"
    text = f"{earlier}\n\n{transition}\n\n{later}"
    validator_result = {
        "consistency": {
            "conflicts": [{
                "fact": "角色A先认出伙伴。",
                "text_claim": "角色A后来不认识伙伴。",
                "authority_source": "current_text",
                "evidence_spans": [earlier, later],
                "transition_evidence": transition,
                "category": "identity_conflict",
                "severity": "critical",
            }],
        },
    }

    with patch(
        "app.agents.scene_validator.SceneValidator.execute",
        new_callable=AsyncMock,
        return_value=validator_result,
    ):
        result = await gate._run_consistency(
            text, {"scene_id": "scene_1"}, {}, {}, {}, [], "hash", 0,
        )

    assert result["violations"] == []
    rejection = result["raw_report"]["evidence_rejections"][0]
    assert rejection["reason"] == "explicit_transition_evidence_explains_state_change"


@pytest.mark.asyncio
async def test_consistency_forwards_same_chapter_chronology_to_validator(gate):
    captured = {}

    async def fake_execute(self, context):
        captured.update(context)
        return {"consistency": {"conflicts": []}}

    with patch(
        "app.agents.scene_validator.SceneValidator.execute",
        new=fake_execute,
    ):
        await gate._run_consistency(
            "当前场景延续新状态。",
            {"scene_id": "scene_2"},
            {},
            {"mark": "old"},
            {},
            [],
            "hash",
            0,
            previous_scene_ending="上一场景明确写出印记消失。",
            previous_scenes_summary="印记先变灰，随后消失。",
        )

    assert captured["previous_scene_ending"] == "上一场景明确写出印记消失。"
    assert captured["previous_scenes_summary"] == "印记先变灰，随后消失。"


@pytest.mark.asyncio
async def test_scene_validator_prompt_gives_prior_scene_transition_newer_authority(monkeypatch):
    captured = {}

    class FakeLLM:
        async def generate(self, **kwargs):
            captured.update(kwargs)
            return (
                '{"consistency":{"pass":true,"conflicts":[]},'
                '"contract_compliance":{"violations":[]},'
                '"reader_experience":{"scores":{},"advisories":[]}}'
            )

    validator = SceneValidator()

    async def fake_get_llm_client():
        return FakeLLM()

    monkeypatch.setattr(validator, "get_llm_client", fake_get_llm_client)
    result = await validator._unified_llm_check(
        generated_text="她抬起光滑的手掌。",
        scene_contract={"scene_id": "scene_2"},
        core_facts={},
        current_state={"mark": "gold"},
        chapter_state={},
        character_cards=[],
        character_names=[],
        chapter_number=28,
        scene_index=1,
        genre="",
        target_reader="",
        previous_scene_ending="金色纹路消失得干干净净。",
        previous_scenes_summary="禁术令金线变灰并断裂。",
    )

    assert result["consistency"]["conflicts"] == []
    assert "CHRONOLOGY PRECEDENCE RULE" in captured["system_prompt"]
    assert "CONSISTENCY EVIDENCE RULE" in captured["system_prompt"]
    assert '"evidence_spans"' in captured["system_prompt"]
    assert "金色纹路消失得干干净净" in captured["user_prompt"]
    assert "不得要求正文恢复较早基线" in captured["user_prompt"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def gate():
    return QualityGate()


@pytest.fixture
def base_gate_context():
    """基础门控上下文，使用抽象占位符。"""
    return {
        "generated_text": "角色A在地点Y发现了物品X。这是正常的叙事文本。",
        "scene_contract": {
            "chapter_number": 1,
            "scene_index": 0,
            "pov_character": "角色A",
        },
        "chapter_state": {"chapter_number": 1},
        "character_cards": [],
        "character_names": ["角色A"],
        "project_id": "test-project",
        "chapter_number": 1,
        "scene_index": 0,
        "core_facts": {},
        "current_state": {},
    }


def _make_extraction_result_with_violations():
    """构造包含违规的抽取+审计结果。"""
    prop = NarrativeProposition(
        proposition_id="test-prop-1",
        project_id="test-project",
        chapter_number=1,
        scene_index=0,
        subject=NarrativeEntity(name="角色A", entity_type="character"),
        predicate=NarrativePredicate(name="位于", category="location"),
        object=None,
        truth_layer="current",
        certainty="confirmed",
        responsibility="active_actor",
        source_text="角色A在地点Y",
    )

    extraction = ExtractionResult(
        propositions=[prop],
        entity_mentions=[prop.subject],
    )

    audit = AuditReport(
        passed=False,
        commit_blocked=True,
        violations=[
            AuditViolation(
                type="spatial_conflict",
                severity="high",
                blocks_commit=True,
                target_span="角色A在地点Y",
                expected_behavior="角色A不应同时出现在两个地点",
                suggested_strategy="patch_text",
            )
        ],
        proposition_ids=["test-prop-1"],
    )

    return extraction, audit


def _make_extraction_result_clean():
    """构造无违规的抽取+审计结果。"""
    prop = NarrativeProposition(
        proposition_id="test-prop-clean",
        project_id="test-project",
        chapter_number=1,
        scene_index=0,
        subject=NarrativeEntity(name="角色A", entity_type="character"),
        predicate=NarrativePredicate(name="行走", category="action"),
        object=None,
        truth_layer="current",
        certainty="confirmed",
        responsibility="active_actor",
        source_text="角色A在行走",
    )

    extraction = ExtractionResult(
        propositions=[prop],
        entity_mentions=[prop.subject],
    )

    audit = AuditReport(
        passed=True,
        commit_blocked=False,
        violations=[],
        proposition_ids=["test-prop-clean"],
    )

    return extraction, audit


# ---------------------------------------------------------------------------
# 测试：QualityGate.evaluate 返回分层报告
# ---------------------------------------------------------------------------

class TestLayeredReports:
    """测试门控返回分层报告。"""

    @pytest.mark.asyncio
    async def test_evaluate_returns_layered_reports(self, gate, base_gate_context):
        """evaluate 应返回包含分层报告的结果。"""
        # 禁用命题层以避免 LLM 调用
        policy = {"proposition_extraction_mode": "off", "narrative_contract_mode": "off"}
        base_gate_context["generation_feature_policy"] = policy

        with patch.object(gate, "_run_deterministic", return_value=[]), \
             patch.object(gate, "_run_quality_checkers", return_value=[]), \
             patch.object(gate, "_run_consistency", return_value={"violations": []}), \
             patch.object(gate, "_run_semantic", return_value=[]), \
             patch.object(gate, "_run_fcip", return_value={"violations": [], "detection": {}}):
            result = await gate.evaluate(base_gate_context)

        assert "reports" in result
        assert "hard_correctness" in result["reports"]
        assert "narrative_contract" in result["reports"]
        assert "style_quality" in result["reports"]
        assert "reader_experience" in result["reports"]

    @pytest.mark.asyncio
    async def test_evaluate_has_layer_summary(self, gate, base_gate_context):
        """evaluate 应返回 layer_summary。"""
        policy = {"proposition_extraction_mode": "off", "narrative_contract_mode": "off"}
        base_gate_context["generation_feature_policy"] = policy

        with patch.object(gate, "_run_deterministic", return_value=[]), \
             patch.object(gate, "_run_quality_checkers", return_value=[]), \
             patch.object(gate, "_run_consistency", return_value={"violations": []}), \
             patch.object(gate, "_run_semantic", return_value=[]), \
             patch.object(gate, "_run_fcip", return_value={"violations": [], "detection": {}}):
            result = await gate.evaluate(base_gate_context)

        assert "layer_summary" in result
        assert "hard_correctness" in result["layer_summary"]
        assert "narrative_contract" in result["layer_summary"]


# ---------------------------------------------------------------------------
# 测试：hard_correctness 报告包含命题审计结果
# ---------------------------------------------------------------------------

class TestHardCorrectnessReport:
    """测试 hard_correctness 层包含命题审计结果。"""

    @pytest.mark.asyncio
    async def test_hard_correctness_includes_audit_results(self, gate, base_gate_context):
        """hard_correctness 报告应包含命题审计结果。"""
        policy = {"proposition_extraction_mode": "enforce", "narrative_contract_mode": "off"}
        base_gate_context["generation_feature_policy"] = policy

        extraction, audit = _make_extraction_result_with_violations()

        with patch.object(gate, "_run_proposition_layer", return_value=(extraction, audit, None, [])), \
             patch.object(gate, "_run_deterministic", return_value=[]), \
             patch.object(gate, "_run_quality_checkers", return_value=[]), \
             patch.object(gate, "_run_consistency", return_value={"violations": []}), \
             patch.object(gate, "_run_semantic", return_value=[]), \
             patch.object(gate, "_run_fcip", return_value={"violations": [], "detection": {}}):
            result = await gate.evaluate(base_gate_context)

        assert "extraction" in result["reports"]["hard_correctness"]
        assert "audit" in result["reports"]["hard_correctness"]
        assert result["reports"]["hard_correctness"]["audit"]["passed"] is False


# ---------------------------------------------------------------------------
# 测试：shadow 模式
# ---------------------------------------------------------------------------

class TestShadowMode:
    """测试 shadow 模式：抽取运行但违规不阻断。"""

    @pytest.mark.asyncio
    async def test_shadow_mode_violations_do_not_block(self, gate, base_gate_context):
        """shadow 模式下违规不阻断提交。"""
        policy = {"proposition_extraction_mode": "shadow", "narrative_contract_mode": "off"}
        base_gate_context["generation_feature_policy"] = policy

        extraction, audit = _make_extraction_result_with_violations()

        # shadow 模式下 _run_proposition_layer 返回空 violations（不添加到 all_violations）
        with patch.object(gate, "_run_proposition_layer", return_value=(extraction, audit, None, [])), \
             patch.object(gate, "_run_deterministic", return_value=[]), \
             patch.object(gate, "_run_quality_checkers", return_value=[]), \
             patch.object(gate, "_run_consistency", return_value={"violations": []}), \
             patch.object(gate, "_run_semantic", return_value=[]), \
             patch.object(gate, "_run_fcip", return_value={"violations": [], "detection": {}}):
            result = await gate.evaluate(base_gate_context)

        # shadow 模式下命题层违规不应添加到 all_violations
        # 但报告仍然记录审计结果
        assert result["reports"]["hard_correctness"]["audit"]["passed"] is False
        # 没有命题层违规被添加到主违规列表
        prop_violation_types = [
            v["type"] for v in result["violations"]
            if v.get("source") == "hard_correctness"
        ]
        assert "spatial_conflict" not in prop_violation_types


# ---------------------------------------------------------------------------
# 测试：report 模式
# ---------------------------------------------------------------------------

class TestReportMode:
    """测试 report 模式：违规添加但 blocks_commit=False。"""

    @pytest.mark.asyncio
    async def test_report_mode_violations_added_but_not_blocking(self, gate, base_gate_context):
        """report 模式下违规添加但 blocks_commit=False。"""
        policy = {"proposition_extraction_mode": "report", "narrative_contract_mode": "off"}
        base_gate_context["generation_feature_policy"] = policy

        extraction, audit = _make_extraction_result_with_violations()

        # 构造 report 模式下的 violations（blocks_commit=False）
        from app.models.violation import make_violation
        prop_violations = [
            make_violation(
                vtype="spatial_conflict",
                severity="high",
                detail="角色A不应同时出现在两个地点",
                source="hard_correctness",
                blocks_commit=False,
            )
        ]

        with patch.object(gate, "_run_proposition_layer", return_value=(extraction, audit, None, prop_violations)), \
             patch.object(gate, "_run_deterministic", return_value=[]), \
             patch.object(gate, "_run_quality_checkers", return_value=[]), \
             patch.object(gate, "_run_consistency", return_value={"violations": []}), \
             patch.object(gate, "_run_semantic", return_value=[]), \
             patch.object(gate, "_run_fcip", return_value={"violations": [], "detection": {}}):
            result = await gate.evaluate(base_gate_context)

        # report 模式下违规应存在但 blocks_commit=False
        spatial_violations = [
            v for v in result["violations"]
            if v["type"] == "spatial_conflict"
        ]
        assert len(spatial_violations) > 0
        for v in spatial_violations:
            assert v["blocks_commit"] is False


# ---------------------------------------------------------------------------
# 测试：enforce 模式
# ---------------------------------------------------------------------------

class TestEnforceMode:
    """测试 enforce 模式：违规阻断提交。"""

    @pytest.mark.asyncio
    async def test_enforce_mode_violations_block_commit(self, gate, base_gate_context):
        """enforce 模式下违规应阻断提交。"""
        policy = {"proposition_extraction_mode": "enforce", "narrative_contract_mode": "off"}
        base_gate_context["generation_feature_policy"] = policy

        extraction, audit = _make_extraction_result_with_violations()

        from app.models.violation import make_violation
        prop_violations = [
            make_violation(
                vtype="spatial_conflict",
                severity="high",
                detail="角色A不应同时出现在两个地点",
                source="hard_correctness",
                blocks_commit=True,
            )
        ]

        with patch.object(gate, "_run_proposition_layer", return_value=(extraction, audit, None, prop_violations)), \
             patch.object(gate, "_run_deterministic", return_value=[]), \
             patch.object(gate, "_run_quality_checkers", return_value=[]), \
             patch.object(gate, "_run_consistency", return_value={"violations": []}), \
             patch.object(gate, "_run_semantic", return_value=[]), \
             patch.object(gate, "_run_fcip", return_value={"violations": [], "detection": {}}):
            result = await gate.evaluate(base_gate_context)

        assert result["commit_blocked"] is True
        assert result["passed"] is False
        spatial_violations = [
            v for v in result["violations"]
            if v["type"] == "spatial_conflict"
        ]
        assert len(spatial_violations) > 0
        assert any(v["blocks_commit"] for v in spatial_violations)


# ---------------------------------------------------------------------------
# 测试：向后兼容（现有检查仍正常工作）
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    """测试现有检查在引入命题层后仍正常工作。"""

    @pytest.mark.asyncio
    async def test_empty_text_still_blocks(self, gate):
        """空文本仍应阻断提交。"""
        context = {
            "generated_text": "",
            "scene_contract": {},
        }

        result = await gate.evaluate(context)

        assert result["passed"] is False
        assert result["commit_blocked"] is True
        violation_types = [v["type"] for v in result["violations"]]
        assert "empty_text" in violation_types

    @pytest.mark.asyncio
    async def test_missing_contract_still_blocks(self, gate):
        """缺少场景合同仍应阻断提交。"""
        context = {
            "generated_text": "角色A在地点Y行走。",
            "scene_contract": None,
        }

        result = await gate.evaluate(context)

        assert result["passed"] is False
        assert result["commit_blocked"] is True
        violation_types = [v["type"] for v in result["violations"]]
        assert "missing_contract" in violation_types

    @pytest.mark.asyncio
    async def test_deterministic_checks_still_run(self, gate, base_gate_context):
        """确定性检查仍应正常运行。"""
        policy = {"proposition_extraction_mode": "off", "narrative_contract_mode": "off"}
        base_gate_context["generation_feature_policy"] = policy

        with patch.object(gate, "_run_deterministic", return_value=[]) as mock_det, \
             patch.object(gate, "_run_quality_checkers", return_value=[]), \
             patch.object(gate, "_run_consistency", return_value={"violations": []}), \
             patch.object(gate, "_run_semantic", return_value=[]), \
             patch.object(gate, "_run_fcip", return_value={"violations": [], "detection": {}}):
            result = await gate.evaluate(base_gate_context)

        mock_det.assert_called_once()

    @pytest.mark.asyncio
    async def test_consistency_checks_still_run(self, gate, base_gate_context):
        """一致性检查仍应正常运行。"""
        policy = {"proposition_extraction_mode": "off", "narrative_contract_mode": "off"}
        base_gate_context["generation_feature_policy"] = policy

        with patch.object(gate, "_run_deterministic", return_value=[]), \
             patch.object(gate, "_run_quality_checkers", return_value=[]), \
             patch.object(gate, "_run_consistency", return_value={"violations": []}) as mock_cons, \
             patch.object(gate, "_run_semantic", return_value=[]), \
             patch.object(gate, "_run_fcip", return_value={"violations": [], "detection": {}}):
            result = await gate.evaluate(base_gate_context)

        mock_cons.assert_called_once()

    @pytest.mark.asyncio
    async def test_proposition_layer_off_skips_extraction(self, gate, base_gate_context):
        """proposition_extraction_mode=off 时应跳过命题抽取。"""
        policy = {"proposition_extraction_mode": "off", "narrative_contract_mode": "off"}
        base_gate_context["generation_feature_policy"] = policy

        with patch.object(gate, "_run_proposition_layer") as mock_prop, \
             patch.object(gate, "_run_deterministic", return_value=[]), \
             patch.object(gate, "_run_quality_checkers", return_value=[]), \
             patch.object(gate, "_run_consistency", return_value={"violations": []}), \
             patch.object(gate, "_run_semantic", return_value=[]), \
             patch.object(gate, "_run_fcip", return_value={"violations": [], "detection": {}}):
            result = await gate.evaluate(base_gate_context)

        mock_prop.assert_not_called()
