import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent
from app.db.db_models import CoordinatorRun, CrossSystemProposal
from app.services.memory_core import CoreMemoryService
from app.services.foreshadowing_service import ForeshadowingService
from app.services.outline_index_service import OutlineIndexService
from app.services.worldview_digest import WorldviewDigestService
from app.services.cross_system_event_bus import CrossSystemEventBus
from app.services.cross_system_proposal_service import CrossSystemProposalService
from app.services.llm_client import LLMClient

logger = logging.getLogger(__name__)

WEAVE_TOOLS = [
    {
        "name": "weave_propose",
        "description": "提交跨系统提案。当你发现需要修改另一个系统的数据时使用。",
        "parameters": {
            "type": "object",
            "properties": {
                "target_domain": {"type": "string", "description": "world_rule/character/location/foreshadowing/outline_chapter/thread_plan"},
                "target_entity_type": {"type": "string"},
                "proposal_type": {"type": "string", "description": "new/modify/delete/supplement"},
                "title": {"type": "string"},
                "description": {"type": "string"},
                "proposal_data": {"type": "object"},
                "base_version": {"type": "string"},
            },
            "required": ["target_domain", "target_entity_type", "proposal_type", "title", "proposal_data"],
        },
    },
    {
        "name": "weave_check_conflicts",
        "description": "检查跨系统一致性冲突。",
        "parameters": {
            "type": "object",
            "properties": {
                "scope": {"type": "string", "description": "full/chapter_cross/rule_cross/character_cross/foreshadowing_cross"},
                "target_chapter": {"type": "integer"},
            },
        },
    },
    {
        "name": "weave_get_pending_proposals",
        "description": "获取待处理的跨系统提案列表。",
        "parameters": {
            "type": "object",
            "properties": {
                "target_domain": {"type": "string"},
            },
        },
    },
    {
        "name": "weave_review_proposal",
        "description": "审查跨系统提案。",
        "parameters": {
            "type": "object",
            "properties": {
                "proposal_id": {"type": "string"},
                "action": {"type": "string", "description": "approve/reject/modify"},
                "review_notes": {"type": "string"},
                "modified_data": {"type": "object"},
            },
            "required": ["proposal_id", "action"],
        },
    },
    {
        "name": "weave_get_impact_report",
        "description": "分析某实体变更的跨系统影响范围。",
        "parameters": {
            "type": "object",
            "properties": {
                "entity_type": {"type": "string"},
                "entity_id_or_name": {"type": "string"},
            },
            "required": ["entity_type", "entity_id_or_name"],
        },
    },
    {
        "name": "weave_check_coordination_status",
        "description": "获取当前项目的跨系统协调状态。",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "register_foreshadowing_reveal",
        "description": "标记某个伏笔已在当前章揭示。",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "chapter_number": {"type": "integer"},
                "reveal_note": {"type": "string"},
            },
            "required": ["name", "chapter_number"],
        },
    },
    {
        "name": "register_generated_setting",
        "description": "标记章节写作中自动产生的新设定想法。",
        "parameters": {
            "type": "object",
            "properties": {
                "setting_type": {"type": "string", "description": "rule/character/location"},
                "name": {"type": "string"},
                "description": {"type": "string"},
                "chapter_number": {"type": "integer"},
            },
            "required": ["setting_type", "name", "description", "chapter_number"],
        },
    },
]

CONFLICT_ANALYSIS_PROMPT = """你是「万象谱」跨系统协调分析引擎。你的任务是检测小说各子系统之间的一致性冲突。

## 可用数据

### 世界观规则
{world_rules}

### 大纲摘要
{outline_summary}

### 伏笔窗口
{foreshadowing_window}

### 人物弧光
{character_arcs}

## 检查范围
{scope_description}

## 输出格式

请以 JSON 格式输出冲突报告：
```json
{{
  "conflicts": [
    {{
      "conflict_type": "rule_vs_character|rule_vs_location|character_vs_foreshadowing|outline_vs_worldview|foreshadowing_timing",
      "severity": "critical|high|normal|low",
      "description": "冲突的具体描述",
      "involved_systems": ["world_rule", "character", ...],
      "involved_entities": ["实体名1", "实体名2"],
      "suggestion": "修复建议"
    }}
  ],
  "warnings": [
    {{
      "description": "潜在风险描述",
      "involved_entities": ["实体名"],
      "suggestion": "预防建议"
    }}
  ],
  "summary": "整体一致性评估摘要"
}}
```"""

IMPACT_ANALYSIS_PROMPT = """你是「万象谱」跨系统影响分析引擎。你的任务是分析某个实体变更后对其他子系统的影响范围。

## 变更实体
- 类型：{entity_type}
- 标识：{entity_id_or_name}

### 世界观规则
{world_rules}

### 大纲摘要
{outline_summary}

### 伏笔窗口
{foreshadowing_window}

### 人物弧光
{character_arcs}

## 输出格式

请以 JSON 格式输出影响报告：
```json
{{
  "direct_impacts": [
    {{
      "target_system": "world_rule|character|location|foreshadowing|outline",
      "target_entity": "受影响的实体名",
      "impact_type": "contradiction|constraint|dependency|narrative",
      "description": "影响描述",
      "severity": "critical|high|normal|low"
    }}
  ],
  "cascade_impacts": [
    {{
      "path": "A → B → C",
      "description": "级联影响描述",
      "severity": "high|normal|low"
    }}
  ],
  "affected_chapters": [1, 3, 5],
  "risk_level": "high|medium|low",
  "mitigation_suggestions": ["建议1", "建议2"]
}}
```"""


class WeaveCoordinatorAgent(BaseAgent):
    name = "weave_coordinator"

    async def execute(self, context: dict) -> dict:
        project_id = context.get("project_id")
        db = context.get("db")
        trigger_type = context.get("trigger_type", "manual")
        trigger_source = context.get("trigger_source", "")
        trigger_event = context.get("trigger_event", "")
        scope = context.get("scope", "full")
        target_chapter = context.get("target_chapter")

        if not project_id or not db:
            return {"error": "缺少 project_id 或 db"}

        run = CoordinatorRun(
            project_id=project_id,
            trigger_type=trigger_type,
            trigger_source=trigger_source,
            trigger_event=trigger_event,
            status="running",
        )
        db.add(run)
        await db.flush()

        started_at = datetime.now(timezone.utc)
        llm_calls = 0
        tokens_used = 0
        proposals_created = 0
        events_published = 0
        findings = []
        actions_taken = []

        try:
            conflict_report = await self.analyze_conflicts(
                db, project_id, scope, target_chapter
            )
            llm_calls += 1
            findings = conflict_report.get("conflicts", [])

            if findings:
                critical_conflicts = [
                    c for c in findings if c.get("severity") in ("critical", "high")
                ]
                if critical_conflicts:
                    event_bus = CrossSystemEventBus()
                    for conflict in critical_conflicts[:5]:
                        try:
                            await event_bus.publish_event(
                                db,
                                str(project_id),
                                "CONSISTENCY_ALERT.v1",
                                "weave_coordinator",
                                priority="high",
                                payload={
                                    "conflict_type": conflict.get("conflict_type", ""),
                                    "description": conflict.get("description", ""),
                                    "involved_entities": conflict.get("involved_entities", []),
                                    "suggestion": conflict.get("suggestion", ""),
                                },
                            )
                            events_published += 1
                        except Exception:
                            logger.warning("Failed to publish consistency alert", exc_info=True)

                    actions_taken.append(f"发布 {len(critical_conflicts)} 条一致性告警")

            summary = conflict_report.get("summary", "一致性检查完成")

            run.status = "completed"
            run.summary = summary
            run.findings = findings
            run.actions_taken = actions_taken
            run.proposals_created = proposals_created
            run.events_published = events_published
            run.llm_calls = llm_calls
            run.tokens_used = tokens_used
            run.completed_at = datetime.now(timezone.utc)
            duration = (datetime.now(timezone.utc) - started_at).total_seconds()
            run.duration_ms = int(duration * 1000)
            await db.flush()

            return {
                "run_id": str(run.id),
                "status": "completed",
                "conflicts": findings,
                "warnings": conflict_report.get("warnings", []),
                "summary": summary,
                "proposals_created": proposals_created,
                "events_published": events_published,
            }

        except Exception as e:
            logger.error("WeaveCoordinator execute failed: %s", e, exc_info=True)
            run.status = "failed"
            run.error_message = str(e)[:500]
            run.findings = findings
            run.actions_taken = actions_taken
            run.llm_calls = llm_calls
            run.tokens_used = tokens_used
            run.completed_at = datetime.now(timezone.utc)
            duration = (datetime.now(timezone.utc) - started_at).total_seconds()
            run.duration_ms = int(duration * 1000)
            await db.flush()

            return {
                "run_id": str(run.id),
                "status": "failed",
                "error": str(e),
                "conflicts": findings,
            }

    async def analyze_conflicts(
        self,
        db: AsyncSession,
        project_id,
        scope: str,
        target_chapter: Optional[int] = None,
    ) -> dict:
        pid = str(project_id)

        try:
            core_service = CoreMemoryService()
            rules = await core_service.list_world_rules(pid)
            characters = await core_service.list_characters(pid)
            locations = await core_service.list_locations(pid)
            foreshadowing = await core_service.list_foreshadowing(pid)
        except Exception:
            rules = []
            characters = []
            locations = []
            foreshadowing = []

        try:
            digest_service = WorldviewDigestService()
            world_rules_text = await digest_service.get_digest(pid, db)
        except Exception:
            world_rules_text = self._serialize_rules_brief(rules, characters, locations)

        try:
            index_service = OutlineIndexService()
            outline_summary = await index_service.get_index(pid, db)
            if not outline_summary:
                outline_summary = "暂无大纲数据"
        except Exception:
            outline_summary = "暂无大纲数据"

        try:
            fs_service = ForeshadowingService()
            fs_lines = await fs_service.list_lines(pid, db)
            foreshadowing_window = self._format_foreshadowing_window(fs_lines, target_chapter)
        except Exception:
            foreshadowing_window = self._format_foreshadowing_brief(foreshadowing, target_chapter)

        character_arcs = self._format_character_arcs(characters)

        scope_descriptions = {
            "full": "全面检查所有子系统间的一致性",
            "chapter_cross": f"检查第{target_chapter or '?'}章涉及的跨系统一致性",
            "rule_cross": "检查世界规则与其他系统的一致性",
            "character_cross": "检查人物设定与其他系统的一致性",
            "foreshadowing_cross": "检查伏笔系统与其他系统的一致性",
        }
        scope_description = scope_descriptions.get(scope, scope)

        prompt = CONFLICT_ANALYSIS_PROMPT.format(
            world_rules=world_rules_text or "暂无世界观数据",
            outline_summary=outline_summary,
            foreshadowing_window=foreshadowing_window or "暂无伏笔数据",
            character_arcs=character_arcs or "暂无人物数据",
            scope_description=scope_description,
        )

        try:
            llm = await self.get_llm_client()
            response = await llm.generate(
                system_prompt=prompt,
                user_prompt="请执行一致性分析，输出 JSON 格式的冲突报告。",
                temperature=0.3,
                max_tokens=4096,
            )
            return self._parse_json_response(response)
        except Exception as e:
            logger.error("analyze_conflicts LLM call failed: %s", e, exc_info=True)
            return {
                "conflicts": [],
                "warnings": [],
                "summary": f"分析失败：{str(e)[:200]}",
            }

    async def analyze_impact(
        self,
        db: AsyncSession,
        project_id,
        entity_type: str,
        entity_id_or_name: str,
    ) -> dict:
        pid = str(project_id)

        try:
            core_service = CoreMemoryService()
            rules = await core_service.list_world_rules(pid)
            characters = await core_service.list_characters(pid)
            locations = await core_service.list_locations(pid)
            foreshadowing = await core_service.list_foreshadowing(pid)
        except Exception:
            rules = []
            characters = []
            locations = []
            foreshadowing = []

        try:
            digest_service = WorldviewDigestService()
            world_rules_text = await digest_service.get_digest(pid, db)
        except Exception:
            world_rules_text = self._serialize_rules_brief(rules, characters, locations)

        try:
            index_service = OutlineIndexService()
            outline_summary = await index_service.get_index(pid, db)
            if not outline_summary:
                outline_summary = "暂无大纲数据"
        except Exception:
            outline_summary = "暂无大纲数据"

        try:
            fs_service = ForeshadowingService()
            fs_lines = await fs_service.list_lines(pid, db)
            foreshadowing_window = self._format_foreshadowing_window(fs_lines, None)
        except Exception:
            foreshadowing_window = self._format_foreshadowing_brief(foreshadowing, None)

        character_arcs = self._format_character_arcs(characters)

        prompt = IMPACT_ANALYSIS_PROMPT.format(
            entity_type=entity_type,
            entity_id_or_name=entity_id_or_name,
            world_rules=world_rules_text or "暂无世界观数据",
            outline_summary=outline_summary,
            foreshadowing_window=foreshadowing_window or "暂无伏笔数据",
            character_arcs=character_arcs or "暂无人物数据",
        )

        try:
            llm = await self.get_llm_client()
            response = await llm.generate(
                system_prompt=prompt,
                user_prompt=f"请分析实体「{entity_id_or_name}」（类型：{entity_type}）变更后的跨系统影响，输出 JSON 格式的影响报告。",
                temperature=0.3,
                max_tokens=4096,
            )
            return self._parse_json_response(response)
        except Exception as e:
            logger.error("analyze_impact LLM call failed: %s", e, exc_info=True)
            return {
                "direct_impacts": [],
                "cascade_impacts": [],
                "affected_chapters": [],
                "risk_level": "unknown",
                "mitigation_suggestions": [],
            }

    async def check_proposal_compatibility(
        self,
        db: AsyncSession,
        project_id,
        proposal_data: dict,
        target_domain: str,
    ) -> dict:
        pid = str(project_id)

        try:
            core_service = CoreMemoryService()
            rules = await core_service.list_world_rules(pid)
            characters = await core_service.list_characters(pid)
            locations = await core_service.list_locations(pid)
        except Exception:
            return {"compatible": True, "warnings": [], "conflicts": []}

        conflicts = []
        warnings = []

        if target_domain in ("world_rule", "worldview"):
            proposal_name = proposal_data.get("name", "")
            for rule in rules:
                if rule.get("name") == proposal_name:
                    conflicts.append({
                        "type": "duplicate_name",
                        "message": f"已存在同名规则：{proposal_name}",
                    })
                if rule.get("priority") == "critical":
                    new_constraints = proposal_data.get("constraints", [])
                    rule_constraints = rule.get("constraints", [])
                    for nc in new_constraints:
                        for rc in rule_constraints:
                            if self._is_constraint_conflict(nc, rc):
                                conflicts.append({
                                    "type": "constraint_conflict",
                                    "message": f"与宪法级规则「{rule.get('name')}」冲突",
                                })

        elif target_domain in ("character",):
            proposal_name = proposal_data.get("name", "")
            for c in characters:
                c_data = c.model_dump() if hasattr(c, "model_dump") else c
                if c_data.get("name") == proposal_name:
                    conflicts.append({
                        "type": "duplicate_name",
                        "message": f"已存在同名人物：{proposal_name}",
                    })

        elif target_domain in ("location",):
            proposal_name = proposal_data.get("name", "")
            for loc in locations:
                if loc.get("name") == proposal_name:
                    conflicts.append({
                        "type": "duplicate_name",
                        "message": f"已存在同名地点：{proposal_name}",
                    })

        return {
            "compatible": len(conflicts) == 0,
            "conflicts": conflicts,
            "warnings": warnings,
        }

    async def handle_tool(
        self,
        tool_name: str,
        tool_args: dict,
        project_id,
        db: AsyncSession,
        caller: str = "",
    ) -> dict:
        pid = str(project_id)

        if tool_name == "weave_propose":
            proposal_service = CrossSystemProposalService()
            proposal = await proposal_service.create_proposal(
                db=db,
                project_id=pid,
                source_system=caller or "weave_coordinator",
                target_domain=tool_args.get("target_domain", ""),
                target_entity_type=tool_args.get("target_entity_type", ""),
                proposal_type=tool_args.get("proposal_type", ""),
                title=tool_args.get("title", ""),
                description=tool_args.get("description", ""),
                proposal_data=tool_args.get("proposal_data", {}),
                base_version=tool_args.get("base_version"),
            )
            if proposal:
                return {
                    "proposal_id": str(proposal.id),
                    "status": proposal.status,
                    "title": proposal.title,
                    "target_domain": proposal.target_domain,
                }
            return {"error": "提案创建失败"}

        elif tool_name == "weave_check_conflicts":
            scope = tool_args.get("scope", "full")
            target_chapter = tool_args.get("target_chapter")
            return await self.analyze_conflicts(db, project_id, scope, target_chapter)

        elif tool_name == "weave_get_pending_proposals":
            proposal_service = CrossSystemProposalService()
            target_domain = tool_args.get("target_domain", "")
            proposals = await proposal_service.get_pending_for_system(
                db, pid, target_domain
            )
            return {
                "proposals": [
                    {
                        "id": str(p.id),
                        "title": p.title,
                        "target_domain": p.target_domain,
                        "proposal_type": p.proposal_type,
                        "description": p.description,
                        "status": p.status,
                        "created_at": p.created_at.isoformat() if p.created_at else None,
                    }
                    for p in proposals
                ],
                "count": len(proposals),
            }

        elif tool_name == "weave_review_proposal":
            proposal_service = CrossSystemProposalService()
            proposal_id = tool_args.get("proposal_id", "")
            action = tool_args.get("action", "")
            review_notes = tool_args.get("review_notes", "")
            modified_data = tool_args.get("modified_data")

            if action == "modify" and modified_data:
                action = "approve"
            elif action not in ("approve", "reject"):
                return {"error": f"不支持的操作：{action}"}

            proposal = await proposal_service.review_proposal(
                db=db,
                proposal_id=proposal_id,
                action=action,
                reviewed_by=caller or "weave_coordinator",
                review_notes=review_notes,
                modified_data=modified_data if action == "approve" else None,
            )
            if proposal:
                return {
                    "proposal_id": str(proposal.id),
                    "status": proposal.status,
                    "reviewed_by": proposal.reviewed_by,
                }
            return {"error": "提案审查失败，可能不存在或已处理"}

        elif tool_name == "weave_get_impact_report":
            entity_type = tool_args.get("entity_type", "")
            entity_id_or_name = tool_args.get("entity_id_or_name", "")
            return await self.analyze_impact(db, project_id, entity_type, entity_id_or_name)

        elif tool_name == "weave_check_coordination_status":
            from app.services.coordination_read_model_service import CoordinationReadModelService
            read_service = CoordinationReadModelService()
            status = await read_service.get_status_summary(db, pid, caller or "weave_coordinator")
            return status

        elif tool_name == "register_foreshadowing_reveal":
            name = tool_args.get("name", "")
            chapter_number = tool_args.get("chapter_number", 0)
            reveal_note = tool_args.get("reveal_note", "")
            try:
                fs_service = ForeshadowingService()
                lines = await fs_service.list_lines(pid, db)
                target_line = None
                for line in lines:
                    line_data = line if isinstance(line, dict) else {}
                    if line_data.get("name") == name:
                        target_line = line
                        break
                if target_line:
                    return {
                        "status": "registered",
                        "name": name,
                        "chapter_number": chapter_number,
                        "note": "伏笔揭示已记录（占位实现）",
                    }
                return {"status": "not_found", "name": name, "note": f"未找到伏笔：{name}"}
            except Exception as e:
                return {"error": f"伏笔揭示注册失败：{str(e)[:200]}"}

        elif tool_name == "register_generated_setting":
            setting_type = tool_args.get("setting_type", "")
            name = tool_args.get("name", "")
            description = tool_args.get("description", "")
            chapter_number = tool_args.get("chapter_number", 0)

            domain_map = {
                "rule": "world_rule",
                "character": "character",
                "location": "location",
            }
            target_domain = domain_map.get(setting_type)
            if not target_domain:
                return {"error": f"未知的设定类型：{setting_type}，支持的类型：rule/character/location"}

            proposal_service = CrossSystemProposalService()
            proposal = await proposal_service.create_proposal(
                db=db,
                project_id=pid,
                source_system=caller or "chapter_writer",
                target_domain=target_domain,
                target_entity_type=setting_type,
                proposal_type="supplement",
                title=f"[章节{chapter_number}产出] {name}",
                description=description,
                proposal_data={
                    "name": name,
                    "description": description,
                    "setting_type": setting_type,
                    "chapter_number": chapter_number,
                    "auto_generated": True,
                },
            )
            if proposal:
                return {
                    "proposal_id": str(proposal.id),
                    "status": proposal.status,
                    "name": name,
                    "note": "已自动转为跨系统提案",
                }
            return {"error": "设定注册失败"}

        return {"error": f"未知工具：{tool_name}"}

    @staticmethod
    def _serialize_rules_brief(rules, characters, locations) -> str:
        parts = []
        if rules:
            parts.append("### 世界规则")
            for r in rules[:10]:
                parts.append(f"- [{r.get('priority', '')}][{r.get('category', '')}] {r.get('name', '')}：{r.get('description', '')[:80]}")
        if characters:
            parts.append("### 人物")
            for c in characters[:8]:
                c_data = c.model_dump() if hasattr(c, "model_dump") else c
                parts.append(f"- {c_data.get('name', '')}：{c_data.get('desire', '')[:40]}")
        if locations:
            parts.append("### 地点")
            for loc in locations[:8]:
                parts.append(f"- {loc.get('name', '')}（{loc.get('atmosphere', '')}）")
        return "\n".join(parts) if parts else "暂无数据"

    @staticmethod
    def _format_foreshadowing_window(fs_lines, target_chapter) -> str:
        if not fs_lines:
            return "暂无伏笔数据"
        parts = []
        for line in fs_lines:
            if isinstance(line, dict):
                name = line.get("name", "")
                status = line.get("status", "")
                bury_start = line.get("bury_window_start", "?")
                bury_end = line.get("bury_window_end", "?")
                reveal_start = line.get("reveal_window_start", "?")
                reveal_end = line.get("reveal_window_end", "?")
            else:
                name = getattr(line, "name", "")
                status = getattr(line, "status", "")
                bury_start = getattr(line, "bury_window_start", "?")
                bury_end = getattr(line, "bury_window_end", "?")
                reveal_start = getattr(line, "reveal_window_start", "?")
                reveal_end = getattr(line, "reveal_window_end", "?")
            parts.append(f"- 「{name}」[{status}] 埋:{bury_start}-{bury_end} 揭:{reveal_start}-{reveal_end}")
        return "\n".join(parts)

    @staticmethod
    def _format_foreshadowing_brief(foreshadowing, target_chapter) -> str:
        if not foreshadowing:
            return "暂无伏笔数据"
        parts = []
        for f in foreshadowing[:15]:
            name = f.get("name", "")
            status = f.get("status", "active")
            plant = f.get("bury_window_start", "?")
            reveal = f.get("reveal_window_start", "?")
            parts.append(f"- 「{name}」[{status}] 第{plant}章埋→第{reveal}章揭")
        return "\n".join(parts)

    @staticmethod
    def _format_character_arcs(characters) -> str:
        if not characters:
            return "暂无人物数据"
        parts = []
        for c in characters[:10]:
            c_data = c.model_dump() if hasattr(c, "model_dump") else c
            name = c_data.get("name", "")
            desire = c_data.get("desire", "")[:40]
            arc = c_data.get("arc", "")[:40]
            personality = c_data.get("personality", "")[:30]
            line = f"- {name}"
            if desire:
                line += f" | 欲望：{desire}"
            if arc:
                line += f" | 弧光：{arc}"
            if personality:
                line += f" | 性格：{personality}"
            parts.append(line)
        return "\n".join(parts)

    @staticmethod
    def _parse_json_response(response: str) -> dict:
        if not response:
            return {"conflicts": [], "warnings": [], "summary": "空响应"}
        try:
            import re
            matches = re.findall(r"```json\s*(\{.*?\})\s*```", response, re.DOTALL)
            if matches:
                return json.loads(matches[0])
        except (json.JSONDecodeError, Exception):
            pass
        try:
            brace_start = response.find("{")
            brace_end = response.rfind("}")
            if brace_start >= 0 and brace_end > brace_start:
                return json.loads(response[brace_start:brace_end + 1])
        except (json.JSONDecodeError, Exception):
            pass
        return {
            "conflicts": [],
            "warnings": [],
            "summary": response[:500],
        }

    @staticmethod
    def _is_constraint_conflict(constraint_a: str, constraint_b: str) -> bool:
        negation_words = ["不能", "不可", "禁止", "无法", "绝不", "不得", "严禁"]
        for neg in negation_words:
            if neg in constraint_a and neg not in constraint_b:
                a_core = constraint_a.replace(neg, "").strip()
                if a_core and a_core in constraint_b:
                    return True
            if neg in constraint_b and neg not in constraint_a:
                b_core = constraint_b.replace(neg, "").strip()
                if b_core and b_core in constraint_a:
                    return True
        return False
