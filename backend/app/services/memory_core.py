import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.db.db_models import async_session, Project
from app.models.character import CharacterCreate, CharacterResponse
from app.services.core_entity_resolver import CoreEntityResolver


class CoreMemoryService:
    async def _get_project(self, project_id) -> Project | None:
        pid = project_id if isinstance(project_id, uuid.UUID) else uuid.UUID(str(project_id))
        async with async_session() as session:
            result = await session.execute(
                select(Project).where(Project.id == pid)
            )
            return result.scalar_one_or_none()

    async def _save_project(self, project: Project) -> None:
        async with async_session() as session:
            db_project = await session.get(Project, project.id)
            if db_project:
                db_project.core_data = project.core_data
                flag_modified(db_project, "core_data")
                db_project.core_version = (db_project.core_version or 0) + 1
                db_project.updated_at = datetime.now(timezone.utc)
                await session.commit()

    def _get_characters_from_core(self, core_data: dict) -> list[dict]:
        return core_data.get("characters", [])

    def _set_characters_to_core(self, core_data: dict, characters: list[dict]) -> dict:
        core_data = dict(core_data)
        core_data["characters"] = characters
        return core_data

    async def get_character(self, project_id: str, character_id: str) -> CharacterResponse | None:
        """Resolve a character by stable ID, canonical name, or alias."""
        project = await self._get_project(project_id)
        if not project:
            return None
        characters = self._get_characters_from_core(project.core_data or {})
        resolved = CoreEntityResolver.resolve_cards(
            characters,
            character_id,
            entity_type="character",
        )
        if resolved is None:
            return None
        char = dict(resolved.card)
        char.setdefault("project_id", project_id)
        char.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        char.setdefault("updated_at", datetime.now(timezone.utc).isoformat())
        try:
            return CharacterResponse(**char)
        except Exception:
            return None

    async def list_characters(self, project_id: str) -> list[CharacterResponse]:
        project = await self._get_project(project_id)
        if not project:
            return []
        characters = self._get_characters_from_core(project.core_data or {})
        result = []
        for char in characters:
            if "id" not in char:
                char["id"] = str(uuid.uuid4())
            if "project_id" not in char:
                char["project_id"] = project_id
            if "created_at" not in char:
                char["created_at"] = datetime.now(timezone.utc).isoformat()
            if "updated_at" not in char:
                char["updated_at"] = datetime.now(timezone.utc).isoformat()
            try:
                result.append(CharacterResponse(**char))
            except Exception:
                pass
        return result

    async def create_character(self, project_id: str, data: CharacterCreate) -> CharacterResponse:
        project = await self._get_project(project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")

        now = datetime.now(timezone.utc).isoformat()
        char_id = str(uuid.uuid4())
        char_data = data.model_dump()
        char_data["id"] = char_id
        char_data["project_id"] = project_id
        char_data["created_at"] = now
        char_data["updated_at"] = now

        characters = self._get_characters_from_core(project.core_data or {})
        characters.append(char_data)

        project.core_data = self._set_characters_to_core(project.core_data or {}, characters)
        await self._save_project(project)

        return CharacterResponse(**char_data)

    async def update_character(self, project_id: str, character_id: str, data: dict) -> CharacterResponse:
        project = await self._get_project(project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")

        characters = self._get_characters_from_core(project.core_data or {})
        found = None
        for i, char in enumerate(characters):
            if char.get("id") == character_id:
                validation = await self.validate_core_change(project_id, "character_update", data)
                if not validation.get("valid") and validation.get("conflicts"):
                    locked_fields = data.keys() & {"name", "personality", "desire", "deep_need"}
                    if locked_fields:
                        for conflict in validation["conflicts"]:
                            if isinstance(conflict, dict) and conflict.get("fact"):
                                pass
                merged = dict(char)
                merged.update(data)
                normalized = CharacterCreate(**merged).model_dump()
                for preserved in ("id", "project_id", "created_at"):
                    if preserved in char:
                        normalized[preserved] = char[preserved]
                char = normalized
                char["updated_at"] = datetime.now(timezone.utc).isoformat()
                characters[i] = char
                found = char
                break

        if found is None:
            raise ValueError(f"Character {character_id} not found")

        project.core_data = self._set_characters_to_core(project.core_data or {}, characters)
        await self._save_project(project)

        return CharacterResponse(**found)

    async def get_location(self, project_id: str, location_id: str) -> dict | None:
        """Resolve a location by stable ID, canonical name, or alias."""
        project = await self._get_project(project_id)
        if not project:
            return None
        locations = (project.core_data or {}).get("locations", [])
        resolved = CoreEntityResolver.resolve_cards(locations, location_id, entity_type="location")
        return dict(resolved.card) if resolved else None

    async def list_locations(self, project_id: str) -> list[dict]:
        project = await self._get_project(project_id)
        if not project:
            return []
        return (project.core_data or {}).get("locations", [])

    async def update_location(self, project_id: str, location_id: str, data: dict) -> dict | None:
        """按 ID 更新地点（与 update_character / update_item 路径一致）。"""
        project = await self._get_project(project_id)
        if not project:
            return None

        locations = (project.core_data or {}).get("locations", [])
        found = None
        for i, loc in enumerate(locations):
            if loc.get("id") == location_id:
                loc.update(data)
                loc["updated_at"] = datetime.now(timezone.utc).isoformat()
                locations[i] = loc
                found = loc
                break

        if found is None:
            return None

        cd = dict(project.core_data or {})
        cd["locations"] = locations
        project.core_data = cd
        await self._save_project(project)

        return found

    # ----------------------------------------------------- 物品卡（方案 3 B2）

    def _get_items_from_core(self, core_data: dict) -> list[dict]:
        return core_data.get("items", [])

    def _set_items_to_core(self, core_data: dict, items: list[dict]) -> dict:
        core_data = dict(core_data)
        core_data["items"] = items
        return core_data

    async def list_items(self, project_id: str) -> list[dict]:
        """获取物品卡列表（方案 3 B2）。"""
        project = await self._get_project(project_id)
        if not project:
            return []
        return self._get_items_from_core(project.core_data or {})

    async def get_item(self, project_id: str, item_id: str) -> dict | None:
        """Resolve an item by stable ID, canonical name, or alias."""
        items = await self.list_items(project_id)
        resolved = CoreEntityResolver.resolve_cards(items, item_id, entity_type="item")
        return dict(resolved.card) if resolved else None

    async def get_item_by_name(self, project_id: str, name: str) -> dict | None:
        """按名称查找物品卡（含别名匹配）。"""
        return await self.get_item(project_id, name)

    async def create_item(self, project_id: str, data: dict) -> dict:
        """创建物品卡（方案 3 B2）。"""
        project = await self._get_project(project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")

        item_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        item = {
            "id": item_id,
            "name": data.get("name", ""),
            "aliases": data.get("aliases", []),
            "appearance": data.get("appearance", ""),
            "function": data.get("function", ""),
            "origin": data.get("origin", ""),
            "owner": data.get("owner", ""),
            "status": data.get("status", "intact"),
            "description": data.get("description", ""),
            "importance": data.get("importance", "minor"),
            "first_appear_chapter": data.get("first_appear_chapter"),
            "related_foreshadowing": data.get("related_foreshadowing", ""),
            "created_at": now,
            "updated_at": now,
        }

        items = self._get_items_from_core(project.core_data or {})
        items.append(item)

        project.core_data = self._set_items_to_core(project.core_data or {}, items)
        await self._save_project(project)

        return item

    async def update_item(self, project_id: str, item_id: str, data: dict) -> dict | None:
        """更新物品卡（方案 3 B2）。"""
        project = await self._get_project(project_id)
        if not project:
            return None

        items = self._get_items_from_core(project.core_data or {})
        found = None
        for i, it in enumerate(items):
            if it.get("id") == item_id:
                it.update(data)
                it["updated_at"] = datetime.now(timezone.utc).isoformat()
                items[i] = it
                found = it
                break

        if found is None:
            return None

        project.core_data = self._set_items_to_core(project.core_data or {}, items)
        await self._save_project(project)

        return found

    async def delete_item(self, project_id: str, item_id: str) -> bool:
        """删除物品卡（方案 3 B2）。"""
        project = await self._get_project(project_id)
        if not project:
            return False

        items = self._get_items_from_core(project.core_data or {})
        new_items = [it for it in items if it.get("id") != item_id]

        if len(new_items) == len(items):
            return False

        project.core_data = self._set_items_to_core(project.core_data or {}, new_items)
        await self._save_project(project)

        return True

    async def update_item_state(
        self, project_id: str, item_name: str, new_status: str, owner: str | None = None
    ) -> dict | None:
        """按名称更新物品状态（与方案 9 事实填写单的 item_states 联动）。"""
        item = await self.get_item_by_name(project_id, item_name)
        if not item:
            return None
        update_data = {"status": new_status}
        if owner is not None:
            update_data["owner"] = owner
        return await self.update_item(project_id, item["id"], update_data)

    # ---- with_db 变体：用于 worldview_projection_service 自动提升时共享事务 ----

    async def create_item_with_db(self, db, project_id: str, data: dict) -> dict:
        """方案 3 B2：在指定 db 会话中创建物品卡（用于自动提升）。"""
        project = await self._get_project_with_db(db, project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")

        item_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        item = {
            "id": item_id,
            "name": data.get("name", ""),
            "aliases": data.get("aliases", []),
            "appearance": data.get("appearance", ""),
            "function": data.get("function", ""),
            "origin": data.get("origin", ""),
            "owner": data.get("owner", ""),
            "status": data.get("status", "intact"),
            "description": data.get("description", ""),
            "importance": data.get("importance", "minor"),
            "first_appear_chapter": data.get("first_appear_chapter"),
            "related_foreshadowing": data.get("related_foreshadowing", ""),
            "created_at": now,
            "updated_at": now,
        }

        items = self._get_items_from_core(project.core_data or {})
        items.append(item)

        project.core_data = self._set_items_to_core(project.core_data or {}, items)
        await self._save_project_with_db(db, project)

        return item

    async def update_item_with_db(
        self, db, project_id: str, item_id: str, data: dict
    ) -> dict | None:
        """方案 3 B2：在指定 db 会话中更新物品卡（用于自动提升）。"""
        project = await self._get_project_with_db(db, project_id)
        if not project:
            return None

        items = self._get_items_from_core(project.core_data or {})
        found = None
        for i, it in enumerate(items):
            if it.get("id") == item_id:
                it.update(data)
                it["updated_at"] = datetime.now(timezone.utc).isoformat()
                items[i] = it
                found = it
                break

        if found is None:
            return None

        project.core_data = self._set_items_to_core(project.core_data or {}, items)
        await self._save_project_with_db(db, project)

        return found

    async def get_item_by_name_with_db(
        self, db, project_id: str, name: str
    ) -> dict | None:
        """方案 3 B2：在指定 db 会话中按名称查找物品卡（含别名匹配）。"""
        project = await self._get_project_with_db(db, project_id)
        if not project:
            return None
        items = self._get_items_from_core(project.core_data or {})
        resolved = CoreEntityResolver.resolve_cards(items, name, entity_type="item")
        return dict(resolved.card) if resolved else None

    async def resolve_entity_with_db(
        self,
        db,
        project_id: str,
        query: str,
        *,
        expected_type: str | None = None,
    ) -> dict | None:
        """Transaction-safe unified resolver used by projection pipelines."""
        project = await self._get_project_with_db(db, project_id)
        if not project:
            return None
        resolved = CoreEntityResolver.resolve_core_data(
            project.core_data or {},
            query,
            expected_type=expected_type,
        )
        return resolved.to_dict() if resolved else None

    def _get_world_rules_from_core(self, core_data: dict) -> list[dict]:
        return core_data.get("world_rules", [])

    def _set_world_rules_to_core(self, core_data: dict, rules: list[dict]) -> dict:
        core_data = dict(core_data)
        core_data["world_rules"] = rules
        return core_data

    async def list_world_rules(self, project_id: str) -> list[dict]:
        project = await self._get_project(project_id)
        if not project:
            return []
        return self._get_world_rules_from_core(project.core_data or {})

    async def get_world_rule(self, project_id: str, rule_id: str) -> dict | None:
        rules = await self.list_world_rules(project_id)
        for rule in rules:
            if rule.get("id") == rule_id:
                return rule
        return None

    async def create_world_rule(self, project_id: str, data: dict) -> dict:
        project = await self._get_project(project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")

        rule_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        rule = {
            "id": rule_id,
            "category": data.get("category", "general"),
            "name": data.get("name", ""),
            "description": data.get("description", ""),
            "constraints": data.get("constraints", []),
            "priority": data.get("priority", "normal"),
            "locked": data.get("locked", False),
            "created_at": now,
            "updated_at": now,
        }

        rules = self._get_world_rules_from_core(project.core_data or {})
        rules.append(rule)

        project.core_data = self._set_world_rules_to_core(project.core_data or {}, rules)
        await self._save_project(project)

        return rule

    async def update_world_rule(self, project_id: str, rule_id: str, data: dict) -> dict | None:
        project = await self._get_project(project_id)
        if not project:
            return None

        rules = self._get_world_rules_from_core(project.core_data or {})
        found = None
        for i, rule in enumerate(rules):
            if rule.get("id") == rule_id:
                rule.update(data)
                rule["updated_at"] = datetime.now(timezone.utc).isoformat()
                rules[i] = rule
                found = rule
                break

        if found is None:
            return None

        project.core_data = self._set_world_rules_to_core(project.core_data or {}, rules)
        await self._save_project(project)

        return found

    async def delete_world_rule(self, project_id: str, rule_id: str) -> bool:
        project = await self._get_project(project_id)
        if not project:
            return False

        rules = self._get_world_rules_from_core(project.core_data or {})
        new_rules = [r for r in rules if r.get("id") != rule_id]

        if len(new_rules) == len(rules):
            return False

        project.core_data = self._set_world_rules_to_core(project.core_data or {}, new_rules)
        await self._save_project(project)

        return True

    async def list_foreshadowing(
        self, project_id: str, include_archived: bool = False
    ) -> list[dict]:
        """获取伏笔列表。

        方案 11 Part A2：状态分层访问入口。
        - include_archived=False（默认）：只返回活跃态（planned/active/dormant/revealing/revised）。
        - include_archived=True：返回活跃态 + resolved（用于摘要注入到上下文）。
        - aborted 永远不返回（只在溯源时按需调用 ForeshadowingService 直接查询）。
        """
        from app.services.foreshadowing_service import ForeshadowingService

        async with async_session() as session:
            service = ForeshadowingService()
            if include_archived:
                # 活跃态 + 已揭示态（resolved），不返回 aborted
                statuses = ["planned", "active", "dormant", "revealing", "resolved", "revised"]
            else:
                # 仅活跃态
                # P2-4: revised 是 active 的子状态（P1-22 已合法化为 active 的工作中态），
                # 故保留在活跃态列表中，与文档状态机定义一致。
                statuses = ["planned", "active", "dormant", "revealing", "revised"]
            lines = await service.list_foreshadowing_lines(
                project_id,
                session,
                statuses=statuses,
            )
            return lines

    async def get_foreshadowing(self, project_id: str, item_id: str) -> dict | None:
        items = await self.list_foreshadowing(project_id)
        for item in items:
            if item.get("id") == item_id:
                return item
        return None

    async def create_foreshadowing(self, project_id: str, data: dict) -> dict:
        # 方案 11 Part C：伏笔定义校验。
        # 伏笔必须同时满足：有秘密 + 有埋设点 + 有揭示点。
        # （"影响后续情节"和"有认知差"由业务上下文保证，无法在创建时强校验）
        secret = (
            data.get("secret_canonical_statement")
            or data.get("secret", {}).get("canonical_statement")
            or data.get("description")
        )
        if not secret:
            raise ValueError("伏笔必须有 secret_canonical_statement 或 description（秘密）")
        if not data.get("bury_window_start") or not data.get("bury_window_end"):
            raise ValueError("伏笔必须有 bury_window_start/end（埋设点）")
        if not data.get("reveal_window_start") or not data.get("reveal_window_end"):
            raise ValueError("伏笔必须有 reveal_window_start/end（揭示点）")

        from app.services.foreshadowing_service import ForeshadowingService

        async with async_session() as session:
            service = ForeshadowingService()
            item = await service.create_foreshadowing_line(
                project_id,
                {
                    "name": data.get("name", ""),
                    "description": data.get("description", ""),
                    "status": data.get("status", "planned"),
                    "secret": {
                        "canonical_statement": data.get("description") or data.get("name", ""),
                        "truth_type": "past_event",
                        "impact_level": "moderate",
                        "spoiler_scope": {"characters": data.get("related_characters", [])},
                    },
                    "timeline": {
                        "bury_window": [data.get("bury_window_start"), data.get("bury_window_end")],
                        "reveal_window": [data.get("reveal_window_start"), data.get("reveal_window_end")],
                    },
                },
                session,
            )
            await session.commit()
            return item

    async def update_foreshadowing(self, project_id: str, item_id: str, data: dict) -> dict | None:
        from app.services.foreshadowing_service import ForeshadowingService

        async with async_session() as session:
            service = ForeshadowingService()
            item = await service.update_foreshadowing_line(project_id, item_id, data, session)
            await session.commit()
            return item

    async def delete_foreshadowing(self, project_id: str, item_id: str) -> bool:
        from app.services.foreshadowing_service import ForeshadowingService

        async with async_session() as session:
            service = ForeshadowingService()
            deleted = await service.delete_foreshadowing_line(project_id, item_id, session)
            await session.commit()
            return deleted

    async def get_active_foreshadowing_for_scene(self, project_id: str, chapter: int, scene: int) -> list[dict]:
        from app.services.foreshadowing_service import ForeshadowingService

        async with async_session() as session:
            service = ForeshadowingService()
            lines = await service.list_actionable_for_scene(project_id, chapter, session)
            return lines

    def _get_promotions_from_core(self, core_data: dict) -> list[dict]:
        return core_data.get("promotion_proposals", [])

    def _set_promotions_to_core(self, core_data: dict, items: list[dict]) -> dict:
        core_data = dict(core_data)
        core_data["promotion_proposals"] = items
        return core_data

    async def list_promotion_proposals(self, project_id: str) -> list[dict]:
        project = await self._get_project(project_id)
        if not project:
            return []
        return self._get_promotions_from_core(project.core_data or {})

    async def create_promotion_proposal(self, project_id: str, data: dict) -> dict:
        project = await self._get_project(project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")

        proposal_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        proposal = {
            "id": proposal_id,
            "seed_id": data.get("seed_id", ""),
            "seed_content": data.get("seed_content", ""),
            "reference_count": data.get("reference_count", 0),
            "target_type": data.get("target_type", "character"),
            "target_data": data.get("target_data", {}),
            "status": "pending",
            "conflict_info": data.get("conflict_info", ""),
            "created_at": now,
            "updated_at": now,
        }

        items = self._get_promotions_from_core(project.core_data or {})
        items.append(proposal)

        project.core_data = self._set_promotions_to_core(project.core_data or {}, items)
        await self._save_project(project)

        return proposal

    async def update_promotion_proposal(self, project_id: str, proposal_id: str, data: dict) -> dict | None:
        project = await self._get_project(project_id)
        if not project:
            return None

        items = self._get_promotions_from_core(project.core_data or {})
        found = None
        for i, item in enumerate(items):
            if item.get("id") == proposal_id:
                item.update(data)
                item["updated_at"] = datetime.now(timezone.utc).isoformat()
                items[i] = item
                found = item
                break

        if found is None:
            return None

        project.core_data = self._set_promotions_to_core(project.core_data or {}, items)
        await self._save_project(project)

        return found

    async def delete_promotion_proposal(self, project_id: str, proposal_id: str) -> bool:
        project = await self._get_project(project_id)
        if not project:
            return False

        items = self._get_promotions_from_core(project.core_data or {})
        new_items = [it for it in items if it.get("id") != proposal_id]

        if len(new_items) == len(items):
            return False

        project.core_data = self._set_promotions_to_core(project.core_data or {}, new_items)
        await self._save_project(project)

        return True

    async def approve_promotion(self, project_id: str, proposal_id: str) -> dict | None:
        project = await self._get_project(project_id)
        if not project:
            return None

        items = self._get_promotions_from_core(project.core_data or {})
        proposal = None
        for item in items:
            if item.get("id") == proposal_id:
                proposal = item
                break

        if proposal is None:
            return None

        if proposal.get("status") != "pending":
            raise ValueError("Only pending proposals can be approved")

        target_type = proposal.get("target_type")
        target_data = proposal.get("target_data", {})
        core_data = dict(project.core_data or {})

        if target_type == "character":
            characters = self._get_characters_from_core(core_data)
            char_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            char_data = dict(target_data)
            char_data["id"] = char_id
            char_data["project_id"] = project_id
            char_data.setdefault("name", "")
            char_data.setdefault("aliases", [])
            char_data.setdefault("appearance", "")
            char_data.setdefault("personality", "")
            char_data.setdefault("desire", "")
            char_data.setdefault("deep_need", "")
            char_data.setdefault("arc", "")
            char_data.setdefault("relationships", {})
            char_data["created_at"] = now
            char_data["updated_at"] = now
            characters.append(char_data)
            core_data = self._set_characters_to_core(core_data, characters)
        elif target_type == "world_rule":
            rules = self._get_world_rules_from_core(core_data)
            rule_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            rule_data = dict(target_data)
            rule_data["id"] = rule_id
            rule_data.setdefault("category", "general")
            rule_data.setdefault("name", "")
            rule_data.setdefault("description", "")
            rule_data.setdefault("constraints", [])
            rule_data.setdefault("priority", "normal")
            rule_data.setdefault("locked", False)
            rule_data["created_at"] = now
            rule_data["updated_at"] = now
            rules.append(rule_data)
            core_data = self._set_world_rules_to_core(core_data, rules)
        elif target_type == "location":
            locations = core_data.get("locations", [])
            loc_id = str(uuid.uuid4())
            loc_data = dict(target_data)
            loc_data["id"] = loc_id
            loc_data.setdefault("name", "")
            loc_data.setdefault("description", "")
            loc_data.setdefault("atmosphere", "")
            locations.append(loc_data)
            core_data["locations"] = locations

        for i, item in enumerate(items):
            if item.get("id") == proposal_id:
                items[i]["status"] = "approved"
                items[i]["updated_at"] = datetime.now(timezone.utc).isoformat()
                break

        core_data = self._set_promotions_to_core(core_data, items)
        project.core_data = core_data
        await self._save_project(project)

        return items[[i for i, it in enumerate(items) if it.get("id") == proposal_id][0]]

    async def reject_promotion(self, project_id: str, proposal_id: str, reason: str = "") -> dict | None:
        project = await self._get_project(project_id)
        if not project:
            return None

        items = self._get_promotions_from_core(project.core_data or {})
        found = None
        for i, item in enumerate(items):
            if item.get("id") == proposal_id:
                items[i]["status"] = "rejected"
                items[i]["conflict_info"] = reason
                items[i]["updated_at"] = datetime.now(timezone.utc).isoformat()
                found = items[i]
                break

        if found is None:
            return None

        project.core_data = self._set_promotions_to_core(project.core_data or {}, items)
        await self._save_project(project)

        return found

    def _get_style_profiles_from_core(self, core_data: dict) -> list[dict]:
        return core_data.get("style_profiles", [])

    def _set_style_profiles_to_core(self, core_data: dict, items: list[dict]) -> dict:
        core_data = dict(core_data)
        core_data["style_profiles"] = items
        return core_data

    def _default_style_embedding(self) -> dict:
        return {
            "emotionality": 0.0,
            "sentence_complexity": 0.0,
            "narrative_distance": 0.0,
            "info_density": 0.0,
            "dialogue_ratio": 0.0,
            "description_density": 0.0,
            "rhythm_steepness": 0.0,
            "narrator_intrusion": 0.0,
        }

    def _default_persona_card(self) -> dict:
        return {
            "identity": "",
            "decision_pattern": "",
            "expression_style": "",
            "interpersonal_behavior": "",
            "hard_rules": [],
        }

    def _default_style_statistics(self) -> dict:
        return {
            "global": {
                "total_chars": 0,
                "paragraph_count": 0,
                "chapter_count": 0,
                "word_freq_top": [],
                "sentence_length": {"mean": 0.0, "median": 0.0, "stdev": 0.0},
                "paragraph_length": {"mean": 0.0, "median": 0.0, "stdev": 0.0},
                "punctuation_profile": {},
            },
            "chapter_curves": {},
            "scene_distribution": {},
            "evolution": self._default_evolution_report(),
        }

    def _default_evolution_report(self) -> dict:
        return {
            "change_points": [],
            "style_clusters": [],
            "is_multi_style": False,
            "sub_profiles": [],
            "explanation": "",
            "recommended_usage": "",
        }

    def _normalize_style_profile(self, profile: dict | None) -> dict | None:
        if not profile:
            return None
        normalized = dict(profile)
        if not normalized.get("source_books"):
            normalized["source_books"] = []
        if not normalized.get("style_features"):
            normalized["style_features"] = {
            "vocabulary": "",
            "sentence_structure": "",
            "tone": "",
            "pacing": "",
            "description_style": "",
            "dialogue_style": "",
            "narrative_voice": "",
            "signature_phrases": [],
            "avoid_patterns": [],
        }
        if not normalized.get("style_embedding"):
            normalized["style_embedding"] = self._default_style_embedding()
        if not normalized.get("persona_card"):
            normalized["persona_card"] = self._default_persona_card()
        if normalized.get("sample_passages") is None:
            normalized["sample_passages"] = []
        if normalized.get("style_prompt") is None:
            normalized["style_prompt"] = ""
        if not normalized.get("style_statistics"):
            normalized["style_statistics"] = self._default_style_statistics()
        if not normalized.get("evolution_report"):
            normalized["evolution_report"] = self._default_evolution_report()
        if normalized.get("confidence") is None:
            normalized["confidence"] = 0.0
        if not normalized.get("version"):
            normalized["version"] = 1
        if normalized.get("active") is None:
            normalized["active"] = False
        if normalized.get("status") is None:
            normalized["status"] = "learning"
        if normalized.get("domains") is None:
            normalized["domains"] = []
        if normalized.get("parent_profile_ids") is None:
            normalized["parent_profile_ids"] = []
        if normalized.get("frozen") is None:
            normalized["frozen"] = False
        if normalized.get("editor_influence_enabled") is None:
            normalized["editor_influence_enabled"] = True
        if normalized.get("learning_progress") is None:
            normalized["learning_progress"] = None
        return normalized

    async def list_style_profiles(self, project_id: str) -> list[dict]:
        project = await self._get_project(project_id)
        if not project:
            return []
        return [p for p in (self._normalize_style_profile(item) for item in self._get_style_profiles_from_core(project.core_data or {})) if p]

    async def get_style_profile(self, project_id: str, profile_id: str) -> dict | None:
        items = await self.list_style_profiles(project_id)
        for item in items:
            if item.get("id") == profile_id:
                return item
        return None

    async def create_style_profile(self, project_id: str, data: dict) -> dict:
        project = await self._get_project(project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")

        profile_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        profile = {
            "id": profile_id,
            "name": data.get("name", "未命名风格"),
            "source_books": data.get("source_books", []),
            "style_features": data.get("style_features", {
                "vocabulary": "",
                "sentence_structure": "",
                "tone": "",
                "pacing": "",
                "description_style": "",
                "dialogue_style": "",
                "narrative_voice": "",
                "signature_phrases": [],
                "avoid_patterns": [],
            }),
            "style_embedding": data.get("style_embedding", {
                "emotionality": 0.0,
                "sentence_complexity": 0.0,
                "narrative_distance": 0.0,
                "info_density": 0.0,
                "dialogue_ratio": 0.0,
                "description_density": 0.0,
                "rhythm_steepness": 0.0,
                "narrator_intrusion": 0.0,
            }),
            "persona_card": data.get("persona_card", {
                "identity": "",
                "decision_pattern": "",
                "expression_style": "",
                "interpersonal_behavior": "",
                "hard_rules": [],
            }),
            "sample_passages": data.get("sample_passages", []),
            "style_prompt": data.get("style_prompt", ""),
            "style_statistics": data.get("style_statistics", self._default_style_statistics()),
            "evolution_report": data.get("evolution_report", self._default_evolution_report()),
            "confidence": data.get("confidence", 0.0),
            "version": data.get("version", 1),
            "active": data.get("active", False),
            "status": data.get("status", "learning"),
            "domains": data.get("domains", []),
            "parent_profile_ids": data.get("parent_profile_ids", []),
            "frozen": data.get("frozen", False),
            "editor_influence_enabled": data.get("editor_influence_enabled", True),
            "learning_progress": data.get("learning_progress", None),
            "created_at": now,
            "updated_at": now,
        }

        items = self._get_style_profiles_from_core(project.core_data or {})
        items.append(profile)

        project.core_data = self._set_style_profiles_to_core(project.core_data or {}, items)
        await self._save_project(project)

        return self._normalize_style_profile(profile) or profile

    async def update_style_profile(self, project_id: str, profile_id: str, data: dict) -> dict | None:
        project = await self._get_project(project_id)
        if not project:
            return None

        items = self._get_style_profiles_from_core(project.core_data or {})
        found = None
        for i, item in enumerate(items):
            if item.get("id") == profile_id:
                item.update(data)
                item["updated_at"] = datetime.now(timezone.utc).isoformat()
                items[i] = item
                found = item
                break

        if found is None:
            return None

        project.core_data = self._set_style_profiles_to_core(project.core_data or {}, items)
        await self._save_project(project)

        return self._normalize_style_profile(found) or found

    async def delete_style_profile(self, project_id: str, profile_id: str) -> bool:
        project = await self._get_project(project_id)
        if not project:
            return False

        items = self._get_style_profiles_from_core(project.core_data or {})
        new_items = [it for it in items if it.get("id") != profile_id]

        if len(new_items) == len(items):
            return False

        project.core_data = self._set_style_profiles_to_core(project.core_data or {}, new_items)
        await self._save_project(project)

        return True

    async def get_active_style_profile(self, project_id: str) -> dict | None:
        items = await self.list_style_profiles(project_id)
        for item in items:
            if (
                item.get("active")
                and item.get("status") == "ready"
                and item.get("editor_influence_enabled", True) is not False
            ):
                return item
        for item in items:
            if (
                item.get("frozen")
                and item.get("status") == "ready"
                and item.get("editor_influence_enabled", True) is not False
            ):
                return item
        return None

    async def activate_style_profile(self, project_id: str, profile_id: str) -> dict | None:
        project = await self._get_project(project_id)
        if not project:
            return None

        items = self._get_style_profiles_from_core(project.core_data or {})
        found = None
        for i, item in enumerate(items):
            if item.get("id") == profile_id:
                item["active"] = True
                item["frozen"] = False
                item["editor_influence_enabled"] = True
                item["updated_at"] = datetime.now(timezone.utc).isoformat()
                items[i] = item
                found = item
            else:
                item["active"] = False
                item["updated_at"] = datetime.now(timezone.utc).isoformat()
                items[i] = item

        if found is None:
            return None

        project.core_data = self._set_style_profiles_to_core(project.core_data or {}, items)
        await self._save_project(project)

        return self._normalize_style_profile(found) or found

    async def freeze_style_profile(self, project_id: str, profile_id: str) -> dict | None:
        project = await self._get_project(project_id)
        if not project:
            return None

        items = self._get_style_profiles_from_core(project.core_data or {})
        found = None
        for i, item in enumerate(items):
            if item.get("id") == profile_id:
                item["active"] = True
                item["frozen"] = True
                item["editor_influence_enabled"] = True
                item["updated_at"] = datetime.now(timezone.utc).isoformat()
                items[i] = item
                found = item
            else:
                item["active"] = False
                item["updated_at"] = datetime.now(timezone.utc).isoformat()
                items[i] = item

        if found is None:
            return None

        project.core_data = self._set_style_profiles_to_core(project.core_data or {}, items)
        await self._save_project(project)

        return self._normalize_style_profile(found) or found

    async def set_style_profile_editor_influence(
        self,
        project_id: str,
        profile_id: str,
        enabled: bool,
    ) -> dict | None:
        project = await self._get_project(project_id)
        if not project:
            return None

        items = self._get_style_profiles_from_core(project.core_data or {})
        found = None
        for i, item in enumerate(items):
            if item.get("id") == profile_id:
                item["editor_influence_enabled"] = bool(enabled)
                item["active"] = bool(enabled)
                item["updated_at"] = datetime.now(timezone.utc).isoformat()
                items[i] = item
                found = item
            elif enabled:
                item["active"] = False
                item["updated_at"] = datetime.now(timezone.utc).isoformat()
                items[i] = item

        if found is None:
            return None

        project.core_data = self._set_style_profiles_to_core(project.core_data or {}, items)
        await self._save_project(project)

        return self._normalize_style_profile(found) or found

    async def get_style_statistics(self, project_id: str, profile_id: str) -> dict | None:
        profile = await self.get_style_profile(project_id, profile_id)
        if not profile:
            return None
        return profile.get("style_statistics", self._default_style_statistics())

    async def get_style_embedding(self, project_id: str, profile_id: str) -> dict | None:
        profile = await self.get_style_profile(project_id, profile_id)
        if not profile:
            return None
        return profile.get("style_embedding", self._default_style_embedding())

    async def get_style_evolution(self, project_id: str, profile_id: str) -> dict | None:
        profile = await self.get_style_profile(project_id, profile_id)
        if not profile:
            return None
        return profile.get("evolution_report", self._default_evolution_report())

    async def compare_style_profiles(self, project_id: str, profile_ids: list[str]) -> list[dict]:
        profiles = []
        for profile_id in profile_ids:
            profile = await self.get_style_profile(project_id, profile_id)
            if profile:
                profiles.append(profile)
        reports = []
        if len(profiles) < 2:
            return reports

        for i in range(len(profiles)):
            for j in range(i + 1, len(profiles)):
                a = profiles[i]
                b = profiles[j]
                emb_a = a.get("style_embedding", self._default_style_embedding())
                emb_b = b.get("style_embedding", self._default_style_embedding())
                conflicts = []
                total_delta = 0.0
                for dim_key in emb_a.keys():
                    val_a = float(emb_a.get(dim_key, 0.0))
                    val_b = float(emb_b.get(dim_key, 0.0))
                    delta = abs(val_a - val_b)
                    total_delta += delta
                    if delta > 0.3:
                        conflicts.append({
                            "dimension": dim_key,
                            "dimension_label": dim_key,
                            "value_a": val_a,
                            "value_b": val_b,
                            "delta": round(delta, 3),
                            "level": "L2" if delta <= 0.6 else "L3" if delta <= 0.8 else "L4",
                            "type": "numeric",
                        })
                rules_a = a.get("persona_card", {}).get("hard_rules", [])
                rules_b = b.get("persona_card", {}).get("hard_rules", [])
                if rules_a != rules_b:
                    conflicts.append({
                        "dimension": "hard_rules",
                        "dimension_label": "硬规则",
                        "value_a": len(rules_a),
                        "value_b": len(rules_b),
                        "delta": 1.0,
                        "level": "L4" if (rules_a and rules_b) else "L2",
                        "type": "rule",
                        "rule_conflict": {"rule_a": rules_a, "rule_b": rules_b},
                    })
                reports.append({
                    "profile_a_id": a["id"],
                    "profile_a_name": a["name"],
                    "profile_b_id": b["id"],
                    "profile_b_name": b["name"],
                    "conflicts": conflicts,
                    "compatibility_score": round(max(0.0, 1.0 - total_delta / max(len(emb_a), 1)), 2),
                })
        return reports

    async def create_merged_style_profile(self, project_id: str, data: dict) -> dict:
        source_ids = data.get("source_profile_ids", [])
        profiles = []
        for profile_id in source_ids:
            profile = await self.get_style_profile(project_id, profile_id)
            if profile:
                profiles.append(profile)
        if len(profiles) < 2:
            raise ValueError("至少需要两个风格画像进行融合")

        weights = data.get("weights", [])
        if len(weights) != len(profiles):
            weights = [1.0 / len(profiles)] * len(profiles)

        merged_embedding = {}
        for dim_key in self._default_style_embedding().keys():
            merged_embedding[dim_key] = round(
                sum(float(profiles[i].get("style_embedding", {}).get(dim_key, 0.0)) * float(weights[i]) for i in range(len(profiles))),
                3,
            )

        merged_features = {
            "vocabulary": profiles[0].get("style_features", {}).get("vocabulary", ""),
            "sentence_structure": profiles[0].get("style_features", {}).get("sentence_structure", ""),
            "tone": profiles[0].get("style_features", {}).get("tone", ""),
            "pacing": profiles[0].get("style_features", {}).get("pacing", ""),
            "description_style": profiles[0].get("style_features", {}).get("description_style", ""),
            "dialogue_style": profiles[0].get("style_features", {}).get("dialogue_style", ""),
            "narrative_voice": profiles[0].get("style_features", {}).get("narrative_voice", ""),
            "signature_phrases": [],
            "avoid_patterns": [],
        }
        for profile in profiles:
            for key in ["signature_phrases", "avoid_patterns"]:
                for item in profile.get("style_features", {}).get(key, []):
                    if item not in merged_features[key]:
                        merged_features[key].append(item)

        merged_persona = profiles[0].get("persona_card", self._default_persona_card())
        merged_statistics = self._default_style_statistics()
        merged_statistics["global"]["source_profile_count"] = len(profiles)
        merged_statistics["global"]["source_names"] = [profile["name"] for profile in profiles]
        merged_statistics["evolution"] = {
            "change_points": [],
            "style_clusters": [],
            "is_multi_style": len(profiles) > 1,
            "sub_profiles": [],
            "explanation": "融合风格由多个源画像合成",
            "recommended_usage": "可用于统一风格输出或作为融合实验画像",
        }

        profile = await self.create_style_profile(project_id, {
            "name": data.get("name") or f"融合风格（{' + '.join(profile['name'] for profile in profiles[:3])}）",
            "source_books": [],
            "style_features": merged_features,
            "style_embedding": merged_embedding,
            "persona_card": merged_persona,
            "sample_passages": [],
            "style_prompt": data.get("style_prompt", ""),
            "style_statistics": merged_statistics,
            "evolution_report": {
                "change_points": [],
                "style_clusters": [],
                "is_multi_style": len(profiles) > 1,
                "sub_profiles": [],
                "explanation": "融合后画像默认视作复合风格",
                "recommended_usage": "可按需要再继续精炼",
            },
            "confidence": data.get("confidence", 0.5),
            "version": 1,
            "active": False,
            "status": data.get("status", "ready"),
            "domains": data.get("domains", []),
            "parent_profile_ids": source_ids,
        })

        return profile

    async def rollback_style_profile(self, project_id: str, profile_id: str, version: int) -> dict | None:
        profile = await self.get_style_profile(project_id, profile_id)
        if not profile:
            return None
        if version < 1:
            version = 1
        updated = await self.update_style_profile(project_id, profile_id, {"version": version})
        return updated

    async def validate_core_change(self, project_id: str, change_type: str, change_data: dict) -> dict:
        try:
            from app.agents.scene_validator import SceneValidator
            checker = SceneValidator()

            project = await self._get_project(project_id)
            core_data = project.core_data if project else None
            if not core_data:
                return {"valid": True, "reason": "无现有核心数据"}

            existing_summary = []
            if "characters" in core_data:
                for c in core_data["characters"][:5]:
                    existing_summary.append(f"人物：{c.get('name', '')} - {c.get('personality', '')[:30]}")
            if "world_rules" in core_data:
                for r in core_data["world_rules"][:5]:
                    existing_summary.append(f"规则：{r.get('name', '')} - {r.get('description', '')[:30]}")

            change_desc = f"修改类型：{change_type}，修改内容：{json.dumps(change_data, ensure_ascii=False)[:200]}"

            core_summary = "\n".join(existing_summary)
            result = await checker.execute({
                "generated_text": change_desc,
                "scene_contract": {
                    "goal": "validate core memory change",
                    "must_show": [],
                    "forbidden": [],
                },
                "core_facts": {
                    "existing_core_summary": {
                        "type": "outline",
                        "title": "Core memory snapshot",
                        "core_conflict": core_summary,
                    }
                },
                "current_state": {},
            })

            report = result.get("consistency", {})
            return {
                "valid": report.get("pass", True),
                "conflicts": report.get("conflicts", []),
            }
        except Exception:
            return {"valid": True, "reason": "校验服务不可用，默认通过"}

    async def _get_project_with_db(self, db, project_id) -> Project | None:
        pid = project_id if isinstance(project_id, uuid.UUID) else uuid.UUID(str(project_id))
        result = await db.execute(
            select(Project).where(Project.id == pid)
        )
        return result.scalar_one_or_none()

    async def _save_project_with_db(self, db, project: Project) -> None:
        db_project = await db.get(Project, project.id)
        if db_project:
            db_project.core_data = project.core_data
            flag_modified(db_project, "core_data")
            db_project.core_version = (db_project.core_version or 0) + 1
            db_project.updated_at = datetime.now(timezone.utc)
            await db.flush()

    async def list_world_rules_with_db(self, db, project_id: str) -> list[dict]:
        """Read world rules through the caller's transaction."""
        project = await self._get_project_with_db(db, project_id)
        if not project:
            return []
        return self._get_world_rules_from_core(project.core_data or {})

    async def create_world_rule_with_db(self, db, project_id: str, data: dict) -> dict:
        project = await self._get_project_with_db(db, project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")

        rule_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        rule = {
            "id": rule_id,
            "category": data.get("category", "general"),
            "name": data.get("name", ""),
            "description": data.get("description", ""),
            "constraints": data.get("constraints", []),
            "priority": data.get("priority", "normal"),
            "locked": data.get("locked", False),
            "created_at": now,
            "updated_at": now,
        }

        rules = self._get_world_rules_from_core(project.core_data or {})
        rules.append(rule)

        project.core_data = self._set_world_rules_to_core(project.core_data or {}, rules)
        await self._save_project_with_db(db, project)

        return rule

    async def update_world_rule_with_db(self, db, project_id: str, rule_id: str, data: dict) -> dict | None:
        project = await self._get_project_with_db(db, project_id)
        if not project:
            return None

        rules = self._get_world_rules_from_core(project.core_data or {})
        found = None
        for i, rule in enumerate(rules):
            if rule.get("id") == rule_id:
                rule.update(data)
                rule["updated_at"] = datetime.now(timezone.utc).isoformat()
                rules[i] = rule
                found = rule
                break

        if found is None:
            return None

        project.core_data = self._set_world_rules_to_core(project.core_data or {}, rules)
        await self._save_project_with_db(db, project)

        return found

    async def delete_world_rule_with_db(self, db, project_id: str, rule_id: str) -> bool:
        project = await self._get_project_with_db(db, project_id)
        if not project:
            return False

        rules = self._get_world_rules_from_core(project.core_data or {})
        new_rules = [r for r in rules if r.get("id") != rule_id]

        if len(new_rules) == len(rules):
            return False

        project.core_data = self._set_world_rules_to_core(project.core_data or {}, new_rules)
        await self._save_project_with_db(db, project)

        return True

    async def create_character_with_db(self, db, project_id: str, data) -> CharacterResponse:
        project = await self._get_project_with_db(db, project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")

        now = datetime.now(timezone.utc).isoformat()
        char_id = str(uuid.uuid4())
        if hasattr(data, "model_dump"):
            char_data = data.model_dump()
        else:
            char_data = CharacterCreate(**dict(data)).model_dump()
        char_data["id"] = char_id
        char_data["project_id"] = project_id
        char_data["created_at"] = now
        char_data["updated_at"] = now

        characters = self._get_characters_from_core(project.core_data or {})
        characters.append(char_data)

        project.core_data = self._set_characters_to_core(project.core_data or {}, characters)
        await self._save_project_with_db(db, project)

        return CharacterResponse(**char_data)

    async def update_character_with_db(self, db, project_id: str, character_id: str, data: dict) -> CharacterResponse:
        project = await self._get_project_with_db(db, project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")

        characters = self._get_characters_from_core(project.core_data or {})
        found = None
        for i, char in enumerate(characters):
            if char.get("id") == character_id:
                merged = dict(char)
                merged.update(data)
                normalized = CharacterCreate(**merged).model_dump()
                for preserved in ("id", "project_id", "created_at"):
                    if preserved in char:
                        normalized[preserved] = char[preserved]
                char = normalized
                char["updated_at"] = datetime.now(timezone.utc).isoformat()
                characters[i] = char
                found = char
                break

        if found is None:
            raise ValueError(f"Character {character_id} not found")

        project.core_data = self._set_characters_to_core(project.core_data or {}, characters)
        await self._save_project_with_db(db, project)

        return CharacterResponse(**found)

    async def delete_character_with_db(self, db, project_id: str, character_id: str) -> bool:
        project = await self._get_project_with_db(db, project_id)
        if not project:
            return False

        characters = self._get_characters_from_core(project.core_data or {})
        new_characters = [c for c in characters if c.get("id") != character_id]

        if len(new_characters) == len(characters):
            return False

        project.core_data = self._set_characters_to_core(project.core_data or {}, new_characters)
        await self._save_project_with_db(db, project)

        return True

    async def add_location_with_db(self, db, project_id: str, data: dict) -> dict:
        project = await self._get_project_with_db(db, project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")

        loc_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        loc = {
            "id": loc_id,
            "name": data.get("name", ""),
            "description": data.get("description", ""),
            "created_at": now,
            "updated_at": now,
        }
        loc.update(data)

        locations = (project.core_data or {}).get("locations", [])
        locations.append(loc)

        cd = dict(project.core_data or {})
        cd["locations"] = locations
        project.core_data = cd
        await self._save_project_with_db(db, project)

        return loc

    async def list_locations_with_db(self, db, project_id: str) -> list[dict]:
        """Read locations through the caller's transaction."""
        project = await self._get_project_with_db(db, project_id)
        if not project:
            return []
        return (project.core_data or {}).get("locations", [])

    async def update_location_with_db(self, db, project_id: str, location_id: str, data: dict) -> dict | None:
        project = await self._get_project_with_db(db, project_id)
        if not project:
            return None

        locations = (project.core_data or {}).get("locations", [])
        found = None
        for i, loc in enumerate(locations):
            if loc.get("id") == location_id:
                loc.update(data)
                loc["updated_at"] = datetime.now(timezone.utc).isoformat()
                locations[i] = loc
                found = loc
                break

        if found is None:
            return None

        cd = dict(project.core_data or {})
        cd["locations"] = locations
        project.core_data = cd
        await self._save_project_with_db(db, project)

        return found

    async def delete_location_with_db(self, db, project_id: str, location_id: str) -> bool:
        project = await self._get_project_with_db(db, project_id)
        if not project:
            return False

        locations = (project.core_data or {}).get("locations", [])
        new_locations = [l for l in locations if l.get("id") != location_id]

        if len(new_locations) == len(locations):
            return False

        cd = dict(project.core_data or {})
        cd["locations"] = new_locations
        project.core_data = cd
        await self._save_project_with_db(db, project)

        return True
