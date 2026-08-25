from __future__ import annotations

import re
from statistics import mean, pstdev

from app.models.quality_advisory import make_advisory
from app.services.quality_checkers.ai_flavor_taxonomy import (
    ABSTRACT_NOUNS,
    DIALOGUE_TAGS,
    EMOTION_LABELS,
    EXPLANATORY_PATTERNS,
    FALSE_RANGE_PATTERNS,
    FILLER_PHRASES,
    FORMULAIC_TRANSITIONS,
    INFLATED_SIGNIFICANCE,
    NEGATIVE_PARALLEL_PATTERNS,
    PROMOTIONAL_LANGUAGE,
    STRUCTURE_WORDS,
    SUMMARY_ENDINGS,
    THREE_PART_ESCALATION_PATTERNS,
    TECHNICAL_REGISTER,
    TEMPLATE_PHRASES,
    TIER1_WORDS,
    TIER3_DENSITY_WORDS,
    VAGUE_ATTRIBUTION,
    VISUAL_VERBS,
    OVEREXPLAIN_PHRASES,
    SIMILE_PATTERNS,
    SIMILE_WORD,
    SYNESTHESIA_PATTERNS,
    FALSE_AGENCY_PATTERNS,
)
from app.utils.dash_artifacts import count_dash_artifacts, iter_dash_artifacts


class AIFlavorChecker:
    """Deterministic, non-blocking Chinese prose AI-flavor diagnostics."""

    def check(
        self,
        text: str,
        scene_contract: dict | None = None,
        chapter_state: dict | None = None,
    ) -> dict:
        advisories = []
        if not text:
            return self._report([], {})

        metrics = {
            "text_length": len(text),
            "emdash_pair_hits": count_dash_artifacts(text),
            "dash_artifact_hits": count_dash_artifacts(text),
            "max_consecutive_emdash_pairs": self._max_consecutive_emdash_pairs(text),
            "template_phrase_hits": self._count_hits(text, TEMPLATE_PHRASES),
            "summary_ending_hits": self._count_hits(text, SUMMARY_ENDINGS),
            "technical_register_hits": self._count_hits(text, TECHNICAL_REGISTER),
            "emotion_label_hits": self._count_hits(text, EMOTION_LABELS),
            "dialogue_tag_hits": self._count_hits(text, DIALOGUE_TAGS),
            "explanatory_narration_hits": sum(
                len(re.findall(pattern, text)) for pattern in EXPLANATORY_PATTERNS
            ),
            "formulaic_transition_hits": self._count_hits(text, FORMULAIC_TRANSITIONS),
            "inflated_significance_hits": self._count_hits(text, INFLATED_SIGNIFICANCE),
            "promotional_language_hits": self._count_hits(text, PROMOTIONAL_LANGUAGE),
            "filler_phrase_hits": self._count_hits(text, FILLER_PHRASES),
            "negative_parallel_hits": sum(
                len(re.findall(pattern, text)) for pattern in NEGATIVE_PARALLEL_PATTERNS
            ),
            "false_range_hits": sum(
                len(re.findall(pattern, text)) for pattern in FALSE_RANGE_PATTERNS
            ),
            "three_part_escalation_hits": sum(
                len(re.findall(pattern, text)) for pattern in THREE_PART_ESCALATION_PATTERNS
            ),
            "vague_attribution_hits": self._count_hits(text, VAGUE_ATTRIBUTION),
            "overexplain_hits": self._count_hits(text, OVEREXPLAIN_PHRASES),
            "tier1_hit_count": self._count_hits(text, TIER1_WORDS),
            "tier3_hit_count": self._count_hits(text, TIER3_DENSITY_WORDS),
            "structure_word_hits": self._count_hits(text, STRUCTURE_WORDS),
            # ---- 新增：中文小说特有 AI 痕迹 ----
            "simile_hits": sum(len(re.findall(p, text)) for p in SIMILE_PATTERNS),
            "simile_word_count": text.count(SIMILE_WORD),
            "synesthesia_hits": sum(len(re.findall(p, text)) for p in SYNESTHESIA_PATTERNS),
            "false_agency_hits": sum(len(re.findall(p, text)) for p in FALSE_AGENCY_PATTERNS),
        }
        metrics.update(self._rhythm_metrics(text))
        metrics.update(self._tier_cluster_metrics(text))
        metrics["tier3_density"] = round(
            metrics["tier3_hit_count"] / max(len(text), 1),
            4,
        )

        advisories.extend(self._template_phrase_advisories(text, metrics))
        advisories.extend(self._format_artifact_advisories(text))
        advisories.extend(self._dialogue_tag_advisories(text, metrics))
        advisories.extend(self._emotion_label_advisories(text, metrics))
        advisories.extend(self._technical_register_advisories(text, metrics))
        advisories.extend(self._explanatory_narration_advisories(text, metrics))
        advisories.extend(self._formulaic_transition_advisories(text, metrics))
        advisories.extend(self._punctuation_artifact_advisories(text, metrics))
        advisories.extend(self._rhythm_advisories(text, metrics))
        advisories.extend(self._inflated_significance_advisories(text, metrics))
        advisories.extend(self._promotional_language_advisories(text, metrics))
        advisories.extend(self._filler_phrase_advisories(text, metrics))
        advisories.extend(self._negative_parallel_advisories(text, metrics))
        advisories.extend(self._false_range_advisories(text, metrics))
        advisories.extend(self._three_part_escalation_advisories(text, metrics))
        advisories.extend(self._tier_cluster_advisories(text, metrics))
        # ---- 新增：中文小说特有 AI 痕迹检查 ----
        advisories.extend(self._simile_density_advisories(text, metrics))
        advisories.extend(self._synesthesia_formula_advisories(text, metrics))
        advisories.extend(self._false_agency_advisories(text, metrics))

        return self._report(advisories, metrics)

    def _report(self, advisories: list[dict], metrics: dict) -> dict:
        high = sum(1 for item in advisories if item.get("severity") == "high")
        medium = sum(1 for item in advisories if item.get("severity") == "medium")
        level = "red" if high >= 2 else "yellow" if high or medium else "green"
        score = max(0, 100 - high * 18 - medium * 8 - (len(advisories) - high - medium) * 3)
        return {
            "schema_version": 1,
            "status": "ok",
            "overall_level": level,
            "score": score,
            "summary": self._summary(level, advisories),
            "metrics": metrics,
            "advisories": advisories,
        }

    @staticmethod
    def _summary(level: str, advisories: list[dict]) -> str:
        if level == "green":
            return "未发现明显模板化或机械表达痕迹。"
        types = []
        for item in advisories:
            if item.get("type") not in types:
                types.append(item.get("type"))
        return "发现疑似 AI 味风险：" + "、".join(str(t) for t in types[:5])

    @staticmethod
    def _count_hits(text: str, words: list[str]) -> int:
        return sum(text.count(word) for word in words)

    @staticmethod
    def _max_consecutive_emdash_pairs(text: str) -> int:
        matches = list(iter_dash_artifacts(text or ""))
        if not matches:
            return 0
        longest = 1
        current = 1
        previous = matches[0]
        for match in matches[1:]:
            gap = (text or "")[previous.end():match.start()]
            if gap.strip():
                current = 1
            else:
                current += 1
                longest = max(longest, current)
            previous = match
        return longest

    @staticmethod
    def _snip(text: str, needle: str, window: int = 28) -> str:
        pos = text.find(needle)
        if pos < 0:
            return needle
        start = max(0, pos - window)
        end = min(len(text), pos + len(needle) + window)
        return text[start:end].replace("\n", " ")

    def _template_phrase_advisories(self, text: str, metrics: dict) -> list[dict]:
        hits = metrics["template_phrase_hits"] + metrics["summary_ending_hits"]
        density = hits / max(len(text) / 1000, 1)
        if density < 4:
            return []
        phrase = next((p for p in TEMPLATE_PHRASES + SUMMARY_ENDINGS if p in text), "")
        return [make_advisory(
            "ai_template_phrase",
            "high" if density >= 7 else "medium",
            f"模板化承接或总结句密度偏高：每千字约 {density:.1f} 处。",
            target_span=self._snip(text, phrase) if phrase else None,
            expected_behavior="用角色动作、场景后果或具体细节替代泛化总结。",
            detector="ai_flavor_checker",
            confidence=min(0.95, 0.45 + density / 10),
        )]

    def _format_artifact_advisories(self, text: str) -> list[dict]:
        advisories = []
        heading_hits = len(re.findall(r"(?m)^\s*(?:第?[一二三四五六七八九十\d]+[、.．]|#+\s+|[-*]\s+)", text))
        colon_lines = len(re.findall(r"(?m)^[^。！？\n]{2,18}[:：]", text))
        if heading_hits + colon_lines >= 3:
            advisories.append(make_advisory(
                "ai_format_artifact",
                "medium",
                f"正文中出现 {heading_hits + colon_lines} 处清单、小标题或说明格式痕迹。",
                expected_behavior="小说正文应优先保持叙事段落形态，避免报告式结构。",
                detector="ai_flavor_checker",
                confidence=0.78,
            ))
        return advisories

    def _dialogue_tag_advisories(self, text: str, metrics: dict) -> list[dict]:
        count = metrics["dialogue_tag_hits"]
        dialogue_marks = text.count("“") + text.count('"')
        if dialogue_marks < 6 or count < 5:
            return []
        ratio = count / max(dialogue_marks, 1)
        if ratio < 0.35:
            return []
        tag = next((t for t in DIALOGUE_TAGS if t in text), "")
        return [make_advisory(
            "ai_dialogue_tag_overuse",
            "medium",
            f"对白标签使用偏机械：标签 {count} 次，对话标记 {dialogue_marks} 次。",
            target_span=self._snip(text, tag) if tag else None,
            expected_behavior="用动作、停顿、视线和语气区分说话人，减少重复标签。",
            detector="ai_flavor_checker",
            confidence=0.72,
        )]

    def _emotion_label_advisories(self, text: str, metrics: dict) -> list[dict]:
        density = metrics["emotion_label_hits"] / max(len(text) / 1000, 1)
        if density < 6:
            return []
        label = next((e for e in EMOTION_LABELS if e in text), "")
        return [make_advisory(
            "ai_emotion_label",
            "medium",
            f"直接情绪命名密度偏高：每千字约 {density:.1f} 处。",
            target_span=self._snip(text, label) if label else None,
            expected_behavior="让情绪通过选择、动作、感官和对白潜台词外化。",
            detector="ai_flavor_checker",
            confidence=0.68,
        )]

    def _technical_register_advisories(self, text: str, metrics: dict) -> list[dict]:
        density = metrics["technical_register_hits"] / max(len(text) / 1000, 1)
        if density < 5:
            return []
        word = next((w for w in TECHNICAL_REGISTER if w in text), "")
        return [make_advisory(
            "ai_technical_register",
            "low" if density < 8 else "medium",
            f"正文疑似混入说明文或技术报告语体：每千字约 {density:.1f} 处。",
            target_span=self._snip(text, word) if word else None,
            expected_behavior="将抽象说明改写为人物能感知到的具体场景信息。",
            detector="ai_flavor_checker",
            confidence=0.62,
        )]

    def _explanatory_narration_advisories(self, text: str, metrics: dict) -> list[dict]:
        density = metrics["explanatory_narration_hits"] / max(len(text) / 1000, 1)
        if density < 3:
            return []
        return [make_advisory(
            "ai_explanatory_narration",
            "medium",
            f"解释人物感受或动机的句式偏密集：每千字约 {density:.1f} 处。",
            expected_behavior="用人物选择、动作后果和潜台词呈现动机，减少直接替读者解释。",
            detector="ai_flavor_checker",
            confidence=0.7,
        )]

    def _formulaic_transition_advisories(self, text: str, metrics: dict) -> list[dict]:
        density = metrics["formulaic_transition_hits"] / max(len(text) / 1000, 1)
        if density < 5:
            return []
        word = next((item for item in FORMULAIC_TRANSITIONS if item in text), "")
        return [make_advisory(
            "ai_formulaic_transition",
            "medium",
            f"公式化转折和承接词密度偏高：每千字约 {density:.1f} 处。",
            target_span=self._snip(text, word) if word else None,
            expected_behavior="让场景变化由动作、视线、声音或因果结果自然衔接。",
            detector="ai_flavor_checker",
            confidence=0.68,
        )]

    def _punctuation_artifact_advisories(self, text: str, metrics: dict) -> list[dict]:
        """Keep the checker diagnostic-only for dash usage.

        ``check()`` already emits the raw ``emdash_pair_hits`` and
        ``max_consecutive_emdash_pairs`` metrics.  The sole acceptance
        thresholds live in the mounted anti_ai_prose Skill contract and are
        evaluated by AgentSkillValidator; duplicating them here previously
        created a second, divergent gate.
        """
        return []

    @staticmethod
    def _split_sentences(text: str) -> list[str]:
        return [s.strip() for s in re.split(r"[。！？!?]+", text) if s.strip()]

    def _rhythm_metrics(self, text: str) -> dict:
        sentences = self._split_sentences(text)
        paragraphs = [p.strip() for p in re.split(r"\n+", text) if p.strip()]
        sentence_lengths = [len(s) for s in sentences]
        paragraph_lengths = [len(p) for p in paragraphs]
        sentence_mean = mean(sentence_lengths) if sentence_lengths else 0
        sentence_cv = (
            pstdev(sentence_lengths) / sentence_mean
            if len(sentence_lengths) >= 2 and sentence_mean
            else 0
        )
        paragraph_mean = mean(paragraph_lengths) if paragraph_lengths else 0
        paragraph_cv = (
            pstdev(paragraph_lengths) / paragraph_mean
            if len(paragraph_lengths) >= 2 and paragraph_mean
            else 0
        )
        return {
            "sentence_count": len(sentences),
            "sentence_length_variance": round(pstdev(sentence_lengths), 3) if len(sentence_lengths) >= 2 else 0,
            "sentence_length_cv": round(sentence_cv, 3),
            "paragraph_count": len(paragraphs),
            "paragraph_length_variance": round(pstdev(paragraph_lengths), 3) if len(paragraph_lengths) >= 2 else 0,
            "paragraph_length_cv": round(paragraph_cv, 3),
            # ---- 新增：burstiness 和 lexical_diversity（诊断用，不阻断） ----
            "burstiness_score": round(pstdev(sentence_lengths), 3) if len(sentence_lengths) >= 2 else 0,
            "lexical_diversity_score": round(self._lexical_diversity(text), 3),
        }

    @staticmethod
    def _lexical_diversity(text: str) -> float:
        """Type-Token Ratio：不同词数 / 总词数。数值越低越像 AI。"""
        # 简单中文分词：按字符切分，2-3 字为一组
        # 对中文小说，用字符级 TTR 作为近似值
        if not text:
            return 0.0
        # 用 2-gram 作为"词"的近似
        tokens = [text[i:i+2] for i in range(len(text) - 1)]
        if not tokens:
            return 0.0
        unique = len(set(tokens))
        total = len(tokens)
        return unique / total if total else 0.0

    def _tier_cluster_metrics(self, text: str) -> dict:
        paragraphs = [p.strip() for p in re.split(r"\n+", text or "") if p.strip()]
        visual_cluster_count = 0
        abstract_cluster_count = 0
        structure_cluster_count = 0
        for paragraph in paragraphs or [text or ""]:
            visual_hits = self._count_hits(paragraph, VISUAL_VERBS)
            abstract_hits = self._count_hits(paragraph, ABSTRACT_NOUNS)
            structure_hits = self._count_hits(paragraph, STRUCTURE_WORDS)
            if visual_hits >= 2:
                visual_cluster_count += 1
            if abstract_hits >= 3:
                abstract_cluster_count += 1
            if structure_hits >= 3:
                structure_cluster_count += 1
        return {
            "visual_verb_cluster_count": visual_cluster_count,
            "abstract_noun_cluster_count": abstract_cluster_count,
            "tier2_cluster_count": visual_cluster_count + abstract_cluster_count,
            "structure_word_cluster_count": structure_cluster_count,
        }

    def _rhythm_advisories(self, text: str, metrics: dict) -> list[dict]:
        if metrics.get("sentence_count", 0) < 10:
            return []
        sentence_cv = metrics.get("sentence_length_cv", 0)
        paragraph_cv = metrics.get("paragraph_length_cv", 0)
        # 收紧阈值：0.32 → 0.40（人类写作 CV > 0.4）
        if sentence_cv >= 0.40:
            return []
        if metrics.get("paragraph_count", 0) >= 4 and paragraph_cv >= 0.35:
            return []
        severity = "high" if sentence_cv < 0.25 else "medium"
        return [make_advisory(
            "ai_rhythm_uniformity",
            severity,
            f"句段节奏变化偏低：句长变异系数 {sentence_cv:.2f}，段长变异系数 {paragraph_cv:.2f}。",
            expected_behavior="在动作、心理、环境和对白之间形成更明显的长短句变化。",
            detector="ai_flavor_checker",
            confidence=0.66,
        )]

    def _inflated_significance_advisories(self, text: str, metrics: dict) -> list[dict]:
        density = metrics["inflated_significance_hits"] / max(len(text) / 1000, 1)
        if density < 3:
            return []
        word = next((w for w in INFLATED_SIGNIFICANCE if w in text), "")
        return [make_advisory(
            "ai_inflated_significance",
            "medium" if density < 6 else "high",
            f"夸大意义或遗产式表达密度偏高：每千字约 {density:.1f} 处。",
            target_span=self._snip(text, word) if word else None,
            expected_behavior="直接陈述事实和结果，不加象征意义或历史定位的修饰。",
            detector="ai_flavor_checker",
            confidence=0.72,
        )]

    def _promotional_language_advisories(self, text: str, metrics: dict) -> list[dict]:
        density = metrics["promotional_language_hits"] / max(len(text) / 1000, 1)
        if density < 3:
            return []
        word = next((w for w in PROMOTIONAL_LANGUAGE if w in text), "")
        return [make_advisory(
            "ai_promotional_language",
            "medium" if density < 6 else "high",
            f"宣传性或广告式语言密度偏高：每千字约 {density:.1f} 处。",
            target_span=self._snip(text, word) if word else None,
            expected_behavior="用具体细节替代夸张赞美，保持叙事的中立和克制。",
            detector="ai_flavor_checker",
            confidence=0.7,
        )]

    def _filler_phrase_advisories(self, text: str, metrics: dict) -> list[dict]:
        density = metrics["filler_phrase_hits"] / max(len(text) / 1000, 1)
        if density < 3:
            return []
        phrase = next((p for p in FILLER_PHRASES if p in text), "")
        return [make_advisory(
            "ai_filler_phrase",
            "medium",
            f"填充短语密度偏高：每千字约 {density:.1f} 处。",
            target_span=self._snip(text, phrase) if phrase else None,
            expected_behavior="删除不增加信息的铺垫词，直接进入实质内容。",
            detector="ai_flavor_checker",
            confidence=0.68,
        )]

    def _negative_parallel_advisories(self, text: str, metrics: dict) -> list[dict]:
        hits = metrics["negative_parallel_hits"]
        if hits < 1:
            return []
        match = ""
        for pattern in NEGATIVE_PARALLEL_PATTERNS:
            m = re.search(pattern, text)
            if m:
                match = m.group()
                break
        # 收紧阈值：>=1 触发 medium，>=3 触发 high，>=6 触发 critical
        if hits >= 6:
            severity = "critical"
        elif hits >= 3:
            severity = "high"
        else:
            severity = "medium"
        return [make_advisory(
            "ai_negative_parallel",
            severity,
            f"否定式排比句式出现 {hits} 次。",
            target_span=match or None,
            expected_behavior="直接陈述核心观点，避免'不是A，而是B'的排比包装。",
            detector="ai_flavor_checker",
            confidence=0.75,
        )]

    def _false_range_advisories(self, text: str, metrics: dict) -> list[dict]:
        hits = metrics["false_range_hits"]
        if hits < 1:
            return []
        match = ""
        for pattern in FALSE_RANGE_PATTERNS:
            m = re.search(pattern, text)
            if m:
                match = m.group()
                break
        return [make_advisory(
            "ai_false_range",
            "medium",
            f"虚假范围结构出现 {hits} 次。",
            target_span=match or None,
            expected_behavior="列举具体项目而非构造从X到Y的虚假连续统。",
            detector="ai_flavor_checker",
            confidence=0.65,
        )]

    def _three_part_escalation_advisories(self, text: str, metrics: dict) -> list[dict]:
        hits = metrics["three_part_escalation_hits"]
        if hits < 1:
            return []
        match = ""
        for pattern in THREE_PART_ESCALATION_PATTERNS:
            m = re.search(pattern, text)
            if m:
                match = m.group()
                break
        return [make_advisory(
            "ai_three_part_escalation",
            "high" if hits >= 2 else "medium",
            f"三段式递进句式出现 {hits} 次。",
            target_span=match or None,
            expected_behavior="拆掉'不仅/而且/更'等递进框架，保留真正的信息推进。",
            detector="ai_flavor_checker",
            confidence=0.76,
        )]

    def _tier_cluster_advisories(self, text: str, metrics: dict) -> list[dict]:
        advisories: list[dict] = []
        if int(metrics.get("tier2_cluster_count") or 0) > 0:
            advisories.append(make_advisory(
                "ai_tier2_cluster",
                "medium",
                (
                    "同段视觉动词或抽象名词形成集群："
                    f"视觉动词段落 {metrics.get('visual_verb_cluster_count', 0)}，"
                    f"抽象名词段落 {metrics.get('abstract_noun_cluster_count', 0)}。"
                ),
                expected_behavior="保留少量必要词，其余改成具体动作、物件、感官或人物选择。",
                detector="ai_flavor_checker",
                confidence=0.67,
            ))
        if float(metrics.get("tier3_density") or 0) > 0.03:
            advisories.append(make_advisory(
                "ai_tier3_density",
                "low",
                f"低信息泛化词密度偏高：{metrics.get('tier3_density'):.3f}。",
                expected_behavior="把'显著/有效/独特/深刻'等评价词改成可观察的具体结果。",
                detector="ai_flavor_checker",
                confidence=0.58,
            ))
        return advisories

    # ---- 新增：中文小说特有 AI 痕迹检查方法 ----

    def _simile_density_advisories(self, text: str, metrics: dict) -> list[dict]:
        """明喻密度检查：双重检测——SIMILE_PATTERNS 匹配 + '像'字独立密度。

        中文小说中'像'字如果每千字 > 3 次，几乎可以确定明喻过度。
        同时检测'像...一样/似的/般'等完整明喻结构的密度。

        通用修复 S-1：密度类问题的检测是全文密度驱动（多个'像'字共同触发），
        但修复若只针对单一 target_span，LLM 改 1 处后剩余 '像' 字密度仍超阈值，
        advisory 重新产生，修复-检测循环数学上无法收敛。
        因此 advisory 携带 evidence_samples（所有命中位置），让 lane 指令
        指示 LLM 一次性系统性降低全文密度，而非只改 1 处。
        """
        hits = metrics.get("simile_hits", 0)
        word_count = metrics.get("simile_word_count", 0)
        text_len = max(len(text), 1)
        density = hits / max(text_len / 1000, 1)
        word_density = word_count / max(text_len / 1000, 1)

        # 双重触发：完整明喻结构密度 >= 1.5/千字，或'像'字密度 >= 3.0/千字
        if density < 1.5 and word_density < 3.0:
            return []

        # 取较高 severity
        if density >= 3.0 or word_density >= 5.0:
            severity = "high"
        else:
            severity = "medium"

        # 收集所有"像"字所在短句，作为 evidence_samples。
        # 这些是密度类问题需要 LLM 系统性处理的全部位置，而非只 1 处 target_span。
        all_candidates = re.findall(r"[^。！？\n]{0,30}像[^。！？\n]{0,50}", text)
        # 按长度降序，便于 LLM 优先处理长句
        all_samples = sorted(
            {c.strip() for c in all_candidates if c.strip()},
            key=len,
            reverse=True,
        )

        # 优先取完整明喻结构作为 target_span
        match = ""
        for pattern in SIMILE_PATTERNS:
            m = re.search(pattern, text)
            if m:
                match = m.group()
                break

        # 回退：当无完整明喻结构匹配但'像'字密度高时，取最长短句作为 target_span
        if not match and all_samples:
            match = all_samples[0]

        detail = f"明喻句式密度偏高：完整结构 {hits} 处（每千字 {density:.1f}），'像'字 {word_count} 次（每千字 {word_density:.1f}）。"
        return [make_advisory(
            "ai_simile_overuse",
            severity,
            detail,
            target_span=match or None,
            expected_behavior=(
                "系统性降低全文'像'字密度：将 evidence_samples 中列出的多个明喻位置"
                "改为直接动作描写或感官描写，每处保持原句叙事信息不丢失。"
                "禁止只改 target_span 一处。"
            ),
            detector="ai_flavor_checker",
            confidence=0.72,
            # 通用修复 S-1：密度类问题携带所有命中位置，让 LLM 一次性处理
            evidence_samples=all_samples[:20],
            # 通用修复 S-5：携带对应的可测量 validator metric 名，
            # 让弱后检能正确路由到 ai_flavor validator 重算路径
            validator_metrics=["simile_hits", "simile_word_count"],
        )]

    def _synesthesia_formula_advisories(self, text: str, metrics: dict) -> list[dict]:
        """通感公式检查：'X中带着一丝Y'等每章 ≤2 处。"""
        hits = metrics.get("synesthesia_hits", 0)
        if hits < 1:
            return []
        if hits <= 2:
            return []
        match = ""
        for pattern in SYNESTHESIA_PATTERNS:
            m = re.search(pattern, text)
            if m:
                match = m.group()
                break
        severity = "high" if hits >= 4 else "medium"
        return [make_advisory(
            "ai_synesthesia_formula",
            severity,
            f"通感公式句式出现 {hits} 次。",
            target_span=match or None,
            expected_behavior="用具体感官细节替代'X中带着一丝Y'的通感公式。",
            detector="ai_flavor_checker",
            confidence=0.7,
        )]

    def _false_agency_advisories(self, text: str, metrics: dict) -> list[dict]:
        """虚假代理检查：无生命物体执行人类动作。来自 stop-slop。"""
        hits = metrics.get("false_agency_hits", 0)
        if hits < 1:
            return []
        match = ""
        for pattern in FALSE_AGENCY_PATTERNS:
            m = re.search(pattern, text)
            if m:
                match = m.group()
                break
        severity = "high" if hits >= 3 else "medium"
        return [make_advisory(
            "ai_false_agency",
            severity,
            f"虚假代理句式出现 {hits} 次：无生命物体执行人类动作。",
            target_span=match or None,
            expected_behavior="用具体角色作为动作执行者，避免'决定出现了'、'变化发生了'等无主语句式。",
            detector="ai_flavor_checker",
            confidence=0.68,
        )]
