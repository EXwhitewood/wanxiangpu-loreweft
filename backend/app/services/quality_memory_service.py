from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Mapping

from sqlalchemy import and_, select

from app.models.literary_quality import LiteraryQualityContract
from app.models.narrative_experience import NarrativeExperienceContract

logger = logging.getLogger(__name__)

_QUALITY_MEMORY_PATTERN_TYPES = {
    "template_phrase": "summary_ending_overuse",
    "explanatory_narration": "explanatory_narration_overuse",
    "voice_merge": "voice_merge_trend",
    "rhythm_uniformity": "rhythm_uniformity_trend",
    "concept_budget_exceeded": "concept_overload_trend",
    "dialogue_info_dump": "dialogue_dump_trend",
    "low_reading_drive": "low_reading_drive_trend",
    "low_scene_pressure": "low_scene_pressure_trend",
    "passive_protagonist": "passive_protagonist_trend",
    "exposition_driven_reveal": "exposition_driven_reveal_trend",
    "weak_hook_out": "weak_hook_out_trend",
    "specificity_budget_unmet": "specificity_budget_unmet_trend",
    "abstraction_over_budget": "abstraction_over_budget_trend",
    "prose_identity_weak": "prose_identity_weak_trend",
    "weak_opening_hook": "weak_opening_hook_trend",
    "low_event_density": "low_event_density_trend",
    "low_conflict_density": "low_conflict_density_trend",
    "flat_pressure_ramp": "flat_pressure_ramp_trend",
    "weak_curiosity_engine": "weak_curiosity_engine_trend",
    "low_reversal_density": "low_reversal_density_trend",
    "missing_micro_payoff": "missing_micro_payoff_trend",
    "weak_chapter_end_hook": "weak_chapter_end_hook_trend",
    "low_reader_retention": "low_reader_retention_trend",
    "fact_boundary_conflict": "fact_boundary_conflict_trend",
    "knowledge_boundary_violation": "knowledge_boundary_violation_trend",
    "plausibility_break": "plausibility_break_trend",
    "memory_plausibility_break": "memory_plausibility_break_trend",
    "narration_explanation_artifact": "narration_explanation_trend",
    "explanatory_punctuation_artifact": "explanatory_punctuation_trend",
}


def _coerce_project_id(project_id):
    try:
        return uuid.UUID(str(project_id))
    except Exception:
        return project_id


class QualityMemoryService:
    """Persist and summarize narrative-experience quality data."""

    def get_project_quality_memory(self, project=None, core_data: dict | None = None) -> dict:
        if core_data is None:
            core_data = getattr(project, "core_data", None)
        if not isinstance(core_data, Mapping):
            return {"recent_quality_patterns": [], "mode_fit_history": []}
        raw = core_data.get("quality_memory")
        if not isinstance(raw, Mapping):
            return {"recent_quality_patterns": [], "mode_fit_history": []}
        return self.summarize_quality_memory(dict(raw))

    def summarize_quality_memory(self, quality_memory: dict) -> dict:
        patterns = quality_memory.get("recent_quality_patterns", [])
        if not isinstance(patterns, list):
            patterns = []
        mode_history = quality_memory.get("mode_fit_history", [])
        if not isinstance(mode_history, list):
            mode_history = []
        raw_excerpts = quality_memory.get("recent_ai_flavor_excerpts", [])
        if not isinstance(raw_excerpts, list):
            raw_excerpts = []
        summary = {
            "recent_quality_patterns": [
                {
                    "type": str(item.get("type", ""))[:80],
                    "count": int(item.get("count", 0) or 0),
                    "last_seen_chapter": int(item.get("last_seen_chapter", 0) or 0),
                    "last_seen_scene": int(item.get("last_seen_scene", 0) or 0),
                    "suggestion": str(item.get("suggestion", ""))[:180],
                    # 保留 counted_keys 用于去重（同一 chapter:scene 不重复计数）
                    "counted_keys": list(item.get("counted_keys") or [])[-50:],
                }
                for item in patterns[:20]
                if isinstance(item, Mapping) and item.get("type")
            ],
            "mode_fit_history": [
                {
                    "writing_mode_id": str(item.get("writing_mode_id", "general"))[:80],
                    "avg_mode_fit": round(float(item.get("avg_mode_fit", 0) or 0), 2),
                    "sample_count": int(item.get("sample_count", 0) or 0),
                }
                for item in mode_history[:12]
                if isinstance(item, Mapping)
            ],
            # 反例驱动用：存 LLM 自己上一章产出的 AI 味具体句子，供下一章 prompt 注入。
            # 每条最多 200 字，最多保留 20 条，避免上下文爆炸。
            "recent_ai_flavor_excerpts": [
                {
                    "text": str(item.get("text", ""))[:200],
                    "type": str(item.get("type", ""))[:80],
                    "chapter": int(item.get("chapter", 0) or 0),
                    "scene": int(item.get("scene", 0) or 0),
                }
                for item in raw_excerpts[:20]
                if isinstance(item, Mapping) and item.get("text")
            ],
        }
        try:
            from app.services.structured_memory_compiler import get_structured_memory_compiler

            summary["quality_policy"] = get_structured_memory_compiler().compile_quality_policy(summary)
        except Exception:
            summary["quality_policy"] = {
                "schema_version": 1,
                "policy_actions": [],
                "writer_constraints": [],
                "planning_warnings": [],
                "quality_gate_candidates": [],
            }
        return summary

    def update_quality_memory(
        self,
        existing_memory: dict,
        *,
        advisories: list[dict],
        mode_fit: dict | None = None,
        writing_mode_id: str = "general",
        chapter_number: int = 0,
        scene_index: int = 0,
        max_patterns: int = 12,
    ) -> dict:
        memory = self.summarize_quality_memory(existing_memory or {})
        patterns = {item["type"]: dict(item) for item in memory.get("recent_quality_patterns", [])}
        # 反例驱动：收集 AI flavor 类 advisory 的 target_span 作为具体反例句子
        excerpts = list(memory.get("recent_ai_flavor_excerpts", []))
        for adv in advisories or []:
            if not isinstance(adv, Mapping):
                continue
            severity = adv.get("severity", "low")
            if severity not in {"medium", "high"}:
                continue
            try:
                confidence = float(adv.get("confidence", 0.5) or 0.5)
            except (TypeError, ValueError):
                confidence = 0.5
            if confidence < 0.55:
                continue
            adv_type = str(adv.get("type", ""))[:80]
            if not adv_type:
                continue
            pattern_type = _QUALITY_MEMORY_PATTERN_TYPES.get(adv_type, adv_type)
            item = patterns.setdefault(pattern_type, {
                "type": pattern_type,
                "count": 0,
                "suggestion": _suggestion_for_type(adv_type),
                "last_seen_chapter": 0,
                "last_seen_scene": 0,
                "counted_keys": [],  # 去重键列表（chapter:scene），防止同一场景多次复检重复计数
            })
            # 去重：同一 chapter+scene 的同一 advisory_type 只累计一次
            # 避免"初检、修复后检、重复复检"对同一文本重复增加质量记忆计数
            dedup_key = f"{int(chapter_number or 0)}:{int(scene_index or 0)}"
            counted_keys = item.get("counted_keys") or []
            if dedup_key not in counted_keys:
                item["count"] = int(item.get("count", 0) or 0) + 1
                # 保留最近 50 个去重键，避免无限增长
                counted_keys.append(dedup_key)
                item["counted_keys"] = counted_keys[-50:]
            item["last_seen_chapter"] = int(chapter_number or 0)
            item["last_seen_scene"] = int(scene_index or 0)
            # 收集 AI flavor 具体反例（target_span 非空时）
            detector = str(adv.get("detector", ""))[:80]
            target_span = adv.get("target_span")
            if detector == "ai_flavor_checker" and target_span:
                excerpt_text = str(target_span)[:200]
                if excerpt_text and not any(e.get("text") == excerpt_text for e in excerpts):
                    excerpts.append({
                        "text": excerpt_text,
                        "type": adv_type,
                        "chapter": int(chapter_number or 0),
                        "scene": int(scene_index or 0),
                    })

        memory["recent_quality_patterns"] = sorted(
            patterns.values(),
            key=lambda item: (-int(item.get("count", 0) or 0), item.get("type", "")),
        )[:max_patterns]
        # 反例最多保留 20 条（按章节正序，最新的在尾部）
        memory["recent_ai_flavor_excerpts"] = excerpts[-20:]
        memory["mode_fit_history"] = self._update_mode_history(
            memory.get("mode_fit_history", []),
            writing_mode_id=writing_mode_id,
            mode_fit=mode_fit or {},
        )
        return memory

    async def save_experience_contract(
        self,
        db,
        *,
        project_id,
        chapter_number: int,
        scene_index: int,
        generation_revision: int,
        contract: NarrativeExperienceContract | dict,
        compiler_warnings: list[str] | None = None,
    ) -> str:
        from app.db.db_models import SceneExperienceContract

        model = contract if isinstance(contract, NarrativeExperienceContract) else NarrativeExperienceContract(**(contract or {}))
        existing = await db.execute(
            select(SceneExperienceContract.id).where(
                and_(
                    SceneExperienceContract.project_id == _coerce_project_id(project_id),
                    SceneExperienceContract.chapter_number == chapter_number,
                    SceneExperienceContract.scene_index == scene_index,
                    SceneExperienceContract.generation_revision == generation_revision,
                    SceneExperienceContract.writing_mode_id == model.writing_mode_id,
                )
            ).limit(1)
        )
        existing_id = existing.scalar_one_or_none()
        if existing_id:
            return str(existing_id)
        obj_id = str(uuid.uuid4())
        db.add(SceneExperienceContract(
            id=obj_id,
            project_id=_coerce_project_id(project_id),
            chapter_number=chapter_number,
            scene_index=scene_index,
            generation_revision=generation_revision,
            writing_mode_id=model.writing_mode_id,
            contract=model.model_dump(),
            compiler_warnings=compiler_warnings or [],
            created_at=datetime.now(timezone.utc),
        ))
        await db.flush()
        return obj_id

    async def save_literary_quality_contract(
        self,
        db,
        *,
        project_id,
        chapter_number: int,
        scene_index: int,
        generation_revision: int,
        contract: LiteraryQualityContract | dict,
        style_profile_id: str = "",
        conflict_summary: dict | None = None,
    ) -> str:
        from app.db.db_models import SceneLiteraryQualityContract

        model = contract if isinstance(contract, LiteraryQualityContract) else LiteraryQualityContract(**(contract or {}))
        existing = await db.execute(
            select(SceneLiteraryQualityContract.id).where(
                and_(
                    SceneLiteraryQualityContract.project_id == _coerce_project_id(project_id),
                    SceneLiteraryQualityContract.chapter_number == chapter_number,
                    SceneLiteraryQualityContract.scene_index == scene_index,
                    SceneLiteraryQualityContract.generation_revision == generation_revision,
                    SceneLiteraryQualityContract.writing_mode_id == model.writing_mode_id,
                )
            ).limit(1)
        )
        existing_id = existing.scalar_one_or_none()
        if existing_id:
            return str(existing_id)
        obj_id = str(uuid.uuid4())
        db.add(SceneLiteraryQualityContract(
            id=obj_id,
            project_id=_coerce_project_id(project_id),
            chapter_number=chapter_number,
            scene_index=scene_index,
            generation_revision=generation_revision,
            writing_mode_id=model.writing_mode_id,
            contract=model.model_dump(),
            style_profile_id=style_profile_id,
            conflict_summary=conflict_summary or {},
            created_at=datetime.now(timezone.utc),
        ))
        await db.flush()
        return obj_id

    async def save_quality_report(
        self,
        db,
        *,
        project_id,
        chapter_number: int,
        scene_index: int,
        generation_revision: int,
        writing_mode_id: str,
        scores: dict | None = None,
        advisories: list[dict] | None = None,
        mode_fit: dict | None = None,
        style_conflicts: dict | None = None,
    ) -> str:
        from app.db.db_models import ExperienceQualityReport

        existing = await db.execute(
            select(ExperienceQualityReport.id).where(
                and_(
                    ExperienceQualityReport.project_id == _coerce_project_id(project_id),
                    ExperienceQualityReport.chapter_number == chapter_number,
                    ExperienceQualityReport.scene_index == scene_index,
                    ExperienceQualityReport.generation_revision == generation_revision,
                    ExperienceQualityReport.writing_mode_id == writing_mode_id,
                )
            ).limit(1)
        )
        existing_id = existing.scalar_one_or_none()
        if existing_id:
            return str(existing_id)
        obj_id = str(uuid.uuid4())
        db.add(ExperienceQualityReport(
            id=obj_id,
            project_id=_coerce_project_id(project_id),
            chapter_number=chapter_number,
            scene_index=scene_index,
            generation_revision=generation_revision,
            writing_mode_id=writing_mode_id,
            scores=scores or {},
            advisories=self._sanitize_advisories(advisories or []),
            mode_fit=mode_fit or {},
            style_conflicts=style_conflicts or {},
            created_at=datetime.now(timezone.utc),
        ))
        await db.flush()
        return obj_id

    async def save_revision_audit(
        self,
        db,
        *,
        project_id,
        chapter_number: int,
        scene_index: int,
        generation_revision: int,
        revision_type: str,
        before_hash: str,
        after_hash: str,
        applied_patches: list[dict] | None = None,
        locked_contract_hash: str = "",
        locked_style_hash: str = "",
    ) -> str:
        from app.db.db_models import QualityRevisionAudit

        obj_id = str(uuid.uuid4())
        db.add(QualityRevisionAudit(
            id=obj_id,
            project_id=_coerce_project_id(project_id),
            chapter_number=chapter_number,
            scene_index=scene_index,
            generation_revision=generation_revision,
            revision_type=revision_type[:80],
            before_hash=before_hash[:80],
            after_hash=after_hash[:80],
            applied_patches=applied_patches or [],
            locked_contract_hash=locked_contract_hash[:80],
            locked_style_hash=locked_style_hash[:80],
            created_at=datetime.now(timezone.utc),
        ))
        await db.flush()
        return obj_id

    async def persist_scene_quality_payload(
        self,
        db,
        *,
        project_id,
        chapter_number: int,
        payload: dict,
    ) -> None:
        scene_index = int(payload.get("scene_index", 0) or 0)
        revision = int(payload.get("generation_revision", 1) or 1)
        exp_contract = payload.get("experience_contract") or {}
        lit_contract = payload.get("literary_quality_contract") or {}
        style_conflict = payload.get("style_conflict_report") or {}
        reports = payload.get("quality_reports") or {}
        writing_mode_id = str(payload.get("writing_mode_id") or "general")

        if exp_contract:
            await self.save_experience_contract(
                db,
                project_id=project_id,
                chapter_number=chapter_number,
                scene_index=scene_index,
                generation_revision=revision,
                contract=exp_contract,
                compiler_warnings=payload.get("compiler_warnings", []),
            )
        if lit_contract:
            await self.save_literary_quality_contract(
                db,
                project_id=project_id,
                chapter_number=chapter_number,
                scene_index=scene_index,
                generation_revision=revision,
                contract=lit_contract,
                style_profile_id=str(payload.get("style_profile_id", "")),
                conflict_summary=style_conflict,
            )

        advisories = []
        scores = {}
        mode_fit = payload.get("mode_fit") if isinstance(payload.get("mode_fit"), Mapping) else {}
        for key in ("narrative_experience", "literary_quality", "commercial_pacing", "workflow_advisory"):
            report = reports.get(key)
            if not isinstance(report, Mapping):
                continue
            scores[key] = report.get("scores", {})
            advisories.extend(report.get("advisories", []) or [])
            if key == "narrative_experience" and isinstance(report.get("mode_fit"), Mapping):
                mode_fit = dict(report.get("mode_fit") or {})
        if scores or advisories or mode_fit or style_conflict:
            await self.save_quality_report(
                db,
                project_id=project_id,
                chapter_number=chapter_number,
                scene_index=scene_index,
                generation_revision=revision,
                writing_mode_id=writing_mode_id,
                scores=scores,
                advisories=advisories,
                mode_fit=mode_fit,
                style_conflicts=style_conflict,
            )

        if advisories or mode_fit:
            await self._update_project_quality_memory(
                db,
                project_id=project_id,
                advisories=advisories,
                mode_fit=mode_fit,
                writing_mode_id=writing_mode_id,
                chapter_number=chapter_number,
                scene_index=scene_index,
            )

    async def get_chapter_experience_quality(self, db, project_id, chapter_number: int) -> list[dict]:
        from app.db.db_models import ExperienceQualityReport

        stmt = (
            select(ExperienceQualityReport)
            .where(
                and_(
                    ExperienceQualityReport.project_id == _coerce_project_id(project_id),
                    ExperienceQualityReport.chapter_number == chapter_number,
                )
            )
            .order_by(ExperienceQualityReport.scene_index, ExperienceQualityReport.generation_revision.desc())
        )
        result = await db.execute(stmt)
        rows = result.scalars().all()
        return [
            {
                "id": row.id,
                "project_id": str(row.project_id),
                "chapter_number": row.chapter_number,
                "scene_index": row.scene_index,
                "generation_revision": row.generation_revision,
                "writing_mode_id": row.writing_mode_id,
                "scores": row.scores,
                "advisories": row.advisories,
                "mode_fit": row.mode_fit,
                "style_conflicts": row.style_conflicts,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]

    async def rebuild_project_quality_memory(
        self,
        db,
        project_id,
        *,
        excluded_chapter_numbers: set[int] | None = None,
    ) -> dict:
        """Rebuild quality memory from durable reports of committed chapters.

        This is used after workflow cancellation and chapter deletion.  It
        removes counters that may have been written by historical pre-commit
        code without discarding valid examples from chapters that still exist.
        """
        from app.db.db_models import Chapter, ExperienceQualityReport, Project

        pid = _coerce_project_id(project_id)
        excluded = {int(number) for number in (excluded_chapter_numbers or set())}
        project = await db.get(Project, pid)
        if project is None:
            return {}

        result = await db.execute(
            select(ExperienceQualityReport)
            .join(
                Chapter,
                and_(
                    Chapter.project_id == ExperienceQualityReport.project_id,
                    Chapter.chapter_number == ExperienceQualityReport.chapter_number,
                ),
            )
            .where(
                ExperienceQualityReport.project_id == pid,
                Chapter.status == "committed",
            )
            .order_by(
                ExperienceQualityReport.chapter_number,
                ExperienceQualityReport.scene_index,
                ExperienceQualityReport.generation_revision.desc(),
                ExperienceQualityReport.created_at.desc(),
            )
        )
        rows = list(result.scalars().all())

        # Keep only the newest report for each committed scene/mode.  Older
        # revisions are audit history, not additional quality-memory samples.
        latest_rows = []
        seen: set[tuple[int, int, str]] = set()
        for row in rows:
            if int(row.chapter_number or 0) in excluded:
                continue
            key = (
                int(row.chapter_number or 0),
                int(row.scene_index or 0),
                str(row.writing_mode_id or "general"),
            )
            if key in seen:
                continue
            seen.add(key)
            latest_rows.append(row)
        latest_rows.sort(key=lambda row: (row.chapter_number, row.scene_index))

        committed_numbers = {
            int(number)
            for number in (
                await db.execute(
                    select(Chapter.chapter_number).where(
                        Chapter.project_id == pid,
                        Chapter.status == "committed",
                    )
                )
            ).scalars().all()
            if int(number) not in excluded
        }
        existing = (project.core_data or {}).get("quality_memory", {})
        existing_excerpts = existing.get("recent_ai_flavor_excerpts", []) if isinstance(existing, dict) else []
        valid_excerpts = [
            dict(item)
            for item in existing_excerpts
            if isinstance(item, Mapping)
            and int(item.get("chapter", 0) or 0) in committed_numbers
        ][-20:]

        memory: dict = {"recent_ai_flavor_excerpts": valid_excerpts}
        for row in latest_rows:
            memory = self.update_quality_memory(
                memory,
                advisories=list(row.advisories or []),
                mode_fit=dict(row.mode_fit or {}),
                writing_mode_id=str(row.writing_mode_id or "general"),
                chapter_number=int(row.chapter_number or 0),
                scene_index=int(row.scene_index or 0),
            )

        core = dict(project.core_data or {})
        core["quality_memory"] = self.summarize_quality_memory(memory)
        project.core_data = core
        await db.flush()
        return core["quality_memory"]

    @staticmethod
    def _sanitize_advisories(advisories: list[dict]) -> list[dict]:
        sanitized = []
        for adv in advisories:
            if not isinstance(adv, Mapping):
                continue
            sanitized.append({
                "type": str(adv.get("type", ""))[:80],
                "severity": str(adv.get("severity", "low"))[:20],
                "expected_behavior": str(adv.get("expected_behavior", ""))[:260],
                "detector": str(adv.get("detector", adv.get("source_checker", "")))[:80],
                "confidence": float(adv.get("confidence", 0.5) or 0.5),
            })
        return sanitized[:30]

    async def _update_project_quality_memory(
        self,
        db,
        *,
        project_id,
        advisories: list[dict],
        mode_fit: dict,
        writing_mode_id: str,
        chapter_number: int,
        scene_index: int,
    ) -> None:
        from app.db.db_models import Project

        project = await db.get(Project, _coerce_project_id(project_id))
        if project is None:
            return
        core = dict(project.core_data or {})
        current = core.get("quality_memory", {})
        if not isinstance(current, dict):
            current = {}
        core["quality_memory"] = self.update_quality_memory(
            current,
            advisories=advisories,
            mode_fit=mode_fit,
            writing_mode_id=writing_mode_id or "general",
            chapter_number=chapter_number,
            scene_index=scene_index,
        )
        project.core_data = core
        await db.flush()

    @staticmethod
    def _update_mode_history(history: list, *, writing_mode_id: str, mode_fit: dict) -> list[dict]:
        if not isinstance(history, list):
            history = []
        try:
            fit_score = float(mode_fit.get("fit_score", mode_fit.get("weighted_score", 0)) or 0)
        except (TypeError, ValueError):
            fit_score = 0.0
        if fit_score <= 0:
            return history[:12]
        by_mode = {
            str(item.get("writing_mode_id", "general")): dict(item)
            for item in history
            if isinstance(item, Mapping)
        }
        item = by_mode.setdefault(writing_mode_id, {
            "writing_mode_id": writing_mode_id,
            "avg_mode_fit": 0.0,
            "sample_count": 0,
        })
        count = int(item.get("sample_count", 0) or 0)
        avg = float(item.get("avg_mode_fit", 0) or 0)
        item["avg_mode_fit"] = round(((avg * count) + fit_score) / (count + 1), 2)
        item["sample_count"] = count + 1
        return sorted(by_mode.values(), key=lambda row: row.get("writing_mode_id", ""))[:12]


def _suggestion_for_type(adv_type: str) -> str:
    commercial_suggestions = {
        "weak_opening_hook": "后续场景优先用异常、目标、危机或利益变化开场。",
        "low_event_density": "后续场景减少静态铺陈，增加发现、阻碍、交换或代价。",
        "low_conflict_density": "后续场景把冲突落到人物互动、资源、时间或现场变化。",
        "flat_pressure_ramp": "后续场景让压力逐段升级，最终逼出选择。",
        "weak_curiosity_engine": "后续场景保留能改变局面的清晰未解问题。",
        "low_reversal_density": "后续场景安排信息、关系、局势或代价变向。",
        "missing_micro_payoff": "后续场景兑现一个小问题，再引出更大的未解问题。",
        "weak_chapter_end_hook": "后续章末停在新发现、新危险、新选择或代价落下前。",
        "low_reader_retention": "后续场景同时检查目标、阻碍、代价、悬念、兑现和钩子。",
        "fact_boundary_conflict": "后续场景生成前先压缩硬事实边界，身份、地点、对象归属和权限不得漂移。",
        "knowledge_boundary_violation": "后续场景明确角色此刻知道、能推断和不能知道的信息。",
        "plausibility_break": "后续场景让重要判断有观察、对话、线索或身体反应支撑。",
        "memory_plausibility_break": "后续场景把记忆改为触发式、片段式、不完整呈现。",
        "narration_explanation_artifact": "后续场景减少解释腔，用动作、停顿、视线和选择承载判断。",
        "explanatory_punctuation_artifact": "后续场景降低解释性破折号密度，改用句读、动作或对白承接。",
    }
    if adv_type in commercial_suggestions:
        return commercial_suggestions[adv_type]

    suggestions = {
        "low_reading_drive": "后续场景优先建立压力、选择和出场钩子。",
        "low_scene_pressure": "让阻碍通过互动、时间、代价或现场变化呈现。",
        "passive_protagonist": "增加核心角色可观察选择。",
        "exposition_driven_reveal": "把说明拆给动作、对话、物件和后果。",
        "weak_hook_out": "结尾停在具体行动、发现、代价或疑问上。",
        "specificity_budget_unmet": "补充承担信息或情绪证据的具体细节。",
        "abstraction_over_budget": "降低抽象解释，改为场景化呈现。",
        "prose_identity_weak": "增强句式、措辞和叙述人格辨识度。",
    }
    return suggestions.get(adv_type, "后续场景保持该问题的质量警戒。")
