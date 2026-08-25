"""方案18：writer-主编实时反馈回路测试套件。

覆盖文档 §18 定义的关键验证场景：
1. 执行报告测试：验证 writer 输出包含 execution_report，且 _build_execution_report 正确检测
   must_show 覆盖 / forbidden 违规 / ending_state 达成
2. 回流测试：验证 EditorFeedbackService.record_rewrite_feedback 持久化 FBI 偏离原因
3. 下一章调整测试：验证主编规划下一章时能通过 build_feedback_prompt_section 看到上一章执行报告
4. contract_style_conflict 测试：验证 _attempt_style_polish_auto_repair 触发记录而非仅 return
"""
from __future__ import annotations

import pytest

from app.agents.core_generation import CoreGenerationAgent
from app.services import editor_feedback_service
from app.services.editor_feedback_service import EditorFeedbackService


@pytest.fixture
def isolated_feedback_dir(tmp_path, monkeypatch):
    """隔离反馈数据目录，避免污染真实数据。"""
    feedback_dir = tmp_path / "editor_feedback"
    feedback_dir.mkdir()
    monkeypatch.setattr(editor_feedback_service, "_FEEDBACK_DIR", str(feedback_dir))
    return str(feedback_dir)


# ---------------------------------------------------------------------------
# 步骤1：Writer 执行报告测试
# ---------------------------------------------------------------------------


def test_build_execution_report_detects_must_show_coverage():
    """验证1：_build_execution_report 能检测 must_show 覆盖情况。"""
    agent = CoreGenerationAgent()
    generated_text = "凤溪走进戒律堂，银线草在掌心微微发光。"

    report = agent._build_execution_report(
        generated_text=generated_text,
        writer_input_packet={
            "must_include": ["凤溪走进戒律堂", "银线草"],
            "must_avoid": ["黑市"],
            "ending_state": "凤溪离开",
        },
    )

    assert report["must_show_coverage"]["凤溪走进戒律堂"] is True
    assert report["must_show_coverage"]["银线草"] is True
    assert report["forbidden_violations"] == []
    assert report["ending_state_achieved"] is False  # "凤溪离开" 不在文本中
    assert any("ending_state" in d for d in report["deviations"])


def test_build_execution_report_detects_missing_must_show():
    """验证1：must_show 未覆盖时能正确标记为 False 并加入 deviations。"""
    agent = CoreGenerationAgent()
    generated_text = "凤溪走进戒律堂。"

    report = agent._build_execution_report(
        generated_text=generated_text,
        writer_input_packet={
            "must_include": ["凤溪走进戒律堂", "银线草"],
            "must_avoid": [],
            "ending_state": "",
        },
    )

    assert report["must_show_coverage"]["凤溪走进戒律堂"] is True
    assert report["must_show_coverage"]["银线草"] is False
    assert any("must_show" in d for d in report["deviations"])


def test_build_execution_report_detects_forbidden_violations():
    """验证1：_build_execution_report 能检测 forbidden 违规。"""
    agent = CoreGenerationAgent()
    generated_text = "她走进黑市，看到黑市的灯火。"

    report = agent._build_execution_report(
        generated_text=generated_text,
        writer_input_packet={
            "must_include": [],
            "must_avoid": ["黑市"],
            "ending_state": "",
        },
    )

    assert len(report["forbidden_violations"]) > 0
    assert any("黑市" in v for v in report["forbidden_violations"])
    assert any("forbidden" in d for d in report["deviations"])


def test_build_execution_report_ending_state_achieved():
    """验证1：ending_state 达成时 ending_state_achieved 为 True。"""
    agent = CoreGenerationAgent()
    generated_text = "凤溪离开了戒律堂。"

    report = agent._build_execution_report(
        generated_text=generated_text,
        writer_input_packet={
            "must_include": [],
            "must_avoid": [],
            "ending_state": "凤溪离开",
        },
    )

    assert report["ending_state_achieved"] is True
    assert not any("ending_state" in d for d in report["deviations"])


def test_build_execution_report_reads_only_writer_input_packet():
    """验证1：执行报告只读取编排层生成的 WriterInputPacket。"""
    agent = CoreGenerationAgent()
    generated_text = "凤溪拿起银线草。"

    report = agent._build_execution_report(
        generated_text=generated_text,
        writer_input_packet={
            "must_include": ["银线草"],
            "must_avoid": ["黑市"],
            "ending_state": "凤溪离开",
            "output_constraints": {"target_chars": 100},
        },
    )

    assert report["must_show_coverage"]["银线草"] is True
    assert report["forbidden_violations"] == []
    assert report["ending_state_achieved"] is False
    assert report["expected_word_count"] == 100


def test_build_execution_report_handles_dict_ending_state():
    """验证1：ending_state 为 dict 类型时也能正确检测。"""
    agent = CoreGenerationAgent()
    generated_text = "凤溪离开了戒律堂。"

    report = agent._build_execution_report(
        generated_text=generated_text,
        writer_input_packet={
            "must_include": [],
            "must_avoid": [],
            "ending_state": {"character": "凤溪", "state": "离开"},
        },
    )

    assert report["ending_state_achieved"] is True


def test_build_execution_report_empty_inputs():
    """验证1：空输入时返回空报告，不报错。"""
    agent = CoreGenerationAgent()
    generated_text = "一段普通正文。"

    report = agent._build_execution_report(
        generated_text=generated_text,
        writer_input_packet={},
    )

    assert report["must_show_coverage"] == {}
    assert report["forbidden_violations"] == []
    assert report["ending_state_achieved"] is True
    assert report["deviations"] == []
    assert report["word_count"] == len(generated_text)


# ---------------------------------------------------------------------------
# 步骤2：FBI rewrite 回流测试
# ---------------------------------------------------------------------------


def test_record_and_get_rewrite_feedback(isolated_feedback_dir):
    """验证2：EditorFeedbackService 能记录和查询 FBI rewrite 偏离原因。"""
    project_id = "test-project-fbi"
    EditorFeedbackService.record_rewrite_feedback(
        project_id,
        chapter_number=3,
        feedback={
            "scene_index": 0,
            "violation_type": "scene_contract_violation",
            "detail": "writer 偏离了 must_show",
            "original_contract": {"scene_id": "c3-s1"},
            "deviation": "must_show 未覆盖",
        },
    )

    feedbacks = EditorFeedbackService.get_rewrite_feedback(project_id, 3)
    assert len(feedbacks) == 1
    assert feedbacks[0]["scene_index"] == 0
    assert feedbacks[0]["violation_type"] == "scene_contract_violation"
    assert "must_show" in feedbacks[0]["detail"]
    assert "recorded_at" in feedbacks[0]


def test_record_rewrite_feedback_multiple(isolated_feedback_dir):
    """验证2：同一章节多次 rewrite 反馈都被记录。"""
    project_id = "test-project-fbi-multi"
    for i in range(3):
        EditorFeedbackService.record_rewrite_feedback(
            project_id,
            chapter_number=5,
            feedback={
                "scene_index": i,
                "violation_type": "scene_contract_violation",
                "detail": f"场景 {i} 偏离",
            },
        )

    feedbacks = EditorFeedbackService.get_rewrite_feedback(project_id, 5)
    assert len(feedbacks) == 3
    assert {f["scene_index"] for f in feedbacks} == {0, 1, 2}


def test_get_rewrite_feedback_empty(isolated_feedback_dir):
    """验证2：没有记录时返回空列表。"""
    feedbacks = EditorFeedbackService.get_rewrite_feedback("nonexistent", 1)
    assert feedbacks == []


# ---------------------------------------------------------------------------
# 步骤3：主编参考上一章执行报告测试
# ---------------------------------------------------------------------------


def test_record_and_get_execution_report(isolated_feedback_dir):
    """验证3：EditorFeedbackService 能记录和查询 writer 执行报告。"""
    project_id = "test-project-exec"
    EditorFeedbackService.record_execution_report(
        project_id,
        chapter_number=2,
        report={
            "must_show_coverage": {"银线草": True},
            "forbidden_violations": [],
            "ending_state_achieved": True,
            "deviations": [],
        },
    )

    report = EditorFeedbackService.get_execution_report(project_id, 2)
    assert report is not None
    assert report["must_show_coverage"]["银线草"] is True
    assert report["ending_state_achieved"] is True
    assert "recorded_at" in report


def test_build_feedback_prompt_section_includes_prev_deviation(isolated_feedback_dir):
    """验证3：主编规划下一章时能收到上一章的执行报告反馈。"""
    project_id = "test-project-editor"

    # 记录上一章（第2章）的执行报告，包含多种偏离
    EditorFeedbackService.record_execution_report(
        project_id,
        chapter_number=2,
        report={
            "must_show_coverage": {"银线草": True, "戒律堂": False},
            "forbidden_violations": ["触发了禁止的元素：黑市"],
            "ending_state_achieved": False,
            "ending_state_detail": "凤溪离开",
            "deviations": ["must_show 未覆盖：戒律堂"],
        },
    )

    # 主编规划第3章时获取反馈
    prompt = EditorFeedbackService.build_feedback_prompt_section(project_id, 3)

    assert "第2章" in prompt
    assert "戒律堂" in prompt  # 未覆盖的 must_show
    assert "黑市" in prompt  # forbidden 违规
    assert "ending_state" in prompt  # ending_state 未达成
    assert "请在规划本章时参考上一章的反馈" in prompt


def test_build_feedback_prompt_section_finds_nearest_prev_chapter(isolated_feedback_dir):
    """验证3：build_feedback_prompt_section 会向前查找最近的有反馈的章节。"""
    project_id = "test-project-nearest"

    # 第1章没有反馈，第2章有反馈
    EditorFeedbackService.record_execution_report(
        project_id,
        chapter_number=2,
        report={
            "must_show_coverage": {"元素A": False},
            "forbidden_violations": [],
            "ending_state_achieved": True,
            "deviations": ["must_show 未覆盖：元素A"],
        },
    )

    # 规划第4章时，应找到第2章（跳过第3章）
    prompt = EditorFeedbackService.build_feedback_prompt_section(project_id, 4)

    assert "第2章" in prompt
    assert "元素A" in prompt


def test_build_feedback_prompt_section_returns_empty_when_no_feedback(isolated_feedback_dir):
    """验证3：没有上一章反馈时返回空字符串。"""
    prompt = EditorFeedbackService.build_feedback_prompt_section("nonexistent", 1)
    assert prompt == ""


def test_build_feedback_prompt_section_includes_rewrite_feedback(isolated_feedback_dir):
    """验证3：反馈 prompt 中包含 FBI rewrite 反馈。"""
    project_id = "test-project-rewrite-prompt"

    EditorFeedbackService.record_rewrite_feedback(
        project_id,
        chapter_number=2,
        feedback={
            "scene_index": 1,
            "violation_type": "scene_contract_violation",
            "detail": "场景1的 must_show 未覆盖",
        },
    )

    prompt = EditorFeedbackService.build_feedback_prompt_section(project_id, 3)

    assert "第2章" in prompt
    assert "FBI" in prompt or "重写" in prompt
    assert "场景1" in prompt or "must_show" in prompt


def test_get_chapter_feedback_summary_aggregates_all(isolated_feedback_dir):
    """验证3：get_chapter_feedback_summary 聚合执行报告、rewrite、风格冲突。"""
    project_id = "test-project-summary"

    EditorFeedbackService.record_execution_report(
        project_id, 1, {"must_show_coverage": {}, "deviations": []}
    )
    EditorFeedbackService.record_rewrite_feedback(
        project_id, 1, {"scene_index": 0, "detail": "test"}
    )
    EditorFeedbackService.record_style_conflict(
        project_id, 1, {"dimension": "pacing"}
    )

    summary = EditorFeedbackService.get_chapter_feedback_summary(project_id, 1)

    assert summary is not None
    assert summary["execution_report"] is not None
    assert len(summary["rewrite_feedbacks"]) == 1
    assert len(summary["style_conflicts"]) == 1


def test_get_chapter_feedback_summary_returns_none_when_empty(isolated_feedback_dir):
    """验证3：没有任何反馈时返回 None。"""
    summary = EditorFeedbackService.get_chapter_feedback_summary("nonexistent", 1)
    assert summary is None


# ---------------------------------------------------------------------------
# 步骤4：contract_style_conflict 测试
# ---------------------------------------------------------------------------


def test_record_style_conflict_persists(isolated_feedback_dir):
    """验证4：record_style_conflict 能持久化合同-风格冲突。"""
    project_id = "test-project-conflict"
    EditorFeedbackService.record_style_conflict(
        project_id,
        chapter_number=5,
        conflict={
            "dimension": "pacing",
            "detail": "合同要求快节奏，但风格约束要求慢节奏",
        },
    )

    summary = EditorFeedbackService.get_chapter_feedback_summary(project_id, 5)
    assert summary is not None
    assert len(summary["style_conflicts"]) == 1
    assert summary["style_conflicts"][0]["dimension"] == "pacing"
    assert "recorded_at" in summary["style_conflicts"][0]


@pytest.mark.asyncio
async def test_attempt_style_polish_auto_repair_records_contract_style_conflict(isolated_feedback_dir):
    """验证4：contract_style_conflict 被记录到反馈服务，而非仅 return。"""
    from app.api import editor_chat

    result = await editor_chat._attempt_style_polish_auto_repair(
        text="测试文本",
        polish_result={
            "requires_human_review": True,
            "summary": {
                "contract_style_conflict": {
                    "dimension": "pacing",
                    "detail": "合同要求快节奏，但风格约束要求慢节奏",
                }
            },
        },
        active_style={},
        llm=None,
        project_id="test-project-conflict-repair",
        chapter_number=5,
    )

    # 验证返回值
    assert result["attempted"] is False
    assert result["reason"] == "contract_style_conflict"
    assert result["conflict"] is not None

    # 验证冲突被记录到反馈服务
    summary = EditorFeedbackService.get_chapter_feedback_summary(
        "test-project-conflict-repair", 5
    )
    assert summary is not None
    assert len(summary["style_conflicts"]) == 1
    assert summary["style_conflicts"][0]["dimension"] == "pacing"


@pytest.mark.asyncio
async def test_attempt_style_polish_auto_repair_no_project_id_skips_record(isolated_feedback_dir):
    """验证4：未传 project_id 时不记录但仍返回 contract_style_conflict（向后兼容）。"""
    from app.api import editor_chat

    result = await editor_chat._attempt_style_polish_auto_repair(
        text="测试文本",
        polish_result={
            "requires_human_review": True,
            "summary": {
                "contract_style_conflict": {"dimension": "pacing"}
            },
        },
        active_style={},
        llm=None,
        # 不传 project_id 和 chapter_number
    )

    assert result["attempted"] is False
    assert result["reason"] == "contract_style_conflict"
    assert result["conflict"] is not None


@pytest.mark.asyncio
async def test_attempt_style_polish_auto_repair_no_conflict_no_record(isolated_feedback_dir):
    """验证4：没有 contract_style_conflict 时不记录。"""
    from app.api import editor_chat

    result = await editor_chat._attempt_style_polish_auto_repair(
        text="测试文本",
        polish_result={
            "requires_human_review": True,
            "summary": {"contract_style_conflict": False},
            # 没有 issues，所以没有 failures
        },
        active_style={},
        llm=None,
        project_id="test-project-no-conflict",
        chapter_number=5,
    )

    # 没有 failures，应该返回 no_style_failures
    assert result["attempted"] is False
    assert result["reason"] == "no_style_failures"

    # 验证没有记录冲突
    summary = EditorFeedbackService.get_chapter_feedback_summary(
        "test-project-no-conflict", 5
    )
    # summary 应该是 None（没有任何反馈）
    assert summary is None


@pytest.mark.asyncio
async def test_attempt_style_polish_auto_repair_string_conflict_recorded(isolated_feedback_dir):
    """验证4：contract_style_conflict 为字符串时也能正确记录。"""
    from app.api import editor_chat

    result = await editor_chat._attempt_style_polish_auto_repair(
        text="测试文本",
        polish_result={
            "requires_human_review": True,
            "summary": {
                "contract_style_conflict": "pacing 与合同冲突"
            },
        },
        active_style={},
        llm=None,
        project_id="test-project-str-conflict",
        chapter_number=7,
    )

    assert result["attempted"] is False
    assert result["reason"] == "contract_style_conflict"

    # 字符串类型的冲突应被转换为 dict 并记录
    summary = EditorFeedbackService.get_chapter_feedback_summary(
        "test-project-str-conflict", 7
    )
    assert summary is not None
    assert len(summary["style_conflicts"]) == 1
    # 字符串冲突应被包装为 {"summary": "..."}，然后 pop summary 得到默认 dimension
    assert summary["style_conflicts"][0]["dimension"] == "contract_style_conflict"
