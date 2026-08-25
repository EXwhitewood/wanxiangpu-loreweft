"""Resumable, server-managed generation of a multi-chapter spine.

The requested chapter count is a completion contract, not a suggestion to the
model.  Small plans may fit in one batch and larger plans may require many, but
both use the same validation, progress and atomic-promotion semantics.
"""

from __future__ import annotations

import asyncio
import copy
import inspect
import json
import logging
import math
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.db.db_models import Project
from app.services.llm_task_profiles import LLMTaskType

logger = logging.getLogger(__name__)

DEFAULT_BATCH_SIZE = 25
MIN_BATCH_SIZE = 5
MAX_BATCH_SIZE = 40
MAX_TARGET_CHAPTERS = 2000

# One process can have many projects, but only one expansion may mutate a
# project's draft at a time.  The draft itself is the durable source of truth;
# this registry only prevents duplicate in-process workers.
_TASKS: dict[str, asyncio.Task] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _project_uuid(project_id: uuid.UUID | str) -> uuid.UUID:
    return project_id if isinstance(project_id, uuid.UUID) else uuid.UUID(str(project_id))


def _chapter_spine(outline: dict | None) -> list[dict]:
    outline = outline or {}
    spine = outline.get("chapter_spine")
    if isinstance(spine, list):
        return [item for item in spine if isinstance(item, dict)]
    chapters = outline.get("chapters")
    if isinstance(chapters, list):
        return [item for item in chapters if isinstance(item, dict)]
    return []


def _chapter_count(outline: dict | None) -> int:
    return len(_chapter_spine(outline))


def _json_compact(value: Any, max_chars: int = 12000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= max_chars else text[:max_chars] + "…"


class OutlineExpansionService:
    """Start, inspect, resume and cancel a long-form outline expansion."""

    def __init__(
        self,
        *,
        session_factory=None,
        batch_generator: Callable[[dict], Awaitable[Any] | Any] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._batch_generator = batch_generator

    def _sessions(self):
        if self._session_factory is not None:
            return self._session_factory
        from app.db.db_models import async_session

        return async_session

    @staticmethod
    def _extract_anchors(outline: dict | None, target: int) -> list[dict]:
        source = _chapter_spine(outline)
        # A previous long outline may itself contain hundreds of chapters.  A
        # compact, evenly sampled set is enough to preserve its macro rhythm in
        # a regeneration prompt without reintroducing a giant context window.
        if len(source) > 20:
            indexes = [round(i * (len(source) - 1) / 19) for i in range(20)]
            source = [source[index] for index in indexes]
        if not source:
            unit_count = min(10, max(1, math.ceil(target / 50)))
            return [
                {
                    "anchor_id": f"unit_{index + 1}",
                    "title": f"第{index + 1}叙事单元",
                    "summary": "待根据故事宪法和项目设定展开",
                }
                for index in range(unit_count)
            ]
        anchors: list[dict] = []
        for index, item in enumerate(source, start=1):
            anchors.append(
                {
                    "anchor_id": str(item.get("node_id") or f"unit_{index}"),
                    "title": str(item.get("title") or f"第{index}叙事单元"),
                    "summary": str(
                        item.get("summary")
                        or item.get("conflict_text")
                        or item.get("main_conflict")
                        or item.get("core_conflict")
                        or ""
                    )[:500],
                    "pov_character": str(item.get("pov_character") or ""),
                }
            )
        return anchors

    @staticmethod
    def _build_units(anchors: list[dict], target: int) -> list[dict]:
        count = max(1, len(anchors))
        base, remainder = divmod(target, count)
        units: list[dict] = []
        start = 1
        for index, anchor in enumerate(anchors):
            size = base + (1 if index < remainder else 0)
            end = start + size - 1
            units.append({
                "unit_id": f"unit_{index + 1}",
                "chapter_range": [start, end],
                "anchor": copy.deepcopy(anchor),
            })
            start = end + 1
        return units

    @staticmethod
    def _job_from_outline(outline: dict | None) -> dict | None:
        meta = (outline or {}).get("meta") or {}
        job = meta.get("expansion_job")
        return copy.deepcopy(job) if isinstance(job, dict) else None

    @staticmethod
    def _state(job: dict | None, *, actual_chapters: int = 0, live: bool = False) -> dict:
        if not job:
            return {
                "has_job": False,
                "status": "idle",
                "actual_chapters": actual_chapters,
            }
        target = int(job.get("target_chapters") or 0)
        completed = int(job.get("completed_chapters") or 0)
        return {
            "has_job": True,
            "job_id": job.get("job_id"),
            "status": job.get("status", "unknown"),
            "target_chapters": target,
            "batch_size": int(job.get("batch_size") or DEFAULT_BATCH_SIZE),
            "completed_chapters": completed,
            "next_chapter": int(job.get("next_chapter") or completed + 1),
            "actual_chapters": actual_chapters,
            "progress": round((completed / target) * 100, 2) if target else 0,
            "error": job.get("error"),
            "message": job.get("message"),
            "started_at": job.get("started_at"),
            "updated_at": job.get("updated_at"),
            "live": live,
        }

    async def get_status(self, project_id: uuid.UUID | str, db: AsyncSession) -> dict:
        project = await db.get(Project, _project_uuid(project_id))
        if not project:
            return {"error": "项目不存在"}
        draft = copy.deepcopy(project.draft_outline or {})
        job = self._job_from_outline(draft)
        actual = _chapter_count(draft) if job else _chapter_count(project.outline_data)
        if not job:
            job = self._job_from_outline(project.outline_data)
        key = str(project.id)
        live = bool(_TASKS.get(key) and not _TASKS[key].done())
        state = self._state(job, actual_chapters=actual, live=live)
        # A process restart cannot leave a durable job claiming to be actively
        # running.  Report it as resumable without mutating the database from a
        # read endpoint.
        if state.get("status") in {"queued", "running", "completing"} and not live:
            state["status"] = "paused"
            state["message"] = "任务未在运行，可点击继续"
        return state

    async def start(
        self,
        project_id: uuid.UUID | str,
        target_chapters: int,
        db: AsyncSession,
        *,
        batch_size: int = DEFAULT_BATCH_SIZE,
        replace_existing: bool = True,
        force_restart: bool = False,
        seed_context: dict | None = None,
        schedule: bool = True,
    ) -> dict:
        try:
            target = int(target_chapters)
            batch = int(batch_size)
        except (TypeError, ValueError) as exc:
            return {"error": "目标章数和批次大小必须是整数", "code": "invalid_expansion_options"}
        if target < 1 or target > MAX_TARGET_CHAPTERS:
            return {
                "error": f"目标章数必须在 1-{MAX_TARGET_CHAPTERS} 之间",
                "code": "invalid_expansion_target",
            }
        if batch < MIN_BATCH_SIZE or batch > MAX_BATCH_SIZE:
            return {
                "error": f"每批章数必须在 {MIN_BATCH_SIZE}-{MAX_BATCH_SIZE} 之间",
                "code": "invalid_expansion_batch_size",
            }

        pid = _project_uuid(project_id)
        project = await db.get(Project, pid)
        if not project:
            return {"error": "项目不存在", "code": "project_not_found"}
        if (project.outline_data or {}).get("_frozen"):
            return {"error": "正式大纲已冻结，请先解冻后再扩展", "code": "outline_frozen"}

        existing_draft = copy.deepcopy(project.draft_outline or {})
        existing_job = self._job_from_outline(existing_draft)
        if existing_job:
            existing_status = str(existing_job.get("status") or "")
            if existing_status in {"queued", "running", "completing"}:
                if int(existing_job.get("target_chapters") or 0) == target:
                    return self._state(
                        existing_job,
                        actual_chapters=_chapter_count(existing_draft),
                        live=bool(_TASKS.get(str(pid))),
                    ) | {"status": "already_running"}
                return {"error": "已有另一项多章节规划任务正在运行", "code": "expansion_running"}
            if (
                not force_restart
                and int(existing_job.get("target_chapters") or 0) == target
                and existing_status in {"failed", "paused", "cancelled"}
            ):
                return await self.resume(pid, db, schedule=schedule)
            if not force_restart:
                return {"error": "已有待处理的扩展草稿，请先继续或丢弃", "code": "expansion_draft_pending"}
        elif existing_draft:
            return {"error": "已有待确认的大纲草稿，请先处理后再扩展", "code": "outline_draft_pending"}

        source_outline = copy.deepcopy(project.outline_data or {})
        current_count = _chapter_count(source_outline)
        if current_count == target and not force_restart:
            completed_job = self._job_from_outline(source_outline)
            if completed_job and completed_job.get("status") == "completed":
                return self._state(completed_job, actual_chapters=current_count)
            return {
                "has_job": False,
                "status": "already_complete",
                "target_chapters": target,
                "completed_chapters": current_count,
                "actual_chapters": current_count,
                "progress": 100,
            }
        if not replace_existing and current_count > target:
            return {"error": "追加模式不能把目标章数设为现有章数以下", "code": "invalid_append_target"}

        anchors = self._extract_anchors(source_outline, target)
        units = self._build_units(anchors, target)
        job_id = str(uuid.uuid4())
        job = {
            "job_id": job_id,
            "status": "queued",
            "target_chapters": target,
            "batch_size": batch,
            "replace_existing": bool(replace_existing),
            "force_restart": bool(force_restart),
            "base_outline_version": int(getattr(project, "outline_version", 0) or 0),
            "completed_chapters": 0,
            "next_chapter": 1,
            "source_anchor_count": len(anchors),
            "source_anchors": anchors,
            "units": units,
            "seed_context": copy.deepcopy(seed_context or {}),
            "created_at": _now(),
            "updated_at": _now(),
            "error": None,
            "message": "已排队，等待按批生成",
        }
        draft = self._initial_draft(source_outline, job)
        project.draft_outline = draft
        flag_modified(project, "draft_outline")
        await db.commit()
        await db.refresh(project)
        if schedule:
            self._schedule(pid, job_id)
        return self._state(job, actual_chapters=0, live=bool(schedule)) | {
            "status": "started",
            "draft_saved": True,
        }

    async def resume(
        self,
        project_id: uuid.UUID | str,
        db: AsyncSession,
        *,
        schedule: bool = True,
    ) -> dict:
        pid = _project_uuid(project_id)
        project = await db.get(Project, pid)
        if not project:
            return {"error": "项目不存在", "code": "project_not_found"}
        draft = copy.deepcopy(project.draft_outline or {})
        job = self._job_from_outline(draft)
        if not job:
            return {"error": "没有可续跑的多章节规划任务", "code": "expansion_not_found"}
        status = str(job.get("status") or "")
        if status == "completed":
            return self._state(job, actual_chapters=_chapter_count(draft))
        if status in {"queued", "running", "completing"} and _TASKS.get(str(pid)):
            return self._state(job, actual_chapters=_chapter_count(draft), live=True) | {"status": "already_running"}
        job["status"] = "queued"
        job["error"] = None
        job["message"] = "已重新排队，将从上次成功批次继续"
        job["updated_at"] = _now()
        draft.setdefault("meta", {})["expansion_job"] = job
        project.draft_outline = draft
        flag_modified(project, "draft_outline")
        await db.commit()
        if schedule:
            self._schedule(pid, str(job.get("job_id")))
        return self._state(job, actual_chapters=_chapter_count(draft), live=bool(schedule)) | {"status": "resumed"}

    async def cancel(self, project_id: uuid.UUID | str, db: AsyncSession) -> dict:
        pid = _project_uuid(project_id)
        key = str(pid)
        task = _TASKS.get(key)
        if task and not task.done():
            task.cancel()
        project = await db.get(Project, pid)
        if not project:
            return {"error": "项目不存在", "code": "project_not_found"}
        draft = copy.deepcopy(project.draft_outline or {})
        job = self._job_from_outline(draft)
        if not job:
            return {"error": "没有正在运行的多章节规划任务", "code": "expansion_not_found"}
        job["status"] = "cancelled"
        job["message"] = "任务已取消，正式大纲未改变"
        job["updated_at"] = _now()
        draft.setdefault("meta", {})["expansion_job"] = job
        project.draft_outline = draft
        flag_modified(project, "draft_outline")
        await db.commit()
        return self._state(job, actual_chapters=_chapter_count(draft))

    def _initial_draft(self, source: dict, job: dict) -> dict:
        draft: dict = {}
        for key in (
            "story_constitution",
            "arc_plan",
            "generation_corridors",
        ):
            if source.get(key) not in (None, "", [], {}):
                draft[key] = copy.deepcopy(source[key])
        draft["macro_plan"] = self._expanded_macro_plan(source, job)
        if source.get("thread_plan") not in (None, "", [], {}):
            draft["thread_plan"] = self._remap_thread_plan(
                source.get("thread_plan") or {},
                job.get("units") or [],
            )
        meta = copy.deepcopy(source.get("meta") or {})
        meta.pop("frozen_layers", None)
        meta["generation_target_chapters"] = int(job["target_chapters"])
        meta["generation_staged"] = True
        meta["expansion_job"] = copy.deepcopy(job)
        draft["meta"] = meta
        draft["chapter_spine"] = []
        draft["scene_briefs"] = {}
        return draft

    @staticmethod
    def _expanded_macro_plan(source: dict, job: dict) -> dict:
        macro = copy.deepcopy(source.get("macro_plan") or {})
        volumes: list[dict] = []
        for index, unit in enumerate(job.get("units") or [], start=1):
            anchor = unit.get("anchor") or {}
            volumes.append({
                "volume_id": str(anchor.get("anchor_id") or unit.get("unit_id") or f"v{index}"),
                "name": str(anchor.get("title") or f"第{index}卷"),
                "chapter_range": copy.deepcopy(unit.get("chapter_range") or []),
                "summary": str(anchor.get("summary") or ""),
                "source": "expanded_from_anchor",
            })
        macro["volumes"] = volumes
        macro["target_chapters"] = int(job.get("target_chapters") or 0)
        macro["source_anchor_count"] = int(job.get("source_anchor_count") or len(volumes))
        return macro

    @staticmethod
    def _remap_thread_plan(thread_plan: dict, units: list[dict]) -> dict:
        """Scale old macro-node references into the expanded chapter ranges."""
        remapped = copy.deepcopy(thread_plan)
        threads = remapped.get("threads")
        if not isinstance(threads, list) or not units:
            return remapped

        def _mapped(number: Any, operation: str) -> int | None:
            try:
                anchor_index = int(number) - 1
            except (TypeError, ValueError):
                return None
            if anchor_index < 0 or anchor_index >= len(units):
                # References that are already inside the expanded range are
                # preserved.  This matters when restarting a former 500-chapter
                # plan from sampled anchors.
                try:
                    value = int(number)
                    target = int((units[-1].get("chapter_range") or [0, 0])[1])
                    return value if 1 <= value <= target else None
                except (TypeError, ValueError, IndexError):
                    return None
            start, end = [int(value) for value in units[anchor_index]["chapter_range"]]
            if operation in {"payoff", "reveal", "resolve", "close"}:
                return end
            if operation in {"escalation", "escalate", "develop"}:
                return (start + end) // 2
            return start

        field_ops = {
            "plant_chapters": "plant",
            "escalation_chapters": "escalation",
            "reveal_chapters": "reveal",
            "payoff_chapters": "payoff",
        }
        for thread in threads:
            if not isinstance(thread, dict):
                continue
            for field, operation in field_ops.items():
                values = thread.get(field)
                if not isinstance(values, list):
                    continue
                mapped = [_mapped(value, operation) for value in values]
                thread[field] = list(dict.fromkeys(value for value in mapped if value is not None))
        return remapped

    def _schedule(self, project_id: uuid.UUID, job_id: str | None) -> None:
        key = str(project_id)
        current = _TASKS.get(key)
        if current and not current.done():
            return
        task = asyncio.create_task(self._run(key, str(job_id or "")))
        _TASKS[key] = task

        def _cleanup(done: asyncio.Task) -> None:
            if _TASKS.get(key) is done:
                _TASKS.pop(key, None)
            try:
                done.exception()
            except (asyncio.CancelledError, Exception):
                pass

        task.add_done_callback(_cleanup)

    async def _run(self, project_id: str, job_id: str) -> None:
        try:
            while True:
                async with self._sessions()() as db:
                    project = await db.get(Project, _project_uuid(project_id))
                    if not project:
                        return
                    draft = copy.deepcopy(project.draft_outline or {})
                    job = self._job_from_outline(draft)
                    if not job or str(job.get("job_id")) != job_id:
                        return
                    if job.get("status") == "cancelled":
                        return
                    target = int(job.get("target_chapters") or 0)
                    batch_size = int(job.get("batch_size") or DEFAULT_BATCH_SIZE)
                    start = int(job.get("next_chapter") or 1)
                    if start > target:
                        break
                    end = min(target, start + batch_size - 1)
                    job["status"] = "running"
                    job["started_at"] = job.get("started_at") or _now()
                    job["updated_at"] = _now()
                    job["message"] = f"正在生成第 {start}-{end} 章"
                    draft.setdefault("meta", {})["expansion_job"] = job
                    project.draft_outline = draft
                    flag_modified(project, "draft_outline")
                    await db.commit()
                    context = self._batch_context(project, draft, job, start, end)

                batch = await self._generate_validated_batch(context, start, end)

                async with self._sessions()() as db:
                    project = await db.get(Project, _project_uuid(project_id))
                    if not project:
                        return
                    draft = copy.deepcopy(project.draft_outline or {})
                    job = self._job_from_outline(draft)
                    if not job or str(job.get("job_id")) != job_id:
                        return
                    if job.get("status") == "cancelled":
                        return
                    expected_start = int(job.get("next_chapter") or 1)
                    if expected_start != start:
                        # Another worker cannot safely append to this job.
                        raise RuntimeError("章节扩展进度发生冲突，请重新加载后续跑")
                    spine = _chapter_spine(draft)
                    existing_numbers = {int(item.get("chapter_number")) for item in spine if item.get("chapter_number") is not None}
                    if any(int(item["chapter_number"]) in existing_numbers for item in batch):
                        raise RuntimeError("批次包含已写入的章节号，拒绝重复落盘")
                    spine.extend(batch)
                    spine.sort(key=lambda item: int(item["chapter_number"]))
                    draft["chapter_spine"] = spine
                    # Keep the legacy projection in the draft too, so the
                    # existing preview/sidebar code can inspect real progress.
                    draft["chapters"] = [self._legacy_chapter(item) for item in spine]
                    job["completed_chapters"] = end
                    job["next_chapter"] = end + 1
                    job["status"] = "completing" if end >= target else "queued"
                    job["error"] = None
                    job["updated_at"] = _now()
                    job["message"] = f"已完成 {end}/{target} 章"
                    draft.setdefault("meta", {})["expansion_job"] = job
                    project.draft_outline = draft
                    flag_modified(project, "draft_outline")
                    await db.commit()
                if end >= target:
                    await self._finalize(project_id, job_id)
                    return
        except asyncio.CancelledError:
            # cancel() writes the durable cancelled state.  If cancellation
            # came from an application shutdown, leave a resumable paused job.
            await self._mark_interrupted(project_id, job_id)
            raise
        except Exception as exc:
            logger.exception("Outline expansion failed for %s", project_id)
            await self._mark_failed(project_id, job_id, str(exc))

    def _batch_context(self, project, draft: dict, job: dict, start: int, end: int) -> dict:
        previous = _chapter_spine(draft)
        previous_tail = previous[-3:]
        units = [
            unit for unit in (job.get("units") or [])
            if int(unit.get("chapter_range", [0, 0])[1]) >= start
            and int(unit.get("chapter_range", [0, 0])[0]) <= end
        ]
        return {
            "project_id": str(project.id),
            "project_info": {
                "name": project.name or "",
                "description": project.description or "",
                "genre": project.genre or "",
                "word_count_target": project.word_count_target,
            },
            "target_chapters": int(job.get("target_chapters") or 0),
            "start_chapter": start,
            "end_chapter": end,
            "source_anchors": copy.deepcopy(job.get("source_anchors") or []),
            "units": copy.deepcopy(units),
            "previous_tail": copy.deepcopy(previous_tail),
            "seed_context": copy.deepcopy(job.get("seed_context") or {}),
            "story_constitution": copy.deepcopy(draft.get("story_constitution") or {}),
            "thread_plan": copy.deepcopy(draft.get("thread_plan") or {}),
        }

    async def _generate_validated_batch(self, context: dict, start: int, end: int) -> list[dict]:
        last_error = ""
        for attempt in range(3):
            request_context = dict(context)
            request_context["validation_error"] = last_error
            raw = await self._generate_batch(request_context)
            try:
                return self._normalize_batch(raw, start, end, context)
            except ValueError as exc:
                last_error = str(exc)
                logger.warning(
                    "Outline expansion batch %s-%s rejected (attempt %s): %s",
                    start,
                    end,
                    attempt + 1,
                    last_error,
                )
        raise RuntimeError(f"第{start}-{end}章批次未通过结构校验：{last_error}")

    async def _generate_batch(self, context: dict) -> Any:
        if self._batch_generator is not None:
            value = self._batch_generator(context)
            return await value if inspect.isawaitable(value) else value
        from app.agents.outline_architect import OutlineArchitectAgent

        llm = await OutlineArchitectAgent().get_llm_client()
        prompt = self._build_batch_prompt(context)
        return await llm.generate(
            system_prompt=(
                "你是小说多章节脊柱生成器。你只负责逐章结构化规划，不写正文。"
                "一次只生成指定范围，绝不把多个章节合并成卷或节点。"
            ),
            user_prompt=prompt,
            temperature=0.45,
            max_tokens=24000,
            timeout=300,
            task_type=LLMTaskType.OUTLINE_GENERATION,
        )

    def _build_batch_prompt(self, context: dict) -> str:
        start = context["start_chapter"]
        end = context["end_chapter"]
        error = context.get("validation_error")
        return (
            f"项目：{context['project_info'].get('name', '')}\n"
            f"简介：{context['project_info'].get('description', '')}\n"
            f"题材：{context['project_info'].get('genre', '')}\n"
            f"全书目标：{context['target_chapters']}章\n"
            f"本批严格范围：第{start}-{end}章，共{end - start + 1}章\n\n"
            "请返回 JSON 对象 {\"chapter_spine\": [...]}，数组必须恰好包含本批每一个章节号，"
            "不得跳号、重复、合并或只返回关键节点。每项至少包含 title、summary、conflict_text、"
            "core_conflict(desire/obstacle/action/turn)、value_shift(axis/from/to)、pov_character、hook、"
            "thread_ops。summary 和 conflict_text 都要描述可承载约2000字正文的单一剧情单元。\n\n"
            f"故事宪法：{_json_compact(context.get('story_constitution'), 9000)}\n"
            f"线索计划：{_json_compact(context.get('thread_plan'), 9000)}\n"
            f"原有宏观锚点：{_json_compact(context.get('source_anchors'), 9000)}\n"
            f"本批所属单元：{_json_compact(context.get('units'), 7000)}\n"
            f"上一批末尾：{_json_compact(context.get('previous_tail'), 5000)}\n"
            f"作者补充：{_json_compact(context.get('seed_context'), 5000)}\n"
            + (f"上次校验失败，请修正：{error}\n" if error else "")
            + "只输出 JSON，不要 Markdown，不要解释。"
        )

    @staticmethod
    def _parse_json(raw: Any) -> Any:
        if isinstance(raw, (dict, list)):
            return raw
        text = str(raw or "").strip()
        if not text:
            raise ValueError("模型返回为空")
        fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.IGNORECASE | re.DOTALL)
        if fenced:
            text = fenced.group(1).strip()
        decoder = json.JSONDecoder()
        for marker in ("{", "["):
            index = text.find(marker)
            if index < 0:
                continue
            try:
                value, _ = decoder.raw_decode(text[index:])
                return value
            except json.JSONDecodeError:
                continue
        raise ValueError("无法解析为 JSON")

    @classmethod
    def _normalize_batch(cls, raw: Any, start: int, end: int, context: dict) -> list[dict]:
        payload = cls._parse_json(raw)
        if isinstance(payload, list):
            items = payload
        elif isinstance(payload, dict):
            items = payload.get("chapter_spine") or payload.get("chapters") or []
        else:
            items = []
        if not isinstance(items, list):
            raise ValueError("chapter_spine 必须是数组")
        expected_numbers = list(range(start, end + 1))
        actual_numbers: list[int] = []
        normalized: list[dict] = []
        unit_by_chapter: dict[int, str] = {}
        for unit in context.get("units") or []:
            try:
                unit_start, unit_end = unit["chapter_range"]
                for number in range(int(unit_start), int(unit_end) + 1):
                    unit_by_chapter[number] = str(unit.get("unit_id") or "")
            except (KeyError, TypeError, ValueError):
                continue
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("章节项必须是对象")
            try:
                number = int(item.get("chapter_number"))
            except (TypeError, ValueError):
                raise ValueError("章节号必须是整数")
            actual_numbers.append(number)
            title = str(item.get("title") or "").strip()
            conflict = str(
                item.get("conflict_text")
                or item.get("summary")
                or item.get("main_conflict")
                or ""
            ).strip()
            hook = str(item.get("hook") or "").strip()
            if not title or not conflict or not hook:
                raise ValueError(f"第{number}章缺少 title、conflict_text/summary 或 hook")
            normalized_item = copy.deepcopy(item)
            normalized_item["chapter_number"] = number
            normalized_item["chapter_id"] = str(item.get("chapter_id") or f"ch_{number:03d}")
            normalized_item["title"] = title
            normalized_item["conflict_text"] = conflict
            normalized_item.setdefault("summary", conflict)
            if unit_by_chapter.get(number):
                normalized_item.setdefault("node_id", unit_by_chapter[number])
            normalized.append(normalized_item)
        if actual_numbers != expected_numbers:
            raise ValueError(
                f"本批必须严格返回 {expected_numbers[0]}-{expected_numbers[-1]}，实际为 {actual_numbers[:12]}"
            )
        return normalized

    @staticmethod
    def _legacy_chapter(item: dict) -> dict:
        return {
            "chapter_number": item.get("chapter_number"),
            "title": item.get("title", ""),
            "main_conflict": item.get("conflict_text", ""),
            "core_conflict": copy.deepcopy(item.get("core_conflict")),
            "value_shift": copy.deepcopy(item.get("value_shift")),
            "pov_character": item.get("pov_character", ""),
            "hook": item.get("hook", ""),
            "node_id": item.get("node_id"),
        }

    async def _finalize(self, project_id: str, job_id: str) -> None:
        async with self._sessions()() as db:
            project = await db.get(Project, _project_uuid(project_id))
            if not project:
                return
            draft = copy.deepcopy(project.draft_outline or {})
            job = self._job_from_outline(draft)
            if not job or str(job.get("job_id")) != job_id:
                return
            target = int(job.get("target_chapters") or 0)
            spine = _chapter_spine(draft)
            numbers = [int(item.get("chapter_number")) for item in spine if item.get("chapter_number") is not None]
            if numbers != list(range(1, target + 1)):
                raise RuntimeError("最终提交前章节脊柱未形成 1-N 连续序列")
            base_version = int(job.get("base_outline_version") or 0)
            current_version = int(getattr(project, "outline_version", 0) or 0)
            if current_version != base_version:
                raise RuntimeError("正式大纲在扩展期间发生变化，已停止自动替换，请重新开始任务")
            job["status"] = "completed"
            job["completed_chapters"] = target
            job["next_chapter"] = target + 1
            job["message"] = f"已完成 {target}/{target} 章，正在提交正式大纲"
            job["updated_at"] = _now()
            job["error"] = None
            draft.setdefault("meta", {})["expansion_job"] = job
            draft["meta"]["generation_target_chapters"] = target
            draft["meta"]["generation_staged"] = False
            from app.services.story_plan_service import StoryPlanService

            result = await StoryPlanService().save_outline_data(
                _project_uuid(project_id),
                draft,
                db,
                mode="replace",
                expected_chapter_count=target,
            )
            if result.get("error"):
                raise RuntimeError(result["error"])
            project.draft_outline = {}
            flag_modified(project, "draft_outline")
            await db.commit()
            try:
                from app.services.outline_index_service import OutlineIndexService

                await OutlineIndexService().generate_and_save_index(_project_uuid(project_id), db)
                await db.commit()
            except Exception as exc:
                # The official outline is already committed.  Index rebuild is
                # auxiliary and can safely be retried by a later refresh.
                logger.warning("Outline index rebuild after expansion failed: %s", exc)

    async def _mark_failed(self, project_id: str, job_id: str, error: str) -> None:
        await self._mark_status(project_id, job_id, "failed", error[:1200], "批次失败，可点击继续")

    async def _mark_interrupted(self, project_id: str, job_id: str) -> None:
        await self._mark_status(project_id, job_id, "paused", None, "任务已暂停，可点击继续")

    async def _mark_status(self, project_id: str, job_id: str, status: str, error: str | None, message: str) -> None:
        try:
            async with self._sessions()() as db:
                project = await db.get(Project, _project_uuid(project_id))
                if not project:
                    return
                draft = copy.deepcopy(project.draft_outline or {})
                job = self._job_from_outline(draft)
                if not job or str(job.get("job_id")) != job_id:
                    return
                if job.get("status") in {"cancelled", "completed"}:
                    return
                job["status"] = status
                job["error"] = error
                job["message"] = message
                job["updated_at"] = _now()
                draft.setdefault("meta", {})["expansion_job"] = job
                project.draft_outline = draft
                flag_modified(project, "draft_outline")
                await db.commit()
        except Exception:
            logger.exception("Unable to persist outline expansion status")
