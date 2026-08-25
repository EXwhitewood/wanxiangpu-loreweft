from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Mapping
from copy import deepcopy
from html.parser import HTMLParser
from typing import Any

from app.engines.fcip_engine import FCIPEngine
from app.models.writing_assistance import (
    AdvisoryDimension,
    ChapterAdvisoryCheckRequest,
    ChapterAdvisoryCheckResponse,
    ChapterAdvisoryDiagnostic,
    ChapterAdvisoryFinding,
    ChapterAdvisorySource,
    WritingAssistanceSettings,
)
from app.services.foreshadowing_service import ForeshadowingService
from app.services.llm_gateway import LLMGateway, get_llm_gateway
from app.services.llm_task_profiles import LLMTaskType
from app.services.state_manager import StateManager

logger = logging.getLogger(__name__)

_MAX_DRAFT_CHARS = 120_000
_ALLOWED_SEVERITIES = {"high", "medium", "low"}


class _DraftTextExtractor(HTMLParser):
    _BLOCK_TAGS = {
        "p",
        "div",
        "br",
        "li",
        "blockquote",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in self._BLOCK_TAGS and self.parts:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def text(self) -> str:
        joined = "".join(self.parts)
        joined = re.sub(r"[ \t\f\v]+", " ", joined)
        joined = re.sub(r"\n\s*\n+", "\n", joined)
        return joined.strip()


def chapter_revision_hash(content: str) -> str:
    return hashlib.sha256((content or "").encode("utf-8")).hexdigest()[:16]


def _plain_text(content: str) -> str:
    if "<" not in content or ">" not in content:
        return content.strip()
    parser = _DraftTextExtractor()
    try:
        parser.feed(content)
        parser.close()
        return parser.text()
    except Exception:
        return re.sub(r"<[^>]+>", "", content).strip()


def _compact_json_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 5:
        if isinstance(value, (Mapping, list, tuple)):
            return "[内容已折叠]"
        return str(value)[:500]
    if isinstance(value, Mapping):
        compacted: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 60:
                compacted["_truncated"] = f"另有 {len(value) - 60} 项"
                break
            compacted[str(key)] = _compact_json_value(item, depth=depth + 1)
        return compacted
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        compacted = [_compact_json_value(item, depth=depth + 1) for item in items[:60]]
        if len(items) > 60:
            compacted.append(f"[另有 {len(items) - 60} 项]")
        return compacted
    if isinstance(value, str):
        return value[:1500]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:500]


def _chapter_outline_context(outline_data: Mapping[str, Any], chapter_number: int) -> dict:
    result: dict[str, Any] = {}
    for key in ("chapter_spine", "chapters"):
        records = outline_data.get(key)
        if not isinstance(records, list):
            continue
        for record in records:
            if not isinstance(record, Mapping):
                continue
            try:
                matches = int(record.get("chapter_number", -1)) == chapter_number
            except (TypeError, ValueError):
                matches = False
            if matches:
                result[key] = deepcopy(dict(record))
                break

    scene_briefs = outline_data.get("scene_briefs")
    if isinstance(scene_briefs, Mapping):
        for key in (
            f"ch_{chapter_number:03d}",
            f"chapter_{chapter_number}",
            str(chapter_number),
        ):
            if key in scene_briefs:
                result["scene_brief"] = deepcopy(scene_briefs[key])
                break

    constitution = outline_data.get("story_constitution")
    if isinstance(constitution, Mapping):
        result["story_constitution"] = deepcopy(dict(constitution))

    return result


def _reference_context(project: Any, story_state: Mapping[str, Any], chapter_number: int) -> dict:
    core_data = getattr(project, "core_data", None)
    core_data = core_data if isinstance(core_data, Mapping) else {}
    outline_data = getattr(project, "outline_data", None)
    outline_data = outline_data if isinstance(outline_data, Mapping) else {}

    relevant_core_keys = (
        "characters",
        "world_rules",
        "locations",
        "facts",
        "canonical_facts",
        "chapter_summaries",
    )
    core_reference = {
        key: deepcopy(core_data[key])
        for key in relevant_core_keys
        if key in core_data
    }
    return _compact_json_value(
        {
            "project": {
                "name": getattr(project, "name", ""),
                "description": getattr(project, "description", ""),
                "genre": getattr(project, "genre", ""),
            },
            "core_facts": core_reference,
            "current_story_state": deepcopy(dict(story_state)),
            "chapter_outline_reference": _chapter_outline_context(
                outline_data, chapter_number
            ),
            "quality_criteria": {
                "pacing": "章节应持续产生可辨认的行动、决定、冲突或信息推进，避免长段内容没有叙事变化。",
                "clarity": "时间、地点、行动主体和因果关系应当清楚，读者不需要猜测谁做了什么。",
                "character_expression": "人物情绪、欲望与变化应有正文证据，不能只靠概括标签宣告。",
                "dialogue": "对话应有明确归属并承担推进、冲突、关系或信息功能。",
                "hook": "章节开头或结尾应提供继续阅读的压力、问题、悬念、转折或承诺。",
                "prose_style": "表达应避免明显重复、空泛解释和连续模板化句式，同时尊重作者既有声音。",
            },
        }
    )


def _has_reference_material(context: Mapping[str, Any]) -> bool:
    if (
        context.get("core_facts")
        or context.get("chapter_outline_reference")
        or context.get("quality_criteria")
    ):
        return True
    state = context.get("current_story_state")
    if not isinstance(state, Mapping):
        return False
    return any(
        bool(state.get(key))
        for key in (
            "narrative_time",
            "pov_character",
            "objective_state",
            "subjective_views",
            "completed_events",
            "active_constraints",
        )
    )


def _canonical_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _evidence_is_in_draft(evidence: str, draft: str) -> bool:
    cleaned = evidence.strip().strip("….").strip()
    if len(cleaned) < 2:
        return False
    return _canonical_space(cleaned) in _canonical_space(draft)


def _fingerprint(
    category: str,
    evidence: str,
    source_type: str,
    source_label: str,
    source_excerpt: str,
) -> str:
    material = "|".join(
        [
            category.strip().lower(),
            _canonical_space(evidence),
            source_type.strip().lower(),
            _canonical_space(source_label),
            _canonical_space(source_excerpt),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


class ChapterAdvisoryService:
    """Read-only, non-blocking consistency reminders for either writing path."""

    def __init__(
        self,
        *,
        gateway: LLMGateway | None = None,
        fcip_engine: FCIPEngine | None = None,
        state_manager: StateManager | None = None,
    ) -> None:
        self.gateway = gateway or get_llm_gateway()
        self.fcip_engine = fcip_engine or FCIPEngine(ForeshadowingService())
        self.state_manager = state_manager or StateManager()

    async def check(
        self,
        *,
        project: Any,
        project_id: str,
        chapter_number: int,
        request: ChapterAdvisoryCheckRequest,
        settings: WritingAssistanceSettings,
        db: Any,
    ) -> ChapterAdvisoryCheckResponse:
        revision_hash = chapter_revision_hash(request.content)
        reminder_settings = settings.consistency_reminders
        requested_dimensions = (
            reminder_settings.dimensions
            if request.dimensions is None
            else request.dimensions
        )
        selected = list(requested_dimensions)
        selected = [item for item in selected if item in reminder_settings.dimensions]

        response = ChapterAdvisoryCheckResponse(
            status="complete",
            trigger=request.trigger,
            revision_hash=revision_hash,
            checked_dimensions=selected,
        )
        if not reminder_settings.enabled:
            response.status = "disabled"
            response.diagnostics.append(
                ChapterAdvisoryDiagnostic(
                    code="reminders_disabled",
                    message="一致性提醒尚未开启，本次未执行检查。",
                )
            )
            return response
        if request.trigger == "save" and not reminder_settings.check_on_save:
            response.status = "disabled"
            response.diagnostics.append(
                ChapterAdvisoryDiagnostic(
                    code="save_check_disabled",
                    message="保存后检查尚未开启，本次保存不触发提醒。",
                )
            )
            return response
        if not selected:
            response.diagnostics.append(
                ChapterAdvisoryDiagnostic(
                    code="no_dimensions_selected",
                    message="尚未选择检查维度。",
                )
            )
            return response

        draft = _plain_text(request.content)
        if not draft:
            response.diagnostics.append(
                ChapterAdvisoryDiagnostic(
                    code="empty_draft",
                    message="正文为空，没有需要检查的内容。",
                )
            )
            return response

        analysis_draft = draft
        if len(analysis_draft) > _MAX_DRAFT_CHARS:
            analysis_draft = analysis_draft[:_MAX_DRAFT_CHARS]
            response.diagnostics.append(
                ChapterAdvisoryDiagnostic(
                    code="draft_truncated",
                    message="正文较长，本次语义检查只覆盖前 120000 个字符。",
                    level="warning",
                )
            )

        story_state: dict[str, Any] = {}
        try:
            state = await self.state_manager.get_state(project_id)
            story_state = (
                state.model_dump()
                if hasattr(state, "model_dump")
                else dict(state or {})
            )
        except Exception as exc:
            logger.warning("[chapter-advisory] state read failed: %s", exc)
            response.status = "degraded"
            response.diagnostics.append(
                ChapterAdvisoryDiagnostic(
                    code="state_unavailable",
                    message="当前故事状态暂时不可读，已继续检查其他参照信息。",
                    level="warning",
                )
            )

        # A chapter save may pass a frozen pre-save state.  Override the
        # current state read above so a new projection cannot hide a conflict.
        reference_snapshot = request.reference_snapshot or {}
        frozen_state = reference_snapshot.get("story_state")
        if isinstance(frozen_state, dict):
            story_state = deepcopy(frozen_state)

        if "foreshadowing" in selected:
            try:
                fcip_result = await self.fcip_engine.post_gen_writeback(
                    project_id,
                    chapter_number,
                    draft,
                    db,
                    apply_writeback=False,
                )
                response.findings.extend(
                    self._map_fcip_findings(
                        fcip_result.get("violations", []), revision_hash, draft
                    )
                )
            except Exception as exc:
                logger.warning("[chapter-advisory] read-only FCIP scan failed: %s", exc)
                response.status = "degraded"
                response.diagnostics.append(
                    ChapterAdvisoryDiagnostic(
                        code="foreshadowing_scan_unavailable",
                        message="伏笔认知检查暂时不可用，其他提醒结果不受影响。",
                        level="warning",
                    )
                )

        context = _reference_context(project, story_state, chapter_number)
        frozen_outline = reference_snapshot.get("outline_data")
        if isinstance(frozen_outline, dict):
            context["chapter_outline_reference"] = _chapter_outline_context(
                frozen_outline,
                chapter_number,
            )
            context["outline_version"] = reference_snapshot.get("outline_version", 0)
        frozen_core = reference_snapshot.get("core_data")
        if isinstance(frozen_core, dict):
            context["core_facts"] = {
                key: deepcopy(frozen_core[key])
                for key in (
                    "characters",
                    "world_rules",
                    "locations",
                    "facts",
                    "canonical_facts",
                    "chapter_summaries",
                )
                if key in frozen_core
            }
        if reference_snapshot:
            context["reference_snapshot"] = {
                "outline_version": reference_snapshot.get("outline_version", 0),
                "state_version": reference_snapshot.get("state_version", ""),
            }
        if not _has_reference_material(context):
            response.diagnostics.append(
                ChapterAdvisoryDiagnostic(
                    code="no_reference_context",
                    message="项目还没有可用于比对的设定、状态或本章大纲。",
                )
            )
            return self._deduplicate(response)

        result = await self.gateway.generate_json(
            system=self._system_prompt(selected),
            prompt=self._user_prompt(
                chapter_number=chapter_number,
                draft=analysis_draft,
                context=context,
            ),
            temperature=0.1,
            task_type=LLMTaskType.JSON_DETECTION,
        )
        if not result.ok:
            response.status = "degraded"
            response.diagnostics.append(
                ChapterAdvisoryDiagnostic(
                    code=result.error_type or "semantic_check_unavailable",
                    message="语义检查暂时不可用；保存和正文编辑均未受影响。",
                    level="warning",
                )
            )
            return self._deduplicate(response)

        response.findings.extend(
            self._map_semantic_findings(
                result.parsed_json,
                revision_hash=revision_hash,
                draft=draft,
                selected=set(selected),
                reference_text=json.dumps(context, ensure_ascii=False, default=str),
            )
        )
        return self._deduplicate(response)

    @staticmethod
    def _system_prompt(selected: list[AdvisoryDimension]) -> str:
        schema = {
            "findings": [
                {
                    "category": "fact | character | timeline | world_rule | pov_knowledge | foreshadowing | outline_deviation | pacing | clarity | character_expression | dialogue | hook | prose_style",
                    "severity": "high | medium | low",
                    "title": "简短标题",
                    "detail": "为什么值得提醒，不得声称阻断提交",
                    "evidence_quote": "正文中的精确原句",
                    "source": {
                        "source_type": "结构化来源类型",
                        "label": "来源名称或字段",
                        "excerpt": "与原句冲突或偏离的参照内容",
                    },
                    "confidence": 0.8,
                }
            ]
        }
        return (
            "你是万象谱章节工作台中的只读一致性提醒器。你的职责是把当前正文与给定的结构化"
            "事实、人物状态、时间线、世界规则、角色认知、伏笔信息和本章大纲参考进行比对。\n"
            "这不是 FBI 审查、修复或提交闸门。所有发现都只是可忽略的非阻断提醒。\n"
            "只报告有正文精确证据且有明确参照来源的冲突或显著偏离；不得补造事实，不得把创作"
            "选择、风格差异或暂时未写到的内容当成错误。普通大纲偏离只能作为低或中等级提醒；只有正文明确改变本章核心目标、关键事件或后续任务依赖时，才可标为 high，以便用户决定是否让大纲追随正文。\n"
            f"本次允许的分类只有：{', '.join(selected)}。其他分类不要输出。\n"
            "evidence_quote 必须逐字来自正文，source.excerpt 必须来自给定参照。没有可靠证据就"
            "返回空 findings。不要输出改写后的正文或补丁。\n"
            "严格按以下 JSON 结构返回：\n"
            + json.dumps(schema, ensure_ascii=False, indent=2)
        )

    @staticmethod
    def _user_prompt(*, chapter_number: int, draft: str, context: Mapping[str, Any]) -> str:
        return (
            f"待检查章节：第 {chapter_number} 章\n\n"
            "【结构化参照】\n"
            + json.dumps(context, ensure_ascii=False, default=str)
            + "\n\n【当前正文】\n"
            + draft
        )

    @staticmethod
    def _map_fcip_findings(
        violations: list[dict], revision_hash: str, draft: str
    ) -> list[ChapterAdvisoryFinding]:
        findings: list[ChapterAdvisoryFinding] = []
        for raw in violations if isinstance(violations, list) else []:
            if not isinstance(raw, Mapping):
                continue
            evidence = str(raw.get("matched_text") or raw.get("target_span") or "")[:300]
            cleaned_evidence = evidence.strip().strip("….").strip()
            if not _evidence_is_in_draft(cleaned_evidence, draft):
                continue
            rule_id = str(raw.get("rule_id") or "FCIP")[:80]
            suggestion = str(
                raw.get("suggestion") or raw.get("expected_behavior") or "检查角色认知边界"
            )[:500]
            severity_raw = str(raw.get("severity") or "low").lower()
            severity = {
                "critical": "high",
                "high": "medium",
                "medium": "low",
                "low": "low",
            }.get(severity_raw, "low")
            fp = _fingerprint(
                "foreshadowing",
                cleaned_evidence,
                "foreshadowing_rule",
                rule_id,
                suggestion,
            )
            findings.append(
                ChapterAdvisoryFinding(
                    finding_id=f"advisory_{fp}",
                    fingerprint=fp,
                    revision_hash=revision_hash,
                    category="foreshadowing",
                    severity=severity,
                    title=f"伏笔认知提醒 · {rule_id}",
                    detail=str(raw.get("description") or raw.get("detail") or suggestion)[:600],
                    evidence_quote=cleaned_evidence,
                    source=ChapterAdvisorySource(
                        source_type="foreshadowing_rule",
                        label=rule_id,
                        excerpt=suggestion,
                    ),
                    confidence=1.0,
                )
            )
        return findings

    @staticmethod
    def _map_semantic_findings(
        payload: dict | list | None,
        *,
        revision_hash: str,
        draft: str,
        selected: set[str],
        reference_text: str,
    ) -> list[ChapterAdvisoryFinding]:
        if isinstance(payload, Mapping):
            items = payload.get("findings", [])
        elif isinstance(payload, list):
            items = payload
        else:
            items = []

        findings: list[ChapterAdvisoryFinding] = []
        for raw in items if isinstance(items, list) else []:
            if not isinstance(raw, Mapping):
                continue
            category = str(raw.get("category") or "")
            if category not in selected:
                continue
            evidence = str(raw.get("evidence_quote") or "")[:300].strip()
            if not _evidence_is_in_draft(evidence, draft):
                continue
            source_raw = raw.get("source")
            source_raw = source_raw if isinstance(source_raw, Mapping) else {}
            source_type = str(
                source_raw.get("source_type") or raw.get("source_type") or ""
            )[:80].strip()
            source_label = str(
                source_raw.get("label") or raw.get("source_label") or ""
            )[:160].strip()
            source_excerpt = str(
                source_raw.get("excerpt") or raw.get("source_excerpt") or ""
            )[:500].strip()
            if not source_type or not source_label or not source_excerpt:
                continue
            if _canonical_space(source_excerpt) not in _canonical_space(reference_text):
                continue
            try:
                confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.5))))
            except (TypeError, ValueError):
                confidence = 0.5
            if confidence < 0.55:
                continue
            severity = str(raw.get("severity") or "low").lower()
            if severity not in _ALLOWED_SEVERITIES:
                severity = "low"
            fp = _fingerprint(
                category, evidence, source_type, source_label, source_excerpt
            )
            findings.append(
                ChapterAdvisoryFinding(
                    finding_id=f"advisory_{fp}",
                    fingerprint=fp,
                    revision_hash=revision_hash,
                    category=category,
                    severity=severity,
                    title=str(raw.get("title") or "一致性提醒")[:120],
                    detail=str(raw.get("detail") or "请核对正文与参照信息。")[:600],
                    evidence_quote=evidence,
                    source=ChapterAdvisorySource(
                        source_type=source_type,
                        label=source_label,
                        excerpt=source_excerpt,
                    ),
                    confidence=confidence,
                )
            )
        return findings

    @staticmethod
    def _deduplicate(
        response: ChapterAdvisoryCheckResponse,
    ) -> ChapterAdvisoryCheckResponse:
        unique: dict[str, ChapterAdvisoryFinding] = {}
        for finding in response.findings:
            unique.setdefault(finding.fingerprint, finding)
        response.findings = list(unique.values())
        return response
