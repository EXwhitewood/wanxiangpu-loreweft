"""场景事实合同编译器测试。

使用抽象实体占位符（角色A/B、物品X、地点Y、组织Z、事件E），
遵循反污染原则，不引用任何具体作品角色名或道具名。
"""

from unittest.mock import patch

import pytest

from app.models.narrative_proposition import (
    Certainty,
    ClueConstraint,
    CompiledContract,
    FactConstraint,
    FactContract,
    Responsibility,
    ResponsibilityConstraint,
    SpatialConstraint,
    TemporalConstraint,
)
from app.services.scene_contract_compiler import SceneContractCompiler


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def compiler():
    return SceneContractCompiler()


@pytest.fixture
def base_compile_context():
    """基础编译上下文，使用抽象占位符。"""
    return {
        "raw_scene_contract": {
            "chapter_number": 1,
            "scene_index": 0,
            "pov_character": "角色A",
            "pov": {"character": "角色A"},
        },
        "scene_provenance": {
            "current_facts": {
                "established_facts": ["角色A在地点Y", "物品X在地点Y"],
                "character_states": {"角色A": "在地点Y"},
                "completed_events": ["事件E已发生"],
                "active_constraints": [],
            },
            "original_facts": {
                "character_fates": [
                    {"name": "角色B", "cause": "事件E", "timeline": "第一章", "antagonist": "组织Z"},
                ],
            },
            "spatial_anchor": {
                "current_location": "地点Y",
                "destination_location": "",
            },
            "timeline_anchor": {
                "forbidden_recap_events": ["事件E"],
                "opening_state": "初始状态",
                "ending_state": "结束状态",
            },
            "clues": [
                {
                    "description": "物品X上的指纹",
                    "source_actor": "角色A",
                    "placement_time": "事件E之前",
                    "discovery_condition": "角色A检查物品X",
                },
            ],
        },
        "chapter_state": {"chapter_number": 1},
        "foreshadowing_ops": [],
        "outline_beat": {},
        "genre_profile": {},
    }


def _normalized_contract_with_provenance(base_compile_context):
    """返回包含 scene_provenance 的标准化合同，供 mock 使用。

    _compile_spatial_constraints / _compile_temporal_constraints /
    _compile_clue_constraints 从 normalized_contract 中读取
    scene_provenance，因此 mock 返回值必须包含它。
    """
    contract = dict(base_compile_context["raw_scene_contract"])
    contract["scene_provenance"] = base_compile_context["scene_provenance"]
    return contract


# ---------------------------------------------------------------------------
# 测试：compile 返回 CompiledContract
# ---------------------------------------------------------------------------

class TestCompileResult:
    """测试编译结果的结构正确性。"""

    @pytest.mark.asyncio
    async def test_compile_returns_compiled_contract(self, compiler, base_compile_context):
        """compile 应返回 CompiledContract 实例。"""
        with patch("app.services.scene_contract_compiler.normalize_scene_contract_v2") as mock_norm:
            mock_norm.return_value = base_compile_context["raw_scene_contract"]
            result = await compiler.compile(base_compile_context)

        assert isinstance(result, CompiledContract)

    @pytest.mark.asyncio
    async def test_compile_has_fact_contract(self, compiler, base_compile_context):
        """编译结果应包含 fact_contract。"""
        with patch("app.services.scene_contract_compiler.normalize_scene_contract_v2") as mock_norm:
            mock_norm.return_value = base_compile_context["raw_scene_contract"]
            result = await compiler.compile(base_compile_context)

        assert isinstance(result.fact_contract, FactContract)


# ---------------------------------------------------------------------------
# 测试：current_facts 从 scene_provenance 提取
# ---------------------------------------------------------------------------

class TestCurrentFacts:
    """测试当前事实的提取。"""

    @pytest.mark.asyncio
    async def test_current_facts_from_scene_provenance(self, compiler, base_compile_context):
        """current_facts 应从 scene_provenance 中提取。"""
        with patch("app.services.scene_contract_compiler.normalize_scene_contract_v2") as mock_norm:
            mock_norm.return_value = base_compile_context["raw_scene_contract"]
            result = await compiler.compile(base_compile_context)

        assert len(result.fact_contract.current_facts) > 0
        # 应包含 established_facts 中的内容
        assert any("角色A" in f or "物品X" in f for f in result.fact_contract.current_facts)

    @pytest.mark.asyncio
    async def test_current_facts_include_completed_events(self, compiler, base_compile_context):
        """current_facts 应包含 completed_events。"""
        with patch("app.services.scene_contract_compiler.normalize_scene_contract_v2") as mock_norm:
            mock_norm.return_value = base_compile_context["raw_scene_contract"]
            result = await compiler.compile(base_compile_context)

        assert any("事件E" in f for f in result.fact_contract.current_facts)

    @pytest.mark.asyncio
    async def test_current_facts_include_character_states(self, compiler, base_compile_context):
        """current_facts 应包含 character_states。"""
        with patch("app.services.scene_contract_compiler.normalize_scene_contract_v2") as mock_norm:
            mock_norm.return_value = base_compile_context["raw_scene_contract"]
            result = await compiler.compile(base_compile_context)

        assert any("角色A" in f for f in result.fact_contract.current_facts)


# ---------------------------------------------------------------------------
# 测试：reference_facts 从 original_facts 提取
# ---------------------------------------------------------------------------

class TestReferenceFacts:
    """测试参考设定事实的提取。"""

    @pytest.mark.asyncio
    async def test_reference_facts_from_original_facts(self, compiler, base_compile_context):
        """reference_facts 应从 original_facts 中提取。"""
        with patch("app.services.scene_contract_compiler.normalize_scene_contract_v2") as mock_norm:
            mock_norm.return_value = base_compile_context["raw_scene_contract"]
            result = await compiler.compile(base_compile_context)

        assert len(result.fact_contract.reference_facts) > 0
        assert any("角色B" in f for f in result.fact_contract.reference_facts)


# ---------------------------------------------------------------------------
# 测试：forbidden_assertions 从 foreshadowing_ops 生成
# ---------------------------------------------------------------------------

class TestForbiddenAssertions:
    """测试禁止断言的生成。"""

    @pytest.mark.asyncio
    async def test_forbidden_assertions_from_foreshadowing_ops(self, compiler, base_compile_context):
        """未到达揭示窗口的伏笔应生成禁止断言。"""
        base_compile_context["foreshadowing_ops"] = [
            {
                "status": "planned",
                "name": "角色A的秘密",
                "secret_canonical_statement": "角色A是组织Z的卧底",
                "subject_role": "角色A",
                "reveal_window_start": 5,
            }
        ]

        with patch("app.services.scene_contract_compiler.normalize_scene_contract_v2") as mock_norm:
            mock_norm.return_value = base_compile_context["raw_scene_contract"]
            result = await compiler.compile(base_compile_context)

        assert len(result.fact_contract.forbidden_assertions) > 0
        fa = result.fact_contract.forbidden_assertions[0]
        assert fa.event_type == "foreshadowing_reveal"
        assert "confirmed" in fa.forbidden_certainty

    @pytest.mark.asyncio
    async def test_resolved_foreshadowing_no_forbidden(self, compiler, base_compile_context):
        """已解决的伏笔不应生成禁止断言。"""
        base_compile_context["foreshadowing_ops"] = [
            {
                "status": "resolved",
                "name": "角色A的秘密",
                "secret_canonical_statement": "角色A是组织Z的卧底",
            }
        ]

        with patch("app.services.scene_contract_compiler.normalize_scene_contract_v2") as mock_norm:
            mock_norm.return_value = base_compile_context["raw_scene_contract"]
            result = await compiler.compile(base_compile_context)

        # resolved 的伏笔不应生成禁止断言
        foreshadowing_fas = [
            fa for fa in result.fact_contract.forbidden_assertions
            if fa.event_type == "foreshadowing_reveal"
        ]
        assert len(foreshadowing_fas) == 0


# ---------------------------------------------------------------------------
# 测试：required_ambiguities 从大纲悬念标记生成
# ---------------------------------------------------------------------------

class TestRequiredAmbiguities:
    """测试必须保持模糊约束的生成。"""

    @pytest.mark.asyncio
    async def test_required_ambiguities_from_suspense_markers(self, compiler, base_compile_context):
        """大纲悬念标记应生成必须模糊约束。"""
        base_compile_context["outline_beat"] = {
            "suspense_markers": [
                {"description": "角色A的真实身份"},
            ]
        }

        with patch("app.services.scene_contract_compiler.normalize_scene_contract_v2") as mock_norm:
            mock_norm.return_value = base_compile_context["raw_scene_contract"]
            result = await compiler.compile(base_compile_context)

        assert len(result.fact_contract.required_ambiguities) > 0
        ra = result.fact_contract.required_ambiguities[0]
        assert ra.event_type == "suspense_preservation"
        assert "confirmed" in ra.forbidden_certainty

    @pytest.mark.asyncio
    async def test_required_ambiguities_from_mystery_field(self, compiler, base_compile_context):
        """大纲 mystery 字段包含悬念标记词应生成约束。"""
        base_compile_context["outline_beat"] = {
            "mystery": "角色A的真实身份是未揭示的",
        }

        with patch("app.services.scene_contract_compiler.normalize_scene_contract_v2") as mock_norm:
            mock_norm.return_value = base_compile_context["raw_scene_contract"]
            result = await compiler.compile(base_compile_context)

        assert len(result.fact_contract.required_ambiguities) > 0


# ---------------------------------------------------------------------------
# 测试：clue_constraints 从场景合同线索生成
# ---------------------------------------------------------------------------

class TestClueConstraints:
    """测试线索来源约束的生成。"""

    @pytest.mark.asyncio
    async def test_clue_constraints_from_scene_contract(self, compiler, base_compile_context):
        """场景合同中的线索应生成线索来源约束。"""
        normalized = _normalized_contract_with_provenance(base_compile_context)
        with patch("app.services.scene_contract_compiler.normalize_scene_contract_v2", return_value=normalized):
            result = await compiler.compile(base_compile_context)

        assert len(result.fact_contract.clue_constraints) > 0
        cc = result.fact_contract.clue_constraints[0]
        assert cc.clue_description == "物品X上的指纹"

    @pytest.mark.asyncio
    async def test_clue_constraints_missing_source(self, compiler, base_compile_context):
        """缺少来源的线索应标记 required_source_actor=True。"""
        base_compile_context["scene_provenance"]["clues"] = [
            {
                "description": "物品X上的指纹",
                "source_actor": "未知",
                "placement_time": "事件E之前",
                "discovery_condition": "角色A检查物品X",
            }
        ]
        normalized = _normalized_contract_with_provenance(base_compile_context)

        with patch("app.services.scene_contract_compiler.normalize_scene_contract_v2", return_value=normalized):
            result = await compiler.compile(base_compile_context)

        cc = result.fact_contract.clue_constraints[0]
        assert cc.required_source_actor is True


# ---------------------------------------------------------------------------
# 测试：spatial_constraints 从位置信息生成
# ---------------------------------------------------------------------------

class TestSpatialConstraints:
    """测试空间约束的生成。"""

    @pytest.mark.asyncio
    async def test_spatial_constraints_from_location(self, compiler, base_compile_context):
        """视角角色的位置信息应生成空间约束。"""
        normalized = _normalized_contract_with_provenance(base_compile_context)
        with patch("app.services.scene_contract_compiler.normalize_scene_contract_v2", return_value=normalized):
            result = await compiler.compile(base_compile_context)

        assert len(result.fact_contract.spatial_constraints) > 0
        sc = result.fact_contract.spatial_constraints[0]
        assert sc.subject_name == "角色A"
        assert "地点Y" in sc.allowed_locations


# ---------------------------------------------------------------------------
# 测试：temporal_constraints 从 forbidden_recap_events 生成
# ---------------------------------------------------------------------------

class TestTemporalConstraints:
    """测试时间约束的生成。"""

    @pytest.mark.asyncio
    async def test_temporal_constraints_from_forbidden_recap(self, compiler, base_compile_context):
        """forbidden_recap_events 应生成 no_replay 时间约束。"""
        normalized = _normalized_contract_with_provenance(base_compile_context)
        with patch("app.services.scene_contract_compiler.normalize_scene_contract_v2", return_value=normalized):
            result = await compiler.compile(base_compile_context)

        no_replay_constraints = [
            tc for tc in result.fact_contract.temporal_constraints
            if tc.no_replay
        ]
        assert len(no_replay_constraints) > 0
        assert any("事件E" in tc.event_description for tc in no_replay_constraints)


# ---------------------------------------------------------------------------
# 测试：blocked_contract
# ---------------------------------------------------------------------------

class TestBlockedContract:
    """测试合同内部冲突导致 blocked_contract。"""

    @pytest.mark.asyncio
    async def test_blocked_contract_when_internal_conflicts(self, compiler):
        """合同内部冲突应标记 blocked_contract=True。"""
        context = {
            "raw_scene_contract": {
                "chapter_number": 1,
                "scene_index": 0,
                "pov": {"character": "角色A"},
                "source_of_truth": {
                    "must_show": ["foreshadowing_reveal"],
                },
            },
            "scene_provenance": {},
            "chapter_state": {},
            "foreshadowing_ops": [],
            "outline_beat": {},
            "genre_profile": {},
        }

        # 构造一个会产生 forbidden_assertions 与 must_show 冲突的场景
        with patch("app.services.scene_contract_compiler.normalize_scene_contract_v2") as mock_norm:
            mock_norm.return_value = context["raw_scene_contract"]

            # 手动注入冲突：forbidden_assertions 包含 must_show 中的事件
            with patch.object(
                compiler, "_compile_forbidden_assertions",
                return_value=[
                    FactConstraint(
                        event_type="foreshadowing_reveal",
                        reason="伏笔未到揭示窗口",
                    )
                ]
            ):
                result = await compiler.compile(context)

        # must_show 包含 "foreshadowing_reveal"，与 forbidden_assertions 冲突
        assert result.blocked_contract is True
        assert len(result.compiler_warnings) > 0

    @pytest.mark.asyncio
    async def test_not_blocked_when_no_conflicts(self, compiler, base_compile_context):
        """无内部冲突时 blocked_contract 应为 False。"""
        with patch("app.services.scene_contract_compiler.normalize_scene_contract_v2") as mock_norm:
            mock_norm.return_value = base_compile_context["raw_scene_contract"]
            result = await compiler.compile(base_compile_context)

        assert result.blocked_contract is False


class TestLongTermMemoryConstraints:
    @pytest.mark.asyncio
    async def test_long_term_constraints_compile_into_fact_contract(self, compiler, base_compile_context):
        base_compile_context["raw_scene_contract"]["long_term_constraints"] = {
            "required_prior_facts": [
                {"type": "established_facts", "text": "CharacterA opened ObjectX."}
            ],
            "character_state_constraints": [
                {
                    "character_name": "CharacterA",
                    "state": {"location": "PlaceY"},
                    "constraint": "do_not_contradict_without_explicit_transition",
                }
            ],
            "proposition_constraints": [
                {
                    "claim": "CharacterA suspects OrganizationZ",
                    "constraint": "do_not_upgrade_to_confirmed_fact_without_evidence",
                    "source_text": "CharacterA suspects OrganizationZ.",
                }
            ],
            "foreshadowing_constraints": [
                {
                    "name": "ObjectX origin",
                    "action": "protect_secret",
                    "constraint": "forbid_premature_reveal",
                    "reader_intended_state": "suspicious",
                }
            ],
        }

        with patch("app.services.scene_contract_compiler.normalize_scene_contract_v2") as mock_norm:
            mock_norm.return_value = base_compile_context["raw_scene_contract"]
            result = await compiler.compile(base_compile_context)

        facts = result.fact_contract.current_facts
        assert any("[established_facts] CharacterA opened ObjectX." in f for f in facts)
        assert any("[character_state] CharacterA" in f and "PlaceY" in f for f in facts)
        assert any("[proposition] CharacterA suspects OrganizationZ" in f for f in facts)
        assert any("[foreshadowing:protect_secret] ObjectX origin" in f for f in facts)
        assert any(
            item.event_type == "long_term_proposition_boundary"
            for item in result.fact_contract.required_ambiguities
        )
        assert any(
            item.event_type == "foreshadowing_boundary"
            for item in result.fact_contract.forbidden_assertions
        )
        assert any(
            item.event_type == "long_term_foreshadowing_boundary"
            for item in result.fact_contract.required_ambiguities
        )

    @pytest.mark.asyncio
    async def test_reveal_window_foreshadowing_can_expose_secret(self, compiler, base_compile_context):
        base_compile_context["raw_scene_contract"]["long_term_constraints"] = {
            "foreshadowing_constraints": [
                {
                    "name": "ObjectX origin",
                    "action": "reveal",
                    "constraint": "resolve_or_advance_reveal_window",
                    "secret_canonical_statement": "ObjectX was forged by OrganizationZ.",
                }
            ],
        }

        with patch("app.services.scene_contract_compiler.normalize_scene_contract_v2") as mock_norm:
            mock_norm.return_value = base_compile_context["raw_scene_contract"]
            result = await compiler.compile(base_compile_context)

        facts = result.fact_contract.current_facts
        assert any("ObjectX was forged by OrganizationZ." in f for f in facts)
        assert not any(
            item.event_type == "long_term_foreshadowing_boundary"
            for item in result.fact_contract.required_ambiguities
        )
