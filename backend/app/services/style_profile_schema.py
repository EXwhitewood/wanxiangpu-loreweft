"""方案 26：风格资产层标准化与 FBI 风格审查适配器。

本模块整合方案 26 的多个 Part：
- Part F：style_profile / style_directive schema 定义与校验
- Part B：主编 style_directive 质量校验入口
- Part C：STYLE_ISSUE_FAMILIES 定义、StyleReviewAdapter（检测→issue）、
  writer_deviations_to_issues（writer 自报偏离→advisory issue）

设计原则：
1. style_profile / style_directive 仍是 plain dict（向后兼容），本模块只提供 schema 校验
2. StyleReviewAdapter 复用 StylePolishSkill 的检测能力，但产出 ReviewCaseIssue dict
   而非直接改正文（Part E 封口的对应物）
3. 风格类 issue family 与现有 FBI family 平行，不污染现有 family
"""
from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Part F：style_profile / style_directive schema
# ----------------------------------------------------------------------

# style_profile 必填字段（与 memory_core.create_style_profile 对齐）
STYLE_PROFILE_REQUIRED_FIELDS = [
    "id",
    "name",
    "style_features",
    "style_embedding",
    "persona_card",
]

# style_features 子字段
STYLE_FEATURES_FIELDS = [
    "vocabulary",
    "sentence_structure",
    "tone",
    "pacing",
    "description_style",
    "dialogue_style",
    "narrative_voice",
    "signature_phrases",
    "avoid_patterns",
]

# style_embedding 8 维向量
STYLE_EMBEDDING_DIMENSIONS = [
    "emotionality",
    "sentence_complexity",
    "narrative_distance",
    "info_density",
    "dialogue_ratio",
    "description_density",
    "rhythm_steepness",
    "narrator_intrusion",
]

# style_directive 必填字段（场景级，主编输出）
STYLE_DIRECTIVE_REQUIRED_FIELDS = [
    "scene_style_role",
    "narrative_distance",
    "rhythm_goal",
    "dialogue_density",
    "description_density",
    "avoid",
]


def validate_style_profile(profile: dict | None) -> list[str]:
    """校验 style_profile 完整性，返回缺失字段列表。

    方案 26 Part F：style_profile 作为项目资产，全链路共享同一份。
    本函数用于在写入/读取时校验，不阻断流程（缺失记录在返回列表中）。
    """
    if not profile or not isinstance(profile, dict):
        return ["style_profile_empty"]

    issues: list[str] = []
    for field in STYLE_PROFILE_REQUIRED_FIELDS:
        if field not in profile or profile[field] in (None, "", [], {}):
            issues.append(f"style_profile.{field}_missing")

    style_features = profile.get("style_features") or {}
    if isinstance(style_features, dict):
        for field in STYLE_FEATURES_FIELDS:
            if field not in style_features:
                issues.append(f"style_profile.style_features.{field}_missing")

    style_embedding = profile.get("style_embedding") or {}
    if isinstance(style_embedding, dict):
        for dim in STYLE_EMBEDDING_DIMENSIONS:
            if dim not in style_embedding:
                issues.append(f"style_profile.style_embedding.{dim}_missing")

    return issues


def validate_style_directive(directive: dict | None) -> list[str]:
    """校验场景合同的 style_directive，返回缺失字段列表。

    方案 26 Part B：主编输出场景合同后调用本函数校验。
    缺失关键字段时触发主编重试（由调用方决定）。
    """
    if not directive or not isinstance(directive, dict):
        return ["style_directive_missing"]

    issues: list[str] = []
    for field in STYLE_DIRECTIVE_REQUIRED_FIELDS:
        val = directive.get(field)
        if val in (None, "", [], {}):
            issues.append(f"style_directive.{field}_missing_or_empty")
    return issues


# P2-19：Part B2 style_directive 校验失败重试上限。
# 主编在 MAX_STYLE_DIRECTIVE_RETRIES 次重试后仍校验失败时，回退到
# build_default_style_directive 生成的默认指令，避免流程卡死。
MAX_STYLE_DIRECTIVE_RETRIES = 2


def build_default_style_directive(style_profile: dict | None) -> dict:
    """构造默认 style_directive，用于 Part B2 重试耗尽后的回退。

    方案 26 Part B2：主编多次重试仍无法输出合规 style_directive 时，
    回退到本函数生成的默认指令，保证流程不卡死。默认指令：
    - scene_style_role="inherit"（继承全局风格）
    - 各维度取"中"/"balanced"等保守值
    - avoid 优先继承 style_profile.style_features.avoid_patterns；
      无画像或画像无 avoid_patterns 时用占位符（满足非空校验）

    Args:
        style_profile: 项目风格画像（可选）

    Returns:
        合规的 style_directive dict，附加 _fallback=True 标记
    """
    style_profile = style_profile or {}
    style_features = style_profile.get("style_features") or {}
    avoid_patterns = style_features.get("avoid_patterns") or []
    # avoid 必须非空才能通过 validate_style_directive 校验
    if not avoid_patterns:
        avoid_patterns = ["（继承全局风格，无场景级额外禁用）"]
    return {
        "scene_style_role": "inherit",
        "narrative_distance": "中",
        "rhythm_goal": "balanced",
        "dialogue_density": "中",
        "description_density": "中",
        "avoid": list(avoid_patterns),
        "_fallback": True,  # 标记为回退生成，供下游识别
    }


# ----------------------------------------------------------------------
# Part C：FBI 风格审查维度
# ----------------------------------------------------------------------

# 风格类 issue family（与现有 FBI family 平行，不污染 anti_ai_local 等）
STYLE_ISSUE_FAMILIES = [
    "style_consistency",       # 风格一致性：与 style_profile 不符
    "anti_ai_style",           # AI 味：结构词堆积、模板句、机械停顿
    "sentence_rhythm",         # 句式节奏：相邻句长/句式重复
    "paragraph_shape",         # 段落形态：段落形态重复或过密
    "voice_drift",             # 声线偏移：角色声线与 persona 不符
    "structure_word_cluster",  # 结构词堆积：逻辑词、说明性句壳堆积
]


def make_style_issue(
    family: str,
    severity: str,
    metric: str,
    description: str,
    *,
    anchor: int | None = None,
    evidence: str = "",
    target_span: str = "",
    extra: dict | None = None,
) -> dict:
    """构造一个风格类 ReviewCaseIssue dict。

    与 FBI 现有 ReviewCaseIssue 字段对齐，便于直接加入案卷。
    target_span 优先使用显式传入值；缺省时从 evidence 推导，
    确保 FBI 路由表的 anchor_strategy=localized_target_span 能命中。
    """
    issue: dict[str, Any] = {
        "family": family,
        "severity": severity,
        "metric": metric,
        "description": description,
        # Style alignment is a generation preference and repair hint, not a
        # fact/contract boundary.  FBI may repair it opportunistically, but it
        # must never become a strong commit blocker.
        "blocks_commit": False,
        "enforcement": "advisory",
        "scope": "prose_text",
        "repairable_by_text": True,
    }
    if anchor is not None:
        issue["anchor"] = anchor
    # 方案 26 Part D：target_span 是 FBI 路由 anchor 解析的首选字段。
    # evidence 可能是上下文片段或匹配词，也可作为 target_span 使用。
    resolved_span = target_span or evidence
    if resolved_span:
        issue["target_span"] = resolved_span
        issue["localization_status"] = "localized"
    if evidence:
        issue["evidence"] = evidence
    if extra:
        issue.update(extra)
    # Callers may attach routing metadata through ``extra``, but a pure style
    # issue is never allowed to acquire commit-blocking authority inside FBI.
    issue["blocks_commit"] = False
    issue["enforcement"] = "advisory"
    issue["repairable_by_text"] = True
    return issue


def writer_deviations_to_issues(compliance: dict | None) -> list[dict]:
    """将 writer 自报的 style_compliance.deviations 转为 advisory issue。

    方案 26 Part C3：writer 自报偏离是 advisory（不是 blocking），
    进入 FBI 案卷供审查官参考。
    """
    if not compliance or not isinstance(compliance, dict):
        return []

    issues: list[dict] = []
    for dev in compliance.get("deviations", []) or []:
        if not isinstance(dev, dict):
            continue
        directive = dev.get("directive", "unknown")
        expected = dev.get("expected", "")
        actual = dev.get("actual", "")
        reason = dev.get("reason", "")
        issues.append(make_style_issue(
            family="style_consistency",
            severity="advisory",
            metric="style_directive_deviation",
            description=(
                f"writer 自报偏离 {directive}：期望 {expected}，"
                f"实际 {actual}，原因：{reason}"
            ),
            extra={"scene_index": dev.get("scene_index")},
        ))
    return issues


# ----------------------------------------------------------------------
# Part C/E：StyleReviewAdapter——style_polish 检测逻辑转 issue 生成器
# ----------------------------------------------------------------------

class StyleReviewAdapter:
    """将 StylePolishSkill 的检测结果转为 ReviewCaseIssue dict 列表。

    方案 26 Part C2 + Part E：
    - 复用 StylePolishSkill 的检测能力（禁用词、重复、风格一致性、维度失败）
    - 产出 issue 列表，不直接改正文
    - 调用方（scene_generation_pipeline.py）将 issue 加入 quality_report 或 FBI 案卷

    P2-17 修复：本 Adapter 处于 review（只读）路径，禁止调用 StylePolishSkill.polish()
    该方法内部会调用 polish_transitions() 生成 polished_text（写入副作用）。
    改为只调用只读检测方法（check_forbidden_words/check_repetition/
    check_style_consistency/_infer_style_embedding/_detect_dimension_failures/
    _detect_contract_style_conflict），并延迟实例化 StylePolishSkill 避免导入期副作用。
    """

    def __init__(self) -> None:
        # P2-17：延迟实例化，避免在 review 路径上提前持有 polish skill 实例。
        # StylePolishSkill 构造函数本身无副作用，但延迟实例化可让单元测试
        # 在不需要 polish skill 时不必触发 import。
        self._polish_skill = None

    def _ensure_polish_skill(self):
        """延迟实例化 StylePolishSkill，仅用于复用只读检测方法。"""
        if self._polish_skill is None:
            from app.skills.style_polish import StylePolishSkill
            self._polish_skill = StylePolishSkill()
        return self._polish_skill

    def _detect_style_violations_readonly(
        self,
        text: str,
        style_features: dict | None,
        style_embedding: dict | None,
        style_directive: dict | None,
    ) -> dict:
        """P2-17：只读检测，复用 StylePolishSkill 的检测方法但不调用 polish()。

        polish() 内部会调用 polish_transitions() 生成 polished_text（写入副作用），
        在 review 路径上不应触发。本方法直接调用只读检测方法，返回与 polish()
        相同结构的子集字段（style_violations / dimension_failures /
        contract_style_conflict），供 issue 转换使用。

        Returns:
            dict: {
                "style_violations": list[dict],
                "dimension_failures": list[dict],
                "contract_style_conflict": dict | None,
            }
        """
        skill = self._ensure_polish_skill()

        # 1. avoid_patterns 违反检测（只读）
        style_violations: list[dict] = []
        if style_features:
            try:
                style_violations = skill.check_style_consistency(text, style_features) or []
            except Exception as exc:
                logger.debug(
                    "[StyleReviewAdapter] check_style_consistency failed: %s", exc
                )
                style_violations = []

        # 2. 维度偏离检测（只读）
        dimension_failures: list[dict] = []
        try:
            inferred_embedding = skill._infer_style_embedding(text)
            embedding_delta: dict[str, float] = {}
            if style_embedding:
                for key, target in style_embedding.items():
                    if not isinstance(target, (int, float)):
                        continue
                    embedding_delta[key] = round(
                        abs(inferred_embedding.get(key, 0.0) - float(target)), 3
                    )
            dimension_failures = skill._detect_dimension_failures(
                embedding_delta, style_directive, inferred_embedding,
            ) or []
        except Exception as exc:
            logger.debug(
                "[StyleReviewAdapter] dimension failure detection failed: %s", exc
            )
            dimension_failures = []

        # 3. 合同-风格冲突检测（只读）
        contract_style_conflict = None
        try:
            contract_style_conflict = skill._detect_contract_style_conflict(
                dimension_failures, style_directive,
            )
        except Exception as exc:
            logger.debug(
                "[StyleReviewAdapter] contract_style_conflict detection failed: %s", exc
            )
            contract_style_conflict = None

        return {
            "style_violations": style_violations,
            "dimension_failures": dimension_failures,
            "contract_style_conflict": contract_style_conflict,
        }

    async def detect_style_issues(
        self,
        text: str,
        style_profile: dict | None,
        scene_contracts: list[dict] | None = None,
        style_directive: dict | None = None,
    ) -> list[dict]:
        """检测风格问题，返回 issue dict 列表。

        Args:
            text: 待检测正文
            style_profile: 项目风格画像（含 style_features/style_embedding/persona_card）
            scene_contracts: 场景合同列表（用于 contract_style_conflict 检测）
            style_directive: 本场景风格指令（可选，优先于 scene_contracts 中提取）

        Returns:
            ReviewCaseIssue dict 列表，可直接加入 FBI 案卷
        """
        if not text:
            return []

        style_profile = style_profile or {}
        style_features = style_profile.get("style_features") or {}
        style_embedding = style_profile.get("style_embedding") or {}
        persona_card = style_profile.get("persona_card") or {}

        # P2-17：调用只读检测方法，禁止调用 polish()（会触发 polish_transitions 写入副作用）
        detect_result = self._detect_style_violations_readonly(
            text,
            style_features=style_features or None,
            style_embedding=style_embedding or None,
            style_directive=style_directive,
        )

        issues: list[dict] = []

        # 1. 禁用词检测（硬约束）→ style_consistency / blocking
        issues.extend(self._convert_forbidden_words(text, style_features))

        # 2. 重复短语检测（软约束）→ sentence_rhythm / warning
        issues.extend(self._convert_repetition(text))

        # 3. 风格一致性 violations（avoid_patterns）→ style_consistency / blocking
        for violation in detect_result.get("style_violations", []) or []:
            issues.append(make_style_issue(
                family="style_consistency",
                severity="blocking",
                metric="avoid_pattern_violation",
                description=str(violation.get("description") or violation),
                evidence=str(violation.get("evidence") or violation.get("match") or ""),
                extra={"suggestion": violation.get("suggestion", "")},
            ))

        # 4. 维度失败（soft 约束）→ style_consistency / warning
        for failure in detect_result.get("dimension_failures", []) or []:
            if not isinstance(failure, dict):
                continue
            issues.append(make_style_issue(
                family="style_consistency",
                severity="warning",
                metric=failure.get("metric", "dimension_failure"),
                description=str(failure.get("description") or failure),
                extra={"expected": failure.get("expected"),
                       "actual": failure.get("actual")},
            ))

        # 5. 合同-风格冲突 → style_consistency / blocking（应回流主编）
        conflict = detect_result.get("contract_style_conflict")
        if conflict:
            issues.append(make_style_issue(
                family="style_consistency",
                severity="blocking",
                metric="contract_style_conflict",
                description=str(conflict.get("description") or conflict),
                extra={"conflict_type": conflict.get("type", ""),
                       "repair_hint": conflict.get("repair_hint", "")},
            ))

        # 6. 段落形态重复（软约束）→ paragraph_shape / warning
        issues.extend(self._detect_paragraph_shape_repeat(text))

        # 7. 句式节奏重复（软约束）→ sentence_rhythm / warning
        issues.extend(self._detect_sentence_rhythm_repeat(text))

        # 8. 结构词堆积（软约束）→ structure_word_cluster / warning
        issues.extend(self._detect_structure_word_cluster(text))

        return issues

    # ------------------------------------------------------------------
    # 内部转换方法
    # ------------------------------------------------------------------

    def _convert_forbidden_words(
        self, text: str, style_features: dict
    ) -> list[dict]:
        """禁用词检测 → style_consistency / blocking issue。"""
        issues: list[dict] = []
        try:
            # P2-17：通过延迟实例化访问，避免直接持有 polish skill 实例
            violations = self._ensure_polish_skill().check_forbidden_words(text)
        except Exception as exc:
            logger.debug("[StyleReviewAdapter] check_forbidden_words failed: %s", exc)
            return issues
        for v in violations or []:
            if not isinstance(v, dict):
                continue
            issues.append(make_style_issue(
                family="style_consistency",
                severity="blocking",
                metric="forbidden_word",
                description=f"违反风格禁用表达：{v.get('word', '')}",
                anchor=v.get("position"),
                evidence=v.get("context", ""),
                extra={"suggestion": v.get("suggestion", "")},
            ))
        return issues

    def _convert_repetition(self, text: str) -> list[dict]:
        """重复短语检测 → sentence_rhythm / warning issue。"""
        issues: list[dict] = []
        try:
            # P2-17：通过延迟实例化访问，避免直接持有 polish skill 实例
            repetitions = self._ensure_polish_skill().check_repetition(text)
        except Exception as exc:
            logger.debug("[StyleReviewAdapter] check_repetition failed: %s", exc)
            return issues
        for rep in repetitions or []:
            if not isinstance(rep, dict):
                continue
            issues.append(make_style_issue(
                family="sentence_rhythm",
                severity="warning",
                metric="phrase_repeat",
                description=f"短语重复：{rep.get('phrase', '')}（出现 {rep.get('count', 0)} 次）",
                evidence=rep.get("phrase", ""),
            ))
        return issues

    # 以下检测逻辑参考 style_polish.py 的现有实现，转为 issue 而非改正文

    _STRUCTURE_WORDS = (
        "因此", "所以", "于是", "然而", "但是", "不过", "实际上",
        "事实上", "换句话说", "也就是说", "与此同时", "在此基础上",
        "总的来说", "综上所述", "由此可见", "不难看出",
    )

    def _detect_structure_word_cluster(self, text: str) -> list[dict]:
        """结构词堆积检测 → structure_word_cluster / warning issue。

        在 500 字窗口内若同一结构词出现 ≥3 次，视为堆积。
        """
        issues: list[dict] = []
        if not text:
            return issues
        window = 500
        for word in self._STRUCTURE_WORDS:
            positions = [m.start() for m in re.finditer(re.escape(word), text)]
            if len(positions) < 3:
                continue
            for i in range(len(positions) - 2):
                if positions[i + 2] - positions[i] <= window:
                    issues.append(make_style_issue(
                        family="structure_word_cluster",
                        severity="warning",
                        metric="structure_word_cluster_count",
                        description=f"结构词堆积：{word} 在 500 字窗口内出现 ≥3 次",
                        anchor=positions[i],
                        evidence=word,
                    ))
                    break
        return issues

    def _detect_sentence_rhythm_repeat(self, text: str) -> list[dict]:
        """句式节奏检测 → sentence_rhythm / warning issue。

        检测相邻句长是否过于接近（连续 ≥3 句字数差 ≤2）。
        """
        issues: list[dict] = []
        if not text:
            return issues
        sentences = [s for s in re.split(r"[。！？!?]", text) if s.strip()]
        if len(sentences) < 4:
            return issues
        streak = 1
        for i in range(1, len(sentences)):
            if abs(len(sentences[i]) - len(sentences[i - 1])) <= 2:
                streak += 1
                if streak >= 3:
                    # 方案 26 Part D：提取重复句式片段作为 evidence/target_span
                    start_idx = max(0, i - streak + 1)
                    streak_text = "。".join(sentences[start_idx:i + 1]) + "。"
                    issues.append(make_style_issue(
                        family="sentence_rhythm",
                        severity="warning",
                        metric="uniform_sentence_streak",
                        description=f"句长过于均匀：连续 {streak} 句字数差 ≤2",
                        evidence=streak_text,
                    ))
                    break
            else:
                streak = 1
        return issues

    def _detect_paragraph_shape_repeat(self, text: str) -> list[dict]:
        """段落形态重复检测 → paragraph_shape / warning issue。

        检测连续段落是否字数过于接近（连续 ≥3 段字数差 ≤10%）。
        """
        issues: list[dict] = []
        if not text:
            return issues
        paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
        if len(paragraphs) < 4:
            return issues
        streak = 1
        for i in range(1, len(paragraphs)):
            prev_len = len(paragraphs[i - 1]) or 1
            curr_len = len(paragraphs[i])
            if abs(curr_len - prev_len) / prev_len <= 0.1:
                streak += 1
                if streak >= 3:
                    # 方案 26 Part D：提取重复段落作为 evidence/target_span
                    start_idx = max(0, i - streak + 1)
                    streak_text = "\n".join(paragraphs[start_idx:i + 1])
                    issues.append(make_style_issue(
                        family="paragraph_shape",
                        severity="warning",
                        metric="paragraph_shape_repeat",
                        description=f"段落形态重复：连续 {streak} 段字数差 ≤10%",
                        evidence=streak_text,
                    ))
                    break
            else:
                streak = 1
        return issues


# ----------------------------------------------------------------------
# Part A：writer style_compliance 解析
# ----------------------------------------------------------------------

def parse_style_compliance(raw_output: str) -> tuple[str, dict]:
    """从 writer 输出中分离正文和 style_compliance 字段。

    方案 26 Part A2：writer 输出正文后附 style_compliance JSON 块。
    与方案 9 的 parse_writer_output 类似，但专用于 style_compliance。

    约定：style_compliance 与 scene_facts 可能在同一个 JSON 块中，
    也可能独立成块。本函数优先查找包含 style_compliance key 的块。

    Returns:
        (正文, style_compliance dict)。无 compliance 时返回 (raw_output, {})。
    """
    if not raw_output or not raw_output.strip():
        return raw_output or "", {}

    json_block_re = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
    matches = list(json_block_re.finditer(raw_output))
    if not matches:
        return raw_output, {}

    import json as _json
    for match in reversed(matches):
        try:
            payload = _json.loads(match.group(1))
        except _json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        compliance = payload.get("style_compliance")
        if isinstance(compliance, dict):
            text = raw_output[: match.start()].rstrip()
            return text, compliance
    return raw_output, {}


def validate_style_compliance(compliance: dict | None) -> list[str]:
    """校验 style_compliance 完整性，返回缺失字段列表。"""
    if not compliance or not isinstance(compliance, dict):
        return ["style_compliance_empty"]

    required = [
        "directives_received",
        "compliance_status",
        "deviations",
        "avoid_checked",
        "avoid_violations",
    ]
    return [f for f in required if f not in compliance]
