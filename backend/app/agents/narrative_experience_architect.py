from __future__ import annotations

from app.agents.base import BaseAgent
from app.models.literary_quality import LiteraryQualityContract
from app.models.narrative_experience import NarrativeExperienceContract
from app.models.writing_mode import WritingModeProfile


class NarrativeExperienceArchitectAgent(BaseAgent):
    """Compile scene-level experience and literary-quality contracts.

    The current implementation is deterministic and project-agnostic. It is an
    agent boundary so an LLM-enhanced version can be introduced later without
    changing the pipeline contract.
    """

    name = "narrative_experience_architect"

    async def execute(self, context: dict) -> dict:
        profile = WritingModeProfile(**(context.get("writing_mode_profile") or {}))
        scene_contract = context.get("scene_contract") or {}
        scene_beat = context.get("scene_beat") or {}
        chapter_number = int(context.get("chapter_number") or 0)
        scene_index = int(context.get("scene_index") or 0)
        project_id = str(context.get("project_id") or "")

        exp = self._experience_contract(
            profile=profile,
            project_id=project_id,
            chapter_number=chapter_number,
            scene_index=scene_index,
            scene_contract=scene_contract,
            scene_beat=scene_beat,
        )
        lit = self._literary_contract(
            profile=profile,
            project_id=project_id,
            chapter_number=chapter_number,
            scene_index=scene_index,
            scene_contract=scene_contract,
            scene_beat=scene_beat,
        )
        return {
            "experience_contract": exp.model_dump(),
            "literary_quality_contract": lit.model_dump(),
            "compiler_warnings": [],
        }

    def _experience_contract(
        self,
        *,
        profile: WritingModeProfile,
        project_id: str,
        chapter_number: int,
        scene_index: int,
        scene_contract: dict,
        scene_beat: dict,
    ) -> NarrativeExperienceContract:
        goal = _first_text(scene_contract.get("goal"), scene_beat.get("goal"))
        conflict = _first_text(scene_contract.get("conflict"), scene_beat.get("conflict"))
        outcome = _first_text(scene_contract.get("ending_state"), scene_beat.get("outcome"))
        hook = _first_text(scene_contract.get("hook"), scene_beat.get("hook"))
        info_release = _list_text(scene_beat.get("info_release")) or _list_text(scene_contract.get("must_show"))
        forbidden = _list_text(scene_contract.get("forbidden"))
        scene_role = _infer_scene_role(scene_contract, scene_beat)
        primary_emotion = _infer_emotion(scene_role, profile.id)

        carriers = profile.experience_defaults.get("preferred_information_carriers", [])
        if not isinstance(carriers, list):
            carriers = []
        pressure_source = _infer_pressure(conflict, profile)
        dialogue_pressure = _dialogue_requirement(profile, scene_contract, conflict)

        return NarrativeExperienceContract(
            project_id=project_id,
            chapter_number=chapter_number,
            scene_index=scene_index,
            writing_mode_id=profile.id,
            scene_role=scene_role,
            reader_promise=hook or outcome or goal,
            target_reader_state_start="带着上一场余波进入当前压力",
            target_reader_state_end=hook or "获得新问题或新压力",
            primary_emotion=primary_emotion,
            secondary_emotion="好奇" if hook or info_release else "",
            emotional_shift=f"从{primary_emotion or '关注'}推进到{hook or outcome or '新的不确定'}",
            protagonist_desire=goal,
            obstacle=conflict,
            pressure_source=pressure_source,
            stakes=outcome,
            decision_point=_infer_decision_point(goal, conflict, outcome),
            agency_requirement="核心角色必须有可观察选择或行动，不只被动接收信息。",
            curiosity_question=_question_from_hook_or_conflict(hook, conflict, outcome),
            information_delta=info_release[:5],
            withheld_information=forbidden[:5],
            reveal_policy={
                "preferred_carriers": carriers[:5],
                "avoid_answer_dump": True,
                "attach_information_to_scene": True,
            },
            scene_embodiment={
                "carrier_types": carriers[:5],
                "minimum_visible_actions": 2,
                "minimum_concrete_details": 3,
            },
            sensory_anchor=["object", "body", "environment"],
            social_interaction_requirement="需要互动压力" if dialogue_pressure in {"medium", "high"} else "",
            dialogue_pressure=dialogue_pressure,
            pacing_shape=_pacing_shape(scene_role, profile.id),
            hook_out=hook or outcome,
            prohibited_experience=profile.experience_defaults.get("avoid", [])[:5],
            success_metrics={
                key: profile.weight(key)
                for key in (
                    "reading_drive",
                    "clarity",
                    "dramatic_pressure",
                    "protagonist_agency",
                    "information_design",
                    "emotional_engagement",
                )
            },
        )

    def _literary_contract(
        self,
        *,
        profile: WritingModeProfile,
        project_id: str,
        chapter_number: int,
        scene_index: int,
        scene_contract: dict,
        scene_beat: dict,
    ) -> LiteraryQualityContract:
        defaults = profile.literary_quality_defaults or {}
        specificity_requirement = str(defaults.get("specificity_requirement", "medium"))
        min_details = {"low": 2, "medium": 3, "high": 4}.get(specificity_requirement, 3)
        dialogue_requirement = str(defaults.get("dialogue_pressure_requirement", "medium"))
        prose_requirement = str(defaults.get("prose_distinctiveness_requirement", "medium"))

        return LiteraryQualityContract(
            project_id=project_id,
            chapter_number=chapter_number,
            scene_index=scene_index,
            writing_mode_id=profile.id,
            prose_goal=_prose_goal(profile.id, prose_requirement),
            specificity_budget={
                "minimum_specific_details": min_details,
                "detail_types": {
                    "object": 1,
                    "body": 1,
                    "environment": 1,
                    "social_rule": 1 if profile.id in {"commercial_web", "emotional", "mystery"} else 0,
                },
                "generic_label_limit": 3,
                "abstract_explanation_limit": 2 if defaults.get("abstraction_ceiling") != "high" else 4,
                "must_attach_information_to_scene": True,
            },
            abstraction_ceiling=str(defaults.get("abstraction_ceiling", "medium")),
            exposition_policy={
                "prefer_scene_carriers": True,
                "max_consecutive_expository_sentences": 2,
            },
            dialogue_policy={
                "pressure_requirement": dialogue_requirement,
                "avoid_information_dump": True,
            },
            character_voice_policy={
                "require_distinct_dialogue_when_multiple_speakers": True,
            },
            sensory_policy={
                "required_anchor_types": ["body", "object", "environment"],
                "avoid_detached_description": True,
            },
            cultural_texture_policy={
                "requirement": str(defaults.get("cultural_texture_requirement", "medium")),
                "must_come_from_current_world": True,
            },
            intertextuality_policy={
                "requirement": str(defaults.get("intertextuality_requirement", "low")),
                "never_force_reference": True,
            },
            rhythm_policy={
                "vary_sentence_length": True,
                "avoid_uniform_paragraphs": True,
            },
            style_alignment_policy={
                "style_hard_rules_override_quality": True,
                "conflict_policy": profile.style_interaction.get("conflict_policy", "negotiate"),
            },
            avoid_patterns=[
                "abstract_summary_without_scene_evidence",
                "emotion_label_without_behavior",
                "answer_dump",
            ],
            required_textual_moves=[
                "信息进入场景时必须依附动作、对话、物件或感官观察。",
                "情绪判断必须有可观察证据。",
            ],
            forbidden_textual_moves=[
                "不得为追求文采改变事实合同。",
                "不得用文学化含混遮蔽硬线索或必要因果。",
            ],
            revision_priorities=[
                "先修事实，再修体验；先修场景压力，再修句子装饰。",
            ],
        )


def _first_text(*values) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()[:300]
        if isinstance(value, list):
            text = "；".join(str(v).strip() for v in value if str(v).strip())
            if text:
                return text[:300]
    return ""


def _list_text(value) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip()[:220] for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()[:220]]
    return []


def _infer_scene_role(scene_contract: dict, scene_beat: dict) -> str:
    raw = " ".join(str(v) for v in (
        scene_contract.get("type", ""),
        scene_beat.get("type", ""),
        scene_contract.get("goal", ""),
        scene_contract.get("conflict", ""),
        scene_beat.get("hook", ""),
    ))
    if any(k in raw for k in ("线索", "发现", "调查", "疑点")):
        return "线索发现"
    if any(k in raw for k in ("对峙", "冲突", "争执", "交锋")):
        return "压力升级"
    if any(k in raw for k in ("转折", "反转", "揭示")):
        return "关系或信息转折"
    if any(k in raw for k in ("开篇", "开局", "第一")):
        return "开局冲击"
    return "推进场景"


def _infer_emotion(scene_role: str, profile_id: str) -> str:
    if profile_id == "light_comedy":
        return "轻快紧张"
    if profile_id == "emotional":
        return "牵挂"
    if profile_id == "mystery" or "线索" in scene_role:
        return "疑问"
    if profile_id == "literary":
        return "克制波动"
    return "紧张"


def _infer_pressure(conflict: str, profile: WritingModeProfile) -> str:
    if any(k in conflict for k in ("时间", "赶", "来不及")):
        return "time_pressure"
    if any(k in conflict for k in ("误会", "质疑", "身份", "关系")):
        return "social_risk"
    defaults = profile.experience_defaults.get("preferred_scene_pressure", [])
    if isinstance(defaults, list) and defaults:
        return str(defaults[0])
    return "desire_vs_obstacle"


def _dialogue_requirement(profile: WritingModeProfile, scene_contract: dict, conflict: str) -> str:
    base = str(profile.literary_quality_defaults.get("dialogue_pressure_requirement", "medium"))
    if scene_contract.get("dialogue_pressure"):
        return str(scene_contract.get("dialogue_pressure"))
    if any(k in conflict for k in ("对峙", "争执", "谈判", "质问", "误会")):
        return "high"
    return base


def _infer_decision_point(goal: str, conflict: str, outcome: str) -> str:
    if goal or conflict:
        return f"在「{conflict or '阻碍'}」下，为「{goal or '当前目标'}」做出可见选择。"
    if outcome:
        return f"用主动行动抵达「{outcome}」。"
    return "至少出现一次可观察选择。"


def _question_from_hook_or_conflict(hook: str, conflict: str, outcome: str) -> str:
    source = hook or conflict or outcome
    if not source:
        return "接下来压力会如何变化？"
    return f"{source.rstrip('？?。')}？"


def _pacing_shape(scene_role: str, profile_id: str) -> str:
    if profile_id == "literary":
        return "压低开场，逐步显露压力，结尾留余波"
    if profile_id == "commercial_web":
        return "尽快给压力，中段升级，结尾抛钩"
    if profile_id == "mystery":
        return "先给可感异常，再给局部线索，结尾扩大疑问"
    if "线索" in scene_role:
        return "观察-误差-确认局部线索-留下疑问"
    return "开场承压，中段选择，结尾变化"


def _prose_goal(profile_id: str, prose_requirement: str) -> str:
    if profile_id == "commercial_web":
        return "清晰、有压力、有具体现场感，避免说明文和模板感。"
    if profile_id == "literary":
        return "语言有辨识度，细节承担心理和关系变化，避免空泛抒情。"
    if profile_id == "mystery":
        return "线索可见、因果清楚，语言服务疑问设计。"
    if profile_id == "light_comedy":
        return "节奏轻快，反应具体，对话和动作承担笑点。"
    if prose_requirement == "high":
        return "保持文体辨识度，同时让信息依附场景。"
    return "具体、清楚、有节奏，避免泛化总结。"
