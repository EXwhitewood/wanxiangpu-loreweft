"""统一 validator 集合（方案 7 Part A）。

合并三套 validator（QualityGate / AgentSkillCommitGate / ChapterCommitHealthChecker）
的输出，提供统一的 validate 入口和 stale finding 检测，使初审、recheck、final_acceptance
使用相同的 validator 集合和 metric 命名。

设计原则：
- 不替换三套 validator 的特化实现，只做"合并 + 统一 metric 名 + 统一 text_hash"
- validate() 返回的 violations 列表统一包含 metric / text_hash / source_validator 字段
- is_stale_finding() 用于 FBI 去重时检测"验证结果是否针对旧文本"

对应方案 7 Part A 的"方案 A（推荐）"：保留两个 validator 的特化实现，
但统一调用时机（初审和 recheck 都跑两者），合并 violations。
"""
from __future__ import annotations

import hashlib
from typing import Any

from app.services.metric_registry import determine_blocks_commit


class ValidatorCollection:
    """统一 validator 集合，合并三套 validator 的输出。

    三套 validator 各自的输出结构不同：
    - QualityGate: ``violations`` 列表，元素是 Violation TypedDict（含 type/severity/text_hash）
    - AgentSkillValidator: ``failures`` 列表，元素是 finding dict（含 metric/validator/text_hash）
    - ChapterCommitHealthChecker: ``issues`` 列表，元素是 issue dict（含 code/metric/text_hash）

    本类把它们归一化到统一结构，让下游 FBI 去重和 stale 检测能跨 validator 工作。
    """

    def validate(
        self,
        *,
        text: str = "",
        quality_gate_result: dict | None = None,
        skill_validation: dict | None = None,
        health_check_result: dict | None = None,
        text_hash: str = "",
    ) -> dict[str, Any]:
        """合并三套 validator 的 violations，统一 metric 命名和 text_hash。

        参数：
            text: 被校验的正文（仅在 text_hash 为空时用于计算兜底 hash）
            quality_gate_result: QualityGate 的输出（含 violations 列表）
            skill_validation: AgentSkillValidator 的输出（含 failures 列表）
            health_check_result: ChapterCommitHealthChecker 的输出（含 issues 列表）
            text_hash: 当前正文的 hash，用于 stale 检测；为空时按 text 计算

        返回：
            ``{"violations": [...], "passed": bool, "text_hash": str, "sources": {...}}``
            其中 violations 元素统一包含 metric / type / severity / blocks_commit /
            text_hash / source_validator / source_collection 字段。
        """
        # 计算 text_hash（兜底）：优先用调用方传入的，否则按 text 计算
        if not text_hash and text:
            text_hash = hashlib.md5(text.encode("utf-8")).hexdigest()

        violations: list[dict[str, Any]] = []

        # 合并 QualityGate violations
        if quality_gate_result:
            violations.extend(
                self._normalize_quality_gate_violations(quality_gate_result, text_hash)
            )

        # 合并 AgentSkillValidator failures
        if skill_validation:
            violations.extend(
                self._normalize_skill_findings(skill_validation, text_hash)
            )

        # 合并 ChapterCommitHealthChecker issues
        if health_check_result:
            violations.extend(
                self._normalize_health_issues(health_check_result, text_hash)
            )

        # 去重：同一 metric + target_span + source_validator 只保留一个
        violations = self._dedupe_violations(violations)

        passed = not any(v.get("blocks_commit") for v in violations)

        return {
            "violations": violations,
            "passed": passed,
            "text_hash": text_hash,
            "sources": {
                "quality_gate": bool(quality_gate_result),
                "skill_validator": bool(skill_validation),
                "health_checker": bool(health_check_result),
            },
        }

    def is_stale_finding(
        self,
        finding: dict,
        *,
        current_text_hash: str,
    ) -> bool:
        """检测 finding 是否针对旧文本（stale）。

        判定规则（保守策略，避免误标）：
        - ``current_text_hash`` 为空 → ``False``（无当前 hash 可比对）
        - finding 无 ``text_hash`` → ``False``（无法判定，不标记 stale）
        - finding.text_hash == current_text_hash → ``False``（一致，非 stale）
        - finding.text_hash != current_text_hash → ``True``（不一致，stale）

        该方法供 FBI 去重时使用：当 finding 标记为 stale 时，应重新校验当前文本，
        而不是直接复用旧 finding 的修复结论。
        """
        if not current_text_hash:
            return False
        finding_hash = str(finding.get("text_hash") or "")
        if not finding_hash:
            return False
        return finding_hash != current_text_hash

    # ------------------------------------------------------------------
    # 归一化各 validator 的输出
    # ------------------------------------------------------------------

    def _normalize_quality_gate_violations(
        self,
        result: dict,
        text_hash: str,
    ) -> list[dict[str, Any]]:
        """归一化 QualityGate 的 violations。

        QualityGate 的 violation 已经是 Violation TypedDict，包含 type/severity/
        text_hash/blocks_commit 等字段。这里统一把 type 映射到 metric 字段，
        保证三套 validator 输出同构；若 blocks_commit 缺失则用 determine_blocks_commit 补齐。
        """
        violations: list[dict[str, Any]] = []
        for violation in result.get("violations") or []:
            if not isinstance(violation, dict):
                continue
            normalized = dict(violation)
            # 统一 metric 字段：QualityGate 用 type，这里补 metric
            metric = str(normalized.get("metric") or normalized.get("type") or "")
            normalized["metric"] = metric
            # type 字段保留（QualityGate 路径下游依赖 type）
            normalized["type"] = metric
            normalized["source_validator"] = str(
                normalized.get("source_validator")
                or normalized.get("source")
                or "quality_gate"
            )
            # 补齐 blocks_commit：QualityGate 通常已有，缺失时用 determine_blocks_commit
            if "blocks_commit" not in normalized or normalized.get("blocks_commit") is None:
                severity = str(normalized.get("severity") or "medium")
                normalized["blocks_commit"] = determine_blocks_commit(metric, severity)
            # 补齐 text_hash（QualityGate 通常已有，兜底）
            if not normalized.get("text_hash"):
                normalized["text_hash"] = text_hash
            # 统一 source_collection 标记，便于下游区分来源
            normalized["source_collection"] = "quality_gate"
            violations.append(normalized)
        return violations

    def _normalize_skill_findings(
        self,
        result: dict,
        text_hash: str,
    ) -> list[dict[str, Any]]:
        """归一化 AgentSkillValidator 的 failures。

        AgentSkillValidator 的 finding 包含 metric/validator/severity/text_hash
        （Part D 已注入 text_hash）。这里补齐 blocks_commit（用
        determine_blocks_commit 统一判定）和 type 字段，使与 QualityGate 同构。
        """
        violations: list[dict[str, Any]] = []
        for finding in result.get("failures") or []:
            if not isinstance(finding, dict):
                continue
            normalized = dict(finding)
            metric = str(normalized.get("metric") or normalized.get("type") or "")
            normalized["metric"] = metric
            # 统一 type 字段（QualityGate 用 type）
            normalized["type"] = metric
            normalized["source_validator"] = str(
                normalized.get("validator")
                or normalized.get("source_validator")
                or "agent_skill_validator"
            )
            severity = str(normalized.get("severity") or "medium")
            # 统一 blocks_commit：item 显式提供 > determine_blocks_commit(metric, severity)
            if "blocks_commit" not in normalized or normalized.get("blocks_commit") is None:
                normalized["blocks_commit"] = determine_blocks_commit(metric, severity)
            # 补齐 text_hash（Part D 已注入，兜底）
            if not normalized.get("text_hash"):
                normalized["text_hash"] = text_hash
            normalized["source_collection"] = "skill_validator"
            violations.append(normalized)
        return violations

    def _normalize_health_issues(
        self,
        result: dict,
        text_hash: str,
    ) -> list[dict[str, Any]]:
        """归一化 ChapterCommitHealthChecker 的 issues。

        Health issue 包含 code/severity/metric/text_hash（Part D 已补齐 metric 与
        text_hash）。这里把 code 映射到 metric/type，补齐 blocks_commit，使与
        QualityGate / skill 路径同构。
        """
        violations: list[dict[str, Any]] = []
        for issue in result.get("issues") or []:
            if not isinstance(issue, dict):
                continue
            normalized = dict(issue)
            # 统一 metric 字段：health 用 code 或 metric（Part D 已让 metric 默认等于 code）
            metric = str(normalized.get("metric") or normalized.get("code") or "")
            normalized["metric"] = metric
            normalized["type"] = metric
            normalized["source_validator"] = "chapter_commit_health_checker"
            severity = str(normalized.get("severity") or "")
            # health checker 用 "hard" 表示阻断，映射到 "critical" 以统一 severity 体系
            if severity == "hard":
                severity = "critical"
                normalized["severity"] = severity
            # 统一 blocks_commit：hard → True，其他用 determine_blocks_commit
            if "blocks_commit" not in normalized or normalized.get("blocks_commit") is None:
                normalized["blocks_commit"] = determine_blocks_commit(metric, severity)
            # 补齐 text_hash
            if not normalized.get("text_hash"):
                normalized["text_hash"] = text_hash
            normalized["source_collection"] = "health_checker"
            violations.append(normalized)
        return violations

    # ------------------------------------------------------------------
    # 去重
    # ------------------------------------------------------------------

    @staticmethod
    def _dedupe_violations(violations: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """去重：同一 metric + target_span + source_validator + scene 只保留一个。

        保留优先级：
        1. blocks_commit=True 优先（阻断问题必须保留）
        2. severity 更高优先
        3. 先到先得

        注意：不同 source_validator 的同 metric issue 不去重（因为可能是不同
        validator 对同一问题的不同视角，需要 FBI 综合判断）。
        """
        severity_rank = {
            "critical": 0,
            "hard": 0,  # health checker 的 hard 等价于 critical
            "high": 1,
            "medium": 2,
            "low": 3,
        }
        deduped: dict[str, dict[str, Any]] = {}
        for violation in violations:
            metric = str(violation.get("metric") or "")
            target_span = str(violation.get("target_span") or "")
            source_validator = str(violation.get("source_validator") or "")
            scene_index = violation.get("scene_index")
            scene_key = "" if scene_index is None else str(scene_index)
            key = f"{scene_key}:{source_validator}:{metric}:{target_span}"
            if key not in deduped:
                deduped[key] = violation
                continue
            existing = deduped[key]
            # blocks_commit=True 优先
            if violation.get("blocks_commit") and not existing.get("blocks_commit"):
                deduped[key] = violation
                continue
            if existing.get("blocks_commit") and not violation.get("blocks_commit"):
                continue
            # severity 更高优先
            existing_rank = severity_rank.get(str(existing.get("severity") or ""), 9)
            new_rank = severity_rank.get(str(violation.get("severity") or ""), 9)
            if new_rank < existing_rank:
                deduped[key] = violation
        return list(deduped.values())


_validator_collection: ValidatorCollection | None = None


def get_validator_collection() -> ValidatorCollection:
    """获取全局 ValidatorCollection 单例。"""
    global _validator_collection
    if _validator_collection is None:
        _validator_collection = ValidatorCollection()
    return _validator_collection
