import copy
import hashlib
import json
import logging
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import ValidationError

from app.db.db_models import Project
from app.agents.core_generation import CoreGenerationAgent
from app.agents.scene_post_processor import ScenePostProcessor
from app.agents.scene_repairer import SceneRepairer
from app.services.state_manager import StateManager
from app.services.memory_shell import ShellMemoryService
from app.services.memory_core import CoreMemoryService
from app.services.circuit_breaker import circuit_breaker
from app.services.scene_provenance import normalize_scene_contract_v2
from app.skills.scene_preparation import ScenePreparationSkill
from app.services.llm_task_profiles import LLMTaskType

logger = logging.getLogger(__name__)

_QUALITY_REPORT_KEYS = (
    "ai_flavor",
    "concept_budget",
    "character_voice",
    "reader_experience",
    "narrative_experience",
    "literary_quality",
    "mode_fit",
    "style_experience_conflict",
    "commercial_pacing",
    "scene_credibility",
)


def _has_quality_report_content(report: dict) -> bool:
    if not isinstance(report, dict):
        return False
    for key in (
        "advisories",
        "scores",
        "score",
        "status",
        "mode",
        "mode_fit",
        "conflicts",
        "contract",
        "overall_level",
    ):
        value = report.get(key)
        if value not in (None, "", [], {}):
            return True
    violations = report.get("violations")
    return isinstance(violations, list) and bool(violations)


def _collect_quality_reports_from_gate(quality_report: dict | None) -> dict:
    if not isinstance(quality_report, dict):
        return {}
    reports = quality_report.get("reports", {})
    if not isinstance(reports, dict):
        return {}

    collected: dict = {}
    for key in _QUALITY_REPORT_KEYS:
        report = reports.get(key)
        if isinstance(report, dict) and _has_quality_report_content(report):
            collected[key] = report

    experimental = reports.get("experimental", {})
    if isinstance(experimental, dict):
        for key in _QUALITY_REPORT_KEYS:
            report = experimental.get(key)
            if (
                key not in collected
                and isinstance(report, dict)
                and _has_quality_report_content(report)
            ):
                collected[key] = report

    narrative = collected.get("narrative_experience")
    if (
        "mode_fit" not in collected
        and isinstance(narrative, dict)
        and isinstance(narrative.get("mode_fit"), dict)
        and narrative.get("mode_fit")
    ):
        collected["mode_fit"] = narrative["mode_fit"]

    return collected


def normalize_editor_scene_contracts(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    return [dict(contract) for contract in value if isinstance(contract, dict)]


def _append_final_violation_detail(message: str, result: dict, error_code: str) -> str:
    if error_code not in ("recovery_exhausted", "recovery_non_repairable", "commit_blocked"):
        return message
    quality_report = result.get("consistency_report", {}).get("quality_gate", {})
    violations = quality_report.get("violations", []) if isinstance(quality_report, dict) else []
    blocking = [
        violation
        for violation in violations
        if isinstance(violation, dict) and violation.get("blocks_commit")
    ]
    if not blocking:
        return message
    worst = blocking[0]
    return f"{message}（最后一次违规：{worst.get('type', '未知')} - {worst.get('detail', '')}）"


def _proposition_effects_from_quality_report(quality_report: dict | None) -> dict:
    from app.services.chapter_output_effects import proposition_effects_from_quality_report

    return proposition_effects_from_quality_report(quality_report)


def _experience_effects_from_quality_report(quality_report: dict | None, scene_contract: dict | None) -> dict:
    from app.services.chapter_output_effects import quality_effects_from_quality_report

    return quality_effects_from_quality_report(quality_report, scene_contract)


async def run_scene_postprocess_fanout(
    *,
    project_id: str,
    chapter_number: int,
    generated_text: str,
    story_state,
    scene_package: dict,
    report: dict,
    scene_contract: dict | None = None,
) -> dict:
    """Run independent scene post-processing jobs concurrently.

    These jobs only produce pending effects; they do not apply state. The
    caller remains responsible for serial state commit ordering.
    """

    def state_to_dict(value) -> dict:
        if isinstance(value, dict):
            return value
        if hasattr(value, "model_dump"):
            try:
                dumped = value.model_dump()
                return dumped if isinstance(dumped, dict) else {}
            except Exception as exc:
                # P3-1 修复：model_dump 失败时记录告警，便于排查序列化异常
                logger.warning("state_to_dict model_dump failed: %s", exc)
                return {}
        return {}

    async def process_scene() -> dict:
        try:
            return await ScenePostProcessor().execute({
                "generated_text": generated_text,
                "current_state": state_to_dict(story_state),
                "project_id": project_id,
                "chapter_number": chapter_number,
                "scene_index": scene_package.get("scene_index", 0),
            })
        except Exception as exc:
            logger.warning("[ScenePipeline] Scene post-process fanout failed: %s", exc)
            return {"detail_seeds": [], "state_patch": {}, "state_delta": {}}

    # Worldview extraction is deliberately chapter-scoped.  Running the same
    # extractor for every scene produced data that had no durable consumer and
    # then repeated the work after commit.  The chapter outbox owns the single
    # canonical projection after the chapter is durable.
    postprocess_result = await process_scene()

    seeds = postprocess_result.get("detail_seeds", []) if isinstance(postprocess_result, dict) else []
    if not isinstance(postprocess_result, dict):
        postprocess_result = {}
    state_patch = postprocess_result.get("state_patch", {})
    state_delta = postprocess_result.get("state_delta", {})
    pending_effects = {
        "detail_seeds": seeds,
        "state_patch": state_patch,
        "state_delta": state_delta,
        "foreshadowing_detection": report.get("foreshadowing_detection", {}),
    }
    pending_effects.update(
        _proposition_effects_from_quality_report(report.get("quality_gate", {}))
    )
    pending_effects.update(
        _experience_effects_from_quality_report(
            report.get("quality_gate", {}),
            scene_contract if isinstance(scene_contract, dict) else {},
        )
    )
    return {
        "seeds": seeds,
        "state_patch": state_patch,
        "state_delta": state_delta,
        "pending_effects": pending_effects,
        "review_extraction": {
            "available": True,
            "detail_seeds": seeds,
            "state_patch": state_patch,
            "state_delta": state_delta,
            "foreshadowing_clues": postprocess_result.get("foreshadowing_clues", []),
        },
    }


def _deep_merge_quality_extensions(existing: dict | None, patch: dict | None) -> dict:
    def merge_dict(left: dict, right: dict) -> dict:
        result = dict(left or {})
        for child_key, child_value in (right or {}).items():
            if isinstance(child_value, list):
                current_value = result.get(child_key)
                if not isinstance(current_value, list):
                    current_value = []
                result[child_key] = list(dict.fromkeys([*current_value, *child_value]))
            elif isinstance(child_value, dict):
                current_value = result.get(child_key)
                if isinstance(current_value, dict):
                    result[child_key] = merge_dict(current_value, child_value)
                else:
                    result[child_key] = dict(child_value)
            elif child_key not in result:
                result[child_key] = child_value
        return result

    merged = merge_dict(dict(existing or {}), patch or {})
    merged.setdefault("schema_version", 1)
    return merged


async def _run_inline_ai_quality_revision(
    *,
    generated_text: str,
    scene_contract: dict | None,
    chapter_state: dict | None,
    character_cards: list[dict] | None,
    feature_policy,
    project_quality_memory: dict | None,
    chapter_number: int,
    scene_index: int,
) -> tuple[str, dict]:
    """Run current-turn AI-quality local patches before full validation.

    This sits between CoreGenerationAgent and SceneRecoveryController, so all
    later validation, extraction, trace, and writeback sees the revised text.

    反例驱动去 AI 味策略（零阻断）：
    - report 模式：只走确定性后处理（DeslopGateEngine），不调 LLM
    - assist/enforce 模式：先走确定性后处理，再走 LLM 修复（原有逻辑）
    - 任何步骤失败都回退原文，不阻断主流程
    """
    if not generated_text:
        return generated_text, {}

    try:
        from app.agents.ai_quality_coordinator import AIQualityCoordinatorAgent, CoordinatorInput

        coordinator = AIQualityCoordinatorAgent()
        source_modes = coordinator._resolve_modes(feature_policy)
        # 放宽模式判断：report 也运行（只走确定性路径，不调 LLM）
        active_inline_modes = {"report", "assist", "enforce"}
        if not any(mode in active_inline_modes for mode in source_modes.values()):
            return generated_text, {}

        # ===== 确定性后处理路径（report/assist/enforce 都走，零 LLM 调用）=====
        deterministic_text = generated_text
        deterministic_report: dict = {}
        try:
            from app.services.deslop_gate_engine import get_deslop_gate_engine
            engine = get_deslop_gate_engine()
            # 检测 Gate I/J/K/M（明喻/通感/虚假代理/重复句）
            detect_gates = ["I", "J", "K", "M"]
            deslop_reports = engine.detect(deterministic_text, gates=detect_gates)
            if deslop_reports:
                plan = engine.create_repair_plan(
                    deslop_reports,
                    max_delete_ratio=0.2,  # 保守上限，超限回退原文
                    preserve_plot=True,
                )
                deslop_result = engine.apply_repair(deterministic_text, plan, deslop_reports)
                if deslop_result.repaired_text and deslop_result.repaired_text != deterministic_text:
                    deterministic_text = deslop_result.repaired_text
                    deterministic_report = {
                        "status": "applied",
                        "mode": "deterministic_post_process",
                        "gates_applied": plan.gates_to_apply,
                        "total_issues": deslop_result.total_issues,
                        "fixed_issues": deslop_result.fixed_issues,
                        "deletion_ratio": round(deslop_result.deletion_ratio, 4),
                    }
                else:
                    deterministic_report = {
                        "status": "skipped",
                        "mode": "deterministic_post_process",
                        "reason": "no_change_after_repair",
                    }
            else:
                deterministic_report = {
                    "status": "skipped",
                    "mode": "deterministic_post_process",
                    "reason": "no_deslop_issues_detected",
                }
        except Exception as det_err:
            logger.warning(f"[ScenePipeline] 确定性后处理失败，回退原文: {det_err}")
            deterministic_report = {
                "status": "failed",
                "mode": "deterministic_post_process",
                "error": str(det_err),
            }

        # report 模式只走确定性后处理，不调 LLM
        llm_active_modes = {"assist", "enforce"}
        if not any(mode in llm_active_modes for mode in source_modes.values()):
            if deterministic_text != generated_text:
                return deterministic_text, deterministic_report
            return generated_text, deterministic_report

        # ===== LLM 修复路径（assist/enforce 才走，原有逻辑）=====
        quality_reports = _build_inline_quality_reports(
            generated_text=deterministic_text,
            scene_contract=scene_contract or {},
            chapter_state=chapter_state or {},
            character_cards=character_cards or [],
            source_modes=source_modes,
        )
        if not quality_reports:
            if deterministic_text != generated_text:
                return deterministic_text, deterministic_report
            return generated_text, {
                "status": "skipped",
                "reason": "no_current_turn_quality_reports",
                "mode": "inline_pre_gate",
                "deterministic": deterministic_report,
            }

        coord_output = coordinator.coordinate(CoordinatorInput(
            scene_contract=scene_contract or {},
            draft_text=deterministic_text,
            quality_reports=quality_reports,
            project_quality_memory=project_quality_memory or {},
            source_modes=source_modes,
            current_chapter=chapter_number,
            current_scene=scene_index,
        ))
        eligible_hints = [
            hint for hint in coord_output.revision_hints
            if isinstance(hint, dict) and hint.get("auto_revise_allowed")
        ][:2]
        report = {
            "status": "skipped",
            "mode": "inline_pre_gate",
            "risk_summary": coord_output.risk_summary,
            "revision_hints": coord_output.revision_hints,
            "eligible_hint_count": len(eligible_hints),
            "deterministic": deterministic_report,
        }
        if not eligible_hints:
            report["reason"] = "no_auto_revisable_hints"
            if deterministic_text != generated_text:
                return deterministic_text, report
            return generated_text, report

        revision_result = await SceneRepairer().execute({
            "generated_text": deterministic_text,
            "draft_text": deterministic_text,
            "scene_contract": scene_contract or {},
            "revision_hints": eligible_hints,
            "quality_reports": quality_reports,
            "repair_strategy": "inline",
        })
        applied_patches = revision_result.get("applied_patches", [])
        skipped_patches = revision_result.get("skipped_patches", [])
        skipped_reason_counts: dict = {}
        if isinstance(skipped_patches, list):
            for patch in skipped_patches:
                if isinstance(patch, dict):
                    reason = patch.get("reason") or "unknown"
                    skipped_reason_counts[reason] = skipped_reason_counts.get(reason, 0) + 1
        revised_text = (
            revision_result.get("repaired_text")
            or revision_result.get("revised_text")
            or deterministic_text
        )
        report.update({
            "status": "applied" if revision_result.get("success") else "failed",
            "applied_patch_count": revision_result.get("applied_patch_count", len(applied_patches) if isinstance(applied_patches, list) else 0),
            "applied_patches": applied_patches if isinstance(applied_patches, list) else [],
            "skipped_patches": skipped_patches if isinstance(skipped_patches, list) else [],
            "skipped_reason_counts": revision_result.get("skipped_reason_counts", skipped_reason_counts),
            "draft_hash": revision_result.get("draft_hash", hashlib.sha256(deterministic_text.encode("utf-8")).hexdigest()),
            "final_hash": revision_result.get("final_hash", hashlib.sha256(revised_text.encode("utf-8")).hexdigest()),
            "failure_reason": revision_result.get("failure_reason", ""),
            "error": revision_result.get("error", ""),
        })
        if revision_result.get("success") and revised_text != generated_text:
            return revised_text, report
        # LLM 修复没改善，但确定性后处理可能已经改了
        if deterministic_text != generated_text:
            return deterministic_text, report
        return generated_text, report
    except Exception as exc:
        logger.warning(f"[ScenePipeline] AI质量内联修订失败: {exc}")
        return generated_text, {
            "status": "failed",
            "mode": "inline_pre_gate",
            "error": str(exc),
        }


def _build_inline_quality_reports(
    *,
    generated_text: str,
    scene_contract: dict,
    chapter_state: dict,
    character_cards: list[dict],
    source_modes: dict,
) -> dict:
    reports: dict = {}

    def inline_enabled(name: str) -> bool:
        return source_modes.get(name) in {"assist", "enforce"}

    def inline_mode(name: str) -> str:
        return str(source_modes.get(name) or "off")

    # AI flavor 在 report 模式下也运行检测器（生成 advisory 供质量记忆和反例驱动），但不做 inline revision
    ai_flavor_mode = inline_mode("ai_flavor")
    if ai_flavor_mode in ("report", "assist", "enforce"):
        from app.services.quality_checkers.ai_flavor_checker import AIFlavorChecker

        report = AIFlavorChecker().check(generated_text, scene_contract, chapter_state)
        report["mode"] = ai_flavor_mode
        reports["ai_flavor"] = report
    if inline_enabled("concept_budget"):
        from app.services.quality_checkers.concept_budget_checker import ConceptBudgetChecker

        report = ConceptBudgetChecker().check(generated_text, scene_contract, chapter_state)
        report["mode"] = inline_mode("concept_budget")
        reports["concept_budget"] = report
    if inline_enabled("character_voice"):
        from app.services.quality_checkers.character_voice_checker import CharacterVoiceChecker

        report = CharacterVoiceChecker().check(
            generated_text, scene_contract, chapter_state, character_cards,
        )
        report["mode"] = inline_mode("character_voice")
        reports["character_voice"] = report
    if inline_enabled("narrative_experience"):
        from app.services.quality_checkers.narrative_experience_checker import NarrativeExperienceChecker
        from app.services.writing_mode_profile_service import WritingModeProfileService

        profile = WritingModeProfileService().get_profile(
            ((scene_contract.get("experience_contract") or {}).get("writing_mode_id") if isinstance(scene_contract, dict) else None)
            or "general"
        )
        report = NarrativeExperienceChecker().check(
            generated_text,
            (scene_contract or {}).get("experience_contract", {}),
            profile,
            scene_contract=scene_contract,
        )
        report["mode"] = inline_mode("narrative_experience")
        reports["narrative_experience"] = report
        if isinstance(report.get("mode_fit"), dict):
            mode_fit = dict(report["mode_fit"])
            mode_fit["mode"] = source_modes.get("mode_fit", "off")
            reports["mode_fit"] = mode_fit
    if inline_enabled("literary_quality"):
        from app.services.quality_checkers.literary_quality_checker import LiteraryQualityChecker
        from app.services.writing_mode_profile_service import WritingModeProfileService

        profile = WritingModeProfileService().get_profile(
            ((scene_contract.get("literary_quality_contract") or {}).get("writing_mode_id") if isinstance(scene_contract, dict) else None)
            or "general"
        )
        report = LiteraryQualityChecker().check(
            generated_text,
            (scene_contract or {}).get("literary_quality_contract", {}),
            profile,
        )
        report["mode"] = inline_mode("literary_quality")
        reports["literary_quality"] = report
    if inline_enabled("commercial_pacing"):
        from app.services.quality_checkers.commercial_pacing_checker import CommercialPacingChecker

        report = CommercialPacingChecker().check(
            generated_text,
            (scene_contract or {}).get("commercial_pacing_contract", {}),
            scene_contract=scene_contract,
        )
        report["mode"] = inline_mode("commercial_pacing")
        reports["commercial_pacing"] = report
    return reports


async def run_foreshadowing_post_check(
    project: Project,
    project_id: str,
    chapter_number: int,
    scene_index: int,
    generated_text: str,
    db: AsyncSession,
) -> dict:
    from app.engines.fcip_engine import FCIPEngine
    from app.services.foreshadowing_service import ForeshadowingService

    service = ForeshadowingService()
    engine = FCIPEngine(service)
    result = await engine.post_gen_writeback(
        project_id=project_id,
        chapter_number=chapter_number,
        generated_text=generated_text,
        db=db,
        apply_writeback=False,
    )

    return result


async def persist_scene_effects(
    project_id: str,
    chapter_number: int,
    scene_index: int,
    generated_text: str,
    effects: dict,
    db: AsyncSession | None = None,
    project: Project | None = None,
) -> None:
    _REQUIRED_EFFECTS = ("state_patch",)
    _BEST_EFFORT_EFFECTS = ("detail_seeds", "foreshadowing_detection")

    required_errors = []

    state_patch = effects.get("state_patch", {})
    if state_patch:
        try:
            await StateManager().apply_patch(project_id, copy.deepcopy(state_patch))
        except Exception as exc:
            if isinstance(exc, ValidationError):
                summary = f"状态补丁格式不兼容，包含 {len(exc.errors())} 个无法归一化字段"
            else:
                summary = str(exc)
            required_errors.append(f"state_patch: {summary}")
            logger.exception("[generation] REQUIRED effect failed - state_patch")

    if required_errors:
        raise RuntimeError(f"Required effects failed: {'; '.join(required_errors)}")

    shell_service = ShellMemoryService()
    for seed_index, seed in enumerate(effects.get("detail_seeds", [])):
        seed_data = dict(seed)
        seed_data["chapter_number"] = chapter_number
        seed_data["scene_number"] = scene_index + 1
        seed_data.setdefault("id", str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"loreweft:{project_id}:{chapter_number}:{scene_index}:detail:{seed_index}:"
            f"{seed_data.get('entity_id', '')}:{seed_data.get('fact') or seed_data.get('content', '')}",
        )))
        try:
            await shell_service.store_detail_seed(project_id, seed_data)
        except Exception as exc:
            logger.warning("[generation] best-effort effect failed - detail seed: %s", exc)

    # Worldview and foreshadowing persistence are intentionally absent here.
    # The post-commit worldview projection owns both so failed/uncommitted
    # chapters cannot mutate durable cards or clue state.

    # 方案 11 Part D Bug 1：删除 best-effort 强推 active→resolved。
    # 原逻辑绕过状态机直接 update_foreshadowing({"status": "resolved"})，
    # 导致伏笔状态在不应被推进时被推进。
    # 状态推进应由 fcip_engine.post_gen_writeback 通过 transition_state 状态机完成
    # （见 fcip_engine.py 的 apply_writeback 分支，方案 11 Part D Bug 2）。

    if db is not None:
        generation_trace = effects.get("generation_trace")
        if isinstance(generation_trace, dict) and generation_trace:
            try:
                from app.services.generation_trace_service import GenerationTraceService

                await GenerationTraceService().record_in_transaction(db, generation_trace)
            except Exception as exc:
                logger.warning("[generation] best-effort effect failed - generation trace: %s", exc)

        progression_mentions = effects.get("progression_mentions")
        if isinstance(progression_mentions, list) and progression_mentions:
            try:
                from app.repositories.progression_repository import ProgressionRepository

                progression_repository = ProgressionRepository()
                progression_status = str(effects.get("progression_status") or "candidate")
                for mention in progression_mentions:
                    if not isinstance(mention, dict):
                        continue
                    await progression_repository.add_candidate(
                        db,
                        project_id=project_id,
                        entity_type=str(mention.get("entity_type") or "unknown"),
                        entity_id=str(mention.get("entity_id") or mention.get("entity_name") or ""),
                        chapter_number=chapter_number,
                        scene_index=scene_index,
                        change_type="mention",
                        after_value={
                            "entity_name": mention.get("entity_name", ""),
                            "alias": mention.get("alias", ""),
                        },
                        evidence_text=str(mention.get("evidence_text") or ""),
                        status=progression_status,
                    )
            except Exception as exc:
                logger.warning("[generation] best-effort effect failed - progressions: %s", exc)
        await db.commit()


async def extract_chapter_facts(
    generated_text: str,
    chapter_number: int,
    character_cards: list[dict],
    scene_contract: dict | None = None,
) -> tuple[dict, str]:
    from app.services.agent_config import AgentConfigManager
    from app.services.llm_client import LLMClient

    config_manager = AgentConfigManager()
    config = await config_manager.get_agent_config("core_generation")
    llm = LLMClient(
        api_format=config.api_format,
        api_key=config.api_key,
        base_url=config.base_url,
        model=config.model,
    )

    char_names = [c.get("name", "") for c in character_cards if isinstance(c, dict)]

    system_prompt = (
        "你是一个事实提取器。从小说文本中提取已确立的关键事实和结构化场景状态。"
        "只提取文本中明确陈述的事实，不要推断。"
        "输出 JSON 格式：\n"
        '{"established_facts": ["事实1", "事实2", ...], '
        '"character_states": {"角色名": "当前状态描述", ...}, '
        '"completed_events": ["已完成事件1", "已完成事件2", ...], '
        '"active_constraints": ["仍有效的约束1", ...], '
        '"scene_ending": "场景结尾200-300字原文"}'
    )

    user_prompt = (
        f"第{chapter_number}章生成文本：\n{generated_text[:3000]}\n\n"
        f"涉及角色：{'、'.join(char_names) if char_names else '未知'}\n\n"
    )

    if scene_contract:
        ending_state = scene_contract.get("ending_state", "")
        if ending_state:
            user_prompt += f"场景预期结束状态：{ending_state}\n\n"

    user_prompt += "请提取本章已确立的关键事实、角色当前状态、已完成事件、当前约束和场景结尾原文："

    try:
        response = await llm.generate(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=0.1,
            task_type=LLMTaskType.JSON_DETECTION,
        )
        result = json.loads(response)
        if isinstance(result, dict):
            result.setdefault("completed_events", [])
            result.setdefault("active_constraints", [])
            result.setdefault("scene_ending", generated_text[-300:] if generated_text else "")
            return result, "llm_extractor"
    except Exception as exc:
        # LLM 返回非 JSON 或调用失败时降级到确定性 fallback 提取
        logger.debug("[generation] LLM 事实提取失败，使用 fallback: %s", exc)

    return (
        _fallback_extract_chapter_facts(
            generated_text,
            character_cards,
            scene_contract=scene_contract,
        ),
        "deterministic_extractor_fallback",
    )


def _fallback_extract_chapter_facts(
    generated_text: str,
    character_cards: list[dict] | None = None,
    *,
    scene_contract: dict | None = None,
) -> dict:
    """Deterministic fact extraction fallback using verbatim text snippets.

    The fallback avoids hallucinating structured lore: facts/events are copied
    from complete source sentences, and character states only say that a named
    character appears in the scene.
    """
    text = generated_text or ""
    character_cards = character_cards or []
    scene_contract = scene_contract or {}

    raw_sentences = re.split(r"(?<=[。！？!?])\s*|\n+", text)
    sentences: list[str] = []
    for raw in raw_sentences:
        sentence = re.sub(r"\s+", " ", raw or "").strip()
        if not sentence:
            continue
        if len(sentence) < 8 or len(sentence) > 140:
            continue
        if sentence.endswith(("？", "?")):
            continue
        sentences.append(sentence)

    def _is_fact_like(sentence: str) -> bool:
        return bool(re.search(r"(是|为|有|在|从|将|已|已经|发现|查获|取出|进入|离开|出现|证明|确认|暴露|留下)", sentence))

    def _is_event_like(sentence: str) -> bool:
        return bool(re.search(r"(走|进|出|取|递|举|看|说|问|答|查|验|抓|落|站|跪|打开|合上|离开|跟着|出现)", sentence))

    established_facts = []
    for sentence in sentences:
        if _is_fact_like(sentence) and sentence not in established_facts:
            established_facts.append(sentence)
        if len(established_facts) >= 5:
            break

    completed_events = []
    for sentence in sentences:
        if _is_event_like(sentence) and sentence not in completed_events:
            completed_events.append(sentence)
        if len(completed_events) >= 4:
            break

    character_states = {}
    names = [
        str(card.get("name") or "").strip()
        for card in character_cards
        if isinstance(card, dict) and str(card.get("name") or "").strip()
    ]
    for name in names:
        if name and name in text:
            character_states[name] = "appears in this scene"

    active_constraints = []
    ending_state = scene_contract.get("ending_state") or scene_contract.get("outcome")
    if ending_state:
        active_constraints.append(f"Expected ending state: {ending_state}")

    return {
        "established_facts": established_facts,
        "character_states": character_states,
        "completed_events": completed_events,
        "active_constraints": active_constraints,
        "scene_ending": generated_text[-300:] if generated_text else "",
    }


async def _build_core_facts(
    project: Project,
    db: AsyncSession | None = None,
) -> dict:
    core_facts = {}

    characters = (project.core_data or {}).get("characters", [])
    for c in characters:
        if isinstance(c, dict):
            name = c.get("name", "")
            if name:
                fact_entry = {
                    "type": "character",
                    "name": name,
                    "personality": c.get("personality", ""),
                    # 方案 3 A1：模型字段实际是 desire/deep_need
                    "external_goal": c.get("external_goal", "") or c.get("desire", ""),
                    "internal_desire": c.get("internal_desire", "") or c.get("deep_need", ""),
                    "arc": c.get("arc", ""),
                    # 方案 3 A3：补全新字段
                    "role": c.get("role", ""),
                    "faction": c.get("faction", ""),
                    "status": c.get("status", "active"),
                }
                if c.get("original_fate"):
                    fact_entry["original_fate"] = c["original_fate"]
                if c.get("relationships"):
                    fact_entry["relationships"] = c["relationships"]
                core_facts[f"角色_{name}"] = fact_entry

    try:
        from app.services.memory_core import CoreMemoryService
        memory = CoreMemoryService()
        rules = await memory.list_world_rules(str(project.id))
        for r in rules:
            r_data = r if isinstance(r, dict) else (r.model_dump() if hasattr(r, "model_dump") else {})
            name = r_data.get("name", "")
            if name:
                core_facts[f"铁则_{name}"] = {
                    "type": "world_rule",
                    "name": name,
                    "description": r_data.get("description", ""),
                    "category": r_data.get("category", ""),
                    "priority": r_data.get("priority", ""),
                }
    except Exception as exc:
        # 世界规则加载失败时降级，core_facts 仍可用但不包含铁则
        logger.warning("[generation] 加载世界规则失败，降级继续: %s", exc)

    outline_data = project.outline_data or {}
    chapters = outline_data.get("chapter_spine") or outline_data.get("chapters", [])
    for ch in chapters[:3]:
        ch_num = ch.get("chapter_number", "")
        title = ch.get("title", "")
        core_conflict = ch.get("core_conflict", ch.get("main_conflict", ""))
        if core_conflict:
            core_facts[f"大纲_第{ch_num}章"] = {
                "type": "outline",
                "chapter": ch_num,
                "title": title,
                "core_conflict": core_conflict,
            }

    core_data = project.core_data or {}
    history = core_data.get("generation_history", [])
    if isinstance(history, list) and history:
        latest = history[-1] if isinstance(history[-1], dict) else {}
        prev_state = latest.get("chapter_state", {})
        if prev_state:
            core_facts["前章状态"] = {
                "type": "previous_chapter_state",
                "established_facts": prev_state.get("established_facts", []),
                "completed_events": prev_state.get("completed_events", []),
                "active_constraints": prev_state.get("active_constraints", []),
                "character_states": prev_state.get("character_states", {}),
            }

    return core_facts


async def run_scene_pipeline(
    project_id: str,
    chapter_number: int,
    scene_package: dict,
    custom_instructions: str | None,
    project: Project,
    db: AsyncSession | None = None,
    pre_generated_text: str | None = None,
    skip_pre_generated_validation: bool = False,
    defer_pre_generated_postprocess: bool = False,
    recovery_budget: dict | None = None,
) -> dict:
    state_manager = StateManager()
    story_state = await state_manager.get_state(project_id)
    from app.services.generation_feature_policy import resolve_generation_feature_policy

    feature_policy = resolve_generation_feature_policy(project)
    if recovery_budget is None:
        recovery_budget = _recovery_budget_for_generation_mode(feature_policy.generation_mode)

    preparation = ScenePreparationSkill()
    prepared = await preparation.prepare(
        project_id=project_id,
        scene_beat=scene_package.get("scene_beat", {}),
        story_state=story_state.model_dump(),
        pov_character=scene_package.get("pov_character"),
        chapter_number=chapter_number,
        db=db,
    )
    scene_package["character_cards"] = prepared.get("character_cards", [])
    scene_package["location_cards"] = prepared.get("location_cards", [])

    genre_profile = None
    scene_contract = scene_package.get("scene_contract")
    if scene_contract:
        # 加载 Genre Profile 用于 V2 标准化
        genre_profile = None
        try:
            from app.services.genre_profile_service import GenreProfileService
            genre_svc = GenreProfileService()
            core_data = project.core_data if project else {}
            genre_profile = await genre_svc.get_project_genre_profile(
                str(project.id), db=db, core_data=core_data,
            )
        except Exception as exc:
            # Genre profile 加载失败时降级，使用默认 genre_profile=None
            logger.warning("[generation] Genre profile 加载失败，降级继续: %s", exc)

        scene_package["scene_contract"] = normalize_scene_contract_v2(
            scene_contract,
            genre_profile=genre_profile,
            chapter_number=chapter_number,
            scene_index=scene_package.get("scene_index", 0),
            scene_beat=scene_package.get("scene_beat", {}),
            chapter_state=scene_package.get("chapter_state", {}),
            character_cards=prepared.get("character_cards", []),
            pov_character=scene_package.get("pov_character"),
            previous_scene_ending=scene_package.get("previous_scene_ending", ""),
        )

    core_context = {
        "scene_context_package": {
            **scene_package,
            "focus_prompt": prepared.get("focus_prompt", ""),
            "character_cards": prepared.get("character_cards", []),
            "location_cards": prepared.get("location_cards", []),
            "historical_details": prepared.get("historical_details", []),
            "relevant_rules": prepared.get("relevant_rules", {}),
            "style_profile": prepared.get("style_profile"),
            "style_prompt": prepared.get("style_prompt", ""),
            "style_sample_passages": prepared.get("style_sample_passages", []),
            "style_embedding": prepared.get("style_embedding", {}),
            "persona_card": prepared.get("persona_card", {}),
            "style_statistics": prepared.get("style_statistics", {}),
            "evolution_report": prepared.get("evolution_report", {}),
            "writing_mode_profile": prepared.get("writing_mode_profile", {}),
            "quality_memory": prepared.get("quality_memory", {}),
            "chapter_state": scene_package.get("chapter_state", {}),
            "previous_scenes_summary": scene_package.get("previous_scenes_summary", ""),
        },
        "custom_instructions": custom_instructions,
        "chapter_summaries": prepared.get("chapter_summaries", ""),
        "forward_constraints": prepared.get("forward_constraints", ""),
        "relevant_rules": prepared.get("relevant_rules", {}),
        "generation_feature_policy": feature_policy,
    }

    try:
        from app.services.worldview_digest import WorldviewDigestService

        digest_service = WorldviewDigestService()
        chapter_context = await digest_service.get_chapter_context(
            project_id, chapter_number, db
        )
        if chapter_context:
            core_context["worldview_context"] = chapter_context
    except Exception as exc:
        # WorldviewDigest 加载失败时降级，core_context 不含 worldview_context
        logger.warning("[generation] WorldviewDigest 加载失败，降级继续: %s", exc)

    if not scene_package.get("scene_beat"):
        core_context["project_info"] = {
            "name": project.name,
            "description": project.description or "",
            "genre": project.genre or "",
            "word_count_target": project.word_count_target,
        }

    from app.services.scene_truth_snapshot import build_scene_truth_snapshot, validate_first_scene_baseline

    chapter_baseline_data = None
    if db is not None:
        try:
            from app.db.db_models import ChapterBaseline
            bl_query = await db.execute(
                select(ChapterBaseline).where(
                    ChapterBaseline.project_id == project.id if hasattr(project, 'id') else project_id,
                    ChapterBaseline.chapter_number == chapter_number,
                )
            )
            bl_obj = bl_query.scalar_one_or_none()
            if bl_obj and bl_obj.baseline_story_state:
                chapter_baseline_data = bl_obj.baseline_story_state
        except Exception as exc:
            # ChapterBaseline 查询失败时降级，跳过首场基线校验
            logger.warning("[generation] ChapterBaseline 查询失败，降级继续: %s", exc)

    scene_contract = scene_package.get("scene_contract", {})
    scene_index = scene_package.get("scene_index", 0)

    if scene_index == 0 and chapter_baseline_data:
        baseline_conflicts = validate_first_scene_baseline(
            chapter_baseline=chapter_baseline_data,
            story_state=story_state.model_dump() if hasattr(story_state, "model_dump") else {},
            scene_contract=scene_contract,
        )
        if baseline_conflicts:
            conflict_msgs = [f"{c['field']}: 基线={c['baseline_value']}, 大纲={c['outline_value']}" for c in baseline_conflicts]
            return {
                "generated_text": "",
                "status": "baseline_conflict",
                "error": f"首场与章节基线冲突: {'; '.join(conflict_msgs)}",
                "commit_blocked": True,
                "error_code": "outline_state_baseline_conflict",
            }

    truth_snapshot = build_scene_truth_snapshot(
        story_state=story_state.model_dump() if hasattr(story_state, "model_dump") else {},
        chapter_state=scene_package.get("chapter_state", {}),
        scene_contract=scene_contract,
        previous_scene_ending=scene_package.get("previous_scene_ending", ""),
        scene_id=f"ch{chapter_number}s{scene_index}",
    )
    core_context["scene_truth_snapshot"] = truth_snapshot

    word_budget = scene_package.get("word_budget") or scene_contract.get("word_budget") or {}
    core_context["word_budget"] = word_budget

    fact_contract = None
    compiler_warnings: list[str] = []
    if scene_contract and getattr(feature_policy, "narrative_contract_mode", "off") != "off":
        try:
            from app.services.scene_contract_compiler import SceneContractCompiler

            compiled = await SceneContractCompiler().compile({
                "raw_scene_contract": scene_contract,
                "scene_beat": scene_package.get("scene_beat", {}),
                "story_state": story_state.model_dump(),
                "chapter_state": scene_package.get("chapter_state", {}),
                "foreshadowing_ops": scene_package.get("foreshadowing_ops", []),
                "genre_profile": genre_profile,
                "worldview_context": core_context.get("worldview_context", {}),
                "scene_provenance": scene_contract.get("scene_provenance", {}),
            })
            fact_contract = compiled.fact_contract
            compiler_warnings = compiled.compiler_warnings
            if compiled.scene_contract:
                scene_contract = compiled.scene_contract
                scene_package["scene_contract"] = scene_contract
                core_context["scene_context_package"]["scene_contract"] = scene_contract
            core_context["fact_contract"] = fact_contract.model_dump()
            if compiled.blocked_contract and feature_policy.narrative_contract_mode == "enforce":
                return {
                    "generated_text": "",
                    "detail_seeds_count": 0,
                    "state_patch": {},
                    "pending_effects": {},
                    "consistency_report": {
                        "pass": False,
                        "error": "Scene contract compiler blocked this scene contract.",
                        "compiler_warnings": compiler_warnings,
                    },
                    "error_code": "scene_contract_compile_blocked",
                    "has_critical": True,
                    "commit_blocked": True,
                    "recovery_mode": "none",
                    "attempts": [],
                }
        except Exception as exc:
            if getattr(feature_policy, "narrative_contract_mode", "off") == "enforce":
                return {
                    "generated_text": "",
                    "detail_seeds_count": 0,
                    "state_patch": {},
                    "pending_effects": {},
                    "consistency_report": {
                        "pass": False,
                        "error": f"Scene contract compiler unavailable: {exc}",
                    },
                    "error_code": "scene_contract_compiler_unavailable",
                    "has_critical": False,
                    "commit_blocked": True,
                    "recovery_mode": "none",
                    "attempts": [],
                }

    scene_credibility_enabled = getattr(feature_policy, "scene_credibility_mode", "off") != "off"
    scene_credibility_contract = {}
    if scene_contract and scene_credibility_enabled:
        try:
            from app.services.scene_credibility_compiler import SceneCredibilityCompiler
            from app.services.quality_memory_service import QualityMemoryService

            quality_memory = QualityMemoryService().get_project_quality_memory(project)
            compiled_credibility = SceneCredibilityCompiler().compile({
                "project_id": project_id,
                "chapter_number": chapter_number,
                "scene_index": scene_index,
                "scene_contract": scene_contract,
                "fact_contract": fact_contract.model_dump() if hasattr(fact_contract, "model_dump") else (core_context.get("fact_contract") or {}),
                "scene_provenance": scene_contract.get("scene_provenance", {}) if isinstance(scene_contract, dict) else {},
                "scene_credibility_contract": scene_credibility_contract or (
                    scene_contract.get("scene_credibility_contract", {}) if isinstance(scene_contract, dict) else {}
                ),
                "chapter_state": scene_package.get("chapter_state", {}),
                "story_state": story_state.model_dump() if hasattr(story_state, "model_dump") else {},
                "character_cards": prepared.get("character_cards", []),
                "quality_memory": quality_memory,
            })
            scene_credibility_contract = compiled_credibility.contract.model_dump()
            scene_contract = dict(scene_contract)
            scene_contract["scene_credibility_contract"] = scene_credibility_contract
            existing_qe = scene_contract.get("quality_extensions")
            if not isinstance(existing_qe, dict):
                existing_qe = {"schema_version": 1}
            scene_contract["quality_extensions"] = _deep_merge_quality_extensions(
                existing_qe,
                compiled_credibility.quality_extensions_patch,
            )
            scene_package["scene_contract"] = scene_contract
            core_context["scene_context_package"]["scene_contract"] = scene_contract
            core_context["scene_credibility_contract"] = scene_credibility_contract
            core_context["scene_credibility_compiler_warnings"] = compiled_credibility.compiler_warnings
        except Exception as exc:
            logger.warning(f"[ScenePipeline] 场景可信度合同编译失败，降级继续: {exc}")

    experience_quality_enabled = any(
        getattr(feature_policy, field, "off") != "off"
        for field in (
            "writing_mode_profile_mode",
            "narrative_experience_mode",
            "literary_quality_mode",
            "mode_fit_mode",
            "style_experience_conflict_mode",
        )
    )
    if scene_contract and experience_quality_enabled:
        try:
            from app.agents.ai_quality_coordinator import AIQualityCoordinatorAgent
            from app.services.experience_context_builder import ExperienceContextBuilder
            from app.services.experience_contract_compiler import ExperienceContractCompiler
            from app.services.quality_memory_service import QualityMemoryService
            from app.services.writing_mode_profile_service import WritingModeProfileService

            writing_profile = WritingModeProfileService().get_project_profile(project, feature_policy)
            quality_memory = QualityMemoryService().get_project_quality_memory(project)
            exp_context = ExperienceContextBuilder().build_for_scene(
                project_id=project_id,
                project=project,
                chapter_number=chapter_number,
                scene_index=scene_index,
                scene_beat=scene_package.get("scene_beat", {}),
                story_state=story_state.model_dump() if hasattr(story_state, "model_dump") else {},
                scene_contract=scene_contract,
                prepared=prepared,
                writing_mode_profile=writing_profile,
                quality_memory=quality_memory,
            )
            compiled_quality = await ExperienceContractCompiler().compile(exp_context)
            scene_contract = dict(scene_contract)
            scene_contract["experience_contract"] = compiled_quality.experience_contract.model_dump()
            scene_contract["literary_quality_contract"] = compiled_quality.literary_quality_contract.model_dump()
            existing_qe = scene_contract.get("quality_extensions")
            if not isinstance(existing_qe, dict):
                existing_qe = {"schema_version": 1}
            memory_patch = AIQualityCoordinatorAgent().build_pre_generation_patch(
                project_id=str(project_id),
                scene_contract=scene_contract,
                quality_memory=quality_memory,
                feature_policy=feature_policy,
            )
            merged_qe = _deep_merge_quality_extensions(existing_qe, memory_patch)
            merged_qe = _deep_merge_quality_extensions(
                merged_qe,
                compiled_quality.quality_extensions_patch,
            )
            scene_contract["quality_extensions"] = merged_qe
            scene_package["scene_contract"] = scene_contract
            core_context["scene_context_package"]["scene_contract"] = scene_contract
            core_context["scene_context_package"]["writing_mode_profile"] = writing_profile.model_dump()
            core_context["scene_context_package"]["quality_memory"] = quality_memory
            core_context["writing_mode_profile"] = writing_profile.model_dump()
            core_context["style_conflict_report"] = compiled_quality.style_conflict_report
            core_context["experience_compiler_warnings"] = compiled_quality.compiler_warnings
        except Exception as exc:
            logger.warning(f"[ScenePipeline] 叙事体验/文学质量合同编译失败，降级继续: {exc}")

    commercial_pacing_enabled = getattr(feature_policy, "commercial_pacing_mode", "off") != "off"
    if scene_contract and commercial_pacing_enabled:
        try:
            from app.services.commercial_pacing_compiler import CommercialPacingCompiler

            compiled_pacing = CommercialPacingCompiler().compile({
                "project_id": project_id,
                "chapter_number": chapter_number,
                "scene_index": scene_index,
                "scene_count": len(scene_packages) if "scene_packages" in locals() else None,
                "scene_beat": scene_package.get("scene_beat", {}),
                "scene_contract": scene_contract,
                "commercial_pacing_profile_id": getattr(feature_policy, "commercial_pacing_profile_id", "general"),
            })
            scene_contract = dict(scene_contract)
            scene_contract["commercial_pacing_contract"] = compiled_pacing.commercial_pacing_contract.model_dump()
            existing_qe = scene_contract.get("quality_extensions")
            if not isinstance(existing_qe, dict):
                existing_qe = {"schema_version": 1}
            scene_contract["quality_extensions"] = _deep_merge_quality_extensions(
                existing_qe,
                compiled_pacing.quality_extensions_patch,
            )
            scene_package["scene_contract"] = scene_contract
            core_context["scene_context_package"]["scene_contract"] = scene_contract
            core_context["commercial_pacing_compiler_warnings"] = compiled_pacing.compiler_warnings
        except Exception as exc:
            logger.warning(f"[ScenePipeline] 商业节奏合同编译失败，降级继续: {exc}")

    reader_corpus_enabled = getattr(feature_policy, "reader_corpus_mode", "off") != "off"
    if scene_contract and reader_corpus_enabled:
        try:
            from app.services.reader_experience_corpus import ReaderExperienceCorpusService

            corpus_service = ReaderExperienceCorpusService()
            corpus_guidance = await corpus_service.build_generation_guidance(
                db,
                project=project,
                genre=getattr(project, "genre", "") or "",
                chapter_number=chapter_number,
                scene_index=scene_index,
                scene_count=None,
                scene_contract=scene_contract,
                limit=5,
            )
            if corpus_guidance.has_guidance:
                scene_contract = dict(scene_contract)
                existing_qe = scene_contract.get("quality_extensions")
                if not isinstance(existing_qe, dict):
                    existing_qe = {"schema_version": 1}
                scene_contract["quality_extensions"] = _deep_merge_quality_extensions(
                    existing_qe,
                    corpus_service.to_quality_extensions_patch(corpus_guidance),
                )
                scene_contract["reader_corpus_guidance"] = corpus_guidance.model_dump()
                scene_package["scene_contract"] = scene_contract
                core_context["scene_context_package"]["scene_contract"] = scene_contract
                core_context["reader_corpus_guidance"] = corpus_guidance.model_dump()
        except Exception as exc:
            logger.warning("[ScenePipeline] Reader corpus guidance unavailable, continuing without it: %s", exc)

    if not circuit_breaker.is_available("core_generation"):
        return {
            "generated_text": "",
            "detail_seeds_count": 0,
            "state_patch": {},
            "pending_effects": {},
            "consistency_report": {"pass": False, "error": "核心生成服务暂时不可用，请稍后重试"},
            "circuit_state": circuit_breaker.get_state("core_generation"),
            "error_code": "core_generation_circuit_open",
            "has_critical": False,
            "commit_blocked": True,
            "recovery_mode": "none",
            "attempts": [],
        }

    from app.services.scene_provenance import normalize_writer_context

    core_context, ctx_warnings = normalize_writer_context(core_context)
    critical_warnings = [w for w in ctx_warnings if w.get("severity") == "critical"]
    if critical_warnings:
        worst = critical_warnings[0]
        return {
            "generated_text": "",
            "detail_seeds_count": 0,
            "state_patch": {},
            "pending_effects": {},
            "consistency_report": {"pass": False, "error": f"Writer上下文类型异常：{worst['field']} 期望 {worst['expected_type']}，收到 {worst['received_type']}"},
            "error_code": "core_generation_context_invalid",
            "has_critical": False,
            "commit_blocked": True,
            "recovery_mode": "none",
            "attempts": [],
            "invalid_field": worst["field"],
            "expected_type": worst["expected_type"],
            "received_type": worst["received_type"],
            "context_warnings": ctx_warnings,
        }

    # Writer facts flow into the chapter outbox and are not persisted here.
    # 方案 26：writer style_compliance 随 result 流出，供 StyleReviewAdapter 收集偏离
    writer_scene_facts: dict = {}
    writer_style_compliance: dict = {}
    if pre_generated_text is not None:
        generated_text = pre_generated_text
    else:
        writer_input_packet = core_context["scene_context_package"].get("writer_input_packet")
        if not isinstance(writer_input_packet, dict) or not writer_input_packet:
            return {
                "generated_text": "",
                "detail_seeds_count": 0,
                "state_patch": {},
                "pending_effects": {},
                "consistency_report": {
                    "pass": False,
                    "error": "WriterInputPacket 缺失；场景合同必须在编排层完成编译。",
                },
                "error_code": "writer_input_packet_missing",
                "has_critical": False,
                "commit_blocked": True,
                "recovery_mode": "none",
                "attempts": [],
            }
        core_agent = CoreGenerationAgent()
        writer_exc = None
        try:
            core_result = await core_agent.execute(core_context)
            generated_text = core_result.get("generated_text", "")
            writer_scene_facts = core_result.get("scene_facts", {}) or {}
            writer_style_compliance = core_result.get("style_compliance", {}) or {}
            circuit_breaker.record_success("core_generation")
        except AttributeError as attr_exc:
            return {
                "generated_text": "",
                "detail_seeds_count": 0,
                "state_patch": {},
                "pending_effects": {},
                "consistency_report": {"pass": False, "error": f"Writer上下文类型异常：{attr_exc}"},
                "error_code": "core_generation_context_invalid",
                "has_critical": False,
                "commit_blocked": True,
                "recovery_mode": "none",
                "attempts": [],
                "underlying_error": str(attr_exc),
            }
        except Exception as exc:
            writer_exc = exc
            try:
                core_agent2 = CoreGenerationAgent()
                core_result = await core_agent2.execute(core_context)
                generated_text = core_result.get("generated_text", "")
                writer_scene_facts = core_result.get("scene_facts", {}) or {}
                writer_style_compliance = core_result.get("style_compliance", {}) or {}
                circuit_breaker.record_success("core_generation")
                writer_exc = None
            except AttributeError as attr_exc2:
                return {
                    "generated_text": "",
                    "detail_seeds_count": 0,
                    "state_patch": {},
                    "pending_effects": {},
                    "consistency_report": {"pass": False, "error": f"Writer上下文类型异常：{attr_exc2}"},
                    "error_code": "core_generation_context_invalid",
                    "has_critical": False,
                    "commit_blocked": True,
                    "recovery_mode": "none",
                    "attempts": [],
                    "underlying_error": str(attr_exc2),
                }
            except Exception as retry_exc:
                writer_exc = retry_exc
                circuit_breaker.record_failure("core_generation")

        if writer_exc is not None:
            return {
                "generated_text": "",
                "detail_seeds_count": 0,
                "state_patch": {},
                "pending_effects": {},
                "consistency_report": {"pass": False, "error": f"核心生成服务调用失败：{writer_exc}"},
                "circuit_state": circuit_breaker.get_state("core_generation"),
                "error_code": "core_generation_llm_error",
                "has_critical": False,
                "commit_blocked": True,
                "recovery_mode": "none",
                "attempts": [],
                "underlying_error": str(writer_exc),
            }

        if not generated_text:
            return {
                "generated_text": "",
                "detail_seeds_count": 0,
                "state_patch": {},
                "pending_effects": {},
                "consistency_report": {"pass": False, "error": "模型返回空正文"},
                "circuit_state": circuit_breaker.get_state("core_generation"),
                "error_code": "core_generation_empty_response",
                "has_critical": False,
                "commit_blocked": True,
                "recovery_mode": "none",
                "attempts": [],
            }

    scene_contract = scene_package.get("scene_contract")
    inline_revision_report = {}
    should_inline_revise = pre_generated_text is None or bool(scene_package.get("_chapter_writer_generated"))
    if generated_text and should_inline_revise:
        project_quality_memory = {}
        try:
            raw_quality_memory = (project.core_data or {}).get("quality_memory", {})
            if isinstance(raw_quality_memory, dict):
                project_quality_memory = raw_quality_memory
        except Exception as exc:
            # P3-1 修复：quality_memory 读取失败时记录告警
            logger.warning("read project quality_memory failed: %s", exc)
            project_quality_memory = {}

        generated_text, inline_revision_report = await _run_inline_ai_quality_revision(
            generated_text=generated_text,
            scene_contract=scene_contract if isinstance(scene_contract, dict) else {},
            chapter_state=scene_package.get("chapter_state", {}),
            character_cards=prepared.get("character_cards", []),
            feature_policy=feature_policy,
            project_quality_memory=project_quality_memory,
            chapter_number=chapter_number,
            scene_index=scene_package.get("scene_index", 0),
        )

    core_facts = await _build_core_facts(project, db)
    previous_propositions = []
    if db is not None and getattr(feature_policy, "proposition_extraction_mode", "off") != "off":
        try:
            from app.services.proposition_store_service import PropositionStoreService

            previous_propositions = await PropositionStoreService().get_proposition_models_for_scene_preparation(
                db,
                project_id=project_id,
                chapter_number=chapter_number,
            )
        except Exception as exc:
            # P3-1 修复：proposition 加载失败时记录告警
            logger.warning("load previous_propositions failed: %s", exc)
            previous_propositions = []
    report = {}
    has_critical = False
    commit_blocked = False
    recovery_mode = "none"
    attempts = []
    error_code = ""
    scene_recovery_status = ""
    underlying_error = ""
    recovery = {}

    if generated_text and not (pre_generated_text is not None and skip_pre_generated_validation):
        from app.services.scene_recovery_controller import SceneRecoveryController

        character_cards = prepared.get("character_cards", [])
        character_names = [
            c.get("name", "")
            for c in character_cards
            if isinstance(c, dict) and c.get("name")
        ]
        recovery = await SceneRecoveryController(budget=recovery_budget).recover_generated_scene(
            {
                "scene_contract": scene_contract,
                "compiled_scene_contract": scene_package.get("scene_contract", {}),
                "fact_contract": fact_contract,
                "compiler_warnings": compiler_warnings,
                "scene_package": scene_package,
                "scene_beat": scene_package.get("scene_beat", {}),
                "chapter_state": scene_package.get("chapter_state", {}),
                "scene_provenance": scene_contract.get("scene_provenance", {}) if isinstance(scene_contract, dict) else {},
                "previous_propositions": previous_propositions,
                "previous_scene_ending": scene_package.get("previous_scene_ending", ""),
                "previous_scenes_summary": scene_package.get("previous_scenes_summary", ""),
                "character_names": character_names,
                "character_cards": character_cards,
                "project": project,
                "project_id": project_id,
                "chapter_number": chapter_number,
                "scene_index": scene_package.get("scene_index", 0),
                "db": db,
                "core_facts": core_facts,
                "current_state": story_state.model_dump(),
                "worldview_context": core_context.get("worldview_context", {}),
                "scene_truth_snapshot": truth_snapshot,
                "word_budget": word_budget,
                "writing_mode_profile": prepared.get("writing_mode_profile", {}),
                # QualityGate has both legacy top-level consumers and newer
                # style_context consumers.  Populate both from one snapshot.
                "style_profile": prepared.get("style_profile") or {},
                "style_prompt": prepared.get("style_prompt", ""),
                "style_sample_passages": prepared.get("style_sample_passages", []),
                "style_embedding": prepared.get("style_embedding", {}),
                "persona_card": prepared.get("persona_card", {}),
                "style_statistics": prepared.get("style_statistics", {}),
                "evolution_report": prepared.get("evolution_report", {}),
                "style_context": {
                    "style_profile": prepared.get("style_profile"),
                    "style_features": (
                        prepared.get("style_profile") or {}
                    ).get("style_features", {}),
                    "style_prompt": prepared.get("style_prompt", ""),
                    "style_sample_passages": prepared.get("style_sample_passages", []),
                    "style_embedding": prepared.get("style_embedding", {}),
                    "persona_card": prepared.get("persona_card", {}),
                    "style_statistics": prepared.get("style_statistics", {}),
                    "evolution_report": prepared.get("evolution_report", {}),
                },
                "style_conflict_report": core_context.get("style_conflict_report", {}),
                "generation_feature_policy": feature_policy,
                "generation_features": feature_policy.model_dump() if hasattr(feature_policy, "model_dump") else {},
            },
            generated_text,
        )
        generated_text = recovery.get("generated_text", "")
        commit_blocked = recovery.get("commit_blocked", False)
        final_report = recovery.get("final_report") or {}
        has_critical = any(v.get("severity") == "critical" for v in final_report.get("violations", []))
        recovery_mode = recovery.get("recovery_mode", "none")
        attempts = recovery.get("attempts", [])
        scene_recovery_status = recovery.get("status", "")
        underlying_error = recovery.get("error", "")
        quality_report = recovery.get("final_report") or {}
        if recovery.get("fbi_v2_outcome"):
            report["fbi_v2_outcome"] = recovery.get("fbi_v2_outcome")
        report["quality_gate"] = quality_report
        report["scene_critic_violations"] = quality_report.get("violations", [])
        report["scene_recovery_status"] = scene_recovery_status
        report["degraded"] = recovery_mode != "none" or quality_report.get("degraded", False)
        report["pass"] = not commit_blocked

        if recovery.get("degraded"):
            report["degraded"] = True
            report["degraded_violations"] = recovery.get("degraded_violations", [])

        if commit_blocked and not generated_text:
            recovery_status = recovery.get("status", "")
            if recovery_status == "needs_review":
                error_code = "recovery_exhausted"
            elif recovery_status == "pending_validation":
                error_code = "recovery_pending_validation"
            elif recovery_status == "blocked_contract":
                error_code = "recovery_contract_blocked"
            elif recovery_status == "blocked_non_repairable":
                error_code = "recovery_non_repairable"
            elif recovery_status == "blocked_timeout":
                error_code = "recovery_timeout"
            else:
                error_code = "recovery_failed"
        elif commit_blocked and generated_text:
            recovery_status = recovery.get("status", "")
            if recovery_status == "pending_validator_retry":
                error_code = "pending_validator_retry"
            elif recovery_status == "pending_validation":
                error_code = "pending_validation"
            elif recovery_status in ("needs_review", "waiting_human_content_review"):
                error_code = "needs_review"
            elif recovery_status == "blocked_contract":
                error_code = "recovery_contract_blocked"
            else:
                error_code = "commit_blocked"

        fcip_report = quality_report.get("reports", {}).get("fcip", {})
        foreshadowing_check = fcip_report.get("detection", {})
        if foreshadowing_check:
            report["foreshadowing_detection"] = foreshadowing_check
            report["foreshadowing_violations_found"] = foreshadowing_check.get("violations_found", 0)
            report["foreshadowing_critical_count"] = foreshadowing_check.get("critical_count", 0)
            report["foreshadowing_high_count"] = foreshadowing_check.get("high_count", 0)
    elif generated_text:
        report["quality_gate"] = {
            "passed": True,
            "violations": [],
            "manual_override": True,
            "source": "user_accepted_revision",
        }
        report["scene_critic_violations"] = []
        report["scene_recovery_status"] = "manual_accepted"
        report["degraded"] = False
        report["pass"] = True

    if inline_revision_report:
        report["ai_quality_inline_revision"] = inline_revision_report
        if recovery is not None:
            recovery["ai_quality_inline_revision"] = inline_revision_report

    seeds = []
    state_patch = {}
    pending_effects = {}
    review_extraction = {}
    postprocess_deferred = bool(
        pre_generated_text is not None and defer_pre_generated_postprocess
    )

    if not commit_blocked and generated_text and not postprocess_deferred:
        postprocess = await run_scene_postprocess_fanout(
            project_id=project_id,
            chapter_number=chapter_number,
            generated_text=generated_text,
            story_state=story_state,
            scene_package=scene_package,
            report=report,
            scene_contract=scene_contract if isinstance(scene_contract, dict) else {},
        )
        seeds = postprocess["seeds"]
        state_patch = postprocess["state_patch"]
        pending_effects = postprocess["pending_effects"]
        review_extraction = postprocess["review_extraction"]

    trace_id = None
    if generated_text and feature_policy.prompt_trace_mode != "off":
        try:
            from app.services.generation_trace_service import GenerationTraceService

            trace_service = GenerationTraceService()
            trace = trace_service.build_metadata(
                project_id=project_id,
                chapter_number=chapter_number,
                scene_index=scene_package.get("scene_index", 0),
                context=core_context,
                generated_text=generated_text,
                quality_report=report.get("quality_gate", {}),
                recovery=recovery,
                mode=feature_policy.prompt_trace_mode,
                proposition_extraction=report.get("quality_gate", {}).get("reports", {}).get("hard_correctness", {}).get("proposition_extraction", {}),
                proposition_audit=report.get("quality_gate", {}).get("reports", {}).get("hard_correctness", {}).get("proposition_audit", {}),
                fact_contract=report.get("quality_gate", {}).get("reports", {}).get("narrative_contract", {}).get("fact_contract", {}),
            )
            trace_id = str(trace.get("id") or "")
            pending_effects["generation_trace"] = trace
            if trace_id:
                report["generation_trace_id"] = trace_id
        except Exception as exc:
            # P3-1 修复：generation_trace 记录失败时记录告警
            logger.warning("record generation_trace failed: %s", exc)
            trace_id = None

    # AI 质量协调：生成后闭环 —— 汇总 advisory、更新质量记忆
    # 注意：enforce 模式下即使 commit_blocked 也运行 coordinator，以便生成 revision_hints
    coordinator_report = {}
    if generated_text:
        try:
            from app.agents.ai_quality_coordinator import (
                AIQualityCoordinatorAgent,
                CoordinatorInput,
            )

            coordinator = AIQualityCoordinatorAgent()
            # 解析各检测器独立模式
            source_modes = coordinator._resolve_modes(feature_policy)

            # 如果所有检测器都是 off，跳过
            if any(m != "off" for m in source_modes.values()):
                quality_reports = _collect_quality_reports_from_gate(
                    report.get("quality_gate", {})
                )

                quality_memory = (project.core_data or {}).get("quality_memory", {})
                if not isinstance(quality_memory, dict):
                    quality_memory = {}

                coord_input = CoordinatorInput(
                    scene_contract=scene_contract or {},
                    draft_text=generated_text,
                    quality_reports=quality_reports,
                    project_quality_memory=quality_memory,
                    source_modes=source_modes,
                    current_chapter=chapter_number,
                    current_scene=scene_package.get("scene_index", 0),
                )
                coord_output = coordinator.coordinate(coord_input)

                coordinator_report = {
                    "risk_summary": coord_output.risk_summary,
                    "coordinator_mode": coord_output.coordinator_mode,
                }
                if coord_output.quality_guidance_patch:
                    coordinator_report["quality_guidance_patch"] = coord_output.quality_guidance_patch
                if coord_output.revision_hints:
                    coordinator_report["revision_hints"] = coord_output.revision_hints

                # Quality memory is persisted only through the accepted-scene
                # chapter outbox.  Writing it here used to pollute the project
                # when a later validator or the user cancelled the workflow.

                report["ai_quality_coordinator"] = coordinator_report

                # 生成后协调只汇总最终质量报告和质量记忆。
                # enforce 自动修订已前移到 CoreGeneration 初稿之后、full gate 之前的内联 patch 阶段。
        except Exception as coord_err:
            logger.warning(f"[ScenePipeline] AI质量协调失败: {coord_err}")

    should_detect_mentions = (
        feature_policy.mention_detector_mode in ("shadow", "candidate")
        or feature_policy.progression_mode in ("candidate", "active")
    )
    if generated_text and not commit_blocked and should_detect_mentions:
        try:
            from app.services.progression_service import ProgressionService

            write_progressions = (
                feature_policy.mention_detector_mode == "candidate"
                or feature_policy.progression_mode in ("candidate", "active")
            )
            progression = await ProgressionService().detect_candidates(
                db,
                project=project,
                project_id=project_id,
                chapter_number=chapter_number,
                scene_index=scene_package.get("scene_index", 0),
                text=generated_text,
                write=False,
                status="active" if feature_policy.progression_mode == "active" else "candidate",
            )
            if write_progressions and progression.get("mentions"):
                pending_effects["progression_mentions"] = progression.get("mentions", [])
                pending_effects["progression_status"] = (
                    "active" if feature_policy.progression_mode == "active" else "candidate"
                )
            report["progression_candidates"] = {
                "mention_count": progression.get("mention_count", 0),
                "record_count": 0,
                "pending_record_count": (
                    len(progression.get("mentions", [])) if write_progressions else 0
                ),
                "mode": feature_policy.progression_mode,
                "mention_detector_mode": feature_policy.mention_detector_mode,
            }
        except Exception as exc:
            # P3-1 修复：progression_candidates 计算失败时记录告警
            logger.warning("compute progression_candidates failed: %s", exc)
            report["progression_candidates"] = {"status": "unavailable"}

    return {
        "generated_text": generated_text,
        "detail_seeds_count": len(seeds),
        "state_patch": state_patch,
        "pending_effects": pending_effects,
        "review_extraction": review_extraction,
        "postprocess_deferred": postprocess_deferred,
        "consistency_report": report,
        "has_critical": has_critical,
        "commit_blocked": commit_blocked,
        "recovery_mode": recovery_mode,
        "attempts": attempts,
        "error_code": error_code,
        "scene_recovery_status": scene_recovery_status,
        "underlying_error": underlying_error,
        "needs_human_review": recovery.get("needs_human_review", False) if generated_text else False,
        "candidate_text": recovery.get("candidate_text", "") if generated_text else "",
        "fbi_v2_outcome": recovery.get("fbi_v2_outcome", {}) if generated_text else {},
        "trace_id": trace_id,
        # 方案 10：writer 事实填写单（可能为空，调用方需走降级路径）
        "writer_scene_facts": writer_scene_facts,
        # 方案 26：writer style_compliance（可能为空）
        "writer_style_compliance": writer_style_compliance,
        # P0-1 修复：流出 scene_truth_snapshot，供窗口修复管线 protection checker 使用
        "scene_truth_snapshot": truth_snapshot,
    }


def _recovery_budget_for_generation_mode(mode: str) -> dict | None:
    if mode == "quick":
        return {
            "max_patch": 1,
            "max_rewrite": 0,
            "max_contract_repair": 0,
            "max_validator_retry_immediate": 1,
            "max_validator_retry_delayed": 0,
            "max_rounds": 2,
            "timeout_seconds": 90,
            "content_timeout_seconds": 90,
            "validator_timeout_seconds": 60,
        }
    if mode == "deep":
        return {
            "max_patch": 3,
            "max_rewrite": 3,
            "max_contract_repair": 2,
            "max_validator_retry_immediate": 3,
            "max_validator_retry_delayed": 3,
            "max_rounds": 10,
            "timeout_seconds": 360,
            "content_timeout_seconds": 360,
            "validator_timeout_seconds": 240,
        }
    return None
