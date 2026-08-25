from __future__ import annotations

from app.agents.base import BaseAgent
from app.models.reader_experience import ReaderExperienceReport, ReaderExperienceScores
from app.models.quality_advisory import make_advisory
from app.services.json_response import parse_json_response
from app.services.reader_context_builder import ReaderContextBuilder
from app.services.llm_task_profiles import LLMTaskType


class ReaderExperienceAgent(BaseAgent):
    name = "reader_experience"

    async def execute(self, context: dict) -> dict:
        ReaderContextBuilder.assert_no_hidden_keys(context)
        text = context.get("generated_text", "")
        if not text:
            return ReaderExperienceReport(
                status="empty",
                degraded=True,
                error="empty_text",
            ).model_dump()

        try:
            llm = await self.get_llm_client()
            response = await llm.generate(
                system_prompt=self._system_prompt(),
                user_prompt=self._user_prompt(context),
                temperature=0.2,
                task_type=LLMTaskType.SHORT_EXTRACTION,
            )
            raw = parse_json_response(response)
            raw = self._normalize_payload(raw)
            report = ReaderExperienceReport(**raw)
            return report.model_dump()
        except Exception as exc:
            return self._heuristic_report(text, str(exc)).model_dump()

    @staticmethod
    def _system_prompt() -> str:
        return (
            "你是冷读者体验评审，只能根据读者已经看到的信息评价小说片段。"
            "不要使用未来剧情、隐藏设定或作者意图。输出 JSON，字段包括 schema_version、"
            "status、scores、confusions、drop_points、advisories。scores 为 0-10 分。"
        )

    @staticmethod
    def _user_prompt(context: dict) -> str:
        return (
            f"题材：{context.get('genre', '')}\n"
            f"目标读者：{context.get('target_reader', '中文网文读者')}\n"
            f"章节：{context.get('chapter_number', 0)} 场景：{context.get('scene_index', 0)}\n"
            f"读者已知摘要：{context.get('previous_public_summary', '')}\n\n"
            f"当前正文：\n{context.get('generated_text', '')[:5000]}\n\n"
            "请从继续阅读欲望、清晰度、认知负担、情绪投入和节奏评价。"
        )

    @staticmethod
    def _heuristic_report(text: str, error: str) -> ReaderExperienceReport:
        length = len(text)
        paragraphs = [p for p in text.splitlines() if p.strip()]
        cognitive_load = min(10, max(1, text.count("所谓") + text.count("其实") + text.count("等级") + 3))
        clarity = 7 if length > 300 else 5
        pacing = 6 if paragraphs and max(len(p) for p in paragraphs) < 500 else 4
        advisories = []
        if cognitive_load >= 7:
            advisories.append(make_advisory(
                "reader_cognitive_load",
                "medium",
                "冷读者视角下新信息和解释性表达可能偏多。",
                expected_behavior="减少同场景概念投放，让冲突和行动先推动阅读。",
                detector="reader_experience_heuristic",
                confidence=0.55,
            ))
        return ReaderExperienceReport(
            scores=ReaderExperienceScores(
                continue_reading=6,
                clarity=clarity,
                cognitive_load=cognitive_load,
                emotional_engagement=6,
                pacing=pacing,
            ),
            advisories=advisories,
            degraded=True,
            error=error,
        )

    @staticmethod
    def _normalize_payload(raw) -> dict:
        if not isinstance(raw, dict):
            return {"status": "invalid", "degraded": True, "error": "reader_response_not_object"}
        scores = raw.get("scores")
        if not isinstance(scores, dict):
            raw["scores"] = {}
        advisories = raw.get("advisories", [])
        normalized = []
        if isinstance(advisories, list):
            for item in advisories:
                if isinstance(item, dict):
                    normalized.append(item)
                elif isinstance(item, str) and item.strip():
                    normalized.append(make_advisory(
                        "reader_experience_advice",
                        "low",
                        item.strip(),
                        expected_behavior="按冷读者建议改善清晰度、节奏或情绪投入。",
                        detector="reader_experience",
                        confidence=0.5,
                    ))
        raw["advisories"] = normalized
        for key in ("confusions", "drop_points"):
            value = raw.get(key, [])
            if isinstance(value, str):
                raw[key] = [value]
            elif not isinstance(value, list):
                raw[key] = []
        raw.setdefault("schema_version", 1)
        raw.setdefault("status", "ok")
        return raw
