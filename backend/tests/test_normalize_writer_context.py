import pytest

from app.services.scene_provenance import normalize_writer_context


class TestCoerceToDict:
    def test_dict_passthrough(self):
        ctx, warnings = normalize_writer_context({
            "scene_context_package": {
                "scene_beat": {"goal": "test"},
            },
        })
        assert ctx["scene_context_package"]["scene_beat"] == {"goal": "test"}
        assert warnings == []

    def test_source_of_truth_as_string(self):
        ctx, warnings = normalize_writer_context({
            "scene_context_package": {},
            "scene_contract": {
                "source_of_truth": "主角发现真相",
            },
        })
        assert ctx["scene_contract"]["source_of_truth"] == {}
        assert len(warnings) == 1
        assert warnings[0]["field"] == "scene_contract.source_of_truth"
        assert warnings[0]["expected_type"] == "dict"
        assert warnings[0]["received_type"] == "str"
        assert warnings[0]["severity"] == "critical"

    def test_editor_enrichment_as_string(self):
        ctx, warnings = normalize_writer_context({
            "scene_context_package": {},
            "scene_contract": {
                "editor_enrichment": "保持单一视角",
            },
        })
        assert ctx["scene_contract"]["editor_enrichment"] == {}
        assert len(warnings) == 1
        assert warnings[0]["field"] == "scene_contract.editor_enrichment"
        assert warnings[0]["severity"] == "optional"

    def test_scene_provenance_as_string(self):
        ctx, warnings = normalize_writer_context({
            "scene_context_package": {},
            "scene_contract": {
                "scene_provenance": "场景事实层文本",
            },
        })
        assert ctx["scene_contract"]["scene_provenance"] == {}
        assert len(warnings) == 1
        assert warnings[0]["field"] == "scene_contract.scene_provenance"

    def test_persona_card_as_string(self):
        ctx, warnings = normalize_writer_context({
            "scene_context_package": {
                "persona_card": "第一人称叙述者",
            },
        })
        assert ctx["scene_context_package"]["persona_card"] == {}
        assert len(warnings) == 1
        assert warnings[0]["field"] == "scene_context_package.persona_card"

    def test_style_statistics_as_string(self):
        ctx, warnings = normalize_writer_context({
            "scene_context_package": {
                "style_statistics": "平均段落长度120字",
            },
        })
        assert ctx["scene_context_package"]["style_statistics"] == {}
        assert len(warnings) == 1

    def test_style_embedding_as_string(self):
        ctx, warnings = normalize_writer_context({
            "scene_context_package": {
                "style_embedding": "情感外显度0.7",
            },
        })
        assert ctx["scene_context_package"]["style_embedding"] == {}
        assert len(warnings) == 1

    def test_relevant_rules_as_string(self):
        ctx, warnings = normalize_writer_context({
            "scene_context_package": {
                "relevant_rules": "魔法体系规则",
            },
        })
        assert ctx["scene_context_package"]["relevant_rules"] == {}
        assert len(warnings) == 1

    def test_chapter_rhythm_context_as_string(self):
        ctx, warnings = normalize_writer_context({
            "scene_context_package": {
                "chapter_rhythm_context": "S1快，S2慢",
            },
        })
        assert ctx["scene_context_package"]["chapter_rhythm_context"] == {}
        assert len(warnings) == 1
        assert warnings[0]["field"] == "scene_context_package.chapter_rhythm_context"

    def test_chapter_rhythm_context_passthrough(self):
        payload = {
            "chapter_rhythm_map": "本章3个场景",
            "previous_scene_anchors": [{"summary": "S1已生成"}],
            "next_scene_anchor": "S3搜查房间",
        }
        ctx, warnings = normalize_writer_context({
            "scene_context_package": {
                "chapter_rhythm_context": payload,
            },
        })
        assert ctx["scene_context_package"]["chapter_rhythm_context"] == payload
        assert warnings == []

    def test_evolution_report_as_string(self):
        ctx, warnings = normalize_writer_context({
            "scene_context_package": {
                "evolution_report": "多风格簇",
            },
        })
        assert ctx["scene_context_package"]["evolution_report"] == {}
        assert len(warnings) == 1

    def test_none_values_ignored(self):
        ctx, warnings = normalize_writer_context({
            "scene_context_package": {
                "persona_card": None,
                "style_statistics": None,
            },
            "scene_contract": {
                "source_of_truth": None,
                "editor_enrichment": None,
            },
        })
        assert warnings == []

    def test_empty_string_values_ignored(self):
        ctx, warnings = normalize_writer_context({
            "scene_context_package": {
                "persona_card": "",
            },
        })
        assert warnings == []


class TestCoerceToList:
    def test_character_cards_as_string(self):
        ctx, warnings = normalize_writer_context({
            "scene_context_package": {
                "character_cards": "角色A, 角色B",
            },
        })
        assert ctx["scene_context_package"]["character_cards"] == []
        assert len(warnings) == 1
        assert warnings[0]["expected_type"] == "list"
        assert warnings[0]["received_type"] == "str"

    def test_location_cards_as_dict(self):
        ctx, warnings = normalize_writer_context({
            "scene_context_package": {
                "location_cards": {"name": "王城"},
            },
        })
        assert ctx["scene_context_package"]["location_cards"] == []
        assert len(warnings) == 1
        assert warnings[0]["received_type"] == "dict"

    def test_historical_details_as_string(self):
        ctx, warnings = normalize_writer_context({
            "scene_context_package": {
                "historical_details": "前文细节",
            },
        })
        assert ctx["scene_context_package"]["historical_details"] == []
        assert len(warnings) == 1


class TestSceneContractTypeValidation:
    def test_scene_contract_as_string(self):
        ctx, warnings = normalize_writer_context({
            "scene_context_package": {},
            "scene_contract": "场景合同文本",
        })
        assert ctx["scene_contract"] is None
        assert len(warnings) == 1
        assert warnings[0]["field"] == "scene_contract"
        assert warnings[0]["severity"] == "critical"

    def test_must_show_as_string(self):
        ctx, warnings = normalize_writer_context({
            "scene_context_package": {},
            "scene_contract": {
                "must_show": "主角出场",
            },
        })
        assert ctx["scene_contract"]["must_show"] == []
        assert len(warnings) == 1
        assert warnings[0]["expected_type"] == "list"

    def test_forbidden_as_string(self):
        ctx, warnings = normalize_writer_context({
            "scene_context_package": {},
            "scene_contract": {
                "forbidden": "重复事件",
            },
        })
        assert ctx["scene_contract"]["forbidden"] == []
        assert len(warnings) == 1


class TestCriticalFieldTerminatesPipeline:
    def test_critical_warning_returns_early(self):
        ctx, warnings = normalize_writer_context({
            "scene_context_package": {},
            "scene_contract": {
                "source_of_truth": "主角发现真相",
                "editor_enrichment": "保持单一视角",
            },
        })
        critical = [w for w in warnings if w.get("severity") == "critical"]
        assert len(critical) == 1
        assert critical[0]["field"] == "scene_contract.source_of_truth"

    def test_no_critical_when_optional_only(self):
        ctx, warnings = normalize_writer_context({
            "scene_context_package": {
                "persona_card": "叙述者",
            },
        })
        critical = [w for w in warnings if w.get("severity") == "critical"]
        assert len(critical) == 0
        optional = [w for w in warnings if w.get("severity") == "optional"]
        assert len(optional) == 1


class TestChapterStateNormalization:
    def test_chapter_state_as_string(self):
        ctx, warnings = normalize_writer_context({
            "scene_context_package": {
                "chapter_state": "已确立事实：...",
            },
        })
        assert ctx["scene_context_package"]["chapter_state"] == {}
        assert len(warnings) == 1
        assert warnings[0]["field"] == "scene_context_package.chapter_state"


class TestSceneContextPackageNormalization:
    def test_scp_as_string(self):
        ctx, warnings = normalize_writer_context({
            "scene_context_package": "场景上下文包",
        })
        assert ctx["scene_context_package"] == {}

    def test_multiple_type_drifts_aggregate(self):
        ctx, warnings = normalize_writer_context({
            "scene_context_package": {
                "persona_card": "叙述者",
                "style_statistics": "统计信息",
                "character_cards": "角色列表",
            },
            "scene_contract": {
                "editor_enrichment": "补充信息",
                "must_show": "必须展示",
            },
        })
        assert len(warnings) == 5
        fields = [w["field"] for w in warnings]
        assert "scene_context_package.persona_card" in fields
        assert "scene_context_package.style_statistics" in fields
        assert "scene_context_package.character_cards" in fields
        assert "scene_contract.editor_enrichment" in fields
        assert "scene_contract.must_show" in fields


class TestNestedWriterContextNormalization:
    def test_scene_provenance_nested_dict_as_string(self):
        ctx, warnings = normalize_writer_context({
            "scene_context_package": {},
            "scene_contract": {
                "scene_provenance": {
                    "spatial_anchor": "当前位置：土地庙",
                    "current_facts": {
                        "character_states": "凤溪受伤",
                    },
                },
            },
        })

        provenance = ctx["scene_contract"]["scene_provenance"]
        assert provenance["spatial_anchor"] == {}
        assert provenance["current_facts"]["character_states"] == {}
        assert len(warnings) == 2

    def test_style_statistics_global_as_string(self):
        ctx, warnings = normalize_writer_context({
            "scene_context_package": {
                "style_statistics": {
                    "global": "章节数：3",
                },
            },
        })

        assert ctx["scene_context_package"]["style_statistics"]["global"] == {}
        assert warnings[0]["field"] == "scene_context_package.style_statistics.global"

    def test_relevant_rules_drops_non_dict_items(self):
        ctx, warnings = normalize_writer_context({
            "scene_context_package": {
                "relevant_rules": {
                    "critical_index": ["修士不得随意使用禁术", {"name": "禁术规则", "core": "禁止滥用"}],
                    "relevant_details": ["土地庙位于山脚"],
                },
            },
        })

        rules = ctx["scene_context_package"]["relevant_rules"]
        assert rules["critical_index"] == [{"name": "禁术规则", "core": "禁止滥用"}]
        assert rules["relevant_details"] == []
        assert len(warnings) == 2
