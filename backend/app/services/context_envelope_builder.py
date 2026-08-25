"""Build context envelopes for editor agents and chapter-level Writer."""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import Chapter, ChapterSnapshot
from app.services.context_budget_service import get_context_budget_service
from app.services.context_compressor import get_context_compressor
from app.services.context_ledger_service import get_context_ledger_service
from app.services.evidence_provenance_contract import (
    build_evidence_provenance_contract,
    render_evidence_provenance_rules,
)
from app.services.scene_contract_protocol import (
    build_scene_contract_protocol,
    render_scene_contract_protocol_rules,
)
from app.services.scene_truth_snapshot import build_scene_truth_snapshot, render_truth_snapshot
from app.services.structured_memory_compiler import get_structured_memory_compiler
from app.services.writer_input_enrichment_adapter import get_writer_input_enrichment_adapter

# --- Context envelope token 优化常量（方案四）---
# previous_chapter_tail：上一章正文尾部注入的字符数。
# 5000 → 2500：输入 token 占成本 44.8%，previous_chapter_tail 是 P2 可压缩 block，
# 2500 字符足以保持场景衔接连贯性，超出部分由 prior_chapters_archive 摘要覆盖。
PREVIOUS_CHAPTER_TAIL_CHARS = 2500

# prior_chapters_archive：加载的历史章数与每章 beat 字符数。
# 6 → 4 章：远距离伏笔呼应超过 4 章的极少，4 章 beat 足以覆盖近期剧情线。
# 800 → 500 字符：每章 beat 是摘要性片段，500 字符足以承载关键剧情转折。
PRIOR_ARCHIVE_CHAPTERS = 4
PRIOR_ARCHIVE_BEAT_CHARS = 500

# 候选章 content 两阶段截断的头部/尾部字符数（第一阶段，用于候选构建）
_PRIOR_CANDIDATE_HEAD_CHARS = 1800
_PRIOR_CANDIDATE_TAIL_CHARS = 1200
_PRIOR_CANDIDATE_LONG_THRESHOLD = _PRIOR_CANDIDATE_HEAD_CHARS + _PRIOR_CANDIDATE_TAIL_CHARS + 200


def _safe_list(value: Any) -> list:
    if value in (None, ""):
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    return [value]


def _safe_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _clip_style_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text[:limit]


def _compact_style_list(value: Any, *, limit: int, item_limit: int) -> list[str]:
    items: list[str] = []
    for item in _safe_list(value):
        text = _clip_style_text(item, item_limit)
        if text and text not in items:
            items.append(text)
        if len(items) >= limit:
            break
    return items


def _normalize_frozen_style_dimensions(value: Any) -> list[str]:
    """Return the one frozen-dimension schema consumed by FBI: ``list[str]``."""

    raw_items = value if isinstance(value, list) else [value] if value else []
    dimensions: list[str] = []
    for item in raw_items:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("dimension") or "").strip()
        else:
            name = str(item or "").strip()
        if name and name not in dimensions:
            dimensions.append(name)
        if len(dimensions) >= 16:
            break
    return dimensions


def _compact_style_profile(profile: dict | None) -> dict:
    """Keep the active writing style useful without copying the full corpus asset."""

    if not isinstance(profile, dict) or not profile:
        return {}

    raw_features = _safe_dict(profile.get("style_features"))
    feature_limits = {
        "vocabulary": 700,
        "sentence_structure": 700,
        "tone": 500,
        "pacing": 500,
        "description_style": 700,
        "dialogue_style": 700,
        "narrative_voice": 700,
    }
    style_features: dict[str, Any] = {}
    for key, limit in feature_limits.items():
        value = raw_features.get(key)
        if value not in (None, "", [], {}):
            style_features[key] = _clip_style_text(value, limit)
    for key in ("signature_phrases", "avoid_patterns", "hard_rules"):
        values = _compact_style_list(
            raw_features.get(key),
            limit=12,
            item_limit=120,
        )
        if values:
            style_features[key] = values

    style_embedding = {
        str(key): value
        for key, value in _safe_dict(profile.get("style_embedding")).items()
        if isinstance(value, (int, float, bool))
        or (isinstance(value, str) and len(value) <= 120)
    }

    raw_persona = _safe_dict(profile.get("persona_card"))
    persona_card: dict[str, Any] = {}
    for key in (
        "identity",
        "decision_pattern",
        "expression_style",
        "interpersonal_behavior",
    ):
        value = _clip_style_text(raw_persona.get(key), 300)
        if value:
            persona_card[key] = value
    hard_rules = _compact_style_list(
        raw_persona.get("hard_rules"),
        limit=8,
        item_limit=160,
    )
    if hard_rules:
        persona_card["hard_rules"] = hard_rules

    samples: list[dict[str, str]] = []
    for item in _safe_list(profile.get("sample_passages")):
        if isinstance(item, dict):
            text = _clip_style_text(item.get("text"), 320)
            category = _clip_style_text(item.get("category") or "narrative", 40)
        else:
            text = _clip_style_text(item, 320)
            category = "narrative"
        if text:
            samples.append({"category": category, "text": text})
        if len(samples) >= 4:
            break

    raw_statistics = _safe_dict(profile.get("style_statistics"))
    global_statistics = {
        str(key): value
        for key, value in _safe_dict(raw_statistics.get("global")).items()
        if isinstance(value, (int, float, bool))
        or (isinstance(value, str) and len(value) <= 120)
    }
    raw_global_statistics = _safe_dict(raw_statistics.get("global"))
    for key in ("sentence_length", "paragraph_length", "punctuation_profile"):
        nested = {
            str(nested_key): nested_value
            for nested_key, nested_value in _safe_dict(
                raw_global_statistics.get(key)
            ).items()
            if isinstance(nested_value, (int, float, bool))
            or (isinstance(nested_value, str) and len(nested_value) <= 80)
        }
        if nested:
            global_statistics[key] = dict(list(nested.items())[:24])
    word_freq_top = _safe_list(raw_global_statistics.get("word_freq_top"))[:20]
    if word_freq_top:
        global_statistics["word_freq_top"] = word_freq_top

    raw_evolution = _safe_dict(profile.get("evolution_report"))
    evolution_report: dict[str, Any] = {}
    if raw_evolution:
        evolution_report["is_multi_style"] = bool(raw_evolution.get("is_multi_style"))
        evolution_report["change_points"] = _safe_list(
            raw_evolution.get("change_points")
        )[:12]
        evolution_report["style_clusters"] = _safe_list(
            raw_evolution.get("style_clusters")
        )[:4]
        for key in ("explanation", "recommended_usage"):
            value = _clip_style_text(raw_evolution.get(key), 800)
            if value:
                evolution_report[key] = value

    return {
        "id": str(profile.get("id") or profile.get("profile_id") or ""),
        "name": _clip_style_text(
            profile.get("name") or profile.get("profile_name"),
            120,
        ),
        "version": profile.get("version"),
        "confidence": profile.get("confidence"),
        "domains": _compact_style_list(
            profile.get("domains"),
            limit=12,
            item_limit=80,
        ),
        "frozen": bool(profile.get("frozen")),
        "frozen_dimensions": _normalize_frozen_style_dimensions(
            profile.get("frozen_dimensions")
        ),
        "style_prompt": _clip_style_text(profile.get("style_prompt"), 2400),
        "style_features": style_features,
        "style_embedding": style_embedding,
        "persona_card": persona_card,
        "style_sample_passages": samples,
        "style_statistics": {"global": global_statistics} if global_statistics else {},
        "evolution_report": evolution_report,
    }


def _scene_selection_key(contract: dict, index: int) -> str:
    return str(contract.get("scene_id") or contract.get("scene_index") or index)


def _relevant_entity_names(scene_contracts: list[dict], story_state: dict | None = None) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for state_name in ((story_state or {}).get("pov_character"),):
        if isinstance(state_name, str) and state_name.strip() and state_name not in seen:
            seen.add(state_name)
            names.append(state_name)
    entity_keys = {"characters", "character_names", "pov_character", "pov", "subject_name", "object_name"}
    for contract in scene_contracts or []:
        if not isinstance(contract, dict):
            continue
        for key, value in contract.items():
            if key not in entity_keys:
                continue
            for item in _safe_list(value):
                if isinstance(item, dict):
                    item = item.get("name") or item.get("character_name")
                text = str(item or "").strip()
                if text and len(text) <= 80 and text not in seen:
                    seen.add(text)
                    names.append(text)
    return names


class ContextEnvelopeBuilder:
    """Compile, compact, and record agent context envelopes."""

    def __init__(self):
        self.budget = get_context_budget_service()
        self.compressor = get_context_compressor()
        self.ledger = get_context_ledger_service()

    async def build_chapter_writer_envelope(
        self,
        *,
        project,
        project_id: str,
        chapter_number: int,
        db: AsyncSession,
        story_state: dict,
        compiled_chapter: dict,
        scene_contracts: list[dict],
        chapter_plan_contract: Any | None = None,
        custom_instructions: str | None = None,
        profile_override: dict | None = None,
        manual_focus: str | None = None,
        dry_run: bool = False,
        style_profile: dict | None = None,
    ) -> dict:
        profile = self.budget.resolve_profile(profile_override or self._project_profile(project))
        previous_tail, prior_chapter_candidates = await self._load_previous_context(
            db=db,
            project_id=project_id,
            chapter_number=chapter_number,
        )
        structured_memory = await self._load_structured_memory(
            db=db,
            project_id=project_id,
            chapter_number=chapter_number,
            project=project,
            scene_contracts=scene_contracts,
            story_state=story_state,
        )
        proposition_selection_audit = {
            "aggregate": _safe_dict(structured_memory.get("proposition_selection_trace")),
            "scenes": {
                key: _safe_dict(value).get("trace", {})
                for key, value in _safe_dict(structured_memory.get("_scene_proposition_selections")).items()
            },
        }
        scene_contracts = self._enrich_scene_contracts_with_structured_memory(
            scene_contracts,
            structured_memory,
        )
        scene_map = self._build_scene_map(scene_contracts, compiled_chapter, previous_tail=previous_tail)
        source_of_truth = self._build_source_of_truth(scene_contracts)
        future_corridor = self._build_future_corridor(project.outline_data or {}, chapter_number)
        active_style = style_profile
        if active_style is None:
            try:
                from app.services.memory_core import CoreMemoryService

                active_style = await CoreMemoryService().get_active_style_profile(project_id)
            except Exception:
                active_style = None
        style_context = _compact_style_profile(active_style)

        envelope = {
            "schema_version": 2,
            "envelope_id": "ctxenv_ch{}_chapter_writer_{}".format(
                chapter_number,
                hashlib.sha256(
                    json.dumps(
                        {
                            "project_id": project_id,
                            "chapter_number": chapter_number,
                            "story_state": story_state,
                            "compiled_chapter": compiled_chapter,
                            "scene_contracts": scene_contracts,
                            "style_context": style_context,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                    ).encode("utf-8")
                ).hexdigest()[:16],
            ),
            "target_agent": "chapter_writer",
            "task_id": f"write_chapter_{chapter_number}",
            "model_context_profile": profile.to_dict(),
            "budget": {
                "context_window_tokens": profile.context_window_tokens,
                "reserve_output_tokens": profile.reserve_output_tokens,
                "soft_input_limit_tokens": profile.soft_input_limit_tokens,
                "hard_input_limit_tokens": profile.hard_input_limit_tokens,
            },
            "blocks": [
                self._block(
                    "chapter_scene_map",
                    "chapter_scene_map",
                    "P0",
                    False,
                    {
                        "chapter_number": chapter_number,
                        "scene_count": len(scene_map),
                        "scenes": scene_map,
                    },
                ),
                self._block(
                    "source_of_truth",
                    "source_of_truth",
                    "P0",
                    False,
                    source_of_truth,
                ),
                self._block(
                    "story_state_snapshot",
                    "story_state",
                    "P1",
                    False,
                    get_structured_memory_compiler().compile_current_story_state(story_state),
                ),
                self._block(
                    "structured_story_memory",
                    "structured_story_memory",
                    "P1",
                    False,
                    structured_memory,
                    source_refs=structured_memory.get("source_refs", []) if isinstance(structured_memory, dict) else [],
                ),
                self._block(
                    "current_chapter_outline",
                    "chapter_outline",
                    "P1",
                    False,
                    compiled_chapter.get("chapter_spine") or {},
                ),
                self._block(
                    "chapter_style_context",
                    "style_context",
                    "P1",
                    False,
                    style_context,
                    source_refs=["active_style_profile"] if style_context else [],
                ),
                self._block(
                    "previous_chapter_tail",
                    "previous_text",
                    "P2",
                    True,
                    previous_tail,
                    source_refs=[f"chapter_{chapter_number - 1}_tail"],
                ),
                self._block(
                    "prior_chapters_archive",
                    "rolling_story_summary",
                    "P3",
                    True,
                    self._build_prior_archive(prior_chapter_candidates),
                    source_refs=[f"chapter_{c.get('chapter_number')}" for c in prior_chapter_candidates],
                ),
                self._block(
                    "future_corridor",
                    "future_corridor",
                    "P3",
                    True,
                    future_corridor,
                ),
                self._block(
                    "quality_memory",
                    "quality_memory",
                    "P4",
                    True,
                    (project.core_data or {}).get("quality_memory", {}) if project else {},
                ),
                self._block(
                    "custom_instructions",
                    "user_instructions",
                    "P1" if custom_instructions else "P6",
                    False if custom_instructions else True,
                    custom_instructions or "",
                ),
            ],
            "source_index": {
                "chapter_scene_map": {"source_type": "compiled_scene_contracts"},
                "previous_chapter_tail": {"source_type": "chapter_text", "chapter_number": chapter_number - 1, "span": "tail"},
                "structured_story_memory": {"source_type": "chapter_snapshots_and_propositions"},
                "future_corridor": {"source_type": "outline_future"},
                "chapter_style_context": {"source_type": "active_style_profile"},
            },
            "compaction_policy": {
                "strategy": "domain_structured_priority",
                "manual_focus": manual_focus,
                "provider_native_compaction_allowed": False,
                "llm_summary_allowed": True,
            },
            "created_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        }
        if chapter_plan_contract is not None:
            content = chapter_plan_contract.model_dump() if hasattr(chapter_plan_contract, "model_dump") else chapter_plan_contract
            envelope["blocks"].insert(2, self._block(
                "chapter_plan_contract",
                "chapter_plan_contract",
                "P1",
                False,
                content,
            ))

        envelope = self.budget.attach_token_estimates(envelope)
        compacted = self.compressor.compact(envelope, manual_focus=manual_focus, dry_run=dry_run)
        final_envelope = compacted["envelope"]
        ledger_entry = self.ledger.record(
            project_id=project_id,
            chapter_number=chapter_number,
            task_id=final_envelope["task_id"],
            agent="chapter_writer",
            envelope=final_envelope,
            compaction_report=compacted.get("report"),
            extra={
                "scene_count": len(scene_map),
                "manual_focus": manual_focus or "",
                "dry_run": dry_run,
                "proposition_selection_audit": proposition_selection_audit,
            },
        )
        final_envelope["context_ledger_id"] = ledger_entry["ledger_id"]
        return {
            "envelope": final_envelope,
            "ledger": ledger_entry,
            "compaction": compacted.get("report", {}),
            "validation": compacted.get("validation", {}),
            "style_context": style_context,
        }

    def build_chapter_writer_packet(
        self,
        *,
        envelope: dict,
        chapter_number: int,
        scene_contracts: list[dict],
        custom_instructions: str | None = None,
    ) -> dict:
        scene_map = []
        truth_snapshot_blocks: list[str] = []
        style_context = next(
            (
                _safe_dict(block.get("content"))
                for block in envelope.get("blocks") or []
                if isinstance(block, dict)
                and block.get("block_id") == "chapter_style_context"
            ),
            {},
        )
        adapter = get_writer_input_enrichment_adapter()
        for idx, contract in enumerate(scene_contracts):
            scene_id = str(contract.get("scene_id") or f"ch_{chapter_number:03d}_s{idx+1}")
            evidence_contract = build_evidence_provenance_contract(contract)
            contract_protocol = build_scene_contract_protocol(contract)
            contract_for_writer = {
                **contract,
                "evidence_provenance_contract": evidence_contract,
                "contract_protocol": contract_protocol,
            }
            writer_enrichment = adapter.build(
                contract_for_writer,
                pov_character=contract.get("pov_character", ""),
                previous_scene_summary=str(contract.get("previous_scene_ending") or ""),
            )
            # 补齐 source_of_truth 中被扁平化丢失的关键字段
            source_of_truth = contract.get("source_of_truth") or {}
            editor_enrichment = contract.get("editor_enrichment") or {}
            # 合并 editor_enrichment 的 additional_must_show / additional_forbidden
            merged_must_show = _safe_list(contract.get("must_show") or source_of_truth.get("must_show_outline"))
            merged_forbidden = _safe_list(contract.get("forbidden") or source_of_truth.get("forbidden_outline"))
            additional_must_show = _safe_list(editor_enrichment.get("additional_must_show"))
            additional_forbidden = _safe_list(editor_enrichment.get("additional_forbidden"))
            if additional_must_show:
                merged_must_show = list({*merged_must_show, *additional_must_show})
            if additional_forbidden:
                merged_forbidden = list({*merged_forbidden, *additional_forbidden})
            scene_map.append({
                "scene_id": scene_id,
                "scene_index": idx,
                "pov_character": contract.get("pov_character", ""),
                "goal": contract.get("goal") or source_of_truth.get("goal", ""),
                "conflict": contract.get("conflict") or source_of_truth.get("conflict", ""),
                # 补齐 outline_outcome（大纲结果方向）——之前被扁平化丢失
                "outline_outcome": source_of_truth.get("outline_outcome", ""),
                # 补齐 foreshadowing_ops（伏笔操作）——之前被扁平化丢失
                "foreshadowing_ops": source_of_truth.get("foreshadowing_ops") or [],
                "must_show": merged_must_show,
                "forbidden": merged_forbidden,
                "ending_state": contract.get("ending_state") or editor_enrichment.get("ending_state", ""),
                "opening_state": contract.get("opening_state") or editor_enrichment.get("opening_state", ""),
                "temporal_anchor": contract.get("temporal_anchor") or editor_enrichment.get("temporal_anchor", ""),
                "location_anchor": contract.get("location_anchor", ""),
                "hard_must_show": _safe_list(contract.get("hard_must_show") or contract.get("must_show")),
                "soft_guidance": _safe_list(contract.get("soft_guidance")),
                "deferred_items": _safe_list(contract.get("deferred_items")),
                "long_term_constraints": contract.get("long_term_constraints") or {},
                "information_budget": contract.get("information_budget") or {},
                "style_directive": _safe_dict(contract.get("style_directive")),
                "contract_protocol": contract_protocol,
                "evidence_provenance_contract": evidence_contract,
                "writer_input_enrichment": writer_enrichment.scene_summary,
            })
            # 为每个场景构建不可变事实快照并渲染，注入 user_prompt
            # 之前 chapter_writer 路径完全缺失 truth_snapshot，导致 LLM 不知道
            # 已完成事件、不可回退地点、角色当前状态等不可变事实。
            try:
                snapshot = build_scene_truth_snapshot(
                    story_state=envelope.get("blocks", [{}]) and next(
                        (b.get("content") for b in envelope.get("blocks", [])
                         if isinstance(b, dict) and b.get("block_id") == "story_state_snapshot"),
                        {},
                    ) or {},
                    chapter_state={},
                    scene_contract=contract,
                    previous_scene_ending=str(contract.get("previous_scene_ending") or ""),
                    scene_id=scene_id,
                )
                rendered = render_truth_snapshot(snapshot)
                if rendered:
                    truth_snapshot_blocks.append(
                        f"#### 场景 {scene_id} 不可变事实快照（必须严格遵守，不得违反）\n{rendered}"
                    )
            except Exception:
                pass

        total_target = 0
        total_hard = 0
        for contract in scene_contracts:
            budget = contract.get("word_budget") or {}
            if isinstance(budget, dict):
                total_target += int(budget.get("target_chars") or 0)
                total_hard += int(budget.get("hard_max_chars") or 0)
        if total_target <= 0:
            total_target = max(2500 * max(len(scene_contracts), 1), 4000)
        if total_hard <= 0:
            total_hard = int(total_target * 1.35)

        marker_contract = "\n".join(f"[[SCENE:{item['scene_id']}]]" for item in scene_map)

        user_prompt = (
            f"### 章节任务\n写第 {chapter_number} 章，目标约 {total_target} 字，硬上限约 {total_hard} 字。\n\n"
            "### 必须逐字输出的场景 marker 顺序\n"
            f"{marker_contract}\n\n"
            "### 场景地图（必须逐一完成，并按上方 marker 顺序输出）\n"
            f"{json.dumps(scene_map, ensure_ascii=False, indent=2, default=str)}\n\n"
            "### 上下文 Envelope（已按优先级压缩，P0/P1 为硬约束）\n"
            f"{self._render_envelope(envelope)}\n"
        )
        # 注入每个场景的不可变事实快照——之前 chapter_writer 路径完全缺失，
        # 导致 LLM 不知道已完成事件、不可回退地点、角色当前状态。
        if truth_snapshot_blocks:
            user_prompt += (
                "\n### 各场景不可变事实快照（P0 硬约束，必须严格遵守）\n"
                "以下事实快照描述了每个场景开始时已确立的不可变事实。\n"
                "角色位置、状态、已完成事件、已离开地点都必须严格遵守，不得违反。\n\n"
                + "\n\n".join(truth_snapshot_blocks)
                + "\n"
            )
        if custom_instructions:
            user_prompt += f"\n### 用户额外要求\n{custom_instructions}\n"

        return {
            "user_prompt": user_prompt,
            "scene_map": scene_map,
            "target_chars": total_target,
            "hard_max_chars": total_hard,
            "context_ledger_id": envelope.get("context_ledger_id", ""),
            "token_report": envelope.get("token_report", {}),
            "system_rules": [
                *render_scene_contract_protocol_rules(),
                *render_evidence_provenance_rules(),
            ],
            "style_context": style_context,
        }

    async def preview(
        self,
        *,
        project,
        project_id: str,
        chapter_number: int,
        db: AsyncSession,
        story_state: dict,
        compiled_chapter: dict,
        scene_contracts: list[dict],
        profile_override: dict | None = None,
        manual_focus: str | None = None,
    ) -> dict:
        result = await self.build_chapter_writer_envelope(
            project=project,
            project_id=project_id,
            chapter_number=chapter_number,
            db=db,
            story_state=story_state,
            compiled_chapter=compiled_chapter,
            scene_contracts=scene_contracts,
            profile_override=profile_override,
            manual_focus=manual_focus,
            dry_run=True,
        )
        envelope = result["envelope"]
        return {
            "estimated_tokens": envelope.get("token_report", {}).get("estimated_input_tokens", 0),
            "limit_tokens": envelope.get("token_report", {}).get("limit_tokens", 0),
            "soft_input_limit_tokens": envelope.get("token_report", {}).get("soft_input_limit_tokens", 0),
            "hard_input_limit_tokens": envelope.get("token_report", {}).get("hard_input_limit_tokens", 0),
            "will_compact": envelope.get("token_report", {}).get("over_soft_limit", False),
            "blocks": [
                {
                    "block_id": block.get("block_id"),
                    "block_type": block.get("block_type"),
                    "priority": block.get("priority"),
                    "tokens": block.get("estimated_tokens", 0),
                    "compressible": block.get("compressible", False),
                }
                for block in envelope.get("blocks", [])
                if isinstance(block, dict)
            ],
            "ledger": result["ledger"],
            "validation": result.get("validation", {}),
        }

    def _project_profile(self, project) -> dict:
        core_data = getattr(project, "core_data", None) or {}
        return (
            core_data.get("generation_context")
            or core_data.get("context_profile")
            or {}
        )

    def _block(
        self,
        block_id: str,
        block_type: str,
        priority: str,
        compressible: bool,
        content: Any,
        *,
        source_refs: list[str] | None = None,
    ) -> dict:
        return {
            "block_id": block_id,
            "block_type": block_type,
            "priority": priority,
            "compressible": compressible,
            "loss_tolerance": "none" if not compressible else "low",
            "source_refs": source_refs or [],
            "protect_fields": [
                "scene_id", "character_state", "object_state",
                "unresolved_foreshadowing", "forbidden_reveals",
                "active_propositions", "proposition_constraints",
                "character_state_constraints", "long_term_constraints",
                "active_foreshadowing", "foreshadowing_constraints",
            ],
            "content_format": "json" if isinstance(content, (dict, list)) else "text",
            "content": content,
        }

    def _build_scene_map(
        self,
        scene_contracts: list[dict],
        compiled_chapter: dict,
        *,
        previous_tail: str = "",
    ) -> list[dict]:
        scene_map = []
        scene_packages = compiled_chapter.get("scene_packages") or []
        adapter = get_writer_input_enrichment_adapter()
        for idx, contract in enumerate(scene_contracts):
            pkg = scene_packages[idx] if idx < len(scene_packages) and isinstance(scene_packages[idx], dict) else {}
            source = contract.get("source_of_truth") or {}
            enrichment = contract.get("editor_enrichment") or {}
            evidence_contract = build_evidence_provenance_contract(contract)
            contract_protocol = build_scene_contract_protocol(contract)
            contract_for_writer = {
                **contract,
                "evidence_provenance_contract": evidence_contract,
                "contract_protocol": contract_protocol,
            }
            writer_enrichment = adapter.build(
                contract_for_writer,
                pov_character=contract.get("pov_character") or pkg.get("pov_character", ""),
                previous_scene_summary=previous_tail if idx == 0 else "",
            )
            scene_map.append({
                "scene_id": contract.get("scene_id") or pkg.get("scene_id") or f"scene_{idx+1}",
                "scene_index": idx,
                "pov_character": contract.get("pov_character") or pkg.get("pov_character", ""),
                "goal": source.get("goal") or contract.get("goal", ""),
                "conflict": source.get("conflict") or contract.get("conflict", ""),
                "must_show": _safe_list(source.get("must_show_outline") or contract.get("must_show")),
                "forbidden": _safe_list(source.get("forbidden_outline") or contract.get("forbidden")),
                "ending_state": enrichment.get("ending_state") or contract.get("ending_state", ""),
                "opening_state": enrichment.get("opening_state") or contract.get("opening_state", ""),
                "handoff_to_next": contract.get("handoff_to_next") or "",
                "word_budget": contract.get("word_budget") or pkg.get("word_budget") or {},
                "hard_must_show": _safe_list(contract.get("hard_must_show") or contract.get("must_show")),
                "soft_guidance": _safe_list(contract.get("soft_guidance")),
                "deferred_items": _safe_list(contract.get("deferred_items")),
                "long_term_constraints": contract.get("long_term_constraints") or {},
                "information_budget": contract.get("information_budget") or {},
                "style_directive": _safe_dict(contract.get("style_directive")),
                "contract_protocol": contract_protocol,
                "evidence_provenance_contract": evidence_contract,
                "writer_input_enrichment": writer_enrichment.scene_summary,
            })
        return scene_map

    def _build_source_of_truth(self, scene_contracts: list[dict]) -> dict:
        return {
            "scenes": [
                {
                    "scene_id": contract.get("scene_id", ""),
                    "source_of_truth": contract.get("source_of_truth") or {},
                    "forbidden": _safe_list(contract.get("forbidden")),
                    "do_not_reveal_yet": _safe_list(
                        (contract.get("chapter_plan_context") or {}).get("must_not_resolve")
                    ),
                }
                for contract in scene_contracts
            ]
        }

    def _build_future_corridor(self, outline: dict, chapter_number: int) -> dict:
        future = []
        for item in _safe_list(outline.get("chapter_spine")):
            if not isinstance(item, dict):
                continue
            ch_num = int(item.get("chapter_number") or 0)
            if ch_num <= chapter_number:
                continue
            future.append({
                "chapter_number": ch_num,
                "title": item.get("title", ""),
                "function": item.get("function", ""),
                "must_not_resolve": _safe_list(item.get("must_not_resolve")),
                "thread_ops": _safe_list(item.get("thread_ops")),
                "source_ref": f"chapter_spine.{ch_num}",
            })
            if len(future) >= 6:
                break
        return {
            "summary_type": "future_corridor",
            "do_not_reveal_yet": [
                x
                for item in future
                for x in _safe_list(item.get("must_not_resolve"))
            ],
            "future_chapters": future,
        }

    async def _load_previous_context(
        self,
        *,
        db: AsyncSession,
        project_id: str,
        chapter_number: int,
    ) -> tuple[str, list[dict]]:
        previous_tail = ""
        candidates: list[dict] = []
        try:
            result = await db.execute(
                select(Chapter)
                .where(Chapter.project_id == project_id)
                .where(Chapter.chapter_number < chapter_number)
                .order_by(Chapter.chapter_number.desc())
                .limit(PRIOR_ARCHIVE_CHAPTERS)
            )
            chapters = result.scalars().all()
        except Exception:
            try:
                import uuid
                result = await db.execute(
                    select(Chapter)
                    .where(Chapter.project_id == uuid.UUID(project_id))
                    .where(Chapter.chapter_number < chapter_number)
                    .order_by(Chapter.chapter_number.desc())
                    .limit(PRIOR_ARCHIVE_CHAPTERS)
                )
                chapters = result.scalars().all()
            except Exception:
                chapters = []

        for chapter in chapters:
            content = getattr(chapter, "content", "") or ""
            ch_num = int(getattr(chapter, "chapter_number", 0) or 0)
            if ch_num == chapter_number - 1:
                previous_tail = content[-PREVIOUS_CHAPTER_TAIL_CHARS:]
            candidates.append({
                "chapter_number": ch_num,
                "title": getattr(chapter, "title", "") or "",
                "content": content[:_PRIOR_CANDIDATE_HEAD_CHARS] + ("\n...\n" + content[-_PRIOR_CANDIDATE_TAIL_CHARS:] if len(content) > _PRIOR_CANDIDATE_LONG_THRESHOLD else ""),
            })
        return previous_tail, candidates

    async def _load_structured_memory(
        self,
        *,
        db: AsyncSession,
        project_id: str,
        chapter_number: int,
        project,
        scene_contracts: list[dict] | None = None,
        story_state: dict | None = None,
    ) -> dict:
        latest_state: dict = {}
        source_refs: list[str] = []
        try:
            result = await db.execute(
                select(ChapterSnapshot)
                .where(ChapterSnapshot.project_id == project_id)
                .where(ChapterSnapshot.chapter_number < chapter_number)
                .where(ChapterSnapshot.stale == False)
                .order_by(ChapterSnapshot.chapter_number.desc())
                .limit(1)
            )
            snapshot = result.scalar_one_or_none()
        except Exception:
            try:
                import uuid
                result = await db.execute(
                    select(ChapterSnapshot)
                    .where(ChapterSnapshot.project_id == uuid.UUID(project_id))
                    .where(ChapterSnapshot.chapter_number < chapter_number)
                    .where(ChapterSnapshot.stale == False)
                    .order_by(ChapterSnapshot.chapter_number.desc())
                    .limit(1)
                )
                snapshot = result.scalar_one_or_none()
            except Exception:
                snapshot = None

        if snapshot is not None:
            latest_state = getattr(snapshot, "chapter_state", None) or {}
            source_refs.append(f"chapter_snapshot_{getattr(snapshot, 'chapter_number', chapter_number - 1)}")

        active_propositions: list[dict] = []
        try:
            from app.services.proposition_store_service import PropositionStoreService

            active_propositions = await PropositionStoreService().get_propositions_for_scene_preparation(
                db,
                project_id,
                max(1, chapter_number - 1),
                max_chapters_back=None,
            )
            if active_propositions:
                source_refs.append("narrative_propositions_recent")
        except Exception:
            active_propositions = []

        active_foreshadowing: list[dict] = []
        try:
            from app.services.foreshadowing_service import ForeshadowingService

            foreshadowing_service = ForeshadowingService()
            lifecycle_updates = await foreshadowing_service.advance_lifecycle_for_chapter(
                project_id,
                chapter_number,
                db,
                changed_by="context_compile",
            )
            active_foreshadowing = await foreshadowing_service.list_foreshadowing_lines(
                project_id,
                db,
                statuses=["planned", "active", "dormant", "revealing"],
            )
            if active_foreshadowing:
                source_refs.append("active_foreshadowing_lines")
            if lifecycle_updates:
                source_refs.append("foreshadowing_lifecycle_advanced")
        except Exception:
            active_foreshadowing = []

        quality_memory = {}
        try:
            from app.services.quality_memory_service import QualityMemoryService

            quality_memory = QualityMemoryService().get_project_quality_memory(project)
            if quality_memory.get("recent_quality_patterns"):
                source_refs.append("project_quality_memory")
        except Exception:
            quality_memory = {}

        character_states = latest_state.get("character_states", {}) if isinstance(latest_state, dict) else {}
        if not isinstance(character_states, dict):
            latest_state["character_states"] = {}
        from app.services.proposition_selector import get_proposition_selector

        selector = get_proposition_selector()
        contracts = [item for item in (scene_contracts or []) if isinstance(item, dict)]
        aggregate_context = {
            "scene_contracts": contracts,
            "story_state": get_structured_memory_compiler().compile_current_story_state(story_state),
        }
        aggregate_selection = selector.select(
            active_propositions,
            chapter_number=chapter_number,
            scene_context=aggregate_context,
            max_items=32,
        )
        per_scene = {}
        for index, contract in enumerate(contracts):
            per_scene[_scene_selection_key(contract, index)] = selector.select(
                active_propositions,
                chapter_number=chapter_number,
                scene_context={"scene_contract": contract, "story_state": aggregate_context["story_state"]},
                max_items=16,
                quotas={
                    "current_hard": 10,
                    "unresolved": 3,
                    "continuity": 2,
                    "historical": 1,
                },
            )
        memory = get_structured_memory_compiler().compile_memory(
            latest_state=latest_state,
            active_propositions=aggregate_selection["selected"],
            active_foreshadowing=active_foreshadowing,
            quality_memory=quality_memory,
            source_refs=source_refs,
            chapter_number=chapter_number,
            relevant_entity_names=_relevant_entity_names(contracts, story_state),
            proposition_selection_trace=aggregate_selection["trace"],
        )
        memory["_scene_proposition_selections"] = per_scene
        return memory

    def _enrich_scene_contracts_with_structured_memory(
        self,
        scene_contracts: list[dict],
        structured_memory: dict,
    ) -> list[dict]:
        compiler = get_structured_memory_compiler()
        scene_selections = _safe_dict(structured_memory.get("_scene_proposition_selections"))
        enriched: list[dict] = []
        for idx, contract in enumerate(scene_contracts or []):
            if not isinstance(contract, dict):
                enriched.append(contract)
                continue
            selection = scene_selections.get(_scene_selection_key(contract, idx))
            next_contract = compiler.enrich_scene_contract(
                contract,
                structured_memory,
                proposition_selection=selection if isinstance(selection, dict) else None,
            )
            enriched.append(next_contract)
            try:
                scene_contracts[idx] = next_contract
            except Exception:
                pass
        structured_memory.pop("_scene_proposition_selections", None)
        trace = _safe_dict(structured_memory.get("proposition_selection_trace"))
        structured_memory["active_proposition_summary"] = {
            "schema_version": 2,
            "selected_count": trace.get("selected_count", 0),
            "candidate_count": trace.get("candidate_count", 0),
            "selected_by_chapter": trace.get("selected_by_chapter", {}),
            "selected_by_bucket": trace.get("selected_by_bucket", {}),
            "truncated": trace.get("truncated", False),
        }
        # Claims live once in each scene's long_term_constraints.  This block
        # retains only the selection audit trail and state projection.
        structured_memory["proposition_constraints"] = []
        structured_memory["proposition_selection_trace"] = compiler.compact_selection_trace(trace)
        foreshadowing_summary = _safe_dict(structured_memory.get("active_foreshadowing_summary"))
        foreshadowing_items = _safe_list(foreshadowing_summary.get("items"))
        structured_memory["active_foreshadowing_summary"] = {
            "schema_version": 2,
            "candidate_count": len(foreshadowing_items),
            "by_action": {
                action: sum(1 for item in foreshadowing_items if item.get("action") == action)
                for action in {str(item.get("action") or "") for item in foreshadowing_items}
                if action
            },
            "index": [
                {
                    "foreshadowing_id": item.get("foreshadowing_id"),
                    "action": item.get("action"),
                    "priority": item.get("priority"),
                }
                for item in foreshadowing_items
            ],
        }
        structured_memory.pop("active_foreshadowing", None)
        structured_memory["foreshadowing_constraints"] = []
        structured_memory.pop("active_character_states", None)
        structured_memory["required_prior_facts"] = []
        structured_memory["character_state_constraints"] = []
        structured_memory.pop("quality_memory", None)
        structured_memory["quality_policy"] = {}
        return enriched

    def _build_prior_archive(self, candidates: list[dict]) -> dict:
        return {
            "summary_type": "prior_chapters_archive",
            "plot_beats": [
                {
                    "source_ref": f"chapter_{item.get('chapter_number')}",
                    "beat": (item.get("content") or "")[:PRIOR_ARCHIVE_BEAT_CHARS],
                    "status": "established",
                }
                for item in candidates
            ],
            "character_states": [],
            "object_states": [],
            "unresolved_foreshadowing": [],
            "forbidden_reveals": [],
        }

    def _render_envelope(self, envelope: dict) -> str:
        parts = []
        for block in envelope.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            header = f"## {block.get('block_id')} [{block.get('priority')}]"
            content = block.get("content")
            if isinstance(content, (dict, list)):
                body = json.dumps(content, ensure_ascii=False, indent=2, default=str)
            else:
                body = str(content or "")
            parts.append(f"{header}\n{body}")
        return "\n\n".join(parts)


_builder: ContextEnvelopeBuilder | None = None


def get_context_envelope_builder() -> ContextEnvelopeBuilder:
    global _builder
    if _builder is None:
        _builder = ContextEnvelopeBuilder()
    return _builder
