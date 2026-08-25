from __future__ import annotations

from app.models.literary_quality import LiteraryQualityContract
from app.models.narrative_experience import NarrativeExperienceContract
from app.models.writing_mode import WritingModeProfile


class StyleExperienceConflictResolver:
    """Negotiate soft quality goals against active style constraints."""

    def resolve(
        self,
        *,
        experience_contract: NarrativeExperienceContract,
        literary_quality_contract: LiteraryQualityContract,
        writing_mode_profile: WritingModeProfile,
        style_context: dict | None = None,
    ) -> dict:
        style_context = style_context or {}
        conflicts: list[dict] = []
        resolutions: list[str] = []

        style_embedding = style_context.get("style_embedding") or {}
        if not isinstance(style_embedding, dict):
            style_embedding = {}
        hard_rules = style_context.get("hard_rules") or []
        if not isinstance(hard_rules, list):
            hard_rules = [str(hard_rules)]

        dialogue_ratio = _as_float(style_embedding.get("dialogue_ratio"), None)
        dialogue_need = (
            experience_contract.dialogue_pressure
            or literary_quality_contract.dialogue_policy.get("pressure_requirement", "")
        )
        if dialogue_ratio is not None and dialogue_ratio < 0.22 and dialogue_need in {"medium", "high"}:
            conflicts.append({
                "type": "dialogue_ratio_conflict",
                "severity": "medium",
                "style_rule": "低对白占比",
                "experience_need": "需要人物互动或社会压力",
                "resolution": "用少量高压对白、动作反应和沉默承载冲突。",
                "writer_guidance": "保持简洁风格，但让人物互动产生明确压力。",
            })
            resolutions.append("对白保持少量，但每句承担压力、关系变化或信息推进。")

        narrative_distance = _as_float(style_embedding.get("narrative_distance"), None)
        if narrative_distance is not None and narrative_distance > 0.75:
            if experience_contract.primary_emotion and experience_contract.primary_emotion not in {"冷静", "克制"}:
                conflicts.append({
                    "type": "emotional_distance_conflict",
                    "severity": "low",
                    "style_rule": "叙事距离偏远",
                    "experience_need": "需要读者感到情绪变化",
                    "resolution": "用行为后果、物件处理和感官反应替代直白情绪说明。",
                    "writer_guidance": "不直接煽情，保留可观察的情绪证据。",
                })
                resolutions.append("情绪不直说，落在动作、物件、停顿和选择后果上。")

        prompt_text = str(style_context.get("style_prompt") or "") + " " + " ".join(str(r) for r in hard_rules)
        if "极简" in prompt_text or "简洁" in prompt_text:
            min_details = int(
                (literary_quality_contract.specificity_budget or {}).get("minimum_specific_details", 0) or 0
            )
            if min_details >= 4:
                conflicts.append({
                    "type": "minimalism_specificity_conflict",
                    "severity": "low",
                    "style_rule": "简洁/极简",
                    "experience_need": "需要具体细节承载现场感",
                    "resolution": "选择少量高信息密度细节，不铺陈清单。",
                    "writer_guidance": "每个细节必须推动情绪、信息或压力。",
                })
                resolutions.append("细节宁少勿泛，优先选择能推动压力和信息的细节。")

        mode_policy = writing_mode_profile.style_interaction.get("conflict_policy", "negotiate")
        return {
            "schema_version": 1,
            "status": "conflict" if conflicts else "ok",
            "conflict_policy": mode_policy,
            "conflicts": conflicts,
            "resolutions": resolutions,
            "quality_extensions_patch": {
                "style_experience_resolution": resolutions[:5],
            } if resolutions else {},
        }


def _as_float(value, default):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
