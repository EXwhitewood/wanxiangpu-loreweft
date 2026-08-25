"""修复工单编译器。

把 ReviewFindingV2 转成可执行 RepairOrder。
分离"发现问题"与"如何修复"。

修复路由：
| 问题类型 | 优先修复方式 |
|---|---|
| 篇幅过长 | 确定性压缩预算 + 段落压缩 |
| 篇幅过短 | 结尾扩写 / 指定段扩写 |
| must_show 超载 | 合同预算调整 |
| advisory | 自动确认 |
| 时间锚点冲突 | 确定性时间锚点替换，失败后升级局部重写 |
| 破折号过量 | 确定性标点降噪 + 文体复检 |
| 明显重复词 | 确定性去重 |
| 解释腔 | FBI 文体专员 |
| 事实冲突 | FBI 事实专员 |
| 常识/认知边界 | FBI 事实/因果专员 |
| 节奏不足 | FBI 节奏专员 |
| 结尾状态未达成 | 结尾补写 / 场景重构 |
| 风格冲突 | 风格守门员审核 |
"""
from __future__ import annotations

import hashlib
import logging
import re

from app.models.repair_order import RepairOrder
from app.models.review_finding_v2 import ReviewFindingV2

logger = logging.getLogger(__name__)


class RepairOrderCompiler:
    """修复工单编译器"""

    # repair_lane → operation 映射
    PREFLIGHT_SKIP_MARKER = "[skip_deterministic_preflight]"

    _LANE_OPERATION: dict[str, str] = {
        "deterministic": "compress_region",
        "fbi_fact": "replace_span",
        "fbi_prose": "replace_span",
        "fbi_pacing": "expand_region",
        "fbi_style_guard": "replace_span",
        "fbi_scene_restructure": "rewrite_scene",
        "fbi_ending": "append_tail",
        "manual_only": "adjust_contract",
        "none": "acknowledge",
    }

    # repair_lane → allowed_delta_chars 映射
    _LANE_DELTA: dict[str, int] = {
        "deterministic": 0,
        "fbi_fact": 100,
        "fbi_prose": 150,
        "fbi_pacing": 200,
        "fbi_style_guard": 100,
        "fbi_scene_restructure": 500,
        "fbi_ending": 300,
        "manual_only": 0,
        "none": 0,
    }

    _DISPATCH_LANES: set[str] = {
        "fbi_fact",
        "fbi_prose",
        "fbi_pacing",
        "fbi_style_guard",
        "fbi_scene_restructure",
        "fbi_ending",
        "manual_only",
    }

    _DISPATCH_OPERATIONS: set[str] = {
        "replace_span",
        "insert_after",
        "insert_bridge_action",
        "insert_bridge_hint",
        "rewrite_window",
        "append_tail",
        "rewrite_scene",
    }

    _TYPE_OPERATION: dict[str, tuple[str, str]] = {
        "timeline_conflict": ("deterministic", "repair_time_anchor"),
        "temporal_conflict": ("deterministic", "repair_time_anchor"),
        "ai_punctuation_artifact": ("deterministic", "clean_punctuation"),
        "explanatory_punctuation_artifact": ("deterministic", "clean_punctuation"),
        "repetition_artifact": ("deterministic", "remove_repetition"),
        "repeated_phrase": ("deterministic", "remove_repetition"),
        "forbidden_assertion_triggered": ("deterministic", "remove_forbidden_event"),
        "forbidden_recap_violation": ("deterministic", "remove_forbidden_event"),
        "forbidden_content": ("deterministic", "remove_forbidden_event"),
        "ending_state_not_reached": ("deterministic", "complete_ending_state"),
        "missing_must_show": ("deterministic", "complete_contract_item"),
        "fact_conflict": ("deterministic", "repair_fact_state"),
        "internal_conflict": ("deterministic", "repair_fact_state"),
        "identity_conflict": ("deterministic", "repair_fact_state"),
        "setting_conflict": ("deterministic", "repair_fact_state"),
        "spatial_conflict": ("deterministic", "repair_fact_state"),
        "spatial_consistency_error": ("deterministic", "repair_fact_state"),
        "ownership_conflict": ("deterministic", "repair_fact_state"),
        "event_repeated": ("deterministic", "repair_fact_state"),
        "causal_chain_error": ("deterministic", "repair_fact_state"),
        "fact_boundary_conflict": ("deterministic", "repair_fact_state"),
        "knowledge_boundary_violation": ("deterministic", "repair_fact_state"),
        "truth_layer_conflict": ("deterministic", "repair_fact_state"),
        "responsibility_polarity_conflict": ("deterministic", "repair_fact_state"),
        "narration_explanation_artifact": ("deterministic", "cleanup_ai_style"),
        "enforced_ai_flavor": ("deterministic", "cleanup_ai_style"),
        "emotion_expression_monotone": ("deterministic", "cleanup_ai_style"),
        "deslop_gate_A": ("deterministic", "cleanup_ai_style"),
        "deslop_gate_B": ("deterministic", "cleanup_ai_style"),
        "deslop_gate_C": ("deterministic", "cleanup_ai_style"),
    }

    def compile(self, finding: ReviewFindingV2) -> RepairOrder:
        """将 ReviewFindingV2 编译为 RepairOrder"""
        lane = finding.repair_lane
        scope = finding.repair_scope

        # 确定 operation
        operation = self._LANE_OPERATION.get(lane, "acknowledge")
        target_region: dict | None = self._extract_target_region(finding)
        instruction = finding.description
        intent_applied = False

        dispatch = self._dispatch_intent(finding)
        if dispatch:
            lane = dispatch["lane"]
            operation = dispatch["operation"]
            target_region = self._merge_dispatch_target(target_region, dispatch["intent"])
            instruction = dispatch["instruction"] or instruction
            intent_applied = True

        type_route = self._TYPE_OPERATION.get(finding.type)
        if not intent_applied and type_route and self._should_use_type_route(finding, target_region):
            lane, operation = type_route

        if finding.type == "forbidden_triggered":
            lane = "deterministic"
            operation = "delete_span"
            for evidence in finding.evidence_spans:
                if isinstance(evidence, dict):
                    target_text = (
                        evidence.get("span")
                        or evidence.get("text")
                        or evidence.get("evidence")
                        or ""
                    )
                    if target_text:
                        target_region = {"text": target_text}
                        break

        if finding.type == "scene_too_long" and "hard_max_chars" not in instruction:
            hard_max_chars = 0
            for evidence in finding.evidence_spans:
                if isinstance(evidence, dict):
                    try:
                        hard_max_chars = int(evidence.get("hard_max_chars") or 0)
                    except (TypeError, ValueError):
                        hard_max_chars = 0
                    if hard_max_chars:
                        break
            instruction = f"{instruction} hard_max_chars {hard_max_chars}"

        # 合同问题走 adjust_contract
        if scope in ("scene_contract", "chapter_contract"):
            operation = "adjust_contract"

        # advisory 走 acknowledge
        if scope == "advisory":
            operation = "acknowledge"
            lane = "none"

        # 确定 allowed_delta_chars
        delta = self._dispatch_delta(dispatch) if dispatch else self._LANE_DELTA.get(lane, 100)

        # 生成工单 ID
        order_id = f"ro_{hashlib.md5(f'{finding.id}:{lane}'.encode()).hexdigest()[:10]}"

        return RepairOrder(
            id=order_id,
            issue_ids=[finding.id],
            lane=lane,
            target_scope=scope,
            target_region=target_region,
            operation=operation,
            instruction=instruction,
            allowed_delta_chars=delta,
            validator=finding.validator,
            max_attempts=finding.max_attempts,
        )

    @classmethod
    def _dispatch_intent(cls, finding: ReviewFindingV2) -> dict | None:
        intent = finding.repair_intent
        if not isinstance(intent, dict) or not intent:
            return None

        lane = str(intent.get("repair_lane") or intent.get("lane") or finding.repair_lane or "").strip()
        operation = str(intent.get("operation") or "").strip()
        confidence = intent.get("confidence", 0.0)
        try:
            confidence_value = float(confidence)
        except (TypeError, ValueError):
            confidence_value = 0.0

        if lane not in cls._DISPATCH_LANES:
            return None
        if operation not in cls._DISPATCH_OPERATIONS:
            return None
        if confidence_value < 0.45:
            return None

        repair_goal = str(intent.get("repair_goal") or intent.get("goal") or finding.description or "").strip()
        return {
            "lane": lane,
            "operation": operation,
            "instruction": repair_goal,
            "intent": intent,
        }

    @staticmethod
    def _merge_dispatch_target(target_region: dict | None, intent: dict) -> dict:
        region = dict(target_region or {})
        region["repair_intent"] = intent
        for key in (
            "semantic_type",
            "repair_class",
            "operation",
            "anchor_text",
            "target_text",
            "window_text",
            "window_start",
            "window_end",
            "insert_position",
            "repair_goal",
            "must_preserve",
            "must_avoid",
            "recheck",
            "confidence",
            "rationale",
        ):
            value = intent.get(key)
            if value not in (None, "", [], {}):
                region[key] = value

        anchor = region.get("anchor_text") or region.get("target_text") or region.get("window_text")
        if anchor and not region.get("text"):
            region["text"] = anchor
        return region

    @staticmethod
    def _dispatch_delta(dispatch: dict | None) -> int:
        if not dispatch:
            return 100
        intent = dispatch.get("intent") or {}
        try:
            value = int(intent.get("allowed_delta_chars") or 0)
        except (TypeError, ValueError):
            value = 0
        if value > 0:
            return max(50, min(value, 1200))
        operation = dispatch.get("operation")
        if operation == "rewrite_scene":
            return 800
        if operation == "rewrite_window":
            return 500
        if operation in {"insert_after", "insert_bridge_action", "insert_bridge_hint", "append_tail"}:
            return 300
        return 150

    @staticmethod
    def _extract_target_region(finding: ReviewFindingV2) -> dict | None:
        """Carry raw evidence into executable repairers."""
        region: dict = {}
        for evidence in finding.evidence_spans:
            if not isinstance(evidence, dict):
                continue
            raw = evidence.get("raw") if isinstance(evidence.get("raw"), dict) else {}
            target_text = (
                evidence.get("span")
                or evidence.get("text")
                or evidence.get("evidence")
                or raw.get("target_span")
                or raw.get("forbidden_item")
                or ""
            )
            if target_text and not region.get("text"):
                region["text"] = str(target_text)
            for key in (
                "expected_behavior",
                "expected_text",
                "expected_state",
                "ending_state",
                "replacement",
                "forbidden_item",
                "detail",
                "current_length",
                "hard_max_chars",
                "target_span",
            ):
                value = raw.get(key) if raw else evidence.get(key)
                if value not in (None, ""):
                    region[key] = value
            if raw:
                region["raw"] = raw
        return region or None

    @classmethod
    def _should_use_type_route(cls, finding: ReviewFindingV2, target_region: dict | None) -> bool:
        if cls.PREFLIGHT_SKIP_MARKER in (finding.description or ""):
            return False

        route = cls._TYPE_OPERATION.get(finding.type)
        if not route:
            return False

        operation = route[1]
        target = target_region or {}
        if finding.repair_lane == "fbi_scene_restructure":
            return False
        if operation in {"repair_time_anchor", "clean_punctuation", "remove_repetition"}:
            return finding.repair_lane == "deterministic"
        if operation in {"repair_time_anchor", "clean_punctuation", "remove_repetition", "cleanup_ai_style"}:
            return True
        if operation == "remove_forbidden_event":
            return bool(target.get("text") or target.get("target_span") or target.get("forbidden_item"))
        if operation in {"complete_ending_state", "complete_contract_item"}:
            return True
        if operation == "repair_fact_state":
            has_target = bool(target.get("text") or target.get("target_span"))
            expected = (
                target.get("replacement")
                or target.get("expected_text")
                or target.get("expected_state")
                or target.get("expected_behavior")
                or target.get("detail")
                or ""
            )
            return has_target and cls._looks_like_direct_replacement(str(expected))
        return True

    @staticmethod
    def _looks_like_direct_replacement(expected: str) -> bool:
        expected = (expected or "").strip()
        if not expected:
            return False
        quoted = re.findall(r"[“\"'「『](.*?)[”\"'」』]", expected)
        candidate = quoted[-1].strip() if quoted else expected
        if not candidate:
            return False
        if candidate.startswith(("删除", "删去", "去掉", "移除", "避免", "保留", "确认", "说明", "建议", "需要", "需", "应当", "应该")):
            return False
        if "，建议" in candidate or "；建议" in candidate or "改为" in candidate[:10]:
            return False
        return len(candidate) <= 120

    def compile_batch(self, findings: list[ReviewFindingV2]) -> list[RepairOrder]:
        """批量编译"""
        orders = []
        for finding in findings:
            if finding.is_actionable():
                orders.append(self.compile(finding))
            else:
                # 不可操作的 finding 生成 acknowledge 工单
                orders.append(RepairOrder(
                    id=f"ro_ack_{hashlib.md5(finding.id.encode()).hexdigest()[:8]}",
                    issue_ids=[finding.id],
                    lane="none",
                    target_scope=finding.repair_scope,
                    operation="acknowledge",
                    instruction=finding.description,
                    max_attempts=0,
                ))
        return orders

    def sort_orders(self, orders: list[RepairOrder]) -> list[RepairOrder]:
        """排序工单：确定性优先，然后按 lane 优先级"""
        lane_priority = {
            "deterministic": 0,
            "fbi_fact": 1,
            "fbi_prose": 2,
            "fbi_pacing": 3,
            "fbi_style_guard": 4,
            "fbi_ending": 5,
            "fbi_scene_restructure": 6,
            "manual_only": 7,
            "none": 8,
        }
        return sorted(orders, key=lambda o: lane_priority.get(o.lane, 99))

    def group_by_lane(self, orders: list[RepairOrder]) -> dict[str, list[RepairOrder]]:
        """按修复通道分组"""
        groups: dict[str, list[RepairOrder]] = {}
        for o in orders:
            lane = o.lane
            if lane not in groups:
                groups[lane] = []
            groups[lane].append(o)
        return groups


# 全局单例
_compiler: RepairOrderCompiler | None = None


def get_repair_order_compiler() -> RepairOrderCompiler:
    global _compiler
    if _compiler is None:
        _compiler = RepairOrderCompiler()
    return _compiler
