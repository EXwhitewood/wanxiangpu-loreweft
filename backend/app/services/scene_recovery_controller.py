import hashlib
import time
import logging

from app.agents.scene_repairer import SceneRepairer
from app.services.quality_gate import QualityGate
from app.models.violation import make_violation, VALIDATOR_FAILURE_TYPES
from app.services.deterministic_repair_engine import TimeAnchorRepairer
from app.services.review_issue_semantics import normalize_violation_semantics
from app.services.recovery_metrics import RecoveryMetrics
from app.services.fact_progression_protocol import (
    apply_protocol_to_violations,
    is_fbi_repairable_violation,
    summarize_protocol,
)
from app.services.unified_repair_router import UnifiedRepairRouter

_logger = logging.getLogger(__name__)

DEFAULT_BUDGET = {
    "max_patch": 2,
    "max_rewrite": 2,
    "max_contract_repair": 2,
    "max_validator_retry_immediate": 3,
    "max_validator_retry_delayed": 3,
    "validator_retry_delay_seconds": [5, 15, 45],
    "max_rounds": 8,
    "timeout_seconds": 240,
    "content_timeout_seconds": 240,
    "validator_timeout_seconds": 180,
    "writer_timeout": 120,
    "patch_timeout": 60,
    "rewrite_timeout": 90,
    "validator_timeout": 45,
}

STREAM_BUDGET = {
    "max_patch": 2,
    "max_rewrite": 2,
    "max_contract_repair": 2,
    "max_validator_retry_immediate": 3,
    "max_validator_retry_delayed": 3,
    "validator_retry_delay_seconds": [5, 15, 45],
    "max_rounds": 8,
    "timeout_seconds": 300,
    "content_timeout_seconds": 300,
    "validator_timeout_seconds": 240,
    "writer_timeout": 120,
    "patch_timeout": 60,
    "rewrite_timeout": 90,
    "validator_timeout": 45,
}

PENDING_VALIDATION_TYPES = VALIDATOR_FAILURE_TYPES

VALID_STRATEGIES = {
    "patch_text",
    "rewrite_scene",
    "repair_contract",
    "validator_retry",
    "patch_text_by_proposition",
    "rewrite_scene_with_fact_contract",
    "patch_literary_quality",
    "rewrite_scene_with_experience_contract",
    "repair_experience_contract",
    "style_experience_negotiation",
    "fbi_repair",
}


def classify_violations(violations: list[dict]) -> dict[str, list[dict]]:
    categories = {
        "text_local": [],
        "scene_structural": [],
        "contract_invalid": [],
        "validator_unavailable": [],
        "non_repairable": [],
    }
    for v in violations:
        if not isinstance(v, dict):
            continue
        classification = str(v.get("classification") or "")
        if classification in {"progression", "time_jump", "referenced_time", "character_claim", "advisory_quality"}:
            continue
        if classification in {"alias_needed", "retcon"}:
            categories["non_repairable"].append(v)
            continue
        if classification == "contract_conflict":
            categories["contract_invalid"].append(v)
            continue
        raw_strategy = str(v.get("suggested_strategy", "manual_review") or "manual_review")
        normalized = normalize_violation_semantics(dict(v))
        strategy = str(normalized.get("suggested_strategy", raw_strategy) or raw_strategy)
        if (
            raw_strategy == "manual_review"
            and strategy == "patch_text"
            and normalized.get("type") not in {"timeline_conflict", "temporal_conflict"}
        ):
            strategy = "manual_review"
        if strategy == "patch_text":
            categories["text_local"].append(normalized)
        elif strategy == "patch_literary_quality":
            categories["text_local"].append(normalized)
        elif strategy == "patch_text_by_proposition":
            categories["text_local"].append(normalized)
        elif strategy == "rewrite_scene":
            categories["scene_structural"].append(normalized)
        elif strategy == "rewrite_scene_with_experience_contract":
            categories["scene_structural"].append(normalized)
        elif strategy == "rewrite_scene_with_fact_contract":
            categories["scene_structural"].append(normalized)
        elif strategy == "repair_contract":
            categories["contract_invalid"].append(normalized)
        elif strategy == "repair_experience_contract":
            categories["contract_invalid"].append(normalized)
        elif strategy == "validator_retry":
            categories["validator_unavailable"].append(normalized)
        elif strategy in {"manual_review", "style_experience_negotiation"}:
            categories["non_repairable"].append(normalized)
        else:
            if normalized.get("type") in PENDING_VALIDATION_TYPES:
                categories["validator_unavailable"].append(normalized)
            else:
                categories["contract_invalid"].append(normalized)
    return categories


class SceneRecoveryController:
    def __init__(self, budget: dict | None = None):
        b = budget or DEFAULT_BUDGET
        self.max_patch = b.get("max_patch", 2)
        self.max_rewrite = b.get("max_rewrite", 2)
        self.max_contract_repair = b.get("max_contract_repair", 1)
        self.max_validator_retry_immediate = b.get("max_validator_retry_immediate", 3)
        self.max_validator_retry_delayed = b.get("max_validator_retry_delayed", 3)
        self.validator_retry_delay_seconds = b.get("validator_retry_delay_seconds", [5, 15, 45])
        self.max_rounds = b.get("max_rounds", 8)
        self.timeout_seconds = b.get("timeout_seconds", 240)
        self.content_timeout_seconds = b.get("content_timeout_seconds", 240)
        self.validator_timeout_seconds = b.get("validator_timeout_seconds", 180)
        self.writer_timeout = b.get("writer_timeout", 120)
        self.patch_timeout = b.get("patch_timeout", 60)
        self.rewrite_timeout = b.get("rewrite_timeout", 90)
        self.validator_timeout = b.get("validator_timeout", 45)
        self.attempt_log: list[dict] = []
        self.start_time: float = 0
        self.draft_length: int = 0
        self.quality_gate = QualityGate()
        self.repair_router = UnifiedRepairRouter()
        self._last_repair_route: dict = {}
        self._patch_count = 0
        self._rewrite_count = 0
        self._contract_repair_count = 0
        self._validator_retry_immediate_count = 0
        self._validator_retry_delayed_count = 0
        self._patch_by_proposition_count = 0
        self._rewrite_with_fact_contract_count = 0
        self._fbi_repair_count = 0
        self._resolved_issue_ids: set[str] = set()
        self._round_count = 0
        self._content_round_count = 0

    @staticmethod
    def _has_blocking_violations(report: dict | None) -> bool:
        if not isinstance(report, dict):
            return True
        return any(
            isinstance(v, dict) and v.get("blocks_commit")
            for v in report.get("violations", [])
        )

    @staticmethod
    def _is_fbi_v2_candidate(finding) -> bool:
        """Allow LLM dispatch to see fuzzy prose findings before they have a lane."""
        if not getattr(finding, "blocks_commit", False):
            return False
        if finding.is_actionable():
            return True
        return (
            getattr(finding, "repair_scope", "") == "prose_text"
            and getattr(finding, "repair_lane", "") in {"none", "manual_only"}
            and bool(getattr(finding, "user_visible", False))
        )

    def _word_count_gate(self, text: str, context: dict) -> list[dict]:
        """字数硬门控：文本超过 hard_max_chars 时返回违规。"""
        word_budget = context.get("word_budget")
        if not word_budget or not isinstance(word_budget, dict):
            return []
        hard_max_chars = word_budget.get("hard_max_chars", 0)
        if hard_max_chars <= 0 or len(text) <= hard_max_chars:
            return []
        return [{
            "violation_id": hashlib.md5(f"scene_too_long:{len(text)}:{hard_max_chars}".encode()).hexdigest()[:12],
            "source": "deterministic",
            "type": "scene_too_long",
            "repair_lane": "deterministic",
            "blocks_commit": True,
            "severity": "high",
            "detail": f"文本长度 {len(text)} 超过硬性上限 {hard_max_chars}",
            "current_length": len(text),
            "hard_max_chars": hard_max_chars,
            "suggested_strategy": "patch_text",
            "scope": "prose_text",
        }]

    def _forbidden_content_gate(self, text: str, context: dict) -> list[dict]:
        """禁止内容门控：检查文本是否包含 forbidden 条目。"""
        scene_contract = context.get("scene_contract", {})
        if not isinstance(scene_contract, dict):
            return []
        forbidden_items = scene_contract.get("forbidden", [])
        if not forbidden_items or not isinstance(forbidden_items, list):
            return []
        violations = []
        for item in forbidden_items:
            if isinstance(item, str) and item in text:
                violations.append({
                    "violation_id": hashlib.md5(f"forbidden_triggered:{item}".encode()).hexdigest()[:12],
                    "source": "narrative_contract",
                    "type": "forbidden_triggered",
                    "repair_lane": "fbi_prose",
                    "blocks_commit": True,
                    "severity": "high",
                    "detail": f"文本包含禁止内容: {item[:50]}",
                    "forbidden_item": item,
                    "target_span": item,
                    "suggested_strategy": "patch_text",
                    "scope": "prose_text",
                })
        return violations

    async def _evaluate_with_pre_gates(self, text: str, context: dict, level: str = "full") -> dict:
        gate_violations = (
            TimeAnchorRepairer.detect_conflicts(text, context)
            + self._word_count_gate(text, context)
            + self._forbidden_content_gate(text, context)
        )
        report = await self.quality_gate.evaluate(
            context | {"generated_text": text},
            level=level,
        )
        if gate_violations:
            report = dict(report)
            report["violations"] = gate_violations + list(report.get("violations", []) or [])
        report["violations"] = apply_protocol_to_violations(
            list(report.get("violations", []) or []),
            context,
        )
        reports = report.get("reports")
        if isinstance(reports, dict):
            reports["fact_progression_protocol"] = summarize_protocol(report["violations"])
        report["protocol_summary"] = summarize_protocol(report["violations"])
        blocking_count = len([
            v for v in report.get("violations", [])
            if isinstance(v, dict) and v.get("blocks_commit")
        ])
        report["passed"] = blocking_count == 0 and bool(report.get("passed", False))
        report["commit_blocked"] = blocking_count > 0
        return report

    @staticmethod
    def _classify_issues(violations: list[dict]) -> dict[str, list[dict]]:
        """将违规分为文本问题和系统问题。

        系统问题（如验证器不可用、JSON 解析失败等）不应发送给 FBI 修复，
        而应标记为 is_system_issue=True, repair_scope="system"。
        """
        _SYSTEM_TYPE_KEYWORDS = {
            "validator_unavailable", "validator_timeout", "validator_error",
            "json_parse_failed", "schema_validation_failed", "internal_error",
        }
        text_issues: list[dict] = []
        system_issues: list[dict] = []
        for v in violations:
            if not isinstance(v, dict):
                continue
            v_type = v.get("type", "")
            if v_type in _SYSTEM_TYPE_KEYWORDS or v_type in VALIDATOR_FAILURE_TYPES:
                issue = dict(v)
                issue["is_system_issue"] = True
                issue["repair_scope"] = "system"
                system_issues.append(issue)
            else:
                text_issues.append(v)
        return {"text_issues": text_issues, "system_issues": system_issues}

    async def recover_generated_scene(self, context: dict, draft: str) -> dict:
        if not self.start_time:
            self.start_time = time.monotonic()
            self.attempt_log = []
            self._context = context

        if not draft:
            return self._block("non_repairable", error="Writer 返回空文本")
        if not self.draft_length:
            self.draft_length = len(draft)

        current_text = draft

        # 前置门控：字数门控 → 禁止内容门控 → 完整质量门控
        current_report = await self._evaluate_with_pre_gates(current_text, context, level="full")
        self._log_attempt("draft", current_text, current_report, violations_before=[], violations_after=current_report.get("violations", []))

        if current_report["passed"]:
            return self._accept(current_text, current_report, recovery_mode="none")

        while self._content_round_count < self.max_rounds:
            self._round_count += 1
            if not self._check_budget():
                return self._block("timeout", current_text, current_report)

            strategy = self._decide_strategy(current_report)
            if strategy == "accept":
                return self._accept(current_text, current_report, recovery_mode="repaired")
            if strategy == "blocked":
                return self._block("non_repairable", current_text, current_report)
            if strategy == "exhausted":
                return self._block("exhaustion", current_text, current_report)
            if strategy == "pending_validator_retry":
                return self._block("validator_exhausted", current_text, current_report)

            if strategy == "validator_retry":
                validator_elapsed = time.monotonic() - self.start_time
                if validator_elapsed >= self.validator_timeout_seconds:
                    return self._block("validator_exhausted", current_text, current_report)
                self._validator_retry_immediate_count += 1
                result = await self._try_validator_retry(current_text, context, current_report)
                validator_violations = [v for v in result.get("report", {}).get("violations", []) if v.get("type") in VALIDATOR_FAILURE_TYPES]
                if not validator_violations:
                    current_text = result.get("text", current_text)
                    current_report = result.get("report", current_report)
                    if current_report.get("passed"):
                        return self._accept(current_text, current_report, recovery_mode="repaired")
                self._log_attempt("validator_retry", current_text, result.get("report"), violations_before=current_report.get("violations", []), violations_after=result.get("report", {}).get("violations", []), round_num=self._round_count)
                continue

            self._content_round_count += 1

            violations_before = current_report.get("violations", [])
            result = await self._execute_strategy(
                strategy, current_text, context, current_report,
            )

            if result.get("context_updates"):
                context = context | result["context_updates"]
                self._context = context

            if result.get("status") == "fbi_needs_workbench":
                self._consume_budget(strategy)
                next_text = result.get("text", current_text)
                next_report = result.get("report", current_report)
                self._log_attempt(
                    strategy,
                    next_text,
                    next_report,
                    violations_before=violations_before,
                    violations_after=next_report.get("violations", []) if isinstance(next_report, dict) else [],
                    round_num=self._round_count,
                    error=result.get("error", "FBI V2 requires workbench"),
                    extra={
                        "v2_status": result.get("v2_status"),
                        "fbi_v2_summary": result.get("fbi_v2_summary", {}),
                        "fbi_v2_outcome": result.get("fbi_v2_outcome", {}),
                    },
                )
                return self._block(
                    "exhaustion",
                    next_text,
                    next_report,
                    error=result.get("error", "FBI V2 requires workbench"),
                )

            if result.get("status") == "blocked_contract":
                self._consume_budget(strategy)
                self._log_attempt(strategy, current_text, None, violations_before=violations_before, violations_after=[], error=result.get("error", "Contract repair failed"))
                return self._block("non_repairable", current_text, current_report, error=result.get("error", "Contract repair failed"))

            if result.get("status") == "agent_timeout":
                self._consume_budget(strategy)
                self._log_attempt(strategy, current_text, None, violations_before=violations_before, violations_after=[], error=result.get("error", "Agent timeout"))
                continue

            if result.get("status") == "agent_error":
                self._consume_budget(strategy)
                self._log_attempt(strategy, current_text, None, violations_before=violations_before, violations_after=[], error=result.get("error", "Agent error"))
                continue

            next_text = result.get("text")
            next_report = result.get("report")

            if next_text is None or next_report is None:
                self._consume_budget(strategy)
                self._log_attempt(strategy, current_text, None, violations_before=violations_before, violations_after=[], error=result.get("error", "Strategy produced no output"))
                continue

            violations_after = next_report.get("violations", [])
            self._log_attempt(
                strategy,
                next_text,
                next_report,
                violations_before=violations_before,
                violations_after=violations_after,
                round_num=self._round_count,
                extra={
                    key: result[key]
                    for key in ("v2_status", "fbi_v2_summary", "fbi_v2_outcome")
                    if key in result
                },
            )
            self._consume_budget(strategy)

            current_text = next_text
            current_report = next_report

            if current_report["passed"]:
                recovery_mode = (
                    "patched" if strategy in ("patch_text", "patch_text_by_proposition")
                    else "rewritten" if strategy in ("rewrite_scene", "rewrite_scene_with_fact_contract")
                    else "repaired"
                )
                return self._accept(current_text, current_report, recovery_mode=recovery_mode)

        return self._block("exhaustion", current_text, current_report)

    async def _execute_strategy(self, strategy: str, current_text: str, context: dict, current_report: dict) -> dict:
        if strategy == "patch_text":
            return await self._try_patch(current_text, context, current_report)
        elif strategy == "patch_literary_quality":
            return await self._try_patch(current_text, context, current_report)
        elif strategy == "patch_text_by_proposition":
            return await self._try_patch_by_proposition(current_text, context, current_report)
        elif strategy == "rewrite_scene":
            return await self._try_rewrite(context, current_report, rejected_text=current_text)
        elif strategy == "rewrite_scene_with_experience_contract":
            return await self._try_rewrite(context, current_report, rejected_text=current_text)
        elif strategy == "rewrite_scene_with_fact_contract":
            return await self._try_rewrite_with_fact_contract(context, current_report, rejected_text=current_text)
        elif strategy == "repair_contract":
            return await self._try_repair_contract(context, current_report, rejected_text=current_text)
        elif strategy == "repair_experience_contract":
            return await self._try_repair_contract(context, current_report, rejected_text=current_text)
        elif strategy == "validator_retry":
            return await self._try_validator_retry(current_text, context, current_report)
        elif strategy == "fbi_repair":
            return await self._try_fbi_repair(current_text, context, current_report)
        return {"status": "agent_error", "error": f"Unknown strategy: {strategy}"}

    async def _try_patch(self, text: str, context: dict, report: dict) -> dict:
        blocking = [v for v in report.get("violations", []) if v.get("blocks_commit") and v.get("suggested_strategy") != "validator_retry"]
        repair_agent = SceneRepairer()
        patch_context = dict(context)
        patch_context["generated_text"] = text
        patch_context["violations"] = blocking
        patch_context["mode"] = "patch_text"
        patch_context["repair_strategy"] = "patch"
        patch_context["scene_contract"] = context.get("scene_contract", {})
        patch_context["scene_truth_snapshot"] = context.get("scene_truth_snapshot")
        patch_context["word_budget"] = context.get("word_budget")
        patch_context["forbidden_items"] = context.get("scene_contract", {}).get("forbidden", [])
        patch_context["scene_credibility_contract"] = context.get("scene_credibility_contract") or (
            context.get("scene_contract", {}).get("scene_credibility_contract", {})
            if isinstance(context.get("scene_contract"), dict) else {}
        )

        try:
            repair_result = await repair_agent.execute(patch_context)
        except Exception as exc:
            return {"status": "agent_error", "error": f"Patch agent exception: {exc}"}

        if not repair_result.get("success"):
            return {"status": "agent_error", "error": repair_result.get("error", "Patch returned failure")}

        patched_text = repair_result.get("repaired_text", "")
        if not patched_text or not self._check_budget(patched_text):
            return {"status": "agent_error", "error": "Patch produced empty or oversized text"}

        fast_report = await self.quality_gate.evaluate(
            context | {"generated_text": patched_text}, level="fast",
        )

        if fast_report["passed"]:
            full_report = await self.quality_gate.evaluate(
                context | {"generated_text": patched_text}, level="full",
            )
            return {"text": patched_text, "report": full_report}

        if fast_report.get("commit_blocked"):
            full_report = await self.quality_gate.evaluate(
                context | {"generated_text": patched_text}, level="full",
            )
            return {"text": patched_text, "report": full_report}

        full_report = await self.quality_gate.evaluate(
            context | {"generated_text": patched_text}, level="full",
        )
        return {"text": patched_text, "report": full_report}

    async def _try_patch_by_proposition(self, text: str, context: dict, report: dict) -> dict:
        blocking = [v for v in report.get("violations", []) if v.get("blocks_commit") and v.get("suggested_strategy") != "validator_retry"]
        repair_agent = SceneRepairer()
        patch_context = dict(context)
        patch_context["generated_text"] = text
        patch_context["violations"] = blocking
        patch_context["mode"] = "patch_text_by_proposition"
        patch_context["repair_strategy"] = "patch"
        patch_context["scene_contract"] = context.get("scene_contract", {})
        patch_context["scene_truth_snapshot"] = context.get("scene_truth_snapshot")
        patch_context["word_budget"] = context.get("word_budget")
        patch_context["forbidden_items"] = context.get("scene_contract", {}).get("forbidden", [])

        # 构建结构化命题违规数据，供修复代理精确理解冲突
        proposition_violations = []
        for v in blocking:
            if v.get("suggested_strategy") == "patch_text_by_proposition":
                evidence = v.get("evidence", {}) if isinstance(v.get("evidence"), dict) else {}
                prop_violation = {
                    "violation_type": v.get("type", ""),
                    "source_text": v.get("target_span", ""),
                    "expected_behavior": v.get("expected_behavior", ""),
                    "actual_truth_layer": evidence.get("current_layer", ""),
                    "expected_truth_layer": evidence.get("expected_layer", ""),
                    "actual_certainty": evidence.get("certainty", ""),
                    "expected_certainty": evidence.get("expected_certainty", ""),
                    "evidence": evidence,
                }
                proposition_violations.append(prop_violation)

        patch_context["proposition_violations"] = proposition_violations
        patch_context["fact_contract"] = context.get("fact_contract")
        patch_context["scene_credibility_contract"] = context.get("scene_credibility_contract") or (
            context.get("scene_contract", {}).get("scene_credibility_contract", {})
            if isinstance(context.get("scene_contract"), dict) else {}
        )

        try:
            repair_result = await repair_agent.execute(patch_context)
        except Exception as exc:
            return {"status": "agent_error", "error": f"Patch-by-proposition agent exception: {exc}"}

        if not repair_result.get("success"):
            return {"status": "agent_error", "error": repair_result.get("error", "Patch-by-proposition returned failure")}

        patched_text = repair_result.get("repaired_text", "")
        if not patched_text or not self._check_budget(patched_text):
            return {"status": "agent_error", "error": "Patch-by-proposition produced empty or oversized text"}

        fast_report = await self.quality_gate.evaluate(
            context | {"generated_text": patched_text}, level="fast",
        )

        if fast_report["passed"]:
            full_report = await self.quality_gate.evaluate(
                context | {"generated_text": patched_text}, level="full",
            )
            return {"text": patched_text, "report": full_report}

        if fast_report.get("commit_blocked"):
            full_report = await self.quality_gate.evaluate(
                context | {"generated_text": patched_text}, level="full",
            )
            return {"text": patched_text, "report": full_report}

        # 修复后 quality_gate.evaluate 会自动重新运行命题抽取和审计，
        # 因为命题层已集成在 QualityGate.evaluate() 中。
        full_report = await self.quality_gate.evaluate(
            context | {"generated_text": patched_text}, level="full",
        )
        return {"text": patched_text, "report": full_report}

    async def _try_rewrite(self, context: dict, report: dict, rejected_text: str | None = None) -> dict:
        blocking = [v for v in report.get("violations", []) if v.get("blocks_commit") and v.get("suggested_strategy") != "validator_retry"]
        rewrite_agent = SceneRepairer()
        rewrite_context = {
            "scene_contract": context.get("scene_contract", {}),
            "chapter_state": context.get("chapter_state", {}),
            "previous_scene_ending": context.get("previous_scene_ending", ""),
            "previous_scenes_summary": context.get("previous_scenes_summary", ""),
            "violations": blocking,
            "rejected_text": rejected_text,
            "repair_strategy": "rewrite",
            "scene_truth_snapshot": context.get("scene_truth_snapshot"),
            "word_budget": context.get("word_budget"),
            "forbidden_items": context.get("scene_contract", {}).get("forbidden", []),
            "scene_credibility_contract": context.get("scene_credibility_contract") or (
                context.get("scene_contract", {}).get("scene_credibility_contract", {})
                if isinstance(context.get("scene_contract"), dict) else {}
            ),
        }

        try:
            rewrite_result = await rewrite_agent.execute(rewrite_context)
        except Exception as exc:
            return {"status": "agent_error", "error": f"Rewrite agent exception: {exc}"}

        if not rewrite_result.get("success"):
            return {"status": "agent_error", "error": rewrite_result.get("error", "Rewrite returned failure")}

        rewritten_text = rewrite_result.get("repaired_text") or rewrite_result.get("generated_text", "")
        if not rewritten_text or not self._check_budget(rewritten_text):
            return {"status": "agent_error", "error": "Rewrite produced empty or oversized text"}

        full_report = await self.quality_gate.evaluate(
            context | {"generated_text": rewritten_text}, level="full",
        )
        return {"text": rewritten_text, "report": full_report}

    async def _try_rewrite_with_fact_contract(self, context: dict, report: dict, rejected_text: str | None = None) -> dict:
        blocking = [v for v in report.get("violations", []) if v.get("blocks_commit") and v.get("suggested_strategy") != "validator_retry"]
        rewrite_agent = SceneRepairer()
        rewrite_context = {
            "scene_contract": context.get("scene_contract", {}),
            "chapter_state": context.get("chapter_state", {}),
            "previous_scene_ending": context.get("previous_scene_ending", ""),
            "previous_scenes_summary": context.get("previous_scenes_summary", ""),
            "violations": blocking,
            "rejected_text": rejected_text,
            "repair_strategy": "rewrite",
            "scene_truth_snapshot": context.get("scene_truth_snapshot"),
            "word_budget": context.get("word_budget"),
            "forbidden_items": context.get("scene_contract", {}).get("forbidden", []),
        }

        # 添加事实合同，确保重写代理了解精确的事实边界
        rewrite_context["fact_contract"] = context.get("fact_contract")

        # 构建结构化命题违规数据，供重写代理精确理解冲突
        proposition_violations = []
        for v in blocking:
            if v.get("suggested_strategy") == "rewrite_scene_with_fact_contract":
                evidence = v.get("evidence", {}) if isinstance(v.get("evidence"), dict) else {}
                prop_violation = {
                    "violation_type": v.get("type", ""),
                    "source_text": v.get("target_span", ""),
                    "expected_behavior": v.get("expected_behavior", ""),
                    "actual_truth_layer": evidence.get("current_layer", ""),
                    "expected_truth_layer": evidence.get("expected_layer", ""),
                    "actual_certainty": evidence.get("certainty", ""),
                    "expected_certainty": evidence.get("expected_certainty", ""),
                    "evidence": evidence,
                }
                proposition_violations.append(prop_violation)

        rewrite_context["proposition_violations"] = proposition_violations

        try:
            rewrite_result = await rewrite_agent.execute(rewrite_context)
        except Exception as exc:
            return {"status": "agent_error", "error": f"Rewrite-with-fact-contract agent exception: {exc}"}

        if not rewrite_result.get("success"):
            return {"status": "agent_error", "error": rewrite_result.get("error", "Rewrite-with-fact-contract returned failure")}

        rewritten_text = rewrite_result.get("repaired_text") or rewrite_result.get("generated_text", "")
        if not rewritten_text or not self._check_budget(rewritten_text):
            return {"status": "agent_error", "error": "Rewrite-with-fact-contract produced empty or oversized text"}

        # 修复后 quality_gate.evaluate 会自动重新运行命题抽取和审计，
        # 因为命题层已集成在 QualityGate.evaluate() 中。
        full_report = await self.quality_gate.evaluate(
            context | {"generated_text": rewritten_text}, level="full",
        )
        return {"text": rewritten_text, "report": full_report}

    async def _try_repair_contract(self, context: dict, report: dict, rejected_text: str = "") -> dict:
        contract = context.get("scene_contract", {})
        violations = [v for v in report.get("violations", []) if v.get("suggested_strategy") == "repair_contract"]

        repaired_contract = dict(contract)
        repairable_fields = {"goal", "conflict", "must_show", "forbidden"}
        repaired_count = 0

        if any(v.get("type") == "missing_contract" for v in violations):
            rebuilt_contract = self._rebuild_missing_contract(context)
            if rebuilt_contract:
                repaired_contract = rebuilt_contract
                repaired_count += 1

        for v in violations:
            expected = v.get("expected_behavior", "")
            target_span = v.get("target_span", "")
            if target_span in repairable_fields and expected:
                repaired_contract[target_span] = expected
                repaired_count += 1

        if repaired_count > 0:
            new_context = dict(context)
            new_context["scene_contract"] = repaired_contract

            rewrite_result = await self._try_rewrite(new_context, report, rejected_text=rejected_text)

            if rewrite_result.get("text"):
                rewrite_result["contract_repaired"] = True
                rewrite_result["repaired_fields"] = list(repairable_fields & {v.get("target_span") for v in violations})
                rewrite_result["context_updates"] = {"scene_contract": repaired_contract}
                return rewrite_result

        return {
            "status": "blocked_contract",
            "error": f"Contract repair failed: {len(violations)} unrepairable contract violations",
            "commit_blocked": True,
        }

    @staticmethod
    def _rebuild_missing_contract(context: dict) -> dict:
        package = context.get("scene_package") or {}
        compiled_contract = context.get("compiled_scene_contract") or package.get("scene_contract")
        if isinstance(compiled_contract, dict) and compiled_contract:
            return dict(compiled_contract)

        beat = context.get("scene_beat") or package.get("scene_beat") or {}
        if not isinstance(beat, dict):
            return {}

        goal = beat.get("goal_text") or beat.get("goal") or ""
        conflict = beat.get("conflict_text") or beat.get("conflict") or ""
        ending_state = beat.get("outcome_text") or beat.get("outcome") or ""
        if not goal or not conflict:
            return {}

        scene_index = context.get("scene_index", package.get("scene_index", 0))
        chapter_number = context.get("chapter_number", 0)
        pov_character = package.get("pov_character") or beat.get("pov_character") or beat.get("pov") or ""
        return {
            "scene_id": package.get("scene_id") or f"ch_{chapter_number:03d}_s{scene_index + 1}",
            "chapter_number": chapter_number,
            "scene_index": scene_index,
            "pov_character": pov_character,
            "pov_lock": pov_character,
            "temporal_anchor": beat.get("temporal_anchor") or "",
            "location_anchor": beat.get("location_anchor") or beat.get("location") or "",
            "goal": goal,
            "conflict": conflict,
            "must_show": beat.get("must_show") or [],
            "forbidden": beat.get("forbidden") or [],
            "ending_state": ending_state,
        }

    async def _try_validator_retry(self, text: str, context: dict, report: dict) -> dict:
        new_report = await self.quality_gate.evaluate(
            context | {"generated_text": text}, level="full",
        )
        validator_violations = [v for v in new_report.get("violations", []) if v.get("type") in VALIDATOR_FAILURE_TYPES]
        if not validator_violations:
            RecoveryMetrics().record_validator_retry(recovered=True)
        return {"text": text, "report": new_report}

    def _apply_v2_contract_adjustments(self, context: dict, findings: list) -> dict:
        """应用 V2 合同级确定性修复。

        正文没有错而合同过载时，直接调整当前 workflow 的 scene_contract：
        must_show 只保留前 5 条，其余转为 nice_to_have。这样复检会基于
        修正后的合同进行，而不是把合同问题伪装成正文问题。
        """
        scene_contract = dict(context.get("scene_contract") or {})
        if not scene_contract:
            return {}

        changed = False
        for finding in findings:
            f_type = getattr(finding, "type", "")
            repair_scope = getattr(finding, "repair_scope", "")
            if repair_scope != "scene_contract":
                continue

            if f_type == "must_show_overload":
                must_show = scene_contract.get("must_show", [])
                if isinstance(must_show, list) and len(must_show) > 5:
                    kept = must_show[:5]
                    overflow = must_show[5:]
                    scene_contract["must_show"] = kept
                    existing_nice = scene_contract.get("nice_to_have", [])
                    if not isinstance(existing_nice, list):
                        existing_nice = []
                    scene_contract["nice_to_have"] = existing_nice + [
                        item for item in overflow if item not in existing_nice
                    ]
                    changed = True

        if not changed:
            return {}

        updates = {"scene_contract": scene_contract}
        scene_context_package = context.get("scene_context_package")
        if isinstance(scene_context_package, dict):
            updated_package = dict(scene_context_package)
            updated_package["scene_contract"] = scene_contract
            updates["scene_context_package"] = updated_package
        return updates

    async def _try_fbi_repair(self, text: str, context: dict, report: dict) -> dict:
        """通过 FBI V2 修复问题。

        V2 链路：QualityFindingHub → RepairOrderCompiler → FBIV2Orchestrator
        1. 所有 violations 和 deslop findings 统一通过 QualityFindingHub 归一化
        2. RepairOrderCompiler 编译为可执行 RepairOrder
        3. FBIV2Orchestrator 执行：确定性优先 → LLM 修复 → 验收

        V2 失败时显式返回 v2_status，不再静默降级到旧路径。
        """
        text_hash = hashlib.md5(text.encode()).hexdigest()[:12]
        return await self._try_fbi_repair_v2_primary(text, context, report, text_hash)

    async def _try_fbi_repair_v2_primary(self, text: str, context: dict, report: dict, text_hash: str) -> dict:
        """Primary FBI V2 repair path.

        Return contract:
        - text/report: FBI cleared blocking issues and the scene can continue.
        - status=fbi_needs_workbench: FBI tried its full loop but unresolved issues remain.
        - missing V2 modules are deployment errors and must fail visibly.
        """
        from app.services.quality_finding_hub import get_quality_finding_hub
        from app.services.fbi_v2.orchestrator import get_fbi_v2_orchestrator

        try:
            violations = [
                violation for violation in list(report.get("violations", []) or [])
                if is_fbi_repairable_violation(violation)
            ]
            if not violations:
                return {
                    "status": "fbi_needs_workbench",
                    "text": text,
                    "report": report,
                    "method": "fbi_v2",
                    "v2_status": "v2_failed",
                    "fbi_v2_outcome": {
                        "status": "needs_workbench",
                        "summary": {"total_orders": 0, "succeeded": 0, "failed": 0, "rounds_used": 0},
                        "failure_reasons": ["no protocol-repairable prose violations"],
                    },
                    "fbi_v2_summary": {"total_orders": 0, "succeeded": 0, "failed": 0, "rounds_used": 0},
                    "error": "FBI V2 found no protocol-repairable prose violations",
                }
            for violation in violations:
                if isinstance(violation, dict):
                    violation.setdefault("text_hash", text_hash)

            classified = self._classify_issues(violations)
            text_issues = classified["text_issues"]
            system_issues = classified["system_issues"]
            if system_issues:
                _logger.info("FBI V2 skipped %d validator/system issues", len(system_issues))

            hub = get_quality_finding_hub()
            deslop_reports = (report.get("reports") or {}).get("deslop_gate", {})
            deslop_findings = (
                deslop_reports.get("review_findings", [])
                if isinstance(deslop_reports, dict)
                else []
            )
            findings = hub.normalize_all(violations=text_issues, deslop_findings=deslop_findings)
            actionable = [
                finding for finding in findings
                if self._is_fbi_v2_candidate(finding) and finding.id not in self._resolved_issue_ids
            ]

            if not actionable:
                outcome = {
                    "status": "failed",
                    "text": text,
                    "summary": {"total_orders": 0, "succeeded": 0, "failed": 0, "rounds_used": 0},
                    "failure_reasons": ["no actionable findings after normalization"],
                    "unresolved_findings": [
                        finding.model_dump() for finding in findings if finding.blocks_commit
                    ],
                }
                return {
                    "status": "fbi_needs_workbench",
                    "text": text,
                    "report": report,
                    "method": "fbi_v2",
                    "v2_status": "v2_failed",
                    "fbi_v2_outcome": outcome,
                    "fbi_v2_summary": outcome["summary"],
                    "error": "FBI V2 found no executable repair orders",
                }

            context_updates = self._apply_v2_contract_adjustments(context, actionable)
            effective_context = context | context_updates if context_updates else context
            current_text = text
            current_findings = actionable
            outcome: dict = {}
            final_report = report
            resolved_ids: list[str] = []
            patches: list[dict] = []
            failure_reasons: list[str] = []
            max_cascade_rounds = 3

            for cascade_round in range(1, max_cascade_rounds + 1):
                outcome = await get_fbi_v2_orchestrator().run(
                    project_id=str(context.get("project_id", "")),
                    candidate_text=current_text,
                    findings=current_findings,
                    context=effective_context,
                    max_rounds=3,
                )

                current_text = outcome.get("text", current_text) or current_text
                patches.extend(outcome.get("patches", []) or [])
                round_resolved = list(outcome.get("resolved_finding_ids", []) or [])
                if round_resolved:
                    self._resolved_issue_ids.update(round_resolved)
                    resolved_ids.extend([rid for rid in round_resolved if rid not in resolved_ids])

                final_report = await self._evaluate_with_pre_gates(current_text, effective_context, level="full")
                has_blocking = self._has_blocking_violations(final_report)
                if outcome.get("status") == "resolved" and not has_blocking and self._check_budget(current_text):
                    summary = dict(outcome.get("summary", {}) or {})
                    summary["cascade_rounds"] = cascade_round
                    return {
                        "text": current_text,
                        "report": final_report,
                        "method": "fbi_v2",
                        "v2_status": "v2_resolved",
                        "patches": patches,
                        "fbi_v2_summary": summary,
                        "fbi_v2_outcome": outcome,
                        "resolved_issue_ids": resolved_ids,
                        **({"context_updates": context_updates} if context_updates else {}),
                    }

                if not has_blocking:
                    break

                final_violations = [
                    violation for violation in list(final_report.get("violations", []) or [])
                    if is_fbi_repairable_violation(violation)
                ]
                for violation in final_violations:
                    if isinstance(violation, dict):
                        violation.setdefault("text_hash", hashlib.md5(current_text.encode()).hexdigest()[:12])
                final_classified = self._classify_issues(final_violations)
                final_findings = hub.normalize_all(violations=final_classified["text_issues"], deslop_findings=[])
                next_findings = [
                    finding for finding in final_findings
                    if self._is_fbi_v2_candidate(finding) and finding.id not in self._resolved_issue_ids
                ]
                if not next_findings:
                    failure_reasons.append("blocking findings remain but no executable FBI orders were produced")
                    break
                if cascade_round >= max_cascade_rounds:
                    failure_reasons.append("blocking findings remain after FBI V2 cascade rounds")
                    break
                current_findings = next_findings

            new_text = current_text
            summary = dict(outcome.get("summary", {}) or {})
            summary["cascade_rounds"] = summary.get("cascade_rounds") or max_cascade_rounds
            has_blocking = self._has_blocking_violations(final_report)
            failure_reasons.extend(list(outcome.get("failure_reasons") or []))
            if has_blocking and "blocking findings remain after FBI V2 recheck" not in failure_reasons:
                failure_reasons.append("blocking findings remain after FBI V2 recheck")
            if not self._check_budget(new_text):
                failure_reasons.append("candidate text exceeds recovery budget")

            return {
                "status": "fbi_needs_workbench",
                "text": new_text,
                "report": final_report,
                "method": "fbi_v2",
                "v2_status": "v2_failed" if outcome.get("status") != "resolved" else "v2_degraded",
                "patches": patches,
                "fbi_v2_summary": summary,
                "fbi_v2_outcome": outcome,
                "resolved_issue_ids": resolved_ids,
                "remaining_issues": final_report.get("violations", []),
                "failure_reasons": failure_reasons,
                "error": "; ".join(failure_reasons) or "FBI V2 did not clear all blocking findings",
                **({"context_updates": context_updates} if context_updates else {}),
            }
        except Exception as exc:
            _logger.exception("FBI V2 repair crashed")
            outcome = {
                "status": "failed",
                "text": text,
                "summary": {"total_orders": 0, "succeeded": 0, "failed": 0, "rounds_used": 0},
                "failure_reasons": [str(exc)],
            }
            return {
                "status": "fbi_needs_workbench",
                "text": text,
                "report": report,
                "method": "fbi_v2",
                "v2_status": "v2_failed",
                "fbi_v2_outcome": outcome,
                "fbi_v2_summary": outcome["summary"],
                "error": f"FBI V2 repair crashed: {exc}",
            }

    def _decide_strategy(self, report: dict) -> str:
        violations = report.get("violations", [])
        blocking = [v for v in violations if v.get("blocks_commit")]
        if not blocking:
            return "accept"

        route = self.repair_router.route(
            blocking,
            budgets={
                "patch_used": self._patch_count,
                "patch_limit": self.max_patch,
                "rewrite_used": self._rewrite_count,
                "rewrite_limit": self.max_rewrite,
            },
            fbi_mode=self._get_fbi_mode(),
        )
        self._last_repair_route = route.as_dict()
        _logger.info("Unified repair route: %s", self._last_repair_route)
        if route.strategy == "validator_retry":
            if self._validator_retry_immediate_count < self.max_validator_retry_immediate:
                return "validator_retry"
            return "pending_validator_retry"
        if route.strategy in {"repair_contract", "fbi_repair", "blocked"}:
            return route.strategy

        categories = classify_violations(blocking)

        # 命题审计违规类型集合
        _PROPOSITION_PATCH_TYPES = {
            "truth_layer_conflict",
            "certainty_escalation",
            "spatial_conflict",
            "ownership_conflict",
            "required_ambiguity_broken",
        }
        _PROPOSITION_REWRITE_TYPES = {
            "responsibility_polarity_conflict",
            "temporal_conflict",
            "clue_provenance_error_proposition",
            "forbidden_assertion_triggered",
        }

        # 检查是否存在命题审计违规，优先使用命题感知策略
        blocking_types = {v.get("type", "") for v in blocking}
        has_proposition_patch = bool(blocking_types & _PROPOSITION_PATCH_TYPES)
        has_proposition_rewrite = bool(blocking_types & _PROPOSITION_REWRITE_TYPES)

        non_validator_blocking = [
            v for v in blocking
            if isinstance(v, dict) and v.get("type") not in VALIDATOR_FAILURE_TYPES
        ]
        if not non_validator_blocking:
            if self._validator_retry_immediate_count < self.max_validator_retry_immediate:
                return "validator_retry"
            return "pending_validator_retry"

        protocol_classes = {str(v.get("classification") or "") for v in non_validator_blocking}
        if protocol_classes and protocol_classes <= {"contract_conflict"}:
            if self._contract_repair_count < self.max_contract_repair:
                return "repair_contract"
            return "blocked"
        if protocol_classes & {"alias_needed", "retcon"}:
            return "blocked"

        # FBI 优先：如果 FBI 模式启用，优先使用 FBI 修复
        fbi_mode = self._get_fbi_mode()
        fbi_repairable = [v for v in non_validator_blocking if is_fbi_repairable_violation(v)]
        if fbi_mode not in ("off",) and fbi_repairable:
            return "fbi_repair"

        if has_proposition_patch and self._patch_by_proposition_count < self.max_patch:
            return "patch_text_by_proposition"

        if has_proposition_rewrite and self._rewrite_with_fact_contract_count < self.max_rewrite:
            return "rewrite_scene_with_fact_contract"

        if categories["non_repairable"]:
            has_validator = bool(categories["validator_unavailable"])
            has_content = bool(categories["text_local"] or categories["scene_structural"] or categories["contract_invalid"])
            if has_validator and not has_content:
                if self._validator_retry_immediate_count < self.max_validator_retry_immediate:
                    return "validator_retry"
                return "pending_validator_retry"
            return "blocked"

        if categories["validator_unavailable"]:
            if self._validator_retry_immediate_count < self.max_validator_retry_immediate:
                return "validator_retry"
            return "pending_validator_retry"

        if categories["contract_invalid"]:
            if self._contract_repair_count < self.max_contract_repair:
                return "repair_contract"
            return "blocked"

        if categories["scene_structural"]:
            if self._rewrite_count < self.max_rewrite:
                return "rewrite_scene"

        high_severity = [v for v in blocking if v.get("severity") in ("high", "critical")]
        if len(high_severity) >= 2 and self._rewrite_count < self.max_rewrite:
            return "rewrite_scene"

        if categories["text_local"]:
            if self._patch_count < self.max_patch:
                return "patch_text"

        if self._rewrite_count < self.max_rewrite:
            return "rewrite_scene"

        if self._patch_count < self.max_patch:
            return "patch_text"

        # The contract is still valid: the automatic content-repair budget was
        # simply exhausted. Preserve the best candidate for the resumable
        # human-review checkpoint instead of misreporting contract corruption.
        return "exhausted"

    def _get_fbi_mode(self) -> str:
        if hasattr(self, "_context") and self._context:
            generation_features = self._context.get("generation_features")
            if isinstance(generation_features, dict):
                return str(generation_features.get("fbi_repair_mode", "off") or "off")
            policy = self._context.get("generation_feature_policy")
            if isinstance(policy, dict):
                return str(policy.get("fbi_repair_mode", "off") or "off")
            if policy is not None:
                return str(getattr(policy, "fbi_repair_mode", "off") or "off")
        return "off"

    def _consume_budget(self, strategy: str):
        if strategy == "patch_text":
            self._patch_count += 1
        elif strategy == "patch_text_by_proposition":
            self._patch_by_proposition_count += 1
            self._patch_count += 1
        elif strategy == "rewrite_scene":
            self._rewrite_count += 1
        elif strategy == "rewrite_scene_with_fact_contract":
            self._rewrite_with_fact_contract_count += 1
            self._rewrite_count += 1
        elif strategy == "repair_contract":
            self._contract_repair_count += 1
        elif strategy == "validator_retry":
            self._validator_retry_immediate_count += 1
        elif strategy == "fbi_repair":
            self._fbi_repair_count += 1

    def _check_budget(self, candidate_text: str | None = None) -> bool:
        elapsed = time.monotonic() - self.start_time
        if elapsed >= self.timeout_seconds:
            return False
        if candidate_text is None or not self.draft_length:
            return True
        word_budget = self._context.get("word_budget") if hasattr(self, "_context") else None
        if word_budget and isinstance(word_budget, dict):
            hard_max = word_budget.get("hard_max_chars", 0)
            if hard_max > 0 and len(candidate_text) > hard_max * 1.5:
                return False
        return len(candidate_text) <= self.draft_length * 3

    def _log_attempt(
        self,
        strategy: str,
        text: str | None,
        report: dict | None,
        violations_before: list | None = None,
        violations_after: list | None = None,
        round_num: int = 0,
        error: str = "",
        extra: dict | None = None,
    ):
        before_ids = [v.get("violation_id") for v in (violations_before or []) if isinstance(v, dict)]
        after_ids = [v.get("violation_id") for v in (violations_after or []) if isinstance(v, dict)]
        repaired = set(before_ids) - set(after_ids)
        introduced = set(after_ids) - set(before_ids)

        entry = {
            "round": round_num or len(self.attempt_log) + 1,
            "strategy": strategy,
            "status": "accepted" if (report and report.get("passed")) else ("error" if error else "validation_failed"),
            "text_length": len(text) if text else 0,
            "violation_ids_before": before_ids,
            "violation_ids_after": after_ids,
            "repaired_violation_ids": list(repaired),
            "introduced_violation_ids": list(introduced),
            "duration_ms": int((time.monotonic() - self.start_time) * 1000),
        }
        if error:
            entry["error"] = error
        if report and isinstance(report, dict):
            entry["commit_blocked"] = report.get("commit_blocked", False)
            entry["passed"] = report.get("passed", False)
        if self._last_repair_route:
            entry["repair_route"] = dict(self._last_repair_route)
        if extra:
            entry.update(extra)
        self.attempt_log.append(entry)

    def _accept(self, text: str, report: dict, recovery_mode: str) -> dict:
        metrics = RecoveryMetrics()
        if recovery_mode in ("patched", "rewritten", "repaired"):
            metrics.record_content_repair(accepted=True)
        result = {
            "generated_text": text,
            "commit_blocked": False,
            "status": "accepted",
            "recovery_mode": recovery_mode,
            "attempts": self.attempt_log,
            "final_report": report,
        }
        latest_fbi = self._latest_fbi_v2_outcome()
        if latest_fbi:
            result["fbi_v2_outcome"] = latest_fbi
        return result

    def _block(self, reason: str, current_text: str | None = None, final_report: dict | None = None, error: str = "") -> dict:
        if reason == "exhaustion" and current_text and final_report:
            remaining_blocking = [v for v in final_report.get("violations", []) if v.get("blocks_commit")]
            if not remaining_blocking:
                return self._accept(current_text, final_report, recovery_mode="repaired")

        if reason == "validator_exhausted":
            validator_violations = [v for v in (final_report or {}).get("violations", []) if v.get("type") in VALIDATOR_FAILURE_TYPES]
            failure_types = list({v.get("type", "unknown") for v in validator_violations})
            last_errors = [v.get("detail", "")[:200] for v in validator_violations[:3]]
            RecoveryMetrics().record_validator_retry(recovered=False)
            result = {
                "generated_text": current_text or "",
                "commit_blocked": True,
                "status": "pending_validator_retry",
                "recovery_mode": "none",
                "attempts": self.attempt_log,
                "final_report": final_report,
                "error": error or "审校器暂时不可用，正文暂存待验证",
                "candidate_text": current_text or "",
                "needs_human_review": False,
                "validator_retry": {
                    "immediate_attempts": self._validator_retry_immediate_count,
                    "delayed_attempts": self._validator_retry_delayed_count,
                    "failure_types": failure_types,
                    "last_errors": last_errors,
                    "next_retry_delays": self.validator_retry_delay_seconds,
                },
            }
            latest_fbi = self._latest_fbi_v2_outcome()
            if latest_fbi:
                result["fbi_v2_outcome"] = latest_fbi
            return result

        status = f"blocked_{reason}"
        if reason == "exhaustion":
            remaining_blocking = [v for v in (final_report or {}).get("violations", []) if v.get("blocks_commit")]
            has_only_validator = all(v.get("type") in VALIDATOR_FAILURE_TYPES for v in remaining_blocking) if remaining_blocking else False
            if has_only_validator:
                status = "pending_validator_retry"
            else:
                status = "waiting_human_content_review"
        elif reason == "timeout":
            status = "pending_validation"
        elif reason == "non_repairable":
            status = "blocked_contract"

        result = {
            "generated_text": current_text or "",
            "commit_blocked": True,
            "status": status,
            "recovery_mode": "none",
            "attempts": self.attempt_log,
            "final_report": final_report,
            "error": error,
            "candidate_text": current_text or "",
            "needs_human_review": bool(current_text) and status == "waiting_human_content_review",
        }
        latest_fbi = self._latest_fbi_v2_outcome()
        if latest_fbi:
            result["fbi_v2_outcome"] = latest_fbi
        return result

    def _latest_fbi_v2_outcome(self) -> dict | None:
        for attempt in reversed(self.attempt_log):
            outcome = attempt.get("fbi_v2_outcome") if isinstance(attempt, dict) else None
            if isinstance(outcome, dict) and outcome:
                return outcome
        return None
