from app.services.context_envelope_builder import ContextEnvelopeBuilder
from app.services.evidence_provenance_contract import (
    build_evidence_provenance_contract,
    render_evidence_provenance_rules,
)
from app.services.writer_input_enrichment_adapter import WriterInputEnrichmentAdapter


def test_hook_out_is_softened_when_it_confirms_forbidden_identity_reveal():
    contract = {
        "scene_function": "climax",
        "forbidden": [
            "不得让幕后操盘者完全暴露身份或出场露面",
            "不得揭示幕后操盘者的动机或具体身份",
        ],
        "commercial_pacing_contract": {
            "hook_out": "主角确认真正的敌人就是此前传闻中的内门长辈。",
        },
    }

    enrichment = WriterInputEnrichmentAdapter().build(contract)

    hook_out = enrichment.packet_patch["climax_pacing"]["hook_out"]
    assert "内门长辈" not in hook_out
    assert "真正的敌人" not in hook_out
    assert "不得确认幕后者身份" in hook_out


def test_hook_out_keeps_non_confirming_clue_under_identity_reveal_ban():
    contract = {
        "scene_function": "climax",
        "forbidden": ["不得揭示幕后操盘者的动机或具体身份"],
        "commercial_pacing_contract": {
            "hook_out": "主角瞥见回廊转角处一角玄色衣袍消失。",
        },
    }

    enrichment = WriterInputEnrichmentAdapter().build(contract)

    hook_out = enrichment.packet_patch["climax_pacing"]["hook_out"]
    assert hook_out == "主角瞥见回廊转角处一角玄色衣袍消失。"


def test_evidence_provenance_contract_is_generic_writer_guidance():
    contract = build_evidence_provenance_contract()
    rules = render_evidence_provenance_rules(contract)

    assert contract["rule"] == "no_unprovenanced_evidence"
    assert "source" in contract["required_fields"]
    assert "acquisition_time" in contract["required_fields"]
    assert "causal_link" in contract["required_fields"]
    assert "纸条" in contract["carriers"]
    assert "地图" in contract["carriers"]
    assert any("任何推动行动" in rule for rule in rules)


def test_writer_enrichment_exposes_evidence_provenance_control():
    contract = {
        "evidence_provenance_contract": build_evidence_provenance_contract(),
    }

    enrichment = WriterInputEnrichmentAdapter().build(contract)

    assert "evidence_provenance_control" in enrichment.active_capabilities
    patch = enrichment.packet_patch["evidence_provenance_control"]
    assert patch["rule"] == "no_unprovenanced_evidence"
    assert "source" in patch["required_fields"]


def test_chapter_writer_packet_carries_evidence_provenance_contract():
    builder = ContextEnvelopeBuilder()
    packet = builder.build_chapter_writer_packet(
        envelope={"context_ledger_id": "ledger-test", "blocks": []},
        chapter_number=16,
        scene_contracts=[{
            "scene_id": "s1",
            "goal": "追查线索",
            "must_show": ["主角根据证据转向新地点"],
        }],
    )

    scene = packet["scene_map"][0]
    assert scene["evidence_provenance_contract"]["rule"] == "no_unprovenanced_evidence"
    assert "evidence_provenance_control" in scene["writer_input_enrichment"]["active_capabilities"]
    assert any("证据来源硬约束" in rule for rule in packet["system_rules"])
    assert "evidence_provenance_contract" in packet["user_prompt"]
