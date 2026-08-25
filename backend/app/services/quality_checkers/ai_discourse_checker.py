from __future__ import annotations

import re
from collections import Counter
from typing import Any

from app.models.quality_advisory import make_advisory


DEFAULT_SENTENCE_SHELLS = [
    r"不是.{1,30}而是",
    r"不仅.{1,30}(?:更|而且)",
    r"真正.{0,12}的是",
    r"重要的不是.{1,24}而是",
    r"(?:答案|原因|道理)(?:其实)?很简单[：:]",
    r".{1,18}的本质[，,]?是",
    r"无论.{1,18}还是.{1,18}[，,]?都",
]

DEFAULT_EXPLANATION_STARTS = (
    "这说明",
    "这意味着",
    "这表明",
    "这体现了",
    "这反映出",
    "由此可见",
    "换句话说",
)

DEFAULT_MORAL_OVERCLARITY = (
    "真正的勇气",
    "真正的成长",
    "真正的爱",
    "学会了珍惜",
    "终于明白了",
    "一切都会好起来",
    "彼此理解",
    "放下了过去",
    "获得了救赎",
)

DEFAULT_FAKE_INTERACTION = (
    "你是否也",
    "不妨想一想",
    "让我们一起",
    "我们不妨",
    "值得我们思考",
)


class AIDiscourseChecker:
    """Deterministic paragraph- and discourse-level AI-flavor diagnostics."""

    def check(
        self,
        text: str,
        *,
        resource_packs: dict[str, dict] | None = None,
    ) -> dict:
        text = text or ""
        packs = resource_packs or {}
        patterns = self._patterns(packs)
        paragraphs = [item.strip() for item in re.split(r"\n+", text) if item.strip()]
        sentences = self._sentences(text)

        shell_matches = self._regex_matches(text, patterns["sentence_shells"])
        action_explanations = self._action_then_explanation(sentences, patterns["explanation_starts"])
        semantic_restatements = self._semantic_restatements(sentences)
        repeated_shapes = self._repeated_paragraph_shapes(paragraphs, patterns["explanation_starts"])
        mirrored_openings = self._mirrored_openings(paragraphs)
        double_conclusions = self._double_conclusions(paragraphs, patterns["explanation_starts"])
        moral_hits = self._phrase_matches(text, patterns["moral_overclarity"])
        fake_interactions = self._phrase_matches(text, patterns["fake_interaction"])

        metrics = {
            "paragraph_count": len(paragraphs),
            "sentence_count": len(sentences),
            "sentence_shell_count": len(shell_matches),
            "paragraph_shape_repeat_count": repeated_shapes,
            "semantic_restatement_count": len(semantic_restatements),
            "action_then_explanation_count": len(action_explanations),
            "mirrored_paragraph_opening_count": mirrored_openings,
            "double_conclusion_count": double_conclusions,
            "moral_overclarity_count": len(moral_hits),
            "fake_interaction_count": len(fake_interactions),
        }
        advisories = self._advisories(
            metrics,
            shell_matches=shell_matches,
            semantic_restatements=semantic_restatements,
            action_explanations=action_explanations,
            moral_hits=moral_hits,
        )
        return {
            "schema_version": 1,
            "status": "ok",
            "metrics": metrics,
            "advisories": advisories,
            "summary": (
                "未发现明显语篇模板化问题。"
                if not advisories
                else f"发现 {len(advisories)} 类语篇级 AI 味风险。"
            ),
        }

    @staticmethod
    def _patterns(packs: dict[str, dict]) -> dict[str, list[str]]:
        merged: dict[str, list[str]] = {
            "sentence_shells": list(DEFAULT_SENTENCE_SHELLS),
            "explanation_starts": list(DEFAULT_EXPLANATION_STARTS),
            "moral_overclarity": list(DEFAULT_MORAL_OVERCLARITY),
            "fake_interaction": list(DEFAULT_FAKE_INTERACTION),
        }
        for pack in packs.values():
            if not isinstance(pack, dict):
                continue
            for key in merged:
                values = pack.get(key)
                if isinstance(values, list):
                    merged[key].extend(str(item) for item in values if str(item).strip())
        return {key: list(dict.fromkeys(values)) for key, values in merged.items()}

    @staticmethod
    def _sentences(text: str) -> list[str]:
        normalized = str(text or "").strip()
        if not normalized:
            return []
        return [
            item.strip()
            for item in re.findall(r"[^。！？!?\n]+(?:[。！？!?]|$)", normalized)
            if item.strip()
        ]

    @staticmethod
    def _regex_matches(text: str, patterns: list[str]) -> list[str]:
        matches: list[str] = []
        for pattern in patterns:
            try:
                matches.extend(match.group(0) for match in re.finditer(pattern, text))
            except re.error:
                continue
        return matches

    @staticmethod
    def _phrase_matches(text: str, phrases: list[str]) -> list[str]:
        return [phrase for phrase in phrases if phrase and phrase in text]

    @staticmethod
    def _action_then_explanation(sentences: list[str], starts: list[str]) -> list[str]:
        results: list[str] = []
        action_re = re.compile(r"[推拉按抓放扔摔砸撕烧关开递退走跑停转]")
        for previous, current in zip(sentences, sentences[1:]):
            if action_re.search(previous) and current.startswith(tuple(starts)):
                results.append(previous[-30:] + current[:30])
        return results

    def _semantic_restatements(self, sentences: list[str]) -> list[str]:
        results: list[str] = []
        for previous, current in zip(sentences, sentences[1:]):
            if len(previous) < 10 or len(current) < 10:
                continue
            if self._bigram_similarity(previous, current) >= 0.62:
                results.append(previous[-35:] + " / " + current[:35])
        return results

    @staticmethod
    def _bigram_similarity(left: str, right: str) -> float:
        clean_left = re.sub(r"[\W_]+", "", left)
        clean_right = re.sub(r"[\W_]+", "", right)
        left_grams = {clean_left[index:index + 2] for index in range(max(0, len(clean_left) - 1))}
        right_grams = {clean_right[index:index + 2] for index in range(max(0, len(clean_right) - 1))}
        if not left_grams or not right_grams:
            return 0.0
        return len(left_grams & right_grams) / len(left_grams | right_grams)

    def _repeated_paragraph_shapes(self, paragraphs: list[str], starts: list[str]) -> int:
        eligible = [
            self._paragraph_signature(paragraph, starts)
            for paragraph in paragraphs
            if len(paragraph) >= 36
        ]
        if len(eligible) < 8:
            return 0

        counts = Counter(eligible)
        # Paragraph shape naturally repeats in long chapters. Count only the
        # excess above a size-scaled baseline, and only when the repeated shape
        # carries a template-like signal rather than ordinary two-sentence prose.
        natural_repeat_allowance = max(3, min(12, int(len(eligible) * 0.08)))
        excess = 0
        for signature, count in counts.items():
            if count <= natural_repeat_allowance:
                continue
            sentence_count = int(signature[0] or 0)
            has_template_signal = bool(signature[2]) or signature[-1] == "explain"
            repeated_long_band = sentence_count >= 3 and len(set(signature[1])) <= 2
            if sentence_count >= 2 and (has_template_signal or repeated_long_band):
                excess += count - natural_repeat_allowance
        return excess

    def _paragraph_signature(self, paragraph: str, starts: list[str]) -> tuple[Any, ...]:
        sentences = self._sentences(paragraph)
        lengths = [len(item) for item in sentences]
        buckets = tuple("s" if length < 12 else "m" if length < 28 else "l" for length in lengths[:4])
        explanation_end = bool(sentences and sentences[-1].startswith(tuple(starts)))
        opening_shell = bool(sentences and any(sentences[0].startswith(start) for start in starts))
        end_kind = "explain" if explanation_end else "question" if paragraph.endswith(("？", "?")) else "plain"
        return (len(sentences), buckets, opening_shell, end_kind)

    @staticmethod
    def _mirrored_openings(paragraphs: list[str]) -> int:
        prefixes = [re.sub(r"[\W_]+", "", paragraph)[:6] for paragraph in paragraphs if len(paragraph) >= 8]
        counts = Counter(prefix for prefix in prefixes if len(prefix) >= 4)
        return sum(count - 1 for count in counts.values() if count > 1)

    def _double_conclusions(self, paragraphs: list[str], starts: list[str]) -> int:
        count = 0
        for paragraph in paragraphs:
            sentences = self._sentences(paragraph)
            if len(sentences) < 2:
                continue
            last_two = sentences[-2:]
            if all(any(marker in sentence for marker in starts) for sentence in last_two):
                count += 1
        return count

    @staticmethod
    def _advisories(
        metrics: dict,
        *,
        shell_matches: list[str],
        semantic_restatements: list[str],
        action_explanations: list[str],
        moral_hits: list[str],
    ) -> list[dict]:
        advisories: list[dict] = []
        if metrics["sentence_shell_count"] >= 2:
            advisories.append(make_advisory(
                "ai_sentence_shell",
                "medium",
                f"中文模板句壳出现 {metrics['sentence_shell_count']} 次。",
                target_span=shell_matches[0] if shell_matches else None,
                expected_behavior="保留真实逻辑关系，拆除重复的不是/而是、真正/的是、答案很简单等包装。",
                detector="ai_discourse_checker",
                confidence=0.78,
            ))
        if metrics["paragraph_shape_repeat_count"] >= 2:
            advisories.append(make_advisory(
                "ai_paragraph_isomorphism",
                "high",
                "多个段落使用近似相同的句数、句长顺序和总结收束方式。",
                expected_behavior="按场景压力重组段落，不要让每段都复制同一说明模板。",
                detector="ai_discourse_checker",
                confidence=0.8,
            ))
        if metrics["semantic_restatement_count"] >= 1:
            advisories.append(make_advisory(
                "ai_semantic_restatement",
                "medium",
                f"相邻句存在 {metrics['semantic_restatement_count']} 处高相似复述。",
                target_span=semantic_restatements[0] if semantic_restatements else None,
                expected_behavior="保留最有力的一次表达，删除解释性复述或让后句推进新信息。",
                detector="ai_discourse_checker",
                confidence=0.68,
            ))
        if metrics["action_then_explanation_count"] >= 1:
            advisories.append(make_advisory(
                "ai_action_then_explanation",
                "medium",
                "动作已经表达意义，随后旁白再次解释动作含义。",
                target_span=action_explanations[0] if action_explanations else None,
                expected_behavior="相信已经落地的动作和后果，删除重复解释。",
                detector="ai_discourse_checker",
                confidence=0.82,
            ))
        if metrics["moral_overclarity_count"] >= 2:
            advisories.append(make_advisory(
                "ai_moral_overclarity",
                "medium",
                "情绪或道德结论过度完整，人物过早获得清晰、积极的自我解释。",
                target_span=moral_hits[0] if moral_hits else None,
                expected_behavior="保留人物的矛盾、误解、余波和未解决代价。",
                detector="ai_discourse_checker",
                confidence=0.65,
            ))
        return advisories
