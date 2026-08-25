from __future__ import annotations

from app.api.editor_chat import _prepare_review_violations
from app.services.quality_finding_hub import QualityFindingHub
from app.services.repair_order_compiler import RepairOrderCompiler
from app.services.review_finding_normalizer import ReviewFindingNormalizer
from app.services.review_issue_localizer import enrich_review_issue_locations
from app.services.review_issue_semantics import normalize_violation_semantics


def test_semantic_normalizer_reclassifies_unknown_fact_detail():
    violation = normalize_violation_semantics({
        "violation_id": "u_fact_001",
        "source": "unknown",
        "type": "unknown",
        "severity": "high",
        "detail": (
            "文本描述「脚步声在门外。」与已知事实「苏云清已经离开房间，并在门外与另一人轻声交谈后离去」矛盾，"
            "建议修改为「远处又传来脚步声，似是有人朝这边走来。」"
        ),
    })

    assert violation["type"] == "spatial_conflict"
    assert violation["source"] == "consistency"
    assert violation["target_span"] == "脚步声在门外。"
    assert violation["expected_behavior"] == "远处又传来脚步声，似是有人朝这边走来。"
    assert violation["scope"] == "prose_text"
    assert violation["suggested_strategy"] != "manual_review"


def test_quality_finding_hub_routes_reclassified_unknown_to_fbi_fact():
    finding = QualityFindingHub().normalize_violation({
        "violation_id": "u_fact_002",
        "source": "unknown",
        "type": "semantic_quality_error",
        "severity": "high",
        "detail": "文本描述「他如今已是内门弟子」与已知事实「他仍是外门弟子」矛盾，建议修改为「他仍是外门弟子」",
    })

    assert finding.type == "identity_conflict"
    assert finding.repair_scope == "prose_text"
    assert finding.repair_lane == "fbi_fact"
    assert finding.evidence_spans[0]["span"] == "他如今已是内门弟子"
    assert finding.evidence_spans[0]["raw"]["semantic_type"] == "identity_conflict"


def test_legacy_review_finding_normalizer_preserves_semantic_type():
    finding = ReviewFindingNormalizer().normalize_violation({
        "violation_id": "u_fact_003",
        "source": "unknown",
        "type": "unknown",
        "severity": "high",
        "detail": "文本描述「正午的光」与时间锚点辰时三刻矛盾，建议修改为「晨间偏冷的光」",
    })

    assert finding["type"] == "timeline_conflict"
    assert finding["scope"] == "prose_text"
    assert finding["repairable_by_text"] is True
    assert finding["target_span"] == "正午的光"


def test_review_finding_normalizer_preserves_length_budget_metadata():
    finding = ReviewFindingNormalizer().normalize_violation({
        "violation_id": "u_len_001",
        "source": "unknown",
        "type": "unknown",
        "severity": "high",
        "detail": "文本长度 1345 超过硬性上限 1332",
    })

    assert finding["type"] == "scene_too_long"
    assert finding["scope"] == "prose_text"
    assert finding["current_length"] == 1345
    assert finding["hard_max_chars"] == 1332


def test_prepare_review_violations_normalizes_and_dedupes_length_findings():
    issues = _prepare_review_violations([
        {
            "violation_id": "len_1",
            "source": "unknown",
            "type": "unknown",
            "severity": "high",
            "detail": "文本长度 1345 超过硬性上限 1332",
            "blocks_commit": True,
        },
        {
            "violation_id": "len_2",
            "source": "deterministic",
            "type": "scene_too_long",
            "severity": "high",
            "detail": "场景字数1345超出硬上限1332",
            "blocks_commit": True,
        },
    ])

    assert len([item for item in issues if item["type"] == "scene_too_long"]) == 1
    issue = issues[0]
    assert issue["type"] == "scene_too_long"
    assert issue["current_length"] == 1345
    assert issue["hard_max_chars"] == 1332
    assert issue["review_status"] == "open"


def test_commit_blocking_advisory_scope_remains_visible_and_open():
    issues = _prepare_review_violations([{
        "violation_id": "identity_1",
        "type": "identity_conflict",
        "severity": "high",
        "scope": "advisory",
        "blocks_commit": True,
        "detail": "An explicit authority fact conflicts with the candidate text.",
    }])

    assert issues[0]["scope"] == "prose_text"
    assert issues[0]["review_status"] == "open"
    assert issues[0]["user_visible"] is True


def test_review_issue_localizer_adds_evidence_for_global_abstraction_issue():
    text = (
        "凤溪把药草摊在石板上，指尖压住叶脉。\n\n"
        "她忽然意识到这件事的意义远比表面复杂，仿佛命运又把她推回原处。\n\n"
        "火符在石槽底部亮了一下。"
    )

    issue = enrich_review_issue_locations(
        {
            "type": "abstraction_over_budget",
            "detail": "抽象解释超过预算：4 处，高于 2。",
            "blocks_commit": True,
        },
        text,
    )

    assert issue["localization_status"] == "localized"
    assert issue["target_span"]
    assert "意识到" in issue["target_span"]
    assert issue["evidence_spans"]
    assert issue["repair_granularity"] == "sentence_cluster"


def test_prepare_review_violations_localizes_global_quality_issue():
    text = (
        "洞里的光线比外面暗。凤溪把辅药摊开。\n\n"
        "她看着这些东西，觉得局面很复杂，也明白自己不能再退。\n\n"
        "火符压在石槽底部。"
    )

    issues = _prepare_review_violations(
        [{
            "type": "abstraction_over_budget",
            "severity": "medium",
            "detail": "抽象解释超过预算：4 处，高于 2。",
            "blocks_commit": True,
        }],
        candidate_text=text,
    )

    assert issues[0]["review_status"] == "open"
    assert issues[0]["target_span"]
    assert issues[0]["evidence_spans"]
    assert issues[0]["recommended_route"] == "repair_text"


def test_missing_must_show_uses_semantic_insertion_anchor_not_longest_paragraph():
    wrong_span = (
        "膝盖有点软，但不是站不住。她压了一下呼吸，把丹田里的灵力往四肢推。"
        "灵力在经脉里流动得太快，每一条都在涨痛。"
    )
    text = (
        f"{wrong_span}\n\n"
        "守门人向后退了一步。\n\n"
        "“别过来。”\n\n"
        "守门人松开手，钥匙掉在地上。"
    )

    issue = enrich_review_issue_locations({
        "type": "missing_must_show",
        "detail": "守门人松手前没有说出钥匙的用途，只说了'别过来'。",
        "target_span": wrong_span,
        "evidence_span": "守门人说'别过来'后松开了手",
        "evidence_spans": [{"span": wrong_span, "role": "scene_window", "score": 0}],
        "location_confidence": 0.48,
        "blocks_commit": True,
    }, text)

    assert issue["target_span"] == "“别过来。”"
    assert issue["localization_status"] == "localized"
    assert issue["location_confidence"] >= 0.9
    assert issue["evidence_spans"][0]["role"] == "insertion_anchor"


def test_missing_must_show_without_exact_anchor_fails_closed():
    wrong_span = "这是一段很长但与缺失义务无关的正文，用来确认定位器不会继续选择最长段落。"
    text = f"{wrong_span}\n\n角色转身离开。"

    issue = enrich_review_issue_locations({
        "type": "missing_must_show",
        "detail": "场景没有展示契约要求的关键动作。",
        "target_span": wrong_span,
        "evidence_spans": [{"span": wrong_span, "role": "scene_window", "score": 0}],
        "location_confidence": 0.48,
        "blocks_commit": True,
    }, text)

    assert issue["target_span"] == ""
    assert issue["localization_status"] == "missing_anchor"
    assert issue["needs_localization"] is True


def test_validator_system_issue_stays_pending_until_successful_retry():
    issues = _prepare_review_violations([{
        "type": "proposition_extractor_unavailable",
        "severity": "high",
        "detail": "Extractor returned an empty response.",
        "blocks_commit": False,
    }])

    assert issues[0]["scope"] == "validator_system"
    assert issues[0]["review_status"] == "pending_validator_retry"


def test_instruction_like_fact_fix_uses_llm_specialist_not_literal_replacement():
    finding = QualityFindingHub().normalize_violation({
        "violation_id": "u_fact_004",
        "source": "unknown",
        "type": "unknown",
        "severity": "high",
        "detail": (
            "文本描述「她擦净药渣，她走到桌边放下药碗，又转身从袖中取出一个小瓷瓶。」"
            "与已知事实「苏云清已放下药碗」矛盾，建议修改为「删除重复的放药碗，保留小瓷瓶动作」"
        ),
    })

    order = RepairOrderCompiler().compile(finding)

    assert finding.type == "fact_conflict"
    assert order.lane == "fbi_fact"
    assert order.operation == "replace_span"


def test_direct_fact_replacement_can_still_use_deterministic_preflight():
    finding = QualityFindingHub().normalize_violation({
        "violation_id": "u_fact_005",
        "source": "consistency",
        "type": "identity_conflict",
        "severity": "high",
        "detail": "身份称谓冲突",
        "target_span": "内门弟子",
        "expected_behavior": "改为「外门弟子」",
    })

    order = RepairOrderCompiler().compile(finding)

    assert order.lane == "deterministic"
    assert order.operation == "repair_fact_state"


def test_canon_timeline_confusion_is_canonicalized_to_timeline_conflict():
    finding = QualityFindingHub().normalize_violation({
        "violation_id": "u_time_001",
        "source": "critic",
        "type": "canon_timeline_confusion",
        "severity": "high",
        "detail": "正文中写成了两日后，但事实层明确为三日后，时间不一致。",
        "target_span": "两日后",
        "expected_behavior": "正文应使用三日后以匹配事实层时间锚点。",
    })

    order = RepairOrderCompiler().compile(finding)

    assert finding.type == "timeline_conflict"
    assert finding.repair_lane == "deterministic"
    assert finding.repair_scope == "prose_text"
    assert order.lane == "deterministic"
    assert order.operation == "repair_time_anchor"
    assert order.target_region["expected_behavior"] == "正文应使用三日后以匹配事实层时间锚点。"


def test_direct_temporal_replacement_prefers_patch_text_strategy():
    violation = normalize_violation_semantics({
        "violation_id": "u_time_002",
        "source": "critic",
        "type": "canon_timeline_confusion",
        "severity": "high",
        "detail": "正文中写成两日后，但事实层要求三日后。",
        "target_span": "两日后",
        "expected_behavior": "正文应使用三日后。",
    })

    assert violation["type"] == "timeline_conflict"
    assert violation["suggested_strategy"] == "patch_text"
