"""主编系统 V2 重构测试。

覆盖 Phase 0-13 的核心逻辑。
"""
import pytest
import tempfile
import os


# ---- Phase 0: EditorTraceCollector ----

class TestEditorTraceCollector:
    def test_start_trace(self):
        from app.services.editor_trace_collector import EditorTraceCollector
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = EditorTraceCollector(storage_dir=tmpdir)
            trace = collector.start_trace("proj_1", chapter_number=1)
            assert trace.trace_id.startswith("etr_")
            assert trace.project_id == "proj_1"
            assert trace.chapter_number == 1

    def test_record_layer(self):
        from app.services.editor_trace_collector import EditorTraceCollector
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = EditorTraceCollector(storage_dir=tmpdir)
            trace = collector.start_trace("proj_1", chapter_number=1)
            collector.record_layer(trace.trace_id, "editor_planning", "ok", duration_ms=100)
            collector.record_layer(trace.trace_id, "quality_gate", "failed", duration_ms=50, error="violation")
            assert len(trace.layers) == 2
            assert trace.layers[0].layer == "editor_planning"
            assert trace.layers[1].status == "failed"

    def test_finish_trace(self):
        from app.services.editor_trace_collector import EditorTraceCollector
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = EditorTraceCollector(storage_dir=tmpdir)
            trace = collector.start_trace("proj_1", chapter_number=1)
            collector.finish_trace(trace.trace_id, "completed")
            assert trace.final_status == "completed"

    def test_query_traces(self):
        from app.services.editor_trace_collector import EditorTraceCollector
        from app.models.editor_trace import EditorTraceQuery
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = EditorTraceCollector(storage_dir=tmpdir)
            collector.start_trace("proj_1", chapter_number=1)
            collector.start_trace("proj_1", chapter_number=2)
            collector.start_trace("proj_2", chapter_number=1)
            results = collector.query_traces(EditorTraceQuery(project_id="proj_1"))
            assert len(results) == 2

    def test_project_summary(self):
        from app.services.editor_trace_collector import EditorTraceCollector
        with tempfile.TemporaryDirectory() as tmpdir:
            collector = EditorTraceCollector(storage_dir=tmpdir)
            collector.start_trace("proj_1", chapter_number=1)
            summary = collector.get_project_summary("proj_1")
            assert summary["project_id"] == "proj_1"
            assert summary["trace_count"] == 1


# ---- Phase 2: ContractItem ----

class TestContractItem:
    def test_contract_item_creation(self):
        from app.models.contract_item import ContractItem
        item = ContractItem(
            text="张三进入房间",
            source="outline",
            layer="plot",
            obligation_type="current_scene_must",
            priority="high",
            visibility="writer_visible",
        )
        assert item.is_writer_visible() is True
        assert item.is_blocking() is True
        assert item.is_allocatable() is True

    def test_contract_item_not_writer_visible(self):
        from app.models.contract_item import ContractItem
        item = ContractItem(
            text="方法论规则",
            source="methodology",
            obligation_type="advisory",
            visibility="quality_only",
        )
        assert item.is_writer_visible() is False
        assert item.is_allocatable() is False

    def test_contract_item_not_blocking(self):
        from app.models.contract_item import ContractItem
        item = ContractItem(
            text="建议加入爽点",
            source="benchmark",
            obligation_type="advisory",
            priority="low",
        )
        assert item.is_blocking() is False

    def test_compiled_scene_contract_budget_check(self):
        from app.models.contract_item import CompiledSceneContract, ContractItem, InformationBudget
        contract = CompiledSceneContract(
            scene_id="scene_0",
            current_scene_must=[ContractItem(text=f"must_{i}", obligation_type="current_scene_must") for i in range(5)],
            information_budget=InformationBudget(max_current_scene_must=3),
        )
        check = contract.budget_check()
        assert check["must_over_budget"] is True
        assert check["must_count"] == 5


class TestContractItemNormalizer:
    def test_normalize_chapter_plan(self):
        from app.services.contract_item_normalizer import ContractItemNormalizer
        normalizer = ContractItemNormalizer()
        items = normalizer.normalize_chapter_plan({
            "target_emotion": "紧张",
            "chapter_function": "转折",
            "must_progress": ["主角发现真相"],
            "forbidden": ["不能让反派知道"],
        })
        assert len(items) >= 3
        emotions = [i for i in items if i.layer == "emotion"]
        assert len(emotions) == 1
        assert emotions[0].text == "紧张"

    def test_normalize_scene_contract(self):
        from app.services.contract_item_normalizer import ContractItemNormalizer
        normalizer = ContractItemNormalizer()
        items = normalizer.normalize_scene_contract({
            "must_show": ["张三进入", "李四离开", "王五说话", "赵六出现"],
            "forbidden": ["不能让主角知道"],
            "ending_state": "悬念结尾",
            "pov_character": "张三",
            "scene_index": "0",
        })
        must_items = [i for i in items if i.obligation_type == "current_scene_must"]
        assert len(must_items) >= 3
        forbidden_items = [i for i in items if i.obligation_type == "forbidden"]
        assert len(forbidden_items) >= 1

    def test_normalize_all(self):
        from app.services.contract_item_normalizer import ContractItemNormalizer
        normalizer = ContractItemNormalizer()
        items = normalizer.normalize_all(
            chapter_plan={"target_emotion": "紧张"},
            scene_contracts=[{"must_show": ["张三进入"]}],
            story_state_summary={"active_characters": ["张三"]},
        )
        assert len(items) >= 3


# ---- Phase 4: SceneAllocationCompiler ----

class TestSceneAllocationCompiler:
    def test_compile_basic(self):
        from app.services.scene_allocation_compiler import SceneAllocationCompiler
        from app.models.contract_item import ContractItem
        compiler = SceneAllocationCompiler()
        items = [
            ContractItem(text="必须出现1", obligation_type="current_scene_must", priority="high"),
            ContractItem(text="必须出现2", obligation_type="current_scene_must", priority="high"),
            ContractItem(text="禁止1", obligation_type="forbidden", priority="high"),
            ContractItem(text="硬事实1", obligation_type="hard_fact", priority="medium"),
        ]
        plan = compiler.compile("ch_1", items, scene_count=2)
        assert len(plan.scenes) == 2
        # forbidden 应分配到所有场景
        for scene in plan.scenes:
            assert len(scene.forbidden_items) == 1

    def test_compile_to_contracts(self):
        from app.services.scene_allocation_compiler import SceneAllocationCompiler
        from app.models.contract_item import ContractItem
        compiler = SceneAllocationCompiler()
        items = [
            ContractItem(text="必须出现", obligation_type="current_scene_must"),
        ]
        plan = compiler.compile("ch_1", items, scene_count=2)
        contracts = compiler.compile_to_contracts(plan)
        assert len(contracts) == 2

    def test_scene_scoped_items_stay_in_original_scene(self):
        from app.services.contract_item_normalizer import ContractItemNormalizer
        from app.services.scene_allocation_compiler import SceneAllocationCompiler

        normalizer = ContractItemNormalizer()
        compiler = SceneAllocationCompiler()
        items = normalizer.normalize_all(scene_contracts=[
            {
                "scene_index": 0,
                "pov_character": "角色A",
                "must_show": ["A场必须展示"],
                "forbidden": ["A场禁止"],
                "ending_state": "A场结尾",
            },
            {
                "scene_index": 1,
                "pov_character": "角色B",
                "must_show": ["B场必须展示"],
                "forbidden": ["B场禁止"],
                "ending_state": "B场结尾",
            },
        ])

        plan = compiler.compile("ch_1", items, scene_count=2)
        contracts = compiler.compile_to_contracts(plan)

        scene0_text = "\n".join(
            [i.text for i in contracts[0].hard_facts]
            + [i.text for i in contracts[0].current_scene_must]
            + [i.text for i in contracts[0].forbidden]
        )
        scene1_text = "\n".join(
            [i.text for i in contracts[1].hard_facts]
            + [i.text for i in contracts[1].current_scene_must]
            + [i.text for i in contracts[1].forbidden]
        )

        assert "角色A" in scene0_text
        assert "A场必须展示" in scene0_text
        assert "A场结尾" in scene0_text
        assert "A场禁止" in scene0_text
        assert "角色B" not in scene0_text
        assert "B场必须展示" not in scene0_text

        assert "角色B" in scene1_text
        assert "B场必须展示" in scene1_text
        assert "B场结尾" in scene1_text
        assert "B场禁止" in scene1_text
        assert "角色A" not in scene1_text
        assert "A场必须展示" not in scene1_text


# ---- Phase 5: InformationBudgetCompiler ----

class TestInformationBudgetCompiler:
    def test_compile_within_budget(self):
        from app.services.information_budget_compiler import InformationBudgetCompiler
        from app.models.contract_item import CompiledSceneContract, ContractItem, InformationBudget
        compiler = InformationBudgetCompiler()
        contract = CompiledSceneContract(
            scene_id="scene_0",
            current_scene_must=[ContractItem(text=f"must_{i}", obligation_type="current_scene_must") for i in range(2)],
            soft_hints=[ContractItem(text=f"soft_{i}", obligation_type="current_scene_soft_hint") for i in range(1)],
            information_budget=InformationBudget(max_current_scene_must=3, max_soft_hints=2),
        )
        result = compiler.compile(contract, mode="commercial")
        assert len(result.current_scene_must) <= 3
        assert len(result.soft_hints) <= 2

    def test_compile_over_budget_degrades(self):
        from app.services.information_budget_compiler import InformationBudgetCompiler
        from app.models.contract_item import CompiledSceneContract, ContractItem, InformationBudget
        compiler = InformationBudgetCompiler()
        contract = CompiledSceneContract(
            scene_id="scene_0",
            current_scene_must=[ContractItem(text=f"must_{i}", obligation_type="current_scene_must", priority="medium") for i in range(5)],
            information_budget=InformationBudget(max_current_scene_must=3),
        )
        result = compiler.compile(contract, mode="commercial")
        assert len(result.current_scene_must) <= 3
        # 降级的 item 应进入 carry_forward
        assert len(result.hidden_carry_forward) > 0

    def test_compile_scene_contract_budget_is_idempotent(self):
        from app.services.information_budget_compiler import InformationBudgetCompiler

        compiler = InformationBudgetCompiler()
        contract = {"must_show": [f"beat_{i}" for i in range(10)]}

        first = compiler.apply_to_scene_contract(contract)
        second = compiler.apply_to_scene_contract(first)

        assert second["must_show"] == [f"beat_{i}" for i in range(5)]
        assert second["soft_guidance"] == ["beat_5", "beat_6", "beat_7"]
        assert second["deferred_items"] == ["beat_8", "beat_9"]
        assert second["information_budget"]["original_must_show"] == [f"beat_{i}" for i in range(10)]
        assert len(second["compiler_warnings"]) == 1

    def test_compile_scene_contract_budget_uses_dynamic_scene_profile(self):
        from app.services.information_budget_compiler import InformationBudgetCompiler

        compiler = InformationBudgetCompiler()
        lean = compiler.apply_to_scene_contract({
            "scene_function": "过渡余波",
            "target_chars": 2200,
            "must_show": [f"lean_{i}" for i in range(7)],
        })
        intense = compiler.apply_to_scene_contract({
            "scene_function": "高潮揭示",
            "target_chars": 5000,
            "must_show": [f"intense_{i}" for i in range(8)],
        })

        assert len(lean["hard_must_show"]) == 4
        assert len(lean["soft_guidance"]) == 2
        assert lean["deferred_items"] == ["lean_6"]
        assert lean["information_budget"]["budget_profile"] == "lean"
        assert len(intense["hard_must_show"]) == 6
        assert len(intense["soft_guidance"]) == 2
        assert intense["deferred_items"] == []
        assert intense["information_budget"]["budget_profile"] == "high_intensity"


class TestEditorPlanningInformationBudget:
    @pytest.mark.asyncio
    async def test_propose_scene_contracts_applies_budget_before_state_write(self):
        from app.agents.editor_in_chief import EditorInChiefAgent, EditorState

        agent = EditorInChiefAgent()
        state = EditorState()
        result = await agent._tool_propose_scene_contracts(
            {
                "chapter_number": 8,
                "chapter_rhythm_map": "S1 快进，S2 收束。",
                "scene_contracts": [
                    {
                        "scene_id": "c8-s1",
                        "pov_character": "Lin",
                        "goal": "Find the sealed file",
                        "conflict": "The archive keeps changing",
                        "must_show": [f"beat_{i}" for i in range(10)],
                        "forbidden": ["reveal final culprit"],
                        "ending_state": "Lin leaves with only a partial clue",
                    }
                ],
            },
            state,
        )

        contract = result["scene_contracts"][0]
        assert contract["must_show"] == [f"beat_{i}" for i in range(5)]
        assert contract["hard_must_show"] == [f"beat_{i}" for i in range(5)]
        assert contract["soft_guidance"] == ["beat_5", "beat_6", "beat_7"]
        assert contract["deferred_items"] == ["beat_8", "beat_9"]
        assert result["information_budget_summary"][0]["over_budget"] is True
        assert result["_scene_contracts"][0]["information_budget"]["original_must_show"] == [f"beat_{i}" for i in range(10)]


# ---- Phase 6: WriterInputCompiler ----

class TestWriterInputCompiler:
    def test_compile_basic(self):
        from app.services.writer_input_compiler import WriterInputCompiler
        from app.models.contract_item import CompiledSceneContract, ContractItem, InformationBudget
        compiler = WriterInputCompiler()
        contract = CompiledSceneContract(
            scene_id="scene_0",
            scene_function="转折",
            target_emotion="紧张",
            current_scene_must=[ContractItem(text="张三发现真相", obligation_type="current_scene_must", visibility="writer_visible")],
            forbidden=[ContractItem(text="不能让反派知道", obligation_type="forbidden", visibility="writer_visible")],
            information_budget=InformationBudget(target_chars=3000),
        )
        packet = compiler.compile(contract, pov_character="张三")
        assert packet.scene_task != ""
        assert len(packet.must_include) == 1
        assert len(packet.must_avoid) == 1

    def test_compile_to_prompt(self):
        from app.services.writer_input_compiler import WriterInputCompiler
        from app.models.contract_item import WriterInputPacket
        compiler = WriterInputCompiler()
        packet = WriterInputPacket(
            scene_task="写一个约3000字的场景。",
            must_include=["张三发现真相"],
            must_avoid=["不能让反派知道"],
        )
        prompt = compiler.compile_to_prompt(packet)
        assert "张三发现真相" in prompt
        assert "不能让反派知道" in prompt

    def test_compile_enriches_from_existing_scene_sources(self):
        from app.services.writer_input_compiler import WriterInputCompiler
        from app.models.contract_item import CompiledSceneContract, ContractItem

        compiler = WriterInputCompiler()
        contract = CompiledSceneContract(
            scene_id="scene_0",
            scene_function="冲突升级",
            target_emotion="压迫",
            current_scene_must=[
                ContractItem(
                    text="角色在压力下作出选择",
                    obligation_type="current_scene_must",
                    visibility="writer_visible",
                )
            ],
            ending_state="门外传来第二声敲击",
        )
        source_scene_contract = {
            "quality_extensions": {
                "anti_ai_guidance": ["减少解释性总结，用动作和对话承接信息。"],
                "character_voice_guidance": {"角色A": ["短句，先判断后发问"]},
                "reveal_control": {
                    "preferred_carriers": ["物件细节"],
                    "forbidden_future_concepts": ["幕后身份"],
                },
            },
            "scene_provenance": {
                "current_facts": {
                    "character_states": {"角色A": "受伤但保持清醒"},
                    "completed_events": ["门已被锁上"],
                },
                "clues": ["桌面缺口"],
            },
            "commercial_pacing_contract": {
                "pressure_ramp": "持续升高",
                "hook_out": "门外传来第二声敲击",
            },
        }

        packet = compiler.compile(
            contract,
            pov_character="角色A",
            source_scene_contract=source_scene_contract,
        )

        assert "anti_ai_prose" in packet.active_capabilities
        assert "dialogue_voice" in packet.active_capabilities
        assert "foreshadowing_control" in packet.active_capabilities
        assert "action_continuity" in packet.active_capabilities
        assert packet.source_trace["anti_ai_prose"]
        assert packet.writer_input_enrichment["anti_ai_prose"]["guidance"]

        prompt = compiler.compile_to_prompt(packet)
        assert "anti_ai_prose" in prompt
        assert "减少解释性总结" in prompt
        assert "短句，先判断后发问" in prompt


# ---- Phase 7: QualityFindingHub ----

class TestQualityFindingHub:
    def test_normalize_violation(self):
        from app.services.quality_finding_hub import QualityFindingHub
        hub = QualityFindingHub()
        finding = hub.normalize_violation({
            "source": "consistency",
            "type": "fact_conflict",
            "severity": "high",
            "detail": "角色名字不一致",
        })
        assert finding.severity == "S2"
        assert finding.repair_lane == "fbi_fact"
        assert finding.repair_scope == "prose_text"

    def test_normalize_deslop_finding(self):
        from app.services.quality_finding_hub import QualityFindingHub
        hub = QualityFindingHub()
        finding = hub.normalize_deslop_finding({
            "source_checker": "deslop_gate_A",
            "severity": "S3",
            "issue": "解释腔",
        })
        assert finding.repair_lane == "fbi_prose"
        assert finding.source == "deslop_gate"

    def test_normalize_all_deduplicates(self):
        from app.services.quality_finding_hub import QualityFindingHub
        hub = QualityFindingHub()
        findings = hub.normalize_all(
            violations=[
                {"source": "consistency", "type": "fact_conflict", "severity": "high", "detail": "冲突1"},
                {"source": "consistency", "type": "fact_conflict", "severity": "low", "detail": "冲突2"},
            ]
        )
        # 同 type + 同 scope 应去重，保留高 severity
        fact_conflicts = [f for f in findings if f.type == "fact_conflict"]
        assert len(fact_conflicts) == 1
        assert fact_conflicts[0].severity == "S2"

    def test_maps_real_checker_types_to_actionable_lanes(self):
        from app.services.quality_finding_hub import QualityFindingHub

        hub = QualityFindingHub()
        findings = hub.normalize_all(violations=[
            {
                "source": "deterministic",
                "type": "must_show_overload",
                "severity": "medium",
                "detail": "must_show 过多",
                "suggested_strategy": "contract_budget_adjust",
                "scope": "scene_contract",
                "repairable_by_text": False,
                "repairable_by_contract": True,
            },
            {
                "source": "scene_credibility",
                "type": "knowledge_boundary_violation",
                "severity": "high",
                "detail": "角色知道了不该知道的信息",
            },
            {
                "source": "scene_credibility",
                "type": "narration_explanation_artifact",
                "severity": "medium",
                "detail": "解释腔",
            },
            {
                "source": "scene_credibility",
                "type": "plausibility_break",
                "severity": "high",
                "detail": "行为不可信",
            },
        ])

        by_type = {f.type: f for f in findings}
        assert by_type["must_show_overload"].repair_scope == "scene_contract"
        assert by_type["must_show_overload"].repair_lane == "deterministic"
        assert by_type["knowledge_boundary_violation"].repair_lane == "fbi_fact"
        assert by_type["narration_explanation_artifact"].repair_lane == "fbi_prose"
        assert by_type["plausibility_break"].repair_lane == "fbi_scene_restructure"

    def test_explanatory_punctuation_uses_deterministic_lane(self):
        from app.services.quality_finding_hub import QualityFindingHub

        hub = QualityFindingHub()
        findings = hub.normalize_all(violations=[{
            "source": "scene_credibility",
            "type": "explanatory_punctuation_artifact",
            "severity": "high",
            "detail": "破折号制造解释腔",
            "target_span": "——",
        }])

        assert findings[0].repair_lane == "deterministic"
        assert findings[0].is_actionable() is True

    def test_validator_failure_does_not_enter_text_repair_lane(self):
        from app.services.quality_finding_hub import QualityFindingHub

        hub = QualityFindingHub()
        findings = hub.normalize_all(violations=[
            {
                "source": "consistency",
                "type": "consistency_check_unavailable",
                "severity": "medium",
                "detail": "一致性审校返回无法解析",
                "suggested_strategy": "validator_retry",
                "scope": "validator_system",
                "repairable_by_text": False,
                "repairable_by_contract": False,
            }
        ])

        assert len(findings) == 1
        finding = findings[0]
        assert finding.repair_scope == "validator_system"
        assert finding.repair_lane == "none"
        assert finding.is_actionable() is False


# ---- Phase 8: RepairOrderCompiler ----

class TestRepairOrderCompiler:
    def test_compile_finding(self):
        from app.services.repair_order_compiler import RepairOrderCompiler
        from app.models.review_finding_v2 import ReviewFindingV2
        compiler = RepairOrderCompiler()
        finding = ReviewFindingV2(
            id="f1",
            source="consistency",
            type="fact_conflict",
            description="角色名字不一致",
            severity="S2",
            repair_scope="prose_text",
            repair_lane="fbi_fact",
        )
        order = compiler.compile(finding)
        assert order.lane == "fbi_fact"
        assert order.operation == "replace_span"
        assert order.id.startswith("ro_")

    def test_compile_batch(self):
        from app.services.repair_order_compiler import RepairOrderCompiler
        from app.models.review_finding_v2 import ReviewFindingV2
        compiler = RepairOrderCompiler()
        findings = [
            ReviewFindingV2(id="f1", repair_lane="fbi_fact", repair_scope="prose_text", description="问题1"),
            ReviewFindingV2(id="f2", repair_lane="deterministic", repair_scope="prose_text", description="问题2"),
        ]
        orders = compiler.compile_batch(findings)
        assert len(orders) == 2

    def test_sort_orders_deterministic_first(self):
        from app.services.repair_order_compiler import RepairOrderCompiler
        from app.models.repair_order import RepairOrder
        compiler = RepairOrderCompiler()
        orders = [
            RepairOrder(id="1", lane="fbi_prose"),
            RepairOrder(id="2", lane="deterministic"),
            RepairOrder(id="3", lane="fbi_fact"),
        ]
        sorted_orders = compiler.sort_orders(orders)
        assert sorted_orders[0].lane == "deterministic"


# ---- Phase 8: DeterministicRepairEngine ----

class TestDeterministicRepairEngine:
    @pytest.mark.asyncio
    async def test_compress_dashes(self):
        from app.services.deterministic_repair_engine import DeterministicRepairEngine
        from app.models.repair_order import RepairOrder
        engine = DeterministicRepairEngine()
        order = RepairOrder(id="1", lane="deterministic", operation="compress_region")
        result = await engine.execute(order, "他走了——————走了。")
        assert result["changed"] is True
        assert "————" not in result["text"]

    @pytest.mark.asyncio
    async def test_softens_explanatory_dash(self):
        from app.services.deterministic_repair_engine import DeterministicRepairEngine
        from app.models.repair_order import RepairOrder

        engine = DeterministicRepairEngine()
        order = RepairOrder(id="1", lane="deterministic", operation="compress_region")
        result = await engine.execute(order, "她停下——不是害怕，而是听见了脚步声。")

        assert result["changed"] is True
        assert "她停下，不是害怕" in result["text"]

    @pytest.mark.asyncio
    async def test_reduces_excessive_emdash_density(self):
        from app.services.deterministic_repair_engine import DeterministicRepairEngine
        from app.models.repair_order import RepairOrder

        text = (
            "她反复回放——线索一，线索二。\n\n"
            "她竖耳细听——门外没有动静。\n\n"
            "她轻轻一按——木板松动。\n\n"
            "她小心抽出来——是一张纸。\n\n"
            "纸上写着——有人背叛。\n\n"
            "她意识到——现在不能退。"
        )
        engine = DeterministicRepairEngine()
        order = RepairOrder(id="1", lane="deterministic", operation="compress_region")

        result = await engine.execute(order, text)

        assert result["changed"] is True
        assert result["text"].count("——") <= 2
        assert any(p.get("operation") == "reduce_emdash_density" for p in result["patches"])

    @pytest.mark.asyncio
    async def test_acknowledge(self):
        from app.services.deterministic_repair_engine import DeterministicRepairEngine
        from app.models.repair_order import RepairOrder
        engine = DeterministicRepairEngine()
        order = RepairOrder(id="1", lane="deterministic", operation="acknowledge")
        result = await engine.execute(order, "some text")
        assert result.get("acknowledged") is True

    @pytest.mark.asyncio
    async def test_scene_too_long_compresses_to_hard_limit(self):
        from app.services.deterministic_repair_engine import DeterministicRepairEngine
        from app.models.repair_order import RepairOrder

        text = "\n\n".join([
            "# 第一章",
            "她醒了。",
            "这意味着她必须重新理解眼前的一切，因此她意识到事情并不简单。" * 8,
            "她拿起密信，看见符文和残香，确认这是一条关键线索。" * 4,
            "她走到门边，听见脚步声停在门外。",
        ])
        engine = DeterministicRepairEngine()
        order = RepairOrder(
            id="1",
            lane="deterministic",
            operation="compress_region",
            instruction=f"场景字数{len(text)}超出硬上限180",
        )

        result = await engine.execute(order, text)

        assert result["changed"] is True
        assert len(result["text"]) <= 180
        assert "# 第一章" in result["text"]
        assert "她走到门边" in result["text"]


# ---- Phase 10: LLMGateway ----

class TestLLMGateway:
    def test_extract_json(self):
        from app.services.llm_gateway import LLMGateway
        gateway = LLMGateway()
        # 直接 JSON
        assert gateway._extract_json('{"key": "value"}') == '{"key": "value"}'
        # Markdown code block
        result = gateway._extract_json('```json\n{"key": "value"}\n```')
        assert result is not None
        assert '"key"' in result
        # 混合文本
        result = gateway._extract_json('Here is the result: {"status": "ok"} end')
        assert result is not None

    def test_classify_error(self):
        from app.services.llm_gateway import LLMGateway
        gateway = LLMGateway()
        assert gateway._classify_error("Connection timeout") == "provider_timeout"
        assert gateway._classify_error("Content filter triggered") == "content_filtered"
        assert gateway._classify_error("Unknown issue") == "unknown_error"


class TestSceneRecoveryV2ContractAdjustment:
    def test_apply_v2_contract_adjustments_moves_overflow_to_nice_to_have(self):
        from app.models.review_finding_v2 import ReviewFindingV2
        from app.services.scene_recovery_controller import SceneRecoveryController

        controller = SceneRecoveryController()
        context = {
            "scene_contract": {
                "must_show": ["a", "b", "c", "d", "e", "f", "g"],
                "nice_to_have": ["old"],
            }
        }
        updates = controller._apply_v2_contract_adjustments(context, [
            ReviewFindingV2(
                id="f1",
                type="must_show_overload",
                repair_scope="scene_contract",
                repair_lane="deterministic",
            )
        ])

        assert updates["scene_contract"]["must_show"] == ["a", "b", "c", "d", "e"]
        assert updates["scene_contract"]["nice_to_have"] == ["old", "f", "g"]


class TestFBIV2ClosedLoop:
    def test_internal_conflict_routes_to_fact_lane(self):
        from app.services.quality_finding_hub import QualityFindingHub

        finding = QualityFindingHub().normalize_violation({
            "source": "consistency",
            "type": "internal_conflict",
            "severity": "high",
            "detail": "same event is described with conflicting responsibility",
        })

        assert finding.repair_scope == "prose_text"
        assert finding.repair_lane == "fbi_fact"
        assert finding.is_actionable() is True

    def test_pov_conflict_routes_to_scene_restructure_lane(self):
        from app.services.quality_finding_hub import QualityFindingHub

        finding = QualityFindingHub().normalize_violation({
            "source": "consistency",
            "type": "pov_conflict",
            "severity": "critical",
            "detail": "limited POV suddenly shows offscreen dialogue",
        })

        assert finding.repair_scope == "prose_text"
        assert finding.repair_lane == "fbi_scene_restructure"
        assert finding.is_actionable() is True

    def test_forbidden_triggered_compiles_to_delete_order(self):
        from app.services.quality_finding_hub import QualityFindingHub
        from app.services.repair_order_compiler import RepairOrderCompiler

        finding = QualityFindingHub().normalize_violation({
            "source": "narrative_contract",
            "type": "forbidden_triggered",
            "severity": "high",
            "detail": "forbidden text appeared",
            "target_span": "DO_NOT_SHOW",
            "scope": "prose_text",
        })
        order = RepairOrderCompiler().compile(finding)

        assert order.lane == "deterministic"
        assert order.operation == "delete_span"
        assert order.target_region == {"text": "DO_NOT_SHOW"}

    def test_scene_recovery_prefers_fbi_without_one_shot_cap(self):
        from app.services.scene_recovery_controller import SceneRecoveryController

        controller = SceneRecoveryController()
        controller._context = {"generation_features": {"fbi_repair_mode": "assist"}}
        controller._fbi_repair_count = 3

        strategy = controller._decide_strategy({
            "violations": [{
                "type": "internal_conflict",
                "severity": "high",
                "blocks_commit": True,
                "suggested_strategy": "manual_review",
            }]
        })

        assert strategy == "fbi_repair"

    def test_scene_recovery_keeps_validator_failures_out_of_fbi(self):
        from app.services.scene_recovery_controller import SceneRecoveryController

        controller = SceneRecoveryController()
        controller._context = {"generation_features": {"fbi_repair_mode": "assist"}}

        strategy = controller._decide_strategy({
            "violations": [{
                "type": "consistency_check_unavailable",
                "severity": "medium",
                "blocks_commit": True,
                "suggested_strategy": "validator_retry",
            }]
        })

        assert strategy == "validator_retry"

    @pytest.mark.asyncio
    async def test_fbi_v2_resolved_after_recheck_freezes_issue(self, monkeypatch):
        from app.models.review_finding_v2 import ReviewFindingV2
        from app.services.fbi_v2.orchestrator import FBIV2Orchestrator

        orchestrator = FBIV2Orchestrator()
        finding = ReviewFindingV2(
            id="f1",
            source="deterministic",
            type="scene_too_long",
            severity="S2",
            blocks_commit=True,
            repair_scope="prose_text",
            repair_lane="deterministic",
            description="too long hard_max_chars 12",
            validator="deterministic",
        )

        async def fake_execute_round(state, orders, context):
            state.current_text = "short text"
            orders[0].mark_succeeded([{"operation": "compress_region"}])
            state.orders.extend(orders)
            return {
                "round": state.round_num,
                "orders_total": 1,
                "orders_succeeded": 1,
                "orders_failed": 0,
                "orders_skipped": 0,
                "patches": [{"operation": "compress_region"}],
                "changed": True,
            }

        async def fake_recheck(*, text, context, round_num):
            return {"round": round_num, "passed": True, "violations": [], "findings": [], "report": {"passed": True, "violations": []}}

        monkeypatch.setattr(orchestrator, "_execute_round", fake_execute_round)
        monkeypatch.setattr(orchestrator, "_run_recheck", fake_recheck)

        outcome = await orchestrator.run(
            project_id="p1",
            candidate_text="very very very long text",
            findings=[finding],
            context={},
            max_rounds=3,
        )

        assert outcome["status"] == "resolved"
        assert outcome["summary"]["rounds_used"] == 1
        assert outcome["summary"]["succeeded"] == 1
        assert outcome["resolved_finding_ids"] == ["f1"]

    @pytest.mark.asyncio
    async def test_fbi_v2_needs_workbench_after_unresolved_rounds(self, monkeypatch):
        from app.models.review_finding_v2 import ReviewFindingV2
        from app.services.fbi_v2.orchestrator import FBIV2Orchestrator

        orchestrator = FBIV2Orchestrator()
        finding = ReviewFindingV2(
            id="f1",
            source="consistency",
            type="internal_conflict",
            severity="S2",
            blocks_commit=True,
            repair_scope="prose_text",
            repair_lane="fbi_fact",
            description="fact still conflicts",
            validator="consistency",
        )

        async def fake_execute_round(state, orders, context):
            state.orders.extend(orders)
            for order in orders:
                order.mark_failed("no patch")
            return {
                "round": state.round_num,
                "orders_total": len(orders),
                "orders_succeeded": 0,
                "orders_failed": len(orders),
                "orders_skipped": 0,
                "patches": [],
                "changed": False,
            }

        async def fake_recheck(*, text, context, round_num):
            return {
                "round": round_num,
                "passed": False,
                "violations": [],
                "findings": [finding],
                "report": {"passed": False, "violations": []},
            }

        monkeypatch.setattr(orchestrator, "_execute_round", fake_execute_round)
        monkeypatch.setattr(orchestrator, "_run_recheck", fake_recheck)

        outcome = await orchestrator.run(
            project_id="p1",
            candidate_text="unchanged",
            findings=[finding],
            context={},
            max_rounds=3,
        )

        assert outcome["status"] == "needs_workbench"
        assert outcome["summary"]["rounds_used"] == 3
        assert "max rounds" in outcome["failure_reasons"][0]

    @pytest.mark.asyncio
    async def test_scene_recovery_cascades_new_blocking_finding_back_to_fbi(self, monkeypatch):
        from app.services.scene_recovery_controller import SceneRecoveryController
        import app.services.fbi_v2.orchestrator as orchestrator_module

        controller = SceneRecoveryController()
        controller.draft_length = 1000
        run_types = []

        class FakeOrchestrator:
            async def run(self, *, project_id, candidate_text, findings, context, max_rounds):
                run_types.append([f.type for f in findings])
                suffix = len(run_types)
                return {
                    "status": "resolved",
                    "text": f"{candidate_text} repaired{suffix}",
                    "patches": [{"operation": "replace_span", "round": suffix}],
                    "resolved_finding_ids": [f.id for f in findings],
                    "summary": {
                        "total_orders": len(findings),
                        "succeeded": len(findings),
                        "failed": 0,
                        "skipped": 0,
                        "rounds_used": 1,
                    },
                    "failure_reasons": [],
                }

        async def fake_evaluate(text, context, level="full"):
            if len(run_types) == 1:
                return {
                    "passed": False,
                    "commit_blocked": True,
                    "violations": [{
                        "violation_id": "pov1",
                        "source": "consistency",
                        "type": "pov_conflict",
                        "severity": "critical",
                        "detail": "limited POV suddenly shows offscreen dialogue",
                        "blocks_commit": True,
                        "scope": "prose_text",
                    }],
                }
            return {"passed": True, "commit_blocked": False, "violations": []}

        monkeypatch.setattr(orchestrator_module, "get_fbi_v2_orchestrator", lambda: FakeOrchestrator())
        monkeypatch.setattr(controller, "_evaluate_with_pre_gates", fake_evaluate)
        monkeypatch.setattr(controller, "_check_budget", lambda text=None: True)

        result = await controller._try_fbi_repair_v2_primary(
            text="draft",
            context={"project_id": "p1", "generation_features": {"fbi_repair_mode": "assist"}},
            report={
                "passed": False,
                "commit_blocked": True,
                "violations": [{
                    "violation_id": "f1",
                    "source": "consistency",
                    "type": "internal_conflict",
                    "severity": "high",
                    "detail": "fact conflict",
                    "blocks_commit": True,
                    "scope": "prose_text",
                }],
            },
            text_hash="abc",
        )

        assert result["v2_status"] == "v2_resolved"
        assert run_types == [["internal_conflict"], ["pov_conflict"]]
        assert result["fbi_v2_summary"]["cascade_rounds"] == 2
