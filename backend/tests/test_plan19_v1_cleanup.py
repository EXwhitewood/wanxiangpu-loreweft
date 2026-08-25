"""方案19：V1残留代码清理测试。"""
import os
import ast
import pytest


# ---- 测试1：V1残留分支（elif scene_beat）已删除 ----

def test_v1_scene_beat_branch_removed():
    """验证 core_generation.py 中不再有 elif scene_beat 分支。"""
    cg_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "app", "agents", "core_generation.py"
    )
    if not os.path.exists(cg_path):
        pytest.skip(f"core_generation.py not found at {cg_path}")

    with open(cg_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 不应有 elif scene_beat 分支
    # 检查是否还有 "elif scene_beat:" 这样的代码
    lines = content.split("\n")
    scene_beat_elif_lines = [
        line for line in lines
        if "elif scene_beat:" in line and not line.strip().startswith("#")
    ]
    assert len(scene_beat_elif_lines) == 0, (
        f"core_generation.py 仍包含 elif scene_beat 分支: {scene_beat_elif_lines}"
    )


# ---- 测试2：V1兜底逻辑已删除 ----

def test_v1_fallback_logic_removed():
    """验证 _build_execution_report 中不再有 V1 scene_contract 兜底。"""
    cg_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "app", "agents", "core_generation.py"
    )
    if not os.path.exists(cg_path):
        pytest.skip(f"core_generation.py not found at {cg_path}")

    with open(cg_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 找到 _build_execution_report 方法
    method_start = content.find("def _build_execution_report(")
    if method_start == -1:
        pytest.fail("_build_execution_report method not found")

    # 找到方法结束（下一个 def）
    method_end = content.find("\n    def ", method_start + 10)
    if method_end == -1:
        method_end = content.find("\ndef ", method_start + 10)
    if method_end == -1:
        method_end = len(content)

    method_body = content[method_start:method_end]

    # 不应有 scene_contract.get("must_show") 或 scene_contract.get("forbidden") 兜底
    assert 'scene_contract.get("must_show")' not in method_body, (
        "_build_execution_report 仍包含 V1 兜底: scene_contract.get(\"must_show\")"
    )
    assert 'scene_contract.get("forbidden")' not in method_body, (
        "_build_execution_report 仍包含 V1 兜底: scene_contract.get(\"forbidden\")"
    )
    assert 'scene_contract.get("ending_state")' not in method_body, (
        "_build_execution_report 仍包含 V1 兜底: scene_contract.get(\"ending_state\")"
    )


# ---- 测试3：V2路径（writer_input_packet）仍保留 ----

def test_v2_writer_input_packet_path_preserved():
    """验证 V2 writer_input_packet 路径仍保留。"""
    cg_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "app", "agents", "core_generation.py"
    )
    if not os.path.exists(cg_path):
        pytest.skip(f"core_generation.py not found at {cg_path}")

    with open(cg_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "writer_input_packet" in content, (
        "core_generation.py 缺少 writer_input_packet（V2 路径）"
    )
    assert "writer_input_packet.get" in content, (
        "core_generation.py 缺少 writer_input_packet.get 调用（V2 路径）"
    )


# ---- 测试4：V1兼容层（_build_v2_from_v1）已删除 ----

def test_v1_compatibility_layer_removed():
    """验证 Agent 不再内部兜底编译旧场景合同。"""
    cg_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "app", "agents", "core_generation.py"
    )
    if not os.path.exists(cg_path):
        pytest.skip(f"core_generation.py not found at {cg_path}")

    with open(cg_path, "r", encoding="utf-8") as f:
        content = f.read()

    assert "_build_v2_from_v1" not in content
    assert "scene_context_package.writer_input_packet is required" in content


# ---- 测试5：V2编译流水线组件仍保留 ----

def test_v2_compilation_pipeline_preserved():
    """验证 V2编译流水线组件仍保留。"""
    # 检查 writer_input_compiler.py 存在
    wic_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "app", "services", "writer_input_compiler.py"
    )
    assert os.path.exists(wic_path), (
        "writer_input_compiler.py 不存在（V2编译流水线核心组件）"
    )

    # 检查 WriterInputPacket 模型存在
    ci_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "app", "models", "contract_item.py"
    )
    assert os.path.exists(ci_path), (
        "contract_item.py 不存在（WriterInputPacket 模型所在文件）"
    )

    with open(ci_path, "r", encoding="utf-8") as f:
        content = f.read()
    assert "class WriterInputPacket" in content, (
        "contract_item.py 缺少 WriterInputPacket 类定义"
    )


# ---- 测试6：_build_execution_report 仍能正确工作 ----

def test_build_execution_report_works_without_v1_fallback():
    """验证 _build_execution_report 在无V1兜底时仍能正确工作。"""
    from app.agents.core_generation import CoreGenerationAgent

    agent = CoreGenerationAgent()

    # 模拟V2 packet（无V1兜底）
    writer_input_packet = {
        "must_include": ["关键词1", "关键词2"],
        "must_avoid": ["禁止词"],
        "ending_state": "结尾状态",
    }

    # 调用 _build_execution_report
    # 需要确认方法签名，先 Read 确认
    import inspect
    sig = inspect.signature(agent._build_execution_report)
    params = list(sig.parameters.keys())

    # 构造参数
    kwargs = {
        "generated_text": "这是包含关键词1和结尾状态的正文，第二点未提及。",
        "writer_input_packet": writer_input_packet,
    }

    # 如果方法需要 scene_contract 参数，传入空dict
    if "scene_contract" in params:
        kwargs["scene_contract"] = {}

    report = agent._build_execution_report(**kwargs)

    # 验证报告正确生成
    assert "must_show_coverage" in report
    assert "forbidden_violations" in report
    assert "ending_state_achieved" in report
    assert "deviations" in report
    # 关键词1应被覆盖
    assert any("关键词1" in k for k in report["must_show_coverage"])
    # 关键词2未被覆盖
    assert any("关键词2" in k and not v for k, v in report["must_show_coverage"].items())
    # 禁止词未出现在正文中
    assert len(report["forbidden_violations"]) == 0
    # 结尾状态已达成
    assert report["ending_state_achieved"] is True
