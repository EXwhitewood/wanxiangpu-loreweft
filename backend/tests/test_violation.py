import pytest
from app.models.violation import Violation, make_violation, STRATEGY_MAP, SEVERITY_BLOCKS_COMMIT


class TestViolationStructure:
    def test_make_violation_basic_fields(self):
        v = make_violation("forbidden_triggered", "critical", "禁止内容被触发")
        assert v["type"] == "forbidden_triggered"
        assert v["severity"] == "critical"
        assert v["detail"] == "禁止内容被触发"
        assert v["source"] == "deterministic"
        assert v["violation_id"]
        assert len(v["violation_id"]) == 12

    def test_make_violation_suggested_strategy_from_map(self):
        v = make_violation("forbidden_triggered", "high", "test")
        assert v["suggested_strategy"] == "patch_text"

        v = make_violation("canon_timeline_confusion", "high", "test")
        assert v["suggested_strategy"] == "rewrite_scene"

    def test_make_violation_suggested_strategy_override(self):
        v = make_violation("forbidden_triggered", "critical", "test", suggested_strategy="rewrite_scene")
        assert v["suggested_strategy"] == "rewrite_scene"

    def test_make_violation_unknown_type_defaults_to_manual_review(self):
        v = make_violation("some_new_type", "high", "test")
        assert v["suggested_strategy"] == "manual_review"

    def test_make_violation_blocks_commit_critical(self):
        v = make_violation("forbidden_triggered", "critical", "test")
        assert v["blocks_commit"] is True

    def test_make_violation_blocks_commit_high(self):
        v = make_violation("fact_conflict", "high", "test")
        assert v["blocks_commit"] is True

    def test_make_violation_blocks_commit_medium(self):
        v = make_violation("ghost_character", "medium", "test")
        assert v["blocks_commit"] is False

    def test_make_violation_non_blocking_type_override(self):
        v = make_violation("ghost_character", "high", "test")
        assert v["blocks_commit"] is False

    def test_make_violation_blocks_commit_explicit(self):
        v = make_violation("ghost_character", "medium", "test", blocks_commit=True)
        assert v["blocks_commit"] is True

    def test_make_violation_target_span(self):
        v = make_violation("forbidden_triggered", "critical", "test", target_span="穿越")
        assert v["target_span"] == "穿越"

    def test_make_violation_expected_behavior(self):
        v = make_violation("missing_must_show", "high", "test", expected_behavior="正文应体现该内容")
        assert v["expected_behavior"] == "正文应体现该内容"

    def test_violation_id_stable(self):
        v1 = make_violation("forbidden_triggered", "critical", "test", target_span="穿越")
        v2 = make_violation("forbidden_triggered", "critical", "test", target_span="穿越")
        assert v1["violation_id"] == v2["violation_id"]

    def test_violation_id_differs_for_different_input(self):
        v1 = make_violation("forbidden_triggered", "critical", "test", target_span="穿越")
        v2 = make_violation("missing_must_show", "high", "test", target_span="穿越")
        assert v1["violation_id"] != v2["violation_id"]

    def test_all_strategy_map_types_are_valid(self):
        valid_strategies = {
            "patch_text",
            "rewrite_scene",
            "repair_contract",
            "validator_retry",
            "manual_review",
            "patch_text_by_proposition",
            "rewrite_scene_with_fact_contract",
            "patch_literary_quality",
            "rewrite_scene_with_experience_contract",
            "repair_experience_contract",
            "style_experience_negotiation",
            "contract_budget_adjust",
        }
        for vtype, strategy in STRATEGY_MAP.items():
            assert strategy in valid_strategies, f"{vtype} maps to invalid strategy {strategy}"

    def test_violation_is_typed_dict(self):
        v = make_violation("fact_conflict", "high", "test")
        assert isinstance(v, dict)
        required_keys = {"violation_id", "type", "severity", "detail", "suggested_strategy",
                         "target_span", "expected_behavior", "source", "blocks_commit",
                         "scope", "repairable_by_text", "repairable_by_contract"}
        assert required_keys.issubset(set(v.keys()))
