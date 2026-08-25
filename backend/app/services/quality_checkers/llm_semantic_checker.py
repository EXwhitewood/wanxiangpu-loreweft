"""方案 30：B 类语义指标的 LLM 判定器。

遵循 CommercialPacingChecker 的接口契约（check 返回 dict），
但对 B 类语义指标改用 LLM 判定，而非硬编码关键词 + 纯频次统计。

两级过滤机制（降低 LLM 成本）：
1. 预筛：从 A 类 checker 的计数判断是否需要 LLM 精判
   - 抽象词计数 > 阈值 → 触发 LLM 判定抽象解释
   - 冲突词密度 < 阈值 → 触发 LLM 判定冲突密度
   - 好奇词计数 < 阈值 → 触发 LLM 判定悬念驱动
2. 精判：仅当预筛发现潜在问题时才触发 LLM
   - 约 60-70% 的场景不会触发 LLM（A 类预筛通过）
   - 约 30-40% 的场景触发 LLM，增加 1 轮 LLM 调用

判定与修复共享 evidence：LLM 判定产出的 evidence（文本片段）会被写入 advisory，
FBI 修复时直接使用这些 evidence 作为锚点，不再用硬编码关键词匹配。
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# B 类 metric 清单（需要 LLM 判定的语义型指标）
# 循环#10：standalone_abstract_claims 已有可靠的确定性检测器
# （_standalone_abstract_claim_count），不应走 LLM judge 覆盖路径。
# LLM judge 硬编码 before=0 + 默认 passed=True 导致本地自检假通过，
# 与 L10 确定性 recheck 不一致。从 B_CLASS_METRICS 移除后，
# 本地自检与最终 recheck 都用确定性检测器，保持一致。
B_CLASS_METRICS: frozenset[str] = frozenset({
    "low_conflict_density",
    "weak_curiosity_engine",
    "weak_opening_hook",
    "weak_chapter_end_hook",
    "abstraction_over_budget",
    "missing_specific_detail_anchor",
    "voice_phrase_drift",
})

# 预筛阈值（A 类计数超过此阈值才触发 LLM）
# 这些阈值是"潜在问题"的触发线，比 A 类的"判定失败"阈值更宽松
PREFILTER_THRESHOLDS: dict[str, float] = {
    "abstract_word_count": 2,        # 抽象词出现次数 > 2 才触发 LLM
    "conflict_word_density": 0.5,    # 冲突词密度 < 0.5/千字才触发 LLM
    "curiosity_word_count": 1,       # 好奇词出现次数 < 1 才触发 LLM
    "thought_verb_count": 3,         # 思想动词出现次数 > 3 才触发 LLM
}


class LlmSemanticChecker:
    """方案 30：B 类语义指标的 LLM 判定器。

    遵循 CommercialPacingChecker 的接口契约：
    check(text, contract, scene_contract=...) -> dict
    返回 {metrics, scores, advisories, summary}

    与 CommercialPacingChecker 的区别：
    - check 是 async 方法（需要调用 LLM）
    - 判定基于 LLM 语义理解，而非硬编码关键词
    - advisory 带 evidence 字段（LLM 引用的原文片段），供 FBI 修复直接使用
    """

    source_checker = "llm_semantic"

    def __init__(self) -> None:
        # 缓存 LLM agent 实例（避免每次 check 都创建）
        self._judge_agent = None

    async def check(
        self,
        text: str,
        contract: dict | None = None,
        scene_contract: dict | None = None,
        prefilter_result: dict | None = None,
    ) -> dict[str, Any]:
        """对场景文本做 B 类语义指标判定。

        Args:
            text: 场景正文
            contract: 质量合同（含 literary_quality_contract 等）
            scene_contract: 场景合同（含 commercial_pacing_contract 等）
            prefilter_result: A 类 checker 的预筛结果（含 abstract_word_count 等）
                如果未提供，会从 contract 中尝试提取

        Returns:
            {
                "metrics": {metric_name: {passed, score, evidence, issues}},
                "scores": {metric_name: score},  # 0-10
                "advisories": [{type, severity, detail, evidence, ...}],
                "summary": str,
                "degraded": bool,  # LLM 失败时为 True
                "prefilter_passed": bool,  # 预筛是否通过（未触发 LLM）
            }
        """
        if not text:
            return self._build_empty_result("empty_text")

        # 1. 预筛：判断是否需要 LLM 精判
        need_llm, prefilter_metrics, metrics_to_judge = self._prefilter(
            text, contract, prefilter_result
        )

        if not need_llm:
            # 预筛通过，不触发 LLM
            return self._build_pass_result(prefilter_metrics)

        # 2. LLM 精判
        try:
            llm_result = await self._llm_evaluate(
                text, metrics_to_judge, scene_contract or {}
            )
        except Exception as exc:
            logger.warning("LlmSemanticChecker LLM 调用失败: %s", exc, exc_info=True)
            return self._build_degraded_result(prefilter_metrics, str(exc))

        if llm_result.get("degraded"):
            return self._build_degraded_result(
                prefilter_metrics, llm_result.get("error", "llm_failed")
            )

        # 3. 合并结果
        return self._merge_results(prefilter_metrics, llm_result)

    def _prefilter(
        self,
        text: str,
        contract: dict | None,
        prefilter_result: dict | None,
    ) -> tuple[bool, dict[str, Any], list[str]]:
        """预筛：判断是否需要 LLM 精判。

        Returns:
            (need_llm, prefilter_metrics, metrics_to_judge)
            - need_llm: 是否需要触发 LLM
            - prefilter_metrics: 预筛指标数据
            - metrics_to_judge: 需要 LLM 判定的 metric 列表
        """
        prefilter_result = prefilter_result or {}
        contract = contract or {}

        # 从预筛结果中提取 A 类计数
        # prefilter_result 可能来自 QualityGate 的 _build_prefilter_input
        # 包含 commercial_pacing 和 literary_quality 的 metrics
        metrics: dict[str, Any] = {}

        # 从 commercial_pacing 报告提取
        cp_metrics = prefilter_result.get("commercial_pacing_metrics", {})
        if not cp_metrics and isinstance(contract, dict):
            cp_contract = contract.get("commercial_pacing_contract", {}) or {}
            # 如果没有预筛结果，尝试从 contract 重建（降级路径）
            metrics["conflict_word_density"] = 0.0
            metrics["curiosity_word_count"] = 0
        else:
            metrics["conflict_word_density"] = float(
                cp_metrics.get("conflict_density", 0.0)
            )
            metrics["curiosity_word_count"] = int(
                cp_metrics.get("curiosity_hits", 0)
            )

        # 从 literary_quality 报告提取
        lq_metrics = prefilter_result.get("literary_quality_metrics", {})
        metrics["abstract_word_count"] = int(lq_metrics.get("abstract_hits", 0))

        # 从 scene_evidence 报告提取（如果有）
        se_metrics = prefilter_result.get("scene_evidence_metrics", {})
        metrics["thought_verb_count"] = int(se_metrics.get("thought_verb_count", 0))

        # 判断哪些 metric 需要 LLM 判定
        metrics_to_judge: list[str] = []

        # 抽象解释：抽象词计数 > 阈值
        if metrics["abstract_word_count"] > PREFILTER_THRESHOLDS["abstract_word_count"]:
            metrics_to_judge.append("abstraction_over_budget")
            metrics_to_judge.append("standalone_abstract_claims")

        # 思想动词过多：也可能暗示抽象解释问题
        if metrics["thought_verb_count"] > PREFILTER_THRESHOLDS["thought_verb_count"]:
            if "abstraction_over_budget" not in metrics_to_judge:
                metrics_to_judge.append("abstraction_over_budget")

        # 冲突密度：冲突词密度 < 阈值
        if metrics["conflict_word_density"] < PREFILTER_THRESHOLDS["conflict_word_density"]:
            metrics_to_judge.append("low_conflict_density")

        # 悬念驱动：好奇词计数 < 阈值
        if metrics["curiosity_word_count"] < PREFILTER_THRESHOLDS["curiosity_word_count"]:
            metrics_to_judge.append("weak_curiosity_engine")
            metrics_to_judge.append("weak_opening_hook")
            metrics_to_judge.append("weak_chapter_end_hook")

        # 去重，保持顺序
        seen = set()
        deduped_metrics = []
        for m in metrics_to_judge:
            if m not in seen:
                seen.add(m)
                deduped_metrics.append(m)

        need_llm = len(deduped_metrics) > 0
        return need_llm, metrics, deduped_metrics

    async def _llm_evaluate(
        self,
        text: str,
        metrics_to_judge: list[str],
        scene_contract: dict,
    ) -> dict[str, Any]:
        """调用 LlmSemanticJudgeAgent 做 LLM 精判。"""
        if self._judge_agent is None:
            from app.agents.llm_semantic_judge_agent import LlmSemanticJudgeAgent
            self._judge_agent = LlmSemanticJudgeAgent()

        return await self._judge_agent.judge_metrics(
            text=text,
            metrics_to_judge=metrics_to_judge,
            scene_contract=scene_contract,
        )

    def _merge_results(
        self,
        prefilter_metrics: dict[str, Any],
        llm_result: dict[str, Any],
    ) -> dict[str, Any]:
        """合并预筛结果和 LLM 判定结果。"""
        llm_metrics = llm_result.get("metrics", {})

        # 构建 scores 和 advisories
        scores: dict[str, float] = {}
        advisories: list[dict[str, Any]] = []

        for metric_name, metric_data in llm_metrics.items():
            passed = metric_data.get("passed", True)
            score = float(metric_data.get("score", 0.0))
            evidence = metric_data.get("evidence", [])
            issues = metric_data.get("issues", [])

            scores[metric_name] = score

            if not passed:
                # 不通过 → 生成 advisory
                # severity 规则：score < 4 为 high，4-6 为 medium，> 6 为 low
                if score < 4.0:
                    severity = "high"
                elif score < 6.0:
                    severity = "medium"
                else:
                    severity = "low"

                # detail 优先用 issues 的描述
                issue_details = []
                for issue in issues:
                    detail = issue.get("detail", "")
                    if detail:
                        issue_details.append(detail)
                detail_text = "; ".join(issue_details) if issue_details else f"{metric_name} 判定未通过（score={score}）"

                advisory = {
                    "type": metric_name,
                    "severity": severity,
                    "detail": detail_text,
                    "evidence": evidence,  # LLM 引用的原文片段，供 FBI 修复直接使用
                    "issues": issues,
                    "source_checker": self.source_checker,
                    "confidence": 0.9,  # LLM 判定置信度
                }
                advisories.append(advisory)

        # 构建 summary
        total = len(llm_metrics)
        failed = len(advisories)
        if failed == 0:
            summary = f"llm_semantic: all {total} metrics passed"
        else:
            summary = f"llm_semantic: {failed}/{total} metrics failed"

        return {
            "metrics": llm_metrics,
            "scores": scores,
            "advisories": advisories,
            "summary": summary,
            "degraded": False,
            "prefilter_passed": False,
            "prefilter_metrics": prefilter_metrics,
        }

    def _build_pass_result(self, prefilter_metrics: dict[str, Any]) -> dict[str, Any]:
        """预筛通过的结果（不触发 LLM）。"""
        return {
            "metrics": {},
            "scores": {},
            "advisories": [],
            "summary": "prefilter_passed",
            "degraded": False,
            "prefilter_passed": True,
            "prefilter_metrics": prefilter_metrics,
        }

    def _build_degraded_result(
        self, prefilter_metrics: dict[str, Any], error: str
    ) -> dict[str, Any]:
        """LLM 失败的降级结果（不阻断）。"""
        return {
            "metrics": {},
            "scores": {},
            "advisories": [],
            "summary": f"degraded: {error}",
            "degraded": True,
            "prefilter_passed": False,
            "prefilter_metrics": prefilter_metrics,
            "error": error,
        }

    def _build_empty_result(self, reason: str) -> dict[str, Any]:
        """空文本或无效输入的结果。"""
        return {
            "metrics": {},
            "scores": {},
            "advisories": [],
            "summary": f"empty: {reason}",
            "degraded": False,
            "prefilter_passed": True,
            "prefilter_metrics": {},
        }
