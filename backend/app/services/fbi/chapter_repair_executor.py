"""整章修复执行器 - 按 ChapterRepairPlan 的并行分组执行修复命令。

核心职责：
1. 接收 ChapterRepairPlan 和场景正文 dict
2. 按 get_parallel_groups() 分组并行执行
3. 同组内订单并行（asyncio.gather），同场景多订单串行合并
4. 根据 repair_type 分派到对应修复机制
5. 返回更新后的 scene_texts 和 ChapterRepairPlan
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import logging
import re
import time
from typing import Any

from app.models.chapter_review import ChapterRepairOrder, ChapterRepairPlan, RepairWorkUnit, ToolCommandBatch
from app.services.fbi.blueprint_protocol_validator import BlueprintProtocolValidator
from app.services.fbi.blueprint_route_registry import get_blueprint_route
from app.services.fbi.edit_window_planner import EditWindowPlanner
from app.services.fbi.final_gate_text_repair import repair_final_gate_text
from app.services.fbi.patch_applier import PatchApplier
from app.services.fbi.patch_merger import PatchMerger
from app.services.fbi.tool_executor import ToolExecutor
from app.services.fbi.work_unit_builder import build_revision_blueprint
from app.services.scene_contract_protocol import hard_obligation_sources
from app.utils.dash_artifacts import (
    DEFAULT_DASH_ARTIFACT_MAX_PER_1000,
    count_dash_artifacts,
    dash_artifact_allowed_count,
    dash_artifact_replacement,
    dash_artifact_replacement_indexes,
    iter_dash_artifacts,
)
from app.utils.pov_evidence import analyze_inner_access

# 方案 30：B 类语义 metric 的修复侧后置校验改用 LLM
from app.services.quality_checkers.llm_semantic_checker import B_CLASS_METRICS

_logger = logging.getLogger(__name__)

_NO_EFFECT_AFTER_PRIOR_CHANGE_FAILURES = {
    "unchanged_result",
    "target_span_not_improved",
}
# 通用修复：target_metric_not_improved 不再被视为"可被 prior change 豁免"的失败。
# 原因：target_metric_not_improved 有明确的指标 before/after 对比（如 before=1, after=1），
# 指标客观未改善不应因"文本有变化"就认为修复成功。之前的逻辑（_is_non_blocking_no_effect_after_prior_change）
# 会因为 final_text != original_text（tool patch 改了无关文本）就把 failed order 标记为 skipped(accepted=True)，
# 静默放弃修复，导致 L10 强后检发现指标仍违规时 commit_gate blocked。
# 移除后，target_metric_not_improved 的 order 会保持 failed 状态，触发重试机制给系统另一个修复机会。
# unchanged_result 和 target_span_not_improved 保留：它们可能因 prior change 间接解决，合理豁免。

_REPAIR_STRENGTH_ORDER = ["S0", "S1", "S2", "S3", "S4", "S5"]

_DETERMINISTIC_FACT_REPAIR_TYPES = {
    "fact_conflict",
    "internal_conflict",
    "identity_conflict",
    "setting_conflict",
    "spatial_conflict",
    "ownership_conflict",
    "timeline_conflict",
    "temporal_conflict",
    "clue_provenance_error",
    "clue_provenance_error_proposition",
    "unprovenanced_clue",
}

_CHAPTER_REFERENCE_TYPES = {
    "chapter_reference",
    "meta_chapter_reference",
    "meta_narrative_reference",
}

# 方案B：违反类型字段中可能存放 metric 名称的 key
_METRIC_EXTRACTION_KEYS = (
    "metric",
    "metric_name",
    "metric_key",
    "type",
    "violation_type",
    "semantic_type",
    "rule_id",
    "check_id",
    "code",
)

# repair_type 值不是 metric 名称，需要排除
_REPAIR_TYPE_VALUES = frozenset({
    "local_patch",
    "contract_completion_patch",
    "scene_rewrite",
    "cross_scene_alignment",
    "contract_patch",
    "manual_review",
})


def _extract_metric_names(order: ChapterRepairOrder, violations: list[dict]) -> list[str]:
    """从 order 和 violations 中提取 metric 名称（通用，不针对任何 metric）。"""
    names: list[str] = []
    # 1. 从 violations 提取
    for v in violations or []:
        if not isinstance(v, dict):
            continue
        for key in _METRIC_EXTRACTION_KEYS:
            value = str(v.get(key) or "").strip().lower()
            if value and value not in _REPAIR_TYPE_VALUES:
                names.append(value)
    # 2. 从 order.repair_brief 提取
    brief = order.repair_brief or {}
    if isinstance(brief, dict):
        for key in ("metric", "metric_name", "metric_key"):
            value = str(brief.get(key) or "").strip().lower()
            if value and value not in _REPAIR_TYPE_VALUES:
                names.append(value)
    # 3. 从 order.violation_details 提取（如果 violations 参数不是从 violation_details 构建的）
    for v in (order.violation_details or []):
        if not isinstance(v, dict):
            continue
        for key in _METRIC_EXTRACTION_KEYS:
            value = str(v.get(key) or "").strip().lower()
            if value and value not in _REPAIR_TYPE_VALUES:
                names.append(value)
    return names


def _order_requires_llm(order: ChapterRepairOrder, violations: list[dict]) -> bool:
    """判断 order 关联的 metric 是否需要 LLM 创作修复（方案B通用纪律）。

    基于路由表 requires_llm 字段判断，不针对任何 metric：
    - 任何 metric 的 requires_llm=True（含未注册默认 True）→ 返回 True
    - 所有 metric 显式 requires_llm=False → 返回 False
    - 无 metric 信息 → 返回 True（安全默认，让 LLM 兜底）
    """
    metric_names = _extract_metric_names(order, violations)
    if not metric_names:
        return True

    for name in metric_names:
        route = get_blueprint_route(name)
        if route is None:
            # 未注册的 metric 默认需要 LLM
            return True
        if route.requires_llm:
            return True
    # 所有 metric 都显式 requires_llm=False
    return False


_BATCHABLE_LOCAL_QUALITY_TYPES = {
    "abstraction_over_budget",
    "low_conflict_density",
    "flat_pressure_ramp",
    "weak_curiosity_engine",
    "missing_micro_payoff",
    "weak_opening_hook",
    "low_event_density",
    "low_reversal_density",
    "weak_chapter_end_hook",
    "low_reader_retention",
    "reader_experience_advisory",
}

_CHAPTER_REFERENCE_RE = re.compile(
    r"(?:第\s*[零〇一二两三四五六七八九十百千万\d]+\s*[章节回](?:\s*[中里内时])?|"
    r"(?:上一|前一|上|前|本|这|这一)\s*章(?:\s*[中里内时])?)"
)

_WEAK_PROSE_PREFIXES = (
    "的", "了", "着", "但", "而", "却", "仍", "也", "又", "再", "只", "便", "就",
    "她", "他", "它", "这", "那", "一", "有", "在",
)

_WEAK_PROSE_SUFFIXES = (
    "的", "地", "声", "响", "光", "影", "风", "雾", "气", "味", "香", "色",
    "痕", "纹", "线", "尘", "沙", "叶",
)

_WEAK_FUNCTION_TERMS = {
    "同时", "随后", "然后", "接着", "此时", "这时", "于是", "最后",
    "开始", "继续", "依然", "仍然", "忽然", "突然",
}

_ENTITY_ANCHOR_RE = re.compile(
    r"(?:[一二两三四五六七八九十百千万\d]+[位名个]?"
    r"(?:师兄|师姐|师弟|师妹|师父|师尊|长老|弟子|妖兽|妖修|魔修|丹师|敌人|护卫|修士))"
)

# 循环#13：明喻标记——含这些标记的短保护词片段视为弱保护词
# 根因：ai_simile_overuse 修复需要改写含明喻标记的短语（如"更像是一"/"像油一样"），
#   但这些短语可能被纳入 protected_terms，protection_check 拒绝修复，
#   导致明喻密度修复被跳过，recheck 仍检测到明喻超标。
# 通用性：所有题材的明喻密度修复都需要改写明喻短语，不应被保护词阻断。
_SIMILE_MARKERS = (
    "更像", "像是", "仿佛", "似的", "一样", "如同",
    "宛如", "犹如", "好似", "好像", "恍若",
)


def _compute_text_hash(text: str) -> str:
    """计算文本 MD5 哈希。"""
    return hashlib.md5(text.encode("utf-8")).hexdigest()


# 通用：当 FBI 的 expected_behavior/suggested_correction 本身表明当前正文可接受时，
# SceneRepairer 正确返回原文（无需修改），不应计为失败。
# 这些模式是 FBI 自己的分析语言，不是针对任何 metric 的特化判断。
_ACCEPTABLE_AS_IS_RE = re.compile(
    r"是合理的|合理的|可不修改|无需修改|不需要修改|可接受|是正确的|与合同一致|与事实一致"
)


def _order_violation_accepts_no_change(order: ChapterRepairOrder) -> bool:
    """Check if the order's violation description indicates the current text is acceptable as-is.

    通用判断：当 FBI 的 suggested_correction / expected_behavior 本身含接受语言
    （如"是合理的"、"可不修改"、"与合同一致"）时，表明该违规可能不适用于当前场景，
    SceneRepairer 返回原文是正确行为，不应计为失败。
    """
    text_parts: list[str] = [str(order.reason or ""), str(order.instruction or "")]
    for v in (order.violation_details or []):
        if isinstance(v, dict):
            text_parts.append(str(v.get("expected_behavior") or ""))
            text_parts.append(str(v.get("suggested_correction") or ""))
            text_parts.append(str(v.get("detail") or ""))
    combined = " ".join(text_parts)
    return bool(_ACCEPTABLE_AS_IS_RE.search(combined))


def _new_diagnostic_instruction_leaks(
    order: ChapterRepairOrder,
    before_text: str,
    after_text: str,
) -> list[str]:
    """Find newly copied control-plane instructions in narrative output.

    This is evidence-based rather than a topic or phrase blacklist: only text
    sourced from the current repair order's diagnostic metadata can trigger it.
    Legitimate narrative that already existed in the scene is therefore not
    rejected.
    """
    from app.services.fbi.review_minister import (
        _looks_like_instruction,
        _looks_like_quality_contract_statement,
    )

    candidates: list[str] = []
    for violation in order.violation_details or []:
        if not isinstance(violation, dict):
            continue
        for key in (
            "expected_behavior",
            "suggested_strategy",
            "detail",
            "reason",
            "repair_goal",
        ):
            value = violation.get(key)
            if isinstance(value, dict):
                values = [str(item or "") for item in value.values()]
            else:
                values = [str(value or "")]
            for item in values:
                text = re.sub(r"\s+", " ", item).strip()
                if len(text) < 16:
                    continue
                if not (
                    _looks_like_instruction(text)
                    or _looks_like_quality_contract_statement(text)
                ):
                    continue
                if text in after_text and text not in before_text and text not in candidates:
                    candidates.append(text)
    return candidates


# local_patch 后置自检：检查是否引入新的明喻结构。
# 根因：LLM 在修复 fact_conflict 等问题时容易引入新的"像...一样"明喻，
# 导致 recheck 时 ai_simile_overuse 复发。只检查完整明喻结构，确保轻量快速。
_SIMILE_STRUCTURE_PATTERN = re.compile(
    r"像[^。！？\n]{1,40}?(?:一样|似的|般|那样|一般)"
)


def _count_new_simile_structures(before_text: str, after_text: str) -> int:
    """统计 after_text 中比 before_text 新增的完整明喻结构数量。"""
    before_count = len(_SIMILE_STRUCTURE_PATTERN.findall(before_text or ""))
    after_count = len(_SIMILE_STRUCTURE_PATTERN.findall(after_text or ""))
    return max(0, after_count - before_count)


# 通用修复 S-4：扩展后置自检——统计"像"字密度变化
# 根因：原 _count_new_simile_structures 只检测完整明喻结构"像...一样/似的/般"，
#   不检测单独"像"字。但 ai_simile_overuse 的 severity=high 是由"像"字密度 >= 5.0/千字
#   驱动的，单独"像"字（非完整明喻结构）也会推高密度。
#   且当修复目标是 ai_simile_overuse 时跳过自检，导致 LLM 引入新"像"字也无法被发现。
# 修复：新增 _count_new_simile_word 统计"像"字新增数；ai_simile_overuse 修复时
#   不再无脑跳过自检，而是检查"像"字密度是否下降。
# 通用性：所有密度类 AI 痕迹问题的后置自检都应检测修复方向是否正确。
_SIMILE_WORD = "像"


def _count_new_simile_word(before_text: str, after_text: str) -> int:
    """统计 after_text 中比 before_text 新增的'像'字数量。"""
    before_count = (before_text or "").count(_SIMILE_WORD)
    after_count = (after_text or "").count(_SIMILE_WORD)
    return max(0, after_count - before_count)


def _order_violation_types(order: ChapterRepairOrder) -> set[str]:
    """提取 order 关联的所有违规类型（小写）。"""
    types: set[str] = set()
    for v in (order.violation_details or []):
        if not isinstance(v, dict):
            continue
        for key in ("type", "violation_type", "semantic_type", "original_type"):
            val = v.get(key)
            if val:
                types.add(str(val).strip().lower())
    return types


class ChapterRepairExecutor:
    """整章修复执行器。

    按 ChapterRepairPlan.get_parallel_groups() 输出的分组，
    组内并行、组间串行地执行修复订单。
    同一场景上的多个订单会合并后串行执行，避免文本冲突。
    """

    def __init__(self) -> None:
        self._scene_repairer = None
        self._primitive_repair_traces: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Lazy agent accessors
    # ------------------------------------------------------------------

    @property
    def scene_repairer(self):
        """Return the merged scene repairer for repair, inline revision, and rewrite flows."""
        if self._scene_repairer is None:
            from app.agents.scene_repairer import SceneRepairer
            self._scene_repairer = SceneRepairer()
        return self._scene_repairer

    @staticmethod
    def _default_skill_contract_envelope() -> dict[str, Any]:
        """Contract hints every FBI prose repair must keep satisfied.

        Skills remain product contracts here. The FBI executor still owns
        diagnosis, ordering, and repair; this envelope only prevents repair
        agents from reintroducing known Skill-gate failures while fixing a
        different issue.
        """
        return {
            "role": "fbi_repair_skill_contract_envelope",
            "source": "chapter_repair_executor",
            "active_skills": ["anti_ai_prose"],
            "applies_to": "all_fbi_llm_text_repairs",
            "skill_contracts": [
                {
                    "skill_id": "anti_ai_prose",
                    "display_name": "anti_ai_prose",
                    "role": "style_quality_contract",
                    "not_repairer": True,
                    "constraints": {
                        "ai_flavor": {
                            "tier1_hit_count_max": 0,
                            "false_range_count_max": 0,
                            "overexplain_count_max": 0,
                            "dash_per_1000_max": DEFAULT_DASH_ARTIFACT_MAX_PER_1000,
                            "body_language_cliche_count_max": 0,
                            "trait_statement_count_max": 0,
                            "narrator_commentary_count_max": 0,
                        },
                        "ai_discourse": {
                            "paragraph_shape_repeat_count_max": 1,
                            "mirrored_paragraph_opening_count_max": 1,
                            "standalone_abstract_claims_max": 0,
                            "dense_paragraph_streak_max": 2,
                            "abrupt_shift_count_max": 0,
                        },
                    },
                    "repair_guidance": [
                        "While fixing the assigned issue, do not introduce generic explanatory narration, false ranges, over-clean summaries, body-language cliches, abstract trait statements, or dash overuse.",
                        "Prefer concrete in-scene evidence, character-specific voice, active verbs, local friction, and varied sentence/paragraph rhythm.",
                        "Do not add typos, random slang, or artificial perplexity tricks.",
                    ],
                }
            ],
        }

    @classmethod
    def _inject_skill_contract_envelope(
        cls,
        agent_context: dict[str, Any],
        order: ChapterRepairOrder,
        source_context: dict[str, Any] | None = None,
    ) -> None:
        envelope = cls._default_skill_contract_envelope()
        existing = agent_context.get("skill_contract_envelope")
        if not existing and isinstance(source_context, dict):
            existing = source_context.get("skill_contract_envelope")
        if existing:
            envelope["upstream_contract_envelope"] = existing
        envelope["repair_order"] = {
            "order_id": order.order_id,
            "repair_type": order.repair_type,
            "repair_domain": order.repair_domain,
            "repair_lane": order.repair_lane,
            "repair_strength": order.repair_strength,
        }
        agent_context["skill_contract_envelope"] = envelope

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    async def _raise_if_cancelled(context: dict[str, Any]) -> None:
        cancellation_check = context.get("cancellation_check")
        if not callable(cancellation_check):
            return
        cancelled = cancellation_check()
        if asyncio.iscoroutine(cancelled):
            cancelled = await cancelled
        if cancelled:
            raise asyncio.CancelledError("workflow cancelled during chapter repair")

    async def execute(
        self,
        plan: ChapterRepairPlan,
        scene_texts: dict[int, str],
        *,
        context: dict[str, Any] | None = None,
        case_file: dict[str, Any] | None = None,
    ) -> tuple[dict[int, str], ChapterRepairPlan]:
        """执行整章修复计划。

        Args:
            plan: 修复计划，包含订单列表和并行分组信息。
            scene_texts: 场景索引 -> 场景正文的映射。
            context: 额外上下文（scene_contract、chapter_state 等）。
            case_file: 完整案卷上下文，包含全章场景正文、合同、状态、
                审阅结果、修复计划等。修复师用此做知情修复。

        Returns:
            (updated_scene_texts, updated_plan) 二元组。
            updated_scene_texts 包含修复后的正文；
            updated_plan 中各订单的 status/result_text_hash 已更新。
        """
        context = dict(context or {})
        case_file = case_file or {}
        await self._raise_if_cancelled(context)

        if plan.is_clean():
            _logger.info("ChapterRepairExecutor.execute: plan is clean, nothing to do case_id=%s", plan.case_id)
            return dict(scene_texts), plan

        # 方案6 A1+A2：degraded 蓝图不直接执行修复。
        # LLM 失败或残缺的蓝图不应被当 ready 执行，否则执行的是空蓝图或残缺蓝图。
        # 先触发有界 LLM 重试。降级蓝图缺少可审计执行单元，不能为了节省时间
        # 直接落入 scene_repairer，否则空蓝图会被误报成一次真实修复尝试。
        if plan.revision_blueprint and plan.revision_blueprint.status == "degraded":
            max_llm_retries = 2
            _logger.warning(
                "ChapterRepairExecutor.execute: blueprint degraded, retrying up to %d times case_id=%s",
                max_llm_retries,
                plan.case_id,
            )
            retry_succeeded = False
            for attempt in range(max_llm_retries):
                retry_plan = await self._retry_degraded_blueprint(
                    plan, scene_texts, context, case_file, attempt,
                )
                if (
                    retry_plan is not None
                    and retry_plan.revision_blueprint
                    and retry_plan.revision_blueprint.status != "degraded"
                ):
                    plan = retry_plan
                    retry_succeeded = True
                    _logger.info(
                        "ChapterRepairExecutor.execute: degraded retry succeeded attempt=%d case_id=%s",
                        attempt + 1,
                        plan.case_id,
                    )
                    break
            if not retry_succeeded:
                _logger.warning(
                    "ChapterRepairExecutor.execute: degraded retry exhausted, skipping case_id=%s",
                    plan.case_id,
                )
                for order in plan.orders:
                    if order.status == "pending":
                        order.status = "skipped"
                        order.repair_audit = {
                            "reason": "blueprint_degraded_after_retries",
                            "detail": (
                                "revision_blueprint still degraded after "
                                f"{max_llm_retries} LLM retries"
                            ),
                            "retry_attempts": max_llm_retries,
                        }
                return dict(scene_texts), plan

        # 深拷贝 scene_texts，避免原地修改
        updated_texts = dict(scene_texts)
        original_scene_texts = dict(scene_texts)

        # 深拷贝 plan 的订单列表，逐个更新状态。依赖调度可能已经把不可执行订单标成 failed。
        orders_by_id: dict[str, ChapterRepairOrder] = {o.order_id: o.model_copy() for o in plan.orders}
        structured_blueprint_supplied = bool(
            plan.work_units
            or (plan.revision_blueprint and plan.revision_blueprint.work_units)
            or any(order.tool_commands for order in plan.orders)
            or any(
                isinstance((order.repair_brief or {}).get("patch_plan"), list)
                and bool((order.repair_brief or {}).get("patch_plan"))
                for order in plan.orders
            )
            or any(
                isinstance((order.repair_brief or {}).get("tool_commands"), list)
                and bool((order.repair_brief or {}).get("tool_commands"))
                for order in plan.orders
            )
            or any(
                order.repair_type == "scene_rewrite"
                and not order.violation_details
                and not order.repair_brief
                and not context.get("scene_contracts")
                and not context.get("scene_contract")
                for order in plan.orders
            )
        )
        context["_structured_execution_required"] = structured_blueprint_supplied
        work_units = self._ensure_work_units(plan, case_file, context)
        self._sync_work_unit_projection_to_orders(work_units, orders_by_id)
        blueprint_protocol_summary = self._apply_blueprint_protocol_gate(
            work_units,
            orders_by_id,
            updated_texts,
        )
        work_units = [
            unit for unit in work_units
            if unit.result_audit.get("reason") != "blueprint_not_compilable"
        ]
        # F4: 通用 covered issue 阻断 — compound window 已覆盖的 issue 不再单独执行。
        # 逻辑基于 issue_id 集合运算，metric 无关，与 work_unit_builder Phase U-D 互补。
        work_units, coverage_summary = self._apply_compound_window_coverage_gate(
            work_units,
            orders_by_id,
        )
        tool_execution_summary = await self._execute_tool_work_units(
            work_units,
            updated_texts,
            orders_by_id,
            context=context,
            case_file=case_file,
        )
        await self._raise_if_cancelled(context)
        tool_execution_summary["compound_window_coverage"] = coverage_summary
        # F4-B: compound work_unit 执行失败后，重新激活被 coverage_gate 跳过的 order。
        # 通用逻辑：不针对任何 metric，仅基于 issue_id 集合运算。
        # 当 compound work_unit 失败时，被它"覆盖"的 order 应该回到 pending，
        # 让 scene_repairer (LLM 创作兜底) 有机会修复，而不是静默跳过。
        reactivation_summary = self._reactivate_orders_on_compound_failure(
            work_units,
            orders_by_id,
        )
        if reactivation_summary["reactivated_count"] > 0:
            tool_execution_summary["compound_failure_reactivation"] = reactivation_summary
            _logger.info(
                "ChapterRepairExecutor.execute: reactivated %d orders after compound work_unit unverified case_id=%s",
                reactivation_summary["reactivated_count"], plan.case_id,
            )
        # 方案27 Part B：legacy direct-call 已删除；_disable_legacy_executor_orders 仅处理
        # manual_review（skipped）与未知 repair_type（failed）。
        # scene_repairer 可处理的 repair_type（local_patch/scene_rewrite 等）保持 pending，
        # 由后续并行分组循环处理——Part C 未完成，scene_repairer 仍是必要兜底。
        legacy_disabled_summary = self._disable_legacy_executor_orders(
            orders_by_id,
            work_units,
            updated_texts,
            enforce_structured_execution=structured_blueprint_supplied,
        )
        tool_execution_summary["legacy_executor_allowed"] = not structured_blueprint_supplied
        tool_execution_summary["legacy_disabled"] = legacy_disabled_summary
        plan.orders = list(orders_by_id.values())
        plan.work_units = work_units
        if plan.revision_blueprint:
            plan.revision_blueprint.work_units = work_units

        parallel_groups = self._get_execution_groups(plan)
        _logger.info(
            "ChapterRepairExecutor.execute: case_id=%s groups=%d total_orders=%d tool_units=%d",
            plan.case_id, len(parallel_groups), sum(len(g) for g in parallel_groups), len(work_units),
        )

        failed_runtime_order_ids = {
            order.order_id
            for order in orders_by_id.values()
            if self._order_finished_unsuccessfully(order)
        }

        for group_index, group in enumerate(parallel_groups):
            await self._raise_if_cancelled(context)
            _logger.info(
                "ChapterRepairExecutor.execute: group %d/%d orders=%d case_id=%s",
                group_index + 1, len(parallel_groups), len(group), plan.case_id,
            )

            # 将同组订单按 target_scenes 合并：同一场景上的订单串行执行
            executable_group: list[ChapterRepairOrder] = []
            for order in group:
                current_order = orders_by_id.get(order.order_id, order)
                if current_order.status in {"succeeded", "skipped", "failed"}:
                    continue
                failed_dependencies = sorted(set(order.dependencies or []) & failed_runtime_order_ids)
                if failed_dependencies:
                    text_hash = _compute_text_hash("")
                    current_order.status = "failed"
                    current_order.result_text_hash = text_hash
                    current_order.repair_audit = {
                        "accepted": False,
                        "reason": "failed_required_dependencies",
                        "failures": ["failed_required_dependencies"],
                        "failed_dependencies": failed_dependencies,
                        "changed": False,
                        "base_hash": text_hash,
                        "result_hash": text_hash,
                        "executor_path": "dependency_scheduler",
                    }
                    orders_by_id[current_order.order_id] = current_order
                    failed_runtime_order_ids.add(current_order.order_id)
                    continue
                executable_group.append(current_order)

            if not executable_group:
                continue

            scene_to_orders = self._group_orders_by_scene(executable_group)

            # 不同场景之间并行
            coros = []
            scene_indices_in_group: list[int] = []
            for scene_index, scene_orders in scene_to_orders.items():
                if scene_index not in updated_texts:
                    for order in scene_orders:
                        text_hash = _compute_text_hash("")
                        order.status = "failed"
                        order.result_text_hash = text_hash
                        order.repair_audit = {
                            "accepted": False,
                            "reason": "missing_target_scene",
                            "failures": ["missing_target_scene"],
                            "scene_index": scene_index,
                            "changed": False,
                            "base_hash": text_hash,
                            "result_hash": text_hash,
                            "executor_path": "scene_router",
                        }
                        orders_by_id[order.order_id] = order
                        failed_runtime_order_ids.add(order.order_id)
                    continue
                coros.append(
                    self._execute_orders_for_scene(
                        scene_index, scene_orders, updated_texts, context, case_file,
                        original_scene_texts,
                    )
                )
                scene_indices_in_group.append(scene_index)

            results = await asyncio.gather(*coros, return_exceptions=True)
            await self._raise_if_cancelled(context)

            # 收集结果
            for scene_index, result in zip(scene_indices_in_group, results):
                if isinstance(result, Exception):
                    _logger.exception(
                        "ChapterRepairExecutor.execute: scene %d failed case_id=%s: %s",
                        scene_index, plan.case_id, result,
                    )
                    # 该场景所有订单标记为 failed
                    for order in scene_to_orders[scene_index]:
                        order_ref = orders_by_id.get(order.order_id, order)
                        order_ref.status = "failed"
                        orders_by_id[order.order_id] = order_ref
                        failed_runtime_order_ids.add(order.order_id)
                    continue

                new_text, order_results = result
                updated_texts[scene_index] = new_text
                for order_id, status, text_hash, repair_audit in order_results:
                    order_ref = orders_by_id.get(order_id)
                    if order_ref is not None:
                        order_ref.status = status
                        order_ref.result_text_hash = text_hash
                        order_ref.repair_audit = repair_audit
                        orders_by_id[order_id] = order_ref
                        if self._order_finished_unsuccessfully(order_ref):
                            failed_runtime_order_ids.add(order_id)
            for order in group:
                if order.status == "failed":
                    orders_by_id[order.order_id] = order
                    failed_runtime_order_ids.add(order.order_id)

        self._reconcile_creative_work_units(work_units, orders_by_id)

        # 构建更新后的 plan
        round_finalization = self._finalize_repair_round(plan, original_scene_texts, updated_texts)
        self._reconcile_failed_orders_after_all_groups(orders_by_id, original_scene_texts, updated_texts)
        updated_orders = list(orders_by_id.values())
        execution_summary = self._repair_execution_summary(updated_orders)
        execution_summary["tool_execution"] = tool_execution_summary
        execution_summary["blueprint_protocol"] = blueprint_protocol_summary
        execution_summary["blueprint_coverage"] = self._blueprint_coverage_summary(updated_orders, work_units)
        execution_summary["round_finalization"] = round_finalization
        updated_plan = plan.model_copy(update={
            "orders": updated_orders,
            "work_units": work_units,
            "repair_execution_summary": execution_summary,
        })
        return updated_texts, updated_plan

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    # 方案27 Part B 修正：_dispatch_repair_type 可处理的 repair_type 集合。
    # 这些 order 保持 pending 以进入并行分组循环（scene_repairer LLM 修复兜底）。
    # 依据：方案27 Part C 未完成（build_revision_blueprint 保留为兜底），
    # 故 scene_repairer 仍是必要的修复路径，不应被 _disable_legacy_executor_orders 提前标记失败。
    _SCENE_REPAIRER_REPAIR_TYPES: frozenset[str] = frozenset({
        "local_patch",
        "contract_completion_patch",
        "scene_rewrite",
        "cross_scene_alignment",
        "contract_patch",
    })

    @staticmethod
    def _disable_legacy_executor_orders(
        orders_by_id: dict[str, ChapterRepairOrder],
        work_units: list[RepairWorkUnit],
        updated_texts: dict[int, str],
        *,
        enforce_structured_execution: bool = True,
    ) -> dict[str, Any]:
        tool_order_ids = {
            order_id
            for unit in work_units
            for order_id in (unit.source_order_ids or [])
        }
        # 附录4问题37修复：按 order_id 索引 work_units，便于区分
        # local_patch 订单中的 llm_creative_rewrite 与确定性 patch。
        units_by_order_id: dict[str, list[RepairWorkUnit]] = {}
        for unit in work_units:
            for oid in (unit.source_order_ids or []):
                units_by_order_id.setdefault(oid, []).append(unit)
        disabled: list[str] = []
        for order in orders_by_id.values():
            if order.status in {"succeeded", "skipped"}:
                continue
            if (
                order.status == "failed"
                and not str((order.repair_audit or {}).get("reason") or "").startswith(
                    "blueprint_not_compilable"
                )
            ):
                continue
            scene_index = order.owner_scene if order.owner_scene is not None else (
                order.target_scenes[0] if order.target_scenes else None
            )
            result_text = updated_texts.get(scene_index, "") if scene_index is not None else ""
            text_hash = _compute_text_hash(result_text)
            if order.repair_type == "manual_review":
                # 方案27 Part B 修正：manual_review 保持 pending，由并行分组循环的
                # _execute_orders_for_scene 处理（设置 executor_path="manual_review"）。
                # 这样 audit 字段与 scene_repairer 路径一致，避免双套语义。
                continue
            elif order.repair_type in ChapterRepairExecutor._SCENE_REPAIRER_REPAIR_TYPES:
                # Direct/legacy plans that did not declare a structured
                # blueprint remain valid executor inputs.  The fail-closed
                # boundary applies only after a caller supplied structured
                # work (or projected tool commands); otherwise this gate
                # would reject contract, deterministic and test plans before
                # their normal dispatcher can inspect dependencies/scope.
                if not enforce_structured_execution:
                    continue
                related_units = units_by_order_id.get(order.order_id, [])
                has_llm_creative = any(
                    getattr(cmd, "operation", "") == "llm_creative_rewrite"
                    for u in related_units
                    for cmd in (u.tool_batch.commands or [])
                )
                if has_llm_creative:
                    order.repair_audit = {
                        **(order.repair_audit or {}),
                        "executor_path": "scene_repairer_llm_creative",
                        "disposition": "llm_creative_rewrite_pending",
                    }
                    continue

                # 非显式 llm_creative_rewrite 订单必须由结构化 work unit
                # 执行。工具未生成或执行/后检失败时，禁止静默转入
                # SceneRepairer 扩大写入范围；应保留原文并暴露蓝图失败。
                order.status = "failed"
                order.result_text_hash = text_hash
                had_tool_work_unit = order.order_id in tool_order_ids
                prior_tool_failures = list((order.repair_audit or {}).get("tool_failures") or [])
                # T11.7: 区分"真没 work_unit"和"work_unit 执行了但 postcheck 失败"
                if had_tool_work_unit and prior_tool_failures:
                    failure_stage = "tool_postcheck_failed"
                    manual_reason = "确定性工具执行了但未通过验收检查，需人工修复"
                else:
                    failure_stage = "blueprint_not_generated"
                    manual_reason = "审查蓝图官未生成可执行补丁，需人工撰写或补全蓝图"
                order.repair_audit = {
                    **(order.repair_audit or {}),
                    "accepted": False,
                    "reason": "needs_blueprint_completion",
                    "failures": ["needs_blueprint_completion"],
                    "changed": False,
                    "result_hash": text_hash,
                    "executor_path": "legacy_executor_disabled",
                    "had_tool_work_unit": had_tool_work_unit,
                    "disposition": "requires_review_minister_blueprint_completion",
                    # Phase U-G: 前端展示字段
                    "failure_stage": failure_stage,
                    "manual_reason": manual_reason,
                }
            else:
                order.status = "failed"
                order.result_text_hash = text_hash
                order.repair_audit = {
                    **(order.repair_audit or {}),
                    "accepted": False,
                    "reason": "needs_blueprint_completion",
                    "failures": ["needs_blueprint_completion"],
                    "changed": False,
                    "result_hash": text_hash,
                    "executor_path": "legacy_executor_disabled",
                    "had_tool_work_unit": order.order_id in tool_order_ids,
                    "disposition": "requires_review_minister_blueprint_completion",
                    "failure_stage": "blueprint_not_generated",
                    "manual_reason": "审查蓝图官未生成可执行补丁，需人工撰写或补全蓝图",
                }
            disabled.append(order.order_id)
        return {
            "disabled_order_count": len(disabled),
            "disabled_order_ids": disabled,
        }

    @staticmethod
    def _ensure_work_units(
        plan: ChapterRepairPlan,
        case_file: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> list[RepairWorkUnit]:
        """获取 work_units，优先使用 LLM 蓝图，兜底走确定性构建器。

        优先级：
        ① plan.work_units（L7 handler 透传的 LLM patch）
        ② plan.revision_blueprint.work_units（同上，备用字段）
        ③ case_file["repair_plan"].revision_blueprint.work_units（兜底：handler 未透传时）
        ④ build_revision_blueprint(plan.orders)（最终回退：确定性构建器）
        """
        work_units = list(plan.work_units or [])
        if not work_units and plan.revision_blueprint and plan.revision_blueprint.work_units:
            work_units = list(plan.revision_blueprint.work_units)
        # §3.3 G2/G3: 兜底从 case_file["repair_plan"].revision_blueprint.work_units 读取
        # 这用于 L7 handler 未透传的调用路径，按 scene_index 过滤避免跨 scene 误传
        if not work_units and case_file:
            repair_plan = case_file.get("repair_plan")
            if repair_plan is not None:
                rb = getattr(repair_plan, "revision_blueprint", None)
                if rb is not None and rb.work_units:
                    scene_idx = context.get("scene_index") if context else None
                    if scene_idx is not None:
                        filtered = [wu for wu in rb.work_units if wu.owner_scene == scene_idx]
                        work_units = filtered if filtered else list(rb.work_units)
                    else:
                        work_units = list(rb.work_units)
                    plan.revision_blueprint = rb
        if not work_units:
            blueprint = build_revision_blueprint(plan.case_id, plan.orders)
            plan.revision_blueprint = blueprint
            work_units = list(blueprint.work_units)
        return work_units

    @staticmethod
    def _sync_work_unit_projection_to_orders(
        work_units: list[RepairWorkUnit],
        orders_by_id: dict[str, ChapterRepairOrder],
    ) -> None:
        for unit in work_units:
            command_dump = [command.model_dump() for command in unit.tool_batch.commands]
            for order_id in unit.source_order_ids:
                order = orders_by_id.get(order_id)
                if order is None:
                    continue
                order.work_unit_id = unit.work_unit_id
                order.tool_commands = command_dump

    @staticmethod
    def _apply_blueprint_protocol_gate(
        work_units: list[RepairWorkUnit],
        orders_by_id: dict[str, ChapterRepairOrder],
        updated_texts: dict[int, str],
    ) -> dict[str, Any]:
        result = BlueprintProtocolValidator().validate_work_units(work_units)
        invalid_ids = set(result.get("invalid_work_unit_ids") or [])
        if not invalid_ids:
            return result

        unit_results = {
            item.get("work_unit_id"): item
            for item in result.get("unit_results") or []
            if isinstance(item, dict)
        }
        for unit in work_units:
            if unit.work_unit_id not in invalid_ids:
                continue
            unit_failure = unit_results.get(unit.work_unit_id, {})
            # 问题29修复：llm_creative_rewrite 工单需要走 LLM 创作路径（SceneRepairer），
            # 不应被 blueprint_protocol_gate 标记为 failed。
            # 附录4问题15修复：has_llm_creative_fallback 是 unit 级属性（基于 commands），
            # 提前计算并据此决定 unit.status，避免 unit 标 failed 而 order 保持 pending 的语义割裂。
            has_llm_creative_fallback = any(
                getattr(cmd, "operation", "") == "llm_creative_rewrite"
                for cmd in (unit.tool_batch.commands or [])
            )
            if has_llm_creative_fallback:
                # 保持 unit pending，让 _execute_orders_for_scene 走 LLM 创作兜底路径
                unit.status = "pending"
                unit.result_audit = {
                    "accepted": False,
                    "changed": False,
                    "reason": "blueprint_not_compilable_fallback_to_llm_creative",
                    "failures": unit_failure.get("failures") or ["blueprint_not_compilable"],
                    "protocol_validation": unit_failure,
                    "executor_path": "blueprint_protocol_validator",
                    "disposition": "fallback_to_llm_creative_rewrite",
                }
            else:
                unit.status = "failed"
                unit.result_audit = {
                    "accepted": False,
                    "changed": False,
                    "reason": "blueprint_not_compilable",
                    "failures": unit_failure.get("failures") or ["blueprint_not_compilable"],
                    "protocol_validation": unit_failure,
                    "executor_path": "blueprint_protocol_validator",
                }
            for order_id in unit.source_order_ids:
                order = orders_by_id.get(order_id)
                if order is None or order.status in {"succeeded", "skipped", "failed"}:
                    continue
                scene_index = order.owner_scene if order.owner_scene is not None else (
                    order.target_scenes[0] if order.target_scenes else None
                )
                result_text = updated_texts.get(scene_index, "") if scene_index is not None else ""
                # 方案22修复：对有确定性兜底修复的违规类型（如 chapter_reference），
                # blueprint_protocol_gate 失败时不直接标记 failed，
                # 而是保持 pending 让其进入 _execute_orders_for_scene 的 _deterministic_local_patch 兜底。
                # 否则 LLM 蓝图官生成的 delete_exact old_text="第17章" 会因 insufficient_precise_locator 失败，
                # 而 _patch_chapter_references 的正则兜底永远没机会执行。
                order_violation_types = set()
                for v in (order.violation_details or []):
                    if isinstance(v, dict):
                        order_violation_types.add(str(v.get("type") or v.get("violation_type") or "").lower())
                has_deterministic_fallback = bool(order_violation_types & _CHAPTER_REFERENCE_TYPES) or bool(
                    order_violation_types & {"clue_provenance_error", "clue_provenance_error_proposition", "unprovenanced_clue"}
                )
                # 通用修复：当 blueprint_protocol_validator 报 missing_target_scene 时，
                # work_unit 缺少场景信息是蓝图官的错误（LLM 未正确指定 scene_index），
                # 不应阻止修复执行。保持 pending 让 scene_repairer LLM 创作修复兜底。
                # order 本身有 owner_scene/target_scenes，scene_repairer 可以从中获取场景信息。
                has_missing_target_scene = "missing_target_scene" in (
                    unit_failure.get("failures") or []
                )
                if has_deterministic_fallback or has_llm_creative_fallback or has_missing_target_scene:
                    # 保持 pending，让 _execute_orders_for_scene 走兜底路径
                    # （确定性兜底 或 LLM 创作兜底）
                    if has_deterministic_fallback:
                        fallback_reason = "blueprint_not_compilable_fallback_to_deterministic"
                        fallback_disposition = "fallback_to_deterministic_local_patch"
                    else:
                        fallback_reason = "blueprint_not_compilable_fallback_to_llm_creative"
                        fallback_disposition = "fallback_to_llm_creative_rewrite"
                    order.repair_audit = {
                        "accepted": False,
                        "changed": False,
                        "reason": fallback_reason,
                        "failures": unit_failure.get("failures") or ["blueprint_not_compilable"],
                        "protocol_validation": unit_failure,
                        "executor_path": "blueprint_protocol_validator",
                        "disposition": fallback_disposition,
                    }
                else:
                    order.status = "failed"
                    order.result_text_hash = _compute_text_hash(result_text)
                    order.repair_audit = {
                        "accepted": False,
                        "changed": False,
                        "reason": "blueprint_not_compilable",
                        "failures": unit_failure.get("failures") or ["blueprint_not_compilable"],
                        "protocol_validation": unit_failure,
                        "executor_path": "blueprint_protocol_validator",
                        "disposition": "requires_blueprint_agent_completion",
                    }
                orders_by_id[order_id] = order
        return result

    @staticmethod
    def _apply_compound_window_coverage_gate(
        work_units: list[RepairWorkUnit],
        orders_by_id: dict[str, ChapterRepairOrder],
    ) -> tuple[list[RepairWorkUnit], dict[str, Any]]:
        """F4: 通用 covered issue 阻断 — compound window 已覆盖的 issue 不再单独执行。

        逻辑完全基于 issue_id 集合运算，不读取任何 metric 名称 / family / repair_type，
        对所有 metric 通用。与 work_unit_builder.py 的 Phase U-D 防护互补：
        Phase U-D 在蓝图构建阶段阻断，这里在执行阶段阻断（compatibility 路径 / 现场构建的 work_unit）。

        流程：
        1. 从 compound window 的 work_unit 中收集 covered_issue_ids
        2. 过滤掉非 compound 的 work_unit（如果它的 source_violation_ids 全部被覆盖）
        3. 跳过对应的 order（避免 legacy 路径重复执行）
        """
        # 1. 收集 compound window 覆盖的 issue_ids
        covered_issue_ids: set[str] = set()
        compound_order_ids: set[str] = set()
        for unit in work_units:
            is_compound = bool(unit.edit_window.get("compound")) or len(unit.source_violation_ids) > 1
            if is_compound:
                # 通用修复 S-2：blueprint incomplete 的 compound work_unit 不应"覆盖" issue。
                # 如果 compound work_unit 的蓝图不完整（protocol_gate 标记为
                # blueprint_not_compilable 或 blueprint_not_compilable_fallback_to_llm_creative），
                # 它无法可靠修复 issue，不应阻止 individual order 的执行。
                # 否则 individual order 被 skip 后，compound work_unit 又没真正修复 issue，
                # 导致 issue 被静默丢弃（如 fact_conflict + ai_simile_overuse 同窗时，
                # window_contains_llm_issue 导致 compound blueprint incomplete）。
                audit_reason = (unit.result_audit or {}).get("reason", "")
                if audit_reason.startswith("blueprint_not_compilable"):
                    continue
                covered_issue_ids.update(unit.source_violation_ids or [])
                compound_order_ids.update(unit.source_order_ids or [])

        if not covered_issue_ids:
            return work_units, {
                "covered_issue_count": 0,
                "skipped_work_unit_count": 0,
                "skipped_order_count": 0,
            }

        # 2. 过滤掉非 compound 的 work_unit（如果它的 source_violation_ids 全部被覆盖）
        skipped_work_unit_ids: set[str] = set()
        filtered_work_units: list[RepairWorkUnit] = []
        for unit in work_units:
            is_compound = bool(unit.edit_window.get("compound")) or len(unit.source_violation_ids) > 1
            if is_compound:
                filtered_work_units.append(unit)
                continue
            unit_issues = set(unit.source_violation_ids or [])
            if unit_issues and unit_issues <= covered_issue_ids:
                unit.status = "skipped"
                unit.result_audit = {
                    "accepted": True,
                    "changed": False,
                    "reason": "covered_by_compound_window",
                    "covered_issue_ids": sorted(unit_issues),
                    "executor_path": "compound_window_coverage_gate",
                }
                skipped_work_unit_ids.add(unit.work_unit_id)
            else:
                filtered_work_units.append(unit)

        # 3. 跳过对应的 order（避免 legacy 路径重复执行）
        skipped_order_count = 0
        for order in orders_by_id.values():
            if order.status in {"succeeded", "skipped", "failed"}:
                continue
            # A canonical order is the control-plane projection of the
            # compound work unit itself.  Only superseded member projections
            # may be skipped; skipping the canonical owner would bypass both
            # creative execution and postcheck status propagation.
            if order.order_id in compound_order_ids:
                continue
            order_issues = set(order.source_violation_ids or [order.order_id])
            if order_issues and order_issues <= covered_issue_ids:
                order.status = "skipped"
                order.repair_audit = {
                    "accepted": True,
                    "changed": False,
                    "reason": "covered_by_compound_window",
                    "covered_issue_ids": sorted(order_issues),
                    "executor_path": "compound_window_coverage_gate",
                }
                skipped_order_count += 1

        summary = {
            "covered_issue_count": len(covered_issue_ids),
            "skipped_work_unit_count": len(skipped_work_unit_ids),
            "skipped_order_count": skipped_order_count,
            "covered_issue_ids": sorted(covered_issue_ids),
        }
        return filtered_work_units, summary

    @staticmethod
    def _reactivate_orders_on_compound_failure(
        work_units: list[RepairWorkUnit],
        orders_by_id: dict[str, ChapterRepairOrder],
    ) -> dict[str, Any]:
        """F4-B: compound work_unit 执行后，重新激活被 coverage_gate 跳过但未验证修复的 order。

        通用兜底逻辑（不针对任何 metric）：
        1. 收集"未验证修复"的 compound work_unit 的 source_violation_ids
           未验证修复 = work_unit 未成功（failed/pending）或 source_order_ids 为空（后检被跳过）
        2. 找到被 coverage_gate 跳过（reason="covered_by_compound_window"）的 order
        3. 如果 order 的 source_violation_ids 与未验证 compound 的 issue_ids 有交集，
           把 order 状态从 skipped 恢复为 pending，让 scene_repairer 有机会修复。

        这确保 compound work_unit 失败或跳过后检不会导致 issue 被静默丢弃，
        而是回退到逐 order 的 scene_repairer LLM 创作修复路径。
        scene_repairer 的 recheck 会判断 issue 是否已修复，避免重复修复。
        """
        # 1. 收集"未验证修复"的 compound work_unit 覆盖的 issue_ids
        # 条件 A: work_unit 未成功（failed/pending）→ patches 失败或未生成
        # 条件 B: source_order_ids 为空 → 后检（postcheck）被跳过，无法验证 issue 是否已修复
        # 两种情况都意味着 compound work_unit 没有可验证地修复 issue。
        unverified_compound_issue_ids: set[str] = set()
        unverified_compound_unit_ids: list[str] = []
        for unit in work_units:
            is_compound = bool(unit.edit_window.get("compound")) or len(unit.source_violation_ids) > 1
            if not is_compound:
                continue
            # 条件 A: 未成功
            if unit.status != "succeeded":
                unverified_compound_issue_ids.update(unit.source_violation_ids or [])
                unverified_compound_unit_ids.append(unit.work_unit_id)
                continue
            # 条件 B: 成功但 source_order_ids 为空（后检被跳过）
            if not unit.source_order_ids:
                unverified_compound_issue_ids.update(unit.source_violation_ids or [])
                unverified_compound_unit_ids.append(unit.work_unit_id)

        if not unverified_compound_issue_ids:
            return {"reactivated_count": 0, "unverified_compound_unit_ids": [], "reactivated_order_ids": []}

        # 2. 重新激活被 coverage_gate 跳过且 issue 被未验证 compound 覆盖的 order
        reactivated_order_ids: list[str] = []
        for order in orders_by_id.values():
            if order.status != "skipped":
                continue
            audit = order.repair_audit or {}
            if audit.get("reason") != "covered_by_compound_window":
                continue
            order_issues = set(order.source_violation_ids or [order.order_id])
            if order_issues & unverified_compound_issue_ids:
                order.status = "pending"
                order.repair_audit = {
                    "reason": "reactivated_after_compound_unverified",
                    "previous_reason": "covered_by_compound_window",
                    "unverified_compound_unit_ids": unverified_compound_unit_ids,
                    "covered_issue_ids": sorted(order_issues),
                    "executor_path": "compound_failure_reactivation",
                }
                reactivated_order_ids.append(order.order_id)

        return {
            "reactivated_count": len(reactivated_order_ids),
            "unverified_compound_unit_ids": unverified_compound_unit_ids,
            "reactivated_order_ids": reactivated_order_ids,
            "unverified_compound_issue_ids": sorted(unverified_compound_issue_ids),
        }

    async def _execute_tool_work_units(
        self,
        work_units: list[RepairWorkUnit],
        updated_texts: dict[int, str],
        orders_by_id: dict[str, ChapterRepairOrder],
        *,
        context: dict[str, Any],
        case_file: dict[str, Any],
    ) -> dict[str, Any]:
        if not work_units:
            return {
                "attempted": False,
                "work_units": 0,
                "accepted": 0,
                "failed": 0,
                "changed_scenes": [],
            }

        creative_units = [
            unit
            for unit in work_units
            if any(
                getattr(command, "operation", "") == "llm_creative_rewrite"
                for command in (unit.tool_batch.commands or [])
            )
        ]
        creative_ids = {unit.work_unit_id for unit in creative_units}
        deterministic_units = [
            unit for unit in work_units if unit.work_unit_id not in creative_ids
        ]
        for unit in creative_units:
            unit.status = "pending"
            unit.result_audit = {
                "accepted": False,
                "changed": False,
                "reason": "creative_work_unit_pending",
                "disposition": "route_to_creative_work_unit_executor",
                "executor_path": "creative_work_unit_router",
                "source_order_ids": list(unit.source_order_ids or []),
                "source_goal_ids": list(unit.source_goal_ids or []),
            }

        if deterministic_units:
            summary = await self._execute_frozen_patch_work_units(
                deterministic_units,
                updated_texts,
                orders_by_id,
                context=context,
                case_file=case_file,
            )
        else:
            summary = {
                "attempted": bool(creative_units),
                "work_units": 0,
                "accepted": 0,
                "failed": 0,
                "changed_scenes": [],
                "audits": {},
                "execution_layers": [],
            }
        summary["work_units"] = len(work_units)
        summary["creative_work_units"] = len(creative_units)
        summary["creative_work_unit_ids"] = [
            unit.work_unit_id for unit in creative_units
        ]
        summary.setdefault("audits", {}).update({
            unit.work_unit_id: unit.result_audit for unit in creative_units
        })
        if creative_units:
            summary.setdefault("execution_layers", []).append(
                [unit.work_unit_id for unit in creative_units]
            )
        return summary

    @staticmethod
    def _reconcile_creative_work_units(
        work_units: list[RepairWorkUnit],
        orders_by_id: dict[str, ChapterRepairOrder],
    ) -> None:
        """Project the single canonical creative-order result back to its unit."""
        for unit in work_units:
            if not any(
                getattr(command, "operation", "") == "llm_creative_rewrite"
                for command in (unit.tool_batch.commands or [])
            ):
                continue
            source_orders = [
                orders_by_id[order_id]
                for order_id in (unit.source_order_ids or [])
                if order_id in orders_by_id
            ]
            if not source_orders:
                continue
            accepted = all(
                order.status in {"succeeded", "skipped"}
                and (order.repair_audit or {}).get("accepted") is True
                for order in source_orders
            )
            unit.status = "succeeded" if accepted else (
                "failed"
                if all(order.status in {"failed", "skipped"} for order in source_orders)
                else "pending"
            )
            unit.result_audit = {
                "accepted": accepted,
                "changed": any(
                    bool((order.repair_audit or {}).get("changed"))
                    for order in source_orders
                ),
                "reason": (
                    "creative_work_unit_accepted"
                    if accepted
                    else "creative_work_unit_not_accepted"
                ),
                "executor_path": "creative_work_unit_executor",
                "order_results": [
                    {
                        "order_id": order.order_id,
                        "status": order.status,
                        "audit": order.repair_audit,
                    }
                    for order in source_orders
                ],
            }

    async def _execute_frozen_patch_work_units(
        self,
        work_units: list[RepairWorkUnit],
        updated_texts: dict[int, str],
        orders_by_id: dict[str, ChapterRepairOrder],
        *,
        context: dict[str, Any],
        case_file: dict[str, Any],
    ) -> dict[str, Any]:
        base_texts = dict(updated_texts)
        executor = ToolExecutor()
        known_unit_ids = {unit.work_unit_id for unit in work_units}
        dependency_blocked: dict[str, dict[str, Any]] = {}
        executable_units: list[RepairWorkUnit] = []
        for unit in work_units:
            dependencies = set(unit.dependencies or [])
            missing = sorted(dependencies - known_unit_ids)
            if missing:
                dependency_blocked[unit.work_unit_id] = {
                    "accepted": False,
                    "changed": False,
                    "reason": "missing_required_dependencies",
                    "failures": ["missing_required_dependencies"],
                    "missing_dependencies": missing,
                    "executor_path": "fbi_frozen_patch_dependency_scheduler",
                }
                continue
            executable_units.append(unit)
        patches, audits = executor.execute_work_units_as_patches(
            executable_units,
            base_texts,
            case_id=str(context.get("case_id") or case_file.get("case_id") or ""),
        )
        audits.update(dependency_blocked)
        patches_by_scene: dict[int, list[Any]] = {}
        patch_to_unit: dict[str, str] = {}
        for unit in work_units:
            for patch_id in (audits.get(unit.work_unit_id, {}) or {}).get("patch_ids") or []:
                patch_to_unit[patch_id] = unit.work_unit_id
        for patch in patches:
            scene_index = patch.self_audit.get("scene_index")
            if isinstance(scene_index, int):
                patches_by_scene.setdefault(scene_index, []).append(patch)

        merger = PatchMerger()
        applier = PatchApplier()
        merge_summary: dict[int, dict[str, Any]] = {}
        applied_patch_ids: set[str] = set()
        applied_patch_ids_by_scene: dict[int, set[str]] = {}
        conflicted_patch_ids: set[str] = set()
        # 9.3.2 fix: 回流补丁成功应用后，原始冲突 patch 也应视为已解决，
        # 否则原 work_unit 会被误判为 failed（"回流补丁改了正文但原订单仍判失败"）。
        reflow_resolved_patch_ids: set[str] = set()
        changed_scenes: set[int] = set()

        for scene_index, scene_patches in patches_by_scene.items():
            base_text = base_texts.get(scene_index, "")
            merge_result = merger.merge(scene_patches, base_text)
            merge_summary[scene_index] = {
                "patches": len(scene_patches),
                "merged": len(merge_result.get("merged_patches") or []),
                "conflicts": merge_result.get("conflicts") or [],
                "conflict_groups": merge_result.get("conflict_groups") or [],
            }
            for group in merge_result.get("conflict_groups") or []:
                for patch_id in group:
                    conflicted_patch_ids.add(patch_id)
            merged_patches = list(merge_result.get("merged_patches") or [])

            # 9.3.2: 冲突回流——不可仲裁的冲突回流到 EditWindowPlanner 做 compound patch
            compound_reflow = merge_result.get("compound_reflow") or []
            reflow_applied_patch_ids: set[str] = set()
            if compound_reflow:
                reflow_issue_ids: set[str] = set()
                for reflow in compound_reflow:
                    reflow_issue_ids.update(reflow.get("resolves_issue_ids") or [])
                # 记录被回流的原始 patch IDs（这些 patch 因冲突未能直接应用，
                # 但如果回流补丁成功，它们对应的 issue 应视为已解决）
                reflowed_original_patch_ids: set[str] = set()
                for patch in scene_patches:
                    patch_issue_ids = set(getattr(patch, "resolves_issue_ids", None) or [])
                    if patch_issue_ids & reflow_issue_ids:
                        reflowed_original_patch_ids.add(patch.patch_id)
                if reflow_issue_ids:
                    reflow_units = self._build_reflow_work_units(
                        reflow_issue_ids,
                        work_units,
                        scene_index,
                        base_text,
                        case_id=str(context.get("case_id") or case_file.get("case_id") or ""),
                    )
                    if reflow_units:
                        reflow_patches, reflow_audits = executor.execute_work_units_as_patches(
                            reflow_units,
                            {scene_index: base_text},
                            case_id=str(context.get("case_id") or case_file.get("case_id") or ""),
                        )
                        # 回流只执行一次（不递归），重新合并回流 patches
                        reflow_merge = merger.merge(reflow_patches, base_text)
                        reflow_merged_patches = list(reflow_merge.get("merged_patches") or [])
                        merged_patches.extend(reflow_merged_patches)
                        audits.update(reflow_audits)
                        merge_summary[scene_index]["reflow"] = {
                            "input_issues": len(reflow_issue_ids),
                            "reflow_units": len(reflow_units),
                            "reflow_patches": len(reflow_patches),
                            "reflow_merged": len(reflow_merged_patches),
                            "reflow_conflicts": len(reflow_merge.get("conflict_groups") or []),
                            "reflowed_original_patch_ids": sorted(reflowed_original_patch_ids),
                        }
                        # 回流后仍有冲突的 patch 标记为 needs_human_review
                        for reflow_group in reflow_merge.get("conflict_groups") or []:
                            for patch_id in reflow_group:
                                conflicted_patch_ids.add(patch_id)
                        # 记录回流补丁的 patch_id，用于后续判断回流是否成功
                        reflow_applied_patch_ids = {
                            p.patch_id for p in reflow_merged_patches
                        }

            if not merged_patches:
                continue
            apply_result = await applier.apply_patches(
                base_text,
                merged_patches,
                merged_patches[0].base_text_hash,
            )
            merge_summary[scene_index]["apply_result"] = {
                key: value for key, value in apply_result.items()
                if key not in {"new_text"}
            }
            if apply_result.get("success"):
                updated_texts[scene_index] = apply_result.get("new_text", base_text)
                changed_scenes.add(scene_index)
                for patch in merged_patches:
                    applied_patch_ids.add(patch.patch_id)
                    applied_patch_ids_by_scene.setdefault(scene_index, set()).add(
                        patch.patch_id
                    )
                # 9.3.2 fix: 如果回流补丁成功应用，把被回流的原始 patch IDs
                # 也标记为已解决，避免原 work_unit 被误判为 failed
                if compound_reflow and reflowed_original_patch_ids:
                    applied_reflow_ids = {
                        p.patch_id for p in merged_patches
                        if p.patch_id in reflow_applied_patch_ids
                    }
                    if applied_reflow_ids:
                        reflow_resolved_patch_ids.update(reflowed_original_patch_ids)
            else:
                for patch in merged_patches:
                    conflicted_patch_ids.add(patch.patch_id)

        for unit in work_units:
            audit = audits.get(unit.work_unit_id, {})
            unit_patch_ids = set(audit.get("patch_ids") or [])
            if not audit.get("accepted"):
                unit.status = "failed"
                unit.result_audit = audit
                continue
            # 9.3.2 fix: 回流成功的原始 patch 视为已应用
            effective_applied = applied_patch_ids | reflow_resolved_patch_ids
            failed_patch_ids = sorted(unit_patch_ids - effective_applied)
            if failed_patch_ids:
                reason = "patch_merge_or_apply_failed"
                if unit_patch_ids & conflicted_patch_ids:
                    reason = "patch_span_conflict"
                audit = {
                    **audit,
                    "accepted": False,
                    "changed": False,
                    "reason": reason,
                    "failures": [reason],
                    "failed_patch_ids": failed_patch_ids,
                    "merge_summary": merge_summary,
                    "executor_path": "fbi_frozen_patch_merge",
                }
                unit.status = "failed"
                unit.result_audit = audit
                continue

            batch_checks: list[dict[str, Any]] = []
            for order_id in unit.source_order_ids:
                order = orders_by_id.get(order_id)
                if order is None:
                    continue
                scene_index = order.owner_scene if order.owner_scene is not None else (
                    order.target_scenes[0] if order.target_scenes else None
                )
                if scene_index is None:
                    continue
                check_context = dict(context)
                if unit.validator_snapshot:
                    check_context["validator_snapshot"] = unit.validator_snapshot
                order_audit = await self._audit_repair_result_async(
                    order,
                    base_texts.get(scene_index, ""),
                    updated_texts.get(scene_index, ""),
                    scene_index,
                    check_context,
                    case_file,
                )
                # Phase U-F: 联合验收——把 covered issue_ids 和 window_id 写入 trace，
                # 让失败原因能归因到具体 covered issue 或 compound patch。
                batch_checks.append({
                    "order_id": order_id,
                    "covered_issue_ids": list(order.source_violation_ids or []),
                    "window_id": unit.edit_window_id or unit.edit_window.get("window_id", ""),
                    **order_audit,
                })
            if not batch_checks:
                audit = {
                    **audit,
                    "accepted": False,
                    "changed": False,
                    "reason": "missing_source_order_mapping",
                    "failures": ["missing_source_order_mapping"],
                    "batch_postcheck": [],
                    "merge_summary": merge_summary,
                    "executor_path": "fbi_frozen_patch_postcheck",
                }
                unit.status = "failed"
                unit.result_audit = audit
                continue
            failed_checks = [check for check in batch_checks if not check.get("accepted")]
            if failed_checks:
                # U-F: 归因到具体 covered issue
                failed_goal_ids = sorted({
                    str(goal_id)
                    for check in failed_checks
                    for goal_id in (check.get("failed_goal_ids") or [])
                    if goal_id
                })
                failed_issue_ids: set[str] = set()
                for check in failed_checks:
                    check_failed_goals = set(check.get("failed_goal_ids") or [])
                    brief = check.get("repair_brief") or {}
                    goals = brief.get("repair_goals") if isinstance(brief, dict) else []
                    for goal in goals or []:
                        if not isinstance(goal, dict):
                            continue
                        if check_failed_goals and goal.get("goal_id") not in check_failed_goals:
                            continue
                        failed_issue_ids.update(
                            str(issue_id)
                            for issue_id in (goal.get("source_issue_ids") or [])
                            if issue_id
                        )
                    if not check_failed_goals:
                        failed_issue_ids.update(check.get("covered_issue_ids") or [])
                if not failed_issue_ids:
                    failed_issue_ids.update(
                        issue_id
                        for check in failed_checks
                        for issue_id in (check.get("covered_issue_ids") or [])
                    )
                audit = {
                    **audit,
                    "accepted": False,
                    "changed": False,
                    "reason": "batch_protection_or_target_check_failed",
                    "failures": sorted({
                        failure
                        for check in failed_checks
                        for failure in (check.get("failures") or ["batch_postcheck_failed"])
                    }),
                    "batch_postcheck": batch_checks,
                    # U-F: compound patch 失败时归因到具体 covered issue
                    "failed_covered_issue_ids": sorted(failed_issue_ids),
                    "failed_covered_goal_ids": failed_goal_ids,
                    "compound_window_id": unit.edit_window_id or unit.edit_window.get("window_id", ""),
                    "merge_summary": merge_summary,
                    "executor_path": "fbi_frozen_patch_postcheck",
                }
                unit.status = "failed"
                unit.result_audit = audit
                continue

            # 方案6 B2：patches=0 时不标 accepted=True，保持 pending 等 recheck 验证。
            # 没有修复被当成修复成功是错误的，recheck 发现 issue 仍存在则触发循环重试。
            has_patches = bool(unit_patch_ids)
            audit = {
                **audit,
                "accepted": has_patches,
                "changed": has_patches,
                "batch_postcheck": batch_checks,
                "merge_summary": merge_summary,
                "candidate_generation": {
                    "base": "layer_base_revision",
                    "frozen_base": True,
                    "mode": "patch_dry_run",
                    "merge": "centralized_replay",
                    "patch_merge": "patch_merger",
                    "apply": "patch_applier",
                },
                "executor_path": "fbi_tool_work_unit",
            }
            if has_patches:
                unit.status = "succeeded"
            else:
                # patches=0：不假装成功，保持 pending 等 recheck 决定
                unit.status = "pending"
                audit["reason"] = "no_patches_pending_recheck"
            unit.result_audit = audit

        # Postcheck failure is isolated to the smallest unsafe work-unit group.
        # A rejected patch must not remain in the candidate, but independent
        # peer patches must not be discarded with it either.  Rebuild each
        # affected scene from the frozen base using only independent accepted
        # units, then run their postchecks again against the rebuilt candidate.
        failed_applied_scenes: set[int] = set()
        patches_by_id = {patch.patch_id: patch for patch in patches}
        effective_applied_patch_ids = applied_patch_ids | reflow_resolved_patch_ids
        for unit in work_units:
            audit = unit.result_audit or audits.get(unit.work_unit_id, {})
            unit_applied_ids = (
                set(audit.get("patch_ids") or [])
                & effective_applied_patch_ids
            )
            if audit.get("accepted") or not unit_applied_ids:
                continue
            for patch_id in unit_applied_ids:
                patch = patches_by_id.get(patch_id)
                scene_index = (patch.self_audit or {}).get("scene_index") if patch else None
                if isinstance(scene_index, int):
                    failed_applied_scenes.add(scene_index)

        postcheck_isolation_summary: dict[int, dict[str, Any]] = {}
        for scene_index in failed_applied_scenes:
            scene_patch_ids = {
                patch.patch_id
                for patch in patches
                if (patch.self_audit or {}).get("scene_index") == scene_index
                and patch.patch_id in effective_applied_patch_ids
            }
            seed_failed_unit_ids = {
                unit.work_unit_id
                for unit in work_units
                if not (unit.result_audit or {}).get("accepted")
                and bool(
                    set((unit.result_audit or {}).get("patch_ids") or [])
                    & scene_patch_ids
                )
            }
            isolation = await self._replay_scene_after_postcheck_failure(
                scene_index=scene_index,
                seed_failed_unit_ids=seed_failed_unit_ids,
                work_units=work_units,
                patches_by_id=patches_by_id,
                base_text=base_texts.get(scene_index, ""),
                rejected_candidate_text=updated_texts.get(scene_index, ""),
                orders_by_id=orders_by_id,
                context=context,
                case_file=case_file,
                merger=merger,
                applier=applier,
            )
            updated_texts[scene_index] = isolation["candidate_text"]
            if isolation["candidate_text"] != base_texts.get(scene_index, ""):
                changed_scenes.add(scene_index)
            else:
                changed_scenes.discard(scene_index)
            applied_patch_ids.difference_update(
                applied_patch_ids_by_scene.get(scene_index, set())
            )
            applied_patch_ids.update(isolation["retained_patch_ids"])
            postcheck_isolation_summary[scene_index] = {
                key: value
                for key, value in isolation.items()
                if key != "candidate_text"
            }
            merge_summary.setdefault(scene_index, {})["postcheck_isolation"] = (
                postcheck_isolation_summary[scene_index]
            )

        accepted_count = 0
        failed_count = 0
        for unit in work_units:
            audit = unit.result_audit or audits.get(unit.work_unit_id, {})
            if audit.get("accepted"):
                accepted_count += 1
                for order_id in unit.source_order_ids:
                    order = orders_by_id.get(order_id)
                    if order is None or order.status in {"succeeded", "skipped"}:
                        continue
                    scene_index = order.owner_scene if order.owner_scene is not None else (
                        order.target_scenes[0] if order.target_scenes else None
                    )
                    result_text = updated_texts.get(scene_index, "") if scene_index is not None else ""
                    order.status = "succeeded" if audit.get("changed") else "skipped"
                    order.result_text_hash = _compute_text_hash(result_text)
                    order.repair_audit = {
                        "accepted": True,
                        "failures": [],
                        "changed": bool(audit.get("changed")),
                        "executor_path": "fbi_tool_work_unit",
                        "work_unit_id": unit.work_unit_id,
                        "tool_audit": audit,
                    }
                    primitive_repair = self._tool_primitive_repair_trace(audit)
                    if primitive_repair:
                        order.repair_audit["primitive_repair"] = primitive_repair
                    orders_by_id[order_id] = order
            else:
                failed_count += 1
                for order_id in unit.source_order_ids:
                    order = orders_by_id.get(order_id)
                    if order is None or order.status in {"succeeded", "skipped", "failed"}:
                        continue
                    existing_trace = list((order.repair_audit or {}).get("tool_failures") or [])
                    failure_trace = {
                        "work_unit_id": unit.work_unit_id,
                        "reason": audit.get("reason") or "tool_work_unit_failed",
                        "failures": audit.get("failures") or [],
                    }
                    for key in (
                        "failure_evidence",
                        "rollback_scope",
                        "batch_postcheck",
                        "failed_covered_goal_ids",
                        "failed_covered_issue_ids",
                    ):
                        if audit.get(key):
                            failure_trace[key] = audit[key]
                    existing_trace.append(failure_trace)
                    order.repair_audit = {
                        **(order.repair_audit or {}),
                        "tool_failures": existing_trace,
                    }
                    orders_by_id[order_id] = order

        return {
            "attempted": True,
            "work_units": len(work_units),
            "accepted": accepted_count,
            "failed": failed_count,
            "changed_scenes": sorted(changed_scenes),
            "audits": {unit.work_unit_id: unit.result_audit for unit in work_units},
            "execution_layers": [[unit.work_unit_id for unit in work_units]],
            "window_level_scheduler": True,
            "frozen_base_patch_chain": True,
            "patches_generated": len(patches),
            "patches_applied": len(applied_patch_ids),
            "merge_summary": merge_summary,
            "postcheck_isolation": postcheck_isolation_summary,
        }

    @staticmethod
    def _work_unit_window_key(unit: RepairWorkUnit) -> str:
        return str(
            unit.edit_window_id
            or (unit.edit_window or {}).get("window_id")
            or ""
        )

    @staticmethod
    def _patch_spans_overlap(left: Any, right: Any) -> bool:
        left_start = int(left.span.start)
        left_end = int(left.span.end)
        right_start = int(right.span.start)
        right_end = int(right.span.end)
        if left_start == left_end and right_start == right_end:
            return left_start == right_start
        if left_start == left_end:
            return right_start <= left_start <= right_end
        if right_start == right_end:
            return left_start <= right_start <= left_end
        return left_start < right_end and right_start < left_end

    @classmethod
    def _expand_failed_work_unit_group(
        cls,
        seed_ids: set[str],
        scene_units: list[RepairWorkUnit],
        patches_by_unit: dict[str, list[Any]],
    ) -> set[str]:
        """Expand a failed unit to only its unsafe peers.

        Unsafe peers are downstream dependencies, units in the same explicit
        edit window, and units whose frozen-base spans overlap.  Unrelated
        units in the same scene deliberately remain outside the rollback set.
        """

        rejected = set(seed_ids)
        by_id = {unit.work_unit_id: unit for unit in scene_units}
        while True:
            rejected_orders = {
                order_id
                for unit_id in rejected
                for order_id in (by_id.get(unit_id).source_order_ids if by_id.get(unit_id) else [])
            }
            rejected_windows = {
                cls._work_unit_window_key(by_id[unit_id])
                for unit_id in rejected
                if unit_id in by_id and cls._work_unit_window_key(by_id[unit_id])
            }
            rejected_patches = [
                patch
                for unit_id in rejected
                for patch in patches_by_unit.get(unit_id, [])
            ]
            expanded = set(rejected)
            for unit in scene_units:
                if unit.work_unit_id in expanded:
                    continue
                dependency_ids = set(unit.dependencies or [])
                if dependency_ids & (rejected | rejected_orders):
                    expanded.add(unit.work_unit_id)
                    continue
                window_key = cls._work_unit_window_key(unit)
                if window_key and window_key in rejected_windows:
                    expanded.add(unit.work_unit_id)
                    continue
                if any(
                    cls._patch_spans_overlap(patch, rejected_patch)
                    for patch in patches_by_unit.get(unit.work_unit_id, [])
                    for rejected_patch in rejected_patches
                ):
                    expanded.add(unit.work_unit_id)
            if expanded == rejected:
                return rejected
            rejected = expanded

    async def _replay_scene_after_postcheck_failure(
        self,
        *,
        scene_index: int,
        seed_failed_unit_ids: set[str],
        work_units: list[RepairWorkUnit],
        patches_by_id: dict[str, Any],
        base_text: str,
        rejected_candidate_text: str,
        orders_by_id: dict[str, ChapterRepairOrder],
        context: dict[str, Any],
        case_file: dict[str, Any],
        merger: PatchMerger,
        applier: PatchApplier,
    ) -> dict[str, Any]:
        """Replay independent successful patches after a peer postcheck fails."""

        scene_patch_ids = {
            patch_id
            for patch_id, patch in patches_by_id.items()
            if (patch.self_audit or {}).get("scene_index") == scene_index
        }
        scene_units = [
            unit
            for unit in work_units
            if set((unit.result_audit or {}).get("patch_ids") or []) & scene_patch_ids
        ]
        patches_by_unit = {
            unit.work_unit_id: [
                patches_by_id[patch_id]
                for patch_id in (unit.result_audit or {}).get("patch_ids") or []
                if patch_id in scene_patch_ids and patch_id in patches_by_id
            ]
            for unit in scene_units
        }
        rejected_ids = self._expand_failed_work_unit_group(
            seed_failed_unit_ids,
            scene_units,
            patches_by_unit,
        )
        replay_rounds: list[dict[str, Any]] = []
        stable_candidate = base_text
        stable_patch_ids: set[str] = set()
        stable_checks: dict[str, list[dict[str, Any]]] = {}
        replay_failure = ""

        # Removing one patch can make a previously accepted peer fail its
        # metric.  Iterate until the retained set is stable; each round rejects
        # at least one more work unit and is therefore bounded.
        for replay_round in range(len(scene_units) + 1):
            retained_units = [
                unit
                for unit in scene_units
                if unit.work_unit_id not in rejected_ids
                and bool((unit.result_audit or {}).get("accepted"))
            ]
            retained_patches = [
                patch
                for unit in retained_units
                for patch in patches_by_unit.get(unit.work_unit_id, [])
            ]
            if not retained_patches:
                stable_candidate = base_text
                stable_patch_ids = set()
                stable_checks = {}
                break

            replay_merge = merger.merge(retained_patches, base_text)
            merged_patches = list(replay_merge.get("merged_patches") or [])
            if replay_merge.get("conflict_groups") or replay_merge.get("compound_reflow"):
                replay_failure = "retained_patch_replay_conflict"
                rejected_ids.update(unit.work_unit_id for unit in retained_units)
                stable_candidate = base_text
                stable_patch_ids = set()
                stable_checks = {}
                break
            if not merged_patches:
                replay_failure = "retained_patch_replay_empty"
                rejected_ids.update(unit.work_unit_id for unit in retained_units)
                stable_candidate = base_text
                stable_patch_ids = set()
                stable_checks = {}
                break

            apply_result = await applier.apply_patches(
                base_text,
                merged_patches,
                merged_patches[0].base_text_hash,
            )
            if not apply_result.get("success"):
                replay_failure = "retained_patch_replay_apply_failed"
                rejected_ids.update(unit.work_unit_id for unit in retained_units)
                stable_candidate = base_text
                stable_patch_ids = set()
                stable_checks = {}
                break

            candidate_text = str(apply_result.get("new_text", base_text))
            round_checks: dict[str, list[dict[str, Any]]] = {}
            newly_failed_ids: set[str] = set()
            for unit in retained_units:
                batch_checks: list[dict[str, Any]] = []
                for order_id in unit.source_order_ids:
                    order = orders_by_id.get(order_id)
                    if order is None:
                        continue
                    check_context = dict(context)
                    if unit.validator_snapshot:
                        check_context["validator_snapshot"] = unit.validator_snapshot
                    order_audit = await self._audit_repair_result_async(
                        order,
                        base_text,
                        candidate_text,
                        scene_index,
                        check_context,
                        case_file,
                    )
                    batch_checks.append({
                        "order_id": order_id,
                        "covered_issue_ids": list(order.source_violation_ids or []),
                        "window_id": (
                            unit.edit_window_id
                            or (unit.edit_window or {}).get("window_id", "")
                        ),
                        **order_audit,
                    })
                round_checks[unit.work_unit_id] = batch_checks
                if not batch_checks or any(
                    not check.get("accepted") for check in batch_checks
                ):
                    newly_failed_ids.add(unit.work_unit_id)

            replay_rounds.append({
                "round": replay_round + 1,
                "retained_unit_ids": [unit.work_unit_id for unit in retained_units],
                "newly_failed_unit_ids": sorted(newly_failed_ids),
                "candidate_text_hash": _compute_text_hash(candidate_text),
            })
            if not newly_failed_ids:
                stable_candidate = candidate_text
                stable_patch_ids = {patch.patch_id for patch in merged_patches}
                stable_checks = round_checks
                break

            for unit_id in newly_failed_ids:
                unit = next(
                    (item for item in scene_units if item.work_unit_id == unit_id),
                    None,
                )
                if unit is None:
                    continue
                failed_checks = [
                    check
                    for check in round_checks.get(unit_id, [])
                    if not check.get("accepted")
                ]
                failures = sorted({
                    failure
                    for check in failed_checks
                    for failure in (check.get("failures") or ["replay_postcheck_failed"])
                }) or ["missing_source_order_mapping"]
                unit.result_audit = {
                    **(unit.result_audit or {}),
                    "accepted": False,
                    "changed": False,
                    "reason": "replay_postcheck_failed",
                    "failures": failures,
                    "batch_postcheck": round_checks.get(unit_id, []),
                    "executor_path": "fbi_frozen_patch_replay_postcheck",
                }
                unit.status = "failed"
            rejected_ids.update(newly_failed_ids)
            rejected_ids = self._expand_failed_work_unit_group(
                rejected_ids,
                scene_units,
                patches_by_unit,
            )

        rejected_patch_ids = {
            patch.patch_id
            for unit_id in rejected_ids
            for patch in patches_by_unit.get(unit_id, [])
        }
        trigger_ids = sorted(seed_failed_unit_ids)
        for unit in scene_units:
            audit = unit.result_audit or {}
            unit_patches = patches_by_unit.get(unit.work_unit_id, [])
            if unit.work_unit_id in rejected_ids:
                was_trigger = unit.work_unit_id in seed_failed_unit_ids
                rollback_reason = (
                    "work_unit_postcheck_failed"
                    if was_trigger
                    else "dependent_or_overlapping_work_unit_rollback"
                )
                failures = list(dict.fromkeys([
                    *(audit.get("failures") or []),
                    rollback_reason,
                ]))
                failed_checks = [
                    check
                    for check in (audit.get("batch_postcheck") or [])
                    if not check.get("accepted")
                ]
                unit.status = "failed"
                unit.result_audit = {
                    **audit,
                    "accepted": False,
                    "changed": False,
                    "reason": rollback_reason,
                    "failures": failures,
                    "rollback_scope": {
                        "kind": "work_unit_group",
                        "scene_index": scene_index,
                        "trigger_work_unit_ids": trigger_ids,
                        "rolled_back_work_unit_ids": sorted(rejected_ids),
                        "rolled_back_patch_ids": sorted(rejected_patch_ids),
                    },
                    "failure_evidence": {
                        "scene_index": scene_index,
                        "base_text_hash": _compute_text_hash(base_text),
                        "rejected_candidate_text_hash": _compute_text_hash(
                            rejected_candidate_text
                        ),
                        "failed_spans": [
                            {
                                "patch_id": patch.patch_id,
                                "start": int(patch.span.start),
                                "end": int(patch.span.end),
                                "before_text": patch.original_text,
                                "after_text": patch.replacement_text,
                            }
                            for patch in unit_patches
                        ],
                        "postcheck": failed_checks,
                    },
                    "executor_path": "fbi_frozen_patch_failure_isolation",
                }
                continue

            if unit.status == "succeeded" and unit.work_unit_id in stable_checks:
                unit.result_audit = {
                    **audit,
                    "accepted": True,
                    "changed": True,
                    "batch_postcheck": stable_checks[unit.work_unit_id],
                    "replayed_after_peer_failure": True,
                    "replay_candidate_text_hash": _compute_text_hash(stable_candidate),
                    "executor_path": "fbi_frozen_patch_replay",
                }

        return {
            "candidate_text": stable_candidate,
            "trigger_failed_unit_ids": trigger_ids,
            "rolled_back_unit_ids": sorted(rejected_ids),
            "retained_unit_ids": sorted(
                unit.work_unit_id
                for unit in scene_units
                if unit.work_unit_id not in rejected_ids
                and unit.work_unit_id in stable_checks
            ),
            "rolled_back_patch_ids": sorted(rejected_patch_ids),
            "retained_patch_ids": sorted(stable_patch_ids),
            "replay_rounds": replay_rounds,
            "replay_failure": replay_failure,
            "rollback_granularity": "work_unit_group",
        }

    def _build_reflow_work_units(
        self,
        reflow_issue_ids: set[str],
        work_units: list[RepairWorkUnit],
        scene_index: int,
        base_text: str,
        *,
        case_id: str = "",
    ) -> list[RepairWorkUnit]:
        """9.3.2: 从冲突 issue_ids 反查 work_units，重新构造 compound work unit。

        回流只执行一次（不递归），避免无限循环。
        """
        # 1. 找到 source_violation_ids 包含 reflow_issue_ids 的 unit
        reflow_units: list[RepairWorkUnit] = []
        for unit in work_units:
            unit_issue_ids = set(unit.source_violation_ids or [])
            if unit_issue_ids & reflow_issue_ids:
                reflow_units.append(unit)
        if not reflow_units:
            return []
        if len(reflow_units) == 1:
            return reflow_units  # 单个 unit 不需要合并

        # 2. 用 EditWindowPlanner 合并这些 unit 的 commands
        planner = EditWindowPlanner()
        commands = [cmd for unit in reflow_units for cmd in unit.tool_batch.commands]
        compound_cmd = planner._build_compound_replace_command(commands)

        if compound_cmd is not None:
            merged_commands = [compound_cmd]
        else:
            merged_commands = planner._merge_commands(commands)
        if not merged_commands:
            return []

        # 3. 构造新的 compound work unit
        source_issue_ids: list[str] = []
        for unit in reflow_units:
            source_issue_ids.extend(unit.source_violation_ids or [])
        source_issue_ids = list(dict.fromkeys(source_issue_ids))

        source_order_ids: list[str] = []
        for unit in reflow_units:
            source_order_ids.extend(unit.source_order_ids or [])
        source_order_ids = list(dict.fromkeys(source_order_ids))

        compound_families: list[str] = []
        for unit in reflow_units:
            compound_families.extend(unit.compound_issue_families or planner._issue_families(unit))
        compound_families = list(dict.fromkeys(compound_families))

        work_unit_id = _compute_text_hash(
            f"reflow:{case_id}:{scene_index}:{','.join(source_issue_ids)}"
        )

        # 设置 command 的 scene_index 和 source_issue_ids
        for idx, cmd in enumerate(merged_commands):
            cmd.source_issue_ids = list(dict.fromkeys(
                [*cmd.source_issue_ids, *source_issue_ids]
            ))
            if not cmd.command_id:
                cmd.command_id = f"{work_unit_id}:cmd:{idx}"
            if cmd.scene_index is None:
                cmd.scene_index = scene_index

        compound_unit = RepairWorkUnit(
            work_unit_id=work_unit_id,
            local_id=f"reflow_compound_{scene_index}",
            owner_scene=scene_index,
            target_scenes=[scene_index],
            source_order_ids=source_order_ids,
            source_violation_ids=source_issue_ids,
            edit_window=reflow_units[0].edit_window,
            edit_window_id=f"reflow_{scene_index}",
            base_scene_hash=_compute_text_hash(base_text),
            compound_issue_ids=source_issue_ids,
            compound_issue_families=compound_families,
            merge_policy="compound_patch",
            tool_batch=ToolCommandBatch(
                batch_id=f"{work_unit_id}:batch",
                source_blueprint_id="reflow_compound",
                source_work_unit_id=work_unit_id,
                commands=merged_commands,
                target_scenes=[scene_index],
                source_order_ids=source_order_ids,
            ),
            blueprint_source="reflow_compound",
        )
        return [compound_unit]

    @classmethod
    def _conflict_free_work_unit_layer(cls, ready: list[RepairWorkUnit]) -> list[RepairWorkUnit]:
        layer: list[RepairWorkUnit] = []
        for unit in ready:
            if any(cls._work_units_conflict(unit, existing) for existing in layer):
                continue
            layer.append(unit)
        return layer or ready[:1]

    @classmethod
    def _work_units_conflict(cls, left: RepairWorkUnit, right: RepairWorkUnit) -> bool:
        left_scenes = set(left.target_scenes or ([] if left.owner_scene is None else [left.owner_scene]))
        right_scenes = set(right.target_scenes or ([] if right.owner_scene is None else [right.owner_scene]))
        if not left_scenes or not right_scenes:
            return True
        if not (left_scenes & right_scenes):
            return False
        left_keys, left_broad = cls._work_unit_window_keys(left)
        right_keys, right_broad = cls._work_unit_window_keys(right)
        if left_broad or right_broad:
            return True
        if left_keys & right_keys:
            return True
        for left_key in left_keys:
            for right_key in right_keys:
                if cls._window_key_overlap(left_key, right_key):
                    return True
        return False

    @staticmethod
    def _work_unit_window_keys(unit: RepairWorkUnit) -> tuple[set[str], bool]:
        keys: set[str] = set()
        broad = False
        for command in unit.tool_batch.commands:
            scene_index = command.scene_index
            if scene_index is None:
                scene_index = unit.owner_scene if unit.owner_scene is not None else (
                    unit.target_scenes[0] if unit.target_scenes else None
                )
            scene_key = f"scene:{scene_index}" if scene_index is not None else "scene:*"
            if command.operation in {"normalize_punctuation", "merge_paragraphs"}:
                broad = True
                keys.add(scene_key)
                continue
            anchor = (
                command.target_span
                or command.before_span
                or command.after_span
                or command.window_start
                or str(unit.edit_window.get("anchor") or "")
            )
            if not anchor:
                broad = True
                keys.add(scene_key)
                continue
            keys.add(f"{scene_key}:window:{anchor[:120]}")
        if not keys:
            broad = True
            keys.add(f"scene:{unit.owner_scene if unit.owner_scene is not None else '*'}")
        return keys, broad

    @staticmethod
    def _window_key_overlap(left: str, right: str) -> bool:
        left_scene, _, left_anchor = left.partition(":window:")
        right_scene, _, right_anchor = right.partition(":window:")
        if left_scene != right_scene or not left_anchor or not right_anchor:
            return False
        return left_anchor in right_anchor or right_anchor in left_anchor

    @staticmethod
    def _tool_primitive_repair_trace(audit: dict[str, Any]) -> dict[str, Any] | None:
        applied: list[dict[str, Any]] = []
        for result in audit.get("command_results") or []:
            if not isinstance(result, dict):
                continue
            if result.get("operation") not in {"replace_exact", "replace_span"} or not result.get("changed"):
                continue
            applied.append({
                "operation": "replace_phrase",
                "command_id": result.get("command_id", ""),
            })
        if not applied:
            return None
        return {
            "changed": True,
            "executor_path": "structured_patch_plan",
            "applied_patches": applied,
        }

    @staticmethod
    def _tool_target_span_checks(audit: dict[str, Any]) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        for result in audit.get("command_results") or []:
            if not isinstance(result, dict) or result.get("operation") != "normalize_punctuation":
                continue
            before = result.get("dash_count_before", result.get("dash_count"))
            after = result.get("dash_count_after", result.get("dash_count"))
            if before is None or after is None:
                continue
            checks.append({
                "target_span": "dash_artifact",
                "before_count": before,
                "after_count": after,
                "improved": after < before,
            })
        return checks

    @staticmethod
    def _tool_dash_density_check(audit: dict[str, Any]) -> dict[str, Any] | None:
        for result in audit.get("command_results") or []:
            if not isinstance(result, dict) or result.get("operation") != "normalize_punctuation":
                continue
            before = result.get("dash_count_before", result.get("dash_count"))
            after = result.get("dash_count_after", result.get("dash_count"))
            if before is None or after is None:
                continue
            return {
                "before_count": before,
                "after_count": after,
                "allowed": result.get("allowed"),
                "target_count": result.get("allowed"),
                "passed": after <= result.get("allowed", after),
            }
        return None

    @staticmethod
    def _group_orders_by_scene(orders: list[ChapterRepairOrder]) -> dict[int, list[ChapterRepairOrder]]:
        """将订单按 target_scenes 归组。

        如果一个订单涉及多个场景，它会被归入 owner_scene（如有），
        否则归入 target_scenes[0]。同一场景上的订单按 priority 排序。
        """
        scene_map: dict[int, list[ChapterRepairOrder]] = {}
        priority_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}

        for order in orders:
            if order.owner_scene is not None:
                primary_scene = order.owner_scene
            elif order.target_scenes:
                primary_scene = order.target_scenes[0]
            elif order.repair_type in {"contract_patch", "manual_review"}:
                primary_scene = 0
            else:
                _logger.warning("ChapterRepairExecutor: order %s has no target_scenes", order.order_id)
                order.status = "failed"
                order.repair_audit = {
                    "accepted": False,
                    "reason": "missing_target_scene",
                    "failures": ["missing_target_scene"],
                    "changed": False,
                    "executor_path": "scene_router",
                }
                continue

            scene_map.setdefault(primary_scene, []).append(order)

        # 按 priority 排序
        for scene_index in scene_map:
            scene_map[scene_index].sort(key=lambda o: priority_rank.get(o.priority, 99))

        return scene_map

    def _get_execution_groups(self, plan: ChapterRepairPlan) -> list[list[ChapterRepairOrder]]:
        """Build executor groups with dependencies and write-scope safety."""
        groups: list[list[ChapterRepairOrder]] = []
        known_order_ids = {order.order_id for order in plan.orders}
        completed: set[str] = {
            order.order_id
            for order in plan.orders
            if order.status in ("succeeded", "skipped")
        }
        pending = [
            order for order in plan.orders
            if order.status not in ("succeeded", "skipped", "failed")
        ]

        while pending:
            group: list[ChapterRepairOrder] = []
            group_write_scopes: set[str] = set()
            group_has_broad_write = False
            remaining: list[ChapterRepairOrder] = []

            for order in pending:
                dependencies = set(order.dependencies or [])
                if not dependencies <= completed:
                    remaining.append(order)
                    continue

                write_scopes = self._order_write_scopes(order)
                broad_write = self._is_broad_write_order(order, write_scopes)
                order_scene = self._primary_scene_for_order(order)
                order_batchable = self._is_batchable_local_quality_order(order)

                same_scene_batchable = bool(
                    order_batchable
                    and order_scene is not None
                    and all(
                        order_scene == self._primary_scene_for_order(existing)
                        and self._is_batchable_local_quality_order(existing)
                        for existing in group
                    )
                )
                conflicts = bool(write_scopes & group_write_scopes)
                if group and (group_has_broad_write or broad_write):
                    conflicts = True

                if group and conflicts and not same_scene_batchable:
                    remaining.append(order)
                    continue

                group.append(order)
                group_write_scopes.update(write_scopes)
                group_has_broad_write = group_has_broad_write or broad_write

            if not group:
                self._fail_unresolved_dependency_orders(
                    pending,
                    known_order_ids=known_order_ids,
                    completed_order_ids=completed,
                )
                break

            groups.append(group)
            completed.update(order.order_id for order in group)
            pending = remaining

        return groups

    @staticmethod
    def _fail_unresolved_dependency_orders(
        orders: list[ChapterRepairOrder],
        *,
        known_order_ids: set[str],
        completed_order_ids: set[str],
    ) -> None:
        for order in orders:
            dependencies = set(order.dependencies or [])
            missing = sorted(dependencies - known_order_ids)
            unresolved = sorted((dependencies & known_order_ids) - completed_order_ids)
            reason = "missing_required_dependencies" if missing else "cyclic_or_unresolved_dependencies"
            order.status = "failed"
            order.repair_audit = {
                "accepted": False,
                "reason": reason,
                "failures": [reason],
                "missing_dependencies": missing,
                "unresolved_dependencies": unresolved,
                "changed": False,
                "executor_path": "dependency_scheduler",
            }

    @staticmethod
    def _order_finished_unsuccessfully(order: ChapterRepairOrder) -> bool:
        if order.status == "failed":
            return True
        if order.status == "skipped" and not (order.repair_audit or {}).get("accepted"):
            return True
        return False

    @staticmethod
    def _order_write_scopes(order: ChapterRepairOrder) -> set[str]:
        if order.write_scope:
            return set(order.write_scope)
        if order.repair_type == "contract_patch":
            return {"scene_contract"}
        if order.repair_type == "manual_review":
            return {"manual_review"}
        return {f"scene:{scene}" for scene in order.target_scenes}

    @staticmethod
    def _is_broad_write_order(order: ChapterRepairOrder, write_scopes: set[str]) -> bool:
        if order.repair_type == "cross_scene_alignment":
            return True
        if len(write_scopes) > 1:
            return True
        return any(scope in {"chapter_text", "chapter", "all_scenes"} for scope in write_scopes)

    @staticmethod
    def _primary_scene_for_order(order: ChapterRepairOrder) -> int | None:
        if order.owner_scene is not None:
            return order.owner_scene
        if order.target_scenes:
            return order.target_scenes[0]
        if order.repair_type in {"contract_patch", "manual_review"}:
            return 0
        return None

    async def _execute_orders_for_scene(
        self,
        scene_index: int,
        orders: list[ChapterRepairOrder],
        scene_texts: dict[int, str],
        context: dict[str, Any],
        case_file: dict[str, Any] | None = None,
        original_scene_texts: dict[int, str] | None = None,
    ) -> tuple[str, list[tuple[str, str, str, dict[str, Any]]]]:
        """串行执行同一场景上的多个订单。

        Returns:
            (new_text, [(order_id, status, result_text_hash, repair_audit), ...])
        """
        case_file = case_file or {}
        current_text = scene_texts.get(scene_index, "")
        original_text = (original_scene_texts or scene_texts).get(scene_index, current_text)
        order_results: list[tuple[str, str, str, dict[str, Any]]] = []

        index = 0
        while index < len(orders):
            order = orders[index]
            resolved_audit = self._preflight_resolved_order_audit(
                order,
                current_text,
                original_text,
                scene_index,
                context,
            )
            if resolved_audit is not None:
                text_hash = _compute_text_hash(current_text)
                order.status = "skipped"
                order.result_text_hash = text_hash
                order.repair_audit = resolved_audit
                order_results.append((order.order_id, "skipped", text_hash, resolved_audit))
                index += 1
                continue
            batch = self._collect_batchable_local_patch_orders(orders, index)
            if len(batch) > 1:
                new_text, batch_results = await self._execute_batchable_local_patch_orders(
                    scene_index,
                    batch,
                    current_text,
                    original_text,
                    scene_texts,
                    context,
                    case_file,
                )
                if any(result[3].get("accepted") for result in batch_results):
                    current_text = new_text
                order_results.extend(batch_results)
                index += len(batch)
                continue

            prior_tool_failures = list((order.repair_audit or {}).get("tool_failures") or [])
            order.status = "running"
            order_started = time.monotonic()
            try:
                if order.repair_type == "contract_patch":
                    text_hash = _compute_text_hash(current_text)
                    audit = self._apply_contract_patch_order(
                        order,
                        scene_index,
                        context,
                        case_file,
                        current_text,
                    )
                    audit["timing_ms"] = int((time.monotonic() - order_started) * 1000)
                    order.status = "succeeded" if audit["accepted"] else "failed"
                    order.result_text_hash = text_hash
                    order.repair_audit = audit
                    order_results.append((order.order_id, order.status, text_hash, audit))
                    _logger.info(
                        "ChapterRepairExecutor: order %s %s scene=%d type=%s",
                        order.order_id, order.status, scene_index, order.repair_type,
                    )
                    index += 1
                    continue
                if order.repair_type == "manual_review":
                    text_hash = _compute_text_hash(current_text)
                    audit = {
                        "accepted": False,
                        "failures": ["manual_review_required"],
                        "base_hash": text_hash,
                        "result_hash": text_hash,
                        "changed": False,
                        "timing_ms": int((time.monotonic() - order_started) * 1000),
                        "executor_path": "manual_review",
                    }
                    order.status = "skipped"
                    order.result_text_hash = text_hash
                    order.repair_audit = audit
                    order_results.append((order.order_id, "skipped", text_hash, audit))
                    index += 1
                    continue
                new_text, audit = await self._execute_order_candidate_attempts(
                    order,
                    current_text,
                    scene_index,
                    scene_texts,
                    context,
                    case_file,
                )
                text_hash = _compute_text_hash(new_text)
                if not audit["accepted"] and self._is_non_blocking_no_effect_after_prior_change(
                    order, audit, current_text, original_text,
                ):
                    order.status = "skipped"
                    audit = {
                        **audit,
                        "accepted": True,
                        "warnings": audit.get("failures", []),
                        "failures": [],
                        "disposition": "no_effect_after_prior_change",
                    }
                else:
                    order.status = "succeeded" if audit["accepted"] else "failed"
                order.result_text_hash = text_hash
                audit["timing_ms"] = int((time.monotonic() - order_started) * 1000)
                audit.setdefault("executor_path", "single_order")
                if prior_tool_failures:
                    audit["legacy_fallback_after_tool_failure"] = True
                    audit["prior_tool_failures"] = prior_tool_failures
                order.repair_audit = audit
                if audit["accepted"]:
                    current_text = new_text
                order_results.append((order.order_id, order.status, text_hash, audit))
                _logger.info(
                    "ChapterRepairExecutor: order %s %s scene=%d type=%s failures=%s stage=%s",
                    order.order_id,
                    order.status,
                    scene_index,
                    order.repair_type,
                    list(audit.get("failures") or []),
                    audit.get("failure_stage", ""),
                )
            except Exception as exc:
                _logger.exception(
                    "ChapterRepairExecutor: order %s failed scene=%d type=%s: %s",
                    order.order_id, scene_index, order.repair_type, exc,
                )
                order.status = "failed"
                text_hash = _compute_text_hash(current_text)
                order.result_text_hash = text_hash
                order.repair_audit = {
                    "accepted": False,
                    "reason": "exception",
                    "error": str(exc),
                    "base_hash": text_hash,
                    "result_hash": text_hash,
                    "changed": False,
                    "timing_ms": int((time.monotonic() - order_started) * 1000),
                    "executor_path": "single_order",
                }
                order_results.append((order.order_id, "failed", text_hash, order.repair_audit))
            index += 1

        # 方案6 B1：删除 _supersede_no_effect_failures_after_later_repairs 调用。
        # failed 就是 failed，不假装成功。no_effect 的 failed 状态会触发方案6 Part C 的循环重试。
        return current_text, order_results

    def _preflight_resolved_order_audit(
        self,
        order: ChapterRepairOrder,
        current_text: str,
        original_text: str,
        scene_index: int,
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Skip stale repair orders when a reusable evidence probe proves absence.

        This is not a story-specific bypass. Each probe must be conservative:
        it may only accept an order when the current text no longer contains
        confirmed evidence for the rule the order is trying to repair.
        """
        for probe in (
            self._inner_access_resolution_probe,
        ):
            audit = probe(order, current_text, original_text, scene_index, context)
            if audit is not None:
                return audit
        return None

    async def _execute_order_candidate_attempts(
        self,
        order: ChapterRepairOrder,
        current_text: str,
        scene_index: int,
        scene_texts: dict[int, str],
        context: dict[str, Any],
        case_file: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        budget = self._candidate_attempt_budget(order)
        original_strength = order.repair_strength
        original_lane = order.repair_lane
        original_brief = copy.deepcopy(order.repair_brief or {})
        candidate_attempts: list[dict[str, Any]] = []
        final_text = current_text
        final_audit: dict[str, Any] | None = None

        for attempt in range(1, budget + 1):
            attempt_strength = self._strength_for_attempt(order, attempt)
            attempt_lane = self._lane_for_attempt(order, attempt, original_lane, original_brief)
            order.repair_lane = attempt_lane
            order.repair_strength = attempt_strength
            order.repair_brief = self._repair_brief_for_attempt(
                original_brief,
                attempt_lane,
                attempt_strength,
                order.allowed_max_strength,
            )
            attempt_context = dict(context or {})
            attempt_context["repair_attempt"] = {
                "attempt": attempt,
                "budget": budget,
                "repair_lane": attempt_lane,
                "repair_strength": attempt_strength,
                "original_strength": original_strength,
                "original_lane": original_lane,
                "allowed_max_strength": order.allowed_max_strength,
                "reason": "target_metric_not_improved_retry" if attempt > 1 else "initial_candidate",
            }
            candidate_text = await self._dispatch_order(
                order,
                current_text,
                scene_index,
                scene_texts,
                attempt_context,
                case_file,
            )
            audit = await self._audit_repair_result_async(
                order,
                current_text,
                candidate_text,
                scene_index,
                attempt_context,
                case_file,
            )
            if self._should_retry_for_protection(order, audit):
                retry_text, retry_audit = await self._retry_for_protection(
                    order,
                    current_text,
                    candidate_text,
                    audit,
                    scene_index,
                    scene_texts,
                    attempt_context,
                    case_file,
                )
                if retry_audit is not None:
                    audit = retry_audit
                if retry_text is not None:
                    candidate_text = retry_text
            primitive_trace = self._primitive_repair_traces.pop(order.order_id, None)
            if primitive_trace:
                audit["primitive_repair"] = primitive_trace
                audit["executor_path"] = primitive_trace.get("executor_path", "final_gate_text_repair")

            attempt_record = dict((audit.get("candidate_attempts") or [{}])[0])
            attempt_record.update({
                "attempt": attempt,
                "budget": budget,
                "lane": attempt_lane,
                "strength": attempt_strength,
                "decision": "accepted" if audit.get("accepted") else "rejected",
                "failures": list(audit.get("failures") or []),
            })
            candidate_attempts.append(attempt_record)
            final_text = candidate_text
            final_audit = audit

            if audit.get("accepted"):
                break
            if not self._should_retry_for_target_no_progress(order, audit, attempt, budget):
                break

        order.repair_strength = original_strength
        order.repair_lane = original_lane
        order.repair_brief = original_brief
        if final_audit is None:
            text_hash = _compute_text_hash(current_text)
            final_audit = {
                "accepted": False,
                "failures": ["empty_candidate_attempts"],
                "base_hash": text_hash,
                "result_hash": text_hash,
                "changed": False,
                "candidate_attempts": candidate_attempts,
            }
            final_text = current_text
        final_audit = {
            **final_audit,
            "candidate_attempts": candidate_attempts,
            "candidate_attempt_budget": budget,
            "strength_retry": {
                "attempted": len(candidate_attempts) > 1,
                "attempts": len(candidate_attempts),
                "initial_strength": original_strength,
                "final_strength": candidate_attempts[-1].get("strength") if candidate_attempts else original_strength,
            },
            "lane_retry": {
                "attempted": any(
                    attempt.get("lane") != original_lane
                    for attempt in candidate_attempts
                ),
                "initial_lane": original_lane,
                "final_lane": candidate_attempts[-1].get("lane") if candidate_attempts else original_lane,
            },
        }
        return final_text, final_audit

    @staticmethod
    def _candidate_attempt_budget(order: ChapterRepairOrder) -> int:
        if any(
            isinstance(command, dict)
            and command.get("operation") == "llm_creative_rewrite"
            for command in (order.tool_commands or [])
        ):
            # One canonical creative work unit owns one candidate-generation
            # call.  Any later attempt must be represented by an explicit
            # delta work unit so cost and causality remain observable.
            return 1
        try:
            raw = int(order.candidate_attempt_budget or 1)
        except Exception:
            raw = 1
        if order.repair_type in {"contract_patch", "manual_review"}:
            return 1
        # 通用修复（循环 #14 时间优化）：单 order 尝试上限 3→2
        # 大局观：每次 attempt 是一次 LLM 调用（~30-60s）。3 次 attempt 意味着
        #   如果第 1 次生成坏 patch，还会重试 2 次。但根因通常是蓝图信息不足
        #   或 target_span 不准，重试相同输入得到的 patch 往往相似。
        #   循环 #13 已修复 old_text_not_found（场景正文截断），蓝图质量提升，
        #   2 次 attempt 足够覆盖"第 1 次偶发失败 → 第 2 次成功"的场景。
        #   第 3 次 attempt 的边际收益低于其时间成本（违反铁律 11）。
        # 预期收益：每 order 省 ~30-60s，多 order 累计省 ~4min/轮。
        return max(1, min(raw, 2))

    @staticmethod
    def _is_fact_repair_order(order: ChapterRepairOrder, brief: dict[str, Any] | None = None) -> bool:
        brief = brief or order.repair_brief or {}
        if isinstance(brief.get("fact_repair_goal"), dict) and brief.get("fact_repair_goal"):
            return True
        if str(order.repair_domain or "").lower() == "fact":
            return True
        metrics = [
            item for item in (brief.get("target_metrics") or [])
            if isinstance(item, dict)
        ]
        return any(
            str(item.get("metric") or "").lower() in {
                "fact_conflict",
                "internal_conflict",
                "identity_conflict",
                "setting_conflict",
                "spatial_conflict",
                "ownership_conflict",
                "timeline_conflict",
                "temporal_conflict",
            }
            for item in metrics
        )

    @staticmethod
    def _next_attempt_lane(lane: str) -> str:
        return {
            "fact_local_patch": "fact_bridge_patch",
            "fact_bridge_patch": "scene_restructure",
        }.get(str(lane or ""), "")

    @staticmethod
    def _lane_for_attempt(
        order: ChapterRepairOrder,
        attempt: int,
        original_lane: str,
        original_brief: dict[str, Any],
    ) -> str:
        lane = original_lane or order.repair_lane or ""
        if attempt <= 1 or not ChapterRepairExecutor._is_fact_repair_order(order, original_brief):
            return lane
        for _ in range(1, attempt):
            next_lane = ChapterRepairExecutor._next_attempt_lane(lane)
            if not next_lane:
                break
            lane = next_lane
        return lane

    @staticmethod
    def _allowed_operations_for_attempt_lane(lane: str) -> list[str]:
        return {
            "fact_local_patch": ["replace_conflicting_phrase", "insert_causal_bridge"],
            "fact_bridge_patch": [
                "replace_conflicting_phrase",
                "insert_causal_bridge",
                "insert_missing_fact_components",
                "rewrite_process_beat",
            ],
            "scene_restructure": ["rewrite_paragraph", "reorder_micro_beats", "limited_scene_rewrite"],
        }.get(str(lane or ""), [])

    @staticmethod
    def _repair_brief_for_attempt(
        original_brief: dict[str, Any],
        attempt_lane: str,
        attempt_strength: str,
        allowed_max_strength: str,
    ) -> dict[str, Any]:
        brief = copy.deepcopy(original_brief or {})
        if not brief:
            return brief
        brief["repair_lane"] = attempt_lane
        brief["repair_strength"] = attempt_strength
        if allowed_max_strength:
            brief["allowed_max_strength"] = allowed_max_strength
        strategy = dict(brief.get("repair_strategy") or {})
        strategy["current_lane"] = attempt_lane
        strategy.setdefault("preferred_lane", attempt_lane)
        strategy["fallback_lane"] = ChapterRepairExecutor._next_attempt_lane(attempt_lane)
        brief["repair_strategy"] = strategy
        edit_scope = dict(brief.get("edit_scope") or {})
        allowed_ops = ChapterRepairExecutor._allowed_operations_for_attempt_lane(attempt_lane)
        if allowed_ops:
            edit_scope["allowed_operations"] = allowed_ops
        brief["edit_scope"] = edit_scope
        return brief

    @staticmethod
    def _strength_for_attempt(order: ChapterRepairOrder, attempt: int) -> str:
        base = order.repair_strength or "S2"
        max_strength = order.allowed_max_strength or base
        try:
            base_index = _REPAIR_STRENGTH_ORDER.index(base)
        except ValueError:
            base_index = _REPAIR_STRENGTH_ORDER.index("S2")
        try:
            max_index = _REPAIR_STRENGTH_ORDER.index(max_strength)
        except ValueError:
            max_index = base_index
        target_index = min(max(base_index + max(0, attempt - 1), base_index), max_index)
        return _REPAIR_STRENGTH_ORDER[target_index]

    @staticmethod
    def _should_retry_for_target_no_progress(
        order: ChapterRepairOrder,
        audit: dict[str, Any],
        attempt: int,
        budget: int,
    ) -> bool:
        if attempt >= budget:
            return False
        failures = set(audit.get("failures") or [])
        if "target_metric_not_improved" not in failures:
            return False
        if "protected_obligation_removed" in failures or "empty_result" in failures:
            return False
        current_strength = ChapterRepairExecutor._strength_for_attempt(order, attempt)
        next_strength = ChapterRepairExecutor._strength_for_attempt(order, attempt + 1)
        if next_strength != current_strength:
            return True
        # strength 不变时，检查 lane 是否会升级——lane 升级意味着修复策略变化
        # （如 fact_bridge_patch → scene_restructure），即使 strength 相同也值得重试。
        # 之前只看 strength 导致 S4→S4 时跳过第 3 次尝试，即使 budget 还有剩余。
        brief = order.repair_brief or {}
        original_lane = str(brief.get("repair_lane") or order.repair_lane or "")
        current_lane = ChapterRepairExecutor._lane_for_attempt(order, attempt, original_lane, brief)
        next_lane = ChapterRepairExecutor._lane_for_attempt(order, attempt + 1, original_lane, brief)
        return bool(next_lane) and next_lane != current_lane

    @staticmethod
    def _repair_execution_summary(orders: list[ChapterRepairOrder]) -> dict[str, Any]:
        """Summarize FBI repair execution so workflow traces can spot weak repair lanes."""
        total = len(orders)
        status_counts: dict[str, int] = {}
        lane_counts: dict[str, int] = {}
        strength_counts: dict[str, int] = {}
        candidate_attempts_total = 0
        target_check_supported = 0
        target_check_passed = 0
        protection_rejection_count = 0
        no_progress_candidate_count = 0
        strength_retry_attempted = 0
        strength_retry_succeeded = 0
        legacy_fallback_after_tool_failure = 0
        executor_path_counts: dict[str, int] = {}

        for order in orders:
            status_counts[order.status] = status_counts.get(order.status, 0) + 1
            lane = order.repair_lane or "unspecified"
            strength = order.repair_strength or "unspecified"
            lane_counts[lane] = lane_counts.get(lane, 0) + 1
            strength_counts[strength] = strength_counts.get(strength, 0) + 1

            audit = order.repair_audit or {}
            executor_path = str(audit.get("executor_path") or "unknown")
            executor_path_counts[executor_path] = executor_path_counts.get(executor_path, 0) + 1
            if audit.get("legacy_fallback_after_tool_failure"):
                legacy_fallback_after_tool_failure += 1
            attempts = [
                item for item in (audit.get("candidate_attempts") or [])
                if isinstance(item, dict)
            ]
            candidate_attempts_total += len(attempts)
            if attempts:
                for attempt_item in attempts:
                    target_check = attempt_item.get("target_check") or {}
                    if target_check.get("supported"):
                        target_check_supported += 1
                        if target_check.get("passed"):
                            target_check_passed += 1
                    attempt_failures = set(attempt_item.get("failures") or [])
                    if "protected_obligation_removed" in attempt_failures:
                        protection_rejection_count += 1
                    if "target_metric_not_improved" in attempt_failures:
                        no_progress_candidate_count += 1
            else:
                target_check = audit.get("target_self_check") or {}
                if target_check.get("supported"):
                    target_check_supported += 1
                    if target_check.get("passed"):
                        target_check_passed += 1

            failures = set(audit.get("failures") or [])
            if "protected_obligation_removed" in failures or audit.get("removed_protected_terms"):
                protection_rejection_count += 1
            if "target_metric_not_improved" in failures:
                no_progress_candidate_count += 1

            strength_retry = audit.get("strength_retry") or {}
            if strength_retry.get("attempted"):
                strength_retry_attempted += 1
                if audit.get("accepted"):
                    strength_retry_succeeded += 1

        target_improved_rate = (
            round(target_check_passed / target_check_supported, 4)
            if target_check_supported
            else None
        )
        strength_retry_success_rate = (
            round(strength_retry_succeeded / strength_retry_attempted, 4)
            if strength_retry_attempted
            else None
        )
        protection_rejection_rate = (
            round(protection_rejection_count / max(candidate_attempts_total, total), 4)
            if total or candidate_attempts_total
            else 0.0
        )

        # Phase U-G: 收集 human_review_items 和 failure_stage 统计
        failure_stage_counts: dict[str, int] = {}
        human_review_items: list[dict[str, Any]] = []
        for order in orders:
            audit = order.repair_audit or {}
            stage = str(audit.get("failure_stage") or audit.get("blueprint_failure_stage") or "unknown")
            failure_stage_counts[stage] = failure_stage_counts.get(stage, 0) + 1
            if order.status in {"failed", "skipped"} or audit.get("reason") == "needs_blueprint_completion":
                human_review_items.append({
                    "order_id": order.order_id,
                    "issue_ids": list(order.source_violation_ids or []),
                    "repair_type": order.repair_type,
                    "scene_index": order.owner_scene if order.owner_scene is not None else (
                        order.target_scenes[0] if order.target_scenes else None
                    ),
                    "failure_stage": stage,
                    "manual_reason": str(audit.get("manual_reason") or ""),
                    "failure_reason": str(audit.get("reason") or ""),
                    "failures": list(audit.get("failures") or []),
                })

        return {
            "total_orders": total,
            "status_counts": status_counts,
            "lane_counts": lane_counts,
            "strength_counts": strength_counts,
            "candidate_attempts_total": candidate_attempts_total,
            "target_check_supported": target_check_supported,
            "target_check_passed": target_check_passed,
            "target_improved_rate": target_improved_rate,
            "protection_rejection_count": protection_rejection_count,
            "protection_rejection_rate": protection_rejection_rate,
            "no_progress_candidate_count": no_progress_candidate_count,
            "strength_retry_attempted": strength_retry_attempted,
            "strength_retry_succeeded": strength_retry_succeeded,
            "strength_retry_success_rate": strength_retry_success_rate,
            "executor_path_counts": executor_path_counts,
            "legacy_fallback_after_tool_failure": legacy_fallback_after_tool_failure,
            # Phase U-G: 前端展示字段
            "failure_stage_counts": failure_stage_counts,
            "human_review_items": human_review_items,
        }

    @staticmethod
    def _blueprint_coverage_summary(
        orders: list[ChapterRepairOrder],
        work_units: list[RepairWorkUnit],
    ) -> dict[str, Any]:
        covered_orders = {
            order_id
            for unit in work_units
            for order_id in (unit.source_order_ids or [])
            if order_id
        }
        executable_orders = {
            order_id
            for unit in work_units
            if unit.tool_batch.commands
            for order_id in (unit.source_order_ids or [])
            if order_id
        }
        legacy_disabled_orders = [
            order.order_id for order in orders
            if (order.repair_audit or {}).get("reason") == "needs_blueprint_completion"
        ]
        total = len(orders)
        return {
            "total_order_count": total,
            "covered_order_count": len(covered_orders),
            "executable_order_count": len(executable_orders),
            "blueprint_completion_required_count": len(legacy_disabled_orders),
            "covered_order_rate": round(len(covered_orders) / total, 4) if total else 1.0,
            "executable_order_rate": round(len(executable_orders) / total, 4) if total else 1.0,
            "blueprint_completion_required_order_ids": legacy_disabled_orders,
        }

    def _finalize_repair_round(
        self,
        plan: ChapterRepairPlan,
        original_scene_texts: dict[int, str],
        updated_texts: dict[int, str],
    ) -> dict[str, Any]:
        """Run deterministic whole-round cleanup after all repair orders finish."""
        dash_threshold = self._round_dash_threshold(plan)
        cleanup_trace: list[dict[str, Any]] = []
        for scene_index, text in list(updated_texts.items()):
            # 跳过未被修复修改的场景：whole-round cleanup 只清理修复引入的 dash 残留，
            # 不应改动原始正文（否则会让失败场景的正文也被 normalize）。
            if text == original_scene_texts.get(scene_index):
                continue
            before_count = count_dash_artifacts(text)
            allowed = dash_artifact_allowed_count(text, dash_threshold)
            if before_count <= allowed:
                continue
            repaired = self._normalize_dash_artifacts(
                text,
                None,
                [{
                    "type": "dash_density",
                    "metric": "dash_per_1000",
                    "expected_max": dash_threshold,
                }],
            )
            after_count = count_dash_artifacts(repaired)
            if repaired == text or after_count > before_count:
                continue
            updated_texts[scene_index] = repaired
            cleanup_trace.append({
                "scene_index": scene_index,
                "cleanup": "dash_density",
                "threshold": dash_threshold,
                "before_count": before_count,
                "after_count": after_count,
                "before_hash": _compute_text_hash(text),
                "after_hash": _compute_text_hash(repaired),
                "changed_from_original": repaired != original_scene_texts.get(scene_index, ""),
            })
        return {
            "fbi_department_phase": "round_finalization",
            "dash_threshold": dash_threshold,
            "cleanup_count": len(cleanup_trace),
            "cleanups": cleanup_trace,
        }

    @staticmethod
    def _round_dash_threshold(plan: ChapterRepairPlan) -> float:
        thresholds: list[float] = []
        for order in plan.orders:
            for source in [
                order.repair_brief or {},
                *(order.violation_details or []),
            ]:
                if not isinstance(source, dict):
                    continue
                for key in ("expected_max", "max", "dash_per_1000_max"):
                    value = source.get(key)
                    if isinstance(value, (int, float)):
                        thresholds.append(float(value))
                for metric in source.get("target_metrics") or []:
                    if not isinstance(metric, dict):
                        continue
                    value = metric.get("expected_max")
                    if isinstance(value, (int, float)):
                        thresholds.append(float(value))
        return min(thresholds) if thresholds else DEFAULT_DASH_ARTIFACT_MAX_PER_1000

    def _inner_access_resolution_probe(
        self,
        order: ChapterRepairOrder,
        current_text: str,
        original_text: str,
        scene_index: int,
        context: dict[str, Any],
    ) -> dict[str, Any] | None:
        values = self._order_semantic_values(order)
        blob = " ".join(values)
        is_inner_access_order = (
            "head_hopping_count" in values
            or "head_hopping" in values
            or "inner access to non-pov" in blob
            or "non-pov inner access" in blob
        )
        if not is_inner_access_order:
            return None

        pov_name = self._pov_name_for_scene(scene_index, context)
        analysis = analyze_inner_access(current_text, pov_name=pov_name)
        if analysis["confirmed"]:
            return None

        return self._accepted_preflight_audit(
            current_text,
            original_text,
            warning="no_confirmed_rule_evidence",
            resolution_rule="narrative.inner_access",
            evidence_check={
                "rule_id": "narrative.inner_access",
                "scope": "pov_boundary",
                "subject_context": {"pov_character": pov_name},
                "confirmed_matches": 0,
                "ambiguous_matches": len(analysis["ambiguous"]),
                "ambiguous_evidence": analysis["ambiguous"][:8],
            },
        )

    @staticmethod
    def _accepted_preflight_audit(
        current_text: str,
        original_text: str,
        *,
        warning: str,
        resolution_rule: str,
        evidence_check: dict[str, Any],
    ) -> dict[str, Any]:
        text_hash = _compute_text_hash(current_text)
        return {
            "accepted": True,
            "failures": [],
            "warnings": [warning],
            "base_hash": text_hash,
            "result_hash": text_hash,
            "changed": False,
            "disposition": (
                "resolved_by_prior_change"
                if current_text != original_text
                else "no_confirmed_evidence"
            ),
            "resolution_rule": resolution_rule,
            "evidence_checks": [evidence_check],
            "timing_ms": 0,
            "executor_path": "order_resolution_preflight",
        }

    @staticmethod
    def _order_semantic_values(order: ChapterRepairOrder) -> set[str]:
        values = {
            str(order.repair_domain or "").lower(),
            str(order.reason or "").lower(),
            str(order.instruction or "").lower(),
        }
        for violation in order.violation_details or []:
            if not isinstance(violation, dict):
                continue
            values.update(
                str(violation.get(key) or "").lower()
                for key in (
                    "type",
                    "violation_type",
                    "semantic_type",
                    "metric",
                    "validator",
                    "action",
                    "rule_id",
                    "scope",
                )
                if violation.get(key)
            )
        return {value for value in values if value}

    @staticmethod
    def _pov_name_for_scene(scene_index: int, context: dict[str, Any]) -> str:
        scene_contracts = context.get("scene_contracts") or {}
        scene_contract = scene_contracts.get(scene_index) or scene_contracts.get(str(scene_index)) or {}
        if not scene_contract:
            scene_contract = context.get("scene_contract") or {}
        return str(
            scene_contract.get("pov")
            or scene_contract.get("pov_character")
            or scene_contract.get("pov_name")
            or ""
        ).strip()

    def _collect_batchable_local_patch_orders(
        self,
        orders: list[ChapterRepairOrder],
        start_index: int,
    ) -> list[ChapterRepairOrder]:
        batch: list[ChapterRepairOrder] = []
        for order in orders[start_index:]:
            if not self._is_batchable_local_quality_order(order):
                break
            batch.append(order)
        return batch

    @staticmethod
    def _is_batchable_local_quality_order(order: ChapterRepairOrder) -> bool:
        if order.repair_type != "local_patch" or order.priority == "critical":
            return False
        violations = ChapterRepairExecutor._build_violations_from_order(order)
        if not violations:
            return False
        v_types = {
            str(v.get("type") or v.get("violation_type") or "").lower()
            for v in violations
            if isinstance(v, dict)
        }
        if not v_types or not v_types.issubset(_BATCHABLE_LOCAL_QUALITY_TYPES):
            return False
        return not any(str(v.get("target_span") or "").strip() for v in violations)

    async def _execute_batchable_local_patch_orders(
        self,
        scene_index: int,
        orders: list[ChapterRepairOrder],
        current_text: str,
        original_text: str,
        scene_texts: dict[int, str],
        context: dict[str, Any],
        case_file: dict[str, Any] | None,
    ) -> tuple[str, list[tuple[str, str, str, dict[str, Any]]]]:
        batch_started = time.monotonic()
        synthetic = self._build_batch_order(orders)
        for order in orders:
            order.status = "running"

        try:
            new_text = await self._dispatch_order(
                synthetic,
                current_text,
                scene_index,
                scene_texts,
                context,
                case_file,
            )
        except Exception as exc:
            text_hash = _compute_text_hash(current_text)
            elapsed = int((time.monotonic() - batch_started) * 1000)
            results = []
            for order in orders:
                audit = {
                    "accepted": False,
                    "reason": "batch_exception",
                    "error": str(exc),
                    "base_hash": text_hash,
                    "result_hash": text_hash,
                    "changed": False,
                    "timing_ms": elapsed,
                    "executor_path": "batch_local_quality",
                    "batch_size": len(orders),
                    "batch_order_ids": [item.order_id for item in orders],
                }
                order.status = "failed"
                order.result_text_hash = text_hash
                order.repair_audit = audit
                results.append((order.order_id, "failed", text_hash, audit))
            return current_text, results

        elapsed = int((time.monotonic() - batch_started) * 1000)
        results: list[tuple[str, str, str, dict[str, Any]]] = []
        batch_hash = _compute_text_hash(new_text)
        for order in orders:
            audit = await self._audit_repair_result_async(order, current_text, new_text, scene_index, context, case_file)
            audit = {
                **audit,
                "timing_ms": elapsed,
                "executor_path": "batch_local_quality",
                "batch_size": len(orders),
                "batch_order_ids": [item.order_id for item in orders],
                "batch_primary_order_id": synthetic.order_id,
            }
            if not audit["accepted"] and self._is_non_blocking_no_effect_after_prior_change(
                order, audit, current_text, original_text,
            ):
                order.status = "skipped"
                audit = {
                    **audit,
                    "accepted": True,
                    "warnings": audit.get("failures", []),
                    "failures": [],
                    "disposition": "no_effect_after_prior_change",
                }
            else:
                order.status = "succeeded" if audit["accepted"] else "failed"
            order.result_text_hash = batch_hash
            order.repair_audit = audit
            results.append((order.order_id, order.status, batch_hash, audit))
        return new_text, results

    @staticmethod
    def _build_batch_order(orders: list[ChapterRepairOrder]) -> ChapterRepairOrder:
        first = orders[0]
        violations: list[dict] = []
        for order in orders:
            violations.extend(ChapterRepairExecutor._build_violations_from_order(order))
        priority_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        priority = min(
            (order.priority for order in orders),
            key=lambda value: priority_rank.get(value, 99),
        )
        return ChapterRepairOrder(
            order_id=f"batch_{first.order_id}",
            target_scenes=list(first.target_scenes),
            owner_scene=first.owner_scene,
            repair_type="local_patch",
            priority=priority,
            reason="\n".join(order.reason for order in orders if order.reason),
            instruction=(
                "Batch local quality repair. Resolve all listed quality findings in one coherent edit; "
                "avoid separate rewrites per finding."
            ),
            violation_details=violations,
            expected_after_repair={
                "preserve": [
                    item
                    for order in orders
                    for item in (order.expected_after_repair.get("preserve") or [])
                ],
                "remove": [
                    item
                    for order in orders
                    for item in (order.expected_after_repair.get("remove") or [])
                ],
            },
        )

    # 方案6 B1：_supersede_no_effect_failures_after_later_repairs 已删除。
    # failed 不再被假装成功，no_effect 的 failed 状态会触发方案6 Part C 的循环重试。

    def _final_text_improved_for_order(
        self,
        order: ChapterRepairOrder,
        original_text: str,
        final_text: str,
    ) -> bool:
        if self._fact_old_error_signatures_present(order, final_text):
            return False
        checks = self._target_span_checks(order, original_text, final_text)
        if checks:
            if any(check.get("passed") for check in checks):
                return True
            # A later duplicate order may reduce a repeated localized artifact
            # without removing every occurrence.  That is still genuine
            # progress for the earlier no-effect order; fact signatures remain
            # fail-closed above and must disappear completely.
            for check in checks:
                span = str(check.get("target_span") or "")
                if span and final_text.count(span) < original_text.count(span):
                    return True
            return False
        fact_check = self._fact_target_self_check(order, original_text, final_text)
        if fact_check and fact_check.get("supported"):
            return bool(fact_check.get("passed"))
        return final_text != original_text

    async def _retry_degraded_blueprint(
        self,
        plan: ChapterRepairPlan,
        scene_texts: dict[int, str],
        context: dict[str, Any],
        case_file: dict[str, Any] | None,
        attempt: int,
    ) -> ChapterRepairPlan | None:
        """方案6 A2：degraded 蓝图时开新会话重出图。

        参考 final_delta_repair_runtime._try_retry_blueprint 的模式，
        调用 RetryBlueprintRuntime.retry() 重新生成 work_units。
        RetryBlueprintRuntime.retry() 内部递归最多 MAX_RETRY_ROUNDS(=2) 轮，
        此处 attempt 是外层重试计数（0, 1, ...）。

        Args:
            plan: 当前 degraded 的修复计划。
            scene_texts: 场景正文映射。
            context: 执行上下文。
            case_file: 完整案卷上下文。
            attempt: 外层重试轮次（从 0 开始）。

        Returns:
            更新后的 ChapterRepairPlan（revision_blueprint 已替换为重出图结果）；
            若重出图失败或异常，返回 None。
        """
        # 提取 failed_issue_ids：从 plan.work_units 的 source_violation_ids 兜底
        failed_issue_ids: list[str] = []
        for wu in (plan.work_units or []):
            for vid in (wu.source_violation_ids or []):
                if vid and vid not in failed_issue_ids:
                    failed_issue_ids.append(vid)
        # 兜底：从 orders 的 violation_details 提取 issue_id
        if not failed_issue_ids:
            for order in plan.orders:
                violation_details = getattr(order, "violation_details", None) or []
                for v in violation_details:
                    if isinstance(v, dict):
                        issue_id = str(v.get("issue_id") or v.get("id") or "")
                        if issue_id and issue_id not in failed_issue_ids:
                            failed_issue_ids.append(issue_id)

        if not failed_issue_ids:
            _logger.warning(
                "ChapterRepairExecutor._retry_degraded_blueprint: "
                "无法提取 failed_issue_ids，跳过重出图 case_id=%s",
                plan.case_id,
            )
            return None

        # 构造 violations：优先从 case_file 的 review_case_file 提取，否则从 work_units 兜底
        violations: list[dict[str, Any]] = []
        review_case_file = (case_file or {}).get("review_case_file") or {}
        case_issues = review_case_file.get("issues") or review_case_file.get("violations") or []
        for issue in case_issues:
            if isinstance(issue, dict):
                v = dict(issue)
                if not v.get("issue_id"):
                    v["issue_id"] = v.get("id") or ""
                violations.append(v)
        # 循环#10：当 review_case_file 无 violations 时（L3 delta 路径常见），
        # 从 plan.orders 的 violation_details 提取完整上下文（detail/scene_index/evidence），
        # 而非只提供 {"issue_id": vid, "type": "unknown"}（上下文稀疏导致 LLM 无法产出有效 work_unit）。
        if not violations:
            for order in plan.orders:
                for v in (getattr(order, "violation_details", None) or []):
                    if isinstance(v, dict):
                        v_copy = dict(v)
                        if not v_copy.get("issue_id"):
                            v_copy["issue_id"] = v_copy.get("id") or v_copy.get("violation_id") or ""
                        if not v_copy.get("source_scene") and not v_copy.get("scene_index"):
                            if order.owner_scene is not None:
                                v_copy["scene_index"] = order.owner_scene
                        violations.append(v_copy)
        if not violations:
            for wu in (plan.work_units or []):
                for vid in (wu.source_violation_ids or []):
                    violations.append({"issue_id": vid, "type": "unknown"})

        retry_context = {
            "failed_issue_ids": failed_issue_ids,
            "failure_reason": "blueprint_degraded",
            "retry_round": attempt + 1,
            "violations": violations,
            "scene_texts": scene_texts,
            "case_file": case_file or {},
            "case_id": plan.case_id,
        }

        try:
            from app.services.fbi.retry_blueprint_runtime import RetryBlueprintRuntime
            runtime = RetryBlueprintRuntime()
            result = await runtime.retry(
                failed_issue_ids=failed_issue_ids,
                failure_reason="blueprint_degraded",
                retry_round=attempt + 1,
                context=retry_context,
            )
        except Exception as exc:
            _logger.warning(
                "ChapterRepairExecutor._retry_degraded_blueprint: RetryBlueprintRuntime 异常: %s",
                exc, exc_info=True,
            )
            return None

        if result.get("status") != "ready":
            _logger.warning(
                "ChapterRepairExecutor._retry_degraded_blueprint: retry status=%s reason=%s case_id=%s",
                result.get("status"), result.get("reason"), plan.case_id,
            )
            return None

        retry_work_units = result.get("work_units") or []
        if not retry_work_units:
            _logger.warning(
                "ChapterRepairExecutor._retry_degraded_blueprint: retry returned empty work_units case_id=%s",
                plan.case_id,
            )
            return None

        # 用重出图结果替换 plan 的 revision_blueprint
        updated_plan = plan.model_copy(deep=True)
        new_blueprint = updated_plan.revision_blueprint.model_copy(deep=True)
        new_blueprint.work_units = list(retry_work_units)
        new_blueprint.status = "ready"
        # 记录重出图来源，便于追溯
        new_blueprint.completion_summary = {
            **(new_blueprint.completion_summary or {}),
            "blueprint_source": f"fbi_retry_blueprint[degraded:attempt:{attempt + 1}]",
            "retry_trace": result.get("trace") or {},
        }
        updated_plan.revision_blueprint = new_blueprint
        updated_plan.work_units = list(retry_work_units)
        return updated_plan

    @staticmethod
    def _should_retry_for_protection(order: ChapterRepairOrder, audit: dict[str, Any]) -> bool:
        if any(
            isinstance(command, dict)
            and command.get("operation") == "llm_creative_rewrite"
            for command in (order.tool_commands or [])
        ):
            return False
        if order.repair_type not in {
            "scene_rewrite",
            "contract_completion_patch",
            "local_patch",
            "cross_scene_alignment",
        }:
            return False
        failures = set((audit or {}).get("failures") or [])
        if "protected_obligation_removed" not in failures:
            return False
        removed_terms = (audit or {}).get("removed_protected_terms") or []
        return bool(removed_terms)

    async def _retry_for_protection(
        self,
        order: ChapterRepairOrder,
        current_text: str,
        rejected_text: str,
        rejected_audit: dict[str, Any],
        scene_index: int,
        scene_texts: dict[int, str] | None,
        context: dict[str, Any],
        case_file: dict[str, Any] | None = None,
    ) -> tuple[str | None, dict[str, Any] | None]:
        removed_terms = [
            str(term)
            for term in (rejected_audit.get("removed_protected_terms") or [])
            if str(term).strip()
        ]
        if not removed_terms:
            return None, None

        protection_retry = {
            "reason": "protected_obligation_removed",
            "removed_terms": removed_terms[:20],
            "rejected_result_hash": rejected_audit.get("result_hash") or _compute_text_hash(rejected_text),
        }
        if order.repair_type == "local_patch":
            retry_text = await self._repair_local_patch(
                order,
                current_text,
                scene_index,
                context,
                case_file,
                protection_retry=protection_retry,
            )
        elif order.repair_type == "cross_scene_alignment":
            retry_scene_texts = scene_texts or (case_file or {}).get("all_scene_texts") or {scene_index: current_text}
            retry_text = await self._repair_cross_scene_alignment(
                order,
                current_text,
                scene_index,
                retry_scene_texts,
                context,
                case_file,
                protection_retry=protection_retry,
            )
        else:
            retry_text = await self._repair_contract_completion_patch(
                order,
                current_text,
                scene_index,
                context,
                case_file,
                protection_retry=protection_retry,
            )
        retry_audit = await self._audit_repair_result_async(order, current_text, retry_text, scene_index, context, case_file)
        retry_audit = {
            **retry_audit,
            "retry": {
                "reason": "protected_obligation_removed",
                "attempts": 1,
                "previous_failures": rejected_audit.get("failures", []),
                "previous_removed_protected_terms": removed_terms[:20],
                "previous_result_hash": rejected_audit.get("result_hash"),
            },
        }
        if retry_audit["accepted"]:
            return retry_text, retry_audit

        return None, {
            **rejected_audit,
            "retry": {
                "reason": "protected_obligation_removed",
                "attempts": 1,
                "retry_failures": retry_audit.get("failures", []),
                "retry_removed_protected_terms": retry_audit.get("removed_protected_terms", []),
                "retry_result_hash": retry_audit.get("result_hash"),
            },
        }

    @staticmethod
    def _merge_preserve_terms_for_retry(
        preserve: Any,
        protection_retry: dict[str, Any] | None,
    ) -> list[Any]:
        items: list[Any] = []
        if isinstance(preserve, list):
            items.extend(preserve)
        elif preserve:
            items.append(preserve)
        if protection_retry:
            items.extend(protection_retry.get("removed_terms") or [])

        merged: list[Any] = []
        seen: set[str] = set()
        for item in items:
            key = str(item)
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(item)
        return merged

    @staticmethod
    def _scene_repair_protocol_context(
        order: ChapterRepairOrder,
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Return the protocol envelope shared by every SceneRepairer route."""

        return {
            "repair_brief": order.repair_brief or {},
            "repair_lane": order.repair_lane,
            "repair_strength": order.repair_strength,
            "allowed_max_strength": order.allowed_max_strength,
            "candidate_attempt_budget": order.candidate_attempt_budget,
            "repair_attempt": context.get("repair_attempt", {}),
            "force_change": bool(context.get("force_change")),
            "workbench_smart_repair": bool(context.get("workbench_smart_repair")),
            "style_context": context.get("style_context") or {},
            "style_profile": context.get("style_profile") or {},
            "style_prompt": context.get("style_prompt", ""),
            "style_embedding": context.get("style_embedding") or {},
            "persona_card": context.get("persona_card") or {},
        }

    def _reconcile_failed_orders_after_all_groups(
        self,
        orders_by_id: dict[str, ChapterRepairOrder],
        original_scene_texts: dict[int, str],
        updated_texts: dict[int, str],
    ) -> None:
        for order in orders_by_id.values():
            if order.status != "failed":
                continue
            audit = order.repair_audit or {}
            failures = set(audit.get("failures") or [])
            if not failures or not failures.issubset(_NO_EFFECT_AFTER_PRIOR_CHANGE_FAILURES):
                continue
            scene_index = order.owner_scene
            if scene_index is None and order.target_scenes:
                scene_index = order.target_scenes[0]
            if scene_index is None:
                continue
            original_text = original_scene_texts.get(scene_index, "")
            final_text = updated_texts.get(scene_index, "")
            if not original_text or not final_text or final_text == original_text:
                continue
            if not self._final_text_improved_for_order(order, original_text, final_text):
                continue
            final_hash = _compute_text_hash(final_text)
            order.status = "skipped"
            order.result_text_hash = final_hash
            order.repair_audit = {
                **audit,
                "accepted": True,
                "warnings": list(failures),
                "failures": [],
                "disposition": "superseded_by_later_scene_change",
                "final_hash": final_hash,
            }

    def _is_non_blocking_no_effect_after_prior_change(
        self,
        order: ChapterRepairOrder,
        audit: dict[str, Any],
        current_text: str,
        original_text: str,
    ) -> bool:
        failures = set(audit.get("failures") or [])
        if not (
            bool(failures)
            and failures.issubset(_NO_EFFECT_AFTER_PRIOR_CHANGE_FAILURES)
            and current_text != original_text
        ):
            return False
        return self._final_text_improved_for_order(order, original_text, current_text)

    async def _verify_b_class_metric(
        self,
        metric: str,
        before_text: str,
        after_text: str,
        scene_contract: dict | None,
    ) -> dict[str, Any] | None:
        """方案 30：B 类 metric 的后置校验改用 LLM 判定。

        替代硬编码关键词校验（_hook_weakness_score / _QUESTION_KEYWORDS），
        LLM 失败时降级为 None（交回确定性校验兜底）。
        """
        metric_lower = (metric or "").lower()
        if metric_lower not in B_CLASS_METRICS:
            return None
        try:
            from app.agents.llm_semantic_judge_agent import LlmSemanticJudgeAgent

            judge = LlmSemanticJudgeAgent()
            result = await asyncio.wait_for(
                judge.judge_single_metric(metric_lower, after_text, scene_contract),
                timeout=120,
            )
            return {
                "supported": True,
                "passed": bool(result.get("passed", True)),
                "metric": metric_lower,
                "before": 0,
                "after": 0 if result.get("passed") else 1,
                "direction": "decrease",
                "source": "llm_semantic_judge",
                "evidence": result.get("evidence", []),
                "reason": result.get("reason", "llm_verified"),
            }
        except asyncio.TimeoutError:
            _logger.warning(
                "ChapterRepairExecutor: LLM verify timeout for metric=%s, degrade to deterministic",
                metric_lower,
            )
            return None
        except Exception as exc:
            _logger.warning(
                "ChapterRepairExecutor: LLM verify failed for metric=%s: %s, degrade to deterministic",
                metric_lower,
                exc,
            )
            return None

    async def _audit_repair_result_async(
        self,
        order: ChapterRepairOrder,
        before_text: str,
        after_text: str,
        scene_index: int,
        context: dict[str, Any],
        case_file: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Runtime audit with optional semantic verification for B metrics."""
        audit = self._audit_repair_result(
            order,
            before_text,
            after_text,
            scene_index,
            context,
            case_file,
        )
        brief_metrics = (order.repair_brief or {}).get("target_metrics") or []
        b_class_metric_name = next(
            (
                str(item.get("metric") or "").lower()
                for item in brief_metrics
                if isinstance(item, dict)
                and str(item.get("metric") or "").lower() in B_CLASS_METRICS
            ),
            "",
        )
        if (
            not b_class_metric_name
            or before_text == after_text
            or not bool(context.get("_structured_execution_required"))
        ):
            return audit
        scene_contract = (
            context.get("scene_contracts", {}).get(scene_index, {})
            if isinstance(context, dict)
            else {}
        )
        llm_check = await self._verify_b_class_metric(
            b_class_metric_name,
            before_text,
            after_text,
            scene_contract,
        )
        if llm_check is None:
            return audit

        audit["target_self_check"] = llm_check
        failures = [
            item
            for item in (audit.get("failures") or [])
            if item != "target_metric_not_improved"
        ]
        if llm_check.get("supported") and not llm_check.get("passed"):
            failures.append("target_metric_not_improved")
        audit["failures"] = failures
        audit["accepted"] = not failures
        if audit.get("candidate_attempts"):
            audit["candidate_attempts"][0]["target_check"] = llm_check
            audit["candidate_attempts"][0]["decision"] = (
                "accepted" if not failures else "rejected"
            )
            audit["candidate_attempts"][0]["failures"] = list(failures)
        if failures:
            audit["failure_stage"] = "target_metric_not_improved"
            audit["manual_reason"] = "修复后目标指标未改善，需人工调整"
        else:
            audit["failure_stage"] = "succeeded"
            audit["manual_reason"] = ""
        return audit

    def _audit_repair_result(
        self,
        order: ChapterRepairOrder,
        before_text: str,
        after_text: str,
        scene_index: int,
        context: dict[str, Any],
        case_file: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Deterministic accept gate for a repair candidate.

        This is intentionally narrower than the full recheck stage. It only
        rejects candidates that are obviously unusable: empty output, no edit,
        deleted protected obligations, or target-span issues that did not move.
        """
        case_file = case_file or {}
        before_text = before_text or ""
        after_text = after_text or ""
        protected_groups = self._protected_obligation_groups(order, before_text, scene_index, context, case_file)
        removed_audit = self._removed_protected_obligations(protected_groups, before_text, after_text)
        removed_terms = removed_audit["removed"]
        weak_removed_terms = removed_audit["weak_removed"]
        target_span_checks = self._target_span_checks(order, before_text, after_text)
        dash_density_check = self._dash_density_check(order, before_text, after_text)
        # 循环#10：传入 scene_contract，修复 goal_present 本地自检假通过。
        # _validator_metric_self_check → adapter.compare → _measure_agent_skill_validator_metric
        # 需要 scene_contract 来计算 goal_terms；缺 scene_contract 时 goal_terms=[],
        # goal_present 仅靠宽泛正则（_has_intent_signal 匹配"不能/为了"等常见词），
        # 几乎任何中文正文都返回 True，导致本地判通过但最终 recheck 判失败。
        scene_contract_for_check = (
            context.get("scene_contracts", {}).get(scene_index, {})
            if isinstance(context, dict) else {}
        )
        target_self_check = self._target_self_check(
            order,
            before_text,
            after_text,
            dash_density_check=dash_density_check,
            target_span_checks=target_span_checks,
            pov_name=self._pov_name_for_scene(scene_index, context),
            validator_snapshot=context.get("validator_snapshot") if isinstance(context, dict) else None,
            scene_contract=scene_contract_for_check,
        )
        goal_acceptance_checks = self._goal_acceptance_checks(
            order,
            before_text,
            after_text,
            target_self_check=target_self_check,
            validator_snapshot=context.get("validator_snapshot") if isinstance(context, dict) else None,
            scene_contract=scene_contract_for_check,
        )
        prose_integrity_regressions = self._new_blocking_prose_lint_regressions(
            before_text,
            after_text,
        )

        failures: list[str] = []
        diagnostic_instruction_leaks = _new_diagnostic_instruction_leaks(
            order, before_text, after_text
        )
        if not after_text.strip():
            failures.append("empty_result")
        if before_text == after_text and order.repair_type != "manual_review":
            # 通用：当 FBI 的违规描述本身含接受语言（"是合理的"、"可不修改"等）时，
            # 表明当前正文可接受，SceneRepairer 返回原文是正确行为，不计为失败。
            if _order_violation_accepts_no_change(order):
                _logger.info(
                    "ChapterRepairExecutor: order %s no change accepted (violation description indicates text is acceptable)",
                    order.order_id,
                )
            else:
                failures.append("unchanged_result")
        if removed_terms:
            failures.append("protected_obligation_removed")
        if diagnostic_instruction_leaks:
            failures.append("diagnostic_instruction_leakage")
        if prose_integrity_regressions:
            failures.append("prose_integrity_regression")
        failed_span_checks = [check for check in target_span_checks if not check["passed"]]
        if failed_span_checks:
            failures.append("target_span_not_improved")
        if dash_density_check and not dash_density_check["passed"]:
            failures.append("dash_density_not_reduced_to_target")
        if target_self_check.get("supported"):
            if (
                not target_self_check.get("passed")
                and target_self_check.get("source")
                not in {"target_span_checks", "dash_density_check"}
            ):
                failures.append("target_metric_not_improved")
        elif (
            context.get("_structured_execution_required")
            and self._order_requires_supported_acceptance(order)
        ):
            failures.append("unsupported_target_acceptance")
        if any(not check.get("passed") for check in goal_acceptance_checks):
            failures.append("goal_acceptance_failed")
        if any(
            not check.get("supported") and check.get("blocks_commit")
            for check in goal_acceptance_checks
        ):
            failures.append("unsupported_goal_acceptance")

        # 后置自检：local_patch 修复非明喻类问题时，检查是否新引入了明喻结构。
        # 根因：第二轮 local_patch 修复 fact_conflict 等问题时，LLM 容易引入新的
        # "像...一样"明喻句式，导致 recheck 时 ai_simile_overuse 复发。这里做轻量
        # 确定性检查，不调用 LLM，不影响性能。
        if order.repair_type == "local_patch" and before_text != after_text:
            violation_types = _order_violation_types(order)
            is_simile_repair = bool(violation_types & {"ai_simile_overuse", "simile_overuse"})
            if is_simile_repair:
                # 通用修复 S-4：ai_simile_overuse 修复时，检查"像"字密度是否下降
                # 根因：原逻辑对 ai_simile_overuse 修复跳过自检，导致 LLM 即使没有
                #   降低"像"字密度也能通过后置自检，修复循环无法收敛。
                # 修复：检查"像"字数量是否减少；若未减少或反而增加，判失败。
                # 通用性：所有密度类问题的修复方向都应被后置自检验证。
                # 循环#11增强：只减少 1 个不够——检测阈值要求"像"字密度 < 3.0/千字
                #   或 < 5.0/千字（high severity）。LLM 每次只减 1 个时 3 次 attempts
                #   仍无法降到阈值以下。要求至少减少 30% 才算有效修复。
                before_word_count = (before_text or "").count(_SIMILE_WORD)
                after_word_count = (after_text or "").count(_SIMILE_WORD)
                if after_word_count >= before_word_count and before_word_count > 0:
                    failures.append("simile_density_not_reduced")
                    _logger.info(
                        "ChapterRepairExecutor: order %s simile word count not reduced (%d -> %d) during ai_simile_overuse repair",
                        order.order_id,
                        before_word_count,
                        after_word_count,
                    )
                elif before_word_count > 0:
                    # 循环#11：要求"像"字数量至少减少 30%，否则判密度下降不足
                    # 根因：LLM 每次只减 1 个"像"字就能通过自检，但 13→12 仍 >= 5.0/千字（high）。
                    #   要求减少 30% 迫使 LLM 系统性处理多个位置，而非只改 target_span 一处。
                    reduction_ratio = (before_word_count - after_word_count) / before_word_count
                    if reduction_ratio < 0.3 and after_word_count >= 3:
                        failures.append("simile_density_insufficient_reduction")
                        _logger.info(
                            "ChapterRepairExecutor: order %s simile word count insufficient reduction (%.0f%%, %d -> %d), need >= 30%% during ai_simile_overuse repair",
                            order.order_id,
                            reduction_ratio * 100,
                            before_word_count,
                            after_word_count,
                        )
            else:
                new_similes = _count_new_simile_structures(before_text, after_text)
                if new_similes > 0:
                    failures.append("introduced_new_simile")
                    _logger.info(
                        "ChapterRepairExecutor: order %s introduced %d new simile structure(s) during local_patch",
                        order.order_id,
                        new_similes,
                    )

        # 提取蓝图失败原因到 audit（9.1.4 增强）
        blueprint_failure_stage = "legacy"
        route_failure_reason = ""
        route_metric = ""
        route_family = ""
        brief = order.repair_brief or {}
        revision_bp = brief.get("revision_blueprint") if isinstance(brief, dict) else None
        if isinstance(revision_bp, dict):
            rejection = str(revision_bp.get("rejection_reason") or "")
            route_failure_reason = rejection
            route_family = str(revision_bp.get("repair_family") or "")
            bp_status = str(revision_bp.get("status") or "")
            # Phase U-G: 细化 failure_stage，让前端能区分为什么没有自动修
            if bp_status == "blueprint_incomplete":
                blueprint_failure_stage = "blueprint_incomplete"
            elif "route_builder_failed" in rejection:
                blueprint_failure_stage = "route_builder"
            elif rejection:
                blueprint_failure_stage = "protocol_gate"
            elif not revision_bp.get("tool_blueprint"):
                blueprint_failure_stage = "blueprint_not_generated"
            tool_bp = revision_bp.get("tool_blueprint") or {}
            if isinstance(tool_bp, dict):
                route_metric = str(tool_bp.get("metric") or "")

        # Phase U-G: 综合判定 failure_stage 和 manual_reason
        failure_stage = blueprint_failure_stage
        manual_reason = ""
        if not failures:
            failure_stage = "succeeded"
        elif "protected_obligation_removed" in failures:
            failure_stage = "protection_check_failed"
            manual_reason = "修复方案移除了受保护的事实或义务，需人工确认"
        elif "unsupported_target_acceptance" in failures or "unsupported_goal_acceptance" in failures:
            failure_stage = "unsupported_acceptance_check"
            manual_reason = "硬修复目标缺少可执行的验收证据，已按 fail-closed 拒绝候选"
        elif "goal_acceptance_failed" in failures:
            failure_stage = "goal_acceptance_failed"
            manual_reason = "至少一个修复目标没有通过自己的验收检查"
        elif "prose_integrity_regression" in failures:
            failure_stage = "prose_integrity_check_failed"
            manual_reason = "修复结果引入了新的硬语病，已拒绝并回滚"
        elif "target_metric_not_improved" in failures:
            failure_stage = "target_metric_not_improved"
            manual_reason = "修复后目标指标未改善，需人工调整"
        elif "unchanged_result" in failures:
            failure_stage = "tool_execution_no_effect"
            manual_reason = "工具执行后正文未变化，可能 old_text 未匹配"
        elif "empty_result" in failures:
            failure_stage = "tool_execution_empty"
            manual_reason = "修复后正文为空，工具执行异常"
        elif "diagnostic_instruction_leakage" in failures:
            failure_stage = "narrative_safety_check_failed"
            manual_reason = "修复结果把质检说明写入正文，已拒绝并回滚"
        elif blueprint_failure_stage == "blueprint_not_generated":
            manual_reason = "审查蓝图官未生成可执行补丁，需人工撰写"
        elif blueprint_failure_stage == "blueprint_incomplete":
            manual_reason = "蓝图缺少 replacement，需人工补全"
        elif blueprint_failure_stage == "route_builder":
            manual_reason = "路由表未覆盖该 metric，需人工修复"
        elif blueprint_failure_stage == "protocol_gate":
            manual_reason = "蓝图协议校验未通过，需人工修正"
        elif "introduced_new_simile" in failures:
            failure_stage = "introduced_new_simile"
            manual_reason = "local_patch 修复时引入了新的明喻结构，需人工复验"
        else:
            failure_stage = "postcheck_failed"
            manual_reason = "修复后验收未通过，需人工复验"

        return {
            "accepted": not failures,
            "failures": failures,
            "blueprint_failure_stage": blueprint_failure_stage,
            # Phase U-G: 前端展示字段
            "failure_stage": failure_stage,
            "manual_reason": manual_reason,
            "route_failure_reason": route_failure_reason,
            "route_metric": route_metric,
            "route_family": route_family,
            "base_hash": _compute_text_hash(before_text),
            "result_hash": _compute_text_hash(after_text),
            "base_length": len(before_text),
            "result_length": len(after_text),
            "changed": before_text != after_text,
            "protected_terms_checked": sum(len(group["terms"]) for group in protected_groups),
            "protected_obligations_checked": len(protected_groups),
            "removed_protected_terms": removed_terms[:20],
            "weak_removed_protected_terms": weak_removed_terms[:20],
            "diagnostic_instruction_leaks": diagnostic_instruction_leaks[:5],
            "prose_integrity_regressions": prose_integrity_regressions[:10],
            "target_span_checks": target_span_checks[:10],
            "dash_density_check": dash_density_check,
            "target_self_check": target_self_check,
            "goal_acceptance_checks": goal_acceptance_checks,
            "failed_goal_ids": [
                str(check.get("goal_id") or "")
                for check in goal_acceptance_checks
                if not check.get("passed") and check.get("goal_id")
            ],
            "repair_brief": order.repair_brief or {},
            "repair_lane": order.repair_lane,
            "repair_strength": order.repair_strength,
            "allowed_max_strength": order.allowed_max_strength,
            "candidate_attempt_budget": order.candidate_attempt_budget,
            "candidate_attempts": [{
                "attempt": 1,
                "lane": order.repair_lane,
                "strength": order.repair_strength,
                "target_check": target_self_check,
                "protection_check": {
                    "passed": not bool(removed_terms),
                    "removed_protected_terms": removed_terms[:20],
                },
                "decision": "accepted" if not failures else "rejected",
                "failures": list(failures),
            }],
        }

    @staticmethod
    def _new_blocking_prose_lint_regressions(
        before_text: str,
        after_text: str,
    ) -> list[dict[str, Any]]:
        """Reject repair candidates that introduce a new hard prose defect.

        The full workflow still performs its canonical recheck. This bounded
        comparison prevents a smart-repair candidate from replacing sound
        prose with a grammatically broken sentence before that recheck runs.
        Existing defects are not attributed to the current repair order.
        """
        if not after_text or before_text == after_text:
            return []

        from collections import Counter
        from app.services.prose_lint_service import ProseLintService

        def _blocking_signatures(text: str) -> Counter[tuple[str, str]]:
            return Counter(
                (finding.type, finding.target_span)
                for finding in ProseLintService().lint(text)
                if finding.blocks_commit
            )

        before = _blocking_signatures(before_text)
        after = _blocking_signatures(after_text)
        regressions: list[dict[str, Any]] = []
        for (finding_type, target_span), count in after.items():
            added = count - before.get((finding_type, target_span), 0)
            if added <= 0:
                continue
            regressions.append({
                "type": finding_type,
                "target_span": target_span,
                "new_count": added,
            })
        return regressions

    @staticmethod
    def _order_requires_supported_acceptance(order: ChapterRepairOrder) -> bool:
        goals = (order.repair_brief or {}).get("repair_goals") or []
        return any(
            isinstance(goal, dict) and goal.get("blocks_commit") is True
            for goal in goals
        )

    @staticmethod
    def _goal_acceptance_checks(
        order: ChapterRepairOrder,
        before_text: str,
        after_text: str,
        *,
        target_self_check: dict[str, Any],
        validator_snapshot: dict[str, Any] | None = None,
        scene_contract: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        goals = [
            goal
            for goal in ((order.repair_brief or {}).get("repair_goals") or [])
            if isinstance(goal, dict)
        ]
        checks: list[dict[str, Any]] = []
        for goal in goals:
            criteria = [
                criterion
                for criterion in (goal.get("acceptance_criteria") or [])
                if isinstance(criterion, dict)
            ]
            metrics = [
                {
                    "metric": criterion.get("metric"),
                    "operator": criterion.get("operator"),
                    "expected": criterion.get("expected"),
                    "source": criterion.get("source"),
                }
                for criterion in criteria
                if criterion.get("metric")
            ]
            check = ChapterRepairExecutor._validator_metric_self_check(
                metrics,
                before_text,
                after_text,
                validator_snapshot=validator_snapshot,
                scene_contract=scene_contract,
            ) if metrics else None
            if (
                (
                    check is None
                    or (
                        not check.get("supported")
                        and target_self_check.get("deferred_to_recheck")
                    )
                )
                and len(goals) == 1
                and target_self_check.get("supported")
            ):
                check = dict(target_self_check)
                check["source"] = f"goal_projection:{target_self_check.get('source', '')}"
            if check is None:
                check = {
                    "supported": False,
                    "passed": False,
                    "metric": metrics[0].get("metric") if metrics else "",
                    "source": "unsupported_goal_acceptance",
                }
            supported = bool(check.get("supported"))
            passed = bool(check.get("passed")) if supported else False
            checks.append({
                **check,
                "goal_id": str(goal.get("goal_id") or ""),
                "authority_ref": str(goal.get("authority_ref") or ""),
                "blocks_commit": bool(goal.get("blocks_commit", True)),
                "supported": supported,
                "passed": passed,
                "acceptance_criteria": criteria,
            })
        return checks

    @staticmethod
    def _target_self_check(
        order: ChapterRepairOrder,
        before_text: str,
        after_text: str,
        *,
        dash_density_check: dict[str, Any] | None,
        target_span_checks: list[dict[str, Any]],
        pov_name: str = "",
        validator_snapshot: dict[str, Any] | None = None,
        scene_contract: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        brief = order.repair_brief or {}
        metrics = [
            item for item in (brief.get("target_metrics") or [])
            if isinstance(item, dict)
        ]
        metric_names = {
            str(item.get("metric") or "").lower()
            for item in metrics
            if item.get("metric")
        }
        fact_check = ChapterRepairExecutor._fact_target_self_check(order, before_text, after_text)
        deferred_fact_check: dict[str, Any] | None = None
        if fact_check is not None:
            if not fact_check.get("passed"):
                tool_check = ChapterRepairExecutor._tool_postcondition_self_check(order, before_text, after_text)
                if tool_check is not None and tool_check.get("passed"):
                    tool_check["superseded_check"] = fact_check
                    return tool_check
            if fact_check.get("supported"):
                return fact_check
            # A semantic bridge intentionally has no literal required or
            # forbidden span.  Keep the diagnostic trace, then allow a
            # registered consistency metric to defer acceptance to the full
            # semantic recheck below.  Changed/empty/protection/prose checks
            # remain enforced by the surrounding local audit.
            deferred_fact_check = fact_check
        tool_check = ChapterRepairExecutor._tool_postcondition_self_check(order, before_text, after_text)
        if tool_check is not None:
            return tool_check
        if dash_density_check:
            return {
                "supported": True,
                "passed": bool(dash_density_check.get("passed")),
                "metric": "dash_per_1000",
                "before": dash_density_check.get("before_count"),
                "after": dash_density_check.get("after_count"),
                "expected_max": dash_density_check.get("target_count"),
                "direction": "decrease",
                "source": "dash_density_check",
            }
        if target_span_checks:
            failed = [check for check in target_span_checks if not check.get("passed")]
            return {
                "supported": True,
                "passed": not failed,
                "metric": "target_span",
                "before": len(target_span_checks),
                "after": len(failed),
                "direction": "decrease",
                "source": "target_span_checks",
            }
        validator_check = ChapterRepairExecutor._validator_metric_self_check(
            metrics,
            before_text,
            after_text,
            validator_snapshot=validator_snapshot,
            scene_contract=scene_contract,
        )
        if validator_check is not None and validator_check.get("supported"):
            return validator_check
        # 循环#10：commit_health 类 metric 的确定性后检。
        # internal_scene_separator 走 scene_rewrite LLM 路径，但本地自检原来只检查
        # before!=after（unsupported_metric fallback），不验证分隔符是否被删除，
        # 导致本地判通过但最终 recheck 仍检测到分隔符 → blocking。
        # 增加正则确定性后检，与 ChapterCommitHealthChecker 使用同一正则。
        if metric_names & {"internal_scene_separator"}:
            import re as _re
            _SEP_RE = _re.compile(r"(?m)^\s*(?:-{3,}|\*{3,}|#{2,}\s+\S.*)\s*$")
            before_count = len(_SEP_RE.findall(before_text))
            after_count = len(_SEP_RE.findall(after_text))
            return {
                "supported": True,
                "passed": after_count < before_count or after_count == 0,
                "metric": "internal_scene_separator",
                "before": before_count,
                "after": after_count,
                "direction": "decrease",
                "source": "commit_health_separator_check",
            }
        if metric_names & {"head_hopping_count", "head_hopping", "single_pov_per_scene", "pov_consistency"}:
            before_analysis = analyze_inner_access(before_text, pov_name=pov_name)
            after_analysis = analyze_inner_access(after_text, pov_name=pov_name)
            before_score = len(before_analysis.get("confirmed") or [])
            after_score = len(after_analysis.get("confirmed") or [])
            return {
                "supported": True,
                "passed": after_score < before_score or before_score == 0,
                "metric": sorted(metric_names)[0],
                "before": before_score,
                "after": after_score,
                "direction": "decrease",
                "source": "pov_inner_access_score",
            }
        if metric_names & {"voice_fingerprint", "character_voice"}:
            before_score = ChapterRepairExecutor._generic_voice_package_score(before_text)
            after_score = ChapterRepairExecutor._generic_voice_package_score(after_text)
            return {
                "supported": True,
                "passed": after_score < before_score or (before_text != after_text and before_score == 0),
                "metric": sorted(metric_names)[0],
                "before": before_score,
                "after": after_score,
                "direction": "decrease",
                "source": "generic_voice_package_score",
            }
        if metric_names & {"weak_opening_hook", "weak_chapter_end_hook"}:
            before_score = ChapterRepairExecutor._hook_weakness_score(before_text)
            after_score = ChapterRepairExecutor._hook_weakness_score(after_text)
            return {
                "supported": True,
                "passed": after_score < before_score or (before_text != after_text and after_score == 0),
                "metric": sorted(metric_names)[0],
                "before": before_score,
                "after": after_score,
                "direction": "decrease",
                "source": "hook_weakness_score",
            }
        # Creative contract-completion metrics cannot be decided by the
        # synchronous local audit.  They are nevertheless registered metrics,
        # and every accepted candidate is still forced through the downstream
        # semantic recheck before the DAG can commit.  Preserve fail-closed
        # behaviour for unknown hard metrics while allowing a changed, locally
        # safe candidate to reach that mandatory recheck.  This branch follows
        # all available deterministic checks so those remain authoritative.
        try:
            from app.services.validator_protocol import ValidatorProtocolAdapter

            deferred_metrics = sorted(
                metric
                for metric in metric_names
                if ValidatorProtocolAdapter.requires_semantic_recheck(metric)
            )
        except Exception:
            deferred_metrics = []
        if deferred_metrics:
            result = {
                "supported": True,
                "passed": before_text != after_text,
                "metric": deferred_metrics[0],
                "before": _compute_text_hash(before_text),
                "after": _compute_text_hash(after_text),
                "direction": "resolve",
                "source": "mandatory_semantic_recheck",
                "deferred_to_recheck": True,
                "deferred_metrics": deferred_metrics,
            }
            if deferred_fact_check is not None:
                result["superseded_check"] = deferred_fact_check
            return result
        return {
            "supported": False,
            "passed": False,
            "metric": sorted(metric_names)[0] if metric_names else "",
            "source": "unsupported_metric",
        }

    @staticmethod
    def _validator_metric_self_check(
        metrics: list[dict[str, Any]],
        before_text: str,
        after_text: str,
        *,
        validator_snapshot: dict[str, Any] | None = None,
        scene_contract: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        metric_names = [
            str(item.get("metric") or "").lower()
            for item in metrics
            if item.get("metric")
        ]
        if not metric_names:
            return None

        for metric_name in metric_names:
            try:
                from app.services.validator_protocol import ValidatorProtocolAdapter

                adapter = ValidatorProtocolAdapter()
                validator_id = adapter.validator_for_metric(metric_name)
                if not validator_id:
                    continue
                expected = ChapterRepairExecutor._metric_expected(metrics, metric_name)
                # 循环#10：传入 scene_contract，让 _measure_agent_skill_validator_metric
                # 能计算 goal_terms（goal_present 检测需要 scene_contract.goal 字段）。
                context: dict[str, Any] = {}
                if validator_snapshot:
                    context["validator_snapshot"] = validator_snapshot
                if scene_contract:
                    context["scene_contract"] = scene_contract
                return adapter.compare(
                    metric_name,
                    before_text,
                    after_text,
                    validator=validator_id,
                    expected=expected,
                    context=context if context else None,
                )
            except Exception:
                return None
        return None

    @staticmethod
    def _metric_expected(metrics: list[dict[str, Any]], metric_name: str) -> Any:
        for item in metrics:
            if str(item.get("metric") or "").lower() != metric_name:
                continue
            for key in ("expected", "expected_max", "expected_min", "limit", "max", "min"):
                value = item.get(key)
                if value not in (None, ""):
                    return value
        return None

    @staticmethod
    def _metric_progress_passed(
        metric_name: str,
        before_value: Any,
        after_value: Any,
        expected: Any,
        *,
        direction: str = "decrease",
    ) -> bool:
        try:
            before = float(before_value or 0)
            after = float(after_value or 0)
        except Exception:
            return False
        expected_number: float | None = None
        try:
            if expected not in (None, ""):
                expected_number = float(expected)
        except Exception:
            expected_number = None
        if direction == "increase":
            if expected_number is not None and after >= expected_number:
                return True
            return after > before
        if expected_number is not None and after <= expected_number:
            return True
        return after < before or (metric_name == "abrupt_shift_count" and after == 0)

    @staticmethod
    def _tool_postcondition_self_check(
        order: ChapterRepairOrder,
        before_text: str,
        after_text: str,
    ) -> dict[str, Any] | None:
        """Validate deterministic tool repairs by their declared span contract."""
        command_checks: list[dict[str, Any]] = []
        for raw_command in order.tool_commands or []:
            if not isinstance(raw_command, dict):
                continue
            postconditions = raw_command.get("postconditions")
            if not isinstance(postconditions, dict):
                continue
            required_spans = [
                str(item).strip()
                for item in (postconditions.get("required_spans") or [])
                if str(item).strip()
            ]
            forbidden_spans = [
                str(item).strip()
                for item in (postconditions.get("forbidden_spans") or [])
                if str(item).strip()
            ]
            if not required_spans and not forbidden_spans:
                continue

            missing_required = [span for span in required_spans if span not in after_text]
            lingering_forbidden = [span for span in forbidden_spans if span in after_text]
            command_checks.append({
                "command_id": raw_command.get("command_id", ""),
                "operation": raw_command.get("operation", ""),
                "required_count": len(required_spans),
                "forbidden_count": len(forbidden_spans),
                "missing_required": missing_required[:8],
                "lingering_forbidden": lingering_forbidden[:8],
                "passed": not missing_required and not lingering_forbidden,
            })

        if not command_checks:
            return None

        failed = [item for item in command_checks if not item.get("passed")]
        return {
            "supported": True,
            "passed": before_text != after_text and not failed,
            "metric": "tool_postconditions",
            "before": _compute_text_hash(before_text),
            "after": _compute_text_hash(after_text),
            "direction": "satisfy_declared_postconditions",
            "source": "tool_command_postconditions",
            "checks": command_checks[:12],
        }

    @staticmethod
    def _fact_target_self_check(
        order: ChapterRepairOrder,
        before_text: str,
        after_text: str,
    ) -> dict[str, Any] | None:
        brief = order.repair_brief or {}
        fact_goal = brief.get("fact_repair_goal") or {}
        metrics = [
            item for item in (brief.get("target_metrics") or [])
            if isinstance(item, dict)
        ]
        metric_names = {
            str(item.get("metric") or "").lower()
            for item in metrics
            if item.get("metric")
        }
        if not fact_goal and "fact_conflict" not in metric_names:
            return None

        required_entities = [
            str(item).strip()
            for item in (fact_goal.get("required_entities") or [])
            if str(item).strip()
        ]
        forbidden_claims = [
            str(item).strip()
            for item in (fact_goal.get("forbidden_claims") or [])
            if str(item).strip()
        ]
        old_signatures = [
            str(item).strip()
            for item in (fact_goal.get("old_error_signatures") or [])
            if str(item).strip()
        ]
        signatures = []
        for item in [*forbidden_claims, *old_signatures]:
            if item and item not in signatures:
                signatures.append(item)

        before_entities = [
            entity for entity in required_entities
            if ChapterRepairExecutor._fact_signature_present(entity, before_text)
        ]
        after_entities = [
            entity for entity in required_entities
            if ChapterRepairExecutor._fact_signature_present(entity, after_text)
        ]
        missing_required_entities = [
            entity for entity in required_entities
            if entity not in after_entities
        ]
        before_lingering = [
            sig for sig in signatures
            if ChapterRepairExecutor._fact_signature_present(sig, before_text)
        ]
        after_lingering = [
            sig for sig in signatures
            if ChapterRepairExecutor._fact_signature_present(sig, after_text)
        ]

        supported = bool(required_entities or signatures)
        if not supported:
            return {
                "supported": False,
                "passed": before_text != after_text,
                "metric": "fact_conflict",
                "source": "fact_goal_missing",
            }

        required_passed = not missing_required_entities
        old_error_passed = not after_lingering or len(after_lingering) < len(before_lingering)
        passed = before_text != after_text and required_passed and old_error_passed
        return {
            "supported": True,
            "passed": passed,
            "metric": "fact_conflict",
            "before": {
                "required_entities_present": len(before_entities),
                "old_error_signatures_present": len(before_lingering),
            },
            "after": {
                "required_entities_present": len(after_entities),
                "old_error_signatures_present": len(after_lingering),
            },
            "missing_required_entities": missing_required_entities[:12],
            "lingering_old_error_signatures": after_lingering[:12],
            "direction": "resolve",
            "source": "fact_target_self_check",
            "fact_conflict_type": fact_goal.get("conflict_type", ""),
        }

    @staticmethod
    def _fact_old_error_signatures_present(order: ChapterRepairOrder, text: str) -> bool:
        brief = order.repair_brief or {}
        fact_goal = brief.get("fact_repair_goal") or {}
        if not isinstance(fact_goal, dict) or not fact_goal:
            return False
        signatures: list[str] = []
        for key in ("forbidden_claims", "old_error_signatures"):
            for item in fact_goal.get(key) or []:
                sig = str(item).strip()
                if sig and sig not in signatures:
                    signatures.append(sig)
        return any(
            ChapterRepairExecutor._fact_signature_present(signature, text)
            for signature in signatures
        )

    @staticmethod
    def _fact_signature_present(signature: str, text: str) -> bool:
        signature = str(signature or "").strip()
        text = str(text or "")
        if not signature or not text:
            return False
        if signature in text:
            return True
        parts = [
            part.strip()
            for part in re.split(r"\.{3,}|……|…", signature)
            if len(part.strip()) >= 2
        ]
        if parts and all(part in text for part in parts):
            return True
        compact_signature = re.sub(r"\s+", "", signature)
        compact_text = re.sub(r"\s+", "", text)
        if len(compact_signature) >= 2 and compact_signature in compact_text:
            return True
        if len(compact_signature) >= 12:
            chunks = [
                compact_signature[index:index + 6]
                for index in range(0, max(1, len(compact_signature) - 5), 6)
                if len(compact_signature[index:index + 6]) >= 4
            ]
            if chunks and sum(1 for chunk in chunks if chunk in compact_text) >= max(1, len(chunks) - 1):
                return True
        return False

    @staticmethod
    def _paragraph_shape_score(text: str) -> int:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
        prefixes: dict[str, int] = {}
        for paragraph in paragraphs:
            prefix = re.sub(r"[\W_]+", "", paragraph)[:6]
            if not prefix:
                continue
            prefixes[prefix] = prefixes.get(prefix, 0) + 1
        mirrored = sum(count - 1 for count in prefixes.values() if count > 1)
        long_streak = ChapterRepairExecutor._dense_paragraph_score(text)
        return mirrored + long_streak

    @staticmethod
    def _dense_paragraph_score(text: str) -> int:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
        score = 0
        streak = 0
        for paragraph in paragraphs:
            dense = len(paragraph) >= 180
            if dense:
                streak += 1
                score = max(score, streak)
            else:
                streak = 0
        return score

    @staticmethod
    def _generic_voice_package_score(text: str) -> int:
        patterns = (
            "深吸一口气",
            "心中一紧",
            "眼神复杂",
            "说不出的感觉",
            "莫名其妙",
            "一种难以言喻",
            "不由得",
            "下意识",
        )
        return sum((text or "").count(pattern) for pattern in patterns)

    @staticmethod
    def _hook_weakness_score(text: str) -> int:
        """方案 30：降级为兜底——B 类 hook metric 改用 LLM 后置校验，此方法仅在 LLM 不可用时使用。"""
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
        if not paragraphs:
            return 1
        first = paragraphs[0]
        last = paragraphs[-1]
        weak = 0
        if len(first) > 160 and not re.search(r"[？?!！]|忽然|突然|血|响|断|门|影|声", first):
            weak += 1
        if len(last) > 140 and not re.search(r"[？?!！]|却|只见|忽然|突然|断|响|影|血|等", last):
            weak += 1
        if first == last:
            weak += 1
        return weak

    @staticmethod
    def _dash_density_check(
        order: ChapterRepairOrder,
        before_text: str,
        after_text: str,
    ) -> dict[str, Any] | None:
        violations = ChapterRepairExecutor._build_violations_from_order(order)
        if not ChapterRepairExecutor._is_dash_density_order(order, violations):
            return None
        before_count = count_dash_artifacts(before_text)
        after_count = count_dash_artifacts(after_text)
        target_count = ChapterRepairExecutor._dash_target_count(before_text, order, violations)
        return {
            "metric": "dash_per_1000",
            "before_count": before_count,
            "after_count": after_count,
            "target_count": target_count,
            "replacements": max(0, before_count - after_count),
            "passed": before_count <= target_count or after_count <= target_count,
            "executor_path": "deterministic_dash_repair" if after_count < before_count else "dash_density_gate",
        }

    def _protected_obligation_groups(
        self,
        order: ChapterRepairOrder,
        before_text: str,
        scene_index: int,
        context: dict[str, Any],
        case_file: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        groups: list[dict[str, Any]] = []
        case_file = case_file or {}

        def extract_terms(value: Any) -> set[str]:
            terms: set[str] = set()
            if isinstance(value, str):
                self._add_protected_chunks(terms, value)
            elif isinstance(value, dict):
                for item in value.values():
                    terms.update(extract_terms(item))
            elif isinstance(value, list):
                for item in value:
                    terms.update(extract_terms(item))
            return {term for term in terms if term and term in before_text}

        def add_group(source: str, value: Any, *, strict: bool = False) -> None:
            terms = extract_terms(value)
            if terms:
                weak_terms = {
                    term for term in terms
                    if self._is_weak_protected_term(source, term, strict=strict)
                }
                groups.append({
                    "source": source,
                    "strict": strict,
                    "terms": terms,
                    "weak_terms": weak_terms,
                })

        add_group("order.expected_after_repair.preserve", order.expected_after_repair.get("preserve"), strict=True)

        scene_contracts = context.get("scene_contracts") or case_file.get("all_scene_contracts") or {}
        scene_contract = scene_contracts.get(scene_index, {}) if isinstance(scene_contracts, dict) else {}
        if isinstance(scene_contract, dict):
            for layer, obligations in hard_obligation_sources(scene_contract).items():
                for index, obligation in enumerate(obligations):
                    add_group(f"scene_contract.{layer}.{index}", obligation)

        for violation in self._build_violations_from_order(order):
            for key in ("preserve", "must_preserve", "protected_terms"):
                add_group(f"violation.{key}", violation.get(key), strict=True)

        return groups

    @classmethod
    def _removed_protected_obligations(
        cls,
        protected_groups: list[dict[str, Any]],
        before_text: str,
        after_text: str,
    ) -> dict[str, list[str]]:
        removed: list[str] = []
        weak_removed: list[str] = []
        for group in protected_groups:
            terms = sorted(str(term) for term in group.get("terms", set()) if str(term))
            if not terms:
                continue
            weak_terms = {str(term) for term in group.get("weak_terms", set()) if str(term)}
            missing = [term for term in terms if term in before_text and term not in after_text]
            if not missing:
                continue
            if group.get("strict"):
                removed.extend(missing)
                continue

            present_before = [term for term in terms if term in before_text]
            present_after = [term for term in present_before if term in after_text]
            if present_before and not present_after:
                strong_missing = [term for term in present_before if term not in weak_terms]
                weak_missing = [term for term in present_before if term in weak_terms]
                removed.extend(strong_missing)
                weak_removed.extend(weak_missing)
            else:
                weak_removed.extend(term for term in missing if term in weak_terms)
        return {
            "removed": sorted(set(removed)),
            "weak_removed": sorted(set(weak_removed)),
        }

    @staticmethod
    def _is_weak_protected_term(source: str, term: str, *, strict: bool = False) -> bool:
        """Treat short prose texture as advisory during broad scene rewrites.

        Hard facts and explicit preserve fields stay exact. Required outcomes
        and transitions are semantic obligations; a short sensory/connective
        fragment from those fields is not enough by itself to reject a rewrite.
        """
        if strict:
            return False
        source = str(source or "")
        if "hard_facts" in source:
            return False
        term = str(term or "").strip()
        if not term:
            return False
        # Contract chunking also extracts connective/function words.  Their
        # exact surface form is not an obligation: removing ``同时`` while
        # preserving the event must not reject an otherwise valid repair.
        if term in _WEAK_FUNCTION_TERMS:
            return True
        # 循环#13：含明喻标记的短片段视为弱保护词（在 source guard 之前）
        # 根因：ai_simile_overuse 修复需要改写"更像是一"/"像油一样"等明喻短语，
        #   但这些可能被纳入 scene_contract 的 protected_terms。原逻辑中 source
        #   不含 required_outcomes/required_transitions 时直接返回 False（非弱），
        #   protection_check 拒绝修复，导致明喻密度修复被跳过。
        # 修复策略：含明喻标记或以"像"开头的短片段（<= 8 汉字）直接判为弱保护词，
        #   不论 source 来源（hard_facts 已在上方排除）。
        # 通用性：所有题材的明喻短语都不应阻断密度修复。
        chinese_chars = re.findall(r"[\u4e00-\u9fff]", term)
        if len(chinese_chars) <= 8:
            if term.startswith("像") or any(marker in term for marker in _SIMILE_MARKERS):
                return True
        if "required_outcomes" not in source and "required_transitions" not in source:
            return False
        if _ENTITY_ANCHOR_RE.search(term):
            return False
        if len(chinese_chars) > 6:
            return False
        if any(term.startswith(prefix) for prefix in _WEAK_PROSE_PREFIXES):
            return True
        if any(term.endswith(suffix) for suffix in _WEAK_PROSE_SUFFIXES):
            return True
        if "的" in term or re.search(r"([\u4e00-\u9fff])\1", term):
            return True
        return False

    @staticmethod
    def _add_protected_chunks(terms: set[str], value: str) -> None:
        text = str(value or "").strip()
        if not text:
            return
        if 2 <= len(text) <= 40:
            terms.add(text)
        for chunk in re.split(r"[\s,，。；;、：:（）()《》“”\"'！？!?]+", text):
            chunk = chunk.strip()
            if 2 <= len(chunk) <= 24:
                terms.add(chunk)
            if re.fullmatch(r"[\u4e00-\u9fff]{5,24}", chunk):
                terms.add(chunk[:4])
                terms.add(chunk[-4:])

    @staticmethod
    def _target_span_checks(order: ChapterRepairOrder, before_text: str, after_text: str) -> list[dict[str, Any]]:
        checks: list[dict[str, Any]] = []
        for violation in order.violation_details or []:
            if not isinstance(violation, dict):
                continue
            span = str(violation.get("target_span") or "").strip()
            if not span or len(span) > 40:
                continue
            before_count = before_text.count(span)
            after_count = after_text.count(span)
            if before_count == 0:
                continue
            v_type = str(violation.get("type") or order.repair_type).lower()
            should_reduce = any(
                marker in v_type
                for marker in ("punctuation", "artifact", "ai_", "forbidden", "repetition")
            )
            if should_reduce:
                checks.append({
                    "target_span": span,
                    "before_count": before_count,
                    "after_count": after_count,
                    "passed": after_count < before_count,
                })
        return checks

    async def _dispatch_order(
        self,
        order: ChapterRepairOrder,
        current_text: str,
        scene_index: int,
        scene_texts: dict[int, str],
        context: dict[str, Any],
        case_file: dict[str, Any] | None = None,
    ) -> str:
        """根据 repair_type 分派到对应修复机制。

        case_file 包含完整案卷上下文，供修复师做知情修复：
        - all_scene_texts: 全章场景正文
        - all_scene_contracts: 全章场景合同
        - chapter_state: 章节状态
        - chapter_outline_contract: 大纲合同
        - review_packets: 原始审阅结果
        - repair_plan: FBI 修复计划
        - scene_truth_snapshots: 场景事实快照

        Returns:
            修复后的场景正文。
        """
        case_file = case_file or {}
        repair_type = order.repair_type

        if repair_type == "local_patch":
            return await self._repair_local_patch(order, current_text, scene_index, context, case_file)
        elif repair_type == "contract_completion_patch":
            return await self._repair_contract_completion_patch(order, current_text, scene_index, context, case_file)
        elif repair_type == "scene_rewrite":
            return await self._repair_scene_rewrite(order, current_text, scene_index, context, case_file)
        elif repair_type == "cross_scene_alignment":
            return await self._repair_cross_scene_alignment(order, current_text, scene_index, scene_texts, context, case_file)
        elif repair_type == "contract_patch":
            self._apply_contract_patch_order(order, scene_index, context, case_file, current_text)
            return current_text
        elif repair_type == "manual_review":
            return self._handle_manual_review(order, current_text)
        else:
            _logger.warning(
                "ChapterRepairExecutor: unknown repair_type=%s order=%s, failing",
                repair_type, order.order_id,
            )
            raise ValueError(f"未知的修复类型: {repair_type}")

    # ------------------------------------------------------------------
    # Repair type implementations
    # ------------------------------------------------------------------

    async def _repair_local_patch(
        self,
        order: ChapterRepairOrder,
        current_text: str,
        scene_index: int,
        context: dict[str, Any],
        case_file: dict[str, Any] | None = None,
        protection_retry: dict[str, Any] | None = None,
    ) -> str:
        """local_patch: 使用 SceneRepairer 做局部修补。

        SceneRepairer 自动根据违规严重程度选择 inline / patch 策略，
        使用合并后的 SceneRepairer 执行行内修订或局部补丁。
        """
        case_file = case_file or {}
        violations = self._build_violations_from_order(order)
        brief = order.repair_brief or {}
        workbench_semantic_route_kind = str(brief.get("workbench_semantic_route") or "")
        has_creative_command = any(
            isinstance(command, dict)
            and command.get("operation") == "llm_creative_rewrite"
            for command in (order.tool_commands or [])
        )
        workbench_semantic_route = bool(
            workbench_semantic_route_kind in {
                "bounded_creative_work_unit",
                "bounded_cross_scene_consolidation",
            }
            and has_creative_command
        )
        external_protected_span = (
            dict(brief.get("external_protected_span") or {})
            if isinstance(brief.get("external_protected_span"), dict)
            else {}
        )
        use_bounded_cross_scene_consolidation = bool(
            workbench_semantic_route_kind == "bounded_cross_scene_consolidation"
            and has_creative_command
            and isinstance(external_protected_span.get("scene_index"), int)
            and external_protected_span.get("scene_index") != scene_index
        )
        local_semantic_route = bool(
            workbench_semantic_route_kind == "bounded_creative_work_unit"
            and any(
                isinstance(command, dict)
                and command.get("operation") == "llm_creative_rewrite"
                for command in (order.tool_commands or [])
            )
        )
        semantic_target_span = self._primary_target_span(order) if local_semantic_route else ""
        use_bounded_semantic_inline = bool(
            semantic_target_span
            and current_text.count(semantic_target_span) == 1
        )
        deterministic = (
            None
            if protection_retry or workbench_semantic_route
            else self._deterministic_local_patch(
                order,
                current_text,
                violations,
                scene_contract=context.get("scene_contracts", {}).get(scene_index, {}),
            )
        )
        if deterministic is not None and deterministic != current_text:
            _logger.info(
                "ChapterRepairExecutor: deterministic local patch applied for order %s",
                order.order_id,
            )
            return deterministic

        # 判断是否适合 inline：instruction 中包含局部替换关键词
        inline_keywords = ("替换", "局部", "inline", "patch", "AI味", "去AI", "润色")
        # requires_llm=True 的 metric（如 ai_simile_overuse 等密度类问题）不走 inline，
        # 因为 inline 路径不处理 evidence_samples，LLM 只改 target_span 一处，
        # 密度仍超标。必须走 patch 路径（_patch_from_violations）才能系统性处理所有命中位置。
        use_inline = (
            use_bounded_semantic_inline
            or use_bounded_cross_scene_consolidation
            or (
                any(kw in order.instruction for kw in inline_keywords)
                and len(violations) == 1
                and not protection_retry
                and not _order_requires_llm(order, violations)
            )
        )

        preserve = self._merge_preserve_terms_for_retry(
            order.expected_after_repair.get("preserve", []),
            protection_retry,
        )
        agent_context = {
            **self._scene_repair_protocol_context(order, context),
            "generated_text": current_text,
            "violations": violations,
            "scene_contract": context.get("scene_contracts", {}).get(scene_index, {}),
            "scene_truth_snapshot": context.get("scene_truth_snapshots", {}).get(scene_index),
            "chapter_state": context.get("chapter_state", {}),
            "chapter_scene_context": context.get("chapter_scene_context", ""),
            "repair_plan": {
                "root_cause": order.reason,
                "preserve": preserve,
                "remove": order.expected_after_repair.get("remove", []),
            },
        }
        if protection_retry:
            agent_context["repair_strategy"] = "patch"
            agent_context["protection_retry"] = protection_retry
            agent_context["previous_rejected_patch"] = {
                "reason": protection_retry.get("reason"),
                "result_hash": protection_retry.get("rejected_result_hash"),
            }

        # 注入案卷式上下文
        self._inject_case_file(agent_context, case_file, scene_index)
        self._inject_skill_contract_envelope(agent_context, order, context)

        if use_bounded_semantic_inline:
            # A uniquely localized semantic finding needs creative language,
            # but it does not need the model to regenerate the whole scene.
            # Give InlineRepairer a small evidence window while it still
            # applies the returned exact patch against the complete scene.
            problem = "\n".join(
                str(violation.get("detail") or "").strip()
                for violation in violations
                if isinstance(violation, dict) and str(violation.get("detail") or "").strip()
            ) or order.reason
            agent_context["repair_strategy"] = "inline"
            agent_context["inline_window_text"] = self._bounded_inline_window(
                current_text,
                semantic_target_span,
            )
            agent_context["bounded_target_span"] = semantic_target_span
            agent_context["revision_hints"] = [{
                "problem": problem,
                "target_span": semantic_target_span,
                "strategy": brief.get("target_behavior") or order.instruction or "minimal semantic bridge",
                "auto_revise_allowed": True,
                "preserve": preserve,
            }]
            agent_context["bounded_semantic_repair"] = True
        elif use_bounded_cross_scene_consolidation:
            # The protected evidence belongs to another scene and is read-only.
            # Give the model only the writable owner's tail plus a compact
            # read-only evidence window, then apply its exact patch against the
            # complete owner scene.
            external_scene = int(external_protected_span["scene_index"])
            protected_span = str(external_protected_span.get("span") or "").strip()
            external_text = str(
                (case_file.get("all_scene_texts") or {}).get(external_scene)
                or ""
            )
            problem = "\n".join(
                str(violation.get("detail") or "").strip()
                for violation in violations
                if isinstance(violation, dict) and str(violation.get("detail") or "").strip()
            ) or order.reason
            agent_context["repair_strategy"] = "inline"
            agent_context["inline_window_text"] = self._bounded_tail_window(current_text)
            agent_context["external_protected_span"] = external_protected_span
            agent_context["external_protected_context_text"] = self._bounded_inline_window(
                external_text,
                protected_span,
                radius=900,
            ) if protected_span and protected_span in external_text else external_text[:1800]
            agent_context["revision_hints"] = [{
                "problem": problem,
                "target_span": "writable owner-scene tail",
                "strategy": brief.get("target_behavior") or order.instruction or "consolidate duplicate event",
                "auto_revise_allowed": True,
                "preserve": preserve,
            }]
            agent_context["bounded_cross_scene_consolidation"] = True
        elif use_inline:
            # 让 SceneRepairer 使用 inline 策略
            agent_context["repair_strategy"] = "inline"
            if len(violations) == 1:
                agent_context["revision_hints"] = [
                    {
                        "problem": order.reason,
                        "target_span": order.instruction,
                        "strategy": "local_patch",
                        "auto_revise_allowed": True,
                        "preserve": preserve,
                    }
                ]
        elif protection_retry:
            agent_context["revision_hints"] = [
                {
                    "problem": (
                        f"{order.reason}\n"
                        "Retry because the previous candidate removed protected terms."
                    ),
                    "target_span": self._primary_target_span(order),
                    "strategy": "local_patch_protection_retry",
                    "auto_revise_allowed": True,
                    "preserve": preserve,
                    "expected_after_repair": order.expected_after_repair,
                }
            ]

        result = await self.scene_repairer.execute(agent_context)
        repaired = result.get("repaired_text", current_text)
        if not result.get("success") or repaired == current_text:
            _logger.warning(
                "ChapterRepairExecutor: SceneRepairer returned no change for order %s",
                order.order_id,
            )
        if not result.get("success"):
            raise RuntimeError(
                f"SceneRepairer local patch failed for order {order.order_id}: "
                f"{result.get('error', 'unknown')}"
            )
        if repaired == current_text:
            # Return the unchanged candidate to the normal audit/retry path.
            # Raising here bypassed lane escalation and the after-all-groups
            # reconciliation that can legitimately mark duplicate/no-effect
            # work as skipped.  The audit still rejects unchanged blocking
            # work, so this does not create a false success.
            return current_text
        return repaired

    @staticmethod
    def _bounded_inline_window(text: str, target_span: str, *, radius: int = 700) -> str:
        """Return a paragraph-aligned local window around one exact target."""
        text = str(text or "")
        target_span = str(target_span or "")
        index = text.find(target_span)
        if index < 0:
            return text

        start = max(0, index - max(200, radius))
        end = min(len(text), index + len(target_span) + max(200, radius // 2))
        if start > 0:
            boundary = text.find("\n\n", start, index)
            if boundary >= 0:
                start = boundary + 2
        if end < len(text):
            boundary = text.find("\n\n", end)
            if boundary >= 0:
                end = boundary
        return text[start:end].strip()

    @staticmethod
    def _bounded_tail_window(text: str, *, max_chars: int = 2500) -> str:
        """Return a paragraph-aligned writable tail window for consolidation."""
        text = str(text or "")
        if len(text) <= max_chars:
            return text.strip()
        start = max(0, len(text) - max(800, max_chars))
        boundary = text.find("\n\n", start)
        if boundary >= 0:
            start = boundary + 2
        return text[start:].strip()

    def _deterministic_local_patch(
        self,
        order: ChapterRepairOrder,
        current_text: str,
        violations: list[dict],
        scene_contract: dict | None = None,
    ) -> str | None:
        """Apply exact text patches for clear fact/provenance contradictions.

        方案B通用纪律：对 requires_llm=True 的 metric，跳过所有特化修复
        （final_gate_text_repair、_patch_clue_provenance 等），
        让 LLM 创作修复（scene_repairer）兜底。
        只有 requires_llm=False 或未注册路由的纯事实/标点类 metric
        才走确定性快速通道。
        """
        v_types = {
            str(v.get("type") or v.get("violation_type") or "").lower()
            for v in violations
            if isinstance(v, dict)
        }
        if self._is_dash_density_order(order, violations):
            repaired = self._normalize_dash_artifacts(current_text, order, violations)
            if repaired != current_text:
                return repaired

        # 方案B：requires_llm=True 的 metric 跳过结构化补丁（patch_plan），
        # 让 LLM 兜底。patch_plan 仍通过 repair_brief 传递给 LLM 作为参考。
        if not _order_requires_llm(order, violations):
            structured_patch = self._apply_structured_patch_plan(order, current_text)
            if structured_patch is not None and structured_patch != current_text:
                return structured_patch

        # 方案B：requires_llm=True 的 metric 跳过特化修复，让 LLM 兜底
        final_gate_repaired = self._apply_final_gate_text_repair(
            order,
            current_text,
            violations,
            scene_contract=scene_contract,
        )
        if final_gate_repaired is not None and final_gate_repaired != current_text:
            return final_gate_repaired

        if v_types & _CHAPTER_REFERENCE_TYPES:
            repaired = self._patch_chapter_references(current_text, violations)
            if repaired != current_text:
                return repaired

        if v_types & {"clue_provenance_error", "clue_provenance_error_proposition", "unprovenanced_clue"}:
            repaired = self._patch_clue_provenance(current_text, violations)
            if repaired != current_text:
                return repaired

        if not (v_types & _DETERMINISTIC_FACT_REPAIR_TYPES):
            return None

        # ``requires_llm`` governs candidate creation, not execution of an
        # explicit authority-backed fact correction already present in the
        # order.  Always try those bounded old/new pairs before asking the LLM
        # to rewrite a wider window.
        candidates: list[tuple[str, str]] = []
        candidates.extend(self._fact_goal_patch_candidates(order))
        for source in [order.reason, order.instruction, *(v.get("detail") or "" for v in violations)]:
            candidates.extend(self._extract_fact_patch_candidates(str(source or "")))
            candidates.extend(self._extract_suggested_correction_candidates(str(source or "")))
            candidates.extend(self._extract_chinese_fact_conflict_candidates(str(source or "")))

        candidates = self._rank_fact_patch_candidates(current_text, candidates)

        for old, new in candidates:
            if not old or not new:
                continue
            fuzzy_repaired = self._replace_fact_phrase(current_text, old, new)
            if fuzzy_repaired and fuzzy_repaired != current_text:
                self._primitive_repair_traces[order.order_id] = {
                    "changed": True,
                    "executor_path": "deterministic_fact_suggested_correction",
                    "from": old,
                    "to": new,
                }
                return fuzzy_repaired
            if old not in current_text:
                continue
            replacement = self._normalize_fact_replacement(old, new)
            if replacement and replacement != old:
                self._primitive_repair_traces[order.order_id] = {
                    "changed": True,
                    "executor_path": "deterministic_fact_suggested_correction",
                    "from": old,
                    "to": replacement,
                }
                return current_text.replace(old, replacement, 1)
        return None

    def _apply_structured_patch_plan(
        self,
        order: ChapterRepairOrder,
        current_text: str,
    ) -> str | None:
        """Apply Review Minister patch_plan entries before LLM repair."""
        patch_plan = (order.repair_brief or {}).get("patch_plan")
        if not isinstance(patch_plan, list):
            return None

        repaired = current_text
        applied: list[dict[str, Any]] = []
        for item in patch_plan:
            if not isinstance(item, dict):
                continue
            operation = str(item.get("operation") or "replace_phrase")
            if operation not in {"replace_phrase", "replace_conflicting_phrase"}:
                continue
            old = str(item.get("from") or item.get("old") or item.get("source") or "").strip()
            new = str(item.get("to") or item.get("new") or item.get("target") or "").strip()
            if not old or not new or old == new:
                continue
            next_text = self._replace_fact_phrase(repaired, old, new)
            if not next_text and old in repaired:
                next_text = repaired.replace(old, self._normalize_fact_replacement(old, new), 1)
            if next_text and next_text != repaired:
                repaired = next_text
                applied.append({
                    "operation": operation,
                    "from": old,
                    "to": new,
                })

        if not applied or repaired == current_text:
            return None
        self._primitive_repair_traces[order.order_id] = {
            "changed": True,
            "executor_path": "structured_patch_plan",
            "applied_patches": applied,
        }
        return repaired

    def _apply_final_gate_text_repair(
        self,
        order: ChapterRepairOrder,
        current_text: str,
        violations: list[dict],
        *,
        scene_contract: dict | None,
    ) -> str | None:
        result = repair_final_gate_text(
            current_text,
            violations,
            reason=order.reason,
            instruction=order.instruction,
            scene_contract=scene_contract,
        )
        if not result.changed or result.text == current_text:
            return None
        self._primitive_repair_traces[order.order_id] = result.to_audit()
        return result.text

    @staticmethod
    def _rank_fact_patch_candidates(current_text: str, candidates: list[tuple[str, str]]) -> list[tuple[str, str]]:
        seen: set[tuple[str, str]] = set()
        unique: list[tuple[str, str]] = []
        for old, new in candidates:
            key = (old.strip(), new.strip())
            if not key[0] or not key[1] or key in seen:
                continue
            seen.add(key)
            unique.append(key)
        return sorted(
            unique,
            key=lambda pair: (
                pair[0] in current_text,
                len(pair[0]),
                len(pair[1]),
            ),
            reverse=True,
        )

    @staticmethod
    def _fact_goal_patch_candidates(order: ChapterRepairOrder) -> list[tuple[str, str]]:
        brief = order.repair_brief or {}
        fact_goal = brief.get("fact_repair_goal") or {}
        if not isinstance(fact_goal, dict) or not fact_goal:
            return []

        old_values: list[str] = []
        for key in ("text_claim", "old_error_signatures", "forbidden_claims"):
            value = fact_goal.get(key)
            values = value if isinstance(value, list) else [value]
            for item in values:
                text = str(item or "").strip()
                if text and text not in old_values:
                    old_values.append(text)

        suggestion_values: list[str] = []
        for key in ("suggested_correction", "required_relation", "target_behavior"):
            value = str(fact_goal.get(key) or "").strip()
            if value:
                suggestion_values.append(value)
        if not old_values or not suggestion_values:
            return []

        pairs: list[tuple[str, str]] = []
        for old in old_values:
            for suggestion in suggestion_values:
                for replacement in ChapterRepairExecutor._replacement_fragments_from_suggestion(suggestion):
                    new = ChapterRepairExecutor._rewrite_old_claim_with_replacement_fragment(old, replacement)
                    if new and new != old:
                        pairs.append((old, new))
                pairs.extend(ChapterRepairExecutor._extract_suggested_correction_candidates(
                    f"Text claim '{old}' conflicts with established fact ''; suggested correction: \"{suggestion}\""
                ))
        return pairs

    @staticmethod
    def _extract_fact_patch_candidates(text: str) -> list[tuple[str, str]]:
        if not text:
            return []
        quote = r"['\"“”‘’]"
        patterns = [
            rf"正文写{quote}([^'\"“”‘’]+){quote}.*?(?:scene_provenance指定|合同要求|要求|指定|应为|应该是){quote}([^'\"“”‘’]+){quote}",
            rf"(?:正文|文本).*?{quote}([^'\"“”‘’]+){quote}.*?(?:但|而|与).*?(?:指定|要求|应为|应该是).*?{quote}([^'\"“”‘’]+){quote}",
        ]
        pairs: list[tuple[str, str]] = []
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                old = match.group(1).strip()
                new = match.group(2).strip()
                if old and new:
                    pairs.append((old, new))
        return pairs

    @staticmethod
    def _normalize_fact_replacement(old: str, new: str) -> str:
        if re.search(r"[高丈米]", old):
            height = re.search(
                r"约?[一二两三四五六七八九十\d]+(?:人高|丈|米|米高|丈高)",
                new,
            )
            if height:
                return height.group(0)
        return new

    @staticmethod
    def _is_dash_density_order(order: ChapterRepairOrder, violations: list[dict]) -> bool:
        type_values = {
            str(v.get("type") or v.get("violation_type") or v.get("semantic_type") or "").lower()
            for v in violations
            if isinstance(v, dict)
        }
        metric_values = {
            str(v.get("metric") or v.get("metric_name") or v.get("key") or "").lower()
            for v in violations
            if isinstance(v, dict)
        }
        text_blob = " ".join(
            str(part or "").lower()
            for part in [
                order.reason,
                order.instruction,
                order.repair_domain,
                *(v.get("detail") or "" for v in violations if isinstance(v, dict)),
            ]
        )
        return bool(
            type_values
            & {
                "ai_punctuation_artifact",
                "explanatory_punctuation_artifact",
                "dash_density",
                "dash_per_1000",
            }
            or "dash_per_1000" in metric_values
            or ("dash" in text_blob and "punctuation" in text_blob)
            or ("破折号" in text_blob and ("密度" in text_blob or "滥用" in text_blob))
        )

    @staticmethod
    def _normalize_dash_artifacts(
        current_text: str,
        order: ChapterRepairOrder | None = None,
        violations: list[dict] | None = None,
    ) -> str:
        before_count = count_dash_artifacts(current_text)
        if before_count <= 0:
            return current_text

        target_count = ChapterRepairExecutor._dash_target_count(current_text, order, violations or [])
        replacements_needed = max(0, before_count - target_count)
        if replacements_needed <= 0:
            return current_text

        matches = list(iter_dash_artifacts(current_text))
        replacement_indexes = set(
            dash_artifact_replacement_indexes(current_text, matches, replacements_needed)
        )
        pieces: list[str] = []
        cursor = 0
        for match_index, match in enumerate(matches):
            start, end = match.span()
            pieces.append(current_text[cursor:start])
            if match_index in replacement_indexes:
                pieces.append(dash_artifact_replacement(current_text, start, end))
            else:
                pieces.append(match.group(0))
            cursor = end
        pieces.append(current_text[cursor:])
        repaired = "".join(pieces)

        if count_dash_artifacts(repaired) > target_count:
            repaired = ChapterRepairExecutor._replace_excess_dash_artifacts(repaired, target_count)
        return ChapterRepairExecutor._cleanup_dash_repair_punctuation(repaired)

    @staticmethod
    def _replace_excess_dash_artifacts(text: str, target_count: int) -> str:
        matches = list(iter_dash_artifacts(text))
        if len(matches) <= target_count:
            return text

        pieces: list[str] = []
        cursor = 0
        for index, match in enumerate(matches):
            start, end = match.span()
            pieces.append(text[cursor:start])
            pieces.append(match.group(0) if index < target_count else "，")
            cursor = end
        pieces.append(text[cursor:])
        return "".join(pieces)

    @staticmethod
    def _dash_target_count(
        text: str,
        order: ChapterRepairOrder | None,
        violations: list[dict],
    ) -> int:
        explicit_count = ChapterRepairExecutor._extract_dash_target_count(order, violations)
        if explicit_count is not None:
            return max(0, explicit_count)

        threshold = ChapterRepairExecutor._extract_dash_density_threshold(order, violations)
        if threshold is None:
            threshold = 2.0
        return dash_artifact_allowed_count(text, threshold)

    @staticmethod
    def _extract_dash_target_count(
        order: ChapterRepairOrder | None,
        violations: list[dict],
    ) -> int | None:
        candidates: list[Any] = []
        if order is not None:
            candidates.extend(
                (order.expected_after_repair or {}).get(key)
                for key in (
                    "target_dash_count",
                    "max_dash_count",
                    "allowed_dash_count",
                    "dash_budget",
                )
            )
        for violation in violations:
            if not isinstance(violation, dict):
                continue
            candidates.extend(
                violation.get(key)
                for key in (
                    "target_dash_count",
                    "max_dash_count",
                    "allowed_dash_count",
                    "dash_budget",
                    "budget_count",
                )
            )
        for candidate in candidates:
            value = ChapterRepairExecutor._coerce_number(candidate)
            if value is not None:
                return int(value)
        return None

    @staticmethod
    def _extract_dash_density_threshold(
        order: ChapterRepairOrder | None,
        violations: list[dict],
    ) -> float | None:
        candidates: list[Any] = []
        if order is not None:
            candidates.extend(
                (order.expected_after_repair or {}).get(key)
                for key in (
                    "target_dash_per_1000",
                    "max_dash_per_1000",
                    "dash_per_1000",
                    "threshold_per_1000",
                )
            )
        for violation in violations:
            if not isinstance(violation, dict):
                continue
            metric = str(
                violation.get("metric")
                or violation.get("type")
                or violation.get("violation_type")
                or ""
            ).lower()
            for key in (
                "target_dash_per_1000",
                "max_dash_per_1000",
                "dash_per_1000",
                "threshold_per_1000",
            ):
                candidates.append(violation.get(key))
            if metric in {
                "dash_per_1000",
                "dash_density",
                "ai_punctuation_artifact",
                "explanatory_punctuation_artifact",
            }:
                candidates.extend(
                    violation.get(key)
                    for key in (
                        "threshold",
                        "limit",
                        "max_allowed",
                        "expected_max",
                    )
                )
        for candidate in candidates:
            value = ChapterRepairExecutor._coerce_number(candidate)
            if value is not None:
                return float(value)
        return None

    @staticmethod
    def _coerce_number(value: Any) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return float(value)
        match = re.search(r"-?\d+(?:\.\d+)?", str(value))
        if not match:
            return None
        return float(match.group(0))

    @staticmethod
    def _cleanup_dash_repair_punctuation(text: str) -> str:
        text = re.sub(r"，([，、；;：:。！？!?])", r"\1", text)
        text = re.sub(r"。([，、；;：:])", r"。", text)
        text = re.sub(r"：([，、；;：:])", r"：", text)
        return text

    @staticmethod
    def _patch_chapter_references(current_text: str, violations: list[dict]) -> str:
        if not current_text:
            return current_text

        spans: list[str] = []
        for violation in violations:
            if not isinstance(violation, dict):
                continue
            target = str(violation.get("target_span") or "").strip()
            if target and _CHAPTER_REFERENCE_RE.search(target):
                spans.append(target)
            detail = str(violation.get("detail") or violation.get("reason") or "")
            spans.extend(match.group(0) for match in _CHAPTER_REFERENCE_RE.finditer(detail))

        repaired = current_text
        for span in sorted(set(spans), key=len, reverse=True):
            actual_span = ChapterRepairExecutor._expand_chapter_reference_span(repaired, span)
            if actual_span:
                repaired = repaired.replace(
                    actual_span,
                    ChapterRepairExecutor._chapter_reference_replacement(repaired, actual_span),
                    1,
                )

        if repaired != current_text:
            return repaired
        return _CHAPTER_REFERENCE_RE.sub(
            lambda match: ChapterRepairExecutor._chapter_reference_replacement(current_text, match),
            current_text,
        )

    @staticmethod
    def _chapter_reference_replacement(text: str, ref: str | re.Match) -> str:
        if isinstance(ref, re.Match):
            start, end = ref.span()
        else:
            start = text.find(ref)
            end = start + len(ref) if start >= 0 else 0

        ref_text = ref.group(0) if isinstance(ref, re.Match) else str(ref)
        if re.search(r"(?:中|里|内|时|的时候)$", ref_text):
            return "先前"

        before = text[start - 1:start] if start > 0 else ""
        after = text[end:end + 8]
        if before in {"在", "于", "从", "自"}:
            return "先前"
        if not after or re.match(r"^[\s，。；：、！？,.!?;:）)]", after):
            return "先前"
        if re.match(r"^(?:中|里|内|时|的时候)", after):
            return "先前"
        if re.match(r"^[\u4e00-\u9fff]{1,8}", after):
            return "先前在"
        return "先前"

    @staticmethod
    def _expand_chapter_reference_span(text: str, span: str) -> str:
        if not span or span not in text:
            return ""
        start = text.find(span)
        end = start + len(span)
        suffix = text[end:end + 3]
        match = re.match(r"^(?:中|里|内|时|的时候)", suffix)
        if match:
            return span + match.group(0)
        return span

    @staticmethod
    def _patch_clue_provenance(current_text: str, violations: list[dict]) -> str:
        detail = " ".join(str(v.get("detail") or "") for v in violations if isinstance(v, dict))
        target_span = ""
        for violation in violations:
            if isinstance(violation, dict) and violation.get("target_span"):
                target_span = str(violation.get("target_span") or "")
                break
        if not target_span:
            return current_text

        provenance_clause = "她先前发现后将它收起，至今不知道是谁留下的"
        if provenance_clause in current_text:
            return current_text
        anchors = [target_span.strip(), target_span.split("……", 1)[0].strip()]
        anchors.extend(match.group(0) for match in re.finditer(r"“[^”]+”", target_span))
        if detail:
            anchors.extend(match.group(0) for match in re.finditer(r"“[^”]+”", detail))
        for anchor in anchors:
            if not anchor or anchor not in current_text:
                continue
            terminal = anchor[-1] if anchor[-1:] in "。！？" else ""
            body = anchor[:-1] if terminal else anchor
            replacement = f"{body}；{provenance_clause}{terminal or '。'}"
            return current_text.replace(anchor, replacement, 1)
        return current_text

    @staticmethod
    def _extract_suggested_correction_candidates(text: str) -> list[tuple[str, str]]:
        if not text:
            return []
        pairs: list[tuple[str, str]] = []
        for match in re.finditer(r"将(?:正文中的|文本中的)?(.+?)改为(.+?)(?:，|。|,|;|；|$)", text):
            old = match.group(1).strip(" '\"“”‘’")
            new = match.group(2).strip(" '\"“”‘’")
            if old and new:
                pairs.append((old, new))

        claim_match = re.search(
            r"Text claim ['\"](.+?)['\"] conflicts with established fact ['\"](.+?)['\"]",
            text,
        )
        if claim_match:
            old = ChapterRepairExecutor._strip_fact_label(claim_match.group(1))
            new = ChapterRepairExecutor._strip_fact_label(claim_match.group(2))
            suggestion = ChapterRepairExecutor._extract_suggested_correction_text(text)
            for replacement in ChapterRepairExecutor._replacement_fragments_from_suggestion(suggestion):
                suggested_new = ChapterRepairExecutor._rewrite_old_claim_with_replacement_fragment(old, replacement)
                if old and suggested_new and suggested_new != old:
                    pairs.append((old, suggested_new))
            if old and new:
                pairs.append((old, new))
        return pairs

    @staticmethod
    def _extract_suggested_correction_text(text: str) -> str:
        if not text:
            return ""
        match = re.search(
            r"suggested correction\s*:\s*(.+?)(?:$|\n|;\s*(?:source|severity|type)\b)",
            text,
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return ""
        raw = match.group(1).strip()
        if len(raw) >= 2 and raw[0] in "'\"" and raw[-1] == raw[0]:
            raw = raw[1:-1].strip()
        return raw

    @staticmethod
    def _replacement_fragments_from_suggestion(suggestion: str) -> list[str]:
        suggestion = str(suggestion or "").strip()
        if not suggestion:
            return []

        marker_pattern = (
            "\u66ff\u6362\u4e3a|\u6539\u4e3a|\u6539\u6210|"
            "replace\\s+with|change\\s+to"
        )
        fragments: list[str] = []
        marker_match = re.search(marker_pattern, suggestion, re.IGNORECASE)
        search_text = suggestion[marker_match.end():] if marker_match else suggestion
        quote_pattern = r"['\"\u201c\u201d\u2018\u2019]([^'\"\u201c\u201d\u2018\u2019]{1,120})['\"\u201c\u201d\u2018\u2019]"
        for match in re.finditer(quote_pattern, search_text):
            fragment = match.group(1).strip()
            if fragment and fragment not in fragments:
                fragments.append(fragment)

        if not fragments and marker_match:
            tail = search_text.strip()
            tail = re.split(r"[\u3002\uff1b;,.，]|(?:\s+or\s+)|(?:\s+and\s+)", tail, maxsplit=1)[0].strip()
            tail = tail.strip(" '\"\u201c\u201d\u2018\u2019")
            if tail:
                fragments.append(tail)
        return fragments

    @staticmethod
    def _rewrite_old_claim_with_replacement_fragment(old: str, replacement: str) -> str:
        old = str(old or "").strip()
        replacement = str(replacement or "").strip()
        if not old or not replacement or old == replacement:
            return ""
        if replacement in old:
            return old
        if len(replacement) >= max(8, int(len(old) * 0.7)):
            return replacement

        # Replace the smallest leading claim segment whose suffix overlaps the
        # suggested fragment, e.g. "陶罐底部的裂缝..." -> "石槽底部的裂缝...".
        max_prefix = min(len(old), max(len(replacement) + 24, len(replacement)))
        best: tuple[int, int] | None = None
        for end in range(2, max_prefix + 1):
            prefix = old[:end]
            overlap = ChapterRepairExecutor._common_suffix_length(prefix, replacement)
            if overlap >= 2 and end - overlap <= max(8, len(replacement)):
                if best is None or overlap > best[1] or (overlap == best[1] and end < best[0]):
                    best = (end, overlap)
        if best is not None:
            return replacement + old[best[0]:]

        max_suffix = min(len(old), len(replacement))
        for overlap in range(max_suffix, 1, -1):
            if old.endswith(replacement[:overlap]):
                return old[:-overlap] + replacement
        return replacement

    @staticmethod
    def _common_suffix_length(left: str, right: str) -> int:
        max_len = min(len(left), len(right))
        count = 0
        for index in range(1, max_len + 1):
            if left[-index] != right[-index]:
                break
            count += 1
        return count

    @staticmethod
    def _strip_fact_label(value: str) -> str:
        text = str(value or "").strip()
        if "：" in text:
            text = text.split("：", 1)[1]
        elif ":" in text:
            text = text.split(":", 1)[1]
        return text.strip()

    @staticmethod
    def _extract_chinese_fact_conflict_candidates(text: str) -> list[tuple[str, str]]:
        if not text:
            return []
        pairs: list[tuple[str, str]] = []
        patterns = [
            r"规定([^，。；;]+)，但正文(?:中)?描述([^，。；;]+)",
            r"合同(?:指定|要求)([^，。；;]+)，但正文(?:中)?(?:写|描述)([^，。；;]+)",
            r"(?:规定|要求|应为|明确规定|仅有)[^，。；;]*?[\"'“‘]?([^，。；;\"'”’]{2,30})[\"'”’]?，?但正文(?:中)?(?:写成|写为|写的是|写了)[\"'“‘]?([^，。；;\"'”’]{2,30})[\"'”’]?",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, text):
                new = match.group(1).strip()
                old = match.group(2).strip()
                if old and new:
                    pairs.append((old, new))
                    compact_old = re.sub(r"[（(].*?[）)]", "", old).strip()
                    compact_new = re.sub(r"[（(].*?[）)]", "", new).strip()
                    if compact_old and compact_new:
                        pairs.append((compact_old, compact_new))
        return pairs

    @staticmethod
    def _replace_fact_phrase(current_text: str, old: str, new: str) -> str | None:
        if old in current_text:
            replacement = ChapterRepairExecutor._normalize_fact_replacement(old, new)
            return current_text.replace(old, replacement, 1)

        normalized_old = re.sub(r"\s+", "", old)
        if normalized_old and normalized_old in re.sub(r"\s+", "", current_text):
            compact_map = []
            compact_chars = []
            for idx, ch in enumerate(current_text):
                if not ch.isspace():
                    compact_map.append(idx)
                    compact_chars.append(ch)
            compact_text = "".join(compact_chars)
            pos = compact_text.find(normalized_old)
            if pos >= 0:
                start = compact_map[pos]
                end = compact_map[pos + len(normalized_old) - 1] + 1
                return current_text[:start] + new + current_text[end:]
        compound_repaired = ChapterRepairExecutor._replace_compound_fact_phrase(current_text, old, new)
        if compound_repaired:
            return compound_repaired
        return None

    @staticmethod
    def _replace_compound_fact_phrase(current_text: str, old: str, new: str) -> str | None:
        old = re.sub(r"[（(].*?[）)]", "", old)
        old = re.sub(r"[，。；;：:\s'\"“”‘’]", "", old)
        if len(old) < 4:
            return None

        spans: list[tuple[int, int]] = []
        search_start = 0
        old_pos = 0
        while old_pos < len(old):
            best: tuple[int, int, int] | None = None
            max_len = min(8, len(old) - old_pos)
            for length in range(max_len, 1, -1):
                term = old[old_pos : old_pos + length]
                pos = current_text.find(term, search_start)
                if pos >= 0:
                    best = (pos, pos + length, length)
                    break
            if not best:
                old_pos += 1
                continue
            spans.append((best[0], best[1]))
            search_start = best[1]
            old_pos += best[2]

        if len(spans) < 2:
            return None
        start, end = spans[0][0], spans[-1][1]
        if end <= start:
            return None
        segment = current_text[start:end]
        max_segment_len = max(24, len(old) * 4)
        if len(segment) > max_segment_len:
            return None
        replacement = ChapterRepairExecutor._normalize_fact_replacement(old, new)
        return current_text[:start] + replacement + current_text[end:]

    async def _repair_scene_rewrite(
        self,
        order: ChapterRepairOrder,
        current_text: str,
        scene_index: int,
        context: dict[str, Any],
        case_file: dict[str, Any] | None = None,
        protection_retry: dict[str, Any] | None = None,
    ) -> str:
        """scene_rewrite: 使用 SceneRepairer 的 rewrite 策略从头重写场景。"""
        case_file = case_file or {}
        scene_contract = context.get("scene_contracts", {}).get(scene_index, {})
        violations = self._build_violations_from_order(order)
        # The bounded Final Gate primitive is safe to attempt before a full
        # scene rewrite even when semantic candidate generation may require an
        # LLM.  If it cannot make a concrete change, execution falls through
        # to the rewrite path.
        if not protection_retry:
            final_gate_repaired = self._apply_final_gate_text_repair(
                order,
                current_text,
                violations,
                scene_contract=scene_contract,
            )
            if final_gate_repaired is not None and final_gate_repaired != current_text:
                return final_gate_repaired

        agent_context = {
            **self._scene_repair_protocol_context(order, context),
            "generated_text": current_text,
            "violations": violations,
            "scene_contract": scene_contract,
            "chapter_state": context.get("chapter_state", {}),
            "previous_scene_ending": context.get("previous_scene_endings", {}).get(scene_index - 1, ""),
            "previous_scenes_summary": context.get("previous_scenes_summary", ""),
            "rejected_text": current_text,
            "scene_truth_snapshot": context.get("scene_truth_snapshots", {}).get(scene_index),
            "chapter_scene_context": context.get("chapter_scene_context", ""),
            "word_budget": context.get("word_budgets", {}).get(scene_index),
            "repair_plan": {
                "root_cause": order.reason,
                "preserve": self._merge_preserve_terms_for_retry(
                    order.expected_after_repair.get("preserve", []),
                    protection_retry,
                ),
                "remove": order.expected_after_repair.get("remove", []),
            },
            # 强制使用 rewrite 策略
            "repair_strategy": "rewrite",
        }
        if protection_retry:
            agent_context["protection_retry"] = protection_retry
            agent_context["previous_rejected_rewrite"] = {
                "reason": protection_retry.get("reason"),
                "result_hash": protection_retry.get("rejected_result_hash"),
            }

        # 注入案卷式上下文
        self._inject_case_file(agent_context, case_file, scene_index)
        self._inject_skill_contract_envelope(agent_context, order, context)

        result = await self.scene_repairer.execute(agent_context)
        generated = result.get("repaired_text", "")
        if not result.get("success") or not generated:
            raise RuntimeError(f"SceneRepairer rewrite failed for order {order.order_id}: {result.get('error', 'unknown')}")

        # 方案18 步骤2：FBI 触发 rewrite_scene 时回流偏离原因给主编
        project_id = context.get("project_id")
        chapter_number = context.get("chapter_number")
        if project_id and chapter_number is not None:
            try:
                from app.services.editor_feedback_service import EditorFeedbackService
                EditorFeedbackService.record_rewrite_feedback(
                    str(project_id),
                    int(chapter_number),
                    {
                        "scene_index": scene_index,
                        "violation_type": order.repair_type or "scene_rewrite",
                        "detail": (order.reason or "")[:200],
                        "original_contract": scene_contract,
                        "deviation": (order.reason or "")[:200],
                        "order_id": order.order_id,
                    },
                )
            except Exception:
                pass

        return generated

    async def _repair_contract_completion_patch(
        self,
        order: ChapterRepairOrder,
        current_text: str,
        scene_index: int,
        context: dict[str, Any],
        case_file: dict[str, Any] | None = None,
        protection_retry: dict[str, Any] | None = None,
    ) -> str:
        """Patch prose to complete a missing scene contract beat without rewriting the whole scene."""
        case_file = case_file or {}
        scene_contract = context.get("scene_contracts", {}).get(scene_index, {})
        violations = self._build_violations_from_order(order)
        preserve = self._merge_preserve_terms_for_retry(
            order.expected_after_repair.get("preserve", []),
            protection_retry,
        )

        agent_context = {
            **self._scene_repair_protocol_context(order, context),
            "generated_text": current_text,
            "violations": violations,
            "scene_contract": scene_contract,
            "scene_truth_snapshot": context.get("scene_truth_snapshots", {}).get(scene_index),
            "chapter_state": context.get("chapter_state", {}),
            "chapter_scene_context": context.get("chapter_scene_context", ""),
            "repair_strategy": "patch",
            "repair_plan": {
                "root_cause": (
                    f"{order.reason}\n"
                    "Use a minimal local insertion or replacement to satisfy the missing contract beat. "
                    "Do not rewrite the whole scene. Preserve unaffected wording, facts, POV, chronology, "
                    "and all listed protected terms."
                ),
                "preserve": preserve,
                "remove": order.expected_after_repair.get("remove", []),
            },
            "revision_hints": [
                {
                    "problem": order.reason,
                    "target_span": self._primary_target_span(order),
                    "strategy": "contract_completion_patch",
                    "auto_revise_allowed": True,
                    "preserve": preserve,
                    "expected_after_repair": order.expected_after_repair,
                }
            ],
        }
        if protection_retry:
            agent_context["protection_retry"] = protection_retry
            agent_context["previous_rejected_rewrite"] = {
                "reason": protection_retry.get("reason"),
                "result_hash": protection_retry.get("rejected_result_hash"),
            }

        self._inject_case_file(agent_context, case_file, scene_index)
        self._inject_skill_contract_envelope(agent_context, order, context)
        result = await self.scene_repairer.execute(agent_context)
        repaired = result.get("repaired_text", current_text)
        if not result.get("success") or not repaired:
            raise RuntimeError(
                f"SceneRepairer contract completion patch failed for order {order.order_id}: "
                f"{result.get('error', 'unknown')}"
            )
        # 修复（循环 #17 D17-2）：修复"成功但无效"——当 SceneRepairer 返回 success=True
        # 但 repaired == current_text 时，说明 LLM 声称修复成功但实际未修改文本。
        # 原逻辑直接返回 current_text，导致 order 被标记为 succeeded 但问题未修复，
        # 终验 recheck 会发现违规仍然存在（如果 recheck 覆盖了该场景）。
        # 修复：文本无变化时抛出 RuntimeError，让 order 被标记为 failed，
        # 触发重试或降级到其他修复策略。
        # 大局观评估（铁律 12 五问）：
        #   1. 能解决目标问题吗？是——防止"成功但无效"的修复被误判为成功
        #   2. 会引入新问题吗？否——failed order 会走 recoverable_delta 或 blocking 路径，
        #      这正是"修复无效"时应该走的路径
        #   3. 耗时成本？可能微增——failed order 可能触发重试，但重试本就是修复无效时的正确行为
        #   4. 更低成本的替代方案？否——D17-1（扩大 recheck 范围）是 complementary 修复，
        #      但在源头标记 failed 比在 recheck 阶段发现问题更高效
        #   5. 对下一轮修复的影响？正面——failed order 的违规会被传递到下一轮，让修复更精准
        if repaired == current_text:
            raise RuntimeError(
                f"SceneRepairer contract completion patch produced no text change "
                f"for order {order.order_id}: repair_type={order.repair_type}, "
                f"scene={scene_index}, reason={order.reason[:200]}"
            )
        return repaired

    @staticmethod
    def _primary_target_span(order: ChapterRepairOrder) -> str:
        for violation in order.violation_details or []:
            if isinstance(violation, dict):
                span = str(violation.get("target_span") or violation.get("span") or "").strip()
                if span:
                    return span
        return order.instruction

    async def _repair_cross_scene_alignment(
        self,
        order: ChapterRepairOrder,
        current_text: str,
        scene_index: int,
        scene_texts: dict[int, str],
        context: dict[str, Any],
        case_file: dict[str, Any] | None = None,
        protection_retry: dict[str, Any] | None = None,
    ) -> str:
        """cross_scene_alignment: 在 owner_scene 上执行修复以对齐跨场景一致性。

        修复逻辑在 owner_scene 上执行，修改 owner_scene 的正文，
        使其与其他 target_scenes 中的声明对齐。
        跨场景修复必须看到完整案卷。
        """
        case_file = case_file or {}
        owner_scene = order.owner_scene
        if owner_scene is None:
            _logger.warning(
                "ChapterRepairExecutor: cross_scene_alignment order %s has no owner_scene, using scene_index",
                order.order_id,
            )
            owner_scene = scene_index

        owner_text = scene_texts.get(owner_scene, current_text)
        violations = self._build_violations_from_order(order)

        # 收集跨场景上下文
        cross_scene_context = ""
        for idx in order.target_scenes:
            if idx != owner_scene and idx in scene_texts:
                cross_scene_context += f"\n--- 场景 {idx} 正文 ---\n{scene_texts[idx]}\n"

        preserve = self._merge_preserve_terms_for_retry(
            order.expected_after_repair.get("preserve", []),
            protection_retry,
        )
        agent_context = {
            **self._scene_repair_protocol_context(order, context),
            "generated_text": owner_text,
            "violations": violations,
            "scene_contract": context.get("scene_contracts", {}).get(owner_scene, {}),
            "scene_truth_snapshot": context.get("scene_truth_snapshots", {}).get(owner_scene),
            "chapter_state": context.get("chapter_state", {}),
            "chapter_scene_context": context.get("chapter_scene_context", ""),
            "related_scene_context": cross_scene_context,
            "repair_strategy": "patch" if protection_retry else "cross_scene_alignment",
            "repair_plan": {
                "root_cause": order.reason,
                "preserve": preserve,
                "remove": order.expected_after_repair.get("remove", []),
            },
            "conflicts": [
                {
                    "fact": order.instruction,
                    "text_claim": order.reason,
                    "suggestion": order.expected_after_repair.get("suggestion", ""),
                }
            ],
        }
        if protection_retry:
            agent_context["protection_retry"] = protection_retry
            agent_context["previous_rejected_alignment"] = {
                "reason": protection_retry.get("reason"),
                "result_hash": protection_retry.get("rejected_result_hash"),
            }
            agent_context["revision_hints"] = [
                {
                    "problem": (
                        f"{order.reason}\n"
                        "Retry because the previous cross-scene candidate removed protected terms."
                    ),
                    "target_span": self._primary_target_span(order),
                    "strategy": "cross_scene_alignment_protection_retry",
                    "auto_revise_allowed": True,
                    "preserve": preserve,
                    "expected_after_repair": order.expected_after_repair,
                }
            ]

        # 跨场景修复必须注入完整案卷
        self._inject_case_file(agent_context, case_file, owner_scene)
        self._inject_skill_contract_envelope(agent_context, order, context)

        result = await self.scene_repairer.execute(agent_context)
        repaired = result.get("repaired_text", owner_text)

        # 如果修复的就是当前场景，直接返回；否则返回当前场景原文
        if owner_scene == scene_index:
            return repaired
        # 非 owner_scene 的场景不需要修改正文，但订单标记为 succeeded
        return current_text

    def _apply_contract_patch_order(
        self,
        order: ChapterRepairOrder,
        scene_index: int,
        context: dict[str, Any],
        case_file: dict[str, Any] | None,
        current_text: str,
    ) -> dict[str, Any]:
        """Apply a contract-only patch to in-memory contract context."""
        case_file = case_file or {}
        text_hash = _compute_text_hash(current_text)
        contract_patch = self._contract_patch_payload(order)
        updated_targets: list[str] = []

        def apply_to_contract(label: str, contract: Any) -> None:
            if not isinstance(contract, dict):
                return
            for key, value in contract_patch.items():
                if key.startswith("_"):
                    continue
                if key == "fbi_contract_repair_notes":
                    notes = contract.setdefault(key, [])
                    if isinstance(notes, list):
                        notes.extend(value if isinstance(value, list) else [value])
                    else:
                        contract[key] = value
                    continue
                contract[key] = value
            updated_targets.append(label)

        scene_contracts = context.get("scene_contracts")
        if isinstance(scene_contracts, dict):
            apply_to_contract(f"context.scene_contracts[{scene_index}]", scene_contracts.get(scene_index))
        apply_to_contract("context.scene_contract", context.get("scene_contract"))

        all_scene_contracts = case_file.get("all_scene_contracts")
        if isinstance(all_scene_contracts, dict):
            apply_to_contract(f"case_file.all_scene_contracts[{scene_index}]", all_scene_contracts.get(scene_index))

        _logger.info(
            "ChapterRepairExecutor: contract_patch order %s scene=%d targets=%s keys=%s",
            order.order_id,
            scene_index,
            updated_targets,
            sorted(contract_patch),
        )

        return {
            "accepted": True,
            "failures": [],
            "base_hash": text_hash,
            "result_hash": text_hash,
            "base_length": len(current_text or ""),
            "result_length": len(current_text or ""),
            "changed": False,
            "contract_changed": bool(updated_targets),
            "contract_patch": contract_patch,
            "contract_targets_updated": updated_targets,
            "executor_path": "contract_patch",
        }

    @staticmethod
    def _contract_patch_payload(order: ChapterRepairOrder) -> dict[str, Any]:
        patch = dict(order.expected_after_repair or {})
        patch.pop("violation_resolved", None)
        patch.pop("description", None)
        patch.pop("preserve", None)
        patch.pop("remove", None)
        if patch:
            return patch
        return {
            "fbi_contract_repair_notes": [{
                "order_id": order.order_id,
                "reason": order.reason,
                "instruction": order.instruction,
                "source_violation_ids": list(order.source_violation_ids),
            }],
            "_contract_patch_note": "No structured contract delta was provided; recorded repair intent.",
        }

    async def _repair_contract_patch(
        self,
        order: ChapterRepairOrder,
        current_text: str,
        scene_index: int,
        context: dict[str, Any],
        case_file: dict[str, Any] | None = None,
    ) -> str:
        """contract_patch: 修改场景合同后重新生成。

        Stub 实现：标记订单为需要合同修改，记录 expected_after_repair
        供上层系统处理合同变更后重新触发生成。
        """
        audit = self._apply_contract_patch_order(
            order,
            scene_index,
            context,
            case_file,
            current_text,
        )
        order.status = "succeeded" if audit["accepted"] else "failed"
        order.result_text_hash = audit["result_hash"]
        order.repair_audit = audit
        return current_text

    @staticmethod
    def _handle_manual_review(
        order: ChapterRepairOrder,
        current_text: str,
    ) -> str:
        """manual_review: 跳过自动修复，标记为需要人工审核。

        不修改正文，订单状态设为 skipped。
        """
        _logger.info(
            "ChapterRepairExecutor: manual_review order %s - marking for human review",
            order.order_id,
        )
        # 手动审核订单标记为 skipped，不修改正文
        order.status = "skipped"
        return current_text

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_violations_from_order(order: ChapterRepairOrder) -> list[dict]:
        """从 ChapterRepairOrder 构造违规列表，供修复 Agent 使用。"""
        if order.violation_details:
            return [dict(v) for v in order.violation_details if isinstance(v, dict)]

        violations = []
        for vid in order.source_violation_ids:
            violations.append({
                "type": order.repair_type,
                "severity": order.priority,
                "detail": order.reason,
                "target_span": order.instruction,
                "expected_behavior": order.expected_after_repair.get("description", ""),
                "violation_id": vid,
            })
        if not violations:
            # 如果没有 source_violation_ids，至少构造一条
            violations.append({
                "type": order.repair_type,
                "severity": order.priority,
                "detail": order.reason,
                "target_span": order.instruction,
                "expected_behavior": order.expected_after_repair.get("description", ""),
            })
        return violations

    @staticmethod
    def _inject_case_file(
        agent_context: dict[str, Any],
        case_file: dict[str, Any],
        current_scene_index: int,
    ) -> None:
        """将完整案卷上下文注入 agent_context，供修复师做知情修复。

        case_file 应包含：
        - all_scene_texts: 全章场景正文 {scene_index: text}
        - all_scene_contracts: 全章场景合同
        - chapter_outline_contract: 大纲合同
        - chapter_state: 章节状态
        - review_packets: 原始审阅结果
        - repair_plan: FBI 修复计划
        - scene_truth_snapshots: 场景事实快照
        - previous_repair_output: 上一轮修复结果（如有）
        - recheck_failure_samples: 复检失败样本（如有）
        """
        if not case_file:
            return

        # 全章场景正文 — 修复师需要看到完整上下文
        all_scene_texts = case_file.get("all_scene_texts")
        if all_scene_texts:
            agent_context["all_scene_texts"] = all_scene_texts

        # 全章场景合同
        all_scene_contracts = case_file.get("all_scene_contracts")
        if all_scene_contracts:
            agent_context["all_scene_contracts"] = all_scene_contracts

        # 大纲合同
        chapter_outline_contract = case_file.get("chapter_outline_contract")
        if chapter_outline_contract:
            agent_context["chapter_outline_contract"] = chapter_outline_contract

        # 原始审阅结果
        review_packets = case_file.get("review_packets")
        if review_packets:
            # 只传当前场景的 review packet，避免上下文膨胀
            if isinstance(review_packets, dict):
                scene_packet = review_packets.get(current_scene_index)
                if scene_packet is None:
                    scene_packet = review_packets.get(str(current_scene_index))
            elif isinstance(review_packets, (list, tuple)):
                scene_packet = (
                    review_packets[current_scene_index]
                    if 0 <= current_scene_index < len(review_packets)
                    else None
                )
            else:
                scene_packet = None
            if scene_packet:
                agent_context["original_review_packet"] = (
                    scene_packet if isinstance(scene_packet, dict)
                    else scene_packet.model_dump() if hasattr(scene_packet, "model_dump")
                    else str(scene_packet)
                )

        review_case_file = case_file.get("review_case_file")
        if review_case_file:
            agent_context["review_case_file"] = review_case_file

        # FBI 修复计划
        repair_plan = case_file.get("repair_plan")
        if repair_plan:
            agent_context["fbi_repair_plan"] = (
                repair_plan if isinstance(repair_plan, dict)
                else repair_plan.model_dump() if hasattr(repair_plan, "model_dump")
                else str(repair_plan)
            )

        # 上一轮修复结果
        previous_repair = case_file.get("previous_repair_output")
        if previous_repair:
            agent_context["previous_repair_output"] = previous_repair

        # 复检失败样本
        recheck_failures = case_file.get("recheck_failure_samples")
        if recheck_failures:
            agent_context["recheck_failure_samples"] = recheck_failures
