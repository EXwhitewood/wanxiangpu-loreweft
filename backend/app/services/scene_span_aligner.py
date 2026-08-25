"""Align a whole-chapter draft back to scene spans."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass


logger = logging.getLogger(__name__)


_MARKER_RE = re.compile(
    r"^\s*(?:\[\[SCENE:(?P<bracket>[^\]]+)\]\]|<<<SCENE_ID:(?P<angle>[^>]+)>>>)\s*$",
    re.MULTILINE,
)
_STRUCTURAL_HEADER_RE = re.compile(
    r"(?im)^\s*(?:"
    r"\[\[SCENE:[^\]]+\]\]|"
    r"<<<SCENE_ID:[^>]+>>>|"
    r"(?:#{1,6}\s*)?Scene\s*\d+\s*(?:[:：].*)?|"
    r"(?:#{1,6}\s*)?Chapter\s*\d+\s*Scene\s*\d+\s*(?:[:：].*)?|"
    r"(?:#{1,6}\s*)?第\s*[零〇一二两三四五六七八九十百千万\d]+\s*章\s*(?:[:：].*)?|"
    r"(?:#{1,6}\s*)?(?:场景|第)\s*[零〇一二两三四五六七八九十百千万\d]+\s*(?:场|幕|节)?\s*(?:[:：].*)?"
    r")\s*$"
)
# 方案20：语义切分标记
_SCENE_SPLIT_MARKER = "[[SCENE_SPLIT]]"


@dataclass(slots=True)
class SceneSpan:
    scene_id: str
    scene_index: int
    text: str
    start_char: int
    end_char: int
    method: str

    def to_dict(self) -> dict:
        return {
            "scene_id": self.scene_id,
            "scene_index": self.scene_index,
            "text": self.text,
            "start_char": self.start_char,
            "end_char": self.end_char,
            "method": self.method,
        }


class SceneSpanAligner:
    def align(self, chapter_text: str, scene_map: list[dict], *, semantic_split_fn=None) -> dict:
        """对齐章节正文到场景 spans。

        方案20：标记缺失时优先尝试语义切分（LLM 辅助），最后才降级为机械均分。

        Args:
            chapter_text: 整章正文
            scene_map: 场景映射列表
            semantic_split_fn: 可选的异步语义切分函数 (text, scene_ids) -> list[str] | None
                如果提供，标记缺失时调用该函数尝试语义切分。
        """
        chapter_text = chapter_text or ""
        scene_ids = [str(item.get("scene_id") or f"scene_{i+1}") for i, item in enumerate(scene_map)]
        marker_spans = list(_MARKER_RE.finditer(chapter_text))
        if marker_spans:
            spans = self._align_by_markers(chapter_text, marker_spans, scene_ids)
            if len(spans) == len(scene_ids):
                clean_text = self.strip_markers(chapter_text)
                return {
                    "method": "explicit_markers",
                    "passed": True,
                    "spans": [span.to_dict() for span in spans],
                    "clean_text": clean_text,
                    "warnings": [],
                }

        # 方案20：标记缺失，尝试语义切分（如果有 LLM 切分函数）
        fallback_text = self.strip_markers(chapter_text)
        warnings = []
        if not marker_spans:
            warnings.append("chapter writer did not emit scene markers; used paragraph fallback")
        else:
            warnings.append("scene marker count did not match scene map; used paragraph fallback")

        if semantic_split_fn is not None:
            try:
                semantic_scenes = semantic_split_fn(fallback_text, scene_ids)
                if semantic_scenes is not None and len(semantic_scenes) == len(scene_ids):
                    spans = self._build_spans_from_texts(semantic_scenes, scene_ids, fallback_text, "semantic_split")
                    warnings.append("semantic split succeeded (LLM-assisted)")
                    return {
                        "method": "semantic_split",
                        "passed": True,
                        "spans": [span.to_dict() for span in spans],
                        "clean_text": "\n\n".join(span.text for span in spans).strip(),
                        "warnings": warnings,
                    }
                else:
                    warnings.append("semantic split did not return expected scene count; falling back to paragraph split")
            except Exception as exc:
                logger.warning("semantic split failed: %s", exc)
                warnings.append(f"semantic split failed: {exc}; falling back to paragraph split")

        spans = self._fallback_split(fallback_text, scene_ids)
        return {
            "method": "paragraph_fallback",
            "passed": bool(spans),
            "spans": [span.to_dict() for span in spans],
            "clean_text": "\n\n".join(span.text for span in spans).strip(),
            "warnings": warnings,
        }

    def strip_markers(self, text: str) -> str:
        cleaned = _STRUCTURAL_HEADER_RE.sub("", text or "")
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    def _align_by_markers(self, text: str, markers: list[re.Match], scene_ids: list[str]) -> list[SceneSpan]:
        spans: list[SceneSpan] = []
        for index, marker in enumerate(markers):
            scene_id = (marker.group("bracket") or marker.group("angle") or "").strip()
            start = marker.end()
            end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
            content = text[start:end].strip()
            if not scene_id:
                scene_id = scene_ids[index] if index < len(scene_ids) else f"scene_{index+1}"
            spans.append(SceneSpan(
                scene_id=scene_id,
                scene_index=scene_ids.index(scene_id) if scene_id in scene_ids else index,
                text=content,
                start_char=start,
                end_char=end,
                method="explicit_markers",
            ))
        spans.sort(key=lambda span: span.scene_index)
        return spans

    def _build_spans_from_texts(
        self,
        texts: list[str],
        scene_ids: list[str],
        full_text: str,
        method: str,
    ) -> list[SceneSpan]:
        """方案20：从已切分的文本列表构建 SceneSpan，定位 start_char/end_char。"""
        spans: list[SceneSpan] = []
        cursor = 0
        for index, (scene_id, content) in enumerate(zip(scene_ids, texts)):
            content = (content or "").strip()
            start = full_text.find(content[:30], cursor) if content else cursor
            if start < 0:
                start = cursor
            end = start + len(content)
            cursor = max(end, cursor)
            spans.append(SceneSpan(
                scene_id=scene_id,
                scene_index=index,
                text=content,
                start_char=start,
                end_char=end,
                method=method,
            ))
        return spans

    def _fallback_split(self, text: str, scene_ids: list[str]) -> list[SceneSpan]:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
        if not scene_ids:
            return []
        if not paragraphs:
            paragraphs = [text.strip()] if text.strip() else [""]

        chunks: list[list[str]] = [[] for _ in scene_ids]
        for idx, paragraph in enumerate(paragraphs):
            target = min(int(idx * len(scene_ids) / max(len(paragraphs), 1)), len(scene_ids) - 1)
            chunks[target].append(paragraph)

        spans: list[SceneSpan] = []
        cursor = 0
        for index, scene_id in enumerate(scene_ids):
            content = "\n\n".join(chunks[index]).strip()
            start = text.find(content[:30], cursor) if content else cursor
            if start < 0:
                start = cursor
            end = start + len(content)
            cursor = max(end, cursor)
            spans.append(SceneSpan(
                scene_id=scene_id,
                scene_index=index,
                text=content,
                start_char=start,
                end_char=end,
                method="paragraph_fallback",
            ))
        return spans
