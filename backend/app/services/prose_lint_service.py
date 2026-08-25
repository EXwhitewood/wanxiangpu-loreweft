"""Deterministic prose lint checks for cheap pre-LLM validation."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ProseLintFinding:
    type: str
    severity: str
    detail: str
    target_span: str = ""
    suggested_strategy: str = "deterministic_repair"
    blocks_commit: bool = False

    def to_violation_kwargs(self) -> dict:
        return {
            "target_span": self.target_span or None,
            "suggested_strategy": self.suggested_strategy,
            "blocks_commit": self.blocks_commit,
            "evidence": {"source": "prose_lint", "target_span": self.target_span},
        }


class ProseLintService:
    HARD_PATTERNS: tuple[tuple[str, str, str], ...] = (
        (r"第\d+章|第[一二三四五六七八九十百千]+章", "chapter_reference", "正文中不应出现章节号等元叙事标记"),
        (r"(核心动机|情报边界|元冲突|信息释放|功能性角色|爽点机制)", "analysis_jargon", "正文中混入了分析报告术语"),
        (
            r"(?:顿|停|看|望|抬|落|放|收|松|移|转|偏|侧|点|摇|按|拍|推|拉|扯|抓|握|攥|退|进|走|跑|跳|坐|站|跪|倒|沉|抖|晃)了一下了",
            "malformed_aspect_particle",
            "动作补语后重复叠加了完成体助词，句子不完整",
        ),
    )
    SOFT_PATTERNS: tuple[tuple[str, str, str], ...] = (
        (r"不是[^。！？\n]{0,30}而是", "not_but_pattern", "高频辩证句式过于显眼"),
        (r"(显然|毋庸置疑|不难看出|到这里算是|接下来将会)", "meta_narration", "作者说教或元叙事痕迹"),
    )
    TRANSITION_WORDS = ("仿佛", "忽然", "竟然", "似乎", "顿时", "刹那", "猛地")

    def lint(self, text: str) -> list[ProseLintFinding]:
        findings: list[ProseLintFinding] = []
        if not text:
            return findings

        for pattern, f_type, detail in self.HARD_PATTERNS:
            findings.extend(self._regex_findings(text, pattern, f_type, "critical", detail, True))
        for pattern, f_type, detail in self.SOFT_PATTERNS:
            findings.extend(self._regex_findings(text, pattern, f_type, "medium", detail, False))

        findings.extend(self._check_consecutive_le(text))
        findings.extend(self._check_fragmented_paragraphs(text))
        findings.extend(self._check_transition_density(text))
        return findings

    @staticmethod
    def _regex_findings(
        text: str,
        pattern: str,
        f_type: str,
        severity: str,
        detail: str,
        blocks_commit: bool,
    ) -> list[ProseLintFinding]:
        return [
            ProseLintFinding(
                type=f_type,
                severity=severity,
                detail=f"{detail}: {match.group(0)}",
                target_span=match.group(0),
                blocks_commit=blocks_commit,
            )
            for match in re.finditer(pattern, text)
        ]

    @staticmethod
    def _check_consecutive_le(text: str) -> list[ProseLintFinding]:
        findings: list[ProseLintFinding] = []
        sentences = [s.strip() for s in re.split(r"[。！？\n]+", text) if s.strip()]
        combo = 0
        for sentence in sentences:
            if sentence.endswith("了"):
                combo += 1
                if combo == 6:
                    findings.append(ProseLintFinding(
                        type="excessive_le_particle",
                        severity="medium",
                        detail="连续 6 句以“了”结尾，句式节奏过于机械",
                    ))
            else:
                combo = 0
        return findings

    @staticmethod
    def _check_fragmented_paragraphs(text: str) -> list[ProseLintFinding]:
        paragraphs = [p.strip() for p in text.splitlines() if p.strip()]
        findings: list[ProseLintFinding] = []
        combo = 0
        short_count = 0
        narrative_count = 0
        for paragraph in paragraphs:
            if len(paragraph) < 40:
                combo += 1
                short_count += 1
                if combo == 4:
                    findings.append(ProseLintFinding(
                        type="fragmented_paragraphs",
                        severity="medium",
                        detail="连续 4 段以上极短段落，移动端阅读节奏可能过碎",
                    ))
            else:
                combo = 0
            narrative_count += 1
        if narrative_count >= 8 and short_count / narrative_count >= 0.65:
            findings.append(ProseLintFinding(
                type="short_paragraph_ratio_high",
                severity="low",
                detail="短段落比例过高，建议增加若干承接性叙事段",
            ))
        return findings

    def _check_transition_density(self, text: str) -> list[ProseLintFinding]:
        char_count = max(len(text), 1)
        hits = sum(text.count(word) for word in self.TRANSITION_WORDS)
        if hits > max(3, char_count / 1000):
            return [ProseLintFinding(
                type="transition_word_density_high",
                severity="low",
                detail=f"转折/突发词密度偏高，共 {hits} 次",
            )]
        return []

