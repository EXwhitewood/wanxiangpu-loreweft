import hashlib
from typing import Literal, TypedDict


class QualityAdvisory(TypedDict, total=False):
    advisory_id: str
    type: str
    severity: Literal["high", "medium", "low"]
    detail: str
    target_span: str | None
    expected_behavior: str
    detector: str
    confidence: float
    candidate_patch: str | None
    # 通用修复 S-1：密度类 / 多位置类问题（如 ai_simile_overuse）需要 LLM 系统性
    # 改动多处，单一 target_span 无法覆盖。evidence_samples 携带所有命中位置，
    # 供 lane 指令读取并指示 LLM 一次性处理。
    evidence_samples: list[str]
    # 通用修复 S-5：advisory 携带对应的可测量 validator metric 名列表。
    # 根因：advisory type（如 ai_simile_overuse）经 enforce 后变成
    #   enforced_ai_simile_overuse，但 validator_protocol 注册的 metric 是
    #   simile_hits / simile_word_count。chapter_case_intake 构建 target_metric
    #   时 fallback 到 type，弱后检找不到 metric，返回 unsupported_metric。
    # 修复：advisory 携带 validator_metrics，经 violation.evidence 透传到
    #   repair_brief.target_metrics，弱后检能正确路由到重算路径。
    # 通用性：任何 advisory 都可携带对应的可测量指标名，转化链路统一处理。
    validator_metrics: list[str]


def make_advisory(
    atype: str,
    severity: str,
    detail: str,
    *,
    target_span: str | None = None,
    expected_behavior: str = "",
    detector: str = "deterministic",
    confidence: float = 0.5,
    candidate_patch: str | None = None,
    evidence_samples: list[str] | None = None,
    validator_metrics: list[str] | None = None,
) -> QualityAdvisory:
    """Create a non-blocking quality advisory.

    Advisory objects are deliberately separate from Violation objects. They must
    not be consumed by SceneRecoveryController or commit blocking logic.
    """
    raw = f"{atype}:{target_span or detail[:80]}:{detector}"
    advisory = QualityAdvisory(
        advisory_id=hashlib.md5(raw.encode("utf-8")).hexdigest()[:12],
        type=atype,
        severity=severity if severity in {"high", "medium", "low"} else "low",
        detail=detail,
        target_span=target_span,
        expected_behavior=expected_behavior,
        detector=detector,
        confidence=max(0.0, min(1.0, float(confidence))),
        candidate_patch=candidate_patch,
    )
    if evidence_samples:
        advisory["evidence_samples"] = list(evidence_samples)
    if validator_metrics:
        advisory["validator_metrics"] = list(validator_metrics)
    return advisory
