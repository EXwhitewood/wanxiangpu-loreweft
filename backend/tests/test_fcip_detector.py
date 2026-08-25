import pytest

from app.engines.fcip_detector import (
    rule_fh01_l4_direct_access,
    rule_fh02_l3_misattribution,
    rule_fh03_l2_reasoning,
    rule_fh05_l0_expository_monologue,
    rule_fh06_environment_over_salience,
    rule_fh07_post_reveal_blindness,
    rule_fh08_clue_repetition_too_close,
    rule_fh09_meta_term_leak,
    run_deterministic_checks,
)


class TestFH01L4DirectAccess:
    def test_detects_secret_keyword(self):
        v = rule_fh01_l4_direct_access(
            "他终于知道了真实身份是魔族后裔",
            "身份秘密", ["魔族后裔", "真实身份"], "林逸",
        )
        assert len(v) == 2
        assert v[0].rule_id == "FH-01"
        assert v[0].severity == "Critical"

    def test_no_violation_when_clean(self):
        v = rule_fh01_l4_direct_access(
            "他看着窗外的月亮，什么也没说。",
            "身份秘密", ["魔族后裔"], "林逸",
        )
        assert len(v) == 0

    def test_empty_keywords(self):
        v = rule_fh01_l4_direct_access("任意文本", "test", [], "角色")
        assert len(v) == 0


class TestFH02L3Misattribution:
    def test_detects_misattribution(self):
        v = rule_fh02_l3_misattribution("他感觉这件事不对劲", "苏清月")
        assert len(v) >= 1
        assert v[0].rule_id == "FH-02"

    def test_detects_causal_inference(self):
        v = rule_fh02_l3_misattribution("一定是有人因为某种原因做的", "苏清月")
        assert len(v) >= 1

    def test_no_violation_when_clean(self):
        v = rule_fh02_l3_misattribution("他皱了皱眉。", "苏清月")
        assert len(v) == 0


class TestFH03L2Reasoning:
    def test_detects_reasoning_keywords(self):
        for kw in ["难道", "莫非", "会不会", "该不会"]:
            v = rule_fh03_l2_reasoning(f"他心想{kw}有什么隐情", "楚瑶")
            assert len(v) >= 1, f"Failed for keyword: {kw}"

    def test_no_violation_when_clean(self):
        v = rule_fh03_l2_reasoning("他记下了那个细节。", "楚瑶")
        assert len(v) == 0


class TestFH05L0ExpositoryMonologue:
    def test_detects_expository(self):
        v = rule_fh05_l0_expository_monologue(
            "原来如此，我终于明白了真相", "旁白", "真相",
        )
        assert len(v) >= 1
        assert v[0].rule_id == "FH-05"

    def test_no_violation_when_no_secret_words(self):
        v = rule_fh05_l0_expository_monologue(
            "原来如此，天亮了", "旁白", "完全无关的秘密描述",
        )
        assert len(v) == 0


class TestFH06EnvironmentOverSalience:
    def test_detects_over_emphasis(self):
        v = rule_fh06_environment_over_salience("那把剑格外显眼地立在角落")
        assert len(v) >= 1
        assert v[0].rule_id == "FH-06"

    def test_no_violation_when_subtle(self):
        v = rule_fh06_environment_over_salience("角落里有一把旧剑。")
        assert len(v) == 0


class TestFH07PostRevealBlindness:
    def test_detects_ignorance_after_reveal(self):
        v = rule_fh07_post_reveal_blindness(
            "他完全不知道这件事", "林逸", "informed",
        )
        assert len(v) >= 1
        assert v[0].rule_id == "FH-07"

    def test_no_violation_for_blind_character(self):
        v = rule_fh07_post_reveal_blindness(
            "他完全不知道这件事", "林逸", "blind",
        )
        assert len(v) == 0

    def test_detects_all_informed_statuses(self):
        for status in ("informed", "internalized", "verified"):
            v = rule_fh07_post_reveal_blindness("他不知道", "角色", status)
            assert len(v) >= 1, f"Failed for status: {status}"


class TestFH08ClueRepetition:
    def test_detects_close_repetition(self):
        v = rule_fh08_clue_repetition_too_close(
            "墙上挂着一幅奇怪的画",
            ["墙上挂着一幅奇怪的画"],
            min_chapter_distance=2,
        )
        assert len(v) >= 1
        assert v[0].rule_id == "FH-08"

    def test_no_violation_for_different_text(self):
        v = rule_fh08_clue_repetition_too_close(
            "完全不同的内容",
            ["墙上挂着一幅奇怪的画"],
        )
        assert len(v) == 0


class TestFH09MetaTermLeak:
    def test_detects_meta_terms(self):
        for term in ["伏笔", "埋设", "叙事需求", "揭示任务"]:
            v = rule_fh09_meta_term_leak(f"这是一个{term}的描述")
            assert len(v) >= 1, f"Failed for term: {term}"

    def test_no_violation_when_clean(self):
        v = rule_fh09_meta_term_leak("他走进了那间旧屋子。")
        assert len(v) == 0


class TestRunDeterministicChecks:
    def test_full_pipeline(self):
        items = [{
            "name": "身份秘密",
            "secret_canonical_statement": "魔族后裔",
            "character_states": [
                {"character_name": "林逸", "cognitive_level": "fully_blind", "cognitive_status": "blind"},
            ],
            "recent_clue_texts": [],
        }]
        text = "他知道了魔族后裔的事情，这是一个伏笔"
        violations = run_deterministic_checks(text, items, chapter_number=5)
        rule_ids = {v.rule_id for v in violations}
        assert "FH-01" in rule_ids
        assert "FH-09" in rule_ids

    def test_multiple_characters(self):
        items = [{
            "name": "秘密",
            "secret_canonical_statement": "真相",
            "character_states": [
                {"character_name": "林逸", "cognitive_level": "fully_blind", "cognitive_status": "blind"},
                {"character_name": "苏清月", "cognitive_level": "vague_unease", "cognitive_status": "noticed"},
            ],
            "recent_clue_texts": [],
        }]
        text = "他知道了真相。她感觉这件事不对劲。"
        violations = run_deterministic_checks(text, items)
        rule_ids = {v.rule_id for v in violations}
        assert "FH-01" in rule_ids
        assert "FH-02" in rule_ids

    def test_no_violations_clean_text(self):
        items = [{
            "name": "秘密",
            "secret_canonical_statement": "无关内容",
            "character_states": [
                {"character_name": "林逸", "cognitive_level": "fully_blind", "cognitive_status": "blind"},
            ],
            "recent_clue_texts": [],
        }]
        text = "他看着窗外的雨，什么也没说。"
        violations = run_deterministic_checks(text, items)
        assert len(violations) == 0
