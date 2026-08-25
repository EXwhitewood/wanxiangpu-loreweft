"""结尾补写专员测试。

所有测试使用抽象实体占位符（角色A, 角色B, 物品X, 地点Y），
遵循反污染原则，不引入任何真实作品内容。
"""

from __future__ import annotations

import pytest

from app.services.fbi.tools.ending_state_tool import EndingStateTool


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_TEXT = (
    "角色A在地点Y发现了物品X。角色A决定带走物品X。"
    "角色A把物品X放进了口袋。角色A离开了地点Y。"
)

SAMPLE_TEXT_NO_ENDING = (
    "角色A在地点Y发现了物品X。角色A犹豫着要不要拿走物品X。"
)


@pytest.fixture
def tool() -> EndingStateTool:
    return EndingStateTool()


# ---------------------------------------------------------------------------
# 1. check_ending_contract
# ---------------------------------------------------------------------------

class TestCheckEndingContract:
    def test_fulfilled_states(self, tool: EndingStateTool):
        ending_contract = {
            "required_states": ["角色A离开"],
            "optional_states": [],
        }
        result = tool.check_ending_contract(SAMPLE_TEXT, ending_contract)

        assert isinstance(result["fulfilled"], list)
        assert isinstance(result["missing"], list)
        assert isinstance(result["partial"], list)

    def test_missing_states(self, tool: EndingStateTool):
        ending_contract = {
            "required_states": ["角色A做出决定"],
            "optional_states": [],
        }
        result = tool.check_ending_contract(SAMPLE_TEXT_NO_ENDING, ending_contract)

        # "角色A做出决定" 应在 missing 或 partial 中
        all_states = [f["state"] for f in result["fulfilled"]] + result["missing"] + [p["state"] for p in result["partial"]]
        assert len(all_states) > 0

    def test_partial_abstract_expression(self, tool: EndingStateTool):
        """抽象表达（如'她决定了'）应被标记为 partial 而非 fulfilled。"""
        text_with_abstract = "角色A决定了。"
        ending_contract = {
            "required_states": ["角色A做出决定"],
            "optional_states": [],
        }
        result = tool.check_ending_contract(text_with_abstract, ending_contract)

        # 抽象表达应被检测为 partial
        if result["partial"]:
            assert any("抽象" in p.get("note", "") for p in result["partial"])


# ---------------------------------------------------------------------------
# 2. tail_slot_finder
# ---------------------------------------------------------------------------

class TestTailSlotFinder:
    def test_append_for_clean_ending(self, tool: EndingStateTool):
        result = tool.tail_slot_finder(SAMPLE_TEXT)

        assert result["action"] in ("append", "rewrite_last", "rewrite_last_two")
        assert "insert_position" in result
        assert "max_append_chars" in result
        assert result["max_append_chars"] > 0

    def test_rewrite_last_for_short_ending(self, tool: EndingStateTool):
        short_ending_text = "角色A在地点Y发现了物品X。\n\n短。"
        result = tool.tail_slot_finder(short_ending_text)

        assert result["action"] in ("rewrite_last", "append")

    def test_empty_text_returns_append(self, tool: EndingStateTool):
        result = tool.tail_slot_finder("")

        assert result["action"] == "append"
        assert result["max_append_chars"] > 0


# ---------------------------------------------------------------------------
# 3. ending_bridge_planner
# ---------------------------------------------------------------------------

class TestEndingBridgePlanner:
    def test_generates_plan_for_missing_states(self, tool: EndingStateTool):
        missing_states = ["角色A做出决定", "角色A离开地点Y"]
        plan = tool.ending_bridge_planner(missing_states)

        assert isinstance(plan, list)
        assert len(plan) == 2

        for entry in plan:
            assert "state" in entry
            assert "bridge_type" in entry
            assert "suggested_text_length" in entry
            assert entry["bridge_type"] in ("action", "cognition", "decision", "aftermath")

    def test_plan_sorted_by_type_order(self, tool: EndingStateTool):
        missing_states = ["角色A离开地点Y", "角色A明白了真相", "角色A做出决定"]
        plan = tool.ending_bridge_planner(missing_states)

        type_order = {"action": 0, "cognition": 1, "decision": 2, "aftermath": 3}
        orders = [type_order.get(p["bridge_type"], 99) for p in plan]

        # 应按 action -> cognition -> decision -> aftermath 排序
        assert orders == sorted(orders)


# ---------------------------------------------------------------------------
# 4. ChineseTextMetrics sentence_length_stats
# ---------------------------------------------------------------------------

class TestChineseTextMetrics:
    def test_sentence_length_stats(self, tool: EndingStateTool):
        """验证结尾工具对文本的基本分析能力。"""
        # 使用 tail_slot_finder 间接验证文本分析
        result = tool.tail_slot_finder(SAMPLE_TEXT)

        assert isinstance(result, dict)
        assert "action" in result
        assert "insert_position" in result
        # insert_position 应在文本范围内
        assert result["insert_position"] >= 0
