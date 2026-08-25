import json

from app.services.chapter_summary_service import ChapterSummaryService
from app.services.forward_constraint_builder import ForwardConstraintBuilder
from app.services.memory_core import CoreMemoryService
from app.skills.core_query import CoreQuerySkill
from app.skills.shell_search import ShellSearchSkill
from app.skills.state_query import StateQuerySkill
from app.skills.attention_director import AttentionDirectorSkill
from app.engines.fcip_engine import FCIPEngine
from app.services.foreshadowing_service import ForeshadowingService
from app.services.text_coercion import to_entity_name, to_search_text, to_text


class ScenePreparationSkill:
    def __init__(self):
        self.core_query = CoreQuerySkill()
        self.shell_search = ShellSearchSkill()
        self.state_query = StateQuerySkill()
        self.attention_director = AttentionDirectorSkill()
        self.core_memory = CoreMemoryService()

    async def prepare(
        self,
        project_id: str,
        scene_beat: dict,
        story_state: dict | None = None,
        pov_character: str | None = None,
        chapter_number: int | None = None,
        db=None,
    ) -> dict:
        if story_state is None:
            story_state = await self.state_query.get_snapshot(project_id)

        pov = pov_character or story_state.get("pov_character")

        character_cards, character_resolution_trace = await self._gather_character_cards(
            project_id,
            scene_beat,
            pov_character=pov,
        )
        location_cards = await self._gather_location_cards(project_id, scene_beat)
        historical_details = await self._gather_historical_details(project_id, scene_beat)
        raw_foreshadowing_instructions = await self._gather_foreshadowing_instructions(project_id, story_state)
        foreshadowing_instructions = await self._compile_foreshadowing_instructions(
            project_id,
            story_state,
            raw_foreshadowing_instructions,
            pov,
            chapter_number,
            db,
        )
        style_context = await self._gather_style_context(project_id, scene_beat)

        # 加载 Genre Profile
        genre_profile = None
        project_obj = None
        try:
            from app.services.genre_profile_service import GenreProfileService
            genre_svc = GenreProfileService()
            core_data = {}
            if db:
                try:
                    from app.db.db_models import Project
                    from uuid import UUID as UUIDType
                    pid = project_id
                    if isinstance(pid, str):
                        pid = UUIDType(pid)
                    project = await db.get(Project, pid)
                    project_obj = project
                    if project:
                        core_data = project.core_data or {}
                except Exception:
                    pass
            genre_profile = await genre_svc.get_project_genre_profile(
                project_id, db=db, core_data=core_data,
            )
        except Exception:
            pass

        writing_mode_profile = {}
        quality_memory = {}
        try:
            from app.services.generation_feature_policy import resolve_generation_feature_policy
            from app.services.quality_memory_service import QualityMemoryService
            from app.services.writing_mode_profile_service import WritingModeProfileService

            feature_policy = resolve_generation_feature_policy(project_obj) if project_obj else None
            profile = WritingModeProfileService().get_project_profile(project_obj, feature_policy)
            writing_mode_profile = profile.model_dump()
            quality_memory = QualityMemoryService().get_project_quality_memory(project_obj)
        except Exception:
            writing_mode_profile = {}
            quality_memory = {}

        chapter_summaries = ""
        if chapter_number:
            summary_service = ChapterSummaryService()
            try:
                chapter_summaries = await summary_service.get_context_window(project_id, chapter_number, db)
            except Exception:
                chapter_summaries = ""

        forward_constraints = ""
        if chapter_number:
            constraint_builder = ForwardConstraintBuilder()
            try:
                forward_constraints = await constraint_builder.build_all_constraints(project_id, chapter_number, db)
            except Exception:
                forward_constraints = ""

        relevant_rules = {}
        if chapter_number:
            relevant_rules = await self._gather_relevant_rules(project_id, scene_beat, genre_profile)

        style_prompt_payload = style_context if style_context.get("profile") else None

        focus_prompt = await self.attention_director.build_prompt(
            project_id=project_id,
            scene_beat=scene_beat,
            pov_character=pov,
            foreshadowing_instructions=foreshadowing_instructions,
            style_context=style_prompt_payload,
            chapter_summaries=chapter_summaries,
            forward_constraints=forward_constraints,
            relevant_rules=relevant_rules,
            genre_profile=genre_profile,
        )

        return {
            # Put compact, generation-critical style fields first.  Generic
            # Skill output budgets preserve the leading portion of service
            # results, while character/history payloads can be very large.
            "style_prompt": style_context.get("style_prompt"),
            "style_embedding": style_context.get("style_embedding", {}),
            "persona_card": style_context.get("persona_card", {}),
            "style_sample_passages": style_context.get("style_sample_passages", []),
            "style_statistics": style_context.get("style_statistics", {}),
            "evolution_report": style_context.get("evolution_report", {}),
            "style_profile": style_context.get("profile"),
            "character_cards": character_cards,
            "character_resolution_trace": character_resolution_trace,
            "location_cards": location_cards,
            "historical_details": historical_details,
            "foreshadowing_instructions": foreshadowing_instructions,
            "raw_foreshadowing_instructions": raw_foreshadowing_instructions,
            "focus_prompt": focus_prompt,
            "story_state_snapshot": story_state,
            "pov_character": pov,
            "chapter_summaries": chapter_summaries,
            "forward_constraints": forward_constraints,
            "relevant_rules": relevant_rules,
            "writing_mode_profile": writing_mode_profile,
            "quality_memory": quality_memory,
        }

    async def _gather_character_cards(
        self,
        project_id: str,
        scene_beat: dict,
        *,
        pov_character: str | None = None,
    ) -> tuple[list[dict], list[dict]]:
        characters = []
        trace = []
        mentioned = scene_beat.get("characters", [])
        if not isinstance(mentioned, list):
            mentioned = [mentioned]
        if pov_character:
            mentioned = [pov_character, *mentioned]

        seen_ids: set[str] = set()
        seen_queries: set[str] = set()
        for char_ref in mentioned:
            char_ref = to_entity_name(char_ref)
            if not char_ref or char_ref in seen_queries:
                continue
            seen_queries.add(char_ref)
            resolved = await self.core_query.resolve_entity_card(
                project_id,
                char_ref,
                expected_type="character",
            )
            if not resolved:
                trace.append({"query": char_ref, "status": "unresolved"})
                continue
            card = resolved["card"]
            card_id = str(card.get("id") or resolved.get("canonical_name") or char_ref)
            if card_id not in seen_ids:
                seen_ids.add(card_id)
                characters.append(card)
            trace.append({
                "query": char_ref,
                "status": "resolved",
                "entity_id": card.get("id"),
                "canonical_name": resolved.get("canonical_name"),
                "matched_by": resolved.get("matched_by"),
            })

        return characters, trace

    async def _gather_location_cards(self, project_id: str, scene_beat: dict) -> list[dict]:
        locations = []
        location_name = to_entity_name(scene_beat.get("location", ""))

        if location_name:
            resolved = await self.core_query.resolve_entity_card(
                project_id,
                location_name,
                expected_type="location",
            )
            if resolved:
                locations.append(resolved["card"])

        return locations

    async def _gather_historical_details(self, project_id: str, scene_beat: dict) -> list[dict]:
        return await self.shell_search.search_relevant_details(
            project_id=project_id,
            scene_context={"scene_beat": scene_beat},
            limit=8,
        )

    async def _gather_foreshadowing_instructions(self, project_id: str, story_state: dict) -> list[dict]:
        chapter = story_state.get("active_chapter")
        scene = story_state.get("active_scene")
        if chapter is None or scene is None:
            return []
        return await self.core_memory.get_active_foreshadowing_for_scene(project_id, chapter, scene)

    async def _compile_foreshadowing_instructions(
        self,
        project_id: str,
        story_state: dict,
        raw_instructions: list[dict],
        pov_character: str | None,
        chapter_number: int | None,
        db,
    ) -> list[dict]:
        chapter = chapter_number or story_state.get("active_chapter")
        scene = story_state.get("active_scene") or 0
        if not db or not chapter:
            return raw_instructions

        try:
            service = ForeshadowingService()
            fcip = FCIPEngine(service)
            ctx = await fcip.build_context(
                project_id=project_id,
                chapter_number=int(chapter),
                db=db,
                pov_character=pov_character,
                scene_index=int(scene or 0),
            )
            compiled = fcip.compile_for_scene(ctx, raw_instructions)
            return compiled or raw_instructions
        except Exception:
            return raw_instructions

    async def _gather_style_context(self, project_id: str, scene_beat: dict) -> dict:
        profile = await self.core_memory.get_active_style_profile(project_id)
        if not profile:
            return {
                "profile": None,
                "style_prompt": None,
                "style_sample_passages": [],
                "style_embedding": {},
                "persona_card": {},
                "style_statistics": {},
                "evolution_report": {},
            }

        style_prompt = profile.get("style_prompt", "") or None
        all_passages = profile.get("sample_passages", [])

        scene_type = to_search_text(scene_beat.get("type", ""))
        category_map = {
            "action": "action",
            "dialogue": "dialogue",
            "description": "description",
            "emotion": "emotion",
        }
        target_category = category_map.get(scene_type, "")

        matched = []
        other = []
        for p in all_passages:
            if p.get("category") == target_category:
                matched.append(p)
            else:
                other.append(p)

        selected = matched[:3]
        if len(selected) < 3:
            selected.extend(other[:3 - len(selected)])

        return {
            "profile": profile,
            "style_prompt": style_prompt,
            "style_sample_passages": selected,
            "style_embedding": profile.get("style_embedding", {}),
            "persona_card": profile.get("persona_card", {}),
            "style_statistics": profile.get("style_statistics", {}),
            "evolution_report": profile.get("evolution_report", {}),
        }

    async def _gather_relevant_rules(
        self, project_id: str, scene_beat: dict, genre_profile: dict | None = None
    ) -> dict:
        all_rules = await self.core_memory.list_world_rules(project_id)

        pov_character = scene_beat.get("pov_character", scene_beat.get("pov", ""))
        involved_abilities = self._extract_abilities(scene_beat, genre_profile)
        involved_locations = self._extract_locations(scene_beat, genre_profile)

        critical_index = [
            {"name": to_text(r.get("name", "")), "core": to_text(r.get("description", ""))[:50]}
            for r in all_rules if r.get("priority") == "critical"
        ]

        relevant_details = self._filter_rules_by_relevance(
            all_rules, pov_character, involved_abilities, involved_locations
        )

        return {
            "critical_index": critical_index,
            "relevant_details": relevant_details[:10],
        }

    def _extract_abilities(self, scene_beat: dict, genre_profile: dict | None = None) -> list[str]:
        abilities = []
        conflict = to_search_text(scene_beat.get("conflict", ""))
        outcome = to_search_text(scene_beat.get("outcome", ""))
        combined = conflict + outcome

        # 优先从 Genre Profile 的 category_keywords 读取
        if genre_profile:
            from app.services.genre_profile_service import GenreProfileService
            genre_svc = GenreProfileService()
            category_keywords = genre_svc.get_category_keywords(genre_profile)
            if category_keywords:
                for category, keywords in category_keywords.items():
                    if isinstance(keywords, list):
                        for kw in keywords:
                            if kw in combined and category not in abilities:
                                abilities.append(category)
                if abilities:
                    return abilities
            return abilities

        # Legacy fallback: 仅在没有 Genre Profile 时使用旧关键词映射
        ability_keywords = {
            "战斗": "combat", "法术": "magic", "修炼": "magic",
            "灵力": "magic", "武功": "combat", "科技": "technology",
            "魔法": "magic", "力量": "magic", "技能": "combat",
        }
        for cn, cat in ability_keywords.items():
            if cn in combined and cat not in abilities:
                abilities.append(cat)
        return abilities

    def _extract_locations(self, scene_beat: dict, genre_profile: dict | None = None) -> list[str]:
        locations = []

        def add_location(value):
            if isinstance(value, (list, tuple, set)):
                for item in value:
                    add_location(item)
                return
            if isinstance(value, dict):
                for key in ("name", "location", "crime_scene", "place", "site", "value"):
                    candidate = value.get(key)
                    if candidate:
                        add_location(candidate)
                        return
                for candidate in value.values():
                    add_location(candidate)
                return
            location_name = to_entity_name(value)
            if location_name and location_name not in locations:
                locations.append(location_name)

        # 根据 Genre Profile 的 location_schema 决定提取哪些字段
        if genre_profile:
            from app.services.genre_profile_service import GenreProfileService
            genre_svc = GenreProfileService()
            schema = genre_svc.get_location_schema(genre_profile)
            fields = schema.get("fields", ["location"])
            for field in fields:
                add_location(scene_beat.get(field, ""))
            if locations:
                return locations

        # Fallback: 单地点模式
        add_location(scene_beat.get("location", ""))
        return locations

    def _filter_rules_by_relevance(self, all_rules, pov_character, involved_abilities, involved_locations) -> list[dict]:
        relevant = []
        for rule in all_rules:
            rule_data = rule if isinstance(rule, dict) else (rule.model_dump() if hasattr(rule, "model_dump") else {})
            category = to_search_text(to_entity_name(rule_data.get("category", "")))
            name = to_search_text(rule_data.get("name", ""))
            description = to_search_text(rule_data.get("description", ""))

            if category in involved_abilities:
                relevant.append(rule_data)
                continue
            for loc in involved_locations:
                location_text = to_search_text(to_entity_name(loc))
                if location_text in description or location_text in name:
                    relevant.append(rule_data)
                    break
        return relevant
