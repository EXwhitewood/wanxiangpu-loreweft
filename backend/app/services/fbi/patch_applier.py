"""补丁应用器：将验证通过的补丁安全地应用到文本。

核心功能：
- 按逆序应用补丁以保持偏移量正确
- 哈希校验防止并发修改
- 重叠补丁检测与拒绝
- 补丁变基（rebase）支持手动编辑后重新定位
"""

from __future__ import annotations

import logging
from difflib import SequenceMatcher

_logger = logging.getLogger(__name__)


class PatchApplier:
    """补丁应用服务：将 RepairPatch 列表安全应用到基础文本。"""

    async def apply_patches(
        self,
        text: str,
        patches: list,
        base_text_hash: str,
    ) -> dict:
        """将补丁列表应用到文本。

        按逆序（从末尾到开头）应用补丁，以保持偏移量正确。

        Args:
            text: 基础文本
            patches: RepairPatch 列表
            base_text_hash: 期望的基础文本哈希

        Returns:
            dict with: success (bool), new_text (str), new_hash (str),
                       applied_count (int), conflicts (list[str])
        """
        from app.models.fbi_repair import RepairPatch, compute_text_hash

        # 验证基础文本哈希
        actual_hash = compute_text_hash(text)
        if actual_hash != base_text_hash:
            _logger.warning(
                "apply_patches: base_text_hash 不匹配, expected=%s actual=%s",
                base_text_hash, actual_hash,
            )
            return {
                "success": False,
                "new_text": text,
                "new_hash": actual_hash,
                "applied_count": 0,
                "conflicts": [f"base_text_hash 不匹配: expected={base_text_hash}, actual={actual_hash}"],
            }

        if not patches:
            return {
                "success": True,
                "new_text": text,
                "new_hash": actual_hash,
                "applied_count": 0,
                "conflicts": [],
            }

        # 过滤有效补丁
        valid_patches: list[RepairPatch] = []
        for p in patches:
            if not isinstance(p, RepairPatch):
                _logger.warning("apply_patches: 跳过非 RepairPatch 对象")
                continue
            valid_patches.append(p)

        if not valid_patches:
            return {
                "success": True,
                "new_text": text,
                "new_hash": actual_hash,
                "applied_count": 0,
                "conflicts": [],
            }

        # 检测重叠补丁
        sorted_patches = sorted(valid_patches, key=lambda p: (p.span.start, p.span.end))
        conflicts: list[str] = []
        non_overlapping: list[RepairPatch] = []

        for i, patch in enumerate(sorted_patches):
            overlap = False
            for applied in non_overlapping:
                # 两个区间 [a.start, a.end) 和 [b.start, b.end) 重叠条件
                if patch.span.start < applied.span.end and patch.span.end > applied.span.start:
                    conflicts.append(
                        f"补丁重叠: patch_id={patch.patch_id} [{patch.span.start},{patch.span.end}) "
                        f"与 patch_id={applied.patch_id} [{applied.span.start},{applied.span.end})"
                    )
                    overlap = True
                    break
            if not overlap:
                non_overlapping.append(patch)

        if conflicts:
            _logger.warning("apply_patches: 检测到 %d 个重叠冲突", len(conflicts))
            return {
                "success": False,
                "new_text": text,
                "new_hash": actual_hash,
                "applied_count": 0,
                "conflicts": conflicts,
            }

        # 按逆序应用补丁（从末尾到开头）
        reverse_sorted = sorted(non_overlapping, key=lambda p: p.span.start, reverse=True)
        result_text = text
        applied_count = 0

        for patch in reverse_sorted:
            start = patch.span.start
            end = patch.span.end

            # 边界检查
            if start < 0 or end > len(result_text) or start > end:
                conflicts.append(
                    f"补丁 span 越界: patch_id={patch.patch_id} [{start},{end}), "
                    f"文本长度={len(result_text)}"
                )
                continue

            # 验证原文匹配
            actual = result_text[start:end]
            if actual != patch.original_text:
                conflicts.append(
                    f"原文不匹配: patch_id={patch.patch_id}, "
                    f"span 处='{actual[:50]}', 期望='{patch.original_text[:50]}'"
                )
                continue

            # 应用替换
            result_text = result_text[:start] + patch.replacement_text + result_text[end:]
            applied_count += 1
            _logger.debug(
                "apply_patches: applied patch_id=%s [%d,%d) -> '%s'",
                patch.patch_id, start, end, patch.replacement_text[:30],
            )

        new_hash = compute_text_hash(result_text)

        _logger.info(
            "apply_patches: applied %d/%d patches, conflicts=%d",
            applied_count, len(valid_patches), len(conflicts),
        )

        return {
            "success": applied_count == len(valid_patches) and len(conflicts) == 0,
            "new_text": result_text,
            "new_hash": new_hash,
            "applied_count": applied_count,
            "conflicts": conflicts,
        }

    async def rebase_patches(
        self,
        patches: list,
        old_text: str,
        new_text: str,
    ) -> list:
        """当用户手动编辑文本后，尝试将补丁重新定位到新文本。

        使用模糊匹配来重新定位 span 位置。如果补丁的 original_text
        可以在新文本中找到，则更新其 span；否则标记为 rebase_failed。

        Args:
            patches: 需要变基的 RepairPatch 列表
            old_text: 原始文本
            new_text: 用户编辑后的新文本

        Returns:
            更新后的 RepairIssue 列表（部分可能标记 rebase_failed）
        """
        from app.models.fbi_repair import RepairPatch, TextSpan, compute_text_hash

        if old_text == new_text:
            return list(patches)

        rebased: list[RepairPatch] = []

        for patch in patches:
            if not isinstance(patch, RepairPatch):
                continue

            original_text = patch.original_text
            if not original_text:
                # 空原文无法定位，标记失败
                rebased.append(patch.model_copy(update={
                    "self_audit": {**patch.self_audit, "rebase_failed": True, "rebase_reason": "empty_original_text"},
                }))
                continue

            # 精确查找
            exact_pos = new_text.find(original_text)
            if exact_pos >= 0:
                new_span = TextSpan(start=exact_pos, end=exact_pos + len(original_text), label=patch.span.label)
                rebased.append(patch.model_copy(update={
                    "span": new_span,
                    "base_text_hash": compute_text_hash(new_text),
                    "self_audit": {**patch.self_audit, "rebased": True},
                }))
                _logger.debug(
                    "rebase_patches: exact match for patch_id=%s at pos=%d",
                    patch.patch_id, exact_pos,
                )
                continue

            # 模糊匹配：使用 SequenceMatcher 找到最佳匹配位置
            best_pos = -1
            best_ratio = 0.0
            search_len = len(original_text)
            step = max(1, search_len // 4)

            for offset in range(0, len(new_text) - search_len + 1, step):
                candidate = new_text[offset:offset + search_len]
                ratio = SequenceMatcher(None, original_text, candidate).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_pos = offset

            # 也检查原始 span 位置附近
            orig_start = patch.span.start
            if orig_start <= len(new_text):
                search_start = max(0, orig_start - search_len)
                search_end = min(len(new_text), orig_start + search_len * 2)
                for offset in range(search_start, min(search_end, len(new_text) - search_len + 1)):
                    candidate = new_text[offset:offset + search_len]
                    ratio = SequenceMatcher(None, original_text, candidate).ratio()
                    if ratio > best_ratio:
                        best_ratio = ratio
                        best_pos = offset

            if best_ratio >= 0.8 and best_pos >= 0:
                # 模糊匹配成功，但需要调整 original_text 以匹配新文本
                matched_text = new_text[best_pos:best_pos + search_len]
                new_span = TextSpan(start=best_pos, end=best_pos + len(matched_text), label=patch.span.label)
                rebased.append(patch.model_copy(update={
                    "span": new_span,
                    "original_text": matched_text,
                    "base_text_hash": compute_text_hash(new_text),
                    "self_audit": {
                        **patch.self_audit,
                        "rebased": True,
                        "rebase_method": "fuzzy",
                        "rebase_ratio": round(best_ratio, 3),
                    },
                }))
                _logger.debug(
                    "rebase_patches: fuzzy match for patch_id=%s at pos=%d ratio=%.3f",
                    patch.patch_id, best_pos, best_ratio,
                )
            else:
                # 变基失败
                rebased.append(patch.model_copy(update={
                    "self_audit": {
                        **patch.self_audit,
                        "rebase_failed": True,
                        "rebase_reason": f"no_match_found best_ratio={best_ratio:.3f}",
                    },
                }))
                _logger.warning(
                    "rebase_patches: rebase failed for patch_id=%s best_ratio=%.3f",
                    patch.patch_id, best_ratio,
                )

        return rebased
