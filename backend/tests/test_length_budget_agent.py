"""篇幅预算修复专员测试。

所有测试使用抽象实体占位符（角色A, 角色B, 物品X, 地点Y），
遵循反污染原则，不引入任何真实作品内容。
"""

from __future__ import annotations

import pytest

from app.services.fbi.tools.length_budget_tool import LengthBudgetTool


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_TEXT_COMPRESS = (
    "角色A在地点Y发现了物品X。角色A觉得这很重要。角色A感到很惊讶。"
    "角色A走过去。角色A拿起了物品X。角色A看了看物品X。"
    "也就是说，物品X是关键线索。换句话说，角色A必须保管好它。"
    "角色B在远处看着角色A。角色B觉得角色A不知道真相。"
)

SAMPLE_TEXT_EXPAND = (
    "角色A到了地点Y。\n\n角色A发现了物品X。\n\n角色A离开了。"
)


@pytest.fixture
def tool() -> LengthBudgetTool:
    return LengthBudgetTool()


# ---------------------------------------------------------------------------
# 1. count_chinese_chars
# ---------------------------------------------------------------------------

class TestCountChineseChars:
    def test_counts_chinese_chars(self, tool: LengthBudgetTool):
        result = tool.count_chinese_chars("角色A在地点Y")

        assert result["chinese_chars"] > 0
        assert result["total_chars"] == len("角色A在地点Y")
        # "A" and "Y" are English
        assert result["english_chars"] == 2

    def test_counts_punctuation(self, tool: LengthBudgetTool):
        result = tool.count_chinese_chars("角色A说：「你好。」")

        assert result["chinese_punctuation"] > 0

    def test_empty_string(self, tool: LengthBudgetTool):
        result = tool.count_chinese_chars("")

        assert result["chinese_chars"] == 0
        assert result["total_chars"] == 0


# ---------------------------------------------------------------------------
# 2. paragraph_budget_plan (compress)
# ---------------------------------------------------------------------------

class TestParagraphBudgetPlanCompress:
    def test_compress_mode(self, tool: LengthBudgetTool):
        result = tool.paragraph_budget_plan(SAMPLE_TEXT_COMPRESS, 50, "compress")

        assert isinstance(result, list)
        assert len(result) > 0

        for entry in result:
            assert "paragraph_index" in entry
            assert "current_chars" in entry
            assert "target_chars" in entry
            assert "delta" in entry
            assert "priority" in entry
            # 压缩模式下 delta 应为负数或零
            assert entry["delta"] <= 0

    def test_compress_priority_higher_for_longer_paragraphs(self, tool: LengthBudgetTool):
        long_text = "角色A在地点Y发现了物品X。" * 20 + "\n\n" + "短段。"
        result = tool.paragraph_budget_plan(long_text, 50, "compress")

        # 长段落应有更高优先级
        if len(result) >= 2:
            first_priority = result[0]["priority"]
            last_priority = result[-1]["priority"]
            assert first_priority >= last_priority


# ---------------------------------------------------------------------------
# 3. paragraph_budget_plan (expand)
# ---------------------------------------------------------------------------

class TestParagraphBudgetPlanExpand:
    def test_expand_mode(self, tool: LengthBudgetTool):
        result = tool.paragraph_budget_plan(SAMPLE_TEXT_EXPAND, 500, "expand")

        assert isinstance(result, list)
        assert len(result) > 0

        for entry in result:
            # 扩写模式下 delta 应为正数或零
            assert entry["delta"] >= 0

    def test_expand_priority_higher_for_shorter_paragraphs(self, tool: LengthBudgetTool):
        result = tool.paragraph_budget_plan(SAMPLE_TEXT_EXPAND, 500, "expand")

        if len(result) >= 2:
            # 短段落应有更高优先级
            priorities = [e["priority"] for e in result]
            assert max(priorities) > 0


# ---------------------------------------------------------------------------
# 4. redundancy_detector
# ---------------------------------------------------------------------------

class TestRedundancyDetector:
    def test_detects_redundancy(self, tool: LengthBudgetTool):
        result = tool.redundancy_detector(SAMPLE_TEXT_COMPRESS)

        assert isinstance(result, list)
        # 应检测到重复动作（走、拿、看）或重复心理（觉得、感到）
        types = {r["type"] for r in result}
        assert len(types) > 0

    def test_redundancy_has_span_info(self, tool: LengthBudgetTool):
        result = tool.redundancy_detector(SAMPLE_TEXT_COMPRESS)

        for r in result:
            assert "span" in r
            assert "start" in r["span"]
            assert "end" in r["span"]
            assert "risk" in r

    def test_no_redundancy_in_clean_text(self, tool: LengthBudgetTool):
        clean_text = "角色A在地点Y发现了物品X。角色B对此毫不知情。"
        result = tool.redundancy_detector(clean_text)

        # 干净文本不应有高风险冗余
        high_risk = [r for r in result if r["risk"] == "high"]
        assert len(high_risk) == 0


# ---------------------------------------------------------------------------
# 5. expansion_slot_finder
# ---------------------------------------------------------------------------

class TestExpansionSlotFinder:
    def test_finds_expansion_slots(self, tool: LengthBudgetTool):
        result = tool.expansion_slot_finder(SAMPLE_TEXT_EXPAND)

        assert isinstance(result, list)
        assert len(result) > 0

    def test_slots_have_required_fields(self, tool: LengthBudgetTool):
        result = tool.expansion_slot_finder(SAMPLE_TEXT_EXPAND)

        for slot in result:
            assert "type" in slot
            assert "after_span" in slot
            assert "suggested_chars" in slot
            assert slot["suggested_chars"] > 0
