"""叙事命题审计服务：检查命题与事实合同、场景溯源、历史命题之间的冲突。"""

from __future__ import annotations

import logging
from typing import Any

from app.models.narrative_proposition import (
    AuditReport,
    AuditViolation,
    Certainty,
    FactContract,
    NarrativeProposition,
    Responsibility,
    TruthLayer,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 确定性升级路径：低确定性 → confirmed 视为违规升级
# ---------------------------------------------------------------------------
_ESCALATION_FORBIDDEN: dict[Certainty, list[Certainty]] = {
    "accused": ["confirmed"],
    "reported": ["confirmed"],
    "suspected": ["confirmed"],
    "unknown": ["confirmed"],
    "inferred": ["confirmed"],
}

# 非当前事实层：这些层的内容不应被当作 current 写入
_NON_CURRENT_LAYERS: set[TruthLayer] = {
    "reference",
    "memory",
    "dream",
    "rumor",
    "inference",
    "plan",
    "future_hint",
    "counterfactual",
}

# 主动行为类责任归属
_ACTIVE_RESPONSIBILITIES: set[Responsibility] = {"active_actor"}

# 被动/模糊类责任归属
_PASSIVE_RESPONSIBILITIES: set[Responsibility] = {"framed", "accused", "unknown"}


def _entity_names(entity: Any) -> set[str]:
    """提取实体的所有名称（含别名），统一小写用于匹配。"""
    names: set[str] = set()
    if entity is None:
        return names
    name = getattr(entity, "name", None)
    if name:
        names.add(name.strip().lower())
    aliases = getattr(entity, "aliases", None)
    if isinstance(aliases, list):
        for alias in aliases:
            if isinstance(alias, str) and alias.strip():
                names.add(alias.strip().lower())
    return names


def _subject_key(prop: NarrativeProposition) -> str:
    """生成命题主语的小写标准化键。"""
    return prop.subject.name.strip().lower()


def _claim_signature(prop: NarrativeProposition) -> str:
    """生成命题的声明签名（主语+谓词+宾语），用于跨命题比对。"""
    parts = [
        prop.subject.name.strip().lower(),
        prop.predicate.name.strip().lower(),
    ]
    if prop.object is not None:
        parts.append(prop.object.name.strip().lower())
    return "|".join(parts)


def _event_signature(prop: NarrativeProposition) -> str:
    """生成事件签名（主语+谓词），用于责任归属冲突检测。"""
    return f"{prop.subject.name.strip().lower()}|{prop.predicate.name.strip().lower()}"


def _text_match(text: str, candidates: list[str]) -> bool:
    """检查文本是否与候选列表中任一项匹配（大小写不敏感、子串匹配）。"""
    text_lower = text.strip().lower()
    for candidate in candidates:
        candidate_lower = candidate.strip().lower()
        if not candidate_lower:
            continue
        if text_lower == candidate_lower or candidate_lower in text_lower:
            return True
    return False


def _clue_already_established(
    prop: NarrativeProposition,
    fact_texts: list[str],
    previous_propositions: list[NarrativeProposition],
) -> bool:
    """Return True when a clue mention is supported by prior narrative state."""
    claim_text = " ".join(
        part
        for part in (
            prop.subject.name,
            prop.predicate.name,
            prop.object.name if prop.object is not None else "",
            prop.source_text,
        )
        if part
    ).strip().lower()
    if not claim_text:
        return False

    for fact_text in fact_texts:
        if _shares_specific_clue_anchor(claim_text, fact_text.lower()):
            return True

    for previous in previous_propositions:
        if previous.predicate.category != "clue":
            continue
        previous_text = " ".join(
            part
            for part in (
                previous.subject.name,
                previous.predicate.name,
                previous.object.name if previous.object is not None else "",
                previous.source_text,
            )
            if part
        ).strip().lower()
        if _shares_specific_clue_anchor(claim_text, previous_text):
            return True
    return False


def _shares_specific_clue_anchor(left: str, right: str) -> bool:
    """Match a reusable clue by a meaningful shared phrase, including Chinese text."""
    left = "".join(left.split())
    right = "".join(right.split())
    if not left or not right:
        return False
    if left in right or right in left:
        return min(len(left), len(right)) >= 4

    shorter, longer = (left, right) if len(left) <= len(right) else (right, left)
    max_size = min(12, len(shorter))
    for size in range(max_size, 3, -1):
        for start in range(0, len(shorter) - size + 1):
            phrase = shorter[start:start + size]
            if phrase in longer and any(
                ch.isalnum() or "\u4e00" <= ch <= "\u9fff"
                for ch in phrase
            ):
                return True
    return False

class NarrativePropositionAuditor:
    """叙事命题审计器：对抽取出的命题进行多维一致性检查。"""

    async def audit(self, context: dict) -> AuditReport:
        """执行完整审计，返回审计报告。

        Args:
            context: 审计上下文，包含：
                - propositions: 待审计的命题列表
                - fact_contract: 事实合同
                - scene_provenance: 场景溯源信息
                - previous_propositions: 历史命题列表（可选）
        """
        propositions: list[NarrativeProposition] = context.get("propositions", []) or []
        fact_contract: FactContract = context.get("fact_contract") or FactContract()
        scene_provenance: dict = context.get("scene_provenance") or {}
        previous_propositions: list[NarrativeProposition] = (
            context.get("previous_propositions") or []
        )

        logger.info(
            "[NarrativePropositionAuditor] 开始审计，命题数=%d，历史命题数=%d",
            len(propositions),
            len(previous_propositions),
        )

        violations: list[AuditViolation] = []

        # 依次执行各类检查
        violations.extend(
            self._check_truth_layer_conflicts(propositions, fact_contract, previous_propositions)
        )
        violations.extend(
            self._check_certainty_escalations(propositions, fact_contract)
        )
        violations.extend(
            self._check_responsibility_polarity_conflicts(propositions, previous_propositions)
        )
        violations.extend(
            self._check_spatial_conflicts(propositions)
        )
        violations.extend(
            self._check_temporal_conflicts(propositions, fact_contract)
        )
        violations.extend(
            self._check_ownership_conflicts(propositions)
        )
        violations.extend(
            self._check_clue_provenance_errors(
                propositions,
                fact_contract,
                previous_propositions,
            )
        )
        violations.extend(
            self._check_forbidden_assertions(propositions, fact_contract)
        )
        violations.extend(
            self._check_required_ambiguities(propositions, fact_contract)
        )

        # 汇总审计结果
        proposition_ids = [p.proposition_id for p in propositions]
        commit_blocked = any(v.blocks_commit for v in violations)
        passed = not commit_blocked

        warnings: list[str] = []
        if not violations:
            warnings.append("审计通过，未发现违规")
        else:
            critical_count = sum(1 for v in violations if v.severity == "critical")
            high_count = sum(1 for v in violations if v.severity == "high")
            medium_count = sum(1 for v in violations if v.severity == "medium")
            low_count = sum(1 for v in violations if v.severity == "low")
            warnings.append(
                f"发现 {len(violations)} 条违规："
                f"critical={critical_count}, high={high_count}, "
                f"medium={medium_count}, low={low_count}"
            )

        report = AuditReport(
            passed=passed,
            commit_blocked=commit_blocked,
            violations=violations,
            proposition_ids=proposition_ids,
            warnings=warnings,
        )

        logger.info(
            "[NarrativePropositionAuditor] 审计完成，passed=%s，违规数=%d，commit_blocked=%s",
            passed,
            len(violations),
            commit_blocked,
        )

        return report

    # ------------------------------------------------------------------
    # 检查 1: truth_layer_conflict
    # ------------------------------------------------------------------
    def _check_truth_layer_conflicts(
        self,
        propositions: list[NarrativeProposition],
        fact_contract: FactContract,
        previous_propositions: list[NarrativeProposition],
    ) -> list[AuditViolation]:
        """检查非当前层（reference/dream/rumor/inference）的声明被当作 current 写入。"""
        violations: list[AuditViolation] = []

        # 收集历史命题中非当前层的声明签名
        non_current_signatures: dict[str, NarrativeProposition] = {}
        for prev in previous_propositions:
            if prev.truth_layer in _NON_CURRENT_LAYERS:
                sig = _claim_signature(prev)
                non_current_signatures[sig] = prev

        # 收集 fact_contract.reference_facts 中的参考事实
        reference_facts = [f.strip().lower() for f in (fact_contract.reference_facts or []) if f]

        for prop in propositions:
            if prop.truth_layer != "current":
                continue

            # 检查与历史非当前层命题的冲突
            sig = _claim_signature(prop)
            if sig in non_current_signatures:
                prev = non_current_signatures[sig]
                violations.append(
                    AuditViolation(
                        type="truth_layer_conflict",
                        severity="high",
                        blocks_commit=True,
                        target_span=prop.source_text[:200],
                        expected_behavior=(
                            f"该声明在历史命题中属于 '{prev.truth_layer}' 层，"
                            f"不应作为 current 事实写入"
                        ),
                        evidence={
                            "proposition_id": prop.proposition_id,
                            "current_layer": prop.truth_layer,
                            "previous_layer": prev.truth_layer,
                            "claim_signature": sig,
                            "previous_proposition_id": prev.proposition_id,
                        },
                        suggested_strategy="reclassify_truth_layer",
                    )
                )
                continue

            # 检查与 reference_facts 的冲突
            claim_text = f"{prop.subject.name}{prop.predicate.name}"
            if prop.object:
                claim_text += f"{prop.object.name}"
            claim_lower = claim_text.strip().lower()

            for ref_fact in reference_facts:
                if not ref_fact:
                    continue
                if ref_fact in claim_lower or claim_lower in ref_fact:
                    violations.append(
                        AuditViolation(
                            type="truth_layer_conflict",
                            severity="high",
                            blocks_commit=True,
                            target_span=prop.source_text[:200],
                            expected_behavior=(
                                "该声明属于参考设定层(reference_facts)，"
                                "不应作为 current 事实写入"
                            ),
                            evidence={
                                "proposition_id": prop.proposition_id,
                                "current_layer": prop.truth_layer,
                                "matched_reference_fact": ref_fact,
                            },
                            suggested_strategy="reclassify_truth_layer",
                        )
                    )
                    break

        return violations

    # ------------------------------------------------------------------
    # 检查 2: certainty_escalation
    # ------------------------------------------------------------------
    def _check_certainty_escalations(
        self,
        propositions: list[NarrativeProposition],
        fact_contract: FactContract,
    ) -> list[AuditViolation]:
        """检查不确定事实被升级为 confirmed。"""
        violations: list[AuditViolation] = []

        # 收集 required_ambiguities 中禁止升级到 confirmed 的约束
        ambiguity_constraints = fact_contract.required_ambiguities or []

        for prop in propositions:
            if prop.certainty != "confirmed":
                continue

            # 检查通用升级路径违规
            # 查找历史中同一声明是否为低确定性（此处仅检查当前批次内的升级模式）
            # 通过 required_ambiguities 约束检查
            for constraint in ambiguity_constraints:
                if not constraint.forbidden_certainty:
                    continue
                if "confirmed" not in constraint.forbidden_certainty:
                    continue

                # 匹配 subject_role
                subject_names = _entity_names(prop.subject)
                role_lower = constraint.subject_role.strip().lower() if constraint.subject_role else ""

                role_matched = False
                if not role_lower:
                    role_matched = True
                elif role_lower in subject_names:
                    role_matched = True

                if not role_matched:
                    continue

                # 匹配 event_type
                event_lower = constraint.event_type.strip().lower() if constraint.event_type else ""
                predicate_lower = prop.predicate.name.strip().lower()

                event_matched = False
                if not event_lower:
                    event_matched = True
                elif event_lower in predicate_lower or predicate_lower in event_lower:
                    event_matched = True

                if not event_matched:
                    continue

                violations.append(
                    AuditViolation(
                        type="certainty_escalation",
                        severity="high",
                        blocks_commit=True,
                        target_span=prop.source_text[:200],
                        expected_behavior=(
                            f"根据事实合同约束，该声明不应升级为 confirmed；"
                            f"允许的确定性级别：{constraint.allowed_certainty or '无'}"
                        ),
                        evidence={
                            "proposition_id": prop.proposition_id,
                            "certainty": prop.certainty,
                            "constraint_subject_role": constraint.subject_role,
                            "constraint_event_type": constraint.event_type,
                            "constraint_reason": constraint.reason,
                        },
                        suggested_strategy="downgrade_certainty",
                    )
                )
                break

        return violations

    # ------------------------------------------------------------------
    # 检查 3: responsibility_polarity_conflict
    # ------------------------------------------------------------------
    def _check_responsibility_polarity_conflicts(
        self,
        propositions: list[NarrativeProposition],
        previous_propositions: list[NarrativeProposition],
    ) -> list[AuditViolation]:
        """检查同一事件在不同命题中责任归属极性冲突。"""
        violations: list[AuditViolation] = []

        # 合并当前命题和历史命题
        all_propositions = list(previous_propositions) + list(propositions)

        # 按事件签名分组
        event_groups: dict[str, list[NarrativeProposition]] = {}
        for prop in all_propositions:
            sig = _event_signature(prop)
            if sig not in event_groups:
                event_groups[sig] = []
            event_groups[sig].append(prop)

        # 检查每组内是否有极性冲突
        seen_pairs: set[frozenset[str]] = set()
        for sig, group in event_groups.items():
            if len(group) < 2:
                continue

            active_props = [p for p in group if p.responsibility in _ACTIVE_RESPONSIBILITIES]
            passive_props = [p for p in group if p.responsibility in _PASSIVE_RESPONSIBILITIES]

            for active_prop in active_props:
                for passive_prop in passive_props:
                    pair = frozenset({active_prop.proposition_id, passive_prop.proposition_id})
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)

                    violations.append(
                        AuditViolation(
                            type="responsibility_polarity_conflict",
                            severity="high",
                            blocks_commit=True,
                            target_span=active_prop.source_text[:200],
                            expected_behavior=(
                                f"同一事件 '{sig}' 存在冲突的责任归属："
                                f"'{active_prop.responsibility}' vs '{passive_prop.responsibility}'"
                            ),
                            evidence={
                                "event_signature": sig,
                                "active_proposition_id": active_prop.proposition_id,
                                "active_responsibility": active_prop.responsibility,
                                "passive_proposition_id": passive_prop.proposition_id,
                                "passive_responsibility": passive_prop.responsibility,
                            },
                            suggested_strategy="resolve_responsibility",
                        )
                    )

        return violations

    # ------------------------------------------------------------------
    # 检查 4: spatial_conflict
    # ------------------------------------------------------------------
    def _check_spatial_conflicts(
        self,
        propositions: list[NarrativeProposition],
    ) -> list[AuditViolation]:
        """检查同一主体在同一时间出现在互斥位置。"""
        violations: list[AuditViolation] = []

        # 筛选位置类命题
        location_props = [
            p for p in propositions
            if p.predicate.category == "location" and p.location_scope.strip()
        ]

        # 按主语+时间分组
        subject_time_groups: dict[str, list[NarrativeProposition]] = {}
        for prop in location_props:
            key = f"{_subject_key(prop)}|{prop.time_scope.strip().lower()}"
            if key not in subject_time_groups:
                subject_time_groups[key] = []
            subject_time_groups[key].append(prop)

        # 检查每组内是否有不同位置
        for key, group in subject_time_groups.items():
            if len(group) < 2:
                continue

            # 收集所有不同位置
            locations: dict[str, list[NarrativeProposition]] = {}
            for prop in group:
                loc = prop.location_scope.strip().lower()
                if loc not in locations:
                    locations[loc] = []
                locations[loc].append(prop)

            if len(locations) <= 1:
                continue

            # 存在多个不同位置 → 空间冲突
            location_list = list(locations.keys())
            first_prop = group[0]
            violations.append(
                AuditViolation(
                    type="spatial_conflict",
                    severity="high",
                    blocks_commit=True,
                    target_span=first_prop.source_text[:200],
                    expected_behavior=(
                        f"主体 '{first_prop.subject.name}' 在同一时间范围 "
                        f"'{first_prop.time_scope}' 出现在互斥位置："
                        f"{', '.join(location_list)}"
                    ),
                    evidence={
                        "subject": first_prop.subject.name,
                        "time_scope": first_prop.time_scope,
                        "conflicting_locations": location_list,
                        "proposition_ids": [p.proposition_id for p in group],
                    },
                    suggested_strategy="resolve_spatial_conflict",
                )
            )

        return violations

    # ------------------------------------------------------------------
    # 检查 5: temporal_conflict
    # ------------------------------------------------------------------
    def _check_temporal_conflicts(
        self,
        propositions: list[NarrativeProposition],
        fact_contract: FactContract,
    ) -> list[AuditViolation]:
        """检查已完成事件被重演或时间矛盾。"""
        violations: list[AuditViolation] = []

        temporal_constraints = fact_contract.temporal_constraints or []

        for prop in propositions:
            # 检查是否匹配 forbidden_recap_events（no_replay 约束）
            for constraint in temporal_constraints:
                if not constraint.no_replay:
                    continue

                event_desc = constraint.event_description.strip().lower()
                if not event_desc:
                    continue

                # 匹配命题与禁止重演事件
                claim_text = (
                    f"{prop.subject.name} {prop.predicate.name}"
                ).strip().lower()
                if prop.object:
                    claim_text += f" {prop.object.name}".lower()

                if event_desc in claim_text or claim_text in event_desc:
                    violations.append(
                        AuditViolation(
                            type="temporal_conflict",
                            severity="high",
                            blocks_commit=True,
                            target_span=prop.source_text[:200],
                            expected_behavior=(
                                f"事件 '{constraint.event_description}' 已完成，"
                                f"不得重演。原因：{constraint.reason or '无'}"
                            ),
                            evidence={
                                "proposition_id": prop.proposition_id,
                                "forbidden_event": constraint.event_description,
                                "constraint_reason": constraint.reason,
                            },
                            suggested_strategy="remove_recap",
                        )
                    )
                    break

        return violations

    # ------------------------------------------------------------------
    # 检查 6: ownership_conflict
    # ------------------------------------------------------------------
    def _check_ownership_conflicts(
        self,
        propositions: list[NarrativeProposition],
    ) -> list[AuditViolation]:
        """检查同一物品被多个主体持有且无转移事件。"""
        violations: list[AuditViolation] = []

        # 筛选归属类命题
        ownership_props = [
            p for p in propositions
            if p.predicate.category == "ownership" and p.object is not None
        ]

        # 按物品分组
        item_groups: dict[str, list[NarrativeProposition]] = {}
        for prop in ownership_props:
            obj_names = _entity_names(prop.object)
            for name in obj_names:
                if name not in item_groups:
                    item_groups[name] = []
                item_groups[name].append(prop)

        # 检查同一物品是否有多个不同持有者
        for item_name, group in item_groups.items():
            if len(group) < 2:
                continue

            # 收集不同持有者
            holders: dict[str, list[NarrativeProposition]] = {}
            for prop in group:
                holder_key = _subject_key(prop)
                if holder_key not in holders:
                    holders[holder_key] = []
                holders[holder_key].append(prop)

            if len(holders) <= 1:
                continue

            # 检查是否存在转移事件
            has_transfer = any(
                any(
                    kw in prop.predicate.name.lower()
                    for kw in ("转移", "交给", "转交", "转让", "给", "transfer", "give", "hand")
                )
                for prop in group
            )
            if has_transfer:
                continue

            holder_names = list(holders.keys())
            violations.append(
                AuditViolation(
                    type="ownership_conflict",
                    severity="medium",
                    blocks_commit=True,
                    target_span=group[0].source_text[:200],
                    expected_behavior=(
                        f"物品 '{item_name}' 被多个主体持有：{', '.join(holder_names)}，"
                        f"但缺少转移事件"
                    ),
                    evidence={
                        "item": item_name,
                        "holders": holder_names,
                        "proposition_ids": [p.proposition_id for p in group],
                    },
                    suggested_strategy="add_transfer_event",
                )
            )

        return violations

    # ------------------------------------------------------------------
    # 检查 7: clue_provenance_error
    # ------------------------------------------------------------------
    def _check_clue_provenance_errors(
        self,
        propositions: list[NarrativeProposition],
        fact_contract: FactContract,
        previous_propositions: list[NarrativeProposition] | None = None,
    ) -> list[AuditViolation]:
        """检查线索缺少来源/放置者/发现条件。"""
        violations: list[AuditViolation] = []

        clue_constraints = fact_contract.clue_constraints or []
        previous_propositions = previous_propositions or []
        established_fact_texts = [
            str(item).strip()
            for item in [
                *(fact_contract.current_facts or []),
                *(fact_contract.reference_facts or []),
            ]
            if str(item).strip()
        ]

        # 筛选线索类命题
        clue_props = [
            p for p in propositions
            if p.predicate.category == "clue"
        ]

        for prop in clue_props:
            # 查找匹配的线索约束
            matched_constraint = None
            for constraint in clue_constraints:
                desc_lower = constraint.clue_description.strip().lower()
                if not desc_lower:
                    matched_constraint = constraint
                    break
                claim_text = (
                    f"{prop.subject.name} {prop.predicate.name}"
                ).strip().lower()
                if prop.object:
                    claim_text += f" {prop.object.name}".lower()
                if desc_lower in claim_text or claim_text in desc_lower:
                    matched_constraint = constraint
                    break

            # 未配置当前场景的显式线索约束时，历史已建立线索可以被再次提及。
            # 旧线索的来源属于长期事实，不要求每次出现都重新叙述放置过程。
            if matched_constraint is None and _clue_already_established(
                prop,
                established_fact_texts,
                previous_propositions,
            ):
                continue

            # 如果没有匹配的约束，使用默认要求
            requires_source = True
            requires_placement = False
            requires_discovery = False

            if matched_constraint is not None:
                requires_source = matched_constraint.required_source_actor
                requires_placement = matched_constraint.required_placement_time
                requires_discovery = matched_constraint.required_discovery_condition

            # 检查线索命题是否包含必要来源信息
            source_text_lower = prop.source_text.lower()
            missing_fields: list[str] = []

            if requires_source:
                # 检查是否有来源角色信息（主语或宾语提供）
                has_source = (
                    prop.subject.entity_type == "character"
                    or (prop.object is not None and prop.object.entity_type == "character")
                    or any(
                        kw in source_text_lower
                        for kw in ("放置", "留下", "藏", "放", "plant", "hide", "leave")
                    )
                )
                if not has_source:
                    missing_fields.append("source_actor")

            if requires_placement:
                has_placement = bool(prop.time_scope.strip())
                if not has_placement:
                    missing_fields.append("placement_time")

            if requires_discovery:
                has_discovery = any(
                    kw in source_text_lower
                    for kw in ("发现", "找到", "看到", "注意到", "discover", "find", "notice")
                )
                if not has_discovery:
                    missing_fields.append("discovery_condition")

            if missing_fields:
                claim_parts = [prop.subject.name, prop.predicate.name]
                if prop.object:
                    claim_parts.append(prop.object.name)
                claim_summary = " ".join(part for part in claim_parts if part).strip()
                authority_fact = ""
                authority_status = "unverified"
                if matched_constraint is not None:
                    known_parts = [f"线索「{matched_constraint.clue_description}」"]
                    if matched_constraint.source_actor:
                        known_parts.append(f"来源角色={matched_constraint.source_actor}")
                    if matched_constraint.placement_time:
                        known_parts.append(f"放置时间={matched_constraint.placement_time}")
                    if matched_constraint.discovery_condition:
                        known_parts.append(f"发现条件={matched_constraint.discovery_condition}")
                    authority_fact = "；".join(known_parts)
                    authority_status = (
                        "confirmed"
                        if any((
                            matched_constraint.source_actor,
                            matched_constraint.placement_time,
                            matched_constraint.discovery_condition,
                        ))
                        else "missing_provenance"
                    )
                else:
                    claim_tokens = {
                        str(value).strip().lower()
                        for value in (
                            prop.subject.name,
                            prop.predicate.name,
                            prop.object.name if prop.object else "",
                        )
                        if len(str(value).strip()) >= 2
                    }
                    authority_fact = next(
                        (
                            fact
                            for fact in established_fact_texts
                            if any(token in fact.lower() for token in claim_tokens)
                        ),
                        "",
                    )
                    if authority_fact:
                        authority_status = "confirmed"
                    else:
                        authority_fact = f"线索「{claim_summary or prop.source_text[:80]}」的来源尚未建立"
                        authority_status = "missing_provenance"

                # 通用修复：区分「剧情伏笔」和「线索溯源错误」
                # 根因：当场景合同未预声明线索（matched_constraint is None）时，
                #   检测器默认 requires_source=True。但 Writer 可能在正文中写了
                #   有意的伏笔——角色通过观察发现线索（如「传讯符被截了」
                #   「她发现纸鹤上写着字」「她认出这是宗门的暗号」），这些是
                #   叙事性伏笔，不是溯源错误。作者故意不在本章揭示来源。
                # 修复：当无匹配约束时，检查正文是否包含叙事观察暗示——
                #   角色主动发现/观察/识别线索的描述。若存在，降级为 advisory
                #   （不 blocking），避免把剧情伏笔误判为溯源错误。
                # 通用性：所有题材的伏笔场景都适用——角色通过观察发现异常
                #   物/线索是叙事基本手法，不应要求每章都揭示来源。
                is_foreshadowing = matched_constraint is None and any(
                    kw in source_text_lower
                    for kw in (
                        "发现", "认出", "察觉", "注意", "观察", "看到", "听见",
                        "想不通", "不知道是谁", "不明", "神秘", "突然",
                        "被截", "被捏断", "被阻断", "被破坏", "被篡改",
                        "discovered", "noticed", "recognized", "unknown",
                    )
                )
                violations.append(
                    AuditViolation(
                        type="clue_provenance_error",
                        severity="low" if is_foreshadowing else "medium",
                        blocks_commit=not is_foreshadowing,
                        target_span=prop.source_text[:200],
                        expected_behavior=(
                            (
                                f"按已知事实补足线索来源信息：{authority_fact}；"
                                f"缺少字段：{', '.join(missing_fields)}"
                            )
                            if authority_status == "confirmed"
                            else (
                                "当前没有可验证的来源事实，不得编造放置者；"
                                "应把线索写成角色可观察到的异常并明确发现条件，"
                                "或删除未受场景合同支持的线索。"
                            )
                        ),
                        evidence={
                            "proposition_id": prop.proposition_id,
                            "missing_fields": missing_fields,
                            "clue_description": (
                                matched_constraint.clue_description
                                if matched_constraint
                                else "（无匹配约束）"
                            ),
                            "is_foreshadowing": is_foreshadowing,
                            "authority_fact": authority_fact,
                            "authority_status": authority_status,
                            "text_claim": claim_summary,
                        },
                        suggested_strategy="add_clue_provenance",
                    )
                )

        return violations

    # ------------------------------------------------------------------
    # 检查 8: forbidden_assertion_triggered
    # ------------------------------------------------------------------
    def _check_forbidden_assertions(
        self,
        propositions: list[NarrativeProposition],
        fact_contract: FactContract,
    ) -> list[AuditViolation]:
        """检查命题是否触发了禁止断言。"""
        violations: list[AuditViolation] = []

        forbidden_assertions = fact_contract.forbidden_assertions or []

        for prop in propositions:
            for constraint in forbidden_assertions:
                matched = True

                # 匹配 subject_role
                if constraint.subject_role:
                    subject_names = _entity_names(prop.subject)
                    role_lower = constraint.subject_role.strip().lower()
                    if role_lower not in subject_names:
                        matched = False

                # 匹配 event_type
                if matched and constraint.event_type:
                    event_lower = constraint.event_type.strip().lower()
                    predicate_lower = prop.predicate.name.strip().lower()
                    if event_lower not in predicate_lower and predicate_lower not in event_lower:
                        matched = False

                # 匹配 responsibility
                if matched and constraint.responsibility:
                    if prop.responsibility != constraint.responsibility:
                        matched = False

                # 匹配 forbidden_certainty
                if matched and constraint.forbidden_certainty:
                    if prop.certainty not in constraint.forbidden_certainty:
                        matched = False

                if not matched:
                    continue

                violations.append(
                    AuditViolation(
                        type="forbidden_assertion_triggered",
                        severity="critical",
                        blocks_commit=True,
                        target_span=prop.source_text[:200],
                        expected_behavior=(
                            f"该命题触发了禁止断言约束。原因：{constraint.reason or '无'}"
                        ),
                        evidence={
                            "proposition_id": prop.proposition_id,
                            "constraint_subject_role": constraint.subject_role,
                            "constraint_event_type": constraint.event_type,
                            "constraint_responsibility": constraint.responsibility,
                            "constraint_reason": constraint.reason,
                        },
                        suggested_strategy="remove_forbidden_assertion",
                    )
                )

        return violations

    # ------------------------------------------------------------------
    # 检查 9: required_ambiguity_broken
    # ------------------------------------------------------------------
    def _check_required_ambiguities(
        self,
        propositions: list[NarrativeProposition],
        fact_contract: FactContract,
    ) -> list[AuditViolation]:
        """检查应保持不确定的信息被确认为事实。"""
        violations: list[AuditViolation] = []

        ambiguity_constraints = fact_contract.required_ambiguities or []

        for prop in propositions:
            # 只检查确定性为 confirmed 的命题
            if prop.certainty != "confirmed":
                continue

            for constraint in ambiguity_constraints:
                matched = True

                # 匹配 subject_role
                if constraint.subject_role:
                    subject_names = _entity_names(prop.subject)
                    role_lower = constraint.subject_role.strip().lower()
                    if role_lower not in subject_names:
                        matched = False

                # 匹配 event_type
                if matched and constraint.event_type:
                    event_lower = constraint.event_type.strip().lower()
                    predicate_lower = prop.predicate.name.strip().lower()
                    if event_lower not in predicate_lower and predicate_lower not in event_lower:
                        matched = False

                # 匹配 responsibility
                if matched and constraint.responsibility:
                    if prop.responsibility != constraint.responsibility:
                        matched = False

                # 检查确定性是否违反约束
                if matched and constraint.forbidden_certainty:
                    if prop.certainty not in constraint.forbidden_certainty:
                        matched = False

                if not matched:
                    continue

                # 确认该约束要求保持不确定
                allowed = constraint.allowed_certainty or []
                if allowed and prop.certainty not in allowed:
                    violations.append(
                        AuditViolation(
                            type="required_ambiguity_broken",
                            severity="high",
                            blocks_commit=True,
                            target_span=prop.source_text[:200],
                            expected_behavior=(
                                f"根据事实合同，该信息应保持不确定。"
                                f"允许的确定性级别：{allowed}；原因：{constraint.reason or '无'}"
                            ),
                            evidence={
                                "proposition_id": prop.proposition_id,
                                "certainty": prop.certainty,
                                "allowed_certainty": allowed,
                                "constraint_subject_role": constraint.subject_role,
                                "constraint_event_type": constraint.event_type,
                                "constraint_reason": constraint.reason,
                            },
                            suggested_strategy="restore_ambiguity",
                        )
                    )

        return violations
