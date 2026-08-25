import asyncio
import copy
import json
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.base import BaseAgent
from app.db.db_models import Chapter, Project
from app.services.llm_client import LLMClient
from app.services.llm_task_profiles import LLMTaskType, get_profile
from app.services.text_coercion import to_search_text

logger = logging.getLogger(__name__)

EDITOR_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_chapter_outline",
            "description": "从大纲索引服务获取指定章节的完整大纲详情。理解创作意图或规划场景前必须先调用此工具。",
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter_number": {
                        "type": "integer",
                        "description": "章节号",
                    }
                },
                "required": ["chapter_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_outline",
            "description": "全文搜索大纲内容，查找涉及特定关键词、角色、事件的章节。当用户问'哪些章节涉及XX'时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词",
                    }
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_foreshadowing_status",
            "description": "查询指定伏笔线的完整状态：埋设章节、回收章节、当前待收数量。用户提到伏笔时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "伏笔线名称",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_character",
            "description": "查询人物详情：性格、欲望、深层需求、人物弧光阶段。用户询问角色表现或行为动机时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "角色名称（支持模糊匹配）",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_world_rules",
            "description": "查询世界规则详情，按类别筛选（如 magic/combat/technology/society/history）。检查世界观约束时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "规则类别，如 magic/combat/technology/society/history/general",
                    }
                },
                "required": ["category"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_consistency",
            "description": "一致性检查：验证生成内容是否与世界观铁则、当前状态冲突。用户担心内容矛盾时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "dimension": {
                        "type": "string",
                        "description": "检查维度，如 world_rule/character/plot/timeline",
                    },
                    "data": {
                        "type": "string",
                        "description": "需要检查的内容描述",
                    },
                },
                "required": ["dimension", "data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_chapter_content",
            "description": "获取已生成章节的原文片段，用于参考前文细节。可指定起止行号。",
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter_number": {
                        "type": "integer",
                        "description": "章节号",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "起始行号（可选，默认1）",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "结束行号（可选，默认100）",
                    },
                },
                "required": ["chapter_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_chapter_summary",
            "description": "获取指定章节的摘要内容，快速了解该章核心事件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter_number": {
                        "type": "integer",
                        "description": "章节号",
                    }
                },
                "required": ["chapter_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_summaries",
            "description": "获取最近N章的摘要列表，了解近期剧情进展。用户问前文进展时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "获取最近几章的摘要（默认3）",
                    }
                },
                "required": ["count"],
            },
        },
    },
    {
        "type": "function",
        "function": {
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
    },
    {
        "type": "function",
        "function": {
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
    },
    {
        "type": "function",
        "function": {
            "name": "weave_get_pending_proposals",
            "description": "获取待处理的跨系统提案列表。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_domain": {"type": "string"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
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
    },
    {
        "type": "function",
        "function": {
            "name": "weave_check_coordination_status",
            "description": "获取当前项目的跨系统协调状态。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
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
    },
    {
        "type": "function",
        "function": {
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
    },
    {
        "type": "function",
        "function": {
            "name": "propose_scene_contracts",
            "description": "为指定章节生成结构化场景合同列表。在规划生成时必须调用此工具，输出严格 JSON 格式的 scene_contract 列表，并附带轻量章节节奏地图。",
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter_number": {
                        "type": "integer",
                        "description": "章节号",
                    },
                    "chapter_rhythm_map": {
                        "type": "string",
                        "description": "200字以内的章节节奏地图：概括本章各场景的位置、节奏、信息释放边界和结尾气口；若有信件/纸条/物证，标注只给碎片线索，不一次性解释完整阴谋。不要复述完整合同。",
                    },
                    "scene_contracts": {
                        "type": "array",
                        "description": "场景合同列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "scene_id": {"type": "string", "description": "场景ID，格式 c{章号}-s{场景号}"},
                                "time_span": {"type": "string", "description": "场景时间跨度，如'5-8分钟'"},
                                "pov_character": {"type": "string", "description": "视角角色"},
                                "pov_lock": {"type": "string", "description": "视角锁定规则，如'仅限该角色的感知和思维，不得描写其他角色内心'"},
                                "temporal_anchor": {"type": "string", "description": "场景起始时间锚点，如'三月初七·卯时'或'上一场景结束后约一刻钟'"},
                                "location_anchor": {"type": "string", "description": "场景地理位置概要。若已填写current_location/destination_location/distance_state，此字段可自动生成"},
                                "current_location": {"type": "string", "description": "POV角色当前所在地点"},
                                "destination_location": {"type": "string", "description": "POV角色目的地"},
                                "distance_state": {"type": "string", "description": "当前与目的地的距离关系"},
                                "opening_state": {"type": "string", "description": "本场景开头时POV角色的位置、正在做什么、上一场景已完成什么。Writer必须从此状态开始写，不得回溯"},
                                "forbidden_recap_events": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "已完成事件列表，本场景绝对不得重写这些事件，只能一句话以内提及",
                                },
                                "clues": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "description": {"type": "string", "description": "读者可感知的线索描述"},
                                            "source_actor": {"type": "string", "description": "线索来源或放置者"},
                                            "placement_time": {"type": "string", "description": "线索产生或放置时间"},
                                            "discovery_condition": {"type": "string", "description": "POV角色能够发现线索的条件"},
                                        },
                                        "required": ["description", "source_actor"],
                                    },
                                    "description": "本场景出现的线索/遗留物/暗号列表。每条线索必须声明来源和放置者，防止凭空出现无来源的线索",
                                },
                                "scene_provenance": {
                                    "type": "object",
                                    "description": "统一事实层，建议按 current_facts / original_facts / spatial_anchor / timeline_anchor / clues 组织，用于后续生成与审稿共享同一份事实源",
                                },
                                "goal": {"type": "string", "description": "场景目标"},
                                "conflict": {"type": "string", "description": "场景冲突"},
                                "must_show": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "必须展示的关键画面/事件。每个场景最多放 5 条硬展示项；额外内容应改写进节奏地图或轻提示，不要堆进硬合同。",
                                },
                                "forbidden": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "绝对不能写的内容",
                                },
                                "ending_state": {"type": "string", "description": "场景结束时的状态"},
                                "genre_enrichment": {
                                    "type": "object",
                                    "description": "题材扩展字段，由当前 Genre Profile 的 contract_extensions 决定。例如旅程型题材可填 current_location/destination_location/distance_state，推理型题材可填 evidence_state/reader_known_facts 等",
                                },
                                "style_directive": {
                                    "type": "object",
                                    "description": "本场景的文笔风格执行策略。增量原则：只写本场景相对全局风格的差异。可包含 scene_style_role(inherit/intensify/relax/contrast)、reason、skeleton(节奏/信息密度等)、avoid(禁用表达) 等",
                                },
                            },
                            "required": ["scene_id", "pov_character", "goal", "conflict", "must_show", "forbidden", "ending_state"],
                        },
                    },
                },
                "required": ["chapter_number", "scene_contracts"],
            },
        },
    },
]

STATIC_RULES = """你是「万象谱」主编，创作流程的协调者和决策者。

## 职责
- 理解创作意图、增强场景合同、输出结构化场景合同、协调生成
- 不负责：直接撰写正文、修改大纲、修改世界观、重新规划场景目标
- 生成内容必须在大纲划定的走廊内，不得偏离后续章节需要的路径

## 工具使用
- 创作意图 → get_chapter_outline → 规划场景
- 角色问题 → get_character + get_world_rules
- 伏笔问题 → get_foreshadowing_status
- 前文进展 → get_recent_summaries
- 一致性疑虑 → check_consistency
- 获取到信息后立即回复，不要继续调用工具

## 场景合同（分层原则）
- 规划场景时必须调用 propose_scene_contracts
- 每个合同必须包含：scene_id/pov_character/goal/conflict/must_show/forbidden/ending_state
- 信息预算：每个场景的 must_show 最多 5 条硬展示项；第 6-8 条只能作为 soft_guidance，更多内容必须 deferred，不得把整章信息一次性塞进单场景
- 调用 propose_scene_contracts 时必须额外填写 chapter_rhythm_map：200字以内，用 S1/S2... 标注每个场景的章节位置、节奏快慢、信息释放边界和结尾气口；若本章有信件/纸条/遗书/药方/物证，必须标注“只给碎片线索，不一次性解释完整阴谋”；它不是合同复述，而是给 Writer 的全章节奏地图
- 合同分为三层：
  - source_of_truth：来自大纲的不可改字段（goal/conflict/outline_outcome/required_context_refs/must_show_outline/forbidden_outline）
  - editor_enrichment：主编可补字段（pov_lock/temporal_anchor/opening_state/forbidden_recap_events/clues/空间信息）
  - scene_provenance：统一事实层，后续生成和审稿都以它为准

## source_of_truth 不可改规则
- goal 和 conflict 来自大纲的 scene_briefs，主编不得修改
- outline_outcome 来自大纲的 value_shift，主编不得改变；ending_state 由主编补齐，但不得偏离 outline_outcome
- must_show_outline 和 forbidden_outline 来自大纲的 thread_ops 和 generation_corridors，主编不得删除
- 主编只能通过 additional_must_show 和 additional_forbidden 添加额外约束

## editor_enrichment 补充规则
- pov_lock：指定视角锁定规则
- temporal_anchor：指定场景起始时间锚点
- opening_state：本场景开头时POV角色在哪里、正在做什么
- forbidden_recap_events：已完成事件列表，本场景不得重写
- clues：本场景出现的线索/遗留物/暗号列表，每条必须声明来源和放置者
- current_location / destination_location / distance_state / location_anchor：空间信息

## scene_provenance
- 统一事实层，优先按 current_facts / original_facts / spatial_anchor / timeline_anchor / clues 组织
- 由系统自动构建，主编不需要手动填写

## 前瞻约束
- 不得提前揭示后续章节才应揭示的伏笔
- 不得杀死或移除后续章节需要出场的角色

## 输出规范
- 对话阶段：自由讨论，给出建议
- 场景规划时：输出 JSON 格式的场景合同
- 生成确认时：只用简短自然语言确认；不要暴露内部调度指令、Agent 派发过程或工具清单
"""

SYSTEM_PROMPT_BUDGET = 65000
LAYER0_BUDGET_RATIO = 0.15
LAYER1_BUDGET_RATIO = 0.08
LAYER2_BUDGET_RATIO = 0.10
LAYER3_BUDGET_RATIO = 0.05
LAYER4_BUDGET_RATIO = 0.15


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other_chars = len(text) - chinese_chars
    return int(chinese_chars * 1.5 + other_chars * 0.4)


@dataclass
class EditorState:
    project_id: str | None = None
    chapter_number: int | None = None
    project: Project | None = None
    core_data: dict = field(default_factory=dict)
    called_tools: set = field(default_factory=set)
    scene_contracts: list = field(default_factory=list)
    chapter_rhythm_map: str = ""
    active_style_profile: dict | None = None
    genre_profile: dict = field(default_factory=dict)
    quality_memory: dict = field(default_factory=dict)
    feature_policy: dict | None = None
    outline_snapshot: dict = field(default_factory=dict)
    # P2-19：方案 26 Part B2 style_directive 校验失败重试计数器。
    # 主编在 _tool_propose_scene_contracts 中提交场景合同后，若 style_directive
    # 校验失败，会返回错误提示主编补字段。计数器记录连续失败次数，
    # 达到 MAX_STYLE_DIRECTIVE_RETRIES 后回退到默认 style_directive。
    style_directive_retry_count: int = 0


class EditorInChiefAgent(BaseAgent):
    name = "editor_in_chief"
    MAX_TOOL_ITERATIONS = 8

    @staticmethod
    def _chapter_detail_from_outline(outline: dict, chapter_number: int) -> dict:
        if not isinstance(outline, dict):
            return {}
        chapter = {}
        for item in outline.get("chapter_spine", []) or []:
            if isinstance(item, dict) and item.get("chapter_number") == chapter_number:
                chapter = dict(item)
                break
        if not chapter:
            for item in outline.get("chapters", []) or []:
                if isinstance(item, dict) and item.get("chapter_number") == chapter_number:
                    chapter = dict(item)
                    break
        chapter_id = str(chapter.get("chapter_id") or f"ch_{chapter_number:03d}")
        briefs = outline.get("scene_briefs", {})
        brief = briefs.get(chapter_id) if isinstance(briefs, dict) else None
        if isinstance(brief, dict):
            chapter["scene_brief"] = copy.deepcopy(brief)
            chapter["scenes"] = copy.deepcopy(brief.get("scenes", chapter.get("scenes", [])))
        return chapter

    async def execute(self, context: dict) -> dict:
        messages = context.get("messages", [])
        project_id = context.get("project_id")
        db = context.get("db")
        chapter_number = context.get("chapter_number")
        deadline = context.get("deadline")  # absolute deadline timestamp (monotonic)
        execution_id = context.get("execution_id")  # for intermediate state persistence
        project_info = context.get("project_info", {})

        state = EditorState(
            project_id=project_id,
            chapter_number=chapter_number,
            outline_snapshot=copy.deepcopy(context.get("outline_snapshot") or {}),
        )

        if project_id and db:
            try:
                from uuid import UUID as UUIDType
                pid = project_id
                if isinstance(pid, str):
                    pid = UUIDType(pid)
                state.project = await db.get(Project, pid)
                if state.project:
                    state.core_data = state.project.core_data or {}
            except Exception as e:
                logger.warning(f"[EditorInChief] 加载项目数据失败: {e}")

        # 读取 Genre Profile
        if project_id and db:
            try:
                from app.services.genre_profile_service import GenreProfileService
                genre_svc = GenreProfileService()
                state.genre_profile = await genre_svc.get_project_genre_profile(
                    project_id, db=db, core_data=state.core_data,
                )
            except Exception as e:
                logger.warning(f"[EditorInChief] 加载 Genre Profile 失败: {e}")

        # 读取 Active Style Profile
        if project_id:
            try:
                from app.services.memory_core import CoreMemoryService
                memory_core = CoreMemoryService()
                active_style = await memory_core.get_active_style_profile(project_id)
                if active_style:
                    state.active_style_profile = active_style
            except Exception as e:
                logger.warning(f"[EditorInChief] 加载 Active Style Profile 失败: {e}")

        # 读取 Feature Policy 和质量记忆
        if state.project:
            try:
                from app.services.generation_feature_policy import resolve_generation_feature_policy
                state.feature_policy = resolve_generation_feature_policy(state.project)
            except Exception as e:
                logger.warning(f"[EditorInChief] 加载 Feature Policy 失败: {e}")

            try:
                quality_mem = (state.core_data or {}).get("quality_memory", {})
                if isinstance(quality_mem, dict):
                    state.quality_memory = quality_mem
            except Exception as e:
                logger.warning(f"[EditorInChief] 加载质量记忆失败: {e}")

        # 预加载规划上下文，减少 Agent Loop 中的工具调用轮次
        # 方案2接入：通过 UnifiedContextBuilder 统一获取数据层（大纲/伏笔/人物/规则/摘要）
        preloaded_context = []
        if project_id and db and chapter_number:
            try:
                from app.services.unified_context_builder import UnifiedContextBuilder
                ucb = UnifiedContextBuilder()
                editor_ctx = await ucb.build_editor_context(
                    str(project_id), chapter_number, db=db
                )

                # 章节大纲
                outline = self._chapter_detail_from_outline(
                    state.outline_snapshot, chapter_number,
                ) or editor_ctx.outline or {}
                if outline and outline.get("chapter_number"):
                    preloaded_context.append(
                        f"## 当前章节大纲（第{chapter_number}章）\n"
                        f"{json.dumps(outline, ensure_ascii=False, default=str)[:3000]}"
                    )

                # 前瞻约束
                lookahead = editor_ctx.lookahead or {}
                if lookahead and lookahead.get("future_chapters"):
                    preloaded_context.append(
                        "## 前瞻约束（后续章节需求）\n" +
                        "\n".join(
                            f"- 第{fc.get('chapter_number', '?')}章 {fc.get('title', '')}: "
                            f"{fc.get('main_conflict', '')[:80]}"
                            for fc in lookahead.get("future_chapters", [])[:3]
                        )
                    )

                # Core 层：人物/规则/伏笔（从 core 提取摘要级，主编不需要完整卡）
                core = editor_ctx.core or {}
                characters = core.get("characters") or []
                if characters:
                    char_summary = []
                    for ch in characters[:10]:
                        if isinstance(ch, dict):
                            char_summary.append(f"- {ch.get('name', '')}（{ch.get('role', '')}）")
                        else:
                            ch_data = ch.model_dump() if hasattr(ch, "model_dump") else {}
                            char_summary.append(f"- {ch_data.get('name', '')}（{ch_data.get('role', '')}）")
                    preloaded_context.append("## 主要人物\n" + "\n".join(char_summary))

                rules = core.get("world_rules") or []
                if rules:
                    rule_summary = []
                    for r in rules[:8]:
                        if isinstance(r, dict):
                            rule_summary.append(f"- {r.get('name', '')}: {r.get('description', '')[:100]}")
                        else:
                            rule_summary.append(f"- {str(r)[:100]}")
                    preloaded_context.append("## 世界规则\n" + "\n".join(rule_summary))

                foreshadowing = core.get("foreshadowing") or []
                if foreshadowing:
                    fs_summary = []
                    for line in foreshadowing[:8]:
                        if isinstance(line, dict):
                            fs_summary.append(
                                f"- 「{line.get('name', '')}」状态={line.get('status', '')}, "
                                f"埋设={line.get('bury_window_start', '?')}~{line.get('bury_window_end', '?')}, "
                                f"揭示={line.get('reveal_window_start', '?')}~{line.get('reveal_window_end', '?')}"
                            )
                    preloaded_context.append("## 当前活跃伏笔\n" + "\n".join(fs_summary))

                # 前文摘要
                recent_summaries = editor_ctx.recent_summaries or []
                if recent_summaries:
                    sum_text = []
                    for s in recent_summaries[:5]:
                        if isinstance(s, dict):
                            ch_num = s.get("chapter_number", 0)
                            text = str(s.get("summary", ""))[:200]
                            sum_text.append(f"- 第{ch_num}章: {text}")
                        elif isinstance(s, str):
                            sum_text.append(f"- {s[:200]}")
                    if sum_text:
                        preloaded_context.append("## 近期章节摘要\n" + "\n".join(sum_text))

            except Exception as e:
                logger.warning(f"[EditorInChief] UnifiedContextBuilder 预加载失败，回退到分散加载: {e}")
                # 回退：分散加载（保持向后兼容）
                preloaded_context = await self._fallback_preload_context(
                    state, project_id, chapter_number, db
                )

        if preloaded_context:
            # 限制预加载上下文总大小，避免注入过多 token
            combined = "\n\n".join(preloaded_context)
            if len(combined) > 6000:
                combined = combined[:6000] + "\n\n[...上下文已截断]"
            messages = list(messages)  # don't mutate original
            messages.insert(0, {
                "role": "user",
                "content": "以下是当前章节的规划上下文，请直接基于这些信息规划场景合同，无需重复查询：\n\n" + combined,
            })
            messages.append({
                "role": "assistant",
                "content": "已了解章节上下文，现在开始规划场景合同。",
            })

        config = await self.get_agent_config()
        system_prompt = await self._build_system_prompt(state, project_info, db, config.model)

        llm = await self.get_llm_client()

        has_tools = state.project is not None and db is not None
        logger.info(
            f"[EditorInChief] has_tools={has_tools}, project_id={project_id}, chapter={chapter_number}"
        )

        if has_tools:
            try:
                return await self._agent_loop(
                    llm, system_prompt, messages, state, db, deadline=deadline, execution_id=execution_id
                )
            except Exception as e:
                logger.error(f"[EditorInChief] Agent Loop 执行失败: {e}")
                return {"error": f"主编执行失败：{str(e)}", "response": ""}
        else:
            user_prompt = self._merge_messages(messages)
            try:
                response = await llm.generate(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=0.7,
                    task_type=LLMTaskType.EDITOR_PLANNING,
                )
                return {"response": response}
            except Exception as e:
                logger.error(f"[EditorInChief] LLM 调用失败: {e}")
                return {"error": f"LLM 调用失败：{str(e)}", "response": ""}

    async def _build_system_prompt(self, state: EditorState, project_info: dict, db: AsyncSession | None = None, model_name: str | None = None) -> str:
        prompt_parts = []
        current_tokens = 0
        budget = SYSTEM_PROMPT_BUDGET

        if state.project and state.project.core_data:
            budget = state.project.core_data.get("editor_token_budget", SYSTEM_PROMPT_BUDGET)

        if model_name:
            prompt_parts.append(
                f"当前运行模型：{model_name}\n"
                "如果用户询问你正在使用的模型，请准确返回上述模型名，不要自行猜测或改写。"
            )

        prompt_parts.append(STATIC_RULES)
        current_tokens += _estimate_tokens(STATIC_RULES)

        layer1_content = await self._build_layer1(state, project_info, db)
        layer1_tokens = _estimate_tokens(layer1_content)
        max_layer1 = int(budget * LAYER1_BUDGET_RATIO)
        if layer1_tokens > 0 and current_tokens + min(layer1_tokens, max_layer1) < budget * 0.85:
            if layer1_tokens <= max_layer1:
                prompt_parts.append(layer1_content)
                current_tokens += layer1_tokens
            else:
                truncated = self._truncate_to_budget(layer1_content, max_layer1)
                prompt_parts.append(truncated)
                current_tokens += _estimate_tokens(truncated)

        layer2_content = await self._build_layer2(state, db)
        layer2_tokens = _estimate_tokens(layer2_content)
        max_layer2 = int(budget * LAYER2_BUDGET_RATIO)
        if layer2_tokens > 0 and current_tokens + min(layer2_tokens, max_layer2) < budget * 0.85:
            if layer2_tokens <= max_layer2:
                prompt_parts.append(layer2_content)
                current_tokens += layer2_tokens
            else:
                truncated = self._truncate_to_budget(layer2_content, max_layer2)
                prompt_parts.append(truncated)
                current_tokens += _estimate_tokens(truncated)

        layer3_content = await self._build_layer3(state)
        layer3_tokens = _estimate_tokens(layer3_content)
        max_layer3 = int(budget * LAYER3_BUDGET_RATIO)
        if layer3_tokens > 0 and current_tokens + min(layer3_tokens, max_layer3) < budget * 0.90:
            if layer3_tokens <= max_layer3:
                prompt_parts.append(layer3_content)
                current_tokens += layer3_tokens
            else:
                truncated = self._truncate_to_budget(layer3_content, max_layer3)
                prompt_parts.append(truncated)
                current_tokens += _estimate_tokens(truncated)

        layer4_content = await self._build_layer4(state, db)
        layer4_tokens = _estimate_tokens(layer4_content)
        max_layer4 = int(budget * LAYER4_BUDGET_RATIO)
        if layer4_tokens > 0 and current_tokens + min(layer4_tokens, max_layer4) < budget * 0.92:
            if layer4_tokens <= max_layer4:
                prompt_parts.append(layer4_content)
                current_tokens += layer4_tokens
            else:
                truncated = self._truncate_to_budget(layer4_content, max_layer4)
                prompt_parts.append(truncated)
                current_tokens += _estimate_tokens(truncated)

        layer5_content = await self._build_layer5(state)
        if layer5_content and current_tokens + _estimate_tokens(layer5_content) < budget * 0.95:
            prompt_parts.append(layer5_content)
            current_tokens += _estimate_tokens(layer5_content)

        layer6_content = await self._build_layer6(state)
        if layer6_content and current_tokens + _estimate_tokens(layer6_content) < budget * 0.98:
            prompt_parts.append(layer6_content)

        # 质量记忆层：将项目级质量趋势注入主编 prompt
        # 仅在 assist/enforce 模式下注入，off/shadow/report 不影响生成
        quality_layer = self._build_quality_memory_layer(state)
        if quality_layer and current_tokens + _estimate_tokens(quality_layer) < budget * 0.99:
            prompt_parts.append(quality_layer)

        # 方案18 步骤3：注入上一章 writer 执行反馈，让主编规划本章时参考上一章偏离
        if state.project and state.chapter_number and state.chapter_number > 1:
            try:
                from app.services.editor_feedback_service import EditorFeedbackService
                feedback_section = EditorFeedbackService.build_feedback_prompt_section(
                    str(state.project.id), state.chapter_number
                )
                if feedback_section and current_tokens + _estimate_tokens(feedback_section) < budget:
                    prompt_parts.append(feedback_section)
            except Exception as e:
                logger.warning(f"[editor] 加载上一章执行反馈失败: {e}")

        if state.project and db:
            try:
                from app.services.coordination_read_model_service import CoordinationReadModelService
                reader = CoordinationReadModelService()
                coordination_prompt = await reader.build_coordination_prompt(
                    db, str(state.project.id), target_system="editor",
                    relevant_chapter=state.chapter_number,
                    session_id=None,
                    token_budget=300,
                )
                if coordination_prompt:
                    prompt_parts.append(coordination_prompt)
            except Exception as e:
                logger.warning(f"[editor] 加载协调信息失败: {e}")

        return "\n\n".join(prompt_parts)

    async def _fallback_preload_context(
        self,
        state: EditorState,
        project_id,
        chapter_number: int,
        db: AsyncSession | None = None,
    ) -> list[str]:
        """方案2回退：UnifiedContextBuilder 失败时分散加载上下文（向后兼容）。"""
        preloaded_context: list[str] = []

        try:
            from app.services.outline_index_service import OutlineIndexService
            index_svc = OutlineIndexService()
            chapter_detail = await index_svc.get_chapter_detail(
                state.project.id if state.project else project_id, chapter_number, db
            )
            if chapter_detail:
                preloaded_context.append(f"## 当前章节大纲（第{chapter_number}章）\n{json.dumps(chapter_detail, ensure_ascii=False, default=str)[:3000]}")
        except Exception as e:
            logger.warning(f"[EditorInChief] 预加载章节大纲失败: {e}")

        try:
            from app.services.foreshadowing_service import ForeshadowingService
            fs_svc = ForeshadowingService()
            active_lines = await fs_svc.list_foreshadowing_lines(
                project_id, db, statuses=["active", "dormant", "revealing"]
            )
            if active_lines:
                fs_summary = []
                for line in active_lines[:8]:
                    fs_summary.append(
                        f"- 「{line.get('name', '')}」状态={line.get('status', '')}, "
                        f"埋设={line.get('bury_window_start', '?')}~{line.get('bury_window_end', '?')}, "
                        f"揭示={line.get('reveal_window_start', '?')}~{line.get('reveal_window_end', '?')}"
                    )
                preloaded_context.append("## 当前活跃伏笔\n" + "\n".join(fs_summary))
        except Exception as e:
            logger.warning(f"[EditorInChief] 预加载伏笔状态失败: {e}")

        try:
            from app.services.memory_core import CoreMemoryService
            mem = CoreMemoryService()
            characters = await mem.list_characters(project_id)
            if characters:
                char_summary = []
                for ch in characters[:10]:
                    ch_data = ch.model_dump() if hasattr(ch, "model_dump") else ch
                    char_summary.append(f"- {ch_data.get('name', '')}（{ch_data.get('role', '')}）")
                preloaded_context.append("## 主要人物\n" + "\n".join(char_summary))
        except Exception as e:
            logger.warning(f"[EditorInChief] 预加载人物失败: {e}")

        try:
            rules = (state.core_data or {}).get("world_rules", [])
            if rules:
                rule_summary = []
                for r in rules[:8]:
                    rule_summary.append(f"- {r.get('name', '')}: {r.get('description', '')[:100]}")
                preloaded_context.append("## 世界规则\n" + "\n".join(rule_summary))
        except Exception as e:
            logger.warning(f"[EditorInChief] 预加载世界规则失败: {e}")

        try:
            summaries = (state.core_data or {}).get("chapter_summaries", {})
            sum_items = []
            if isinstance(summaries, dict):
                for ch_key, summary_text in summaries.items():
                    try:
                        ch_num = int(ch_key)
                    except (ValueError, TypeError):
                        continue
                    if chapter_number - 2 <= ch_num < chapter_number:
                        sum_items.append((ch_num, str(summary_text)[:200]))
            elif isinstance(summaries, list):
                for s in summaries:
                    ch_num = s.get("chapter_number", 0) if isinstance(s, dict) else 0
                    if chapter_number - 2 <= ch_num < chapter_number:
                        sum_items.append((ch_num, str(s.get("summary", ""))[:200]))
            if sum_items:
                sum_text = [f"- 第{ch_num}章: {text}" for ch_num, text in sorted(sum_items)]
                preloaded_context.append("## 近期章节摘要\n" + "\n".join(sum_text))
        except Exception as e:
            logger.warning(f"[EditorInChief] 预加载章节摘要失败: {e}")

        return preloaded_context

    async def _get_worldview_digest_via_ucb(
        self, state: EditorState, db: AsyncSession | None = None
    ) -> str:
        """P1-C4：通过 UnifiedContextBuilder 统一获取 core，委托 WorldviewDigestService.format_digest_from_core 格式化。

        替代原直接调用 digest_service.get_digest，统一数据获取路径。
        数据获取或格式化失败时返回空字符串（与原 get_digest 行为一致）。
        """
        if not state.project:
            return ""
        try:
            from app.services.unified_context_builder import UnifiedContextBuilder
            from app.services.worldview_digest import WorldviewDigestService
            ucb = UnifiedContextBuilder()
            editor_ctx = await ucb.build_editor_context(
                str(state.project.id), state.chapter_number or 1, db=db
            )
            core = editor_ctx.core or {}
            digest_service = WorldviewDigestService()
            return digest_service.format_digest_from_core(core)
        except Exception as e:
            logger.warning(f"[EditorInChief] UCB 获取世界观摘要失败: {e}")
            return ""

    async def _get_chapter_context_via_ucb(
        self, state: EditorState, chapter_number: int, db: AsyncSession | None = None
    ) -> str:
        """P1-C4：通过 UnifiedContextBuilder 统一获取 core，委托 WorldviewDigestService.format_chapter_context_from_core 格式化。

        替代原直接调用 digest_service.get_chapter_context，统一数据获取路径。
        数据获取或格式化失败时返回空字符串（与原 get_chapter_context 行为一致）。
        """
        if not state.project:
            return ""
        try:
            from app.services.unified_context_builder import UnifiedContextBuilder
            from app.services.worldview_digest import WorldviewDigestService
            ucb = UnifiedContextBuilder()
            editor_ctx = await ucb.build_editor_context(
                str(state.project.id), chapter_number, db=db
            )
            core = editor_ctx.core or {}
            digest_service = WorldviewDigestService()
            return digest_service.format_chapter_context_from_core(core, chapter_number)
        except Exception as e:
            logger.warning(f"[EditorInChief] UCB 获取章节上下文失败: {e}")
            return ""

    async def _build_layer1(self, state: EditorState, project_info: dict, db: AsyncSession | None = None) -> str:
        parts = []
        parts.append("## 项目上下文")

        if state.project:
            parts.append(f"- 名称：{state.project.name}")
            parts.append(f"- 简介：{state.project.description or '暂无'}")
            parts.append(f"- 题材：{state.project.genre or '未指定'}")
            parts.append(f"- 目标字数：{state.project.word_count_target or '未指定'}")
            parts.append(f"- 当前章节：{state.project.current_chapter or 1}")
        elif project_info:
            parts.append(f"- 名称：{project_info.get('name', '未命名')}")
            parts.append(f"- 简介：{project_info.get('description', '暂无')}")
            parts.append(f"- 题材：{project_info.get('genre', '未指定')}")
            parts.append(f"- 目标字数：{project_info.get('word_count_target', '未指定')}")

        if state.project:
            # P1-C4：通过 UnifiedContextBuilder 统一获取 core，委托 WorldviewDigestService.format_digest_from_core 格式化。
            worldview_digest = await self._get_worldview_digest_via_ucb(state, db)
            if worldview_digest:
                parts.append(f"\n### 世界观摘要\n{worldview_digest}")

        # 注入 Genre Profile 信息
        if state.genre_profile:
            genre_name = state.genre_profile.get("display_name", "通用")
            parts.append(f"\n### 题材配置：{genre_name}")
            prohibitions = state.genre_profile.get("activation_prohibitions", [])
            if prohibitions:
                parts.append("题材禁忌：")
                for p in prohibitions:
                    parts.append(f"  - {p}")
            extensions = state.genre_profile.get("contract_extensions", [])
            if extensions:
                ext_names = [e.get("field", "") for e in extensions if isinstance(e, dict)]
                parts.append(f"场景合同扩展字段：{', '.join(ext_names)}")

        # 注入 Active Style Profile 摘要
        if state.active_style_profile:
            sp = state.active_style_profile
            style_name = sp.get("name", sp.get("profile_name", "未命名风格"))
            parts.append(f"\n### 当前文笔风格：{style_name}")
            style_prompt = sp.get("style_prompt", "")
            if style_prompt:
                parts.append(f"风格描述：{style_prompt}")
            persona = sp.get("persona_card", {})
            if persona:
                identity = persona.get("identity", "")
                if identity:
                    parts.append(f"叙述人格：{identity}")
            parts.append(
                "风格编排职责：你需要在规划每个场景时考虑当前文笔风格，"
                "把风格转译成场景级写作策略（叙事距离、情感外显度、描写密度、对白比例、信息密度、禁用表达），"
                "并写入 style_directive。增量原则：只写本场景相对全局风格的差异，不重复复制整份风格画像。"
            )

        return "\n".join(parts)

    async def _build_layer2(self, state: EditorState, db: AsyncSession | None = None) -> str:
        if not state.project:
            return ""

        from app.services.outline_index_service import OutlineIndexService

        index_service = OutlineIndexService()
        try:
            first_level = await index_service.get_first_level_index(
                state.project.id, db
            )
        except Exception as e:
            logger.warning(f"[EditorInChief] 获取大纲索引失败: {e}")
            first_level = None
        if not first_level:
            return ""

        return f"\n## 大纲一级索引\n{first_level}"

    async def _build_layer3(self, state: EditorState) -> str:
        if not state.core_data:
            return ""

        summaries = state.core_data.get("chapter_summaries", {})
        if not summaries:
            return ""

        sorted_keys = sorted(
            [k for k in summaries.keys() if isinstance(k, (int, str))],
            key=lambda x: int(x) if str(x).isdigit() else 0,
            reverse=True,
        )
        recent = sorted_keys[:5]

        if not recent:
            return ""

        parts = ["\n## 前文章节摘要"]
        for ch_key in reversed(recent):
            summary = summaries.get(ch_key, "")
            if summary:
                ch_title = ""
                outline_for_titles = state.outline_snapshot or (
                    state.project.outline_data if state.project else {}
                )
                if outline_for_titles:
                    for ch in outline_for_titles.get("chapters", []):
                        if str(ch.get("chapter_number")) == str(ch_key):
                            ch_title = ch.get("title", "")
                            break
                title_str = f" - {ch_title}" if ch_title else ""
                parts.append(f"\n### 第{ch_key}章{title_str}\n{summary}")

        return "\n".join(parts)

    async def _build_layer4(self, state: EditorState, db: AsyncSession | None = None) -> str:
        if not state.chapter_number or not state.project:
            return ""

        parts = []

        chapter_detail = self._chapter_detail_from_outline(
            state.outline_snapshot, state.chapter_number,
        )
        if not chapter_detail:
            from app.services.outline_index_service import OutlineIndexService

            index_service = OutlineIndexService()
            try:
                chapter_detail = await index_service.get_chapter_detail(
                    state.project.id, state.chapter_number, db
                )
            except Exception as e:
                logger.warning(f"[EditorInChief] 获取章节大纲详情失败: {e}")
                chapter_detail = None
        if chapter_detail:
            parts.append(f"\n## 当前章节大纲（第{state.chapter_number}章）")
            detail_text = json.dumps(chapter_detail, ensure_ascii=False, default=str)
            # 紧凑化：去掉缩进，截断过长内容，避免 system prompt 膨胀
            if len(detail_text) > 3000:
                detail_text = detail_text[:3000] + "\n[...章节大纲已截断]"
            parts.append(detail_text)

        # P1-C4：通过 UnifiedContextBuilder 统一获取 core，委托 WorldviewDigestService.format_chapter_context_from_core 格式化。
        chapter_context = await self._get_chapter_context_via_ucb(state, state.chapter_number, db)
        if chapter_context:
            parts.append(f"\n{chapter_context}")

        return "\n".join(parts)

    async def _build_layer5(self, state: EditorState) -> str:
        if not state.chapter_number or not state.project:
            return ""

        outline = state.outline_snapshot or state.project.outline_data or {}
        chapters = outline.get("chapters", [])
        if not chapters:
            return ""

        current_idx = None
        for i, ch in enumerate(chapters):
            if ch.get("chapter_number") == state.chapter_number:
                current_idx = i
                break

        if current_idx is None:
            return ""

        future_chapters = chapters[current_idx + 1 : current_idx + 4]
        if not future_chapters:
            return ""

        parts = ["\n## 前瞻约束（后续章节需求）"]
        parts.append("⚠️ 生成当前章时必须考虑以下后续章节的需求：\n")
        for fc in future_chapters:
            fc_num = fc.get("chapter_number", "?")
            fc_title = fc.get("title", "")
            fc_conflict = fc.get("main_conflict", fc.get("core_conflict", ""))
            fc_pov = fc.get("pov_character", "")
            reveal_actions = [
                op for op in fc.get("thread_ops", [])
                if isinstance(op, dict) and op.get("op") == "reveal"
            ]

            parts.append(f"### 第{fc_num}章 - {fc_title}")
            parts.append(f"- 核心冲突：{fc_conflict}")
            if fc_pov:
                parts.append(f"- POV角色：{fc_pov}")
            if reveal_actions:
                reveal_names = [f.get("thread_id", "") for f in reveal_actions]
                parts.append(f"- 本章应收伏笔：{'、'.join(reveal_names)}")
            parts.append("")

        return "\n".join(parts)

    async def _build_layer6(self, state: EditorState) -> str:
        if not state.core_data:
            return ""

        parts = []

        cross_memory = state.core_data.get("cross_session_memory", [])
        if cross_memory:
            memory_text = "\n".join(f"- {m}" for m in cross_memory[-5:])
            parts.append(f"\n## 跨会话记忆\n{memory_text}")

        change_notifications = state.core_data.get("change_notifications", [])
        if change_notifications:
            notification_text = "\n".join(f"- {n}" for n in change_notifications[-5:])
            parts.append(f"\n## 变更通知\n{notification_text}")

        active_reminders = state.core_data.get("active_reminders", [])
        if active_reminders:
            reminder_text = "\n".join(f"- {r}" for r in active_reminders[-5:])
            parts.append(f"\n## 激活提醒\n{reminder_text}")

        return "\n".join(parts)

    def _build_quality_memory_layer(self, state: EditorState) -> str:
        """构建质量记忆层，将项目级质量趋势注入主编 prompt.

        仅在 assist/enforce 模式下注入，off/shadow/report 不影响生成。
        """
        # 检查是否有任何检测器处于 assist/enforce 模式
        if not state.feature_policy:
            return ""

        has_active_mode = False
        for mode_field in ("ai_flavor_mode", "concept_budget_mode", "character_voice_mode", "reader_experience_mode"):
            mode_val = "off"
            if hasattr(state.feature_policy, mode_field):
                mode_val = getattr(state.feature_policy, mode_field, "off")
            elif isinstance(state.feature_policy, dict):
                mode_val = state.feature_policy.get(mode_field, "off")
            if mode_val in ("assist", "enforce"):
                has_active_mode = True
                break

        if not has_active_mode:
            return ""

        if not state.quality_memory:
            return ""

        patterns = state.quality_memory.get("recent_quality_patterns", [])
        if not patterns or not isinstance(patterns, list):
            return ""

        # 只展示 count >= 2 的趋势
        significant = [p for p in patterns if isinstance(p, dict) and p.get("count", 0) >= 2]
        if not significant:
            return ""

        parts = ["\n## 项目质量趋势（来自历史生成检测）"]
        parts.append("以下问题在近期生成中反复出现，请在规划场景合同时考虑：\n")
        for p in significant[:5]:
            ptype = p.get("type", "")
            count = p.get("count", 0)
            suggestion = p.get("suggestion", "")
            label = ptype.replace("_", " ").replace("overuse", "偏多").replace("trend", "趋势")
            parts.append(f"- {label}（出现{count}次）：{suggestion}")

        return "\n".join(parts)

    def _truncate_to_budget(self, content: str, max_tokens: int) -> str:
        lines = content.split("\n")
        result_lines = []
        current = 0
        for line in lines:
            line_tokens = _estimate_tokens(line)
            if current + line_tokens > max_tokens:
                break
            result_lines.append(line)
            current += line_tokens
        return "\n".join(result_lines)

    async def _agent_loop(
        self,
        llm: LLMClient,
        system_prompt: str,
        messages: list[dict],
        state: EditorState,
        db: AsyncSession,
        deadline: float | None = None,
        execution_id: str | None = None,
    ) -> dict:
        full_messages = [{"role": "system", "content": system_prompt}]
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                full_messages.append({"role": role, "content": content})

        called_tools: set[str] = set()
        import time
        loop_start = time.monotonic()
        FINAL_RESERVE = 60  # reserve 60s for final result processing
        _last_saved_snapshot: str = ""  # 追踪上次保存的快照，避免无变化时重复写库

        for i in range(self.MAX_TOOL_ITERATIONS):
            elapsed_ms = int((time.monotonic() - loop_start) * 1000)
            remaining_ms = int((deadline - time.monotonic()) * 1000) if deadline else None
            logger.info(
                f"[EditorPlanning] iteration={i}, messages={len(full_messages)}, "
                f"elapsed_ms={elapsed_ms}, remaining_ms={remaining_ms}"
            )

            # Check remaining time before making LLM call
            if deadline:
                remaining = deadline - time.monotonic()
                if remaining < FINAL_RESERVE:
                    logger.warning(
                        f"[EditorPlanning] 剩余时间不足 {int(remaining)}s，提前结束循环"
                    )
                    break
            try:
                llm_timeout = None
                if deadline:
                    remaining_after_reserve = deadline - time.monotonic() - FINAL_RESERVE
                    # 如果 reserve 后剩余不足 30s，不再发起 LLM 调用，直接 break
                    # 避免 max(30, ...) 强制给 30s 从而侵蚀 FINAL_RESERVE
                    if remaining_after_reserve < 30:
                        logger.warning(
                            f"[EditorPlanning] reserve后剩余 {int(remaining_after_reserve)}s 不足30s，提前结束循环"
                        )
                        break
                    llm_timeout = min(90, remaining_after_reserve)
                llm_start = time.monotonic()
                response = await llm.generate_with_tools(
                    messages=full_messages,
                    tools=EDITOR_TOOLS,
                    temperature=0.7,
                    task_type=LLMTaskType.EDITOR_PLANNING,
                    timeout=llm_timeout or 90,
                )
                llm_duration_ms = int((time.monotonic() - llm_start) * 1000)
                logger.info(f"[EditorPlanning] llm_duration_ms={llm_duration_ms}")
            except Exception as e:
                logger.warning(f"[EditorPlanning] LLM 调用失败，尝试降级: {e}")
                # 内层 LLM 超时/失败也走降级路径，返回已有中间成果
                result = {"response": "", "degraded": True, "degraded_reason": f"LLM 调用失败：{str(e)}"}
                if state.scene_contracts:
                    result["scene_contracts"] = state.scene_contracts
                if state.chapter_rhythm_map:
                    result["chapter_rhythm_map"] = state.chapter_rhythm_map
                return result

            if not response.get("has_tool_calls"):
                logger.info("[EditorChief Loop] 无工具调用，返回文本响应")
                result = {"response": response.get("content", "")}
                if state.scene_contracts:
                    result["scene_contracts"] = state.scene_contracts
                if state.chapter_rhythm_map:
                    result["chapter_rhythm_map"] = state.chapter_rhythm_map
                return result

            assistant_msg = response.get("assistant_message", {})
            full_messages.append(assistant_msg)

            tool_calls = response.get("tool_calls", [])
            logger.info(f"[EditorPlanning] 收到 {len(tool_calls)} 个工具调用")

            # 并行执行独立的工具调用
            # 注意：SQLAlchemy AsyncSession 不支持并发操作，
            # 并行工具调用必须使用独立 session，避免 "cannot perform operation on same connection" 错误
            async def _run_single_tool(tc: dict) -> tuple[dict, dict]:
                """执行单个工具调用，返回 (tool_result, tool_message)"""
                fn_name = tc.get("function", {}).get("name", "")
                fn_args_str = tc.get("function", {}).get("arguments", "{}")
                try:
                    fn_args = json.loads(fn_args_str)
                except json.JSONDecodeError:
                    fn_args = {}

                tool_key = f"{fn_name}:{fn_args_str}"
                if tool_key in called_tools:
                    logger.info(f"[EditorPlanning] 重复工具调用被拦截: {fn_name}")
                    tool_result = {"error": "该工具已调用过，请直接基于已有信息回复。"}
                else:
                    called_tools.add(tool_key)
                    logger.info(f"[EditorPlanning] 执行工具: {fn_name}, 参数: {fn_args}")
                    tool_start = time.monotonic()
                    # 判断工具是否需要 DB 访问，需要则使用独立 session
                    needs_db = fn_name not in (
                        "get_chapter_summary", "get_recent_summaries",
                        "propose_scene_contracts",
                    )
                    if needs_db and len(tool_calls) > 1:
                        from app.db.db_models import async_session as _async_session
                        async with _async_session() as tool_db:
                            tool_result = await self._execute_tool(fn_name, fn_args, state, tool_db)
                    else:
                        tool_result = await self._execute_tool(fn_name, fn_args, state, db)
                    tool_duration_ms = int((time.monotonic() - tool_start) * 1000)
                    logger.info(f"[EditorPlanning] tool={fn_name}, tool_duration_ms={tool_duration_ms}")

                tool_message = {
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": json.dumps(tool_result, ensure_ascii=False, default=str),
                }
                return tool_result, tool_message

            if len(tool_calls) > 1:
                # 多个工具调用并行执行
                results = await asyncio.gather(
                    *[_run_single_tool(tc) for tc in tool_calls],
                    return_exceptions=True,
                )
                for idx, res in enumerate(results):
                    if isinstance(res, Exception):
                        tool_result = {"error": f"工具执行异常: {str(res)}"}
                        tool_message = {
                            "role": "tool",
                            "tool_call_id": tool_calls[idx].get("id", ""),
                            "content": json.dumps(tool_result, ensure_ascii=False, default=str),
                        }
                    else:
                        tool_result, tool_message = res

                    if isinstance(tool_result, dict) and "_scene_contracts" in tool_result:
                        state.scene_contracts = tool_result["_scene_contracts"]
                    if isinstance(tool_result, dict) and "_chapter_rhythm_map" in tool_result:
                        state.chapter_rhythm_map = tool_result["_chapter_rhythm_map"]

                    full_messages.append(tool_message)
            else:
                # 单个工具调用，直接执行
                for tc in tool_calls:
                    tool_result, tool_message = await _run_single_tool(tc)

                    if isinstance(tool_result, dict) and "_scene_contracts" in tool_result:
                        state.scene_contracts = tool_result["_scene_contracts"]
                    if isinstance(tool_result, dict) and "_chapter_rhythm_map" in tool_result:
                        state.chapter_rhythm_map = tool_result["_chapter_rhythm_map"]

                    full_messages.append(tool_message)

            # 保存中间规划成果到 WorkflowStep.output_snapshot，超时降级时可恢复
            # 仅在 scene_contracts 或 chapter_rhythm_map 有变化时写入，避免频繁写库
            # 使用独立 session 避免污染外层事务边界
            if execution_id and state.scene_contracts:
                current_snapshot = json.dumps(
                    {"c": len(state.scene_contracts), "r": state.chapter_rhythm_map},
                    ensure_ascii=False,
                )
                if current_snapshot != _last_saved_snapshot:
                    _last_saved_snapshot = current_snapshot
                    try:
                        from app.db.db_models import async_session as _async_session, WorkflowStep as _WS
                        async with _async_session() as save_db:
                            step_q = await save_db.execute(
                                select(_WS).where(
                                    _WS.execution_id == execution_id,
                                    _WS.agent_name == "editor_planning",
                                )
                            )
                            step = step_q.scalar_one_or_none()
                            if step:
                                existing = dict(step.output_snapshot or {})
                                existing.update({
                                    "intermediate_contracts": state.scene_contracts,
                                    "intermediate_rhythm_map": state.chapter_rhythm_map,
                                })
                                step.output_snapshot = existing
                                await save_db.commit()
                    except Exception as save_err:
                        logger.warning(f"[EditorPlanning] 保存中间状态失败: {save_err}")

        result = {"response": "工具调用次数已达上限，请简化请求。"}
        if state.scene_contracts:
            result["scene_contracts"] = state.scene_contracts
        if state.chapter_rhythm_map:
            result["chapter_rhythm_map"] = state.chapter_rhythm_map
        return result

    async def _execute_tool(
        self, name: str, args: dict, state: EditorState, db: AsyncSession | None = None
    ) -> dict:
        if name.startswith("weave_") or name.startswith("register_"):
            from app.agents.weave_coordinator import WeaveCoordinatorAgent
            coordinator = WeaveCoordinatorAgent()
            return await coordinator.handle_tool(name, args, state.project_id, db, caller="editor")

        tool_map = {
            "get_chapter_outline": self._tool_get_chapter_outline,
            "search_outline": self._tool_search_outline,
            "get_foreshadowing_status": self._tool_get_foreshadowing_status,
            "get_character": self._tool_get_character,
            "get_world_rules": self._tool_get_world_rules,
            "check_consistency": self._tool_check_consistency,
            "get_chapter_content": self._tool_get_chapter_content,
            "get_chapter_summary": self._tool_get_chapter_summary,
            "get_recent_summaries": self._tool_get_recent_summaries,
            "propose_scene_contracts": self._tool_propose_scene_contracts,
        }
        handler = tool_map.get(name)
        if not handler:
            return {"error": f"未知工具: {name}"}

        try:
            result = await handler(args, state, db)
            return result
        except Exception as e:
            logger.error(f"[EditorInChief] 工具 {name} 执行失败: {e}")
            return {"error": f"工具执行失败: {str(e)}"}

    async def _tool_get_chapter_outline(self, args: dict, state: EditorState, db: AsyncSession | None = None) -> dict:
        if not state.project:
            return {"error": "项目未加载，无法获取章节大纲"}

        from app.services.outline_index_service import OutlineIndexService

        index_service = OutlineIndexService()
        chapter_number = args.get("chapter_number", 0)
        try:
            chapter = await index_service.get_chapter_detail(
                state.project.id, chapter_number, db
            )
        except Exception as e:
            logger.warning("tool_get_chapter_detail failed: %s", e, exc_info=True)
            return {"error": f"获取章节大纲失败: {str(e)}"}
        if chapter:
            return {
                "chapter": chapter,
                "_hint": "已获取章节详情。基于此信息可以规划场景节拍或回答问题。",
            }
        return {"error": f"未找到第 {chapter_number} 章"}

    async def _tool_search_outline(self, args: dict, state: EditorState, db: AsyncSession | None = None) -> dict:
        if not state.project:
            return {"error": "项目未加载，无法搜索大纲"}

        from app.services.outline_index_service import OutlineIndexService

        index_service = OutlineIndexService()
        query = args.get("query", "")
        try:
            results = await index_service.search_chapters(
                state.project.id, query, db
            )
        except Exception as e:
            return {"error": f"搜索大纲失败: {str(e)}"}
        return {
            "results": results,
            "count": len(results),
            "_hint": "已返回搜索结果。基于这些信息回答用户问题。",
        }

    async def _tool_get_foreshadowing_status(
        self, args: dict, state: EditorState, db: AsyncSession | None = None
    ) -> dict:
        if not state.project:
            return {"error": "项目未加载，无法查询伏笔状态"}

        from app.services.foreshadowing_service import ForeshadowingService
        from app.services.foreshadowing_clue_service import ForeshadowingClueService

        name = args.get("name", "")
        fs_service = ForeshadowingService()
        clue_service = ForeshadowingClueService()

        try:
            line = await fs_service.get_foreshadowing_by_name(state.project.id, name, db)
        except Exception as e:
            logger.warning("tool_get_foreshadowing_status failed: %s", e, exc_info=True)
            return {"error": f"获取伏笔状态失败: {str(e)}"}

        if not line:
            return {"error": f"未找到伏笔线: {name}"}

        result = {
            "name": line["name"],
            "status": line["status"],
            "priority": line["priority"],
            "truth_type": line.get("secret_truth_type", ""),
            "bury_window": [line.get("bury_window_start"), line.get("bury_window_end")],
            "reveal_window": [line.get("reveal_window_start"), line.get("reveal_window_end")],
            "clues_placed": line.get("clues_placed", 0),
            "total_clues_planned": line.get("total_clues_planned", 0),
            "reader_fairness_level": line.get("reader_fairness_level", ""),
        }

        try:
            pool = await clue_service.get_evidence_pool(line["id"], db)
            result["evidence_pool"] = {
                "supportive": pool.get("supportive", 0),
                "distractive": pool.get("distractive", 0),
                "contradictory": pool.get("contradictory", 0),
            }
        except Exception as exc:
            logger.warning("[EditorInChief] 加载证据池失败: %s", exc)

        try:
            cognitive_states = await fs_service.list_cognitive_states_for_foreshadowing(
                state.project.id, line["id"], db,
                chapter_number=state.chapter_number,
            )
            result["character_cognitive_map"] = {
                s["character_name"]: s.get("cognitive_level", "unknown")
                for s in cognitive_states
            }
        except Exception as exc:
            logger.warning("[EditorInChief] 加载认知状态失败: %s", exc)

        try:
            from app.engines.reveal_readiness import calculate_reveal_readiness
            readiness = await calculate_reveal_readiness(line["id"], db)
            result["reveal_readiness"] = readiness.get("readiness", 0)
            result["reveal_ready"] = readiness.get("ready", False)
        except Exception as exc:
            logger.warning("[EditorInChief] 加载揭示准备度失败: %s", exc)

        result["_hint"] = f"伏笔「{name}」当前状态：{line['status']}，认知地图和揭示准备度已加载。"
        return result

    async def _tool_get_character(self, args: dict, state: EditorState, db: AsyncSession | None = None) -> dict:
        if not state.project:
            return {"error": "项目未加载，无法查询人物"}

        from app.services.memory_core import CoreMemoryService

        core_service = CoreMemoryService()
        characters = await core_service.list_characters(str(state.project.id))

        target_name = to_search_text(args.get("name", "")).strip()
        matched = []
        for char in characters:
            char_data = char.model_dump() if hasattr(char, "model_dump") else char
            char_name = to_search_text(char_data.get("name", ""))
            if target_name in char_name or char_name in target_name:
                matched.append(char_data)
            else:
                aliases = char_data.get("aliases", [])
                if isinstance(aliases, list):
                    for alias in aliases:
                        if target_name in to_search_text(alias):
                            matched.append(char_data)
                            break

        if not matched:
            all_names = [
                c.model_dump().get("name", "") if hasattr(c, "model_dump") else c.get("name", "")
                for c in characters[:10]
            ]
            return {
                "error": f"未找到匹配的角色「{args.get('name')}」",
                "available_characters": all_names,
            }

        if len(matched) == 1:
            return {
                "character": matched[0],
                "_hint": "已获取人物详情。基于此信息分析角色表现。",
            }

        return {
            "characters": matched,
            "match_count": len(matched),
            "_hint": f"找到 {len(matched)} 个匹配角色，请确认具体是哪一个。",
        }

    async def _tool_get_world_rules(self, args: dict, state: EditorState, db: AsyncSession | None = None) -> dict:
        if not state.project:
            return {"error": "项目未加载，无法查询世界规则"}

        from app.services.memory_core import CoreMemoryService

        core_service = CoreMemoryService()
        rules = await core_service.list_world_rules(str(state.project.id))

        category = to_search_text(args.get("category", "")).strip()
        if category and category != "general":
            filtered = [r for r in rules if to_search_text(r.get("category", "")) == category]
        else:
            filtered = rules

        if not filtered:
            available_categories = set(r.get("category", "general") for r in rules)
            return {
                "error": f"类别「{args.get('category')}」下没有规则",
                "available_categories": list(available_categories),
                "total_rules": len(rules),
            }

        return {
            "rules": filtered,
            "count": len(filtered),
            "category": category or "全部",
            "_hint": "已获取世界规则。检查生成内容是否符合约束。",
        }

    async def _tool_check_consistency(self, args: dict, state: EditorState, db: AsyncSession | None = None) -> dict:
        dimension = args.get("dimension", "general")
        data = args.get("data", "")

        if not data:
            return {"error": "请提供需要检查的内容"}

        issues = []

        if state.project:
            # P1-C4：通过 UnifiedContextBuilder 统一获取 core，委托 WorldviewDigestService.format_chapter_context_from_core 格式化。
            ch_num = state.chapter_number or 1
            context = await self._get_chapter_context_via_ucb(state, ch_num, db)

            critical_keywords = []
            if context:
                for line in context.split("\n"):
                    if "不可违背" in line or "铁则" in line:
                        critical_keywords.append(line.strip())

            if critical_keywords:
                for rule_line in critical_keywords[:5]:
                    rule_extract = rule_line.replace("-", "").replace("**", "").strip()[:50]
                    issues.append({"level": "info", "rule": rule_extract, "check": "请人工确认是否符合此规则"})

        if dimension == "character" and state.project:
            from app.services.memory_core import CoreMemoryService

            core_service = CoreMemoryService()
            characters = await core_service.list_characters(str(state.project.id))
            char_names = []
            for c in characters:
                c_data = c.model_dump() if hasattr(c, "model_dump") else c
                char_names.append(c_data.get("name", ""))
            if char_names:
                issues.append({
                    "level": "info",
                    "check": f"出场角色需符合以下人物设定：{', '.join(char_names[:8])}",
                })

        return {
            "dimension": dimension,
            "issues": issues,
            "issue_count": len(issues),
            "verdict": "通过" if not any(i.get("level") == "error" for i in issues) else "需要关注",
            "_hint": "已完成一致性检查。如有问题请在生成时修正。",
        }

    async def run_fcip_post_gen_check(
        self,
        project_id,
        chapter_number: int,
        generated_text: str,
        db: AsyncSession,
    ) -> dict:
        from app.engines.fcip_engine import FCIPEngine
        from app.services.foreshadowing_service import ForeshadowingService

        fs_service = ForeshadowingService()
        fcip = FCIPEngine(fs_service)

        try:
            result = await fcip.post_gen_writeback(
                project_id=project_id,
                chapter_number=chapter_number,
                generated_text=generated_text,
                db=db,
            )
        except Exception as e:
            logger.error(f"[EditorInChief] FCIP post-gen check failed: {e}")
            return {"violations_found": 0, "violations": [], "error": str(e)}

        critical = [v for v in result.get("violations", []) if v.get("severity") == "Critical"]
        high = [v for v in result.get("violations", []) if v.get("severity") == "High"]

        if critical:
            logger.warning(
                "[EditorInChief] FCIP detected %d Critical violations, generation should be blocked",
                len(critical),
            )
            result["block_generation"] = True
            result["block_reason"] = f"发现 {len(critical)} 条严重伏笔违规，建议修改后再生成"
        elif high:
            logger.info(
                "[EditorInChief] FCIP detected %d High violations, suggesting revision",
                len(high),
            )
            result["block_generation"] = False
            result["suggestion"] = f"发现 {len(high)} 条高级别伏笔违规，建议检查但不阻断"
        else:
            result["block_generation"] = False

        return result

    async def _tool_get_chapter_content(
        self, args: dict, state: EditorState, db: AsyncSession
    ) -> dict:
        if not state.project:
            return {"error": "项目未加载，无法获取章节内容"}

        chapter_number = args.get("chapter_number", 0)
        start_line = args.get("start_line", 1)
        end_line = args.get("end_line", 100)

        result = await db.execute(
            select(Chapter).where(
                Chapter.project_id == state.project.id,
                Chapter.chapter_number == chapter_number,
            )
        )
        chapter = result.scalar_one_or_none()

        if not chapter or not chapter.content:
            return {"error": f"第 {chapter_number} 章尚未生成或无内容"}

        lines = chapter.content.split("\n")
        total_lines = len(lines)
        excerpt = "\n".join(lines[start_line - 1 : end_line])

        return {
            "chapter_number": chapter_number,
            "title": chapter.title,
            "total_lines": total_lines,
            "excerpt_range": [start_line, min(end_line, total_lines)],
            "content": excerpt,
            "_hint": "已获取章节片段，可用于参考前文细节。",
        }

    async def _tool_get_chapter_summary(self, args: dict, state: EditorState, db: AsyncSession | None = None) -> dict:
        chapter_number = args.get("chapter_number", 0)
        summaries = state.core_data.get("chapter_summaries", {}) if state.core_data else {}

        summary = summaries.get(str(chapter_number)) or summaries.get(chapter_number)
        if not summary:
            return {"error": f"第 {chapter_number} 章暂无摘要"}

        return {
            "chapter_number": chapter_number,
            "summary": summary,
            "_hint": "已获取章节摘要。",
        }

    async def _tool_get_recent_summaries(self, args: dict, state: EditorState, db: AsyncSession | None = None) -> dict:
        count = args.get("count", 3)
        summaries = state.core_data.get("chapter_summaries", {}) if state.core_data else {}

        sorted_keys = sorted(
            [k for k in summaries.keys() if summaries.get(k)],
            key=lambda x: int(x) if str(x).isdigit() else 0,
            reverse=True,
        )
        recent_keys = sorted_keys[:count]

        if not recent_keys:
            return {"error": "暂无任何章节摘要", "available_summaries": []}

        result = []
        for ch_key in recent_keys:
            result.append({
                "chapter_number": int(ch_key) if str(ch_key).isdigit() else ch_key,
                "summary": summaries[ch_key],
            })

        return {
            "summaries": result,
            "count": len(result),
            "_hint": "已获取近期章节摘要，可用于了解剧情进展。",
        }

    async def _tool_propose_scene_contracts(self, args: dict, state: EditorState, db: AsyncSession | None = None) -> dict:
        scene_contracts = args.get("scene_contracts", [])
        chapter_number = args.get("chapter_number", 0)
        chapter_rhythm_map = args.get("chapter_rhythm_map", "")

        if not isinstance(scene_contracts, list) or not scene_contracts:
            return {"error": "场景合同列表不能为空"}

        for i, contract in enumerate(scene_contracts):
            if not isinstance(contract, dict):
                return {"error": f"场景{i+1}合同必须是对象"}
            required_fields = ["scene_id", "pov_character", "goal", "conflict", "must_show", "forbidden", "ending_state"]
            missing = [f for f in required_fields if not contract.get(f)]
            if missing:
                return {"error": f"场景{i+1}缺少必填字段：{', '.join(missing)}"}

        # 方案 26 Part B：style_directive 质量校验
        # 仅在项目有 active_style_profile 时强制——无风格画像时不要求 style_directive
        # P2-19：Part B2 校验失败触发重试，超过 MAX_STYLE_DIRECTIVE_RETRIES 后
        # 回退到默认 style_directive，避免流程卡死。
        if state.active_style_profile:
            from app.services.style_profile_schema import (
                validate_style_directive,
                build_default_style_directive,
                MAX_STYLE_DIRECTIVE_RETRIES,
            )
            # 收集所有场景的 style_directive 校验问题
            failing_indices: list[int] = []
            failing_issues: list[str] = []
            for i, contract in enumerate(scene_contracts):
                if not isinstance(contract, dict):
                    continue
                sd_issues = validate_style_directive(contract.get("style_directive"))
                if sd_issues:
                    failing_indices.append(i)
                    failing_issues.append(f"场景{i+1}：{', '.join(sd_issues)}")

            if failing_indices:
                # 未达重试上限：返回错误提示主编补字段，并递增计数器
                if state.style_directive_retry_count < MAX_STYLE_DIRECTIVE_RETRIES:
                    state.style_directive_retry_count += 1
                    return {
                        "error": (
                            f"style_directive 不完整（第 {state.style_directive_retry_count}/"
                            f"{MAX_STYLE_DIRECTIVE_RETRIES} 次重试）。"
                            f"问题：{'；'.join(failing_issues)}。"
                            "请在 style_directive 中补充 scene_style_role/narrative_distance/"
                            "rhythm_goal/dialogue_density/description_density/avoid 等字段，"
                            "只写本场景相对全局风格的差异。"
                        )
                    }
                # 重试耗尽：回退到默认 style_directive，记录 warning 并继续流程
                logger.warning(
                    "[EditorInChief] style_directive 校验失败已达重试上限 %d 次，"
                    "对场景 %s 回退到默认 style_directive。问题：%s",
                    MAX_STYLE_DIRECTIVE_RETRIES,
                    [i + 1 for i in failing_indices],
                    failing_issues,
                )
                default_sd = build_default_style_directive(state.active_style_profile)
                for i in failing_indices:
                    contract = scene_contracts[i]
                    if isinstance(contract, dict):
                        existing = contract.get("style_directive")
                        # 保留主编已填写的有效字段，仅补缺失字段
                        if isinstance(existing, dict):
                            merged = default_sd.copy()
                            merged.update({k: v for k, v in existing.items() if v})
                            # 仍然不合规则整体回退
                            if validate_style_directive(merged):
                                merged = default_sd
                            contract["style_directive"] = merged
                        else:
                            contract["style_directive"] = default_sd
                # 重置计数器，供后续场景合同重新计数
                state.style_directive_retry_count = 0
            else:
                # 校验通过：重置计数器，避免跨场景累积
                state.style_directive_retry_count = 0

        for contract in scene_contracts:
            if isinstance(contract, dict) and contract.get("source_of_truth"):
                logger.warning(
                    "主编返回的场景合同包含 source_of_truth 字段，"
                    "source_of_truth 应从编译合同中获取，主编不应覆盖此字段"
                )
                contract.pop("source_of_truth", None)

        budget_summaries: list[dict] = []
        try:
            from app.services.information_budget_compiler import get_information_budget_compiler

            budget_compiler = get_information_budget_compiler()
            budgeted_contracts = []
            for idx, contract in enumerate(scene_contracts):
                if not isinstance(contract, dict):
                    budgeted_contracts.append(contract)
                    continue
                budgeted = budget_compiler.apply_to_scene_contract(contract)
                scene_contracts[idx] = budgeted
                budgeted_contracts.append(budgeted)
                info_budget = budgeted.get("information_budget") or {}
                budget_summaries.append({
                    "scene_index": idx,
                    "scene_id": budgeted.get("scene_id") or f"scene_{idx + 1}",
                    "original_must_show_count": len(info_budget.get("original_must_show") or []),
                    "hard_must_show_count": len(budgeted.get("hard_must_show") or []),
                    "soft_guidance_count": len(budgeted.get("soft_guidance") or []),
                    "deferred_count": len(budgeted.get("deferred_items") or []),
                    "over_budget": bool(info_budget.get("over_budget")),
                })
            scene_contracts = budgeted_contracts
        except Exception as e:
            logger.warning("[EditorInChief] 应用信息预算失败: %s", e)

        if not isinstance(chapter_rhythm_map, str):
            chapter_rhythm_map = ""
        chapter_rhythm_map = chapter_rhythm_map.strip()

        # 注入 quality_extensions：根据质量记忆为场景合同添加质量约束
        if state.feature_policy and state.quality_memory:
            try:
                from app.agents.ai_quality_coordinator import AIQualityCoordinatorAgent
                coordinator = AIQualityCoordinatorAgent()

                for contract in scene_contracts:
                    if not isinstance(contract, dict):
                        continue
                    patch = coordinator.build_pre_generation_patch(
                        project_id=state.project_id or "",
                        scene_contract=contract,
                        quality_memory=state.quality_memory,
                        feature_policy=state.feature_policy,
                    )
                    if patch:
                        existing_qe = contract.get("quality_extensions")
                        if not isinstance(existing_qe, dict):
                            existing_qe = {"schema_version": 1}
                        # 深合并：原合同硬约束优先，补丁只追加不覆盖
                        merged = _deep_merge_quality_extensions(existing_qe, patch)
                        contract["quality_extensions"] = merged
            except Exception as e:
                logger.warning(f"[EditorInChief] 注入 quality_extensions 失败: {e}")

        return {
            "chapter_number": chapter_number,
            "scene_contracts": scene_contracts,
            "chapter_rhythm_map": chapter_rhythm_map,
            "information_budget_summary": budget_summaries,
            "contract_count": len(scene_contracts),
            "_hint": f"已生成{len(scene_contracts)}个场景合同和章节节奏地图。这些合同将作为正文生成的硬约束。",
            "_scene_contracts": scene_contracts,
            "_chapter_rhythm_map": chapter_rhythm_map,
        }

    async def execute_stream(self, context: dict) -> AsyncGenerator[dict, None]:
        messages = context.get("messages", [])
        project_id = context.get("project_id")
        db = context.get("db")
        chapter_number = context.get("chapter_number")
        project_info = context.get("project_info", {})

        state = EditorState(
            project_id=project_id,
            chapter_number=chapter_number,
        )

        if project_id and db:
            try:
                state.project = await db.get(Project, project_id)
                if state.project:
                    state.core_data = state.project.core_data or {}
            except Exception as e:
                logger.warning(f"[EditorInChief Stream] 加载项目数据失败: {e}")

        try:
            config = await self.get_agent_config()
            system_prompt = await self._build_system_prompt(state, project_info, db, config.model)
        except Exception as e:
            logger.error(f"[EditorInChief Stream] 构建系统提示失败: {e}")
            yield {"type": "error", "data": {"message": f"构建系统提示失败：{str(e)}"}}
            yield {"type": "done", "data": {}}
            return

        try:
            llm = await self.get_llm_client()
        except Exception as e:
            logger.error(f"[EditorInChief Stream] 获取 LLM 客户端失败: {e}")
            yield {"type": "error", "data": {"message": f"获取 LLM 配置失败：{str(e)}"}}
            yield {"type": "done", "data": {}}
            return

        has_tools = state.project is not None and db is not None
        if not has_tools:
            user_prompt = self._merge_messages(messages)
            try:
                async for chunk in llm.generate_stream(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=0.7,
                    max_tokens=get_profile(LLMTaskType.EDITOR_PLANNING).max_tokens,
                ):
                    yield {"type": "text_delta", "data": {"content": chunk}}
            except Exception as e:
                logger.error(f"[EditorInChief Stream] LLM 流式调用失败: {e}")
                yield {"type": "error", "data": {"message": f"LLM 调用失败：{str(e)}"}}
            yield {"type": "done", "data": {}}
            return

        full_messages = [{"role": "system", "content": system_prompt}]
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                full_messages.append({"role": role, "content": content})

        called_tools: set[str] = set()

        for i in range(self.MAX_TOOL_ITERATIONS):
            try:
                response = await llm.generate_with_tools(
                    messages=full_messages,
                    tools=EDITOR_TOOLS,
                    temperature=0.7,
                    task_type=LLMTaskType.EDITOR_PLANNING,
                )
            except Exception as e:
                logger.error(f"[EditorChief Stream] LLM 调用失败: {e}")
                yield {
                    "type": "error",
                    "data": {"message": f"LLM 调用失败：{str(e)}"},
                }
                yield {"type": "done", "data": {}}
                return

            if not response.get("has_tool_calls"):
                content = response.get("content", "")
                yield {"type": "text_delta", "data": {"content": content}}
                yield {
                    "type": "done",
                    "data": {"response": content},
                }
                return

            assistant_msg = response.get("assistant_message", {})
            full_messages.append(assistant_msg)

            tool_calls = response.get("tool_calls", [])
            for tc in tool_calls:
                fn_name = tc.get("function", {}).get("name", "")
                fn_args_str = tc.get("function", {}).get("arguments", "{}")
                try:
                    fn_args = json.loads(fn_args_str)
                except json.JSONDecodeError:
                    fn_args = {}

                tool_key = f"{fn_name}:{fn_args_str}"
                if tool_key in called_tools:
                    tool_result = {
                        "error": "该工具已调用过，请直接基于已有信息回复。"
                    }
                else:
                    called_tools.add(tool_key)
                    yield {
                        "type": "tool_call",
                        "data": {
                            "tool": fn_name,
                            "args": fn_args,
                            "display": self._tool_display_name(fn_name, fn_args),
                        },
                    }
                    try:
                        tool_result = await self._execute_tool(
                            fn_name, fn_args, state, db
                        )
                    except Exception as e:
                        logger.error(f"[EditorInChief Stream] 工具执行失败 {fn_name}: {e}")
                        tool_result = {"error": f"工具执行失败：{str(e)}"}
                    yield {
                        "type": "tool_result",
                        "data": {
                            "tool": fn_name,
                            "summary": self._tool_result_summary(fn_name, tool_result),
                        },
                    }

                if isinstance(tool_result, dict) and "_scene_contracts" in tool_result:
                    state.scene_contracts = tool_result["_scene_contracts"]
                if isinstance(tool_result, dict) and "_chapter_rhythm_map" in tool_result:
                    state.chapter_rhythm_map = tool_result["_chapter_rhythm_map"]

                full_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": json.dumps(
                            tool_result, ensure_ascii=False, default=str
                        ),
                    }
                )

        yield {
            "type": "done",
            "data": {"response": "工具调用次数已达上限，请简化请求。"},
        }

    def _tool_display_name(self, name: str, args: dict) -> str:
        display_map = {
            "get_chapter_outline": f"正在查看第 {args.get('chapter_number', '?')} 章大纲...",
            "search_outline": f"正在搜索「{args.get('query', '?')}」...",
            "get_foreshadowing_status": f"正在查看伏笔线「{args.get('name', '?')}」...",
            "get_character": f"正在查询角色「{args.get('name', '?')}」...",
            "get_world_rules": f"正在查询规则类别「{args.get('category', '?')}」...",
            "check_consistency": f"正在进行一致性检查（{args.get('dimension', '?')}维度）...",
            "get_chapter_content": f"正在读取第 {args.get('chapter_number', '?')} 章内容...",
            "get_chapter_summary": f"正在获取第 {args.get('chapter_number', '?')} 章摘要...",
            "get_recent_summaries": f"正在获取最近 {args.get('count', 3)} 章摘要...",
        }
        return display_map.get(name, f"正在执行 {name}...")

    def _tool_result_summary(self, name: str, result: dict) -> str:
        if name == "get_chapter_outline":
            ch = result.get("chapter", {})
            return f"已获取第 {ch.get('chapter_number', '?')} 章：{ch.get('title', '')}"
        elif name == "search_outline":
            return f"找到 {result.get('count', 0)} 条结果"
        elif name == "get_foreshadowing_status":
            return f"伏笔线：{result.get('name', '')}（待收 {result.get('pending', 0)}）"
        elif name == "get_character":
            char = result.get("character", result.get("characters", [{}]))
            if isinstance(char, list):
                return f"找到 {result.get('match_count', 0)} 个匹配角色"
            return f"已获取角色：{char.get('name', '')}"
        elif name == "get_world_rules":
            return f"已获取 {result.get('count', 0)} 条规则（类别：{result.get('category', '')}）"
        elif name == "check_consistency":
            return f"一致性检查结果：{result.get('verdict', '完成')}（{result.get('issue_count', 0)}条提示）"
        elif name == "get_chapter_content":
            return f"已读取第 {result.get('chapter_number', '?')} 章（共{result.get('total_lines', 0)}行）"
        elif name == "get_chapter_summary":
            return f"已获取第 {result.get('chapter_number', '?')} 章摘要"
        elif name == "get_recent_summaries":
            return f"已获取 {result.get('count', 0)} 章近期摘要"
        return "已完成"

    def _merge_messages(self, messages: list[dict]) -> str:
        parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if content:
                if role == "user":
                    parts.append(f"用户：{content}")
                elif role == "assistant":
                    parts.append(f"主编：{content}")
        if parts:
            return "\n".join(parts)
        return "请开始工作。"


def _deep_merge_quality_extensions(existing: dict, patch: dict) -> dict:
    """深合并 quality_extensions：原合同硬约束优先，补丁只追加不覆盖.

    规则：
    - anti_ai_guidance：原合同已有则保留，补丁只追加不重复项
    - reveal_control：原合同字段优先，补丁只补充原合同中不存在的字段
      - forbidden_future_concepts：取并集（更严格）
      - new_concept_budget：取较小值（更严格）
      - allowed_new_concepts / deepen_existing_concepts：原合同优先
      - preferred_carriers：原合同优先
    - character_voice_guidance：原合同已有则保留，补丁只追加
    """
    import copy
    result = copy.deepcopy(existing)

    # anti_ai_guidance：追加不重复
    patch_guidance = patch.get("anti_ai_guidance", [])
    if patch_guidance:
        existing_guidance = result.get("anti_ai_guidance", [])
        existing_set = set(existing_guidance)
        for g in patch_guidance:
            if g not in existing_set:
                existing_guidance.append(g)
                existing_set.add(g)
        result["anti_ai_guidance"] = existing_guidance

    # reveal_control：深合并，原合同硬约束优先
    patch_rc = patch.get("reveal_control", {})
    if patch_rc:
        existing_rc = result.get("reveal_control", {})
        if not isinstance(existing_rc, dict):
            existing_rc = {}

        # forbidden_future_concepts：取并集（更严格）
        existing_forbidden = set(existing_rc.get("forbidden_future_concepts", []))
        patch_forbidden = set(patch_rc.get("forbidden_future_concepts", []))
        merged_forbidden = existing_forbidden | patch_forbidden
        existing_rc["forbidden_future_concepts"] = sorted(merged_forbidden)

        # new_concept_budget：取较小值（更严格），安全转换类型
        existing_budget = existing_rc.get("new_concept_budget")
        patch_budget = patch_rc.get("new_concept_budget")
        try:
            existing_budget_int = int(existing_budget) if existing_budget is not None else None
        except (ValueError, TypeError):
            existing_budget_int = None
        try:
            patch_budget_int = int(patch_budget) if patch_budget is not None else None
        except (ValueError, TypeError):
            patch_budget_int = None
        if existing_budget_int is not None and patch_budget_int is not None:
            existing_rc["new_concept_budget"] = min(existing_budget_int, patch_budget_int)
        elif patch_budget_int is not None:
            existing_rc["new_concept_budget"] = patch_budget_int
        elif existing_budget_int is not None:
            existing_rc["new_concept_budget"] = existing_budget_int

        # allowed_new_concepts / deepen_existing_concepts / preferred_carriers：原合同优先
        for key in ("allowed_new_concepts", "deepen_existing_concepts", "preferred_carriers"):
            if key not in existing_rc and key in patch_rc:
                existing_rc[key] = patch_rc[key]

        result["reveal_control"] = existing_rc

    # character_voice_guidance：追加不覆盖
    patch_cvg = patch.get("character_voice_guidance", {})
    if patch_cvg:
        existing_cvg = result.get("character_voice_guidance", {})
        if not isinstance(existing_cvg, dict):
            existing_cvg = {}
        for char_name, guidance_list in patch_cvg.items():
            if char_name not in existing_cvg:
                existing_cvg[char_name] = list(guidance_list)
            else:
                existing_set = set(existing_cvg[char_name])
                for g in guidance_list:
                    if g not in existing_set:
                        existing_cvg[char_name].append(g)
                        existing_set.add(g)
        result["character_voice_guidance"] = existing_cvg

    return result
