"""统一审查发现归一化器。

所有来源都转成 ReviewFinding 结构：
- 事实一致性检查
- 命题审计
- Deslop Gate
- 商业节奏检查
- 叙事体验检查
- 文学质量检查
- 风格冲突检查
- 结尾状态检查
- 篇幅预算检查
- 信息密度检查

然后前端、FBI、自动修复、人工弹窗都只认 ReviewFinding。
"""
from __future__ import annotations
import logging
import uuid

from app.models.violation import (
    ReviewFinding,
    SEVERITY_TO_S_LEVEL,
    SOURCE_TO_CATEGORY,
)
from app.services.review_issue_semantics import normalize_violation_semantics

logger = logging.getLogger(__name__)


class ReviewFindingNormalizer:
    """统一审查发现归一化器"""

    def normalize_violation(self, violation: dict) -> ReviewFinding:
        """将现有 Violation 转换为统一 ReviewFinding"""
        violation = normalize_violation_semantics(violation)
        severity = violation.get("severity", "low")
        source = violation.get("source", "unknown")

        finding = ReviewFinding(
            id=violation.get("violation_id", str(uuid.uuid4())[:8]),
            severity=SEVERITY_TO_S_LEVEL.get(severity, "S4"),
            category=SOURCE_TO_CATEGORY.get(source, "advisory"),
            source_checker=source,
            source_layer=source,
            location=str(violation.get("target_span", "")),
            evidence=str(violation.get("evidence", {})),
            issue=violation.get("detail", ""),
            fix_direction=violation.get("expected_behavior", ""),
            scope=self._infer_scope(violation),
            repairable_by_text=self._is_repairable_by_text(violation),
            repairable_by_contract=self._is_repairable_by_contract(violation),
            suggested_strategy=violation.get("suggested_strategy", "manual_review"),
            confidence=0.8 if severity in ("critical", "high") else 0.6,
        )
        finding["type"] = violation.get("type", "unknown")
        finding["target_span"] = violation.get("target_span", "")
        finding["expected_behavior"] = violation.get("expected_behavior", "")
        finding["classification_reason"] = violation.get("classification_reason", "")
        for key in ("current_length", "hard_max_chars", "hard_min_chars"):
            if violation.get(key) is not None:
                finding[key] = violation[key]
        return finding

    def normalize_deslop_report(self, gate_report: dict) -> ReviewFinding:
        """将 Deslop Gate 报告转换为 ReviewFinding"""
        gate = gate_report.get("gate", "A")
        severity = gate_report.get("severity", "medium")

        # Gate 到 category 的映射
        gate_category_map = {
            "A": "prose",      # 禁用词/高频AI词
            "B": "prose",      # 句式套路
            "C": "prose",      # 心理告知/解释腔
            "D": "pacing",     # 段落节奏均匀
            "E": "character",  # 对话标签与人物声音同质
            "F": "pacing",     # 章末总结/升华腔
            "G": "prose",      # 夸大意义/宣传性语言
            "H": "prose",      # 填充短语/模糊归因
            "I": "prose",      # 明喻过度
            "J": "prose",      # 通感公式
            "K": "prose",      # 虚假代理
            "L": "style",      # 模板承接
        }

        # Gate 到 FBI repair_class 的映射
        gate_repair_map = {
            "A": "prose_de_ai",
            "B": "prose_de_ai",
            "C": "prose_de_ai",
            "D": "pacing_enhancement",
            "E": "voice_alignment",
            "F": "pacing_enhancement",
            "G": "prose_de_ai",
            "H": "prose_de_ai",
            "I": "prose_de_ai",
            "J": "prose_de_ai",
            "K": "prose_de_ai",
            "L": "prose_de_ai",
        }

        return ReviewFinding(
            id=str(uuid.uuid4())[:8],
            severity=SEVERITY_TO_S_LEVEL.get(severity, "S3"),
            category=gate_category_map.get(gate, "prose"),
            source_checker=f"deslop_gate_{gate}",
            source_layer="deslop_gate",
            location="",
            evidence="; ".join(gate_report.get("evidence_spans", [])[:3]),
            issue=f"Deslop Gate {gate}: {gate_report.get('evidence_spans', [''])[0][:50] if gate_report.get('evidence_spans') else ''}",
            fix_direction=f"修复Gate {gate}问题",
            scope="prose_text",
            repairable_by_text=True,
            repairable_by_contract=False,
            suggested_strategy=gate_repair_map.get(gate, "prose_de_ai"),
            confidence=0.7,
        )

    def normalize_quality_report(self, report: dict, source: str) -> list[ReviewFinding]:
        """将质量检查报告转换为 ReviewFinding 列表"""
        findings = []
        violations = report.get("violations", [])
        for v in violations:
            findings.append(self.normalize_violation(v))
        return findings

    def normalize_all(
        self,
        violations: list[dict],
        deslop_reports: list[dict] | None = None,
    ) -> list[ReviewFinding]:
        """归一化所有来源的审查发现"""
        findings = []

        # 归一化 violations
        for v in violations:
            findings.append(self.normalize_violation(v))

        # 归一化 deslop reports
        if deslop_reports:
            for report in deslop_reports:
                if report.get("evidence_spans"):
                    findings.append(self.normalize_deslop_report(report))

        return findings

    def filter_actionable(self, findings: list[ReviewFinding]) -> list[ReviewFinding]:
        """过滤出可操作的发现（排除 advisory 和 S4 级别）"""
        return [
            f for f in findings
            if f["category"] != "advisory" and f["severity"] not in ("S4",)
        ]

    def group_by_scope(self, findings: list[ReviewFinding]) -> dict[str, list[ReviewFinding]]:
        """按 scope 分组"""
        groups: dict[str, list[ReviewFinding]] = {}
        for f in findings:
            scope = f["scope"]
            if scope not in groups:
                groups[scope] = []
            groups[scope].append(f)
        return groups

    def group_by_repair_strategy(self, findings: list[ReviewFinding]) -> dict[str, list[ReviewFinding]]:
        """按修复策略分组"""
        groups: dict[str, list[ReviewFinding]] = {}
        for f in findings:
            strategy = f["suggested_strategy"]
            if strategy not in groups:
                groups[strategy] = []
            groups[strategy].append(f)
        return groups

    def _infer_scope(self, violation: dict) -> str:
        """推断 violation 的 scope"""
        v_type = violation.get("type", "")
        source = violation.get("source", "")

        # 合同类问题
        contract_types = {
            "scene_contract_compile_blocked",
        }
        if v_type in contract_types:
            return "scene_contract"

        # 大纲类问题
        if source == "outline":
            return "outline_plan"

        # 风格类问题
        if source in ("style_quality", "style_experience_conflict"):
            return "style_profile"

        # 默认为正文
        return "prose_text"

    def _is_repairable_by_text(self, violation: dict) -> bool:
        """判断是否可通过文本修复"""
        v_type = violation.get("type", "")
        scope = self._infer_scope(violation)
        if scope in ("scene_contract", "outline_plan"):
            return False
        # 结构性问题不能通过文本修复
        structural_types = {"scene_restructure", "rewrite_scene"}
        if v_type in structural_types:
            return False
        return True

    def _is_repairable_by_contract(self, violation: dict) -> bool:
        """判断是否可通过合同修复"""
        scope = self._infer_scope(violation)
        return scope in ("scene_contract", "outline_plan")


# 全局单例
_normalizer: ReviewFindingNormalizer | None = None

def get_review_finding_normalizer() -> ReviewFindingNormalizer:
    global _normalizer
    if _normalizer is None:
        _normalizer = ReviewFindingNormalizer()
    return _normalizer
