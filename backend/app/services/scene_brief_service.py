import uuid
import logging
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.db_models import Project
from app.services.story_plan_service import StoryPlanService
from app.services.scene_budget_service import SceneBudgetService
from app.services.llm_task_profiles import LLMTaskType

logger = logging.getLogger(__name__)


class SceneBriefService:

    def __init__(self):
        self._plan_service = StoryPlanService()
        self._budget_service = SceneBudgetService()

    async def get_scene_brief(
        self, project_id: uuid.UUID, chapter_number: int, db: AsyncSession
    ) -> dict | None:
        ch_id = f"ch_{chapter_number:03d}"
        plan = await self._plan_service.get_story_plan(project_id, db)
        briefs = plan.get("scene_briefs", {})
        brief = briefs.get(ch_id)
        if brief:
            return brief

        project = await db.get(Project, project_id)
        if not project:
            return None

        outline = project.outline_data or {}
        for ch in outline.get("chapter_spine", []) or []:
            if ch.get("chapter_number") == chapter_number:
                scenes = ch.get("scenes", [])
                if scenes:
                    return {
                        "chapter_id": ch_id,
                        "version": 1,
                        "source": "legacy",
                        "expires_when": "chapter_spine_changed",
                        "expires_scope": "current",
                        "scenes": scenes,
                    }

        for ch in outline.get("chapters", []) or []:
            if ch.get("chapter_number") == chapter_number:
                scenes = ch.get("scenes", [])
                if scenes:
                    return {
                        "chapter_id": ch_id,
                        "version": 1,
                        "source": "legacy",
                        "expires_when": "chapter_spine_changed",
                        "expires_scope": "current",
                        "scenes": scenes,
                    }
        return None

    async def get_scene_briefs_range(
        self,
        project_id: uuid.UUID,
        start_chapter: int,
        end_chapter: int,
        db: AsyncSession,
    ) -> dict[str, dict]:
        result = {}
        for ch_num in range(start_chapter, end_chapter + 1):
            brief = await self.get_scene_brief(project_id, ch_num, db)
            if brief:
                result[f"ch_{ch_num:03d}"] = brief
        return result

    async def save_scene_brief(
        self,
        project_id: uuid.UUID,
        chapter_number: int,
        brief_data: dict,
        db: AsyncSession,
    ) -> dict:
        ch_id = f"ch_{chapter_number:03d}"
        plan = await self._plan_service.get_story_plan(project_id, db)
        briefs = plan.get("scene_briefs", {})
        chapter = next(
            (
                item
                for item in plan.get("chapter_spine", [])
                if item.get("chapter_number") == chapter_number
            ),
            {},
        )
        budget = self._budget_service.calculate(chapter)

        brief_data["chapter_id"] = ch_id
        if "source" not in brief_data:
            brief_data["source"] = "generated"
        current_version = briefs.get(ch_id, {}).get("version", 0)
        incoming_version = brief_data.get("version", 0) or 1
        if current_version > 0:
            brief_data["version"] = max(current_version, incoming_version) + 1
        else:
            brief_data["version"] = incoming_version

        old_signature = briefs.get(ch_id, {}).get("scene_budget_signature")
        if old_signature and old_signature != budget["scene_budget_signature"]:
            brief_data["scene_count_user_overridden"] = False
        brief_data.update({
            "minimum_scene_count": budget["minimum_scene_count"],
            "recommended_scene_count": budget["recommended_scene_count"],
            "scene_budget_reasons": budget["scene_budget_reasons"],
            "scene_budget_signature": budget["scene_budget_signature"],
            "scene_count_user_overridden": brief_data.get(
                "scene_count_user_overridden", False
            ),
        })

        briefs[ch_id] = brief_data
        return await self._plan_service.update_layer(
            project_id, "scene_briefs", briefs, db
        )

    async def mark_user_edited(
        self, project_id: uuid.UUID, chapter_number: int, db: AsyncSession
    ) -> dict:
        brief = await self.get_scene_brief(project_id, chapter_number, db)
        if not brief:
            return {"error": f"第 {chapter_number} 章无场景简报"}

        brief["source"] = "user_edited"
        return await self.save_scene_brief(project_id, chapter_number, brief, db)

    async def invalidate_briefs(
        self,
        project_id: uuid.UUID,
        trigger: str,
        changed_chapter: int,
        db: AsyncSession,
    ) -> dict:
        plan = await self._plan_service.get_story_plan(project_id, db)
        briefs = plan.get("scene_briefs", {})
        invalidated = []

        for ch_id, brief in list(briefs.items()):
            if brief.get("source") == "user_edited":
                continue

            scope = brief.get("expires_scope", "current")
            should_invalidate = False

            if trigger == "chapter_spine_changed":
                if scope in ("current", "adjacent"):
                    ch_num = self._ch_id_to_number(ch_id)
                    if abs(ch_num - changed_chapter) <= 1:
                        should_invalidate = True
                else:
                    should_invalidate = True

            elif trigger == "state_changed":
                if scope in ("current", "adjacent"):
                    ch_num = self._ch_id_to_number(ch_id)
                    if ch_num == changed_chapter:
                        should_invalidate = True

            elif trigger == "thread_plan_changed":
                if scope == "related_threads":
                    should_invalidate = True
                else:
                    should_invalidate = True

            if should_invalidate:
                briefs[ch_id]["expires_when"] = trigger
                invalidated.append(ch_id)

        if invalidated:
            await self._plan_service.update_layer(
                project_id, "scene_briefs", briefs, db
            )

        return {"invalidated": invalidated, "count": len(invalidated)}

    async def discard_brief(
        self, project_id: uuid.UUID, chapter_number: int, db: AsyncSession
    ) -> dict:
        ch_id = f"ch_{chapter_number:03d}"
        plan = await self._plan_service.get_story_plan(project_id, db)
        briefs = plan.get("scene_briefs", {})

        if ch_id not in briefs:
            return {"error": f"第 {chapter_number} 章无场景简报"}

        del briefs[ch_id]
        await self._plan_service.update_layer(
            project_id, "scene_briefs", briefs, db
        )
        return {"discarded": ch_id}

    async def get_expired_briefs(
        self, project_id: uuid.UUID, db: AsyncSession
    ) -> list[dict]:
        plan = await self._plan_service.get_story_plan(project_id, db)
        briefs = plan.get("scene_briefs", {})
        expired = []

        for ch_id, brief in briefs.items():
            if brief.get("source") == "user_edited":
                continue
            expires_when = brief.get("expires_when", "")
            if expires_when:
                expired.append({
                    "chapter_id": ch_id,
                    "chapter_number": self._ch_id_to_number(ch_id),
                    "expires_when": expires_when,
                    "source": brief.get("source", ""),
                    "version": brief.get("version", 0),
                })

        return expired

    async def generate_brief_for_chapter(
        self,
        project_id: uuid.UUID,
        chapter_number: int,
        db: AsyncSession,
        llm_client=None,
    ) -> dict:
        spine_service = None
        from app.services.chapter_spine_service import ChapterSpineService
        spine_service = ChapterSpineService()

        chapter = await spine_service.get_chapter(project_id, chapter_number, db)
        if not chapter:
            return {"error": f"第 {chapter_number} 章不存在于章节脊柱"}

        conflict_text = self._plan_service.get_conflict_text(chapter)
        budget = self._budget_service.calculate(chapter)
        reasons = "；".join(budget["scene_budget_reasons"]) or "常规章节密度"
        prompt = f"""为第{chapter_number}章生成场景简报。
本章建议生成 {budget["recommended_scene_count"]} 个场景，最低保障为 {budget["minimum_scene_count"]} 个，最多建议 {self._budget_service.MAX_RECOMMENDED_SCENE_COUNT} 个。
原因：{reasons}

章节标题：{chapter.get('title', '')}
核心冲突：{conflict_text}
价值转变：{chapter.get('value_shift', '')}
POV角色：{chapter.get('pov_character', '')}
钩子：{chapter.get('hook', '')}

请输出JSON格式的场景数组，每个场景包含：
- scene_id: 场景ID（如 ch_{chapter_number:03d}_s1）
- type: 场景类型（dialogue/action/discovery/reflection/transition）
- beat_role: 结构职责（setup/pressure/decision/turning_point/hook）
- goal: 场景目标
- conflict: 场景冲突
- outcome: 场景结果
- info_release: 释放的信息
- hook: 小钩子
- required_context_refs: 需要引用的上下文（角色/规则/伏笔ID列表）

只输出JSON数组，不要其他文字。"""

        if llm_client:
            try:
                response = await llm_client.generate(
                    system_prompt="你是场景简报生成器，只输出JSON。",
                    user_prompt=prompt,
                    temperature=0.7,
                    task_type=LLMTaskType.SHORT_EXTRACTION,
                )
                import json
                import re

                response_text = response
                match = re.search(r"```json\s*(.*?)\s*```", response, re.S)
                if match:
                    response_text = match.group(1)
                scenes = json.loads(response_text)
                if isinstance(scenes, list):
                    brief_data = {
                        "chapter_id": f"ch_{chapter_number:03d}",
                        "version": 1,
                        "source": "generated",
                        "expires_when": "",
                        "expires_scope": "current",
                        "scenes": scenes,
                    }
                    return await self.save_scene_brief(
                        project_id, chapter_number, brief_data, db
                    )
            except Exception as e:
                logger.warning(f"[SceneBrief] LLM generation failed: {e}")
        return {
            "chapter_id": f"ch_{chapter_number:03d}",
            "status": "prompt_ready",
            "prompt": prompt,
        }

    def _ch_id_to_number(self, ch_id: str) -> int:
        try:
            return int(ch_id.replace("ch_", "").lstrip("0") or "0")
        except (ValueError, AttributeError):
            return 0
