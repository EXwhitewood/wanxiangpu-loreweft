"""信息密度检测器 —— 粗粒度启发式，防止信息倾倒。

检测三类问题：
1. 解释性句式密度过高（"原来""事实上""她知道"等）
2. 设定说明段落过长（纯设定说明超过阈值）
3. must_show 条目过多导致信息注入速率失控

设计原则：不做精确计数，只做粗粒度护栏。
"""

from __future__ import annotations

import re

from app.models.violation import make_violation


# 解释性标记词 —— 出现这些词通常意味着在"讲述"而非"展示"
_EXPLANATORY_MARKERS: list[str] = [
    "原来", "事实上", "实际上", "其实", "要知道",
    "简单来说", "总而言之", "也就是说", "换言之",
    "所谓的", "顾名思义", "不言而喻",
]

# 设定说明段落的开头标记
_SETTING_PARAGRAPH_OPENERS: list[str] = [
    "这", "那", "此", "该", "其",
]

# 设定说明段落的特征词
_SETTING_KEYWORDS: list[str] = [
    "等级", "体系", "制度", "规矩", "法则", "原理",
    "分为", "共有", "分为以下", "依次为", "分别是",
    "修炼", "灵根", "丹药", "法器", "符箓", "功法",
    "门派", "宗门", "势力", "组织", "阶层",
]

# 每1000字允许的解释性标记数量上限
_EXPLANATORY_DENSITY_PER_1000 = 5

# 设定说明段落的最大字数
_SETTING_PARAGRAPH_MAX_CHARS = 80

# must_show 条目数量警告阈值
_MUST_SHOW_WARNING_THRESHOLD = 5
_MUST_SHOW_CRITICAL_THRESHOLD = 7
# 5条=偏满(advisory), >5条=warning, >7条=critical
_MUST_SHOW_ADVISORY_THRESHOLD = 5


class InfoDensityChecker:
    """检查场景正文的信息密度，防止信息倾倒。"""

    def check(
        self,
        text: str,
        scene_contract: dict,
        chapter_state: dict | None = None,
    ) -> list[dict]:
        violations: list[dict] = []

        if not text:
            return violations

        violations.extend(self._check_explanatory_density(text))
        violations.extend(self._check_setting_paragraphs(text))
        violations.extend(self._check_must_show_overload(scene_contract))
        violations.extend(self._check_info_reveal_burst(text, scene_contract))

        return violations

    # ------------------------------------------------------------------
    # 1. 解释性句式密度
    # ------------------------------------------------------------------
    def _check_explanatory_density(self, text: str) -> list[dict]:
        violations: list[dict] = []
        text_len = max(len(text), 1)
        segments = text_len / 1000

        marker_hits: list[tuple[str, int]] = []
        for marker in _EXPLANATORY_MARKERS:
            for m in re.finditer(re.escape(marker), text):
                marker_hits.append((marker, m.start()))

        total_hits = len(marker_hits)
        density = total_hits / segments

        if density > _EXPLANATORY_DENSITY_PER_1000 * 2:
            # 严重信息倾倒
            top_markers = self._top_markers(marker_hits)
            violations.append(make_violation(
                "info_dump_high_density",
                "high",
                f"解释性句式密度过高：每千字{density:.1f}个（阈值{_EXPLANATORY_DENSITY_PER_1000 * 2}），"
                f"高频标记：{top_markers}。大量使用讲述而非展示，信息倾倒风险高。",
                source="deterministic",
                expected_behavior="通过角色体验和行动揭示信息，减少解释性句式",
            ))
        elif density > _EXPLANATORY_DENSITY_PER_1000:
            top_markers = self._top_markers(marker_hits)
            violations.append(make_violation(
                "info_dump_moderate_density",
                "medium",
                f"解释性句式密度偏高：每千字{density:.1f}个（阈值{_EXPLANATORY_DENSITY_PER_1000}），"
                f"高频标记：{top_markers}。建议增加展示性描写。",
                source="deterministic",
                expected_behavior="优先通过场景行动和对话揭示信息",
            ))

        return violations

    # ------------------------------------------------------------------
    # 2. 设定说明段落过长
    # ------------------------------------------------------------------
    def _check_setting_paragraphs(self, text: str) -> list[dict]:
        violations: list[dict] = []

        paragraphs = re.split(r"\n+", text)
        for para in paragraphs:
            para = para.strip()
            if len(para) < _SETTING_PARAGRAPH_MAX_CHARS:
                continue

            setting_keyword_count = sum(1 for kw in _SETTING_KEYWORDS if kw in para)
            if setting_keyword_count < 2:
                continue

            # 检查段落中是否缺少角色动作/感知（纯设定说明的标志）
            action_markers = ["她", "他", "说", "想", "看", "听", "走", "站", "坐"]
            has_action = any(m in para for m in action_markers)

            if not has_action and setting_keyword_count >= 3:
                violations.append(make_violation(
                    "setting_paragraph_too_long",
                    "medium",
                    f"设定说明段落过长（{len(para)}字，含{setting_keyword_count}个设定关键词），"
                    f"缺少角色动作/感知。建议拆分为角色体验中的碎片化揭示。",
                    source="deterministic",
                    target_span=para[:60],
                    expected_behavior="设定信息应通过角色体验逐步揭示，单段不超过80字",
                ))

        return violations

    # ------------------------------------------------------------------
    # 3. must_show 条目过载
    # ------------------------------------------------------------------
    def _check_must_show_overload(self, scene_contract: dict) -> list[dict]:
        violations: list[dict] = []

        must_show = scene_contract.get("must_show", [])
        if not must_show:
            return violations

        count = len(must_show)

        if count > _MUST_SHOW_CRITICAL_THRESHOLD:
            # >7: critical，需要合同修复
            violations.append(make_violation(
                "must_show_overload",
                "high",
                f"场景合同 must_show 包含{count}条必须展示项，远超推荐上限"
                f"（{_MUST_SHOW_WARNING_THRESHOLD}条），极易导致信息倾倒。"
                f"建议拆分到更多场景或由合同预算修复器自动调整。",
                source="deterministic",
                expected_behavior=f"must_show 条目建议不超过{_MUST_SHOW_WARNING_THRESHOLD}条，"
                                 f"多余项应分散到相邻场景",
                suggested_strategy="contract_budget_adjust",
                scope="scene_contract",
                repairable_by_text=False,
                repairable_by_contract=True,
            ))
        elif count > _MUST_SHOW_WARNING_THRESHOLD:
            # >5 且 <=7: warning，建议调整合同
            violations.append(make_violation(
                "must_show_overload",
                "medium",
                f"场景合同 must_show 包含{count}条必须展示项，超过推荐上限"
                f"（{_MUST_SHOW_WARNING_THRESHOLD}条），信息密度偏高。"
                f"建议调整合同条目或分散到相邻场景。",
                source="deterministic",
                expected_behavior=f"must_show 条目建议不超过{_MUST_SHOW_WARNING_THRESHOLD}条",
                suggested_strategy="contract_budget_adjust",
                scope="scene_contract",
                repairable_by_text=False,
                repairable_by_contract=True,
            ))
        elif count == _MUST_SHOW_ADVISORY_THRESHOLD:
            # ==5: advisory，仅提示不阻断
            violations.append(make_violation(
                "must_show_overload",
                "low",
                f"场景合同 must_show 包含{count}条必须展示项，已达到推荐上限"
                f"（{_MUST_SHOW_WARNING_THRESHOLD}条），注意控制信息密度。",
                source="deterministic",
                expected_behavior=f"must_show 条目建议不超过{_MUST_SHOW_WARNING_THRESHOLD}条",
                suggested_strategy="manual_review",
                scope="advisory",
                repairable_by_text=False,
                repairable_by_contract=False,
            ))

        return violations

    # ------------------------------------------------------------------
    # 4. 信息揭示爆发检测
    # ------------------------------------------------------------------
    def _check_info_reveal_burst(self, text: str, scene_contract: dict) -> list[dict]:
        """检测短时间内大量新概念首次出现的模式。"""
        violations: list[dict] = []

        # 提取正文中"首次定义"模式的句子
        definition_patterns = [
            r"[—\-—]\s*[^，。！？]{2,15}(?:是|指|叫做?|称为?|叫做?|就是)",
            r"(?:所谓|名为|名叫|叫做?)\s*[^，。！？]{2,15}",
            r"[^，。！？]{2,10}(?:的规矩|的法则|的原理|的体系|的制度)",
        ]

        definition_count = 0
        for pattern in definition_patterns:
            definition_count += len(re.findall(pattern, text))

        # 每1000字超过3个定义性句子视为信息爆发
        text_len = max(len(text), 1)
        definitions_per_1000 = definition_count / (text_len / 1000)

        if definitions_per_1000 > 5:
            violations.append(make_violation(
                "info_reveal_burst",
                "high",
                f"正文中定义性句子密度过高：每千字{definitions_per_1000:.1f}个定义"
                f"（共{definition_count}个），信息揭示过于集中。",
                source="deterministic",
                expected_behavior="新概念应分散揭示，每千字定义性句子不超过3个",
            ))
        elif definitions_per_1000 > 3:
            violations.append(make_violation(
                "info_reveal_burst",
                "medium",
                f"正文中定义性句子密度偏高：每千字{definitions_per_1000:.1f}个定义"
                f"（共{definition_count}个），建议分散信息揭示。",
                source="deterministic",
                expected_behavior="新概念应分散揭示，每千字定义性句子不超过3个",
            ))

        return violations

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    @staticmethod
    def _top_markers(marker_hits: list[tuple[str, int]], top_n: int = 3) -> str:
        from collections import Counter
        counter = Counter(m for m, _ in marker_hits)
        top = counter.most_common(top_n)
        return "、".join(f"「{m}」({c}次)" for m, c in top)
