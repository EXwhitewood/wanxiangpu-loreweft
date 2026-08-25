"""补丁合并器测试。

所有测试使用抽象实体占位符（角色A, 角色B, 物品X, 地点Y），
遵循反污染原则，不引入任何真实作品内容。

测试补丁合并逻辑：检测重叠、分组冲突、应用顺序等。
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# 补丁合并器实现（纯函数式，供测试使用）
# ---------------------------------------------------------------------------

class PatchMerger:
    """补丁合并器——检测重叠、分组冲突、按序应用。"""

    @staticmethod
    def detect_overlap(p1: dict, p2: dict) -> bool:
        """检测两个补丁是否重叠。"""
        s1, e1 = p1["span_start"], p1["span_end"]
        s2, e2 = p2["span_start"], p2["span_end"]
        # 重叠条件：区间相交
        return s1 < e2 and s2 < e1

    @staticmethod
    def group_conflicts(patches: list[dict]) -> list[list[dict]]:
        """将重叠的补丁分组。

        Returns:
            每组包含互相重叠的补丁列表。
        """
        if not patches:
            return []

        # 按 span_start 排序
        sorted_patches = sorted(patches, key=lambda p: p["span_start"])

        groups: list[list[dict]] = []
        current_group: list[dict] = [sorted_patches[0]]
        current_end = sorted_patches[0]["span_end"]

        for patch in sorted_patches[1:]:
            if patch["span_start"] < current_end:
                # 与当前组重叠
                current_group.append(patch)
                current_end = max(current_end, patch["span_end"])
            else:
                # 不重叠，开始新组
                groups.append(current_group)
                current_group = [patch]
                current_end = patch["span_end"]

        groups.append(current_group)
        return groups

    @staticmethod
    def apply_merged(text: str, patches: list[dict]) -> str:
        """按逆序应用补丁（从后往前），避免偏移问题。

        Returns:
            应用补丁后的文本。
        """
        if not patches:
            return text

        # 按 span_start 降序排列
        sorted_patches = sorted(patches, key=lambda p: p["span_start"], reverse=True)

        result = text
        for patch in sorted_patches:
            start = patch["span_start"]
            end = patch["span_end"]
            replacement = patch["replacement_text"]
            result = result[:start] + replacement + result[end:]

        return result


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_TEXT = "角色A在地点Y发现了物品X。角色B对此毫不知情。事件E即将发生。"


@pytest.fixture
def merger() -> PatchMerger:
    return PatchMerger()


# ---------------------------------------------------------------------------
# 1. 非重叠补丁合并
# ---------------------------------------------------------------------------

class TestMergeNonOverlapping:
    def test_merge_with_non_overlapping_patches(self, merger: PatchMerger):
        patches = [
            {"patch_id": "p1", "span_start": 0, "span_end": 6, "replacement_text": "角色A于地点Y", "original_text": "角色A在地点Y"},
            {"patch_id": "p2", "span_start": 20, "span_end": 25, "replacement_text": "毫不知晓", "original_text": "毫不知情"},
        ]

        groups = merger.group_conflicts(patches)
        assert len(groups) == 2
        assert len(groups[0]) == 1
        assert len(groups[1]) == 1


# ---------------------------------------------------------------------------
# 2. 重叠补丁检测
# ---------------------------------------------------------------------------

class TestMergeOverlapping:
    def test_merge_detects_overlapping_patches(self, merger: PatchMerger):
        patches = [
            {"patch_id": "p1", "span_start": 0, "span_end": 10, "replacement_text": "AAA", "original_text": "角色A在地点Y发现"},
            {"patch_id": "p2", "span_start": 5, "span_end": 15, "replacement_text": "BBB", "original_text": "地点Y发现了物品X"},
        ]

        assert merger.detect_overlap(patches[0], patches[1]) is True

        groups = merger.group_conflicts(patches)
        # 两个重叠的补丁应在同一组
        assert len(groups) == 1
        assert len(groups[0]) == 2


# ---------------------------------------------------------------------------
# 3. 分组冲突
# ---------------------------------------------------------------------------

class TestGroupConflicts:
    def test_merge_groups_conflicts_correctly(self, merger: PatchMerger):
        patches = [
            {"patch_id": "p1", "span_start": 0, "span_end": 10, "replacement_text": "A", "original_text": ""},
            {"patch_id": "p2", "span_start": 5, "span_end": 15, "replacement_text": "B", "original_text": ""},
            {"patch_id": "p3", "span_start": 30, "span_end": 40, "replacement_text": "C", "original_text": ""},
            {"patch_id": "p4", "span_start": 35, "span_end": 45, "replacement_text": "D", "original_text": ""},
        ]

        groups = merger.group_conflicts(patches)
        assert len(groups) == 2
        assert len(groups[0]) == 2  # p1, p2 overlap
        assert len(groups[1]) == 2  # p3, p4 overlap


# ---------------------------------------------------------------------------
# 4. apply_merged 逆序应用
# ---------------------------------------------------------------------------

class TestApplyMerged:
    def test_apply_merged_applies_in_reverse_order(self, merger: PatchMerger):
        text = "0123456789"
        patches = [
            {"span_start": 0, "span_end": 2, "replacement_text": "AB"},
            {"span_start": 5, "span_end": 7, "replacement_text": "CD"},
        ]

        result = merger.apply_merged(text, patches)
        # 从后往前应用：先 [5:7] -> CD, 再 [0:2] -> AB
        assert result == "AB234CD789"

    def test_apply_merged_preserves_text_integrity(self, merger: PatchMerger):
        text = SAMPLE_TEXT
        start = text.index("在地点Y")
        patches = [
            {"span_start": start, "span_end": start + 1, "replacement_text": "于"},
        ]

        result = merger.apply_merged(text, patches)
        # "地点Y" 中 "在" -> "于"
        assert "于地点Y" in result
        # 其余文本应保持不变
        assert "物品X" in result
        assert "角色B" in result


# ---------------------------------------------------------------------------
# 5. detect_overlap 边界情况
# ---------------------------------------------------------------------------

class TestDetectOverlap:
    def test_detect_overlap_with_adjacent_patches(self, merger: PatchMerger):
        """相邻但不重叠的补丁。"""
        p1 = {"span_start": 0, "span_end": 10}
        p2 = {"span_start": 10, "span_end": 20}

        # 相邻（end == start）不算重叠
        assert merger.detect_overlap(p1, p2) is False

    def test_detect_overlap_with_contained_patches(self, merger: PatchMerger):
        """一个补丁完全包含另一个。"""
        p1 = {"span_start": 0, "span_end": 20}
        p2 = {"span_start": 5, "span_end": 10}

        assert merger.detect_overlap(p1, p2) is True


# ---------------------------------------------------------------------------
# 6. 空补丁列表
# ---------------------------------------------------------------------------

class TestEmptyPatches:
    def test_empty_patches_list(self, merger: PatchMerger):
        groups = merger.group_conflicts([])
        assert groups == []

    def test_apply_merged_with_empty_patches(self, merger: PatchMerger):
        result = merger.apply_merged(SAMPLE_TEXT, [])
        assert result == SAMPLE_TEXT
