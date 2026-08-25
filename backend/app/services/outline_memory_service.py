import uuid
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import EditMemory

logger = logging.getLogger(__name__)

CHAPTER_KEY_FIELDS = [
    "title", "main_conflict", "core_conflict", "conflict_text",
    "value_shift", "pov_character", "function", "hook",
]
SPINE_SPECIFIC_FIELDS = [
    "conflict_text", "core_conflict", "value_shift",
    "thread_ops", "function", "node_id", "hook",
]
FORESHADOWING_FIELDS = []


class OutlineMemoryService:

    async def record_change(
        self, project_id: uuid.UUID, change: dict, db: AsyncSession
    ) -> dict:
        record = EditMemory(
            project_id=project_id,
            session_id=change.get("session_id"),
            change_type=change.get("change_type"),
            target=change.get("target"),
            before=change.get("before"),
            after=change.get("after"),
            reason=change.get("reason"),
            impact=change.get("impact"),
            user_feedback=change.get("user_feedback"),
            timestamp=datetime.now(timezone.utc),
            status="pending",
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)

        structural_types = {
            "chapter_add",
            "chapter_delete",
            "conflict_change",
            "character_change",
            "constitution_change",
            "macro_plan_change",
            "arc_plan_change",
            "thread_add",
            "thread_delete",
            "thread_modify",
        }
        if change.get("change_type") in structural_types:
            try:
                from app.services.outline_read_service import OutlineReadService

                read_service = OutlineReadService()
                await read_service.suggest_modification(
                    str(project_id),
                    {
                        "source_agent": "outline_architect",
                        "suggestion": f"大纲结构性变更：{change.get('change_type')} - {change.get('target', '')}，请检查世界观一致性",
                        "target_chapters": [],
                        "reason": f"大纲修改可能影响世界观设定：{change.get('reason', '结构性调整')}",
                    },
                    db,
                )
            except Exception as e:
                logger.warning(
                    f"[OutlineMemory] worldview check trigger failed: {e}"
                )

        return {
            "id": record.id,
            "project_id": record.project_id,
            "session_id": record.session_id,
            "change_type": record.change_type,
            "target": record.target,
            "before": record.before,
            "after": record.after,
            "reason": record.reason,
            "impact": record.impact,
            "user_feedback": record.user_feedback,
            "timestamp": (
                record.timestamp.isoformat() if record.timestamp else None
            ),
            "status": record.status,
        }

    async def auto_record_outline_changes(
        self,
        project_id: uuid.UUID,
        old_outline: dict,
        new_outline: dict,
        db: AsyncSession,
        session_id: uuid.UUID | None = None,
    ) -> list[dict]:
        if session_id is None:
            session_id = uuid.uuid4()

        changes = self._diff_outlines(old_outline, new_outline)
        recorded = []

        for change in changes:
            change["session_id"] = session_id
            try:
                result = await self.record_change(project_id, change, db)
                recorded.append(result)
            except Exception as e:
                logger.warning(
                    f"[OutlineMemory] failed to record change: {e}"
                )

        return recorded

    def _diff_outlines(self, old_outline: dict, new_outline: dict) -> list[dict]:
        changes = []

        old_chapters = {
            ch.get("chapter_number"): ch
            for ch in (old_outline.get("chapters") or [])
        }
        new_chapters = {
            ch.get("chapter_number"): ch
            for ch in (new_outline.get("chapters") or [])
        }

        old_nums = set(old_chapters.keys())
        new_nums = set(new_chapters.keys())

        for num in sorted(new_nums - old_nums):
            ch = new_chapters[num]
            changes.append(
                {
                    "change_type": "chapter_add",
                    "target": {"chapter_number": num, "title": ch.get("title", "")},
                    "before": {},
                    "after": {
                        k: v
                        for k, v in ch.items()
                        if k in CHAPTER_KEY_FIELDS + FORESHADOWING_FIELDS
                    },
                    "reason": f"新增第{num}章：{ch.get('title', '')}",
                    "impact": {"broken_consistency": [], "new_opportunities": []},
                }
            )

        for num in sorted(old_nums - new_nums):
            ch = old_chapters[num]
            changes.append(
                {
                    "change_type": "chapter_delete",
                    "target": {"chapter_number": num, "title": ch.get("title", "")},
                    "before": {
                        k: v
                        for k, v in ch.items()
                        if k in CHAPTER_KEY_FIELDS + FORESHADOWING_FIELDS
                    },
                    "after": {},
                    "reason": f"删除第{num}章：{ch.get('title', '')}",
                    "impact": {
                        "broken_consistency": [
                            f"第{num}章删除，其伏笔可能需要重新安排"
                        ],
                        "new_opportunities": [],
                    },
                }
            )

        for num in sorted(old_nums & new_nums):
            old_ch = old_chapters[num]
            new_ch = new_chapters[num]
            field_changes = self._diff_chapter_fields(old_ch, new_ch)
            if field_changes:
                is_conflict = any(
                    f in ("main_conflict", "core_conflict", "value_shift")
                    for f in field_changes
                )
                change_type = (
                    "conflict_change" if is_conflict else "chapter_modify"
                )

                broken = []
                opportunities = []
                if is_conflict:
                    broken.append(
                        f"第{num}章核心冲突变更，检查后续章节是否受影响"
                    )
                if "thread_ops" in field_changes:
                    old_foreshadow = {
                        f.get("thread_id")
                        for f in old_ch.get("thread_ops", [])
                        if isinstance(f, dict)
                    }
                    new_foreshadow = {
                        f.get("thread_id")
                        for f in new_ch.get("thread_ops", [])
                        if isinstance(f, dict)
                    }
                    removed = old_foreshadow - new_foreshadow
                    added = new_foreshadow - old_foreshadow
                    if removed:
                        broken.append(
                            f"第{num}章移除伏笔：{', '.join(removed)}，检查是否有替代埋设"
                        )
                    if added:
                        opportunities.append(
                            f"第{num}章新增伏笔：{', '.join(added)}"
                        )

                changes.append(
                    {
                        "change_type": change_type,
                        "target": {
                            "chapter_number": num,
                            "title": new_ch.get("title", ""),
                        },
                        "before": {
                            k: old_ch.get(k) for k in field_changes
                        },
                        "after": {
                            k: new_ch.get(k) for k in field_changes
                        },
                        "reason": f"第{num}章修改字段：{', '.join(field_changes)}",
                        "impact": {
                            "broken_consistency": broken,
                            "new_opportunities": opportunities,
                        },
                    }
                )

        old_constitution = old_outline.get("story_constitution")
        new_constitution = new_outline.get("story_constitution")
        if old_constitution != new_constitution:
            changes.append(
                {
                    "change_type": "constitution_change",
                    "target": {"field": "story_constitution"},
                    "before": old_constitution or {},
                    "after": new_constitution or {},
                    "reason": "故事宪法变更",
                    "impact": {
                        "broken_consistency": ["故事宪法变更，检查所有章节是否仍符合核心设定"],
                        "new_opportunities": [],
                    },
                }
            )

        old_macro_plan = old_outline.get("macro_plan")
        new_macro_plan = new_outline.get("macro_plan")
        if old_macro_plan != new_macro_plan:
            changes.append(
                {
                    "change_type": "macro_plan_change",
                    "target": {"field": "macro_plan"},
                    "before": old_macro_plan or {},
                    "after": new_macro_plan or {},
                    "reason": "宏观计划变更",
                    "impact": {
                        "broken_consistency": ["宏观计划变更，检查各幕结构是否受影响"],
                        "new_opportunities": [],
                    },
                }
            )

        old_arc_plan = old_outline.get("arc_plan")
        new_arc_plan = new_outline.get("arc_plan")
        if old_arc_plan != new_arc_plan:
            changes.append(
                {
                    "change_type": "arc_plan_change",
                    "target": {"field": "arc_plan"},
                    "before": old_arc_plan or {},
                    "after": new_arc_plan or {},
                    "reason": "弧线计划变更",
                    "impact": {
                        "broken_consistency": ["弧线计划变更，检查角色弧线与章节对齐"],
                        "new_opportunities": [],
                    },
                }
            )

        thread_changes = self._diff_thread_plan(
            old_outline.get("thread_plan"), new_outline.get("thread_plan")
        )
        changes.extend(thread_changes)

        old_spine = [
            item
            for item in (old_outline.get("chapter_spine") or [])
            if isinstance(item, dict)
        ]
        new_spine = [
            item
            for item in (new_outline.get("chapter_spine") or [])
            if isinstance(item, dict)
        ]
        matched_spine, deleted_spine, added_spine = self._match_spine_items(
            old_spine, new_spine
        )

        for sp in added_spine:
            target = self._spine_target(sp)
            label = self._spine_label(sp)
            changes.append(
                {
                    "change_type": "chapter_add",
                    "target": target,
                    "before": {},
                    "after": {
                        k: v
                        for k, v in sp.items()
                        if k in SPINE_SPECIFIC_FIELDS
                    },
                    "reason": f"新增{label}：{sp.get('title', '')}",
                    "impact": {"broken_consistency": [], "new_opportunities": []},
                }
            )

        for sp in deleted_spine:
            target = self._spine_target(sp)
            label = self._spine_label(sp)
            changes.append(
                {
                    "change_type": "chapter_delete",
                    "target": target,
                    "before": {
                        k: v
                        for k, v in sp.items()
                        if k in SPINE_SPECIFIC_FIELDS
                    },
                    "after": {},
                    "reason": f"删除{label}：{sp.get('title', '')}",
                    "impact": {
                        "broken_consistency": [
                            f"{label}删除，其线索操作可能需要重新安排"
                        ],
                        "new_opportunities": [],
                    },
                }
            )

        for old_sp, new_sp in matched_spine:
            target = self._spine_target(new_sp)
            label = self._spine_label(new_sp)
            spine_changes = self._diff_chapter_fields(old_sp, new_sp)
            if spine_changes:
                is_conflict = any(
                    f in ("conflict_text", "core_conflict", "value_shift")
                    for f in spine_changes
                )
                change_type = (
                    "conflict_change" if is_conflict else "chapter_modify"
                )
                broken = []
                opportunities = []
                if is_conflict:
                    broken.append(
                        f"{label}核心冲突变更，检查后续章节是否受影响"
                    )
                if "thread_ops" in spine_changes:
                    broken.append(
                        f"{label}线索操作变更，检查线索连续性"
                    )
                changes.append(
                    {
                        "change_type": change_type,
                        "target": target,
                        "before": {k: old_sp.get(k) for k in spine_changes},
                        "after": {k: new_sp.get(k) for k in spine_changes},
                        "reason": f"{label}修改字段：{', '.join(spine_changes)}",
                        "impact": {
                            "broken_consistency": broken,
                            "new_opportunities": opportunities,
                        },
                    }
                )

        old_macro = old_outline.get("macro_structure", {})
        new_macro = new_outline.get("macro_structure", {})
        if old_macro != new_macro:
            changes.append(
                {
                    "change_type": "structure_change",
                    "target": {"field": "macro_structure"},
                    "before": old_macro,
                    "after": new_macro,
                    "reason": "宏观叙事结构变更",
                    "impact": {
                        "broken_consistency": ["宏观结构变更，检查所有章节归属"],
                        "new_opportunities": [],
                    },
                }
            )

        return changes

    @staticmethod
    def _spine_identity_candidates(item: dict) -> list[str]:
        """Return all usable identities in schema priority order.

        Older outlines often have no node_id, while recovered Story Plan data
        has chapter_id/chapter_number. Matching by all aliases prevents every
        legacy item from collapsing into the same ``None`` dictionary key.
        """
        candidates: list[str] = []
        node_id = item.get("node_id")
        if node_id not in (None, ""):
            candidates.append(f"node_id:{node_id}")
        chapter_id = item.get("chapter_id")
        if chapter_id not in (None, ""):
            candidates.append(f"chapter_id:{chapter_id}")
        chapter_number = item.get("chapter_number")
        if isinstance(chapter_number, int) and not isinstance(chapter_number, bool):
            candidates.append(f"chapter_number:{chapter_number}")
        return candidates

    @classmethod
    def _match_spine_items(
        cls, old_spine: list[dict], new_spine: list[dict]
    ) -> tuple[list[tuple[dict, dict]], list[dict], list[dict]]:
        aliases: dict[str, list[int]] = {}
        for index, item in enumerate(old_spine):
            for identity in cls._spine_identity_candidates(item):
                aliases.setdefault(identity, []).append(index)

        matched_old: set[int] = set()
        matched: list[tuple[dict, dict]] = []
        added: list[dict] = []
        for new_item in new_spine:
            old_index = next(
                (
                    index
                    for identity in cls._spine_identity_candidates(new_item)
                    for index in aliases.get(identity, [])
                    if index not in matched_old
                ),
                None,
            )
            if old_index is None:
                added.append(new_item)
                continue
            matched_old.add(old_index)
            matched.append((old_spine[old_index], new_item))

        deleted = [
            item for index, item in enumerate(old_spine) if index not in matched_old
        ]
        return matched, deleted, added

    @staticmethod
    def _spine_target(item: dict) -> dict:
        target = {
            "chapter_number": item.get("chapter_number"),
            "chapter_id": item.get("chapter_id"),
            "node_id": item.get("node_id"),
            "hook": item.get("hook", ""),
        }
        return {key: value for key, value in target.items() if value not in (None, "")}

    @staticmethod
    def _spine_label(item: dict) -> str:
        chapter_number = item.get("chapter_number")
        if isinstance(chapter_number, int) and not isinstance(chapter_number, bool):
            return f"第{chapter_number}章"
        return str(item.get("node_id") or item.get("chapter_id") or "未编号章节")

    def _diff_chapter_fields(self, old_ch: dict, new_ch: dict) -> list[str]:
        changed = []
        all_fields = CHAPTER_KEY_FIELDS + SPINE_SPECIFIC_FIELDS + FORESHADOWING_FIELDS
        seen = set()
        for field in all_fields:
            if field in seen:
                continue
            seen.add(field)
            old_val = old_ch.get(field)
            new_val = new_ch.get(field)
            if old_val != new_val:
                changed.append(field)
        return changed

    def _diff_thread_plan(
        self, old_thread_plan: dict | None, new_thread_plan: dict | None
    ) -> list[dict]:
        changes = []
        old_threads_list = (old_thread_plan or {}).get("threads") or []
        new_threads_list = (new_thread_plan or {}).get("threads") or []

        old_threads = {t.get("thread_id"): t for t in old_threads_list}
        new_threads = {t.get("thread_id"): t for t in new_threads_list}

        old_ids = set(old_threads.keys())
        new_ids = set(new_threads.keys())

        for tid in sorted(new_ids - old_ids):
            thread = new_threads[tid]
            changes.append(
                {
                    "change_type": "thread_add",
                    "target": {"thread_id": tid, "name": thread.get("name", "")},
                    "before": {},
                    "after": thread,
                    "reason": f"新增线索：{thread.get('name', tid)}",
                    "impact": {
                        "broken_consistency": [],
                        "new_opportunities": [
                            f"新增线索 {thread.get('name', tid)}，可在后续章节中埋设"
                        ],
                    },
                }
            )

        for tid in sorted(old_ids - new_ids):
            thread = old_threads[tid]
            changes.append(
                {
                    "change_type": "thread_delete",
                    "target": {"thread_id": tid, "name": thread.get("name", "")},
                    "before": thread,
                    "after": {},
                    "reason": f"删除线索：{thread.get('name', tid)}",
                    "impact": {
                        "broken_consistency": [
                            f"线索 {thread.get('name', tid)} 被删除，检查相关伏笔是否需要重新安排"
                        ],
                        "new_opportunities": [],
                    },
                }
            )

        for tid in sorted(old_ids & new_ids):
            old_t = old_threads[tid]
            new_t = new_threads[tid]
            if old_t != new_t:
                diff_fields = [
                    k for k in set(old_t.keys()) | set(new_t.keys())
                    if old_t.get(k) != new_t.get(k)
                ]
                changes.append(
                    {
                        "change_type": "thread_modify",
                        "target": {"thread_id": tid, "name": new_t.get("name", "")},
                        "before": {k: old_t.get(k) for k in diff_fields},
                        "after": {k: new_t.get(k) for k in diff_fields},
                        "reason": f"线索修改 {new_t.get('name', tid)}：字段 {', '.join(diff_fields)}",
                        "impact": {
                            "broken_consistency": [
                                f"线索 {new_t.get('name', tid)} 变更，检查相关章节的线索操作是否一致"
                            ],
                            "new_opportunities": [],
                        },
                    }
                )

        return changes

    async def get_pending_issues(
        self, project_id: uuid.UUID, db: AsyncSession
    ) -> str:
        stmt = (
            select(EditMemory)
            .where(EditMemory.project_id == project_id)
            .order_by(EditMemory.timestamp.desc())
            .limit(5)
        )
        result = await db.execute(stmt)
        records = result.scalars().all()
        if not records:
            return ""

        broken_consistency = []
        new_opportunities = []
        feedbacks = []

        for record in records:
            impact = record.impact or {}
            for item in impact.get("broken_consistency", []):
                broken_consistency.append(item)
            for item in impact.get("new_opportunities", []):
                new_opportunities.append(item)
            if record.user_feedback:
                feedbacks.append(record.user_feedback)

        sections = []
        sections.append("## 待处理事项（来自上次会话）")

        if broken_consistency:
            sections.append("")
            sections.append("⚠️ 一致性检查")
            for item in broken_consistency:
                sections.append(f"- {item}")

        if new_opportunities:
            sections.append("")
            sections.append("💡 待利用机会")
            for item in new_opportunities:
                sections.append(f"- {item}")

        if feedbacks:
            sections.append("")
            sections.append(f"📋 用户特别提醒：{'；'.join(feedbacks)}")

        return "\n".join(sections)

    async def resolve_issue(
        self, record_id: uuid.UUID, db: AsyncSession
    ) -> bool:
        stmt = select(EditMemory).where(EditMemory.id == record_id)
        result = await db.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            return False
        record.status = "resolved"
        await db.commit()
        return True
