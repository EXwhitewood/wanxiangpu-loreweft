import asyncio
import json
import logging
import re
import uuid

from app.services.memory_core import CoreMemoryService
from app.services.memory_shell import ShellMemoryService
from app.services.json_response import parse_json_response
from app.services.llm_task_profiles import LLMTaskType

logger = logging.getLogger(__name__)


class WorldviewExtractionError(Exception):
    pass

EXTRACT_PROMPT_V2 = """从以下章节内容中提取新出现的世界观元素。

已知人物：{known_characters}
已知地点：{known_locations}
已知规则：{known_rules}
已知物品：{known_items}
已知伏笔（必须优先复用 id 和标准名）：{known_foreshadowing}
当前章节大纲：{chapter_outline}

请提取以下六类元素：
1. 新出现的人物（不在已知人物列表中的，包括只被提及的）
2. 新出现的地点（不在已知地点列表中的，包括只被提及的）
3. 新暗示的规则（文中隐含但未明确设定的世界规则）
4. 新出现的伏笔（暗示未来发展的线索或悬念）
5. 新出现的事实（重要的世界观事实，如历史事件、组织信息等）
6. 新出现的物品（关键道具/重要物品/普通物品，包括武器、信物、工具等）

每一项必须包含以下字段：
- name: 实体名称
- aliases: 别名列表（如有）
- operation: 操作类型，可选值：new（首次埋设）/ supplement（增长已有伏笔）/ mention（再次提及）/ reveal（正文已经完成回收）/ conflict（与已有设定冲突）
- description: 结构化描述
- evidence_text: 正文原句依据
- confidence: 置信度，0到1之间的浮点数

【人物字段】：description 只写人物摘要；appearance/personality/desire/deep_need/arc/role/faction/lifecycle_status/narrative_activity/role_importance/relationships 必须作为独立顶层字段。不确定的字段留空，不要编造。
【物品 description 推荐结构】（方案 3 B3）：appearance/function/origin/owner/status/importance。不确定的字段留空，不要编造。

严格按以下JSON格式输出，不要输出其他内容：
```json
{{"characters": [{{"name": "人物名", "aliases": [], "operation": "new", "description": "人物摘要", "appearance": "", "personality": "", "desire": "", "deep_need": "", "arc": "", "role": "", "faction": "", "lifecycle_status": "unknown", "narrative_activity": "dormant", "role_importance": "supporting", "relationships": {{}}, "evidence_text": "正文原句", "confidence": 0.9}}], "locations": [{{"name": "地点名", "aliases": [], "operation": "new", "description": "描述", "evidence_text": "正文原句", "confidence": 0.85}}], "world_rules": [{{"name": "规则名", "aliases": [], "operation": "new", "description": "描述", "evidence_text": "正文原句", "confidence": 0.7}}], "foreshadowing": [{{"foreshadowing_id": "已有伏笔ID或空字符串", "name": "伏笔标准名", "aliases": [], "operation": "new", "description": "描述", "evidence_text": "正文原句", "confidence": 0.8}}], "facts": [{{"name": "事实名", "aliases": [], "operation": "new", "description": "描述", "evidence_text": "正文原句", "confidence": 0.75}}], "items": [{{"name": "物品名", "aliases": [], "operation": "new", "description": "物品摘要", "appearance": "", "function": "", "origin": "", "owner": "", "status": "", "importance": "", "evidence_text": "正文原句", "confidence": 0.8}}]}}
```

注意：
- 规则暗示的置信度应低于明确设定，角色误解或比喻不应被视为规则
- 伏笔需要是明确暗示未来发展的线索，而非普通悬念
- 若正文是在增长或回收“已知伏笔”，必须输出其 foreshadowing_id 和标准 name，不得换一个长句名称新建伏笔
- reveal 只用于正文中已经出现明确揭示证据的情况；仅接近回收窗口或再次提及不能标为 reveal
- 物品只提取对剧情或人物有意义的关键道具、重要物品，避免普通日常物品（如筷子、椅子）
- evidence_text 必须是正文中的原句，便于追溯

章节内容：
{chapter_content}"""


def normalize_entity_name(name: str) -> str:
    normalized = name.strip()
    normalized = re.sub(r'\s+', '', normalized)
    normalized = normalized.lower()
    return normalized


def split_worldview_chunks(text: str, max_chars: int = 2800) -> list[str]:
    """Split a complete chapter without dropping any non-whitespace content."""
    text = str(text or "")
    if not text:
        return []
    paragraphs = re.split(r"(\n\s*\n)", text)
    chunks: list[str] = []
    current = ""
    for part in paragraphs:
        if not part:
            continue
        if len(part) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            for start in range(0, len(part), max_chars):
                chunks.append(part[start:start + max_chars])
            continue
        if current and len(current) + len(part) > max_chars:
            chunks.append(current)
            current = part
        else:
            current += part
    if current:
        chunks.append(current)
    return chunks


class WorldviewExtractor:
    EXTRACTION_VERSION = 2

    async def _load_known_foreshadowing(self, project_id: str, db=None) -> list[dict]:
        """Return a bounded canonical identity registry, not the full clue history."""
        from app.services.foreshadowing_service import ForeshadowingService

        service = ForeshadowingService()
        if db is not None:
            lines = await service.list_foreshadowing_lines(
                project_id,
                db,
                statuses=["planned", "active", "dormant", "revealing", "revised"],
            )
        else:
            from app.db.db_models import async_session

            async with async_session() as session:
                lines = await service.list_foreshadowing_lines(
                    project_id,
                    session,
                    statuses=["planned", "active", "dormant", "revealing", "revised"],
                )

        status_rank = {"revealing": 0, "active": 1, "dormant": 2, "planned": 3, "revised": 4}
        priority_rank = {"critical": 0, "high": 1, "moderate": 2, "low": 3}
        lines.sort(
            key=lambda line: (
                status_rank.get(str(line.get("status") or ""), 9),
                priority_rank.get(str(line.get("priority") or ""), 9),
                str(line.get("name") or ""),
            )
        )
        return [
            {
                "id": str(line.get("id") or ""),
                "name": str(line.get("name") or "")[:80],
                "aliases": [str(alias)[:60] for alias in list(line.get("aliases") or [])[:5]],
                "status": str(line.get("status") or ""),
            }
            for line in lines[:48]
        ]

    async def extract_from_chapter(
        self,
        project_id: str,
        chapter_number: int,
        chapter_content: str,
        *,
        chapter_outline: str = "",
        db=None,
    ) -> dict:
        core_service = CoreMemoryService()

        known_chars = await core_service.list_characters(project_id)
        known_char_names = []
        for c in known_chars:
            c_data = c.model_dump() if hasattr(c, "model_dump") else c
            known_char_names.append(c_data.get("name", ""))
            known_char_names.extend(c_data.get("aliases", []))

        known_locs = await core_service.list_locations(project_id)
        known_loc_names = []
        for loc in known_locs:
            loc_name = loc.get("name", "")
            parent = loc.get("parent_location")
            if loc_name and parent:
                known_loc_names.append(f"{parent}>{loc_name}")
            elif loc_name:
                known_loc_names.append(loc_name)

        known_rules = await core_service.list_world_rules(project_id)
        known_rule_names = [r.get("name", "") for r in known_rules]

        # 方案 3 B3：获取已知物品列表
        known_items = await core_service.list_items(project_id)
        known_item_names = []
        for it in known_items:
            it_name = it.get("name", "") if isinstance(it, dict) else ""
            if it_name:
                known_item_names.append(it_name)
                known_item_names.extend(it.get("aliases", []) if isinstance(it, dict) else [])

        known_foreshadowing = await self._load_known_foreshadowing(project_id, db)

        try:
            from app.services.agent_config import AgentConfigManager
            from app.services.llm_client import LLMClient

            manager = AgentConfigManager()
            config = await manager.get_agent_config("worldbuilder")
            llm = LLMClient(
                api_format=config.api_format,
                api_key=config.api_key,
                base_url=config.base_url,
                model=config.model,
            )

            chunks = split_worldview_chunks(chapter_content)
            if not chunks:
                empty = self._empty_extraction()
                empty["_coverage"] = {"input_chars": 0, "covered_chars": 0, "chunk_count": 0, "failed_chunks": []}
                return empty

            semaphore = asyncio.Semaphore(min(2, len(chunks)))

            async def extract_chunk(chunk_index: int, chunk: str) -> tuple[int, dict | None]:
                prompt = EXTRACT_PROMPT_V2.format(
                    known_characters="、".join(known_char_names) if known_char_names else "无",
                    known_locations="、".join(known_loc_names) if known_loc_names else "无",
                    known_rules="、".join(known_rule_names) if known_rule_names else "无",
                    known_items="、".join(known_item_names) if known_item_names else "无",
                    known_foreshadowing=json.dumps(
                        known_foreshadowing,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ) if known_foreshadowing else "无",
                    chapter_outline=chapter_outline[:1000] if chapter_outline else "无",
                    chapter_content=chunk,
                )
                async with semaphore:
                    try:
                        response = await llm.generate(
                            system_prompt="你是世界观元素提取器。只根据提供的正文分块提取，不补写未出现的信息，只输出JSON。",
                            user_prompt=prompt,
                            temperature=0.2,
                            max_tokens=4096,
                            task_type=LLMTaskType.JSON_AUDIT,
                        )
                        parsed = (
                            self._parse_extraction_v2(response or "")
                            if response and response.strip()
                            else None
                        )
                        if parsed is None and response:
                            parsed = await self._try_repair_json_with_llm(llm, response)
                        return chunk_index, parsed
                    except Exception as exc:
                        logger.warning(
                            "[WorldviewExtractor] chunk %d extraction failed: %s",
                            chunk_index,
                            exc,
                        )
                        return chunk_index, None

            chunk_results = await asyncio.gather(*(
                extract_chunk(chunk_index, chunk)
                for chunk_index, chunk in enumerate(chunks)
            ))
            partials = [parsed for _index, parsed in chunk_results if parsed is not None]
            failed_chunks = [index for index, parsed in chunk_results if parsed is None]

            if failed_chunks:
                degraded = self._merge_chunk_extractions(partials)
                degraded["_degraded"] = True
                degraded["_degraded_reason"] = "chunk_extraction_incomplete"
                degraded["_coverage"] = {
                    "input_chars": len(chapter_content),
                    "covered_chars": sum(len(chunk) for index, chunk in enumerate(chunks) if index not in failed_chunks),
                    "chunk_count": len(chunks),
                    "failed_chunks": failed_chunks,
                }
                return degraded

            merged = self._merge_chunk_extractions(partials)
            merged["_coverage"] = {
                "input_chars": len(chapter_content),
                "covered_chars": sum(len(chunk) for chunk in chunks),
                "chunk_count": len(chunks),
                "failed_chunks": [],
            }
            return merged
        except WorldviewExtractionError:
            raise
        except Exception as e:
            logger.error("[WorldviewExtractor] extraction failed: %s", e)
            raise WorldviewExtractionError(str(e)) from e

    @staticmethod
    def _empty_extraction(*, degraded: bool = False, degraded_reason: str = "") -> dict:
        result = {
            "characters": [],
            "locations": [],
            "world_rules": [],
            "foreshadowing": [],
            "facts": [],
            # 方案 3 B3：物品类目
            "items": [],
        }
        if degraded:
            result["_degraded"] = True
            result["_degraded_reason"] = degraded_reason
        return result

    def _merge_chunk_extractions(self, partials: list[dict]) -> dict:
        merged = self._empty_extraction()
        for category in ("characters", "locations", "world_rules", "foreshadowing", "facts", "items"):
            by_key: dict[str, dict] = {}
            order: list[str] = []
            for partial in partials:
                for raw in partial.get(category, []) if isinstance(partial, dict) else []:
                    if not isinstance(raw, dict) or not raw.get("name"):
                        continue
                    key = normalize_entity_name(str(raw.get("name")))
                    if key not in by_key:
                        by_key[key] = dict(raw)
                        order.append(key)
                        continue
                    current = by_key[key]
                    current_confidence = float(current.get("confidence") or 0.0)
                    incoming_confidence = float(raw.get("confidence") or 0.0)
                    preferred, other = (raw, current) if incoming_confidence > current_confidence else (current, raw)
                    combined = dict(preferred)
                    for field, value in other.items():
                        if field == "aliases":
                            combined[field] = list(dict.fromkeys([*(combined.get(field) or []), *(value or [])]))
                        elif field == "relationships" and isinstance(value, dict):
                            combined[field] = {**value, **(combined.get(field) or {})}
                        elif not combined.get(field) and value not in (None, "", [], {}):
                            combined[field] = value
                    by_key[key] = combined
            merged[category] = [by_key[key] for key in order]
        return merged

    async def _try_repair_json_with_llm(self, llm, raw_response: str) -> dict | None:
        """Ask the LLM to fix its own malformed JSON output."""
        repair_prompt = f"""你之前输出的JSON格式有误，无法解析。请将以下内容修正为合法的JSON，严格遵循schema：
{{"characters": [...], "locations": [...], "world_rules": [...], "foreshadowing": [...], "facts": [...], "items": [...]}}

每个元素必须包含：name, aliases, operation, description, evidence_text, confidence。
只输出修正后的JSON，不要输出其他内容。

原始输出：
{raw_response[:3000]}"""

        try:
            repair_response = await llm.generate(
                system_prompt="你是JSON修复器。只输出合法JSON。",
                user_prompt=repair_prompt,
                temperature=0.1,
                max_tokens=4096,
                task_type=LLMTaskType.JSON_AUDIT,
            )
            if not repair_response or not repair_response.strip():
                return None
            return self._parse_extraction_v2(repair_response)
        except Exception as e:
            logger.warning("[WorldviewExtractor] LLM repair attempt failed: %s", e)
            return None

    async def write_to_shell(
        self, project_id: str, extractions: dict, chapter_number: int,
        *, observation_id: str | None = None,
    ) -> list[dict]:
        shell_service = ShellMemoryService()
        written = []

        def stable_seed_id(kind: str, identity: str) -> str:
            return str(uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"loreweft:{project_id}:{chapter_number}:worldview:{kind}:{identity}",
            ))

        for char in extractions.get("characters", []):
            try:
                seed = await shell_service.store_detail_seed(project_id, {
                    "id": stable_seed_id("character", f"{char.get('name', '')}:{char.get('evidence_text', '')}"),
                    "content": f"新人物：{char.get('name', '')} - {char.get('description', '')}",
                    "type": "character",
                    "tier": "T3",
                    "source_chapter": chapter_number,
                    "metadata": {
                        "name": char.get("name", ""),
                        "aliases": char.get("aliases", []),
                        "auto_extracted": True,
                        "confidence": char.get("confidence", 0.0),
                        "operation": char.get("operation", "new"),
                    },
                    "entity_type": "character",
                    "source_kind": "observation",
                    "source_observation_id": observation_id,
                })
                written.append(seed)
            except Exception as e:
                logger.warning(f"[WorldviewExtractor] failed to write character seed: {e}")

        for loc in extractions.get("locations", []):
            try:
                seed = await shell_service.store_detail_seed(project_id, {
                    "id": stable_seed_id("location", f"{loc.get('name', '')}:{loc.get('evidence_text', '')}"),
                    "content": f"新地点：{loc.get('name', '')} - {loc.get('description', '')}",
                    "type": "location",
                    "tier": "T3",
                    "source_chapter": chapter_number,
                    "metadata": {
                        "name": loc.get("name", ""),
                        "aliases": loc.get("aliases", []),
                        "auto_extracted": True,
                        "confidence": loc.get("confidence", 0.0),
                        "operation": loc.get("operation", "new"),
                    },
                    "entity_type": "location",
                    "source_kind": "observation",
                    "source_observation_id": observation_id,
                })
                written.append(seed)
            except Exception as e:
                logger.warning(f"[WorldviewExtractor] failed to write location seed: {e}")

        for rule in extractions.get("world_rules", []):
            try:
                seed = await shell_service.store_detail_seed(project_id, {
                    "id": stable_seed_id("rule", f"{rule.get('name', '')}:{rule.get('evidence_text', '')}"),
                    "content": f"规则暗示：{rule.get('name', '')} - {rule.get('description', '')}",
                    "type": "rule_hint",
                    "tier": "T3",
                    "source_chapter": chapter_number,
                    "metadata": {
                        "name": rule.get("name", ""),
                        "auto_extracted": True,
                        "confidence": rule.get("confidence", 0.0),
                        "operation": rule.get("operation", "new"),
                    },
                    "entity_type": "world_rule",
                    "source_kind": "observation",
                    "source_observation_id": observation_id,
                })
                written.append(seed)
            except Exception as e:
                logger.warning(f"[WorldviewExtractor] failed to write rule seed: {e}")

        for fs in extractions.get("foreshadowing", []):
            try:
                seed = await shell_service.store_detail_seed(project_id, {
                    "id": stable_seed_id("foreshadowing", f"{fs.get('name', '')}:{fs.get('evidence_text', '')}"),
                    "content": f"新伏笔：{fs.get('name', '')} - {fs.get('description', '')}",
                    "type": "foreshadowing",
                    "tier": "T3",
                    "source_chapter": chapter_number,
                    "metadata": {
                        "name": fs.get("name", ""),
                        "auto_extracted": True,
                        "confidence": fs.get("confidence", 0.0),
                        "operation": fs.get("operation", "new"),
                    },
                    "entity_type": "foreshadowing",
                    "source_kind": "observation",
                    "source_observation_id": observation_id,
                })
                written.append(seed)
            except Exception as e:
                logger.warning(f"[WorldviewExtractor] failed to write foreshadowing seed: {e}")

        for fact in extractions.get("facts", []):
            try:
                seed = await shell_service.store_detail_seed(project_id, {
                    "id": stable_seed_id("fact", f"{fact.get('name', '')}:{fact.get('evidence_text', '')}"),
                    "content": f"新事实：{fact.get('name', '')} - {fact.get('description', '')}",
                    "type": "fact",
                    "tier": "T3",
                    "source_chapter": chapter_number,
                    "metadata": {
                        "name": fact.get("name", ""),
                        "auto_extracted": True,
                        "confidence": fact.get("confidence", 0.0),
                        "operation": fact.get("operation", "new"),
                    },
                    "entity_type": "fact",
                    "source_kind": "observation",
                    "source_observation_id": observation_id,
                })
                written.append(seed)
            except Exception as e:
                logger.warning(f"[WorldviewExtractor] failed to write fact seed: {e}")

        # 方案 3 B3：物品类目写入 Shell 层
        for it in extractions.get("items", []):
            try:
                seed = await shell_service.store_detail_seed(project_id, {
                    "id": stable_seed_id("item", f"{it.get('name', '')}:{it.get('evidence_text', '')}"),
                    "content": f"新物品：{it.get('name', '')} - {it.get('description', '')}",
                    "type": "item",
                    "tier": "T3",
                    "source_chapter": chapter_number,
                    "metadata": {
                        "name": it.get("name", ""),
                        "aliases": it.get("aliases", []),
                        "auto_extracted": True,
                        "confidence": it.get("confidence", 0.0),
                        "operation": it.get("operation", "new"),
                        "appearance": it.get("appearance", ""),
                        "function": it.get("function", ""),
                        "owner": it.get("owner", ""),
                        "status": it.get("status", "intact"),
                        "importance": it.get("importance", "minor"),
                    },
                    "entity_type": "item",
                    "source_kind": "observation",
                    "source_observation_id": observation_id,
                })
                written.append(seed)
            except Exception as e:
                logger.warning(f"[WorldviewExtractor] failed to write item seed: {e}")

        return written

    def _parse_extraction_v2(self, response: str) -> dict:
        empty = {
            "characters": [],
            "locations": [],
            "world_rules": [],
            "foreshadowing": [],
            "facts": [],
            # 方案 3 B3：物品类目
            "items": [],
        }
        try:
            parsed = parse_json_response(response)
            if isinstance(parsed, dict) and any(
                key in parsed
                for key in ("characters", "locations", "world_rules", "foreshadowing", "facts", "items")
            ):
                return self._normalize_extraction(parsed, empty)
        except Exception:
            pass

        try:
            match = re.search(r'```json\s*(\{.*?\})\s*```', response, re.DOTALL)
            if match:
                parsed = json.loads(match.group(1))
                return self._normalize_extraction(parsed, empty)
        except (json.JSONDecodeError, Exception):
            pass

        try:
            first_brace = response.find('{')
            last_brace = response.rfind('}')
            if first_brace != -1 and last_brace > first_brace:
                candidate = response[first_brace:last_brace + 1]
                brace_depth = 0
                for i, ch in enumerate(candidate):
                    if ch == '{':
                        brace_depth += 1
                    elif ch == '}':
                        brace_depth -= 1
                        if brace_depth == 0:
                            sub = candidate[:i + 1]
                            try:
                                parsed = json.loads(sub)
                                if isinstance(parsed, dict) and any(k in parsed for k in ("characters", "locations", "world_rules", "foreshadowing", "facts")):
                                    return self._normalize_extraction(parsed, empty)
                            except json.JSONDecodeError:
                                continue
        except Exception:
            pass

        return None

    def _normalize_extraction(self, parsed: dict, empty: dict) -> dict:
        def clean_str(value) -> str:
            if value is None:
                return ""
            return str(value).strip()

        def clean_list(value) -> list:
            if isinstance(value, list):
                return [clean_str(v) for v in value if clean_str(v)]
            if isinstance(value, str) and value.strip():
                return [value.strip()]
            return []

        def clean_dict(value) -> dict:
            if not isinstance(value, dict):
                return {}
            return {clean_str(k): clean_str(v) for k, v in value.items() if clean_str(k) and clean_str(v)}

        def clean_float(value) -> float:
            try:
                return max(0.0, min(1.0, float(value)))
            except (TypeError, ValueError):
                return 0.0

        def clean_int(value):
            try:
                if value is None or value == "":
                    return None
                return int(value)
            except (TypeError, ValueError):
                return None

        result = {}
        for key in ("characters", "locations", "world_rules", "foreshadowing", "facts", "items"):
            items = parsed.get(key, [])
            if not isinstance(items, list):
                items = []
            normalized = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                normalized_item = {
                    "name": clean_str(item.get("name", "")),
                    "aliases": clean_list(item.get("aliases", [])),
                    "operation": clean_str(item.get("operation", "new")) or "new",
                    "description": clean_str(item.get("description", "")),
                    "evidence_text": clean_str(item.get("evidence_text", "")),
                    "confidence": clean_float(item.get("confidence", 0.0)),
                }

                if key == "characters":
                    for field in (
                        "appearance", "personality", "desire", "deep_need", "arc", "role", "faction",
                        "status", "lifecycle_status", "narrative_activity", "role_importance",
                    ):
                        value = clean_str(item.get(field))
                        if value:
                            normalized_item[field] = value
                    relationships = clean_dict(item.get("relationships"))
                    if relationships:
                        normalized_item["relationships"] = relationships
                elif key == "locations":
                    for field in ("location_type", "parent_location", "atmosphere", "function"):
                        value = clean_str(item.get(field))
                        if value:
                            normalized_item[field] = value
                    spatial_relations = clean_dict(item.get("spatial_relations"))
                    if spatial_relations:
                        normalized_item["spatial_relations"] = spatial_relations
                    rules = clean_list(item.get("rules"))
                    if rules:
                        normalized_item["rules"] = rules
                elif key == "foreshadowing":
                    foreshadowing_id = clean_str(item.get("foreshadowing_id") or item.get("core_entity_id"))
                    if foreshadowing_id:
                        normalized_item["foreshadowing_id"] = foreshadowing_id
                    for field in ("truth_type", "priority", "salience", "reader_intended_state", "clue_text"):
                        value = clean_str(item.get(field))
                        if value:
                            normalized_item[field] = value
                    for field in ("bury_window_start", "reveal_window_start"):
                        value = clean_int(item.get(field))
                        if value is not None:
                            normalized_item[field] = value
                    related = clean_list(item.get("related_characters"))
                    if related:
                        normalized_item["related_characters"] = related
                elif key == "items":
                    # 方案 3 B3：物品类目字段规范化
                    for field in ("appearance", "function", "origin", "owner", "status", "importance", "related_foreshadowing"):
                        value = clean_str(item.get(field))
                        if value:
                            normalized_item[field] = value
                    first_appear = clean_int(item.get("first_appear_chapter"))
                    if first_appear is not None:
                        normalized_item["first_appear_chapter"] = first_appear
                elif key in ("world_rules", "facts"):
                    for field in ("category", "source_scope"):
                        value = clean_str(item.get(field))
                        if value:
                            normalized_item[field] = value
                    for field in ("constraints", "related_entities"):
                        value = clean_list(item.get(field))
                        if value:
                            normalized_item[field] = value

                normalized.append(normalized_item)
            result[key] = normalized
        return result

    def _parse_extraction(self, response: str) -> dict:
        v2 = self._parse_extraction_v2(response)
        if v2 is None:
            return {
                "new_characters": [],
                "new_locations": [],
                "implied_rules": [],
            }
        return {
            "new_characters": v2.get("characters", []),
            "new_locations": v2.get("locations", []),
            "implied_rules": v2.get("world_rules", []),
        }
