"""Phase U-A: EditWindowGrouper — 同窗口问题聚合器。

在蓝图生成前完成同窗口聚合，把多个 violation 归入同一个 EditWindowCase。
这是 FBI 修复主链从 issue-first 切换到 window-first 的核心组件。

聚合规则（满足任一即归入同窗口）：
- target_span / evidence_span 字符区间重叠（overlap_ratio >= 0.25）
- span 间距小于窗口阈值（span_gap_chars <= 80）
- target_span 文本相同
- evidence_span 文本相同
- 同一段落 paragraph_index 相同
- 同一个 route anchor 相同
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from app.models.chapter_review import EditWindowCase

_logger = logging.getLogger(__name__)

# 第一版阈值
_OVERLAP_RATIO_THRESHOLD = 0.25
_SPAN_GAP_CHARS_THRESHOLD = 80
_MAX_WINDOW_CHARS = 500  # 风险控制：单个窗口最大长度


def _hash(value: str) -> str:
    return hashlib.md5((value or "").encode("utf-8")).hexdigest()[:12]


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value or "")


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _violation_scene(violation: dict[str, Any]) -> int | None:
    for key in ("scene_index", "source_scene", "owner_scene"):
        scene = _as_int(violation.get(key))
        if scene is not None:
            return scene
    target_scenes = violation.get("target_scenes") or []
    if isinstance(target_scenes, list) and target_scenes:
        return _as_int(target_scenes[0])
    return None


def _violation_issue_id(violation: dict[str, Any]) -> str:
    return str(
        violation.get("issue_id")
        or violation.get("violation_id")
        or violation.get("id")
        or ""
    )


def _violation_metric(violation: dict[str, Any]) -> str:
    return str(
        violation.get("metric")
        or violation.get("type")
        or violation.get("violation_type")
        or ""
    ).lower()


def _violation_span(violation: dict[str, Any], key: str = "target_span") -> tuple[int, int] | None:
    """提取 violation 的字符 span（start, end）。"""
    span_start = _as_int(violation.get("span_start"))
    span_end = _as_int(violation.get("span_end"))
    if span_start is not None and span_end is not None and span_end >= span_start:
        return (span_start, span_end)
    # 尝试从 target_span_start/target_span_end 等变体读取
    for prefix in ("target_span", "evidence_span", "anchor"):
        start = _as_int(violation.get(f"{prefix}_start"))
        end = _as_int(violation.get(f"{prefix}_end"))
        if start is not None and end is not None and end >= start:
            return (start, end)
    return None


def _violation_span_text(violation: dict[str, Any], key: str) -> str:
    """提取 violation 的 span 文本。"""
    value = violation.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        text = value.get("text") or value.get("span")
        if isinstance(text, str) and text.strip():
            return text.strip()
    return ""


def _violation_paragraph_index(violation: dict[str, Any]) -> int | None:
    return _as_int(violation.get("paragraph_index"))


def _violation_anchor(violation: dict[str, Any]) -> str:
    """提取 violation 的 route anchor 签名。"""
    for key in ("anchor_text", "anchor", "primary_anchor"):
        value = violation.get(key)
        if isinstance(value, str) and value.strip():
            return _compact(value)[:120]
    # 回退到 target_span / evidence_span
    for key in ("target_span", "evidence_span"):
        text = _violation_span_text(violation, key)
        if text:
            return _compact(text)[:120]
    return ""


def _span_overlap_ratio(a: tuple[int, int], b: tuple[int, int]) -> float:
    """计算两个 span 的重叠比例（重叠长度 / 较短 span 长度）。"""
    overlap_start = max(a[0], b[0])
    overlap_end = min(a[1], b[1])
    overlap = max(0, overlap_end - overlap_start)
    if overlap == 0:
        return 0.0
    min_len = min(a[1] - a[0], b[1] - b[0])
    if min_len <= 0:
        return 0.0
    return overlap / min_len


def _span_gap_chars(a: tuple[int, int], b: tuple[int, int]) -> int:
    """计算两个 span 之间的字符间距（不重叠时间隔）。"""
    if a[1] <= b[0]:
        return b[0] - a[1]
    if b[1] <= a[0]:
        return a[0] - b[1]
    return 0  # 重叠


def _violations_share_window(
    v1: dict[str, Any],
    v2: dict[str, Any],
) -> bool:
    """判断两个 violation 是否应归入同一个 EditWindowCase。"""
    # 不同 scene 不聚合
    scene1 = _violation_scene(v1)
    scene2 = _violation_scene(v2)
    if scene1 is not None and scene2 is not None and scene1 != scene2:
        return False

    # 1. span 区间重叠
    span1 = _violation_span(v1)
    span2 = _violation_span(v2)
    if span1 and span2:
        if _span_overlap_ratio(span1, span2) >= _OVERLAP_RATIO_THRESHOLD:
            return True
        if _span_gap_chars(span1, span2) <= _SPAN_GAP_CHARS_THRESHOLD:
            return True

    # 2. target_span 文本相同
    target1 = _violation_span_text(v1, "target_span")
    target2 = _violation_span_text(v2, "target_span")
    if target1 and target2 and _compact(target1) == _compact(target2):
        return True

    # 3. evidence_span 文本相同
    evidence1 = _violation_span_text(v1, "evidence_span")
    evidence2 = _violation_span_text(v2, "evidence_span")
    if evidence1 and evidence2 and _compact(evidence1) == _compact(evidence2):
        return True

    # 4. 同一段落
    para1 = _violation_paragraph_index(v1)
    para2 = _violation_paragraph_index(v2)
    if para1 is not None and para2 is not None and para1 == para2:
        return True

    # 5. 同一个 route anchor
    anchor1 = _violation_anchor(v1)
    anchor2 = _violation_anchor(v2)
    if anchor1 and anchor2 and anchor1 == anchor2:
        return True

    return False


class EditWindowGrouper:
    """Phase U-A: 把 violations 聚合为 EditWindowCase 列表。

    在蓝图生成前完成同窗口聚合，确保同窗口多个 issue 只产生一个 compound blueprint。
    """

    def group_violations(
        self,
        violations: list[dict[str, Any]],
        scene_texts: dict[int, str] | None = None,
        *,
        case_id: str = "",
    ) -> list[EditWindowCase]:
        """把 violations 聚合为 EditWindowCase 列表。

        Args:
            violations: normalized violations 列表。
            scene_texts: scene_index -> 场景正文映射，用于提取 window_text。
            case_id: 案件 ID，用于生成 stable window_id。

        Returns:
            EditWindowCase 列表，每个 case 包含同窗口的所有 issue_ids。
        """
        scene_texts = scene_texts or {}
        if not violations:
            return []

        # 按 scene 分组，再在同 scene 内做窗口聚合
        by_scene: dict[int | None, list[dict[str, Any]]] = {}
        for v in violations:
            scene = _violation_scene(v)
            by_scene.setdefault(scene, []).append(v)

        cases: list[EditWindowCase] = []
        for scene, scene_violations in by_scene.items():
            scene_cases = self._group_scene_violations(
                scene_violations,
                scene_texts.get(scene, "") if scene is not None else "",
                case_id=case_id,
                scene_index=scene,
            )
            cases.extend(scene_cases)
        return cases

    def _group_scene_violations(
        self,
        violations: list[dict[str, Any]],
        scene_text: str,
        *,
        case_id: str,
        scene_index: int | None,
    ) -> list[EditWindowCase]:
        """对同一 scene 内的 violations 做窗口聚合。"""
        if not violations:
            return []

        # 贪心并查集：把应归入同窗口的 violation 合并
        groups: list[list[dict[str, Any]]] = []
        for v in violations:
            placed = False
            for group in groups:
                if any(_violations_share_window(v, existing) for existing in group):
                    group.append(v)
                    placed = True
                    break
            if not placed:
                groups.append([v])

        cases: list[EditWindowCase] = []
        for group_idx, group in enumerate(groups):
            case = self._build_case(
                group,
                scene_text,
                case_id=case_id,
                scene_index=scene_index,
                group_idx=group_idx,
            )
            cases.append(case)
        return cases

    def _build_case(
        self,
        violations: list[dict[str, Any]],
        scene_text: str,
        *,
        case_id: str,
        scene_index: int | None,
        group_idx: int,
    ) -> EditWindowCase:
        """从一组同窗口 violations 构造 EditWindowCase。"""
        issue_ids: list[str] = []
        metrics: list[str] = []
        families: list[str] = []
        source_order_ids: list[str] = []
        for v in violations:
            issue_id = _violation_issue_id(v)
            if issue_id and issue_id not in issue_ids:
                issue_ids.append(issue_id)
            metric = _violation_metric(v)
            if metric and metric not in metrics:
                metrics.append(metric)
            family = str(v.get("issue_family") or v.get("repair_family") or "")
            if family and family not in families:
                families.append(family)
            order_id = str(v.get("order_id") or "")
            if order_id and order_id not in source_order_ids:
                source_order_ids.append(order_id)

        # 计算 window span
        window_start: int | None = None
        window_end: int | None = None
        for v in violations:
            span = _violation_span(v)
            if span:
                if window_start is None or span[0] < window_start:
                    window_start = span[0]
                if window_end is None or span[1] > window_end:
                    window_end = span[1]

        # 提取 window_text
        window_text = ""
        if scene_text and window_start is not None and window_end is not None:
            window_text = scene_text[window_start:window_end]
        elif scene_text:
            # 回退：用第一个 violation 的 target_span 在正文中定位
            for v in violations:
                target = _violation_span_text(v, "target_span")
                if target and target in scene_text:
                    idx = scene_text.find(target)
                    window_start = idx
                    window_end = idx + len(target)
                    window_text = scene_text[window_start:window_end]
                    break

        # 风险控制：窗口过长时截断
        if len(window_text) > _MAX_WINDOW_CHARS:
            _logger.warning(
                "EditWindowGrouper: window_text exceeds max %d chars (got %d), truncating",
                _MAX_WINDOW_CHARS,
                len(window_text),
            )

        # 生成 stable window_id
        window_id = _hash(
            f"{case_id}:scene:{scene_index}:group:{group_idx}:issues:{','.join(issue_ids)}"
        )

        return EditWindowCase(
            window_id=window_id,
            scene_index=scene_index,
            base_text_hash=_hash(scene_text),
            window_start=window_start,
            window_end=window_end,
            window_text=window_text,
            issue_ids=issue_ids,
            metrics=metrics,
            issue_families=families,
            source_order_ids=source_order_ids,
            blocking_level="blocking",
        )

    def covered_issue_ids(self, cases: list[EditWindowCase]) -> set[str]:
        """返回所有被 EditWindowCase 覆盖的 issue_ids。"""
        covered: set[str] = set()
        for case in cases:
            covered.update(case.issue_ids)
        return covered
