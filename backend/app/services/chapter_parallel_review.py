"""整章并行审校服务。

ChapterParallelReviewService: 接收 scene_texts + scene_contracts，并行调用 SceneReviewWorker，汇总 SceneReviewPacket[]
SceneReviewWorker: 对单个场景执行 QualityGate + state_delta extraction + worldview claim extraction + repairability classification

约束：
- 不提交状态
- 不写世界观正式事实
- 不写章节正文
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

from app.models.chapter_review import SceneReviewPacket
from app.services.scene_contract_protocol import bind_contract_violation
from app.services.quality_gate import QualityGate

_logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 可修复性分类辅助
# ---------------------------------------------------------------------------

_CONTRACT_VIOLATION_TYPES: set[str] = {
    "scene_contract_compile_blocked",
    "scene_contract_compiler_unavailable",
    "missing_contract",
    "forbidden_triggered",
    "missing_must_show",
    "ending_state_not_reached",
    "forbidden_recap_violation",
    "forbidden_assertion_triggered",
    "required_ambiguity_broken",
}

_CROSS_SCENE_VIOLATION_TYPES: set[str] = {
    "fact_conflict",
    "timeline_conflict",
    "setting_conflict",
    "identity_conflict",
    "pov_conflict",
    "naming_conflict",
    "clue_provenance_error",
    "clue_provenance_error_proposition",
    "unprovenanced_clue",
    "clue_missing_source",
    "fcip_registration_error",
    "fcip_text_violation",
}

_SYSTEM_VIOLATION_TYPES: set[str] = {
    "empty_text",
    "critic_parse_error",
    "consistency_check_unavailable",
    "fcip_check_unavailable",
    "scene_contract_compiler_unavailable",
    "proposition_layer_unavailable",
    "proposition_extractor_unavailable",
}


def _classify_violation_repairability(violation: dict) -> str:
    """Classify one finding without inheriting the scene packet's worst lane."""
    vtype = str(violation.get("type") or violation.get("violation_type") or "")
    if vtype in _SYSTEM_VIOLATION_TYPES or vtype.endswith("_unavailable"):
        return "degraded_system_issue"
    if vtype in _CONTRACT_VIOLATION_TYPES:
        return "contract_repair_required"
    if vtype in _CROSS_SCENE_VIOLATION_TYPES or vtype.startswith("cross_scene_"):
        return "cross_scene_fixable"
    return "auto_fixable"


def _classify_repairability(
    blocking_violations: list[dict],
    advisory_violations: list[dict],
) -> str:
    """根据违反列表分类可修复性。

    分类优先级（从高到低）：
    1. human_review_required — 存在无法自动修复的违反（如解析失败、系统不可用）
    2. contract_repair_required — 存在合同级违反
    3. cross_scene_fixable — 存在跨场景违反
    4. auto_fixable — 仅有局部可修复违反
    5. clean — 无违反
    """
    all_violations = blocking_violations + advisory_violations

    if not all_violations:
        return "clean"

    # Packet status summarizes the worst lane for orchestration only.  Each
    # finding receives its own repairability below so a transient validator
    # failure cannot turn an unrelated prose repair into manual review.
    for v in all_violations:
        if _classify_violation_repairability(v) == "degraded_system_issue":
            return "human_review_required"

    # 检查合同级违反
    for v in all_violations:
        if v.get("type", "") in _CONTRACT_VIOLATION_TYPES:
            return "contract_repair_required"

    # 检查跨场景违反
    for v in all_violations:
        if v.get("type", "") in _CROSS_SCENE_VIOLATION_TYPES:
            return "cross_scene_fixable"

    # 仅有局部问题
    return "auto_fixable"


def _extract_affected_scenes(violations: list[dict], current_scene_index: int) -> list[int]:
    """从违反列表中提取受影响的场景索引。"""
    affected: set[int] = set()
    for v in violations:
        evidence = v.get("evidence")
        if isinstance(evidence, dict):
            for key in ("affected_scenes", "conflicting_scenes", "related_scenes"):
                scenes = evidence.get(key, [])
                if isinstance(scenes, list):
                    for s in scenes:
                        if isinstance(s, int) and s != current_scene_index:
                            affected.add(s)
        # 从 target_span 中提取场景引用
        target_span = v.get("target_span")
        if isinstance(target_span, dict):
            ref_scene = target_span.get("scene_index")
            if isinstance(ref_scene, int) and ref_scene != current_scene_index:
                affected.add(ref_scene)
    return sorted(affected)


def _extract_dependencies(
    state_delta: dict,
    worldview_claims: list[dict],
    current_scene_index: int,
) -> list[int]:
    """从状态变更和世界观声明中提取依赖的场景索引。"""
    deps: set[int] = set()
    # 从 state_delta 中提取
    if isinstance(state_delta, dict):
        for key in ("dependencies", "source_scenes", "preceding_scenes"):
            scenes = state_delta.get(key, [])
            if isinstance(scenes, list):
                for s in scenes:
                    if isinstance(s, int) and s != current_scene_index:
                        deps.add(s)
    # 从世界观声明中提取
    for claim in worldview_claims:
        if isinstance(claim, dict):
            source = claim.get("source_scene")
            if isinstance(source, int) and source != current_scene_index:
                deps.add(source)
    return sorted(deps)


def _comparable_state_claims_from_delta(state_delta: dict) -> dict:
    """Extract narrative state claims that are safe for cross-scene comparison.

    ``ChapterStateDelta.model_dump()`` also contains transport metadata and
    operation lists (project_id, scene_index, entity_deltas, etc.). Those are
    not opening/ending story state claims and must not become FBI conflicts.
    """
    if not isinstance(state_delta, dict):
        return {}

    claims: dict = {}
    narrative_time = state_delta.get("narrative_time")
    if isinstance(narrative_time, str) and narrative_time.strip():
        claims["narrative_time"] = narrative_time.strip()

    raw_patch = state_delta.get("raw_patch")
    if isinstance(raw_patch, dict):
        for key in ("objective_state", "subjective_views", "active_scene"):
            value = raw_patch.get(key)
            if value not in (None, "", [], {}):
                claims[key] = value

    return claims


def _normalize_scene_violation(
    violation: dict,
    scene_index: int,
    repairability: str,
    scene_contract: dict | None = None,
) -> dict:
    """Attach scene ownership metadata required by FBI chapter intake."""
    normalized = dict(violation)
    source_scene = normalized.get("source_scene")
    if not isinstance(source_scene, int):
        source_scene = scene_index
        normalized["source_scene"] = source_scene

    source_scenes = normalized.get("source_scenes")
    if not isinstance(source_scenes, list):
        source_scenes = [source_scene]
    elif source_scene not in source_scenes:
        source_scenes = [source_scene, *source_scenes]
    normalized["source_scenes"] = [s for s in source_scenes if isinstance(s, int)] or [scene_index]

    target_span = normalized.get("target_span")
    if isinstance(target_span, dict) and not isinstance(target_span.get("scene_index"), int):
        normalized["target_span"] = {**target_span, "scene_index": scene_index}

    normalized.setdefault(
        "repairability",
        _classify_violation_repairability(normalized),
    )
    return bind_contract_violation(
        scene_contract or {},
        normalized,
        scene_index=scene_index,
    )


def _has_hard_ending_contract(scene_contract: dict) -> bool:
    if not isinstance(scene_contract, dict):
        return False
    if str(scene_contract.get("ending_state") or "").strip():
        return True

    editor_enrichment = scene_contract.get("editor_enrichment") or {}
    if isinstance(editor_enrichment, dict) and str(editor_enrichment.get("ending_state") or "").strip():
        return True

    protocol = scene_contract.get("contract_protocol") or scene_contract.get("scene_contract_protocol") or {}
    if isinstance(protocol, dict):
        for key in ("required_outcomes", "required_transitions"):
            values = protocol.get(key) or []
            if isinstance(values, list) and any(str(v).strip() for v in values):
                return True

    return False


def _demote_unsupported_ending_state_violations(
    blocking_violations: list[dict],
    advisory_violations: list[dict],
    scene_contract: dict,
) -> tuple[list[dict], list[dict]]:
    if _has_hard_ending_contract(scene_contract):
        return blocking_violations, advisory_violations

    kept_blocking: list[dict] = []
    demoted: list[dict] = []
    for violation in blocking_violations:
        v_type = str(violation.get("type") or violation.get("violation_type") or "")
        if v_type == "ending_state_not_reached":
            item = dict(violation)
            item["severity"] = "medium"
            item["blocks_commit"] = False
            evidence = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
            item["evidence"] = {
                **evidence,
                "demoted_reason": "ending_state_not_reached_without_explicit_scene_ending_contract",
            }
            demoted.append(item)
        else:
            kept_blocking.append(violation)

    return kept_blocking, advisory_violations + demoted


# ---------------------------------------------------------------------------
# SceneReviewWorker
# ---------------------------------------------------------------------------


class SceneReviewWorker:
    """对单个场景执行并行审校，生成 SceneReviewPacket。

    流程：
    1. QualityGate.evaluate() — 质量门检查
    2. ScenePostProcessor.execute() — 一次调用同时提取 state_delta 和 detail_seeds（不提交/不写入）
    3. 分类 repairability
    4. 确定 dependencies / affected_scenes
    5. 构建 SceneReviewPacket
    """

    def __init__(self, scene_index: int) -> None:
        self.scene_index = scene_index

    async def review(self, context: dict) -> SceneReviewPacket:
        """执行单场景审校并返回 SceneReviewPacket。"""
        scene_index = self.scene_index
        candidate_text = context.get("generated_text", "")
        text_hash = hashlib.md5(candidate_text.encode("utf-8")).hexdigest()[:12] if candidate_text else ""

        packet = SceneReviewPacket(
            scene_index=scene_index,
            text_hash=text_hash,
            candidate_text=candidate_text,
            scene_contract=context.get("scene_contract", {}),
        )

        # ---- 1. QualityGate ----
        try:
            gate = QualityGate()
            # Never forward a shared request-scoped db into QualityGate: the
            # parallel review layer fans out per scene and QualityGate runs an
            # internal 5-way gather, so a shared session would be hit
            # concurrently. Pass only the factory; QualityGate opens its own
            # short-lived session at the DB boundary (FCIP read).
            gate_context = {k: v for k, v in context.items() if k != "db"}
            gate_result = await gate.evaluate(
                gate_context,
                level=str(context.get("quality_level") or "full"),
            )
            packet.quality_gate_report = gate_result
            blocking_violations = [
                v for v in gate_result.get("violations", [])
                if v.get("blocks_commit")
            ]
            advisory_violations = [
                v for v in gate_result.get("violations", [])
                if not v.get("blocks_commit")
            ]
            packet.blocking_violations, packet.advisory_violations = (
                _demote_unsupported_ending_state_violations(
                    blocking_violations,
                    advisory_violations,
                    packet.scene_contract,
                )
            )
        except Exception as exc:
            _logger.error(
                "[SceneReviewWorker] scene=%d QualityGate 失败: %s",
                scene_index, exc, exc_info=True,
            )
            packet.quality_gate_report = {"error": str(exc), "passed": False}
            packet.blocking_violations = []
            packet.advisory_violations = []

        # ---- 2. ScenePostProcessor（合并后的一次调用，仅提取不提交） ----
        state_delta: dict = {}
        try:
            baseline_packet = context.get("precomputed_review_packet")
            if isinstance(baseline_packet, SceneReviewPacket):
                packet.state_delta = dict(baseline_packet.state_delta or {})
                packet.opening_state_claims = dict(baseline_packet.opening_state_claims or {})
                packet.ending_state_claims = dict(baseline_packet.ending_state_claims or {})
                packet.foreshadowing_claims = list(baseline_packet.foreshadowing_claims or [])
                packet.worldview_claims = list(baseline_packet.worldview_claims or [])
                packet.timeline_claims = list(baseline_packet.timeline_claims or [])
                packet.character_state_claims = dict(baseline_packet.character_state_claims or {})
                packet.object_ownership_claims = list(baseline_packet.object_ownership_claims or [])
            else:
                processor_result = context.get("precomputed_review_extraction") or {}
                if not processor_result.get("available"):
                    from app.agents.scene_post_processor import ScenePostProcessor

                    processor = ScenePostProcessor()
                    # 构造不含 db 的上下文，防止 processor 写入
                    extract_context = {k: v for k, v in context.items() if k != "db"}
                    processor_result = await processor.execute(extract_context)

                # 提取 state_delta
                state_delta = processor_result.get("state_delta", {})
                packet.state_delta = state_delta
                packet.opening_state_claims = context.get("current_state", {})
                if isinstance(state_delta, dict) and state_delta:
                    packet.ending_state_claims = _comparable_state_claims_from_delta(state_delta)

                # 提取细节种子相关声明
                detail_seeds = processor_result.get("detail_seeds", [])
                packet.foreshadowing_claims = processor_result.get("foreshadowing_clues", [])
                packet.worldview_claims = self._extract_worldview_claims(detail_seeds)
                packet.timeline_claims = self._extract_timeline_claims(detail_seeds)
                packet.character_state_claims = self._extract_character_state_claims(detail_seeds)
                packet.object_ownership_claims = self._extract_object_claims(detail_seeds)
        except Exception as exc:
            _logger.warning(
                "[SceneReviewWorker] scene=%d ScenePostProcessor 失败: %s",
                scene_index, exc,
            )
            packet.state_delta = {}

        # ---- 4. 分类 repairability ----
        packet.repairability = _classify_repairability(
            packet.blocking_violations,
            packet.advisory_violations,
        )
        packet.blocking_violations = [
            _normalize_scene_violation(
                v,
                scene_index,
                packet.repairability,
                packet.scene_contract,
            )
            for v in packet.blocking_violations
            if isinstance(v, dict)
        ]
        packet.advisory_violations = [
            _normalize_scene_violation(
                v,
                scene_index,
                packet.repairability,
                packet.scene_contract,
            )
            for v in packet.advisory_violations
            if isinstance(v, dict)
        ]

        # ---- 5. 确定 dependencies / affected_scenes ----
        packet.dependencies = _extract_dependencies(
            state_delta, packet.worldview_claims, scene_index,
        )
        packet.affected_scenes = _extract_affected_scenes(
            packet.blocking_violations + packet.advisory_violations,
            scene_index,
        )

        return packet

    # ---- 声明提取辅助 ----

    @staticmethod
    def _extract_worldview_claims(detail_seeds: list[dict]) -> list[dict]:
        """从细节种子中提取世界观声明。"""
        claims: list[dict] = []
        for seed in detail_seeds:
            if not isinstance(seed, dict):
                continue
            entity_id = seed.get("entity_id", "")
            fact = seed.get("fact", "")
            tier = seed.get("tier", "")
            # 世界观声明：涉及世界规则、地点、物品的 T1/T2 级事实
            if tier in ("T1", "T2") and fact:
                claims.append({
                    "entity_id": entity_id,
                    "fact": fact,
                    "tier": tier,
                    "claim_type": "worldview",
                })
        return claims

    @staticmethod
    def _extract_timeline_claims(detail_seeds: list[dict]) -> list[dict]:
        """从细节种子中提取时间线声明。"""
        claims: list[dict] = []
        for seed in detail_seeds:
            if not isinstance(seed, dict):
                continue
            narrative_time = seed.get("narrative_time", "")
            if narrative_time:
                claims.append({
                    "entity_id": seed.get("entity_id", ""),
                    "narrative_time": narrative_time,
                    "fact": seed.get("fact", ""),
                    "claim_type": "timeline",
                })
        return claims

    @staticmethod
    def _extract_character_state_claims(detail_seeds: list[dict]) -> dict:
        """从细节种子中提取角色状态声明。"""
        claims: dict[str, list[dict]] = {}
        for seed in detail_seeds:
            if not isinstance(seed, dict):
                continue
            entity_id = seed.get("entity_id", "")
            fact = seed.get("fact", "")
            if not entity_id or not fact:
                continue
            tier = seed.get("tier", "")
            if tier in ("T1", "T2"):
                claims.setdefault(entity_id, []).append({
                    "fact": fact,
                    "tier": tier,
                })
        return claims

    @staticmethod
    def _extract_object_claims(detail_seeds: list[dict]) -> list[dict]:
        """从细节种子中提取物品归属声明。"""
        claims: list[dict] = []
        for seed in detail_seeds:
            if not isinstance(seed, dict):
                continue
            fact = seed.get("fact", "")
            entity_id = seed.get("entity_id", "")
            object_keywords = (
                "持有", "拿", "带", "捡", "获得", "失去", "丢弃",
                "物品", "武器", "道具",
            )
            if any(kw in fact for kw in object_keywords):
                claims.append({
                    "entity_id": entity_id,
                    "fact": fact,
                    "claim_type": "object_ownership",
                })
        return claims


# ---------------------------------------------------------------------------
# ChapterParallelReviewService
# ---------------------------------------------------------------------------


class ChapterParallelReviewService:
    """整章并行审校服务。

    接收 scene_texts + scene_contracts + 共享上下文，
    为每个场景创建 SceneReviewWorker 并行执行，
    汇总所有 SceneReviewPacket 结果。

    约束：
    - 不提交状态
    - 不写世界观正式事实
    - 不写章节正文
    """

    async def review_chapter(
        self,
        scene_texts: list[str],
        scene_contracts: list[dict],
        shared_context: dict[str, Any] | None = None,
    ) -> list[SceneReviewPacket]:
        """并行审校整章所有场景。

        Args:
            scene_texts: 每个场景的文本，按 scene_index 排列。
            scene_contracts: 每个场景的合同，与 scene_texts 一一对应。
            shared_context: 所有场景共享的上下文（project_id, chapter_number,
                current_state, core_facts 等）。

        Returns:
            SceneReviewPacket 列表，与输入场景一一对应。
            单个场景审校失败时，对应 packet 标记为 unavailable。
        """
        if len(scene_texts) != len(scene_contracts):
            raise ValueError(
                f"scene_texts 长度 ({len(scene_texts)}) 与 "
                f"scene_contracts 长度 ({len(scene_contracts)}) 不匹配"
            )

        shared = shared_context or {}
        scene_count = len(scene_texts)

        _logger.info(
            "[ChapterParallelReview] 开始并行审校 %d 个场景",
            scene_count,
        )

        # 为每个场景构造上下文并创建 worker
        async def _review_scene(index: int) -> SceneReviewPacket:
            worker = SceneReviewWorker(scene_index=index)
            scene_context = self._build_scene_context(
                index,
                scene_texts[index],
                scene_contracts[index],
                shared,
                all_scene_texts=scene_texts,
            )
            return await worker.review(scene_context)

        # 并行执行，单场景失败不影响其他场景
        results = await asyncio.gather(
            *[_review_scene(i) for i in range(scene_count)],
            return_exceptions=True,
        )

        packets: list[SceneReviewPacket] = []
        for i, result in enumerate(results):
            if isinstance(result, SceneReviewPacket):
                packets.append(result)
            elif isinstance(result, BaseException):
                _logger.error(
                    "[ChapterParallelReview] scene=%d 审校失败: %s",
                    i, result, exc_info=result,
                )
                packets.append(self._make_unavailable_packet(
                    i, scene_texts[i], scene_contracts[i], str(result),
                ))

        _logger.info(
            "[ChapterParallelReview] 并行审校完成: %d 个场景, %d 个不可用",
            scene_count,
            sum(1 for p in packets if p.unavailable),
        )

        return packets

    @staticmethod
    def _build_scene_context(
        scene_index: int,
        scene_text: str,
        scene_contract: dict,
        shared: dict,
        *,
        all_scene_texts: list[str] | None = None,
    ) -> dict:
        """为单个场景构造审校上下文。"""
        context = dict(shared)
        context.update({
            "scene_index": scene_index,
            "generated_text": scene_text,
            "scene_contract": scene_contract,
        })
        # 从 scene_contract 中提取常用字段
        if isinstance(scene_contract, dict):
            context.setdefault("chapter_number", scene_contract.get("chapter_number", 0))
            context.setdefault("word_budget", scene_contract.get("word_budget"))

        # The persisted story state is the chapter-opening baseline, not the
        # latest truth inside a multi-scene chapter.  A later scene must also
        # see explicit transitions established by earlier scene candidates;
        # otherwise a legitimate chapter-local state change is reported as a
        # contradiction with the previous chapter.  Keep this bounded so the
        # consistency prompt does not grow with the whole chapter.
        prior_scenes = [
            str(text or "").strip()
            for text in (all_scene_texts or [])[:scene_index]
            if str(text or "").strip()
        ]
        if prior_scenes:
            immediate = prior_scenes[-1]
            context.setdefault("previous_scene_ending", immediate[-1800:])
            context.setdefault(
                "previous_scenes_summary",
                "\n\n".join(text[-600:] for text in prior_scenes[-3:]),
            )
            context.setdefault("prior_scene_context_is_chronological", True)
        return context

    @staticmethod
    def _make_unavailable_packet(
        scene_index: int,
        scene_text: str,
        scene_contract: dict,
        reason: str,
    ) -> SceneReviewPacket:
        """为审校失败的场景构造不可用 packet。"""
        return SceneReviewPacket(
            scene_index=scene_index,
            text_hash=hashlib.md5(scene_text.encode("utf-8")).hexdigest()[:12] if scene_text else "",
            candidate_text=scene_text,
            scene_contract=scene_contract,
            unavailable=True,
            unavailable_reason=reason,
            repairability="human_review_required",
        )
