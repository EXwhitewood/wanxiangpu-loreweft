"""场景合同编译器：将原始场景合同编译为包含事实合同的结构化合同。

位于 EditorInChiefAgent 与 CoreGenerationAgent 之间，
负责将 raw_scene_contract 编译为 CompiledContract（含 FactContract）。
所有规则必须题材无关、作品无关、项目无关。
"""

from __future__ import annotations

import json
import logging
from typing import Any

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
from app.services.scene_provenance import (
    build_scene_provenance,
    normalize_scene_contract_v2,
    render_scene_provenance,
)
from app.services.scene_contract_protocol import build_scene_contract_protocol

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 不确定事实标记词（来自大纲中的悬念/疑点标记）
# ---------------------------------------------------------------------------
_UNCERTAINTY_MARKERS = ("未揭示", "疑点", "悬念", "未知", "待解", "不明")


def _as_list(value: Any) -> list:
    """将任意值安全转换为非空元素列表。"""
    if value is None:
        return []
    if isinstance(value, list):
        return [item for item in value if item not in (None, "")]
    return [value]


def _as_dict(value: Any) -> dict:
    """将任意值安全转换为字典。"""
    if isinstance(value, dict):
        return value
    return {}


class SceneContractCompiler:
    """场景合同编译器。

    将原始场景合同与上下文信息编译为包含 FactContract 的
    CompiledContract，供 CoreGenerationAgent 使用。
    """

    async def compile(self, context: dict) -> CompiledContract:
        """编译原始场景合同为结构化合同。

        Args:
            context: 编译上下文，包含以下键：
                - raw_scene_contract: 原始场景合同
                - scene_provenance: 场景溯源信息
                - chapter_state: 章节状态
                - story_state: 故事状态
                - outline_beat: 大纲节拍
                - worldview_context: 世界观上下文
                - foreshadowing_ops: 伏笔操作列表
                - genre_profile: 题材配置

        Returns:
            编译后的 CompiledContract
        """
        raw_contract = _as_dict(context.get("raw_scene_contract"))
        scene_provenance = _as_dict(context.get("scene_provenance"))
        chapter_state = _as_dict(context.get("chapter_state"))
        story_state = _as_dict(context.get("story_state"))
        outline_beat = _as_dict(context.get("outline_beat"))
        worldview_context = _as_dict(context.get("worldview_context"))
        genre_profile = _as_dict(context.get("genre_profile"))

        chapter_number = raw_contract.get("chapter_number") or chapter_state.get("chapter_number") or 0
        scene_index = raw_contract.get("scene_index", 0)

        # Step 1: 标准化原始场景合同
        normalized_contract = normalize_scene_contract_v2(
            raw_contract,
            genre_profile=genre_profile,
            chapter_number=chapter_number if isinstance(chapter_number, int) else None,
            scene_index=scene_index if isinstance(scene_index, int) else None,
            scene_beat=outline_beat,
            chapter_state=chapter_state,
        )
        logger.info(
            "场景合同已标准化: chapter=%s scene=%s",
            normalized_contract.get("chapter_number"),
            normalized_contract.get("scene_index"),
        )

        # Step 2: 如果传入的 provenance 为空，从标准化合同中取
        long_term_constraints = _as_dict(
            raw_contract.get("long_term_constraints")
            or normalized_contract.get("long_term_constraints")
        )
        if long_term_constraints:
            normalized_contract["long_term_constraints"] = long_term_constraints
        foreshadowing_ops = (
            _as_list(context.get("foreshadowing_ops"))
            + _as_list(raw_contract.get("foreshadowing_ops"))
            + _as_list(long_term_constraints.get("foreshadowing_constraints"))
        )
        if raw_contract.get("information_budget"):
            normalized_contract["information_budget"] = raw_contract.get("information_budget")
        for key in ("hard_must_show", "soft_guidance", "deferred_items"):
            if key in raw_contract:
                normalized_contract[key] = raw_contract.get(key)

        if not scene_provenance:
            scene_provenance = _as_dict(normalized_contract.get("scene_provenance"))
        normalized_contract["contract_protocol"] = build_scene_contract_protocol(normalized_contract)

        # Step 3: 编译 FactContract 各子项
        current_facts = self._compile_current_facts(scene_provenance, long_term_constraints)
        reference_facts = self._compile_reference_facts(scene_provenance)
        uncertain_facts = self._compile_uncertain_facts(outline_beat)
        forbidden_assertions = self._compile_forbidden_assertions(foreshadowing_ops, chapter_number)
        required_ambiguities = self._compile_required_ambiguities(outline_beat, long_term_constraints)
        responsibility_constraints = self._compile_responsibility_constraints(normalized_contract)
        spatial_constraints = self._compile_spatial_constraints(normalized_contract)
        temporal_constraints = self._compile_temporal_constraints(normalized_contract)
        clue_constraints = self._compile_clue_constraints(normalized_contract)

        fact_contract = FactContract(
            current_facts=current_facts,
            reference_facts=reference_facts,
            uncertain_facts=uncertain_facts,
            forbidden_assertions=forbidden_assertions,
            required_ambiguities=required_ambiguities,
            responsibility_constraints=responsibility_constraints,
            spatial_constraints=spatial_constraints,
            temporal_constraints=temporal_constraints,
            clue_constraints=clue_constraints,
        )

        # Step 4: 检测合同内部冲突
        conflicts = self._check_contract_conflicts(normalized_contract, fact_contract)
        blocked_contract = len(conflicts) > 0

        if blocked_contract:
            logger.warning(
                "场景合同存在内部冲突，已标记为 blocked: conflicts=%s",
                conflicts,
            )

        # Step 5: 收集编译警告
        compiler_warnings: list[str] = _as_list(normalized_contract.get("compiler_warnings"))
        if not current_facts:
            compiler_warnings.append("当前事实为空，场景可能缺少已确立事实")
        if not normalized_contract.get("pov_character") and not normalized_contract.get("pov", {}).get("character"):
            compiler_warnings.append("缺少视角角色，可能导致视角约束缺失")
        if blocked_contract:
            compiler_warnings.extend(conflicts)

        compiled = CompiledContract(
            scene_contract=normalized_contract,
            fact_contract=fact_contract,
            compiler_warnings=compiler_warnings,
            blocked_contract=blocked_contract,
        )

        logger.info(
            "场景合同编译完成: chapter=%s scene=%s blocked=%s warnings=%d",
            normalized_contract.get("chapter_number"),
            normalized_contract.get("scene_index"),
            blocked_contract,
            len(compiler_warnings),
        )

        return compiled

    async def compile_from_chapter_plan(
        self,
        chapter_plan,  # ChapterPlanContract
        scene_index: int = 0,
        context: dict | None = None,
    ) -> dict:
        """从章级合同编译场景合同

        每个场景生成前，系统应该从 ChapterPlanContract 中取当前场景合同。
        然后再合并商业节奏合同、叙事体验合同等，最终形成 CompiledSceneContract。
        """
        if context is None:
            context = {}

        # 从章级合同派生场景合同
        scene_contract = {
            "chapter_number": chapter_plan.chapter_number,
            "scene_index": scene_index,
            "chapter_function": chapter_plan.chapter_function,
            "target_emotion": chapter_plan.target_emotion,
            "main_conflict": chapter_plan.main_conflict,
            "must_show": chapter_plan.must_progress,
            "must_not_show": chapter_plan.must_not_resolve,
            "foreshadowing_ops": chapter_plan.foreshadowing_ops,
            "character_delta": chapter_plan.character_delta,
            "relationship_delta": chapter_plan.relationship_delta,
            "word_budget": chapter_plan.word_budget,
        }

        # 如果有原始场景合同数据，合并
        raw_contract = context.get("raw_scene_contract", {})
        if raw_contract:
            # 保留原始合同中章级合同没有的字段
            for key in ["ending_state", "forbidden", "pov", "setting", "characters_present"]:
                if key in raw_contract and key not in scene_contract:
                    scene_contract[key] = raw_contract[key]

        # 调用现有的编译逻辑
        try:
            compiled_context = {
                **context,
                "raw_scene_contract": scene_contract,
            }
            result = await self.compile(compiled_context)
            return {
                "scene_contract": result.scene_contract,
                "fact_contract": result.fact_contract.model_dump() if hasattr(result.fact_contract, 'model_dump') else {},
                "compiler_warnings": result.compiler_warnings,
            }
        except Exception as e:
            # 降级：返回原始场景合同
            return {
                "scene_contract": scene_contract,
                "fact_contract": {},
                "compiler_warnings": [f"编译降级: {e}"],
            }

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _compile_current_facts(self, provenance: dict, long_term_constraints: dict | None = None) -> list[str]:
        """从场景溯源中提取当前事实列表。

        合并 established_facts、character_states、completed_events
        和 active_constraints 为扁平字符串列表。
        """
        current_facts_raw = _as_dict(provenance.get("current_facts"))
        facts: list[str] = []

        established = _as_list(current_facts_raw.get("established_facts"))
        for item in established:
            text = str(item).strip()
            if text:
                facts.append(text)

        character_states = _as_dict(current_facts_raw.get("character_states"))
        for name, state in character_states.items():
            if state and str(state).strip():
                facts.append(f"{name}：{state}")

        completed_events = _as_list(current_facts_raw.get("completed_events"))
        for item in completed_events:
            text = str(item).strip()
            if text:
                facts.append(text)

        active_constraints = _as_list(current_facts_raw.get("active_constraints"))
        for item in active_constraints:
            text = str(item).strip()
            if text:
                facts.append(text)

        facts.extend(self._compile_long_term_current_facts(_as_dict(long_term_constraints)))

        logger.debug("编译当前事实: %d 条", len(facts))
        return facts

    def _compile_long_term_current_facts(self, long_term_constraints: dict) -> list[str]:
        facts: list[str] = []

        for item in _as_list(long_term_constraints.get("required_prior_facts")):
            if isinstance(item, dict):
                text = str(item.get("text") or item.get("claim") or "").strip()
                fact_type = str(item.get("type") or "prior_fact")
            else:
                text = str(item).strip()
                fact_type = "prior_fact"
            if text:
                facts.append(f"[{fact_type}] {text}")

        for item in _as_list(long_term_constraints.get("character_state_constraints")):
            if not isinstance(item, dict):
                continue
            name = str(item.get("character_name") or "").strip()
            state = _as_dict(item.get("state"))
            if not name or not state:
                continue
            facts.append(f"[character_state] {name}: {json.dumps(state, ensure_ascii=False, default=str)}")

        for item in _as_list(long_term_constraints.get("proposition_constraints")):
            if not isinstance(item, dict):
                continue
            claim = str(item.get("claim") or item.get("source_text") or "").strip()
            constraint = str(item.get("constraint") or "").strip()
            if claim:
                suffix = f" ({constraint})" if constraint else ""
                facts.append(f"[proposition] {claim}{suffix}")

        for item in _as_list(long_term_constraints.get("foreshadowing_constraints")):
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("foreshadowing_id") or "").strip()
            action = str(item.get("action") or "").strip()
            constraint = str(item.get("constraint") or "").strip()
            if not name:
                continue
            if action in {"reveal", "overdue_reveal"} and item.get("secret_canonical_statement"):
                facts.append(
                    "[foreshadowing:reveal] "
                    f"{name}: {item.get('secret_canonical_statement')} ({constraint})"
                )
            else:
                facts.append(f"[foreshadowing:{action or 'protect'}] {name} ({constraint})")

        return facts[:50]

    def _compile_reference_facts(self, provenance: dict) -> list[str]:
        """从场景溯源中提取参考设定事实列表。

        主要来源于 original_facts 中的角色命运等非当前事实。
        """
        original_facts_raw = _as_dict(provenance.get("original_facts"))
        facts: list[str] = []

        character_fates = _as_list(original_facts_raw.get("character_fates"))
        for fate in character_fates:
            if not isinstance(fate, dict):
                continue
            name = fate.get("name", "未知")
            cause = fate.get("cause", "")
            parts = [f"{name}"]
            if cause:
                parts.append(f"原因：{cause}")
            timeline = fate.get("timeline", "")
            if timeline:
                parts.append(f"时间：{timeline}")
            antagonist = fate.get("antagonist", "")
            if antagonist:
                parts.append(f"加害者：{antagonist}")
            facts.append("；".join(parts))

        logger.debug("编译参考设定事实: %d 条", len(facts))
        return facts

    def _compile_uncertain_facts(self, outline_beat: dict) -> list[str]:
        """从大纲节拍中提取不确定事实标记。

        扫描大纲中的"未揭示"、"疑点"、"悬念"等标记词，
        生成不确定事实列表。
        """
        facts: list[str] = []

        # 扫描大纲节拍的各个文本字段
        text_fields = [
            "hook", "conflict", "turn", "resolution",
            "mystery", "suspense", "unresolved",
            "key_question", "central_mystery",
        ]
        for field in text_fields:
            value = outline_beat.get(field)
            if not value or not isinstance(value, str):
                continue
            for marker in _UNCERTAINTY_MARKERS:
                if marker in value:
                    facts.append(f"[{marker}] {value.strip()}")
                    break

        # 扫描 thread_ops 中的不确定标记
        thread_ops = _as_list(outline_beat.get("thread_ops"))
        for op in thread_ops:
            if not isinstance(op, dict):
                continue
            op_type = op.get("type", "")
            description = op.get("description", "")
            if op_type in ("bury", "hint") and description:
                facts.append(f"[悬念操作] {description}")
            for marker in _UNCERTAINTY_MARKERS:
                if marker in str(description):
                    facts.append(f"[{marker}] {description.strip()}")
                    break

        # 扫描 must_show 中的不确定标记
        must_show = _as_list(outline_beat.get("must_show"))
        for item in must_show:
            text = str(item).strip()
            if not text:
                continue
            for marker in _UNCERTAINTY_MARKERS:
                if marker in text:
                    facts.append(f"[{marker}] {text}")
                    break

        logger.debug("编译不确定事实: %d 条", len(facts))
        return facts

    def _compile_forbidden_assertions(
        self,
        foreshadowing_ops: list,
        chapter_number: int,
    ) -> list[FactConstraint]:
        """从伏笔操作中生成禁止断言列表。

        对于尚未到达揭示窗口的伏笔，生成禁止断言约束，
        防止生成时提前泄露伏笔真相。
        """
        constraints: list[FactConstraint] = []

        for op in foreshadowing_ops:
            if not isinstance(op, dict):
                continue

            op_constraint = str(op.get("constraint") or "")
            if op_constraint in {
                "forbid_premature_reveal",
                "plant_clue_without_confirming_secret",
                "maintain_reader_state_without_resolution",
            }:
                name = str(op.get("name") or op.get("foreshadowing_id") or "foreshadowing")
                constraints.append(FactConstraint(
                    event_type="foreshadowing_boundary",
                    subject_role=str(op.get("subject_role") or ""),
                    reason=(
                        f"Foreshadowing '{name}' is not in a reveal state; "
                        "the scene may hint or maintain ambiguity but must not confirm the hidden truth."
                    ),
                    forbidden_certainty=["confirmed", "reported"],
                    allowed_certainty=["suspected", "inferred", "unknown", "misleading"],
                ))
                continue

            status = op.get("status", "")
            # 仅对尚未准备好揭示的伏笔生成禁止断言
            if status in ("resolved", "revealing"):
                continue

            reveal_window_start = op.get("reveal_window_start")
            reveal_window_end = op.get("reveal_window_end")

            # 判断是否在揭示窗口之前
            not_ready = False
            if reveal_window_start is not None:
                try:
                    if chapter_number < int(reveal_window_start):
                        not_ready = True
                except (ValueError, TypeError):
                    pass
            elif status in ("planned", "active", "dormant"):
                not_ready = True

            if not not_ready:
                continue

            secret = op.get("secret_canonical_statement", "") or op.get("name", "")
            if not secret:
                continue

            constraints.append(FactConstraint(
                event_type="foreshadowing_reveal",
                subject_role=op.get("subject_role", ""),
                reason=f"伏笔尚未到达揭示窗口（当前章节 {chapter_number}，揭示窗口起始 {reveal_window_start}）",
                forbidden_certainty=["confirmed", "reported"],
                allowed_certainty=["suspected", "inferred", "unknown"],
            ))

        logger.debug("编译禁止断言: %d 条", len(constraints))
        return constraints

    def _compile_required_ambiguities(
        self,
        outline_beat: dict,
        long_term_constraints: dict | None = None,
    ) -> list[FactConstraint]:
        """从大纲悬念/疑点标记中生成必须保持模糊的约束。

        确保生成时不会将本应保持悬念的内容过早确认。
        """
        constraints: list[FactConstraint] = []

        # 从大纲的悬念标记生成必须模糊约束
        suspense_items = _as_list(outline_beat.get("suspense_markers"))
        for item in suspense_items:
            if isinstance(item, dict):
                description = item.get("description", str(item))
            else:
                description = str(item).strip()

            if not description:
                continue

            constraints.append(FactConstraint(
                event_type="suspense_preservation",
                subject_role="",
                reason=f"大纲标记为悬念/疑点，必须保持模糊：{description[:80]}",
                allowed_certainty=["suspected", "inferred", "unknown", "misleading"],
                forbidden_certainty=["confirmed", "reported"],
            ))

        # 扫描大纲文本中的悬念标记词
        text_fields = ["hook", "mystery", "suspense", "key_question"]
        for field in text_fields:
            value = outline_beat.get(field)
            if not value or not isinstance(value, str):
                continue
            for marker in ("疑点", "悬念", "未揭示", "待解"):
                if marker in value:
                    constraints.append(FactConstraint(
                        event_type="suspense_preservation",
                        subject_role="",
                        reason=f"大纲字段 {field} 包含{marker}标记，必须保持模糊",
                        allowed_certainty=["suspected", "inferred", "unknown", "misleading"],
                        forbidden_certainty=["confirmed", "reported"],
                    ))
                    break

        constraints.extend(self._compile_long_term_ambiguities(_as_dict(long_term_constraints)))

        logger.debug("编译必须模糊约束: %d 条", len(constraints))
        return constraints

    def _compile_long_term_ambiguities(self, long_term_constraints: dict) -> list[FactConstraint]:
        constraints: list[FactConstraint] = []
        ambiguity_constraints = {
            "do_not_upgrade_to_confirmed_fact_without_evidence",
            "respect_character_knowledge_and_relationship",
        }
        for item in _as_list(long_term_constraints.get("proposition_constraints")):
            if not isinstance(item, dict):
                continue
            constraint = str(item.get("constraint") or "")
            if constraint not in ambiguity_constraints:
                continue
            claim = str(item.get("claim") or item.get("source_text") or "").strip()
            if not claim:
                continue
            constraints.append(FactConstraint(
                event_type="long_term_proposition_boundary",
                subject_role=str(item.get("subject_name") or ""),
                reason=(
                    "Long-term proposition must not be upgraded into a confirmed "
                    f"fact without on-page transition/evidence: {claim[:120]}"
                ),
                allowed_certainty=["suspected", "inferred", "unknown", "reported"],
                forbidden_certainty=["confirmed"],
            ))
        for item in _as_list(long_term_constraints.get("foreshadowing_constraints")):
            if not isinstance(item, dict):
                continue
            if str(item.get("constraint") or "") not in {
                "forbid_premature_reveal",
                "plant_clue_without_confirming_secret",
                "maintain_reader_state_without_resolution",
            }:
                continue
            name = str(item.get("name") or item.get("foreshadowing_id") or "").strip()
            if not name:
                continue
            constraints.append(FactConstraint(
                event_type="long_term_foreshadowing_boundary",
                subject_role=str(item.get("subject_role") or ""),
                reason=(
                    "Long-term foreshadowing must remain ambiguous until its "
                    f"reveal window: {name[:120]}"
                ),
                allowed_certainty=["suspected", "inferred", "unknown", "misleading"],
                forbidden_certainty=["confirmed", "reported"],
            ))
        return constraints[:24]

    def _compile_responsibility_constraints(
        self,
        scene_contract: dict,
    ) -> list[ResponsibilityConstraint]:
        """从场景合同上下文中生成责任归属约束。

        确保事件的责任归属（主动行为/受害者/被栽赃等）不被错误改写。
        """
        constraints: list[ResponsibilityConstraint] = []

        # 从 source_of_truth 中提取责任约束
        source_of_truth = _as_dict(scene_contract.get("source_of_truth"))
        must_show = _as_list(source_of_truth.get("must_show"))
        for item in must_show:
            if not isinstance(item, dict):
                continue
            event_type = item.get("event_type", "")
            subject_role = item.get("subject_role", "")
            responsibility = item.get("responsibility", "")
            if not event_type:
                continue
            allowed: list[Responsibility] = []
            forbidden: list[Responsibility] = []
            if responsibility and responsibility in (
                "active_actor", "victim", "framed", "accused",
                "coerced", "witness", "unknown",
            ):
                allowed = [responsibility]
            constraints.append(ResponsibilityConstraint(
                event_type=event_type,
                subject_role=subject_role,
                allowed_responsibility=allowed,
                forbidden_responsibility=forbidden,
                reason=f"场景合同指定责任归属：{responsibility or '未指定'}",
            ))

        # 从 editor_enrichment 中提取责任约束
        editor_enrichment = _as_dict(scene_contract.get("editor_enrichment"))
        additional_must_show = _as_list(editor_enrichment.get("additional_must_show"))
        for item in additional_must_show:
            if not isinstance(item, dict):
                continue
            event_type = item.get("event_type", "")
            subject_role = item.get("subject_role", "")
            responsibility = item.get("responsibility", "")
            if not event_type:
                continue
            allowed: list[Responsibility] = []
            if responsibility and responsibility in (
                "active_actor", "victim", "framed", "accused",
                "coerced", "witness", "unknown",
            ):
                allowed = [responsibility]
            constraints.append(ResponsibilityConstraint(
                event_type=event_type,
                subject_role=subject_role,
                allowed_responsibility=allowed,
                forbidden_responsibility=[],
                reason=f"编辑补充指定责任归属：{responsibility or '未指定'}",
            ))

        logger.debug("编译责任归属约束: %d 条", len(constraints))
        return constraints

    def _compile_spatial_constraints(
        self,
        scene_contract: dict,
    ) -> list[SpatialConstraint]:
        """从场景合同位置信息中生成空间约束。

        确保角色不会出现在与当前位置互斥的空间中。
        """
        constraints: list[SpatialConstraint] = []

        provenance = _as_dict(scene_contract.get("scene_provenance"))
        spatial_anchor = _as_dict(provenance.get("spatial_anchor"))

        current_location = spatial_anchor.get("current_location", "")
        destination_location = spatial_anchor.get("destination_location", "")

        # 从 pov 角色生成空间约束
        pov_character = ""
        pov = scene_contract.get("pov")
        if isinstance(pov, dict):
            pov_character = pov.get("character", "")
        if not pov_character:
            pov_character = scene_contract.get("pov_character", "")

        if pov_character and current_location:
            forbidden: list[str] = []
            # 如果当前位置和目的地不同，则禁止角色出现在目的地以外的其他已知位置
            # 此处仅标记明确的空间互斥
            if destination_location and destination_location != current_location:
                forbidden.append(f"非{current_location}且非前往{destination_location}途中的其他地点")

            constraints.append(SpatialConstraint(
                subject_name=pov_character,
                allowed_locations=[current_location] + (
                    [destination_location] if destination_location else []
                ),
                forbidden_locations=forbidden,
                reason=f"视角角色 {pov_character} 当前位于 {current_location}，空间位置必须一致",
            ))

        # 从角色状态中提取空间约束
        current_facts = _as_dict(provenance.get("current_facts"))
        character_states = _as_dict(current_facts.get("character_states"))
        for name, state in character_states.items():
            if name == pov_character:
                continue
            if isinstance(state, dict):
                location = state.get("location", "")
            elif isinstance(state, str) and "位置" in state:
                # 尝试从字符串中提取位置信息
                location = state.split("位置")[-1].strip("：: ") if "位置" in state else ""
            else:
                location = ""
            if location:
                constraints.append(SpatialConstraint(
                    subject_name=name,
                    allowed_locations=[location],
                    forbidden_locations=[],
                    reason=f"角色 {name} 已确立位于 {location}",
                ))

        logger.debug("编译空间约束: %d 条", len(constraints))
        return constraints

    def _compile_temporal_constraints(
        self,
        scene_contract: dict,
    ) -> list[TemporalConstraint]:
        """从 forbidden_recap_events 生成时间约束。

        确保已完成事件不会在正文中被重演或倒叙改写。
        """
        constraints: list[TemporalConstraint] = []

        provenance = _as_dict(scene_contract.get("scene_provenance"))
        timeline_anchor = _as_dict(provenance.get("timeline_anchor"))

        forbidden_recap_events = _as_list(timeline_anchor.get("forbidden_recap_events"))
        for event in forbidden_recap_events:
            text = str(event).strip()
            if not text:
                continue
            constraints.append(TemporalConstraint(
                event_description=text,
                must_before=[],
                must_after=[],
                no_replay=True,
                reason=f"事件已完成，禁止在正文中重演或倒叙改写：{text[:80]}",
            ))

        # 从 completed_events 生成时间约束
        current_facts = _as_dict(provenance.get("current_facts"))
        completed_events = _as_list(current_facts.get("completed_events"))
        for event in completed_events:
            text = str(event).strip()
            if not text:
                continue
            # 避免与 forbidden_recap_events 重复
            if any(text in str(existing) for existing in forbidden_recap_events):
                continue
            constraints.append(TemporalConstraint(
                event_description=text,
                must_before=[],
                must_after=[],
                no_replay=True,
                reason=f"已完成事件，不得重演：{text[:80]}",
            ))

        # 从 opening_state / ending_state 生成顺序约束
        opening_state = timeline_anchor.get("opening_state", "")
        ending_state = timeline_anchor.get("ending_state", "")
        if opening_state and ending_state:
            constraints.append(TemporalConstraint(
                event_description=f"场景从「{opening_state}」过渡到「{ending_state}」",
                must_before=[opening_state],
                must_after=[ending_state],
                no_replay=False,
                reason="场景状态转换顺序约束",
            ))

        logger.debug("编译时间约束: %d 条", len(constraints))
        return constraints

    def _compile_clue_constraints(
        self,
        scene_contract: dict,
    ) -> list[ClueConstraint]:
        """从场景合同线索中生成线索来源约束。

        确保每条线索都有来源角色、放置时间和发现条件。
        """
        constraints: list[ClueConstraint] = []

        provenance = _as_dict(scene_contract.get("scene_provenance"))
        clues = _as_list(provenance.get("clues"))

        for clue in clues:
            if not isinstance(clue, dict):
                continue

            description = clue.get("description", "")
            if not description:
                continue

            source_actor = clue.get("source_actor", "未知")
            placement_time = clue.get("placement_time", "未知")
            discovery_condition = clue.get("discovery_condition", "未知")

            # 判断是否缺少必要信息
            required_source = source_actor in ("未知", "", None)
            required_placement = placement_time in ("未知", "", None)
            required_discovery = discovery_condition in ("未知", "", None)

            constraints.append(ClueConstraint(
                clue_description=description,
                required_source_actor=required_source,
                required_placement_time=required_placement,
                required_discovery_condition=required_discovery,
                source_actor="" if required_source else str(source_actor),
                placement_time="" if required_placement else str(placement_time),
                discovery_condition="" if required_discovery else str(discovery_condition),
                reason=(
                    f"线索来源约束：放置者={'缺失' if required_source else '已明确'}，"
                    f"放置时间={'缺失' if required_placement else '已明确'}，"
                    f"发现条件={'缺失' if required_discovery else '已明确'}"
                ),
            ))

        # 同时检查 editor_enrichment 中的线索
        editor_enrichment = _as_dict(scene_contract.get("editor_enrichment"))
        enrichment_clues = _as_list(editor_enrichment.get("clues"))
        for clue in enrichment_clues:
            if not isinstance(clue, dict):
                continue
            description = clue.get("description", "")
            if not description:
                continue
            # 避免与 provenance 中的线索重复
            if any(description == c.clue_description for c in constraints):
                continue

            source_actor = clue.get("source_actor", "未知")
            placement_time = clue.get("placement_time", "未知")
            discovery_condition = clue.get("discovery_condition", "未知")

            required_source = source_actor in ("未知", "", None)
            required_placement = placement_time in ("未知", "", None)
            required_discovery = discovery_condition in ("未知", "", None)

            constraints.append(ClueConstraint(
                clue_description=description,
                required_source_actor=required_source,
                required_placement_time=required_placement,
                required_discovery_condition=required_discovery,
                source_actor="" if required_source else str(source_actor),
                placement_time="" if required_placement else str(placement_time),
                discovery_condition="" if required_discovery else str(discovery_condition),
                reason=(
                    f"编辑补充线索来源约束：放置者={'缺失' if required_source else '已明确'}，"
                    f"放置时间={'缺失' if required_placement else '已明确'}，"
                    f"发现条件={'缺失' if required_discovery else '已明确'}"
                ),
            ))

        logger.debug("编译线索约束: %d 条", len(constraints))
        return constraints

    def _check_contract_conflicts(
        self,
        scene_contract: dict,
        fact_contract: FactContract,
    ) -> list[str]:
        """检测场景合同与事实合同之间的内部冲突。

        Returns:
            冲突描述列表。非空则标记 blocked_contract=True。
        """
        conflicts: list[str] = []

        # 冲突1：must_show 与 forbidden_assertions 矛盾
        # 如果 must_show 要求展示某事件，但 forbidden_assertions 禁止确认该事件
        source_of_truth = _as_dict(scene_contract.get("source_of_truth"))
        must_show = _as_list(source_of_truth.get("must_show"))
        must_show_texts = [str(item) for item in must_show]

        for forbidden in fact_contract.forbidden_assertions:
            for show_text in must_show_texts:
                # 简单文本重叠检测
                if forbidden.event_type and forbidden.event_type in show_text:
                    conflicts.append(
                        f"must_show 要求展示「{show_text[:60]}」，"
                        f"但 forbidden_assertions 禁止确认该事件"
                    )

        # 冲突2：空间约束矛盾 - 同一角色出现在互斥位置
        location_map: dict[str, set[str]] = {}
        for spatial in fact_contract.spatial_constraints:
            name = spatial.subject_name
            allowed = set(spatial.allowed_locations)
            forbidden = set(spatial.forbidden_locations)
            if name not in location_map:
                location_map[name] = allowed
            else:
                # 检查允许位置和禁止位置是否有交集
                overlap = allowed & forbidden
                if overlap:
                    conflicts.append(
                        f"角色 {name} 的空间约束矛盾："
                        f"同时允许和禁止位置 {overlap}"
                    )
                # 合并允许位置
                location_map[name] = location_map[name] | allowed

        # 冲突3：时间约束矛盾 - 同一事件同时要求 must_before 和 must_after
        for temporal in fact_contract.temporal_constraints:
            overlap = set(temporal.must_before) & set(temporal.must_after)
            if overlap:
                conflicts.append(
                    f"时间约束矛盾：事件「{temporal.event_description[:60]}」"
                    f"同时要求在 {overlap} 之前和之后"
                )

        # 冲突4：责任归属约束矛盾 - 同一事件类型同时允许和禁止同一责任
        for resp in fact_contract.responsibility_constraints:
            overlap = set(resp.allowed_responsibility) & set(resp.forbidden_responsibility)
            if overlap:
                conflicts.append(
                    f"责任归属约束矛盾：事件「{resp.event_type}」"
                    f"同时允许和禁止责任类型 {overlap}"
                )

        if conflicts:
            logger.warning("检测到 %d 个合同内部冲突", len(conflicts))
        else:
            logger.debug("未检测到合同内部冲突")

        return conflicts
