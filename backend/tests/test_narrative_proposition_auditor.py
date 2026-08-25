"""叙事命题审计器测试。

使用抽象实体占位符（角色A/B、物品X、地点Y、组织Z、事件E），
遵循反污染原则，不引用任何具体作品角色名或道具名。
"""

import uuid

import pytest

from app.models.narrative_proposition import (
    AuditReport,
    AuditViolation,
    Certainty,
    ClueConstraint,
    FactConstraint,
    FactContract,
    NarrativeEntity,
    NarrativePredicate,
    NarrativeProposition,
    Responsibility,
    ResponsibilityConstraint,
    SpatialConstraint,
    TemporalConstraint,
    TruthLayer,
)
from app.services.narrative_proposition_auditor import NarrativePropositionAuditor


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def auditor():
    return NarrativePropositionAuditor()


def _make_proposition(
    subject_name: str = "角色A",
    subject_type: str = "character",
    predicate_name: str = "持有",
    predicate_category: str = "ownership",
    object_name: str = "物品X",
    object_type: str = "item",
    truth_layer: str = "current",
    certainty: str = "confirmed",
    polarity: str = "affirmed",
    responsibility: str = "active_actor",
    time_scope: str = "",
    location_scope: str = "地点Y",
    source_text: str = "角色A持有物品X",
    confidence: float = 0.95,
) -> NarrativeProposition:
    """生成一条标准命题。"""
    return NarrativeProposition(
        proposition_id=str(uuid.uuid4()),
        project_id="test-project",
        chapter_number=1,
        scene_index=0,
        subject=NarrativeEntity(name=subject_name, entity_type=subject_type),
        predicate=NarrativePredicate(name=predicate_name, category=predicate_category),
        object=NarrativeEntity(name=object_name, entity_type=object_type) if object_name else None,
        truth_layer=truth_layer,
        certainty=certainty,
        polarity=polarity,
        responsibility=responsibility,
        time_scope=time_scope,
        location_scope=location_scope,
        source_text=source_text,
        confidence=confidence,
    )


@pytest.fixture
def empty_fact_contract():
    return FactContract()


@pytest.fixture
def base_audit_context(empty_fact_contract):
    """基础审计上下文。"""
    return {
        "propositions": [],
        "fact_contract": empty_fact_contract,
        "scene_provenance": {},
        "previous_propositions": [],
    }


# ---------------------------------------------------------------------------
# 测试：truth_layer_conflict
# ---------------------------------------------------------------------------

class TestTruthLayerConflict:
    """测试非当前层声明被当作 current 写入的冲突检测。"""

    @pytest.mark.asyncio
    async def test_reference_written_as_current(self, auditor):
        """reference 层的声明被当作 current 写入应触发违规。"""
        # 历史命题：角色A持有物品X 属于 reference 层
        previous = [
            _make_proposition(
                subject_name="角色A",
                predicate_name="持有",
                object_name="物品X",
                truth_layer="reference",
                source_text="据原书记载，角色A持有物品X",
            )
        ]
        # 当前命题：同样的声明但标记为 current
        current = [
            _make_proposition(
                subject_name="角色A",
                predicate_name="持有",
                object_name="物品X",
                truth_layer="current",
                source_text="角色A持有物品X",
            )
        ]

        context = {
            "propositions": current,
            "fact_contract": FactContract(),
            "scene_provenance": {},
            "previous_propositions": previous,
        }

        report = await auditor.audit(context)

        violation_types = [v.type for v in report.violations]
        assert "truth_layer_conflict" in violation_types

    @pytest.mark.asyncio
    async def test_reference_facts_in_contract_conflict(self, auditor):
        """fact_contract.reference_facts 中的事实被当作 current 写入应触发违规。"""
        current = [
            _make_proposition(
                subject_name="角色A",
                predicate_name="持有",
                object_name="物品X",
                truth_layer="current",
                source_text="角色A持有物品X",
            )
        ]

        fact_contract = FactContract(
            reference_facts=["角色A持有物品X"],
        )

        context = {
            "propositions": current,
            "fact_contract": fact_contract,
            "scene_provenance": {},
            "previous_propositions": [],
        }

        report = await auditor.audit(context)

        violation_types = [v.type for v in report.violations]
        assert "truth_layer_conflict" in violation_types


# ---------------------------------------------------------------------------
# 测试：certainty_escalation
# ---------------------------------------------------------------------------

class TestCertaintyEscalation:
    """测试不确定事实被升级为 confirmed 的检测。"""

    @pytest.mark.asyncio
    async def test_accused_escalated_to_confirmed(self, auditor):
        """accused 升级为 confirmed 应触发违规。"""
        current = [
            _make_proposition(
                subject_name="角色A",
                predicate_name="犯罪",
                object_name="",
                certainty="confirmed",
                source_text="角色A确实犯罪了",
            )
        ]

        fact_contract = FactContract(
            required_ambiguities=[
                FactConstraint(
                    event_type="犯罪",
                    subject_role="角色A",
                    forbidden_certainty=["confirmed"],
                    allowed_certainty=["accused", "suspected"],
                    reason="该事件应保持不确定",
                )
            ]
        )

        context = {
            "propositions": current,
            "fact_contract": fact_contract,
            "scene_provenance": {},
            "previous_propositions": [],
        }

        report = await auditor.audit(context)

        violation_types = [v.type for v in report.violations]
        assert "certainty_escalation" in violation_types


# ---------------------------------------------------------------------------
# 测试：responsibility_polarity_conflict
# ---------------------------------------------------------------------------

class TestResponsibilityPolarityConflict:
    """测试同一事件责任归属极性冲突检测。"""

    @pytest.mark.asyncio
    async def test_active_actor_vs_framed(self, auditor):
        """同一事件中 active_actor 与 framed 冲突应触发违规。"""
        current = [
            _make_proposition(
                subject_name="角色A",
                predicate_name="犯罪",
                object_name="",
                responsibility="active_actor",
                source_text="角色A主动犯罪",
            ),
            _make_proposition(
                subject_name="角色A",
                predicate_name="犯罪",
                object_name="",
                responsibility="framed",
                source_text="角色A是被栽赃的",
            ),
        ]

        context = {
            "propositions": current,
            "fact_contract": FactContract(),
            "scene_provenance": {},
            "previous_propositions": [],
        }

        report = await auditor.audit(context)

        violation_types = [v.type for v in report.violations]
        assert "responsibility_polarity_conflict" in violation_types

    @pytest.mark.asyncio
    async def test_responsibility_conflict_across_current_and_previous(self, auditor):
        """当前命题与历史命题之间的责任归属冲突也应检测。"""
        previous = [
            _make_proposition(
                subject_name="角色A",
                predicate_name="犯罪",
                object_name="",
                responsibility="active_actor",
                source_text="角色A主动犯罪",
            )
        ]
        current = [
            _make_proposition(
                subject_name="角色A",
                predicate_name="犯罪",
                object_name="",
                responsibility="framed",
                source_text="角色A是被栽赃的",
            )
        ]

        context = {
            "propositions": current,
            "fact_contract": FactContract(),
            "scene_provenance": {},
            "previous_propositions": previous,
        }

        report = await auditor.audit(context)

        violation_types = [v.type for v in report.violations]
        assert "responsibility_polarity_conflict" in violation_types


# ---------------------------------------------------------------------------
# 测试：spatial_conflict
# ---------------------------------------------------------------------------

class TestSpatialConflict:
    """测试同一角色同时出现在两个位置的冲突检测。"""

    @pytest.mark.asyncio
    async def test_same_character_at_two_locations(self, auditor):
        """同一角色同一时间出现在两个位置应触发空间冲突。"""
        current = [
            _make_proposition(
                subject_name="角色A",
                predicate_name="位于",
                predicate_category="location",
                object_name="",
                time_scope="同一天",
                location_scope="地点Y",
                source_text="角色A在地点Y",
            ),
            _make_proposition(
                subject_name="角色A",
                predicate_name="位于",
                predicate_category="location",
                object_name="",
                time_scope="同一天",
                location_scope="地点Z",
                source_text="角色A在地点Z",
            ),
        ]

        context = {
            "propositions": current,
            "fact_contract": FactContract(),
            "scene_provenance": {},
            "previous_propositions": [],
        }

        report = await auditor.audit(context)

        violation_types = [v.type for v in report.violations]
        assert "spatial_conflict" in violation_types


# ---------------------------------------------------------------------------
# 测试：temporal_conflict
# ---------------------------------------------------------------------------

class TestTemporalConflict:
    """测试已完成事件被重演的冲突检测。"""

    @pytest.mark.asyncio
    async def test_completed_event_replayed(self, auditor):
        """已完成事件被重演应触发时间冲突。"""
        current = [
            _make_proposition(
                subject_name="角色A",
                predicate_name="获得",
                object_name="物品X",
                source_text="角色A获得了物品X",
            )
        ]

        fact_contract = FactContract(
            temporal_constraints=[
                TemporalConstraint(
                    event_description="角色A 获得 物品X",
                    no_replay=True,
                    reason="该事件已完成",
                )
            ]
        )

        context = {
            "propositions": current,
            "fact_contract": fact_contract,
            "scene_provenance": {},
            "previous_propositions": [],
        }

        report = await auditor.audit(context)

        violation_types = [v.type for v in report.violations]
        assert "temporal_conflict" in violation_types


# ---------------------------------------------------------------------------
# 测试：ownership_conflict
# ---------------------------------------------------------------------------

class TestOwnershipConflict:
    """测试同一物品被两个角色持有的冲突检测。"""

    @pytest.mark.asyncio
    async def test_same_item_held_by_two_characters(self, auditor):
        """同一物品被两个角色同时持有应触发归属冲突。"""
        current = [
            _make_proposition(
                subject_name="角色A",
                predicate_name="持有",
                predicate_category="ownership",
                object_name="物品X",
                source_text="角色A持有物品X",
            ),
            _make_proposition(
                subject_name="角色B",
                predicate_name="持有",
                predicate_category="ownership",
                object_name="物品X",
                source_text="角色B持有物品X",
            ),
        ]

        context = {
            "propositions": current,
            "fact_contract": FactContract(),
            "scene_provenance": {},
            "previous_propositions": [],
        }

        report = await auditor.audit(context)

        violation_types = [v.type for v in report.violations]
        assert "ownership_conflict" in violation_types


# ---------------------------------------------------------------------------
# 测试：clue_provenance_error
# ---------------------------------------------------------------------------

class TestClueProvenanceError:
    """测试线索缺少来源的错误检测。"""

    @pytest.mark.asyncio
    async def test_clue_without_source(self, auditor):
        """线索缺少来源角色应触发线索来源错误。"""
        current = [
            _make_proposition(
                subject_name="物品X",
                subject_type="item",
                predicate_name="被发现",
                predicate_category="clue",
                object_name="",
                source_text="物品X被发现",
            )
        ]

        fact_contract = FactContract(
            clue_constraints=[
                ClueConstraint(
                    clue_description="物品X被发现",
                    required_source_actor=True,
                    reason="线索必须有来源",
                )
            ]
        )

        context = {
            "propositions": current,
            "fact_contract": fact_contract,
            "scene_provenance": {},
            "previous_propositions": [],
        }

        report = await auditor.audit(context)

        violation = next(v for v in report.violations if v.type == "clue_provenance_error")
        assert violation.evidence["authority_fact"]
        assert violation.evidence["authority_status"] == "missing_provenance"
        assert "不得编造" in violation.expected_behavior


    @pytest.mark.asyncio
    async def test_established_clue_reuse_does_not_require_reintroducing_source(self, auditor):
        """A clue established in long-term facts may be mentioned again without replaying its origin."""
        current = [
            _make_proposition(
                subject_name="破损衣料",
                subject_type="item",
                predicate_name="带有三足鸟符号",
                predicate_category="clue",
                object_name="逃字",
                source_text="那块刻着三足鸟符号和逃字的破损衣料还在她布袋内侧。",
            )
        ]
        fact_contract = FactContract(
            current_facts=["三足鸟符号与大师伯关联，最初发现于假凤溪刺客的面具。"],
        )

        report = await auditor.audit({
            "propositions": current,
            "fact_contract": fact_contract,
            "scene_provenance": {},
            "previous_propositions": [],
        })

        assert "clue_provenance_error" not in [v.type for v in report.violations]
# ---------------------------------------------------------------------------
# 测试：forbidden_assertion_triggered
# ---------------------------------------------------------------------------

class TestForbiddenAssertionTriggered:
    """测试禁止断言被触发的检测。"""

    @pytest.mark.asyncio
    async def test_forbidden_assertion_triggered(self, auditor):
        """触发禁止断言约束应产生违规。"""
        current = [
            _make_proposition(
                subject_name="角色A",
                predicate_name="揭示秘密",
                object_name="",
                certainty="confirmed",
                responsibility="active_actor",
                source_text="角色A揭示了秘密",
            )
        ]

        fact_contract = FactContract(
            forbidden_assertions=[
                FactConstraint(
                    event_type="揭示秘密",
                    subject_role="角色A",
                    responsibility="active_actor",
                    reason="该秘密尚未到达揭示窗口",
                )
            ]
        )

        context = {
            "propositions": current,
            "fact_contract": fact_contract,
            "scene_provenance": {},
            "previous_propositions": [],
        }

        report = await auditor.audit(context)

        violation_types = [v.type for v in report.violations]
        assert "forbidden_assertion_triggered" in violation_types


# ---------------------------------------------------------------------------
# 测试：required_ambiguity_broken
# ---------------------------------------------------------------------------

class TestRequiredAmbiguityBroken:
    """测试应保持不确定的信息被确认为事实的检测。"""

    @pytest.mark.asyncio
    async def test_required_ambiguity_broken(self, auditor):
        """应保持模糊的信息被确认为 confirmed 应触发违规。"""
        current = [
            _make_proposition(
                subject_name="角色A",
                predicate_name="是凶手",
                object_name="",
                certainty="confirmed",
                source_text="角色A确实是凶手",
            )
        ]

        fact_contract = FactContract(
            required_ambiguities=[
                FactConstraint(
                    event_type="是凶手",
                    subject_role="角色A",
                    forbidden_certainty=["confirmed", "reported"],
                    allowed_certainty=["suspected", "inferred", "unknown"],
                    reason="凶手身份应保持悬念",
                )
            ]
        )

        context = {
            "propositions": current,
            "fact_contract": fact_contract,
            "scene_provenance": {},
            "previous_propositions": [],
        }

        report = await auditor.audit(context)

        violation_types = [v.type for v in report.violations]
        assert "required_ambiguity_broken" in violation_types


# ---------------------------------------------------------------------------
# 测试：审计通过
# ---------------------------------------------------------------------------

class TestAuditPassed:
    """测试无违规时审计通过。"""

    @pytest.mark.asyncio
    async def test_passed_when_no_violations(self, auditor, base_audit_context):
        """无违规时 AuditReport.passed 应为 True。"""
        base_audit_context["propositions"] = [
            _make_proposition(
                predicate_name="行走",
                predicate_category="action",
                object_name="",
                source_text="角色A在行走",
            )
        ]

        report = await auditor.audit(base_audit_context)

        assert report.passed is True
        assert report.commit_blocked is False
        assert len(report.violations) == 0


# ---------------------------------------------------------------------------
# 测试：commit_blocked
# ---------------------------------------------------------------------------

class TestCommitBlocked:
    """测试存在阻断违规时 commit_blocked 为 True。"""

    @pytest.mark.asyncio
    async def test_commit_blocked_when_blocking_violations(self, auditor):
        """存在阻断违规时 commit_blocked 应为 True。"""
        current = [
            _make_proposition(
                subject_name="角色A",
                predicate_name="位于",
                predicate_category="location",
                object_name="",
                time_scope="同一天",
                location_scope="地点Y",
                source_text="角色A在地点Y",
            ),
            _make_proposition(
                subject_name="角色A",
                predicate_name="位于",
                predicate_category="location",
                object_name="",
                time_scope="同一天",
                location_scope="地点Z",
                source_text="角色A在地点Z",
            ),
        ]

        context = {
            "propositions": current,
            "fact_contract": FactContract(),
            "scene_provenance": {},
            "previous_propositions": [],
        }

        report = await auditor.audit(context)

        assert report.commit_blocked is True
        assert report.passed is False
        assert any(v.blocks_commit for v in report.violations)
