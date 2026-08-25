from __future__ import annotations

from collections import Counter, defaultdict

from app.models.quality_advisory import make_advisory
from app.services.dialogue_attribution_service import DialogueAttributionService


class CharacterVoiceChecker:
    def __init__(self):
        self.dialogues = DialogueAttributionService()

    def check(
        self,
        text: str,
        scene_contract: dict | None = None,
        chapter_state: dict | None = None,
        character_cards: list[dict] | None = None,
    ) -> dict:
        dialogue_items = self.dialogues.extract_dialogues(text or "")
        advisories = []
        by_speaker: dict[str, list[str]] = defaultdict(list)
        for item in dialogue_items:
            speaker = item.get("speaker") or "_unknown"
            by_speaker[speaker].append(item["text"])

        voice_profiles = {
            c.get("name"): c.get("voice_profile") or {}
            for c in (character_cards or [])
            if isinstance(c, dict) and c.get("name")
        }

        for speaker, quotes in by_speaker.items():
            if speaker == "_unknown" or len(quotes) < 2:
                continue
            joined = " ".join(quotes)
            profile = voice_profiles.get(speaker, {})
            avoided = [w for w in profile.get("avoided_words", []) if w and w in joined] if isinstance(profile, dict) else []
            if avoided:
                advisories.append(make_advisory(
                    "character_voice_avoided_word",
                    "medium",
                    f"角色「{speaker}」对白中出现声纹禁用词：{'、'.join(avoided[:5])}。",
                    target_span="、".join(avoided[:5]),
                    expected_behavior="按角色声纹替换为该角色更自然的表达。",
                    detector="character_voice_checker",
                    confidence=0.76,
                ))

        if len(by_speaker) >= 2:
            signatures = {}
            for speaker, quotes in by_speaker.items():
                if speaker == "_unknown" or not quotes:
                    continue
                text_joined = "".join(quotes)
                avg_len = sum(len(q) for q in quotes) / len(quotes)
                particles = Counter(ch for ch in text_joined if ch in "啊呀呢吧嘛吗哼")
                signatures[speaker] = (round(avg_len / 5), tuple(particles.most_common(3)))
            duplicate_groups = Counter(signatures.values())
            if any(count >= 2 for count in duplicate_groups.values()):
                advisories.append(make_advisory(
                    "ai_character_voice_merge",
                    "medium",
                    "多个角色对白的句长和语气助词特征过于接近，存在声音趋同风险。",
                    expected_behavior="为关键角色补充不同的句长、用词、打断习惯和潜台词策略。",
                    detector="character_voice_checker",
                    confidence=0.6,
                ))

        info_only = [
            item for item in dialogue_items
            if any(word in item["text"] for word in ("因为", "所以", "其实", "也就是说", "简单来说"))
        ]
        if len(info_only) >= 3:
            advisories.append(make_advisory(
                "dialogue_info_dump",
                "medium",
                f"对白中出现 {len(info_only)} 处解释性信息传递，潜台词不足。",
                target_span=info_only[0]["context"] if info_only else None,
                expected_behavior="让对白同时承担欲望、回避、误解或关系变化，而不是只解释设定。",
                detector="character_voice_checker",
                confidence=0.7,
            ))

        return {
            "schema_version": 1,
            "status": "ok",
            "dialogue_count": len(dialogue_items),
            "speaker_count": len([s for s in by_speaker if s != "_unknown"]),
            "advisories": advisories,
        }
