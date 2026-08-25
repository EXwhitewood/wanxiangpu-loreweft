import pytest

from app.models.agent_skill import CompiledSkillPacket


def test_inner_access_evidence_uses_configured_markers_without_story_binding():
    from app.utils.pov_evidence import InnerAccessRuleConfig, analyze_inner_access

    config = InnerAccessRuleConfig(markers=("\u5bdf\u89c9",))
    text = (
        "\u4e3b\u89d2\u5bdf\u89c9\u95e8\u540e\u6709\u98ce\u3002"
        "\u914d\u89d2\u5bdf\u89c9\u673a\u5173\u6b63\u5728\u542f\u52a8\u3002"
    )

    analysis = analyze_inner_access(text, pov_name="\u4e3b\u89d2", config=config)

    assert [item["subject"] for item in analysis["confirmed"]] == ["\u914d\u89d2"]
    assert analysis["confirmed"][0]["rule_id"] == "narrative.inner_access"
    assert analysis["confirmed"][0]["scope"] == "pov_boundary"


def test_inner_access_ignores_body_part_compounds_and_degree_complements():
    from app.utils.pov_evidence import analyze_inner_access

    text = (
        "水苔在掌心里被压碎。"
        "她手心里全是汗。"
        "石声响到她觉得外面一定听得到。"
        "旁观者心里一沉。"
    )

    analysis = analyze_inner_access(text, pov_name="凤溪")

    assert [item["subject"] for item in analysis["confirmed"]] == ["旁观者"]
    assert [item["subject"] for item in analysis["ambiguous"]] == ["她"]


def test_inner_access_does_not_promote_objects_or_connectors_to_characters():
    from app.utils.pov_evidence import analyze_inner_access

    text = (
        "黑气像刀穿过纸，纸不觉得痛。"
        "凤溪不知道它意味着什么，也不知道痕迹从何而来。"
        "她只知道自己必须往前走。"
        "苏云清觉得寒意正在逼近。"
    )

    analysis = analyze_inner_access(
        text,
        pov_name="凤溪",
        known_character_names=("凤溪", "苏云清"),
    )

    assert [item["subject"] for item in analysis["confirmed"]] == ["苏云清"]
    assert not any(
        item["subject"] in {"纸", "也", "她只"}
        for item in [*analysis["confirmed"], *analysis["ambiguous"]]
    )


@pytest.mark.asyncio
async def test_ai_flavor_validator_fails_dash_density_contract():
    from app.services.agent_skill_validator import AgentSkillValidator

    packet = CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=["anti_ai_prose"],
        validators=["ai_flavor"],
        validation_contracts={
            "ai_flavor": {
                "anti_ai_prose": {
                    "dash_per_1000_max": 2.5,
                    "retry_policy": {"action": "style_repair"},
                }
            }
        },
        repair_hooks={"ai_flavor": ["prose_repair"]},
    )
    text = "他停住——又往前走——灯影一晃——门缝里没有声音——"

    result = await AgentSkillValidator().validate_output(packet, generated_text=text)

    assert result["passed"] is False
    finding = result["failures"][0]
    assert finding["skill_id"] == "anti_ai_prose"
    assert finding["metric"] == "dash_per_1000"
    assert finding["action"] == "style_repair"
    assert result["validators"]["ai_flavor"]["repair_hooks"] == ["prose_repair"]


@pytest.mark.asyncio
async def test_ai_flavor_validator_counts_all_dash_artifact_variants():
    from app.services.agent_skill_validator import AgentSkillValidator

    packet = CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=["anti_ai_prose"],
        validators=["ai_flavor"],
        validation_contracts={
            "ai_flavor": {
                "anti_ai_prose": {
                    "dash_per_1000_max": 2.5,
                    "retry_policy": {"action": "style_repair"},
                }
            }
        },
    )
    text = "甲——乙。丙—丁。戊--己。庚-辛。"

    result = await AgentSkillValidator().validate_output(packet, generated_text=text)

    finding = next(item for item in result["failures"] if item.get("metric") == "dash_per_1000")
    assert finding["actual"] == 4


@pytest.mark.asyncio
async def test_validator_reports_unimplemented_as_non_blocking_trace():
    from app.services.agent_skill_validator import AgentSkillValidator

    packet = CompiledSkillPacket(
        agent_name="core_generation",
        validators=["future_validator"],
    )

    result = await AgentSkillValidator().validate_output(packet, generated_text="正文")

    assert result["passed"] is False
    assert result["validators"]["future_validator"]["status"] == "degraded"
    assert result["failures"][0]["action"] == "implement_validator"


@pytest.mark.asyncio
async def test_ai_flavor_dash_density_uses_checker_metric_once():
    from app.services.agent_skill_validator import AgentSkillValidator

    packet = CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=["anti_ai_prose"],
        validators=["ai_flavor"],
        validation_contracts={
            "ai_flavor": {
                "anti_ai_prose": {"dash_per_1000_max": 3.0}
            }
        },
    )
    text = "他停住——又往前走——" + ("灯影压在门槛上。" * 130)

    result = await AgentSkillValidator().validate_output(packet, generated_text=text)

    dash_findings = [
        item for item in result["failures"]
        if item.get("metric") == "dash_per_1000"
    ]
    assert dash_findings == []


@pytest.mark.asyncio
async def test_ai_flavor_validator_enforces_anti_ai_pattern_contracts():
    from app.services.agent_skill_validator import AgentSkillValidator

    packet = CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=["anti_ai_prose"],
        validators=["ai_flavor"],
        validation_contracts={
            "ai_flavor": {
                "anti_ai_prose": {
                    "tier1_hit_count": 0,
                    "tier2_cluster_count": 0,
                    "tier3_density_max": 0.03,
                    "three_part_escalation_count": 0,
                    "negative_parallel_count": 0,
                    "false_range_count": 0,
                    "vague_attribution_count": 0,
                    "filler_phrase_count": 0,
                    "promotional_language_count": 0,
                    "inflated_significance_count": 0,
                    "structure_word_cluster_count": 0,
                    "chapter_end_moral_count": 0,
                    "overexplain_count": 0,
                    "retry_policy": {"action": "prose_repair"},
                }
            }
        },
    )
    text = (
        "值得注意的是，这不仅标志着一个至关重要的转折，"
        "而且见证了命运与救赎的交织，更体现了深远意义。\n"
        "首先，勾勒、渲染、流淌在同一段里弥漫开来；其次，专家认为这说明了问题。\n"
        "从门前到院后，从灯下到暗处，所有变化都令人叹为观止。\n"
        "这只是一个开始。"
    )

    result = await AgentSkillValidator().validate_output(packet, generated_text=text)

    # AI flavor 指标（tier1/tier2/three_part/false_range/chapter_end_moral/overexplain）均为 advisory，
    # 不阻断 commit（enforcement=advisory），但 findings 仍记录供观测和修复。
    assert result["passed"] is True
    assert result["failures"] == []
    ai_flavor_findings = result["validators"]["ai_flavor"]["findings"]
    metrics = {item["metric"] for item in ai_flavor_findings}
    assert "tier1_hit_count" in metrics
    assert "tier2_cluster_count" in metrics
    assert "three_part_escalation_count" in metrics
    assert "false_range_count" in metrics
    assert "chapter_end_moral_count" in metrics
    assert "overexplain_count" in metrics
    # 所有 AI flavor findings 应标记为 advisory_only
    assert all(item.get("advisory_only") is True for item in ai_flavor_findings)
    actions = {item["action"] for item in ai_flavor_findings}
    assert "tier1_replace" in actions
    assert "tier2_decluster" in actions
    assert "break_three_part" in actions


@pytest.mark.asyncio
async def test_ai_discourse_validator_flags_sentence_shells_and_action_explanation():
    from app.services.agent_skill_validator import AgentSkillValidator

    packet = CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=["anti_ai_prose"],
        validators=["ai_discourse"],
        validation_contracts={
            "ai_discourse": {
                "anti_ai_prose": {
                    "sentence_shell_count_max": 0,
                    "action_then_explanation_count_max": 0,
                    "retry_policy": {"action": "paragraph_reconstruction"},
                }
            }
        },
        repair_hooks={"ai_discourse": ["paragraph_reconstruction"]},
    )
    text = (
        "重要的不是他是否害怕，而是他是否继续向前。"
        "他把信扔进火盆，纸角立刻卷黑。"
        "这说明他终于放下了过去。"
    )

    result = await AgentSkillValidator().validate_output(packet, generated_text=text)

    # AI discourse 指标（sentence_shell/action_then_explanation）均为 advisory，
    # 不阻断 commit，但 findings 仍记录供观测和修复。
    assert result["passed"] is True
    assert result["failures"] == []
    discourse_findings = result["validators"]["ai_discourse"]["findings"]
    metrics = {item["metric"] for item in discourse_findings}
    assert "sentence_shell_count" in metrics
    assert "action_then_explanation_count" in metrics
    assert all(item.get("advisory_only") is True for item in discourse_findings)
    assert result["validators"]["ai_discourse"]["repair_hooks"] == ["paragraph_reconstruction"]


def test_ai_discourse_paragraph_shape_metric_scales_for_long_chapters():
    from app.services.quality_checkers.ai_discourse_checker import AIDiscourseChecker

    paragraphs = []
    for index in range(80):
        if index % 4 == 0:
            paragraphs.append(f"凤溪把第{index}株药草放进布袋。石面潮湿，叶脉贴着指腹发凉。")
        elif index % 4 == 1:
            paragraphs.append(f"山风从洞口压下来。她停了一息，把松开的绳结重新勒紧，又看了一眼坡下。")
        elif index % 4 == 2:
            paragraphs.append(f"五师兄没有催，只把药铲递过来。铁口碰到石缝，发出很短的一声。")
        else:
            paragraphs.append(f"火光在岩壁上抖了抖。她把根须上的泥剥掉，放进另一只小袋。水声还在脚边。")
    text = "\n\n".join(paragraphs)

    report = AIDiscourseChecker().check(text)

    assert report["metrics"]["paragraph_count"] == 80
    assert report["metrics"]["paragraph_shape_repeat_count"] <= 1


@pytest.mark.asyncio
async def test_voice_fingerprint_validator_flags_generic_voice_and_avoided_words():
    from app.services.agent_skill_validator import AgentSkillValidator

    packet = CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=["anti_ai_prose"],
        validators=["voice_fingerprint"],
        validation_contracts={
            "voice_fingerprint": {
                "anti_ai_prose": {
                    "profile_required": True,
                    "minimum_score": 80,
                    "generic_voice_density_max": 1,
                    "avoided_word_hits_max": 0,
                    "retry_policy": {"action": "voice_reconstruction"},
                }
            }
        },
        repair_hooks={"voice_fingerprint": ["voice_reconstruction"]},
    )
    text = "他眸光一沉，心头一颤。宏大的命运让空气仿佛凝固。"

    result = await AgentSkillValidator().validate_output(
        packet,
        generated_text=text,
        context={
            "voice_fingerprint": {
                "avoided_words": ["宏大"],
                "preferred_markers": ["药味"],
                "sentence_distribution": {"average": 10},
            }
        },
    )

    assert result["passed"] is True
    assert result["failures"] == []
    findings = result["validators"]["voice_fingerprint"]["findings"]
    metrics = {item["metric"] for item in findings}
    assert "generic_voice_density" in metrics
    assert "avoided_word_hits" in metrics
    assert all(item.get("advisory_only") is True for item in findings)
    assert result["validators"]["voice_fingerprint"]["status"] == "ok"


@pytest.mark.asyncio
async def test_voice_fingerprint_without_profile_is_degraded_but_uses_generic_contract():
    from app.services.agent_skill_validator import AgentSkillValidator

    packet = CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=["anti_ai_prose"],
        validators=["voice_fingerprint"],
        validation_contracts={
            "voice_fingerprint": {
                "anti_ai_prose": {
                    "profile_required": False,
                    "generic_voice_density_max": 100,
                }
            }
        },
    )

    result = await AgentSkillValidator().validate_output(
        packet,
        generated_text="他推门进屋，药味从帘后飘出来。",
    )

    assert result["passed"] is True
    assert result["validators"]["voice_fingerprint"]["status"] == "degraded"


@pytest.mark.asyncio
async def test_literary_quality_validator_executes_without_declared_hard_contract():
    from app.services.agent_skill_validator import AgentSkillValidator

    packet = CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=["show_dont_tell"],
        validators=["literary_quality"],
        repair_hooks={
            "literary_quality": [
                "emotion_to_scene_evidence",
                "exposition_to_action",
            ]
        },
    )

    result = await AgentSkillValidator().validate_output(
        packet,
        generated_text="她握住冰冷的门闩，指节发白。院外脚步停在石阶下。",
    )

    validator = result["validators"]["literary_quality"]
    assert validator["status"] == "ok"
    assert validator["passed"] is True
    assert "scores" in validator
    assert validator["repair_hooks"] == [
        "emotion_to_scene_evidence",
        "exposition_to_action",
    ]


@pytest.mark.asyncio
async def test_literary_quality_validator_enforces_declared_medium_limit():
    from app.services.agent_skill_validator import AgentSkillValidator

    packet = CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=["strict_literary_quality"],
        validators=["literary_quality"],
        validation_contracts={
            "literary_quality": {
                "strict_literary_quality": {
                    "max_medium_advisories": 0,
                }
            }
        },
    )
    text = "命运与意义交织，复杂情绪在内心深处浮动。" * 20

    result = await AgentSkillValidator().validate_output(
        packet,
        generated_text=text,
        context={
            "literary_quality_contract": {
                "specificity_budget": {"abstract_explanation_limit": 1}
            }
        },
    )

    # literary_quality 的 medium_advisories 聚合指标为 advisory，不阻断 commit。
    assert result["passed"] is True
    assert result["failures"] == []
    assert result["validators"]["literary_quality"]["status"] == "ok"
    # 方案 7 Part E：literary_quality 的 metric 统一为 literary_quality_advisory，
    # 原始 metric（medium_advisories）保留在 original_metric 字段。
    literary_findings = result["validators"]["literary_quality"]["findings"]
    assert any(
        item.get("metric") == "literary_quality_advisory"
        and item.get("original_metric") == "medium_advisories"
        for item in literary_findings
    )
    assert all(item.get("advisory_only") is True for item in literary_findings)


@pytest.mark.asyncio
async def test_scene_structure_validator_enforces_scene_contract():
    from app.services.agent_skill_validator import AgentSkillValidator

    packet = CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=["scene_crafting"],
        validators=["scene_structure"],
        validation_contracts={
            "scene_structure": {
                "scene_crafting": {
                    "goal_present": "required",
                    "conflict_present": "required",
                    "value_change": "required",
                    "hook_present": "required",
                    "conflict_count_min": 1,
                    "pure_exposition_block_max_words": 150,
                    "passive_percentage_max": 0.15,
                    "retry_policy": {"action": "scene_restructure"},
                }
            }
        },
        repair_hooks={"scene_structure": ["scene_restructure"]},
    )

    result = await AgentSkillValidator().validate_output(
        packet,
        generated_text="庭院很安静。墙上有旧痕。她想起很多往事，意识到这里曾经很重要。",
    )

    assert result["passed"] is False
    validator = result["validators"]["scene_structure"]
    assert validator["status"] == "ok"
    assert validator["repair_hooks"] == ["scene_restructure"]
    assert any(item["metric"] == "conflict_present" for item in result["failures"])


@pytest.mark.asyncio
async def test_narrative_experience_validator_runs_checker():
    from app.services.agent_skill_validator import AgentSkillValidator

    packet = CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=["scene_crafting"],
        validators=["narrative_experience"],
        validation_contracts={
            "narrative_experience": {
                "scene_crafting": {
                    "pressure_score_min": 3,
                    "reader_momentum_at_exit": "required",
                    "retry_policy": {"action": "scene_restructure"},
                }
            }
        },
        repair_hooks={"narrative_experience": ["scene_restructure"]},
    )

    result = await AgentSkillValidator().validate_output(
        packet,
        generated_text="她抓住门环，门外脚步逼近。石阶下传来刀鞘撞击声，她不能退，只能把血按在锁眼上。门开了，里面没有灯。",
        context={
            "narrative_experience_contract": {
                "obstacle": "door guard",
                "decision_point": "open the door or flee",
                "curiosity_question": "what is behind the door",
            }
        },
    )

    validator = result["validators"]["narrative_experience"]
    assert validator["status"] == "ok"
    assert "scores" in validator
    assert validator["repair_hooks"] == ["scene_restructure"]


@pytest.mark.asyncio
async def test_rhythm_metrics_validator_flags_uniform_dense_prose():
    from app.services.agent_skill_validator import AgentSkillValidator

    packet = CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=["rhythm_control"],
        validators=["rhythm_metrics"],
        validation_contracts={
            "rhythm_metrics": {
                "rhythm_control": {
                    "sentence_length_variance_min": 8,
                    "uniform_sentence_streak_max": 3,
                    "breathing_paragraph_ratio_min": 0.15,
                    "dense_paragraph_streak_max": 2,
                    "transition_paragraph_present": True,
                    "abrupt_shift_count": 0,
                    "retry_policy": {"action": "pacing_repair"},
                }
            }
        },
        repair_hooks={"rhythm_metrics": ["pacing_repair"]},
    )
    text = "\n".join([
        "\u4ed6\u63a8\u95e8\u51b2\u8fdb\u9662\u91cc\u3002\u5979\u6293\u4f4f\u5200\u67c4\u540e\u9000\u3002\u706f\u5f71\u7838\u5728\u5899\u9762\u4e0a\u3002",
        "\u4ed6\u63a8\u95e8\u51b2\u8fdb\u9662\u91cc\u3002\u5979\u6293\u4f4f\u5200\u67c4\u540e\u9000\u3002\u706f\u5f71\u7838\u5728\u5899\u9762\u4e0a\u3002",
        "\u4ed6\u63a8\u95e8\u51b2\u8fdb\u9662\u91cc\u3002\u5979\u6293\u4f4f\u5200\u67c4\u540e\u9000\u3002\u706f\u5f71\u7838\u5728\u5899\u9762\u4e0a\u3002",
        "\u4ed6\u63a8\u95e8\u51b2\u8fdb\u9662\u91cc\u3002\u5979\u6293\u4f4f\u5200\u67c4\u540e\u9000\u3002\u706f\u5f71\u7838\u5728\u5899\u9762\u4e0a\u3002",
    ])

    result = await AgentSkillValidator().validate_output(packet, generated_text=text)

    # 节奏指标（variance/uniform_streak/breathing_ratio/dense_streak）均为 advisory，
    # 不阻断 commit，但 findings 仍记录供观测和修复。
    assert result["passed"] is True
    assert result["failures"] == []
    validator = result["validators"]["rhythm_metrics"]
    assert validator["status"] == "ok"
    assert validator["repair_hooks"] == ["pacing_repair"]
    rhythm_findings = validator["findings"]
    metrics = {item["metric"] for item in rhythm_findings}
    assert "sentence_length_variance" in metrics
    assert "uniform_sentence_streak_max" in metrics
    assert "breathing_paragraph_ratio" in metrics
    assert "dense_paragraph_streak_max" in metrics
    assert all(item.get("advisory_only") is True for item in rhythm_findings)


@pytest.mark.asyncio
async def test_rhythm_metrics_validator_requires_transition_only_when_shift_requested():
    from app.services.agent_skill_validator import AgentSkillValidator

    packet = CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=["rhythm_control"],
        validators=["rhythm_metrics"],
        validation_contracts={
            "rhythm_metrics": {
                "rhythm_control": {
                    "transition_paragraph_present": True,
                    "retry_policy": {"action": "pacing_repair"},
                }
            }
        },
    )
    text = "\u5979\u63a8\u5f00\u95e8\u3002\u9662\u91cc\u5f88\u9759\u3002"

    no_shift = await AgentSkillValidator().validate_output(packet, generated_text=text)
    assert no_shift["passed"] is True

    shift = await AgentSkillValidator().validate_output(
        packet,
        generated_text=text,
        context={
            "previous_scene_rhythm": "slow",
            "scene_contract": {"rhythm": "fast"},
        },
    )

    # transition_paragraph_present 为 advisory，不阻断 commit。
    assert shift["passed"] is True
    assert shift["failures"] == []
    shift_findings = shift["validators"]["rhythm_metrics"]["findings"]
    assert any(
        item["metric"] == "transition_paragraph_present"
        for item in shift_findings
    )
    assert all(item.get("advisory_only") is True for item in shift_findings)


@pytest.mark.asyncio
async def test_rhythm_metrics_does_not_count_natural_shift_without_contract():
    from app.services.agent_skill_validator import AgentSkillValidator

    packet = CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=["rhythm_control"],
        validators=["rhythm_metrics"],
        validation_contracts={
            "rhythm_metrics": {
                "rhythm_control": {
                    "abrupt_shift_count": 0,
                }
            }
        },
    )
    text = "\n\n".join([
        "她推门冲进院里，抓住刀柄后退，灯影砸在墙面上。",
        "风从窗缝里进来，纸灰在地上慢慢散开。",
        "她抬手挡住第二刀，肩头撞在门框上，木屑扎进袖口。",
        "雨声停了一瞬，屋檐下只剩水珠落进瓦盆。",
    ])

    result = await AgentSkillValidator().validate_output(packet, generated_text=text)

    assert result["passed"] is True
    assert result["validators"]["rhythm_metrics"]["metrics"]["abrupt_shift_count"] == 0


@pytest.mark.asyncio
async def test_pov_consistency_validator_flags_boundary_breach_and_head_hopping():
    from app.services.agent_skill_validator import AgentSkillValidator

    packet = CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=["narrative_writing"],
        validators=["pov_consistency"],
        validation_contracts={
            "pov_consistency": {
                "narrative_writing": {
                    "single_pov_per_scene": "required",
                    "head_hopping_count": 0,
                    "knowledge_boundary_breach": 0,
                    "narrator_commentary_max": 0,
                    "retry_policy": {"action": "fix_pov_leak"},
                }
            }
        },
        repair_hooks={"pov_consistency": ["voice_repair"]},
    )

    text = (
        "\u4e3b\u89d2\u63e1\u4f4f\u95e8\u73af\u3002"
        "\u65c1\u89c2\u8005\u5fc3\u91cc\u4e00\u6c89\uff0c"
        "\u4ed6\u77e5\u9053\u4e3b\u89d2\u5c1a\u672a\u53d1\u73b0\u5bc6\u5ba4\u3002"
    )

    result = await AgentSkillValidator().validate_output(
        packet,
        generated_text=text,
        context={
            "scene_contract": {
                "pov": "\u4e3b\u89d2",
                "do_not_reveal_yet": ["\u5bc6\u5ba4"],
            }
        },
    )

    assert result["passed"] is False
    validator = result["validators"]["pov_consistency"]
    assert validator["status"] == "ok"
    assert validator["repair_hooks"] == ["voice_repair"]
    head_hopping = next(
        item for item in result["failures"]
        if item["metric"] == "head_hopping_count"
    )
    assert head_hopping["target_span"] == "\u65c1\u89c2\u8005\u5fc3\u91cc\u4e00\u6c89"
    assert head_hopping["evidence"]["matches"][0]["subject"] == "\u65c1\u89c2\u8005"
    assert not any(item["metric"] == "single_pov_per_scene" for item in result["failures"])
    assert any(item["metric"] == "knowledge_boundary_breach" for item in result["failures"])


@pytest.mark.asyncio
async def test_pov_consistency_validator_does_not_treat_connectors_or_negation_as_names():
    from app.services.agent_skill_validator import AgentSkillValidator

    packet = CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=["narrative_writing"],
        validators=["pov_consistency"],
        validation_contracts={
            "pov_consistency": {
                "narrative_writing": {
                    "single_pov_per_scene": "required",
                    "head_hopping_count": 0,
                }
            }
        },
    )
    text = (
        "\u4e3b\u89d2\u4e0d\u77e5\u9053\u540c\u4f34\u8eab\u4f53\u91cc\u53d1\u751f\u4e86\u4ec0\u4e48\uff0c"
        "\u4f46\u5979\u77e5\u9053\u5fc5\u987b\u5728\u5371\u673a\u6269\u6563\u524d\u627e\u5230\u4ed6\u3002"
        "\u4e3b\u89d2\u8111\u4e2d\u63a0\u8fc7\u4e00\u5e45\u65e7\u753b\u9762\u3002"
        "\u8def\u7684\u53e6\u4e00\u8fb9\u6709\u4e00\u5757\u754c\u7891\u3002"
        "\u5982\u679c\u5979\u77e5\u9053\u95e8\u540e\u6709\u4eba\u5c31\u4e0d\u4f1a\u9760\u8fd1\u3002"
        "\u540e\u6765\u5979\u624d\u77e5\u9053\u90a3\u662f\u5e7b\u8c61\u3002"
    )

    result = await AgentSkillValidator().validate_output(
        packet,
        generated_text=text,
        context={"scene_contract": {"pov": "\u4e3b\u89d2"}},
    )

    assert result["passed"] is True
    metrics = result["validators"]["pov_consistency"]["metrics"]
    assert metrics["head_hopping_count"] == 0
    assert metrics["named_inner_access_hits"] == 0
    assert metrics["ambiguous_inner_access_hits"] == 2
    assert metrics["ambiguous_inner_access_evidence"][0]["subject"] == "\u5979"


@pytest.mark.asyncio
async def test_pov_consistency_resolves_modal_pronoun_to_pov_and_local_pronoun_to_non_pov():
    from app.services.agent_skill_validator import AgentSkillValidator

    packet = CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=["narrative_writing"],
        validators=["pov_consistency"],
        validation_contracts={
            "pov_consistency": {
                "narrative_writing": {
                    "single_pov_per_scene": "required",
                    "head_hopping_count": 0,
                }
            }
        },
    )
    text = (
        "林澈把断刃收回鞘中。她必须知道门后藏着什么。"
        "三师兄的手按着石门，但他觉得门后的人已经走了。"
    )

    result = await AgentSkillValidator().validate_output(
        packet,
        generated_text=text,
        context={
            "scene_contract": {"pov": "林澈"},
            "chapter_state": {"character_states": {"林澈": {}}},
        },
    )

    assert result["passed"] is False
    metrics = result["validators"]["pov_consistency"]["metrics"]
    assert metrics["head_hopping_count"] == 1
    assert metrics["head_hopping_evidence"][0]["subject"] == "三师兄"
    assert metrics["head_hopping_evidence"][0]["surface_subject"] == "他"
    assert metrics["head_hopping_evidence"][0]["target_span"].startswith("他觉得")
    assert all(
        "她必须知道" not in item.get("target_span", "")
        for item in metrics["head_hopping_evidence"]
    )


@pytest.mark.asyncio
async def test_pov_consistency_validator_ignores_inner_markers_inside_dialogue():
    from app.services.agent_skill_validator import AgentSkillValidator

    packet = CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=["narrative_writing"],
        validators=["pov_consistency"],
        validation_contracts={
            "pov_consistency": {
                "narrative_writing": {
                    "single_pov_per_scene": "required",
                    "head_hopping_count": 0,
                }
            }
        },
    )
    text = (
        "\u4e3b\u89d2\u6309\u4f4f\u684c\u89d2\u3002"
        "\u4e94\u5e08\u5144\u538b\u4f4e\u58f0\u97f3\uff1a\u201c\u4f60\u77e5\u9053\u7d2b\u7eb9\u7075\u829d\u957f\u5728\u4ec0\u4e48\u5730\u65b9\u5417\uff1f\u201d"
        "\u4e3b\u89d2\u6447\u5934\uff1a\u201c\u4e0d\u77e5\u9053\u3002\u201d"
    )

    result = await AgentSkillValidator().validate_output(
        packet,
        generated_text=text,
        context={"scene_contract": {"pov": "\u4e3b\u89d2"}},
    )

    metrics = result["validators"]["pov_consistency"]["metrics"]
    assert result["passed"] is True
    assert metrics["head_hopping_count"] == 0
    assert metrics["named_inner_access_hits"] == 0


@pytest.mark.asyncio
async def test_tense_consistency_validator_flags_unreturned_flashback():
    from app.services.agent_skill_validator import AgentSkillValidator

    packet = CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=["narrative_writing"],
        validators=["tense_consistency"],
        validation_contracts={
            "tense_consistency": {
                "narrative_writing": {
                    "tense_drift_count": 0,
                    "flashback_tense_correct": "required",
                    "time_jump_without_signal_count": 0,
                    "retry_policy": {"action": "correct_tense"},
                }
            }
        },
        repair_hooks={"tense_consistency": ["voice_repair"]},
    )
    text = (
        "\u5979\u63a8\u5f00\u95e8\u3002"
        "\u4e09\u5e74\u524d\uff0c\u5979\u66fe\u7ecf\u5728\u8fd9\u91cc\u89c1\u8fc7\u90a3\u4e2a\u4eba\u3002"
        "\u90a3\u65f6\u96e8\u5f88\u5927\uff0c\u5979\u4e00\u76f4\u7b49\u5230\u5929\u4eae\u3002"
    )

    result = await AgentSkillValidator().validate_output(packet, generated_text=text)

    assert result["passed"] is False
    validator = result["validators"]["tense_consistency"]
    assert validator["status"] == "ok"
    assert validator["repair_hooks"] == ["voice_repair"]
    assert any(item["metric"] == "flashback_tense_correct" for item in result["failures"])


@pytest.mark.asyncio
async def test_tense_consistency_does_not_treat_adjectival_cengjing_as_flashback():
    from app.services.agent_skill_validator import AgentSkillValidator

    metrics = AgentSkillValidator()._temporal_consistency_metrics(
        "碎片严丝合缝，看不出曾经碎裂过。",
        {"temporal_anchor": "接续上一场景"},
        {},
    )

    assert metrics["flashback_entry_count"] == 0
    assert metrics["flashback_tense_correct"] is True


@pytest.mark.asyncio
async def test_temporal_anchor_accepts_contract_continuity_and_observable_duration():
    from app.services.agent_skill_validator import AgentSkillValidator

    packet = CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=["narrative_writing"],
        validators=["tense_consistency"],
        validation_contracts={
            "tense_consistency": {
                "narrative_writing": {
                    "temporal_anchor_required": True,
                    "tense_drift_count": 0,
                    "flashback_tense_correct": "required",
                    "time_jump_without_signal_count": 0,
                }
            }
        },
    )
    text = "石门震了一下。她抬手护住脸。"

    continuity = await AgentSkillValidator().validate_output(
        packet,
        generated_text=text,
        context={"scene_contract": {"temporal_anchor": "接续上一场景结尾"}},
    )
    duration = await AgentSkillValidator().validate_output(
        packet,
        generated_text="石门最多还能撑三十息。",
        context={"scene_contract": {}},
    )
    missing = await AgentSkillValidator().validate_output(
        packet,
        generated_text=text,
        context={"scene_contract": {}},
    )

    assert continuity["passed"] is True
    continuity_metrics = continuity["validators"]["tense_consistency"]["metrics"]
    assert continuity_metrics["textual_temporal_anchor_count"] == 0
    assert continuity_metrics["contract_continuity_anchor"] is True
    assert continuity_metrics["temporal_anchor_source"] == "scene_contract_continuity"
    assert duration["passed"] is True
    assert duration["validators"]["tense_consistency"]["metrics"]["temporal_anchor_source"] == "text"
    assert missing["passed"] is False


@pytest.mark.asyncio
async def test_scene_evidence_validator_flags_telling_patterns():
    from app.services.agent_skill_validator import AgentSkillValidator

    packet = CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=["show_dont_tell"],
        validators=["scene_evidence"],
        validation_contracts={
            "scene_evidence": {
                "show_dont_tell": {
                    "emotion_label_replacement_required": True,
                    "emotion_label_count_max": 0,
                    "thought_verb_count_max_per_500": 2,
                    "max_standalone_abstract_claims": 0,
                    "trait_statement_count_max": 0,
                    "body_language_cliche_count_max": 0,
                    "dialogue_tag_emotion_label_forbidden": True,
                    "min_senses_per_scene": 2,
                    "retry_policy": {"action": "emotion_to_scene_evidence"},
                }
            }
        },
        repair_hooks={"scene_evidence": ["prose_repair", "emotion_to_scene_evidence"]},
    )
    text = (
        "\u4ed6\u5f88\u6124\u6012\uff0c\u4e5f\u610f\u8bc6\u5230\u81ea\u5df1\u9519\u4e86\u3002"
        "\u6c14\u6c1b\u53d8\u5f97\u538b\u6291\u3002"
        "\u5979\u662f\u4e00\u4e2a\u5584\u826f\u7684\u4eba\u3002"
        "\u4ed6\u63e1\u7d27\u62f3\u5934\u3002"
        "\u201c\u4f60\u8d70\u3002\u201d\u5979\u60b2\u4f24\u5730\u8bf4\u3002"
    )

    result = await AgentSkillValidator().validate_output(packet, generated_text=text)

    assert result["passed"] is False
    validator = result["validators"]["scene_evidence"]
    assert validator["status"] == "ok"
    assert validator["repair_hooks"] == ["prose_repair", "emotion_to_scene_evidence"]
    metrics = {item["metric"] for item in result["failures"]}
    assert "emotion_label_count" in metrics
    assert "standalone_abstract_claims" in metrics
    assert "trait_statement_count" in metrics
    assert "body_language_cliche_count" in metrics
    assert "dialogue_emotion_tag_count" in metrics


@pytest.mark.asyncio
async def test_scene_evidence_validator_accepts_observable_evidence():
    from app.services.agent_skill_validator import AgentSkillValidator

    packet = CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=["show_dont_tell"],
        validators=["scene_evidence"],
        validation_contracts={
            "scene_evidence": {
                "show_dont_tell": {
                    "emotion_label_replacement_required": True,
                    "emotion_label_count_max": 0,
                    "thought_verb_count_max_per_500": 2,
                    "max_standalone_abstract_claims": 0,
                    "trait_statement_count_max": 0,
                    "body_language_cliche_count_max": 0,
                    "dialogue_tag_emotion_label_forbidden": True,
                    "min_senses_per_scene": 2,
                }
            }
        },
    )
    text = (
        "\u4ed6\u628a\u676f\u5b50\u653e\u56de\u684c\u9762\uff0c\u676f\u5e95\u78d5\u51fa\u4e00\u58f0\u8106\u54cd\u3002"
        "\u8336\u6c34\u6e85\u5230\u5951\u4e66\u4e0a\uff0c\u6307\u8282\u8d34\u7740\u51b7\u74f7\uff0c\u4ed6\u6ca1\u6709\u64e6\u3002"
        "\u201c\u5ff5\u5b8c\u3002\u201d"
    )

    result = await AgentSkillValidator().validate_output(packet, generated_text=text)

    assert result["passed"] is True
    validator = result["validators"]["scene_evidence"]
    assert validator["metrics"]["sense_category_count"] >= 2


@pytest.mark.asyncio
async def test_specificity_budget_validator_flags_missing_detail_anchors():
    from app.services.agent_skill_validator import AgentSkillValidator

    packet = CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=["specific_detail_anchor"],
        validators=["specificity_budget"],
        validation_contracts={
            "specificity_budget": {
                "specific_detail_anchor": {
                    "anchor_per_element_min": 1,
                    "sensory_modes_min": 2,
                    "abstract_evidence_pairing_required": True,
                    "anchor_must_carry_information": True,
                    "retry_policy": {"action": "detail_anchor_repair"},
                }
            }
        },
        repair_hooks={"specificity_budget": ["detail_anchor_repair"]},
    )
    text = (
        "\u8fd9\u662f\u4e00\u4e2a\u7834\u65e7\u7684\u623f\u95f4\u3002"
        "\u684c\u5b50\u5f88\u7279\u6b8a\u3002"
        "\u6c14\u6c1b\u975e\u5e38\u538b\u6291\u3002"
    )

    result = await AgentSkillValidator().validate_output(
        packet,
        generated_text=text,
        context={"scene_elements": ["\u623f\u95f4", "\u684c\u5b50"]},
    )

    assert result["passed"] is False
    validator = result["validators"]["specificity_budget"]
    assert validator["status"] == "ok"
    assert validator["repair_hooks"] == ["detail_anchor_repair"]
    metrics = {item["metric"] for item in result["failures"]}
    assert "anchor_coverage" in metrics
    assert "sensory_mode_count" in metrics
    assert "abstract_bare_count" in metrics


@pytest.mark.asyncio
async def test_specificity_budget_validator_accepts_anchored_details():
    from app.services.agent_skill_validator import AgentSkillValidator

    packet = CompiledSkillPacket(
        agent_name="core_generation",
        active_skills=["specific_detail_anchor"],
        validators=["specificity_budget"],
        validation_contracts={
            "specificity_budget": {
                "specific_detail_anchor": {
                    "anchor_per_element_min": 1,
                    "sensory_modes_min": 2,
                    "abstract_evidence_pairing_required": True,
                    "anchor_must_carry_information": True,
                }
            }
        },
    )
    text = (
        "\u623f\u95f4\u7684\u5899\u76ae\u88c2\u5f00\u4e00\u9053\u7070\u767d\u7684\u7eb9\uff0c"
        "\u96e8\u6c34\u987a\u7740\u7a97\u68c2\u6ef4\u5230\u5730\u4e0a\uff0c\u54d2\u55d2\u54cd\u4e86\u4e24\u58f0\u3002"
        "\u684c\u5b50\u8fb9\u7f18\u88ab\u78e8\u5f97\u53d1\u4eae\uff0c"
        "\u5979\u7684\u6307\u5c16\u6309\u5728\u90a3\u5708\u51b7\u786c\u7684\u8336\u6e0d\u4e0a\uff0c\u6ca1\u6709\u64e6\u3002"
    )

    result = await AgentSkillValidator().validate_output(
        packet,
        generated_text=text,
        context={"scene_elements": ["\u623f\u95f4", "\u684c\u5b50"]},
    )

    assert result["passed"] is True
    validator = result["validators"]["specificity_budget"]
    assert validator["status"] == "ok"
    assert validator["metrics"]["element_anchor_coverage"] == 1.0
    assert validator["metrics"]["sensory_mode_count"] >= 2
