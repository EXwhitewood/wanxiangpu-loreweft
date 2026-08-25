from __future__ import annotations

import re
from statistics import mean
from typing import Any

from app.models.quality_advisory import make_advisory
from app.services.quality_checkers.character_voice_checker import CharacterVoiceChecker


DEFAULT_GENERIC_VOICE_MARKERS = (
    "不由得",
    "莫名的",
    "复杂的情绪",
    "眼底闪过一丝",
    "眸光一沉",
    "指尖微微收紧",
    "呼吸一滞",
    "唇角勾起",
    "心头一颤",
    "空气仿佛凝固",
    "时间仿佛静止",
)


class VoiceFingerprintChecker:
    """Check prose against declared narrator and POV voice fingerprints."""

    def check(
        self,
        text: str,
        *,
        context: dict | None = None,
        resource_packs: dict[str, dict] | None = None,
    ) -> dict:
        context = context or {}
        profile = self._profile(context)
        generic_markers = list(DEFAULT_GENERIC_VOICE_MARKERS)
        for pack in (resource_packs or {}).values():
            values = pack.get("generic_voice_markers") if isinstance(pack, dict) else None
            if isinstance(values, list):
                generic_markers.extend(str(item) for item in values if str(item).strip())
        generic_markers = list(dict.fromkeys(generic_markers))

        generic_hits = sum(text.count(marker) for marker in generic_markers)
        avoided_words = self._list(profile.get("avoided_words"))
        preferred_markers = self._list(
            profile.get("preferred_markers")
            or profile.get("signature_phrases")
            or profile.get("preferred_words")
        )
        observation_domains = self._flatten_domains(profile.get("observation_domains"))
        avoided_hits = sum(text.count(word) for word in avoided_words)
        preferred_hits = sum(text.count(word) for word in preferred_markers)
        domain_hits = sum(text.count(word) for word in observation_domains)
        average_sentence_length = self._average_sentence_length(text)
        target_average = self._target_average(profile)
        sentence_deviation = (
            abs(average_sentence_length - target_average) / max(target_average, 1)
            if target_average is not None
            else 0.0
        )
        density = generic_hits / max(len(text) / 1000, 1)
        score = 100
        score -= min(45, avoided_hits * 15)
        score -= min(35, int(density * 6))
        if profile and preferred_markers and preferred_hits == 0 and len(text) >= 300:
            score -= 12
        if profile and observation_domains and domain_hits == 0 and len(text) >= 300:
            score -= 8
        if target_average is not None and sentence_deviation > 0.55:
            score -= 10
        score = max(0, score)

        character_report = CharacterVoiceChecker().check(
            text,
            scene_contract=context.get("scene_contract"),
            chapter_state=context.get("chapter_state"),
            character_cards=self._character_cards(context),
        )
        advisories = list(character_report.get("advisories") or [])
        if density > 3:
            advisories.append(make_advisory(
                "ai_generic_voice",
                "medium" if density < 6 else "high",
                f"通用模型化声纹动作每千字约 {density:.1f} 处。",
                expected_behavior="用当前 POV 的职业、偏见、身体状态、物件历史和关系立场替代通用动作包。",
                detector="voice_fingerprint_checker",
                confidence=0.72,
            ))
        if avoided_hits:
            advisories.append(make_advisory(
                "voice_fingerprint_avoided_word",
                "high",
                f"命中声纹禁用词 {avoided_hits} 次。",
                expected_behavior="改用符合当前叙述者或角色声纹的词汇和观察方式。",
                detector="voice_fingerprint_checker",
                confidence=0.86,
            ))
        if profile and score < 60:
            advisories.append(make_advisory(
                "voice_fingerprint_drift",
                "high",
                f"正文与声明声纹偏离，综合分 {score}/100。",
                expected_behavior="恢复角色的词汇层级、观察领域、句长习惯、情绪间接度和禁用倾向。",
                detector="voice_fingerprint_checker",
                confidence=0.74,
            ))

        return {
            "schema_version": 1,
            "status": "ok" if profile else "degraded",
            "profile_available": bool(profile),
            "metrics": {
                "voice_fingerprint_score": score,
                "generic_voice_hits": generic_hits,
                "generic_voice_density": round(density, 3),
                "avoided_word_hits": avoided_hits,
                "preferred_marker_hits": preferred_hits,
                "observation_domain_hits": domain_hits,
                "average_sentence_length": round(average_sentence_length, 2),
                "sentence_length_deviation": round(sentence_deviation, 3),
                "dialogue_count": character_report.get("dialogue_count", 0),
                "speaker_count": character_report.get("speaker_count", 0),
            },
            "advisories": advisories,
            "summary": (
                "已按声明声纹完成检查。"
                if profile
                else "未提供可用声纹，已执行通用声纹退化检查。"
            ),
        }

    @staticmethod
    def _profile(context: dict) -> dict:
        style_profile = context.get("style_profile") or {}
        pov_card = context.get("pov_character_card") or context.get("persona_card") or {}
        candidates = (
            context.get("voice_fingerprint"),
            style_profile.get("voice_fingerprint") if isinstance(style_profile, dict) else None,
            pov_card.get("voice_fingerprint") if isinstance(pov_card, dict) else None,
            pov_card.get("voice_profile") if isinstance(pov_card, dict) else None,
        )
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate:
                return candidate
        return {}

    @staticmethod
    def _character_cards(context: dict) -> list[dict]:
        cards = context.get("character_cards")
        if isinstance(cards, list):
            return [item for item in cards if isinstance(item, dict)]
        card = context.get("pov_character_card") or context.get("persona_card")
        return [card] if isinstance(card, dict) and card else []

    @staticmethod
    def _list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    def _flatten_domains(self, value: Any) -> list[str]:
        if isinstance(value, dict):
            flattened: list[str] = []
            for items in value.values():
                flattened.extend(self._list(items))
            return flattened
        return self._list(value)

    @staticmethod
    def _average_sentence_length(text: str) -> float:
        sentences = [item.strip() for item in re.split(r"[。！？!?]+", text or "") if item.strip()]
        return mean(len(item) for item in sentences) if sentences else 0.0

    @staticmethod
    def _target_average(profile: dict) -> float | None:
        distribution = profile.get("sentence_distribution")
        if isinstance(distribution, dict):
            value = distribution.get("average") or distribution.get("mean")
            try:
                return float(value)
            except (TypeError, ValueError):
                return None
        return None
