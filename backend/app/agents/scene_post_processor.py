import hashlib
import json
import logging

from app.agents.base import BaseAgent
from app.services.structured_output_parser import StructuredOutputParser, StructuredOutputParseError
from app.services.state_delta_reducer import StateDeltaReducer
from app.services.foreshadowing_service import ForeshadowingService
from app.services.foreshadowing_clue_service import ForeshadowingClueService
from app.services.llm_task_profiles import LLMTaskType

logger = logging.getLogger(__name__)

_UNIFIED_SYSTEM_PROMPT = (
    "你是一位叙事文本后处理专家。你的任务是同时完成两项分析：\n"
    "\n"
    "1. **细节种子提取**：从文本中提取所有可被后续章节引用的细节，包括：\n"
    "   人物状态变化、物品出现/消失、地点描述、时间线索、情感变化等。\n"
    "   每个细节种子包含：entity_id（实体标识）、fact（事实描述）、\n"
    "   narrative_time（叙事时间，如有）、tier（重要度 T1/T2/T3）。\n"
    "\n"
    "2. **状态变更提取**：分析文本中隐含的所有状态变更，输出状态补丁。\n"
    "   状态补丁应包含：\n"
    "   - objective_state: 客观世界状态变更\n"
    "   - subjective_views: 角色主观认知变更\n"
    "   - narrative_time: 叙事时间推进\n"
    "   - active_scene: 当前场景号变更\n"
    "   - completed_events: 本场景中已完成的事件列表\n"
    "   - active_constraints: 当前仍然有效的约束列表\n"
    "   - item_states: 本场景中物品状态变更列表（方案 3 B5 升级）\n"
    "\n"
    "objective_state 必须使用固定结构：\n"
    "{\"角色ID\": {\"location\": \"地点\", \"emotional_state\": \"情绪\", "
    "\"physical_state\": \"身体状态\", \"inventory\": [\"物品\"], \"alive\": true, \"extra\": {}}}。\n"
    "不要在角色对象中自创字段；额外信息放入 extra。不要添加 entities 包装层。\n"
    "narrative_time 必须是简短字符串，不要输出对象。\n"
    "active_constraints 中每个约束为 {\"description\": \"约束描述\", \"source_scene\": \"场景ID\", \"expires\": \"失效条件\"}，"
    "新约束追加，已有约束中满足 expires 条件的应移除。\n"
    # 方案 3 B5：item_states 结构化物品状态变更（替代纯字符串 inventory）
    "item_states 中每个元素为 {\"name\": \"物品名\", \"state\": \"intact/damaged/lost/transformed\", "
    "\"owner\": \"当前持有者\", \"changes\": \"变更说明\"}，"
    "只输出文本中明确发生状态变化的物品，不要输出未变化的物品。\n"
    "\n"
    "输出JSON格式：{\"detail_seeds\": [...], \"state_patch\": {...}}"
)

# Token 优化：system_prompt 固定不变，预先计算 stable_prefix_hash 并构造 cache_policy。
# - OpenAI 兼容路径：自动缓存，stable_prefix_hash 用于本地 cache_hit_rate 推导
# - Anthropic 路径：cache_policy 注入 cache_control breakpoint，享受显式缓存
# ScenePostProcessor 在每个场景生成后调用一次，N 个场景即 N 次相同 system_prompt，
# 缓存命中后 Input Miss 部分（system_prompt tokens）转为 Input Hit，节省约 30% tokens。
_UNIFIED_SYSTEM_PROMPT_HASH = hashlib.sha256(_UNIFIED_SYSTEM_PROMPT.encode("utf-8")).hexdigest()


def _build_post_processor_cache_policy():
    """延迟构造 PromptCachePolicy，避免模块加载时导入 llm_client。"""
    from app.services.llm_client import PromptCachePolicy
    return PromptCachePolicy(
        enabled=True,
        provider="openai_compatible",
        stable_prefix_blocks=[_UNIFIED_SYSTEM_PROMPT],
        dynamic_blocks=[],
        cache_hint_strategy="provider_default",
    )


class ScenePostProcessor(BaseAgent):
    name = "scene_post_processor"

    async def execute(self, context: dict) -> dict:
        generated_text = context.get("generated_text", "")
        current_state = context.get("current_state", {})
        project_id = context.get("project_id")
        chapter_number = context.get("chapter_number", 0)
        scene_index = context.get("scene_index", 0)
        db = context.get("db")

        user_prompt = (
            f"当前状态：\n{json.dumps(current_state, ensure_ascii=False, indent=2)}\n\n"
            f"新生成的文本：\n{generated_text}\n\n"
            "请同时提取细节种子和状态补丁："
        )

        llm = await self.get_llm_client()
        # Token 优化：使用 generate_structured + cache_policy，让固定 system_prompt 享受缓存。
        result = await llm.generate_structured(
            system_prompt=_UNIFIED_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            temperature=0.3,
            max_tokens=2048,
            task_type=LLMTaskType.SHORT_EXTRACTION,
            stable_prefix_hash=_UNIFIED_SYSTEM_PROMPT_HASH,
            cache_policy=_build_post_processor_cache_policy(),
        )
        response = result.content or ""

        parse_error = ""
        parse_repairs: list[str] = []
        detail_seeds: list[dict] = []
        state_patch: dict = {}

        try:
            parsed = StructuredOutputParser.parse(response, require_object=True)
            data = parsed.data if isinstance(parsed.data, dict) else {}
            parse_repairs = parsed.repairs_applied
            detail_seeds = data.get("detail_seeds") or []
            if not isinstance(detail_seeds, list):
                detail_seeds = []
            state_patch = data.get("state_patch") or {}
            if not isinstance(state_patch, dict):
                state_patch = {}
        except StructuredOutputParseError as exc:
            parse_error = f"{exc.error_type}: {exc}"

        # --- Process detail seeds: write foreshadowing clues ---
        foreshadowing_clues = await self._write_foreshadowing_clues(
            detail_seeds, generated_text, project_id, chapter_number, db,
        )

        # --- Process state patch: run through StateDeltaReducer ---
        reducer = StateDeltaReducer()
        delta = reducer.from_legacy_patch(
            state_patch,
            project_id=str(project_id or ""),
            chapter_number=int(chapter_number or 0),
            scene_index=int(scene_index or 0),
        )
        reduced_patch = reducer.reduce(
            current_state if isinstance(current_state, dict) else {},
            delta,
        )

        # 方案 3 B5：将 item_states 写入 Core 层物品卡（结构化物品状态变更）
        item_state_writes = await self._write_item_states(
            state_patch, project_id,
        )

        # --- Build combined result ---
        result: dict = {
            "detail_seeds": detail_seeds,
            "state_patch": reduced_patch,
            "state_delta": delta.model_dump(),
            "parse_error": parse_error,
            "parse_repairs": parse_repairs,
        }

        if foreshadowing_clues:
            result["foreshadowing_clues_written"] = len(foreshadowing_clues)
            result["foreshadowing_clues"] = foreshadowing_clues

        if item_state_writes:
            result["item_states_written"] = len(item_state_writes)
            result["item_states"] = item_state_writes

        return result

    async def _write_item_states(
        self,
        state_patch: dict,
        project_id,
    ) -> list[dict]:
        """方案 3 B5：将 item_states 写入 Core 层物品卡。

        item_states 结构：[{"name": "物品名", "state": "intact/damaged/lost/transformed",
                          "owner": "持有者", "changes": "变更说明"}]
        只更新已存在的物品卡；不存在的不自动创建（避免误识别）。
        """
        if not project_id or not state_patch:
            return []

        item_states = state_patch.get("item_states") or []
        if not isinstance(item_states, list) or not item_states:
            return []

        from app.services.memory_core import CoreMemoryService

        core_service = CoreMemoryService()
        written: list[dict] = []

        for item_change in item_states:
            if not isinstance(item_change, dict):
                continue
            item_name = item_change.get("name", "")
            new_state = item_change.get("state", "")
            if not item_name or not new_state:
                continue

            try:
                owner = item_change.get("owner")
                updated = await core_service.update_item_state(
                    project_id=str(project_id),
                    item_name=item_name,
                    new_status=new_state,
                    owner=owner if owner else None,
                )
                if updated:
                    written.append({
                        "name": item_name,
                        "state": new_state,
                        "owner": owner or updated.get("owner", ""),
                        "changes": item_change.get("changes", ""),
                        "item_id": updated.get("id"),
                    })
            except Exception as e:
                logger.debug(
                    "[ScenePostProcessor] update_item_state failed for %s: %s",
                    item_name, e,
                )

        return written

    async def _write_foreshadowing_clues(
        self,
        detail_seeds: list[dict],
        generated_text: str,
        project_id,
        chapter_number: int,
        db,
    ) -> list[dict]:
        if not project_id or not db or not chapter_number:
            return []

        fs_service = ForeshadowingService()
        clue_service = ForeshadowingClueService()

        try:
            actionable = await fs_service.list_actionable_for_scene(
                project_id, chapter_number, db,
            )
        except Exception as e:
            logger.debug("[ScenePostProcessor] list_actionable failed: %s", e)
            return []

        if not actionable:
            return []

        written = []
        for item in actionable:
            item_name = item.get("name", "")
            item_id = item.get("id")
            if not item_id:
                continue

            matching_seeds = []
            for seed in detail_seeds:
                fact = seed.get("fact", "")
                entity = seed.get("entity_id", "")
                if fact and (item_name in fact or item_name in entity):
                    matching_seeds.append(seed)

            if not matching_seeds:
                continue

            for seed in matching_seeds[:3]:
                try:
                    clue = await clue_service.add_clue(
                        project_id=project_id,
                        foreshadowing_line_id=item_id,
                        clue_type="supportive",
                        chapter_number=chapter_number,
                        clue_text=seed.get("fact", ""),
                        db=db,
                        pov_character=seed.get("entity_id", ""),
                        salience_at_time="subtle" if seed.get("tier") == "T3" else "moderate",
                    )
                    written.append({
                        "foreshadowing_name": item_name,
                        "clue_type": "supportive",
                        "fact": seed.get("fact", "")[:50],
                    })
                except Exception as e:
                    logger.debug("[ScenePostProcessor] add_clue failed: %s", e)

        return written
