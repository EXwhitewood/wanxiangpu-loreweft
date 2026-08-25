"""补丁合并器——合并非重叠补丁，仲裁重叠补丁。

核心功能：
- 检测补丁之间的 span 重叠
- 将重叠补丁分组为冲突组
- 非重叠补丁直接进入合并列表
- 按逆序应用已合并补丁以保持偏移量正确
- 9.3.1: 冲突分类和自动仲裁（8 类冲突），不可仲裁的冲突回流到 EditWindowPlanner
"""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)


class ConflictType:
    """9.3.1: 冲突分类常量。"""

    DUPLICATE_PATCH = "duplicate_patch"
    CONTAINED_PATCH = "contained_patch"
    SURFACE_CLEANUP_INSIDE_REWRITE = "surface_cleanup_inside_rewrite"
    INSERT_SAME_ANCHOR = "insert_same_anchor"
    OVERLAP_SAME_FAMILY = "overlap_same_family"
    OVERLAP_DIFFERENT_FAMILY = "overlap_different_family"
    PROTECTED_SPAN_OVERLAP = "protected_span_overlap"
    UNSAFE_CONFLICT = "unsafe_conflict"


# 表面清理类 strategy（破折号清理、标点规范化等），可被重写补丁吸收
_SURFACE_CLEANUP_STRATEGIES = {
    "normalize_punctuation",
    "cleanup_ai_flavor_window",
    "trim_discourse_window",
    "normalize_structure_words",
    "replace_tier1_ai_flavor_terms",
    "dash_cleanup",
    "punctuation_cleanup",
}


class PatchMerger:
    """补丁合并器——合并非重叠补丁，仲裁重叠补丁。"""

    def merge(self, patches: list, base_text: str) -> dict:
        """合并补丁列表。

        Args:
            patches: RepairPatch 列表
            base_text: 基础文本

        Returns:
            dict:
                merged_patches: list[RepairPatch]  # 可安全应用的补丁
                conflicts: list[dict]  # 冲突的补丁对
                conflict_groups: list[list[str]]  # 冲突组（每组内补丁互相冲突）
                compound_reflow: list[dict]  # 9.3.1 新增：需要回流到 EditWindowPlanner 的冲突
        """
        from app.models.fbi_repair import RepairPatch

        if not patches:
            return {
                "merged_patches": [],
                "conflicts": [],
                "conflict_groups": [],
                "compound_reflow": [],
            }

        # 过滤有效补丁
        valid_patches: list[RepairPatch] = []
        seen_patch_keys: set[tuple] = set()
        for p in patches:
            if not isinstance(p, RepairPatch):
                _logger.warning("PatchMerger.merge: 跳过非 RepairPatch 对象")
                continue
            key = (
                p.base_text_hash,
                p.span.start,
                p.span.end,
                p.original_text,
                p.replacement_text,
            )
            if key in seen_patch_keys:
                continue
            seen_patch_keys.add(key)
            valid_patches.append(p)

        if not valid_patches:
            return {
                "merged_patches": [],
                "conflicts": [],
                "conflict_groups": [],
                "compound_reflow": [],
            }

        # 1. 按 span start 排序
        sorted_patches = sorted(valid_patches, key=lambda p: (p.span.start, p.span.end))

        # 2. 检测重叠并构建冲突图
        overlap_pairs: list[tuple[int, int]] = []  # (index_a, index_b)
        for i in range(len(sorted_patches)):
            for j in range(i + 1, len(sorted_patches)):
                if self.detect_overlap(sorted_patches[i], sorted_patches[j]):
                    overlap_pairs.append((i, j))

        # 3. 用 Union-Find 将重叠补丁分组为冲突组
        parent = list(range(len(sorted_patches)))

        def find(x: int) -> int:
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        def union(a: int, b: int) -> None:
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb

        for i, j in overlap_pairs:
            union(i, j)

        # 收集冲突组
        groups: dict[int, list[int]] = {}
        for idx in range(len(sorted_patches)):
            root = find(idx)
            if root not in groups:
                groups[root] = []
            groups[root].append(idx)

        # 4. 分离：单元素组为非冲突，多元素组为冲突组
        merged_patches: list[RepairPatch] = []
        conflicts: list[dict] = []
        conflict_groups: list[list[str]] = []
        compound_reflow: list[dict] = []  # 9.3.1 新增：需要回流的冲突

        for root, members in groups.items():
            if len(members) == 1:
                # 非冲突补丁
                merged_patches.append(sorted_patches[members[0]])
            else:
                # 冲突组：9.3.1 分类和自动仲裁
                group_patches = [sorted_patches[m] for m in members]
                conflict_type = self._classify_conflict(group_patches, base_text)

                resolved = None
                if conflict_type == ConflictType.DUPLICATE_PATCH:
                    resolved = self._resolve_duplicate(group_patches)
                elif conflict_type == ConflictType.CONTAINED_PATCH:
                    resolved = self._resolve_contained(group_patches, base_text)
                elif conflict_type == ConflictType.SURFACE_CLEANUP_INSIDE_REWRITE:
                    resolved = self._resolve_surface_cleanup(group_patches, base_text)
                elif conflict_type == ConflictType.INSERT_SAME_ANCHOR:
                    resolved = self._resolve_insert_same_anchor(group_patches)

                if resolved is not None:
                    merged_patches.append(resolved)
                    _logger.info(
                        "PatchMerger.merge: 自动仲裁冲突 type=%s, %d patches -> 1 resolved",
                        conflict_type, len(group_patches),
                    )
                else:
                    # 不可自动仲裁，回流到 EditWindowPlanner 做 compound patch
                    reflow_issue_ids = list(set(
                        iid
                        for p in group_patches
                        for iid in (p.resolves_issue_ids or [])
                    ))
                    compound_reflow.append({
                        "conflict_type": conflict_type,
                        "patch_ids": [p.patch_id for p in group_patches],
                        "resolves_issue_ids": reflow_issue_ids,
                        "needs_compound_patch": True,
                    })
                    conflict_groups.append([p.patch_id for p in group_patches])

                # 记录冲突对（无论是否仲裁成功）
                for mi in range(len(members)):
                    for mj in range(mi + 1, len(members)):
                        pa = sorted_patches[members[mi]]
                        pb = sorted_patches[members[mj]]
                        conflicts.append({
                            "patch_a_id": pa.patch_id,
                            "patch_a_span": {"start": pa.span.start, "end": pa.span.end},
                            "patch_b_id": pb.patch_id,
                            "patch_b_span": {"start": pb.span.start, "end": pb.span.end},
                            "conflict_type": conflict_type,
                            "auto_resolved": resolved is not None,
                        })

        _logger.info(
            "PatchMerger.merge: %d patches -> %d merged, %d conflicts, %d conflict_groups, %d reflow",
            len(valid_patches), len(merged_patches), len(conflicts), len(conflict_groups), len(compound_reflow),
        )

        return {
            "merged_patches": merged_patches,
            "conflicts": conflicts,
            "conflict_groups": conflict_groups,
            "compound_reflow": compound_reflow,
        }

    @staticmethod
    def detect_overlap(patch_a, patch_b) -> bool:
        """检测两个补丁是否重叠。

        两个区间 [a.start, a.end) 和 [b.start, b.end) 重叠条件：
        a.start < b.end and b.start < a.end
        """
        if patch_a.span is None or patch_b.span is None:
            return False
        return patch_a.span.start < patch_b.span.end and patch_b.span.start < patch_a.span.end

    # ------------------------------------------------------------------
    # 9.3.1: 冲突分类和自动仲裁
    # ------------------------------------------------------------------

    def _classify_conflict(self, group_patches: list, base_text: str) -> str:
        """9.3.1: 按规则分类冲突类型。"""
        if len(group_patches) < 2:
            return ConflictType.UNSAFE_CONFLICT

        # 检查 protected span 重叠
        for p in group_patches:
            if getattr(p, "touches_protected_span_ids", None):
                return ConflictType.PROTECTED_SPAN_OVERLAP

        # 检查完全相同（duplicate）
        if self._patches_identical(group_patches):
            return ConflictType.DUPLICATE_PATCH

        # 检查包含关系
        if self._patches_contained(group_patches):
            # 如果被包含的是表面清理，标记为 surface_cleanup_inside_rewrite
            if self._is_surface_cleanup(group_patches):
                return ConflictType.SURFACE_CLEANUP_INSIDE_REWRITE
            return ConflictType.CONTAINED_PATCH

        # 检查同 anchor insert（original_text 为空表示 insert 类补丁）
        if all(not p.original_text for p in group_patches):
            anchors = set()
            for p in group_patches:
                # insert 类补丁的 anchor 信息在 self_audit 或 strategy 中
                anchor = ""
                audit = p.self_audit or {}
                if isinstance(audit, dict):
                    anchor = str(audit.get("anchor_text") or audit.get("anchor") or "")
                if not anchor:
                    anchor = str(p.strategy or "")
                anchors.add(anchor)
            if len(anchors) == 1:
                return ConflictType.INSERT_SAME_ANCHOR

        # 检查 family（用 strategy 字段代替 repair_family）
        strategies = [str(p.strategy or "") for p in group_patches if p.strategy]
        if len(set(strategies)) == 1 and strategies:
            return ConflictType.OVERLAP_SAME_FAMILY
        return ConflictType.OVERLAP_DIFFERENT_FAMILY

    @staticmethod
    def _patches_identical(group_patches: list) -> bool:
        """检查所有补丁是否完全相同（span + original_text + replacement_text）。"""
        if len(group_patches) < 2:
            return False
        first = group_patches[0]
        for p in group_patches[1:]:
            if p.span.start != first.span.start or p.span.end != first.span.end:
                return False
            if p.original_text != first.original_text:
                return False
            if p.replacement_text != first.replacement_text:
                return False
        return True

    @staticmethod
    def _patches_contained(group_patches: list) -> bool:
        """检查是否存在包含关系：一个补丁的 span 完全包含另一个。"""
        if len(group_patches) < 2:
            return False
        for i in range(len(group_patches)):
            for j in range(len(group_patches)):
                if i == j:
                    continue
                outer = group_patches[i]
                inner = group_patches[j]
                if (
                    outer.span.start <= inner.span.start
                    and outer.span.end >= inner.span.end
                    and (outer.span.start < inner.span.start or outer.span.end > inner.span.end)
                ):
                    return True
        return False

    @staticmethod
    def _is_surface_cleanup(group_patches: list) -> bool:
        """检查被包含的补丁是否是表面清理类（strategy 在 _SURFACE_CLEANUP_STRATEGIES 中）。"""
        # 找到被包含的（inner）补丁，检查其 strategy
        for i in range(len(group_patches)):
            for j in range(len(group_patches)):
                if i == j:
                    continue
                outer = group_patches[i]
                inner = group_patches[j]
                if (
                    outer.span.start <= inner.span.start
                    and outer.span.end >= inner.span.end
                    and (outer.span.start < inner.span.start or outer.span.end > inner.span.end)
                ):
                    if str(inner.strategy or "") in _SURFACE_CLEANUP_STRATEGIES:
                        return True
        return False

    @staticmethod
    def _resolve_duplicate(group_patches: list):
        """仲裁 duplicate：保留第一个，丢弃其余。"""
        if not group_patches:
            return None
        return group_patches[0]

    def _resolve_contained(self, group_patches: list, base_text: str):
        """仲裁 contained：保留覆盖范围最大的补丁（外层）。"""
        if not group_patches:
            return None
        # 按 span 范围大小排序，保留最大的
        sorted_by_span = sorted(
            group_patches,
            key=lambda p: (p.span.end - p.span.start),
            reverse=True,
        )
        return sorted_by_span[0]

    def _resolve_surface_cleanup(self, group_patches: list, base_text: str):
        """仲裁 surface_cleanup_inside_rewrite：保留重写补丁，把表面清理的效果吸收到 replacement 中。

        表面清理通常是删除某些字符（如破折号），在重写后的文本上应用同样的清理即可。
        """
        if len(group_patches) < 2:
            return group_patches[0] if group_patches else None
        # 找到重写补丁（非表面清理、span 最大的）
        rewrite_patches = [
            p for p in group_patches
            if str(p.strategy or "") not in _SURFACE_CLEANUP_STRATEGIES
        ]
        if not rewrite_patches:
            # 全是表面清理，保留范围最大的
            return self._resolve_contained(group_patches, base_text)
        # 取范围最大的重写补丁作为基础
        rewrite = max(rewrite_patches, key=lambda p: p.span.end - p.span.start)
        # 对表面清理补丁，尝试把其效果应用到 rewrite.replacement_text
        from app.models.fbi_repair import RepairPatch
        new_replacement = rewrite.replacement_text
        for p in group_patches:
            if p is rewrite:
                continue
            if str(p.strategy or "") not in _SURFACE_CLEANUP_STRATEGIES:
                continue
            # 表面清理补丁：从 new_replacement 中删除 original_text 中的字符
            # 简单策略：如果 original_text 的某些字符在 new_replacement 中出现，删除它们
            # 但这太激进，所以只做：如果 original_text 是 new_replacement 的子串，删除它
            if p.original_text and p.original_text in new_replacement:
                new_replacement = new_replacement.replace(p.original_text, p.replacement_text or "")
        # 构造合并后的补丁
        resolved = rewrite.model_copy(deep=True)
        resolved.replacement_text = new_replacement
        resolved.resolves_issue_ids = list(set(
            iid
            for p in group_patches
            for iid in (p.resolves_issue_ids or [])
        ))
        return resolved

    @staticmethod
    def _resolve_insert_same_anchor(group_patches: list):
        """仲裁 insert_same_anchor：合并所有 insert 的 replacement_text。"""
        if len(group_patches) < 2:
            return group_patches[0] if group_patches else None
        from app.models.fbi_repair import RepairPatch
        # 按 span start 排序，保持顺序
        sorted_patches = sorted(group_patches, key=lambda p: p.span.start)
        base = sorted_patches[0].model_copy(deep=True)
        # 合并 replacement_text（用换行分隔，去重）
        seen_texts: set[str] = set()
        combined_parts: list[str] = []
        for p in sorted_patches:
            text = p.replacement_text or ""
            if text and text not in seen_texts:
                seen_texts.add(text)
                combined_parts.append(text)
        base.replacement_text = "\n".join(combined_parts)
        base.resolves_issue_ids = list(set(
            iid
            for p in sorted_patches
            for iid in (p.resolves_issue_ids or [])
        ))
        return base

    def apply_merged(self, text: str, merged_patches: list) -> dict:
        """应用已合并的非冲突补丁。

        按逆序（从末尾到开头）应用补丁，以保持偏移量正确。

        Args:
            text: 基础文本
            merged_patches: 已确认无冲突的 RepairPatch 列表

        Returns:
            dict: {"success": bool, "new_text": str, "applied_count": int}
        """
        from app.models.fbi_repair import RepairPatch, compute_text_hash

        if not merged_patches:
            return {
                "success": True,
                "new_text": text,
                "applied_count": 0,
            }

        valid_patches: list[RepairPatch] = []
        for p in merged_patches:
            if not isinstance(p, RepairPatch):
                _logger.warning("PatchMerger.apply_merged: 跳过非 RepairPatch 对象")
                continue
            valid_patches.append(p)

        if not valid_patches:
            return {
                "success": True,
                "new_text": text,
                "applied_count": 0,
            }

        # 按逆序应用补丁（从末尾到开头）
        reverse_sorted = sorted(valid_patches, key=lambda p: p.span.start, reverse=True)
        result_text = text
        applied_count = 0

        for patch in reverse_sorted:
            start = patch.span.start
            end = patch.span.end

            # 边界检查
            if start < 0 or end > len(result_text) or start > end:
                _logger.warning(
                    "PatchMerger.apply_merged: span 越界 patch_id=%s [%d,%d), text_len=%d",
                    patch.patch_id, start, end, len(result_text),
                )
                continue

            # 验证原文匹配
            actual = result_text[start:end]
            if actual != patch.original_text:
                _logger.warning(
                    "PatchMerger.apply_merged: 原文不匹配 patch_id=%s, "
                    "span处='%s', 期望='%s'",
                    patch.patch_id, actual[:50], patch.original_text[:50],
                )
                continue

            # 应用替换
            result_text = result_text[:start] + patch.replacement_text + result_text[end:]
            applied_count += 1

        _logger.info(
            "PatchMerger.apply_merged: applied %d/%d patches",
            applied_count, len(valid_patches),
        )

        return {
            "success": applied_count == len(valid_patches),
            "new_text": result_text,
            "applied_count": applied_count,
        }
