from __future__ import annotations

from app.agents.scene_repairer import SceneRepairer
from app.services.agent_skill_validator import filter_blocking_findings
from app.services.context_envelope_builder import (
    ContextEnvelopeBuilder,
    _compact_style_profile,
)


def _style_profile() -> dict:
    return {
        "id": "style-1",
        "name": "冷峻近距离叙事",
        "version": 3,
        "frozen": True,
        "frozen_dimensions": [
            {"name": "tone", "type": "tone", "value": "冷峻"},
            "perspective",
        ],
        "style_prompt": "短句推进，冷静克制，以可观察细节承载情绪。",
        "style_features": {
            "vocabulary": "具体、少形容词",
            "sentence_structure": "短句与中句交替",
            "tone": "冷峻克制",
            "pacing": "动作段快，发现段稍缓",
            "description_style": "物件与环境细节优先",
            "dialogue_style": "对白简短，保留潜台词",
            "narrative_voice": "第三人称限知近距离",
            "signature_phrases": ["雨沿着窗框往下爬"],
            "avoid_patterns": ["总结式升华", "解释人物情绪"],
        },
        "style_embedding": {
            "emotionality": 0.2,
            "sentence_complexity": 0.35,
            "narrative_distance": 0.25,
        },
        "persona_card": {
            "identity": "冷静的贴身叙述者",
            "decision_pattern": "先观察，再判断",
            "expression_style": "不替读者总结",
            "interpersonal_behavior": "通过动作表现关系",
            "hard_rules": ["不跳出 POV"],
        },
        "sample_passages": [
            {"category": "description", "text": "雨水压低了檐角，灯影只照到门槛。"},
        ],
        "style_statistics": {
            "global": {
                "total_chars": 90000,
                "sentence_length": {"mean": 14.2, "median": 12.0},
                "punctuation_profile": {"。": 0.71, "，": 0.24},
                "word_freq_top": [{"word": "雨", "count": 82}],
            },
        },
        "evolution_report": {
            "is_multi_style": False,
            "change_points": [],
            "style_clusters": [],
            "recommended_usage": "全书沿用，动作场景适度加速。",
        },
    }


def test_chapter_writer_packet_contains_full_compact_style_and_scene_directive():
    style_context = _compact_style_profile(_style_profile())
    envelope = {
        "blocks": [
            {
                "block_id": "story_state_snapshot",
                "priority": "P0",
                "content": {},
            },
            {
                "block_id": "chapter_style_context",
                "priority": "P1",
                "content": style_context,
            },
        ],
        "context_ledger_id": "ledger-1",
        "token_report": {},
    }
    directive = {
        "scene_style_role": "tighten",
        "narrative_distance": "close",
        "rhythm_goal": "accelerate",
        "dialogue_density": "low",
        "description_density": "medium",
        "avoid": ["总结式升华"],
    }

    packet = ContextEnvelopeBuilder().build_chapter_writer_packet(
        envelope=envelope,
        chapter_number=1,
        scene_contracts=[{
            "scene_id": "c1-s1",
            "goal": "角色抵达门前",
            "conflict": "门内没有回应",
            "ending_state": "角色发现门缝中的血迹",
            "style_directive": directive,
        }],
    )

    assert packet["style_context"]["style_prompt"] == _style_profile()["style_prompt"]
    assert packet["style_context"]["style_features"]["tone"] == "冷峻克制"
    assert packet["style_context"]["style_embedding"]["emotionality"] == 0.2
    assert packet["style_context"]["persona_card"]["identity"] == "冷静的贴身叙述者"
    assert packet["style_context"]["style_sample_passages"][0]["text"] == "雨水压低了檐角，灯影只照到门槛。"
    assert packet["style_context"]["style_statistics"]["global"]["sentence_length"]["mean"] == 14.2
    assert packet["style_context"]["evolution_report"]["recommended_usage"]
    assert packet["style_context"]["frozen_dimensions"] == ["tone", "perspective"]
    assert packet["scene_map"][0]["style_directive"] == directive
    assert _style_profile()["style_prompt"] in packet["user_prompt"]
    assert "雨水压低了檐角" in packet["user_prompt"]
    assert '"rhythm_goal": "accelerate"' in packet["user_prompt"]


def test_direct_scene_repair_prompt_receives_style_without_skill_envelope():
    profile = _style_profile()
    prompt = SceneRepairer._skill_contract_prompt({
        "style_context": profile,
        "persona_card": profile["persona_card"],
        "scene_contract": {
            "style_directive": {"rhythm_goal": "accelerate"},
        },
    })

    assert "Active Writing Style" in prompt
    assert profile["style_prompt"] in prompt
    assert "冷峻克制" in prompt
    assert "style_embedding" in prompt
    assert "雨水压低了檐角" in prompt
    assert "narrator_interpersonal_behavior" in prompt
    assert "rhythm_goal" in prompt
    assert "never a commit blocker" in prompt


def test_style_validator_findings_are_advisory_but_contract_metrics_stay_hard():
    style_finding = {
        "validator": "style_polish",
        "metric": "forbidden_word",
        "severity": "high",
    }
    contract_finding = {
        "validator": "style_polish",
        "metric": "forbidden_triggered",
        "severity": "high",
    }

    blocking = filter_blocking_findings([style_finding, contract_finding])

    assert style_finding["blocks_commit"] is False
    assert style_finding["enforcement"] == "advisory"
    assert blocking == [contract_finding]
    assert contract_finding.get("blocks_commit") is not False
