"""复检监督器测试。

所有测试使用抽象实体占位符（角色A, 角色B, 物品X, 地点Y），
遵循反污染原则，不引入任何真实作品内容。

测试复检逻辑：定向复检、硬事实全局复检、全质量门复检、
新问题检测、复检报告结构。
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# 复检监督器实现（供测试使用）
# ---------------------------------------------------------------------------

class RecheckSupervisor:
    """复检监督器——验证修复后的问题是否真正解决。"""

    async def targeted_recheck(
        self,
        *,
        candidate_text: str,
        resolved_issue_ids: list[str],
        context: dict,
    ) -> dict:
        """定向复检：只检查之前标记为已解决的问题。"""
        from app.services.quality_gate import QualityGate

        quality_gate = QualityGate()
        qg_context = {
            "generated_text": candidate_text,
            "project_id": context.get("project_id", ""),
            "scene_contract": context.get("scene_contract", {}),
            "character_cards": context.get("character_cards", []),
            "chapter_state": context.get("chapter_state", {}),
            "generation_feature_policy": {"scene_credibility_mode": "assist"},
        }

        result = await quality_gate.evaluate(qg_context, level="targeted")

        new_violation_ids: set[str] = set()
        for v in result.get("violations", []):
            vid = v.get("violation_id", "")
            if vid:
                new_violation_ids.add(vid)

        still_resolved = [iid for iid in resolved_issue_ids if iid not in new_violation_ids]
        now_unresolved = [iid for iid in resolved_issue_ids if iid in new_violation_ids]

        return {
            "passed": len(now_unresolved) == 0,
            "resolved_issue_ids": still_resolved,
            "unresolved_issue_ids": now_unresolved,
            "violations": result.get("violations", []),
        }

    async def hard_global_recheck(
        self,
        *,
        candidate_text: str,
        context: dict,
    ) -> dict:
        """硬事实全局复检：只检查事实层级问题。"""
        from app.services.quality_gate import QualityGate

        quality_gate = QualityGate()
        qg_context = {
            "generated_text": candidate_text,
            "project_id": context.get("project_id", ""),
            "scene_contract": context.get("scene_contract", {}),
            "character_cards": context.get("character_cards", []),
            "chapter_state": context.get("chapter_state", {}),
            "generation_feature_policy": {"hard_correctness_mode": "enforce"},
        }

        result = await quality_gate.evaluate(qg_context, level="hard")

        fact_violations = [
            v for v in result.get("violations", [])
            if v.get("source_layer", "") in (
                "hard_correctness", "fact_boundary", "knowledge_boundary",
                "narrative_proposition",
            )
        ]

        return {
            "passed": len(fact_violations) == 0,
            "violations": fact_violations,
            "total_violations": len(result.get("violations", [])),
            "fact_violations": len(fact_violations),
        }

    async def full_quality_recheck(
        self,
        *,
        candidate_text: str,
        context: dict,
    ) -> dict:
        """全质量门复检：运行完整 QualityGate。"""
        from app.services.quality_gate import QualityGate

        quality_gate = QualityGate()
        qg_context = {
            "generated_text": candidate_text,
            "project_id": context.get("project_id", ""),
            "scene_contract": context.get("scene_contract", {}),
            "character_cards": context.get("character_cards", []),
            "chapter_state": context.get("chapter_state", {}),
            "generation_feature_policy": context.get("generation_feature_policy", {}),
        }

        result = await quality_gate.evaluate(qg_context, level="full")

        return {
            "passed": result.get("passed", False),
            "violations": result.get("violations", []),
            "total_violations": len(result.get("violations", [])),
            "by_severity": self._group_by_severity(result.get("violations", [])),
        }

    @staticmethod
    def _group_by_severity(violations: list[dict]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for v in violations:
            sev = v.get("severity", "unknown")
            counts[sev] = counts.get(sev, 0) + 1
        return counts


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_TEXT = "角色A在地点Y发现了物品X。角色B对此毫不知情。"

SAMPLE_CONTEXT = {
    "project_id": "proj_test",
    "scene_contract": {},
    "character_cards": [],
    "chapter_state": {},
}


@pytest.fixture
def supervisor() -> RecheckSupervisor:
    return RecheckSupervisor()


# ---------------------------------------------------------------------------
# 1. targeted_recheck 通过
# ---------------------------------------------------------------------------

class TestTargetedRecheck:
    @pytest.mark.asyncio
    async def test_passes_when_issues_resolved(self, supervisor: RecheckSupervisor):
        with patch("app.services.quality_gate.QualityGate") as MockQG:
            mock_qg = MagicMock()
            mock_qg.evaluate = AsyncMock(return_value={
                "passed": True,
                "violations": [],
            })
            MockQG.return_value = mock_qg

            result = await supervisor.targeted_recheck(
                candidate_text=SAMPLE_TEXT,
                resolved_issue_ids=["v_001", "v_002"],
                context=SAMPLE_CONTEXT,
            )

        assert result["passed"] is True
        assert "v_001" in result["resolved_issue_ids"]
        assert "v_002" in result["resolved_issue_ids"]
        assert len(result["unresolved_issue_ids"]) == 0

    @pytest.mark.asyncio
    async def test_fails_when_issues_persist(self, supervisor: RecheckSupervisor):
        with patch("app.services.quality_gate.QualityGate") as MockQG:
            mock_qg = MagicMock()
            mock_qg.evaluate = AsyncMock(return_value={
                "passed": False,
                "violations": [
                    {"violation_id": "v_001", "type": "fact_conflict", "severity": "high"},
                ],
            })
            MockQG.return_value = mock_qg

            result = await supervisor.targeted_recheck(
                candidate_text=SAMPLE_TEXT,
                resolved_issue_ids=["v_001", "v_002"],
                context=SAMPLE_CONTEXT,
            )

        assert result["passed"] is False
        assert "v_001" in result["unresolved_issue_ids"]
        assert "v_002" in result["resolved_issue_ids"]


# ---------------------------------------------------------------------------
# 2. hard_global_recheck
# ---------------------------------------------------------------------------

class TestHardGlobalRecheck:
    @pytest.mark.asyncio
    async def test_checks_facts(self, supervisor: RecheckSupervisor):
        with patch("app.services.quality_gate.QualityGate") as MockQG:
            mock_qg = MagicMock()
            mock_qg.evaluate = AsyncMock(return_value={
                "passed": False,
                "violations": [
                    {"violation_id": "v_fact_001", "source_layer": "hard_correctness", "severity": "critical"},
                    {"violation_id": "v_style_001", "source_layer": "style", "severity": "low"},
                ],
            })
            MockQG.return_value = mock_qg

            result = await supervisor.hard_global_recheck(
                candidate_text=SAMPLE_TEXT,
                context=SAMPLE_CONTEXT,
            )

        # 只应包含事实层级违规
        assert result["fact_violations"] == 1
        assert len(result["violations"]) == 1
        assert result["violations"][0]["source_layer"] == "hard_correctness"


# ---------------------------------------------------------------------------
# 3. full_quality_recheck
# ---------------------------------------------------------------------------

class TestFullQualityRecheck:
    @pytest.mark.asyncio
    async def test_runs_full_quality_gate(self, supervisor: RecheckSupervisor):
        with patch("app.services.quality_gate.QualityGate") as MockQG:
            mock_qg = MagicMock()
            mock_qg.evaluate = AsyncMock(return_value={
                "passed": True,
                "violations": [],
            })
            MockQG.return_value = mock_qg

            result = await supervisor.full_quality_recheck(
                candidate_text=SAMPLE_TEXT,
                context=SAMPLE_CONTEXT,
            )

        assert result["passed"] is True
        assert result["total_violations"] == 0


# ---------------------------------------------------------------------------
# 4. 新更高优先级问题检测
# ---------------------------------------------------------------------------

class TestNewHigherPriorityIssues:
    @pytest.mark.asyncio
    async def test_recheck_detects_new_higher_priority_issues(self, supervisor: RecheckSupervisor):
        """复检应能发现新出现的更高优先级问题。"""
        with patch("app.services.quality_gate.QualityGate") as MockQG:
            mock_qg = MagicMock()
            mock_qg.evaluate = AsyncMock(return_value={
                "passed": False,
                "violations": [
                    {"violation_id": "v_new_001", "source_layer": "hard_correctness", "severity": "critical"},
                    {"violation_id": "v_style_001", "source_layer": "style", "severity": "low"},
                ],
            })
            MockQG.return_value = mock_qg

            result = await supervisor.hard_global_recheck(
                candidate_text=SAMPLE_TEXT,
                context=SAMPLE_CONTEXT,
            )

        # 新的 critical 问题应被检测到
        assert result["fact_violations"] >= 1
        critical_violations = [v for v in result["violations"] if v.get("severity") == "critical"]
        assert len(critical_violations) > 0


# ---------------------------------------------------------------------------
# 5. 复检报告结构
# ---------------------------------------------------------------------------

class TestRecheckReportStructure:
    @pytest.mark.asyncio
    async def test_recheck_report_structure(self, supervisor: RecheckSupervisor):
        """复检报告应包含标准字段。"""
        with patch("app.services.quality_gate.QualityGate") as MockQG:
            mock_qg = MagicMock()
            mock_qg.evaluate = AsyncMock(return_value={
                "passed": False,
                "violations": [
                    {"violation_id": "v_001", "severity": "high", "source_layer": "consistency"},
                    {"violation_id": "v_002", "severity": "medium", "source_layer": "style"},
                ],
            })
            MockQG.return_value = mock_qg

            result = await supervisor.full_quality_recheck(
                candidate_text=SAMPLE_TEXT,
                context=SAMPLE_CONTEXT,
            )

        # 报告应包含标准字段
        assert "passed" in result
        assert "violations" in result
        assert "total_violations" in result
        assert "by_severity" in result
        assert isinstance(result["by_severity"], dict)
        assert result["total_violations"] == 2
