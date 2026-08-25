import pytest
import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from app.api import editor_chat
from app.services.scene_recovery_controller import SceneRecoveryController, DEFAULT_BUDGET, STREAM_BUDGET
from app.services import scene_recovery_controller
from app.services.quality_gate import QualityGate
from app.services import scene_generation_pipeline as generation
from app.services.project_lock import ProjectLockManager
from app.models.violation import make_violation
from app.agents.scene_critic import SceneCriticAgent
from app.agents.scene_validator import SceneValidator


class TestSceneRecoveryController:
    def test_default_budget(self):
        ctrl = SceneRecoveryController()
        assert ctrl.max_patch == 2
        assert ctrl.max_rewrite == 2
        assert ctrl.timeout_seconds == 240

    def test_stream_budget(self):
        ctrl = SceneRecoveryController(budget=STREAM_BUDGET)
        assert ctrl.max_patch == 2
        assert ctrl.max_rewrite == 2
        assert ctrl.timeout_seconds == 300

    def test_decide_strategy_accept(self):
        ctrl = SceneRecoveryController()
        report = {"violations": [], "commit_blocked": False}
        assert ctrl._decide_strategy(report) == "accept"

    def test_decide_strategy_patch_text(self):
        ctrl = SceneRecoveryController()
        v = make_violation("forbidden_triggered", "critical", "test")
        report = {"violations": [v], "commit_blocked": True}
        assert ctrl._decide_strategy(report) == "patch_text"

    def test_decide_strategy_rewrite_for_multiple_blocking(self):
        ctrl = SceneRecoveryController()
        v1 = make_violation("forbidden_triggered", "critical", "test1")
        v2 = make_violation("forbidden_triggered", "critical", "test2")
        v3 = make_violation("forbidden_triggered", "critical", "test3")
        report = {"violations": [v1, v2, v3], "commit_blocked": True}
        assert ctrl._decide_strategy(report) == "rewrite_scene"

    def test_decide_strategy_rewrite_for_rewrite_suggested(self):
        ctrl = SceneRecoveryController()
        v = make_violation("canon_timeline_confusion", "critical", "test")
        report = {"violations": [v], "commit_blocked": True}
        assert ctrl._decide_strategy(report) == "rewrite_scene"

    def test_decide_strategy_prefers_patch_for_direct_timeline_replacement(self):
        ctrl = SceneRecoveryController()
        v = make_violation(
            "canon_timeline_confusion",
            "critical",
            "timeline",
            target_span="两日后",
            expected_behavior="正文应使用三日后。",
        )
        report = {"violations": [v], "commit_blocked": True}
        assert ctrl._decide_strategy(report) == "patch_text"

    def test_decide_strategy_repairs_contract_before_multiple_blocking_rewrite(self):
        ctrl = SceneRecoveryController()
        v1 = make_violation("missing_contract", "critical", "missing")
        v2 = make_violation("missing_contract", "critical", "still missing")
        report = {"violations": [v1, v2], "commit_blocked": True}
        assert ctrl._decide_strategy(report) == "repair_contract"

    def test_decide_strategy_exhausted_content_repair_enters_review(self):
        ctrl = SceneRecoveryController(budget={"max_patch": 0, "max_rewrite": 0})
        v = make_violation("forbidden_triggered", "critical", "test")
        report = {"violations": [v], "commit_blocked": True}
        assert ctrl._decide_strategy(report) == "exhausted"

    def test_decide_strategy_manual_review_remains_blocked(self):
        ctrl = SceneRecoveryController()
        v = make_violation(
            "fcip_registration_error", "critical", "registry mismatch",
            suggested_strategy="manual_review",
        )
        report = {"violations": [v], "commit_blocked": True}
        assert ctrl._decide_strategy(report) == "blocked"

    def test_blocking_violation_is_never_degraded_accepted(self):
        ctrl = SceneRecoveryController()
        blocking = make_violation("fact_conflict", "medium", "conflict", blocks_commit=True)
        result = ctrl._block(
            "exhaustion",
            current_text="candidate",
            final_report={"violations": [blocking]},
        )
        assert result["commit_blocked"] is True
        assert result["status"] == "waiting_human_content_review"

    def test_fbi_v2_candidate_accepts_manual_only_prose_blocking_issue(self):
        ctrl = SceneRecoveryController()

        class StubFinding:
            blocks_commit = True
            repair_scope = "prose_text"
            repair_lane = "manual_only"
            user_visible = True

            @staticmethod
            def is_actionable():
                return False

        assert ctrl._is_fbi_v2_candidate(StubFinding())

    @pytest.mark.asyncio
    async def test_pause_persists_resumable_review_snapshot(self, monkeypatch):
        update_step = AsyncMock()
        complete_execution = AsyncMock()
        monkeypatch.setattr(editor_chat, "_update_workflow_step", update_step)
        monkeypatch.setattr(editor_chat, "_complete_workflow_execution", complete_execution)

        await editor_chat._pause_for_human_review(
            execution_id="execution-1",
            gen_step="core_generation_1",
            check_step="consistency_check_1",
            error_detail="needs review",
            pipeline_output={"attempts": [], "final_violations": [{"type": "fact_conflict"}]},
            resume_state={"scene_index": 0, "candidate_text": "candidate", "review_version": 0},
            db=MagicMock(),
        )

        assert update_step.await_count == 2
        persisted = complete_execution.await_args.kwargs["result"]
        assert persisted["review"]["candidate_text"] == "candidate"
        assert persisted["review"]["review_version"] == 1
        assert persisted["resume_state"]["review_version"] == 1

    def test_review_violations_receive_stable_issue_ids_and_status(self):
        violations = [{"type": "missing_must_show", "detail": "missing clue", "blocks_commit": True}]

        first = editor_chat._prepare_review_violations(violations)
        second = editor_chat._prepare_review_violations(violations)

        assert first[0]["issue_id"] == second[0]["issue_id"]
        assert first[0]["review_status"] == "open"
        assert first[0]["chat"] == []

    def test_ignored_review_issue_is_removed_from_blocking_guidance(self):
        violations = [{
            "type": "missing_must_show",
            "detail": "missing clue",
            "blocks_commit": True,
            "review_status": "ignored",
        }]

        guidance = editor_chat._build_review_guidance(violations, [])

        assert guidance["blocking_count"] == 0

    def test_text_diff_marks_added_and_deleted_chinese_content(self):
        diff = editor_chat._build_text_diff("她推开房门。", "她轻轻推开窗户。")

        assert diff["added_chars"] > 0
        assert diff["deleted_chars"] > 0
        assert any(item["operation"] == "add" and "轻轻" in item["text"] for item in diff["segments"])
        assert any(item["operation"] == "delete" and "房门" in item["text"] for item in diff["segments"])

    def test_manual_review_violation_does_not_consume_contract_repair(self):
        ctrl = SceneRecoveryController()
        v = make_violation(
            "fcip_registration_error", "critical", "registry mismatch",
            suggested_strategy="manual_review",
        )
        report = {"violations": [v], "commit_blocked": True}
        assert ctrl._decide_strategy(report) == "blocked"

    def test_block_returns_correct_structure(self):
        ctrl = SceneRecoveryController()
        result = ctrl._block("exhaustion", final_report={"test": True})
        assert result["generated_text"] == ""
        assert result["commit_blocked"] is True
        assert result["status"] == "waiting_human_content_review"
        assert result["final_report"] == {"test": True}

    def test_accept_returns_correct_structure(self):
        ctrl = SceneRecoveryController()
        result = ctrl._accept("some text", {"passed": True}, recovery_mode="patched")
        assert result["generated_text"] == "some text"
        assert result["commit_blocked"] is False
        assert result["recovery_mode"] == "patched"

    def test_log_attempt(self):
        ctrl = SceneRecoveryController()
        ctrl.start_time = 0
        report = {
            "violations": [make_violation("test", "high", "detail")],
            "commit_blocked": True,
            "passed": False,
        }
        ctrl._log_attempt("draft", "text", report, violations_before=[], violations_after=report.get("violations", []))
        assert len(ctrl.attempt_log) == 1
        assert ctrl.attempt_log[0]["strategy"] == "draft"
        assert len(ctrl.attempt_log[0]["violation_ids_after"]) == 1
        assert ctrl.attempt_log[0]["commit_blocked"] is True

    @pytest.mark.asyncio
    async def test_recover_generated_scene_accepts_patched_text(self, monkeypatch):
        ctrl = SceneRecoveryController()
        blocking = make_violation("forbidden_triggered", "critical", "禁止内容")
        ctrl.quality_gate.evaluate = AsyncMock(side_effect=[
            {"passed": False, "commit_blocked": True, "violations": [blocking]},
            {"passed": True, "commit_blocked": False, "violations": []},
            {"passed": True, "commit_blocked": False, "violations": []},
        ])
        repair_agent = MagicMock()
        repair_agent.execute = AsyncMock(return_value={
            "success": True,
            "repaired_text": "修复后的正文",
            "repairs": [{"violation_type": "forbidden_triggered"}],
        })
        monkeypatch.setattr(scene_recovery_controller, "SceneRepairer", lambda: repair_agent)

        result = await ctrl.recover_generated_scene(
            {"scene_contract": {"goal": "test"}},
            "包含禁止内容的正文",
        )

        assert result["commit_blocked"] is False
        assert result["recovery_mode"] == "patched"
        assert result["generated_text"] == "修复后的正文"

    @pytest.mark.asyncio
    async def test_rewrite_receives_rejected_draft(self, monkeypatch):
        ctrl = SceneRecoveryController()
        blocking = make_violation("canon_timeline_confusion", "critical", "timeline")
        ctrl.quality_gate.evaluate = AsyncMock(side_effect=[
            {"passed": False, "commit_blocked": True, "violations": [blocking]},
            {"passed": True, "commit_blocked": False, "violations": []},
        ])
        rewrite_agent = MagicMock()
        rewrite_agent.execute = AsyncMock(return_value={
            "success": True,
            "generated_text": "rewritten",
        })
        monkeypatch.setattr(scene_recovery_controller, "SceneRepairer", lambda: rewrite_agent)

        result = await ctrl.recover_generated_scene(
            {"scene_contract": {"goal": "test"}},
            "rejected draft",
        )

        assert result["recovery_mode"] == "rewritten"
        assert rewrite_agent.execute.await_args.args[0]["rejected_text"] == "rejected draft"

    @pytest.mark.asyncio
    async def test_missing_contract_is_rebuilt_from_scene_beat(self, monkeypatch):
        ctrl = SceneRecoveryController()
        blocking = make_violation("missing_contract", "critical", "missing")
        ctrl.quality_gate.evaluate = AsyncMock(side_effect=[
            {"passed": False, "commit_blocked": True, "violations": [blocking]},
            {"passed": True, "commit_blocked": False, "violations": []},
        ])
        rewrite_agent = MagicMock()
        rewrite_agent.execute = AsyncMock(return_value={
            "success": True,
            "generated_text": "rewritten from rebuilt contract",
        })
        monkeypatch.setattr(scene_recovery_controller, "SceneRepairer", lambda: rewrite_agent)

        result = await ctrl.recover_generated_scene(
            {
                "scene_contract": {},
                "scene_beat": {
                    "goal_text": "escape",
                    "conflict_text": "guards",
                    "outcome_text": "outside",
                    "pov_character": "hero",
                },
                "chapter_number": 1,
                "scene_index": 0,
            },
            "rejected draft",
        )

        rewrite_context = rewrite_agent.execute.await_args.args[0]
        assert result["commit_blocked"] is False
        assert rewrite_context["scene_contract"]["goal"] == "escape"
        assert rewrite_context["rejected_text"] == "rejected draft"


class TestQualityGate:
    @pytest.mark.asyncio
    async def test_evaluate_empty_text(self):
        gate = QualityGate()
        result = await gate.evaluate({"generated_text": ""}, level="full")
        assert result["passed"] is False
        assert result["commit_blocked"] is True
        assert any(v["type"] == "empty_text" for v in result["violations"])

    @pytest.mark.asyncio
    async def test_evaluate_fast_level_skips_semantic(self):
        gate = QualityGate()
        with patch.object(gate, '_run_deterministic', new_callable=AsyncMock, return_value=[]), \
             patch.object(gate, '_run_consistency', new_callable=AsyncMock, return_value={"violations": []}), \
             patch.object(gate, '_run_semantic', new_callable=AsyncMock, return_value=[]):
            result = await gate.evaluate({"generated_text": "test text", "scene_contract": {"goal": "test"}}, level="fast")
            assert result["level_executed"] == "fast"
            gate._run_semantic.assert_not_called()

    @pytest.mark.asyncio
    async def test_evaluate_full_level_includes_semantic(self):
        gate = QualityGate()
        with patch.object(gate, '_run_deterministic', new_callable=AsyncMock, return_value=[]), \
             patch.object(gate, '_run_consistency', new_callable=AsyncMock, return_value={"violations": []}), \
             patch.object(gate, '_run_semantic', new_callable=AsyncMock, return_value=[]), \
             patch.object(gate, '_run_fcip', new_callable=AsyncMock, return_value=[]):
            result = await gate.evaluate({"generated_text": "test text", "scene_contract": {"goal": "test"}}, level="full")
            assert result["level_executed"] == "full"
            gate._run_semantic.assert_called_once()

    @pytest.mark.asyncio
    async def test_short_circuit_on_rewrite_suggested(self):
        gate = QualityGate()
        blocking_violation = make_violation("canon_timeline_confusion", "critical", "test")
        with patch.object(gate, '_run_deterministic', new_callable=AsyncMock, return_value=[blocking_violation]), \
             patch.object(gate, '_run_consistency', new_callable=AsyncMock, return_value={"violations": []}), \
             patch.object(gate, '_run_semantic', new_callable=AsyncMock, return_value=[]):
            result = await gate.evaluate({"generated_text": "test text", "scene_contract": {"goal": "test"}}, level="full")
            assert result["level_executed"] == "fast_short_circuited"
            gate._run_semantic.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_short_circuit_on_patch_suggested(self):
        gate = QualityGate()
        patch_violation = make_violation("forbidden_triggered", "critical", "test")
        with patch.object(gate, '_run_deterministic', new_callable=AsyncMock, return_value=[patch_violation]), \
             patch.object(gate, '_run_consistency', new_callable=AsyncMock, return_value={"violations": []}), \
             patch.object(gate, '_run_semantic', new_callable=AsyncMock, return_value=[]), \
             patch.object(gate, '_run_fcip', new_callable=AsyncMock, return_value=[]):
            result = await gate.evaluate({"generated_text": "test text", "scene_contract": {"goal": "test"}}, level="full")
            assert result["level_executed"] == "full"
            gate._run_semantic.assert_called_once()

    @pytest.mark.asyncio
    async def test_evaluate_missing_contract_blocks_commit(self):
        gate = QualityGate()
        result = await gate.evaluate({"generated_text": "test text"}, level="full")
        assert result["passed"] is False
        assert result["commit_blocked"] is True
        assert any(v["type"] == "missing_contract" for v in result["violations"])

    @pytest.mark.asyncio
    async def test_fcip_high_violation_preserves_severity_and_is_patchable(self, monkeypatch):
        gate = QualityGate()
        monkeypatch.setattr(
            generation,
            "run_foreshadowing_post_check",
            AsyncMock(return_value={
                "violations_found": 1,
                "violations": [{"severity": "High", "detail": "伏笔引用过早"}],
            }),
        )

        @asynccontextmanager
        async def db_session_factory():
            yield AsyncMock()

        result = await gate._run_fcip(
            "正文", {}, MagicMock(), "project-1", 1, 0, db_session_factory,
        )

        violation = result["violations"][0]
        assert violation["severity"] == "high"
        assert violation["suggested_strategy"] == "patch_text"

    @pytest.mark.asyncio
    async def test_critic_parse_error_is_non_blocking_after_retry(self):
        gate = QualityGate()
        parse_error = make_violation("critic_parse_error", "medium", "invalid json", source="critic")
        with patch.object(gate, '_run_deterministic', new_callable=AsyncMock, return_value=[]), \
             patch.object(gate, '_run_consistency', new_callable=AsyncMock, return_value={"violations": []}), \
             patch.object(gate, '_run_semantic', new_callable=AsyncMock, return_value=[parse_error]), \
             patch.object(gate, '_run_fcip', new_callable=AsyncMock, return_value=[]):
            result = await gate.evaluate({"generated_text": "test text", "scene_contract": {"goal": "test"}}, level="full")

        assert result["commit_blocked"] is True
        assert any(v["type"] == "critic_parse_error" for v in result["violations"])

    @pytest.mark.asyncio
    async def test_consistency_exception_degrades_without_blocking(self):
        gate = QualityGate()
        with patch.object(gate, '_run_deterministic', new_callable=AsyncMock, return_value=[]), \
             patch.object(gate, '_run_consistency', new_callable=AsyncMock, side_effect=RuntimeError("temporary")), \
             patch.object(gate, '_run_semantic', new_callable=AsyncMock, return_value=[]), \
             patch.object(gate, '_run_fcip', new_callable=AsyncMock, return_value=[]):
            result = await gate.evaluate({"generated_text": "test text", "scene_contract": {"goal": "test"}}, level="full")

        assert result["commit_blocked"] is True
        assert any(v["type"] == "consistency_check_unavailable" for v in result["violations"])


class TestMustShowDeterministicChecks:
    def test_semantic_must_show_does_not_require_verbatim_wording(self):
        critic = SceneCriticAgent()
        violations = critic._deterministic_check(
            "凤溪睁开眼，后脑钝痛。陌生记忆一股脑涌进来，她一时分不清自己身在何处。",
            {
                "must_show": [
                    "凤溪从昏迷/睡梦中醒来，感受到身体不适与记忆混乱",
                ],
            },
            {},
        )

        assert not any(v["type"] == "missing_must_show" for v in violations)


class TestCriticResponseParsing:
    def test_parses_json_code_fence(self):
        critic = SceneCriticAgent()
        result = critic._parse_json_response(
            '```json\n{"violations": [{"type": "fact_conflict"}]}\n```'
        )

        assert result["violations"][0]["type"] == "fact_conflict"

    def test_parses_json_with_explanatory_prefix(self):
        critic = SceneCriticAgent()
        result = critic._parse_json_response(
            '审查完成，结果如下：\n{"violations": []}\n请据此处理。'
        )

        assert result == {"violations": []}

    @pytest.mark.asyncio
    async def test_unknown_semantic_type_is_rewriteable(self, monkeypatch):
        critic = SceneCriticAgent()
        llm = MagicMock()
        llm.generate = AsyncMock(return_value=json.dumps({
            "violations": [{
                "type": "unexpected_semantic_issue",
                "severity": "high",
                "detail": "场景逻辑需要重写",
            }],
        }))
        monkeypatch.setattr(critic, "get_llm_client", AsyncMock(return_value=llm))

        violations = await critic._llm_check("正文", {"goal": "推进剧情"}, {}, [])

        assert violations[0]["type"] == "semantic_quality_error"
        assert violations[0]["suggested_strategy"] == "rewrite_scene"


class TestConsistencyResponseParsing:
    @pytest.mark.asyncio
    async def test_accepts_json_code_fence(self, monkeypatch):
        checker = SceneValidator()
        llm = MagicMock()
        llm.generate = AsyncMock(return_value='```json\n{"consistency":{"pass":true,"conflicts":[],"suggestions":[]},"contract_compliance":{"violations":[]},"reader_experience":{"scores":{},"advisories":[]}}\n```')
        monkeypatch.setattr(checker, "get_llm_client", AsyncMock(return_value=llm))

        result = await checker.execute({
            "generated_text": "正文",
            "scene_contract": {"goal": "test", "must_show": [], "forbidden": []},
            "core_facts": {},
            "current_state": {},
        })

        assert result["consistency"]["pass"] is True
        assert result["consistency"]["conflicts"] == []

    def test_summarize_facts_ignores_non_dict_values(self):
        checker = SceneValidator()
        summary = checker._summarize_facts({
            "legacy": "旧格式文本",
            "hero": {"type": "character", "name": "凤溪"},
        })

        assert "凤溪" in summary

    def test_explicit_literal_anchor_blocks_when_missing(self):
        critic = SceneCriticAgent()
        violations = critic._deterministic_check(
            "凤溪睁开眼，后脑仍在隐隐作痛。",
            {
                "must_show": [
                    {"type": "literal_anchor", "value": "蚀骨丹"},
                ],
            },
            {},
        )

        assert any(v["type"] == "missing_must_show" for v in violations)

    def test_explicit_literal_anchor_passes_when_present(self):
        critic = SceneCriticAgent()
        violations = critic._deterministic_check(
            "凤溪从怀中摸出蚀骨丹，迅速将它收进包袱深处。",
            {
                "must_show_literal_anchors": ["蚀骨丹"],
            },
            {},
        )

        assert not any(v["type"] == "missing_must_show" for v in violations)


class TestProjectLockManager:
    def test_get_lock_returns_same_lock(self):
        lock1 = ProjectLockManager.get_lock("project-1")
        lock2 = ProjectLockManager.get_lock("project-1")
        assert lock1 is lock2

    def test_get_lock_different_projects(self):
        lock1 = ProjectLockManager.get_lock("project-1")
        lock2 = ProjectLockManager.get_lock("project-2")
        assert lock1 is not lock2

    @pytest.mark.asyncio
    async def test_lock_prevents_concurrent_access(self):
        lock = ProjectLockManager.get_lock("test-project-lock")
        acquired = []

        async def task1():
            async with lock:
                acquired.append("task1_start")
                await asyncio.sleep(0.05)
                acquired.append("task1_end")

        async def task2():
            await asyncio.sleep(0.01)
            async with lock:
                acquired.append("task2_start")
                await asyncio.sleep(0.01)
                acquired.append("task2_end")

        await asyncio.gather(task1(), task2())
        assert acquired.index("task1_start") < acquired.index("task1_end")
        assert acquired.index("task2_start") < acquired.index("task2_end")
        assert acquired.index("task1_end") < acquired.index("task2_start")

    def test_cleanup(self):
        lock = ProjectLockManager.get_lock("cleanup-test")
        ProjectLockManager.cleanup("cleanup-test")
        new_lock = ProjectLockManager.get_lock("cleanup-test")
        assert new_lock is not lock
