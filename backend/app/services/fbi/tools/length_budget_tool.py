"""篇幅预算工具——确定性字数统计与预算分配。

纯计算工具，不含 LLM 调用。
"""

from __future__ import annotations

import re
from typing import Literal


class LengthBudgetTool:
    """篇幅预算工具——确定性字数统计与预算分配。"""

    # 中文标点范围
    _CHINESE_PUNCTUATION_RE = re.compile(r"[，。！？；：、""''（）【】《》—…·～「」『』]")
    # 中文字符范围（CJK Unified Ideographs + extensions）
    _CHINESE_CHAR_RE = re.compile(
        r"[\u4e00-\u9fff\u3400-\u4dbf\U00020000-\U0002a6df\U0002a700-\U0002b73f"
        r"\U0002b740-\U0002b81f\U0002b820-\U0002ceaf]"
    )
    # 英文字母
    _ENGLISH_CHAR_RE = re.compile(r"[a-zA-Z]")
    # 数字
    _DIGIT_RE = re.compile(r"[0-9]")

    # 重复动作关键词
    _ACTION_VERBS = [
        "走", "跑", "看", "听", "说", "想", "坐", "站", "拿", "放",
        "推", "拉", "转", "抬", "低", "点", "摇", "握", "松", "抓",
    ]

    # 重复心理判断关键词
    _EMOTION_KEYWORDS = [
        "觉得", "感到", "意识到", "明白", "知道", "发现", "意识到",
        "心想", "暗想", "忍不住", "不由得", "不禁",
    ]

    # 解释性总结句模式
    _EXPLANATORY_PATTERNS = [
        r"也就是说[，,]",
        r"换句话说[，,]",
        r"其实[，,]",
        r"事实上[，,]",
        r"这意味着",
        r"这说明了",
        r"这表明了",
        r"简单来说",
        r"总而言之",
        r"归根结底",
    ]

    def count_chinese_chars(self, text: str) -> dict:
        """统计中文字符、标点、英文数字。

        Returns:
            {
                "chinese_chars": int,
                "chinese_punctuation": int,
                "english_chars": int,
                "digits": int,
                "total_chars": int,
            }
        """
        chinese_chars = len(self._CHINESE_CHAR_RE.findall(text))
        chinese_punctuation = len(self._CHINESE_PUNCTUATION_RE.findall(text))
        english_chars = len(self._ENGLISH_CHAR_RE.findall(text))
        digits = len(self._DIGIT_RE.findall(text))
        total_chars = len(text)

        return {
            "chinese_chars": chinese_chars,
            "chinese_punctuation": chinese_punctuation,
            "english_chars": english_chars,
            "digits": digits,
            "total_chars": total_chars,
        }

    def paragraph_budget_plan(
        self,
        text: str,
        target_chars: int,
        mode: Literal["compress", "expand"],
    ) -> list[dict]:
        """给每段分配压缩/扩写目标。

        Args:
            text: 正文
            target_chars: 目标字数
            mode: 压缩或扩写

        Returns:
            [{"paragraph_index": int, "current_chars": int, "target_chars": int, "delta": int, "priority": int}]
        """
        paragraphs = [p for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            return []

        current_total = len(text)
        total_delta = target_chars - current_total

        # Calculate per-paragraph stats
        para_stats = []
        for idx, para in enumerate(paragraphs):
            para_len = len(para)
            para_stats.append({
                "paragraph_index": idx,
                "current_chars": para_len,
                "proportion": para_len / current_total if current_total > 0 else 0,
            })

        # Distribute delta proportionally, with priority based on content analysis
        results = []
        for idx, stat in enumerate(para_stats):
            proportional_delta = int(total_delta * stat["proportion"])
            para_target = stat["current_chars"] + proportional_delta

            # Priority: higher priority = should be adjusted first
            # For compress: longer paragraphs get higher priority
            # For expand: shorter paragraphs get higher priority
            if mode == "compress":
                priority = min(10, stat["current_chars"] // 50)
            else:
                priority = min(10, max(1, 10 - stat["current_chars"] // 50))

            results.append({
                "paragraph_index": stat["paragraph_index"],
                "current_chars": stat["current_chars"],
                "target_chars": max(20, para_target),  # Minimum paragraph size
                "delta": proportional_delta,
                "priority": priority,
            })

        return results

    def redundancy_detector(self, text: str) -> list[dict]:
        """检测重复动作、重复心理判断、解释性总结句。

        Returns:
            [{
                "type": "repeated_action"|"repeated_emotion"|"explanatory_summary",
                "span": {"start": int, "end": int, "label": str},
                "text": str,
                "risk": "low"|"medium"|"high",
            }]
        """
        results: list[dict] = []

        # Split into sentences
        sentences = re.split(r"[。！？；\n]", text)
        sentence_positions = []
        pos = 0
        for s in sentences:
            s_stripped = s.strip()
            if s_stripped:
                # Find the actual position in the original text
                start = text.find(s_stripped, pos)
                if start == -1:
                    start = pos
                end = start + len(s_stripped)
                sentence_positions.append((s_stripped, start, end))
                pos = end
            else:
                pos += len(s) + 1

        # 1. Detect repeated actions
        action_counts: dict[str, list[tuple[int, int]]] = {}
        for verb in self._ACTION_VERBS:
            for s_text, s_start, s_end in sentence_positions:
                if verb in s_text:
                    if verb not in action_counts:
                        action_counts[verb] = []
                    action_counts[verb].append((s_start, s_end))

        for verb, positions in action_counts.items():
            if len(positions) >= 3:
                # High risk: same action verb appears 3+ times
                for start, end in positions:
                    results.append({
                        "type": "repeated_action",
                        "span": {"start": start, "end": end, "label": f"重复动作: {verb}"},
                        "text": text[start:end],
                        "risk": "high" if len(positions) >= 4 else "medium",
                    })
            elif len(positions) == 2:
                # Low risk: appears twice
                for start, end in positions:
                    results.append({
                        "type": "repeated_action",
                        "span": {"start": start, "end": end, "label": f"重复动作: {verb}"},
                        "text": text[start:end],
                        "risk": "low",
                    })

        # 2. Detect repeated emotion keywords
        emotion_counts: dict[str, list[tuple[int, int]]] = {}
        for keyword in self._EMOTION_KEYWORDS:
            for s_text, s_start, s_end in sentence_positions:
                if keyword in s_text:
                    if keyword not in emotion_counts:
                        emotion_counts[keyword] = []
                    emotion_counts[keyword].append((s_start, s_end))

        for keyword, positions in emotion_counts.items():
            if len(positions) >= 2:
                for start, end in positions:
                    results.append({
                        "type": "repeated_emotion",
                        "span": {"start": start, "end": end, "label": f"重复心理: {keyword}"},
                        "text": text[start:end],
                        "risk": "high" if len(positions) >= 3 else "medium",
                    })

        # 3. Detect explanatory summary sentences
        for pattern in self._EXPLANATORY_PATTERNS:
            for match in re.finditer(pattern, text):
                # Find the sentence containing this match
                match_start = match.start()
                # Extend to full sentence
                sent_start = match_start
                for i in range(match_start - 1, -1, -1):
                    if text[i] in "。！？；\n":
                        sent_start = i + 1
                        break
                else:
                    sent_start = 0

                sent_end = match_start
                for i in range(match.end(), len(text)):
                    if text[i] in "。！？；\n":
                        sent_end = i
                        break
                else:
                    sent_end = len(text)

                results.append({
                    "type": "explanatory_summary",
                    "span": {"start": sent_start, "end": sent_end, "label": "解释性总结"},
                    "text": text[sent_start:sent_end],
                    "risk": "medium",
                })

        return results

    def expansion_slot_finder(self, text: str) -> list[dict]:
        """找可扩写位置。

        Returns:
            [{
                "type": "action_gap"|"causal_bridge"|"consequence"|"ending_echo",
                "after_span": {"start": int, "end": int, "label": str},
                "suggested_chars": int,
            }]
        """
        results: list[dict] = []
        paragraphs = [p for p in text.split("\n\n") if p.strip()]

        if not paragraphs:
            return results

        # Calculate positions for each paragraph
        para_positions = []
        pos = 0
        for para in paragraphs:
            start = text.find(para, pos)
            if start == -1:
                start = pos
            end = start + len(para)
            para_positions.append((para, start, end))
            pos = end

        # 1. Action gaps: very short paragraphs between longer ones
        for idx, (para, start, end) in enumerate(para_positions):
            if len(para) < 30 and idx > 0 and idx < len(para_positions) - 1:
                prev_len = len(para_positions[idx - 1][0])
                next_len = len(para_positions[idx + 1][0])
                if prev_len > 50 and next_len > 50:
                    results.append({
                        "type": "action_gap",
                        "after_span": {"start": start, "end": end, "label": f"段{idx}"},
                        "suggested_chars": 50,
                    })

        # 2. Causal bridge: look for abrupt transitions
        transition_markers = ["但是", "然而", "可是", "突然", "忽然", "就在这时"]
        for idx, (para, start, end) in enumerate(para_positions):
            if idx > 0:
                for marker in transition_markers:
                    if para.startswith(marker):
                        results.append({
                            "type": "causal_bridge",
                            "after_span": {
                                "start": para_positions[idx - 1][1],
                                "end": para_positions[idx - 1][2],
                                "label": f"段{idx - 1}与段{idx}之间",
                            },
                            "suggested_chars": 40,
                        })
                        break

        # 3. Consequence: after decision/action paragraphs
        decision_markers = ["决定", "选择了", "下定决心", "终于", "咬了咬牙"]
        for idx, (para, start, end) in enumerate(para_positions):
            for marker in decision_markers:
                if marker in para and idx == len(para_positions) - 1:
                    results.append({
                        "type": "consequence",
                        "after_span": {"start": start, "end": end, "label": f"段{idx}末尾"},
                        "suggested_chars": 60,
                    })
                    break

        # 4. Ending echo: last paragraph is too short
        if len(para_positions) >= 2:
            last_para, last_start, last_end = para_positions[-1]
            if len(last_para) < 50:
                results.append({
                    "type": "ending_echo",
                    "after_span": {"start": last_start, "end": last_end, "label": "末尾段"},
                    "suggested_chars": 80,
                })

        return results
