import json
import logging
import re
import uuid
from collections.abc import AsyncGenerator

from app.agents.base import BaseAgent
from app.services.llm_client import LLMClient
from app.services.llm_task_profiles import LLMTaskType, get_profile

logger = logging.getLogger(__name__)

OUTLINE_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_chapter_outline",
            "description": "获取指定章节的完整结构化大纲。修改章节前必须先调用此工具获取详情。",
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
            "name": "get_node_outline",
            "description": "获取指定叙事节点的所有章节大纲。当用户提到剧情阶段而非具体章节号时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_id": {
                        "type": "string",
                        "description": "叙事节点ID，如 act1_n3",
                    }
                },
                "required": ["node_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_act_index",
            "description": "获取指定幕的二级索引（节点详情表格）。当需要深入了解某幕的叙事节点分布时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "act_number": {
                        "type": "integer",
                        "description": "幕号，如 1 表示第一幕",
                    }
                },
                "required": ["act_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_outline",
            "description": "全文搜索大纲内容。当用户问'哪些章节涉及XX'时使用。",
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
            "description": "获取指定伏笔线的完整状态：埋设章节、回收章节、当前状态。",
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
            "name": "get_story_constitution",
            "description": "获取故事宪法摘要，包含故事核心承诺、主题、核心梗等最高层设定。当用户提到故事核心、主题、承诺时使用。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_thread_plan",
            "description": "获取线索计划。提供 thread_id 则返回指定线索详情，否则返回全部线索概览。当用户提到伏笔线、线索、主线、支线时使用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "thread_id": {
                        "type": "string",
                        "description": "线索ID（可选，为空则返回全部线索概览）",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_chapter_spine",
            "description": "获取章节脊柱详情，包含章节的结构化元数据（冲突、价值转变、线索操作等）。当用户需要章节结构化详情时使用。",
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
            "name": "apply_story_plan",
            "description": "保存已经完整物化且不超过50章的 Story Plan 2.0 JSON。必须提供 expected_chapter_count；生成新的多章节规划应使用 start_outline_expansion，由服务器持有目标章数合同。",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_json": {
                        "type": "object",
                        "description": "完整的 Story Plan JSON，可包含 story_constitution/macro_plan/arc_plan/chapter_spine/thread_plan/scene_briefs 等层级。",
                    },
                    "expected_chapter_count": {
                        "type": "integer",
                        "description": "plan_json 实际必须包含的逐章数量；必须与服务器校验的物化数量一致。",
                    },
                    "mode": {
                        "type": "string",
                        "enum": ["replace", "patch"],
                        "description": "完整重建用 replace；局部层更新用 patch。",
                    },
                },
                "required": ["plan_json", "expected_chapter_count"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "start_outline_expansion",
            "description": "启动多章节逐章规划任务。任意明确的 N 章目标都由服务器按安全批次生成、校验和持久化；全部完成后才原子替换正式大纲。",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_chapters": {
                        "type": "integer",
                        "description": "全书目标逐章数量，例如500。",
                    },
                    "batch_size": {
                        "type": "integer",
                        "description": "每次模型调用生成的章节数，建议25。",
                    },
                    "replace_existing": {
                        "type": "boolean",
                        "description": "完成后用完整目标替换旧的宏观节点；默认 true。",
                    },
                    "force_restart": {
                        "type": "boolean",
                        "description": "明确要求删除旧规划并重新生成时设为 true。",
                    },
                },
                "required": ["target_chapters"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_chapter_change",
            "description": "修改一个已经存在的章节。修改前必须先读取该章；此工具不会隐式新增章节。",
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter_number": {"type": "integer", "description": "章节号"},
                    "updates": {
                        "type": "object",
                        "description": "章节更新数据，可包含 title/core_conflict/conflict_text/value_shift/pov_character/hook/thread_ops 等字段",
                    },
                },
                "required": ["chapter_number", "updates"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "create_chapter_outline",
            "description": "新增一个大纲章节。提供 chapter_number 时插入该位置并自动后移后续规划；省略时追加到末尾。不会创建正文。",
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter_number": {
                        "type": "integer",
                        "description": "插入后的章节号；省略表示追加到末尾",
                    },
                    "chapter_data": {
                        "type": "object",
                        "description": "新章完整规划，可包含 title/summary/core_conflict/conflict_text/value_shift/pov_character/hook/thread_ops 等字段",
                    },
                },
                "required": ["chapter_data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_chapter_outline",
            "description": "删除用户明确指定的大纲章节，并自动前移后续规划。只有用户明确要求删除时才能调用；不会删除正文，若重排会影响已有正文则拒绝。",
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter_number": {"type": "integer", "description": "要删除的章节号"},
                },
                "required": ["chapter_number"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "apply_scene_brief",
            "description": "将指定章节的完整场景简报写入系统。修改场景简报时使用，scenes 必须包含该章保存后的完整场景数组。",
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter_number": {"type": "integer", "description": "章节号"},
                    "scenes": {
                        "type": "array",
                        "description": "该章完整场景数组",
                        "items": {"type": "object"},
                    },
                },
                "required": ["chapter_number", "scenes"],
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
]

STATIC_RULES = """你是「万象谱」大纲规划总监，专注于小说大纲的全局规划与结构设计。

## 职责边界
- 你的职责：剧情走向决策、冲突布局设计、伏笔埋收管理、角色命运转折规划、叙事结构规划、故事宪法维护、线索计划管理、章节脊柱管理。
- 你不负责：场景细节描写、对话设计、文笔润色。
- 核心约束：修改任何章节的冲突或转折前，必须先确认该章节在整体结构中的位置和影响范围。

## 工具使用策略
- 用户提到具体章节号 → 调用 get_chapter_outline
- 用户提到剧情阶段（如"真相揭示那段"）→ 调用 get_node_outline
- 用户想了解某幕的详细结构 → 调用 get_act_index
- 用户问"哪些章节涉及XX"→ 调用 search_outline
- 用户提到故事核心/主题/承诺 → 调用 get_story_constitution
- 用户提到伏笔线/线索/主线/支线 → 调用 get_thread_plan
- 用户需要章节结构化详情 → 调用 get_chapter_spine
- 用户确认修改某个已有章节 → 先读取，再调用 apply_chapter_change
- 用户明确要求新增/插入某章 → 调用 create_chapter_outline；中间插入会由服务器同步重排后续规划
- 用户明确要求删除某章 → 先读取确认目标，再调用 delete_chapter_outline；禁止把“删去某段内容”误判为删除整章
- 用户确认某章场景简报并要求保存 → 调用 apply_scene_brief，传入该章完整 scenes 数组
- 用户确认完整大纲并要求提交/应用/保存 → 调用 apply_story_plan
- 用户明确要求生成、规划、扩充或重建 N 个章节（N > 1） → 调用 start_outline_expansion；N 只是目标合同，系统自行决定单批或多批
- 需要修改其他系统（世界观/角色/伏笔）的数据 → 调用 weave_propose
- 修改前必须先获取相关章节详情，不可凭索引推断内容
- 同时修改多个章节时，逐章获取、逐章确认
- 获取到所需信息后，立即给出回复或修改建议，不要继续调用工具

## 创作规范摘要
- 冲突 = 渴望 + 障碍 + 行动。冲突类型：人vs人/人vs环境/人vs自我/人vs命运
- 节奏公式：6分推进 + 3分缓冲 + 1分钩子
- 伏笔分层：短期(3-5章) / 中期(10-20章) / 长期(贯穿全书)
- 每章结尾必须有钩子（悬念/危机/反转/情感/信息）
- 人物弧光：正面弧光(缺陷→成长) / 负面弧光(正常→堕落) / 扁平弧光(不变但改变世界)
- 核心梗是过滤器：偏离核心梗的情节直接砍掉
- 人物先于情节：主角需欲望+缺陷，反派需合理动机
- 支线必须回归主线

## 输出规范
- 对话阶段：自由讨论，给出建议和分析
- 确认修改时：使用 apply_chapter_change 工具将单章变更写入系统
- 任意单章均支持查、新增、修改、删除；新增和删除必须使用专用工具，不得用 apply_story_plan 覆盖全书模拟单章操作
- 保存作者已经确认且完整物化的大纲时：使用 apply_story_plan
- 新生成多章节规划时：使用 start_outline_expansion；只有收到服务器返回的 completed/target_chapters 实际回执，才能告诉用户完成
- 严禁输出 `{"action": "new_chapter", "chapter_data": {...}}` 格式的修订案，所有变更必须通过工具落盘
- Story Plan 2.0 JSON 格式要求：
  - chapter_spine 中使用 core_conflict（四元组 desire/obstacle/action/turn）+ conflict_text（文本化）
  - chapter_spine 中使用 value_shift（对象 axis/from/to），不要用 value_transformation
  - chapter_spine 中使用 thread_ops（thread_id/op/mode），不要用 foreshadowing_operations
  - scene_briefs 中每个场景使用 goal/conflict/outcome/info_release/hook
"""

SYSTEM_PROMPT_BUDGET = 65000
LAYER0_BUDGET_RATIO = 0.15
LAYER1_BUDGET_RATIO = 0.08
LAYER3_BUDGET_RATIO = 0.05
CHAPTER_CONTEXT_BUDGET_RATIO = 0.60
ADJACENT_CONTEXT_BUDGET_RATIO = 0.15


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other_chars = len(text) - chinese_chars
    return int(chinese_chars * 1.5 + other_chars * 0.4)


def _truncate_to_tokens(text: str, max_tokens: int) -> str:
    if _estimate_tokens(text) <= max_tokens:
        return text
    truncated = text
    while truncated and _estimate_tokens(truncated) > max_tokens:
        truncated = truncated[: max(0, int(len(truncated) * 0.9))]
    return truncated + "\n...（已按上下文预算截断）"


class OutlineArchitectAgent(BaseAgent):
    name = "outline_architect"
    MAX_TOOL_ITERATIONS = 50
    MAX_DIRECT_CHAPTERS = 50

    @staticmethod
    def _chapter_count(outline: dict | None) -> int:
        outline = outline or {}
        spine = outline.get("chapter_spine")
        if isinstance(spine, list):
            return len(spine)
        chapters = outline.get("chapters")
        return len(chapters) if isinstance(chapters, list) else 0

    @staticmethod
    def _parse_chinese_integer(raw: str) -> int | None:
        """Parse a positive Chinese integer used as a chapter target."""
        digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
        if raw in digits:
            return digits[raw]
        total = 0
        section = 0
        number = 0
        units = {"十": 10, "百": 100, "千": 1000, "万": 10000}
        for char in raw:
            if char in digits:
                number = digits[char]
            elif char in units:
                unit = units[char]
                if unit == 10000:
                    section = (section + (number or 0)) * unit
                    total += section
                    section = 0
                else:
                    section += (number or 1) * unit
                number = 0
        value = total + section + number
        return value or None

    @classmethod
    def _chapter_target_candidates(cls, text: str) -> list[tuple[int, int, int]]:
        """Return (target, start, end) candidates without treating 第N章 as a total."""
        candidates: list[tuple[int, int, int]] = []
        pattern = re.compile(r"(\d{1,5}|[零一二两三四五六七八九十百千万]+)\s*(?:章|回)")
        for match in pattern.finditer(text):
            prefix = text[max(0, match.start() - 1):match.start()]
            if prefix in {"第", "至", "到"}:
                continue
            raw = match.group(1)
            try:
                value = int(raw) if raw.isdigit() else cls._parse_chinese_integer(raw)
            except ValueError:
                value = None
            if value is not None and 1 <= value <= 2000:
                candidates.append((value, match.start(), match.end()))
        return candidates

    @classmethod
    def _explicit_target_from_text(cls, text: str) -> int | None:
        """Extract a requested total while ignoring counts that describe old chapters."""
        fallback: int | None = None
        for value, start, end in cls._chapter_target_candidates(text):
            before = text[max(0, start - 18):start]
            after = text[end:min(len(text), end + 18)]
            if re.search(
                r"(?:删除|删掉|移除|现有|现在的|当前|原来|原本|旧的|只剩|只有|目前仅|实际|显示)\D{0,8}$",
                before,
            ):
                continue
            context = before + after
            if re.search(
                r"(?:目标|总共|共计|计划|原定|定为|定的|规划|设计|制定|生成|扩充|扩展|展开|补齐|"
                r"重建|重新生成|改成|变成|大纲|章节规划)",
                context,
            ):
                return value
            fallback = value
        return fallback

    @staticmethod
    def _declared_outline_target(outline: dict | None) -> int | None:
        outline = outline or {}
        meta = outline.get("meta") if isinstance(outline.get("meta"), dict) else {}
        for raw in (
            outline.get("generation_target_chapters"),
            outline.get("total_chapters"),
            meta.get("generation_target_chapters"),
            ((meta.get("expansion_job") or {}).get("target_chapters")
             if isinstance(meta.get("expansion_job"), dict) else None),
        ):
            try:
                value = int(raw)
            except (TypeError, ValueError):
                continue
            if 1 <= value <= 2000:
                return value
        return None

    def _detect_multi_chapter_planning_request(
        self,
        messages: list[dict],
        existing_outline: dict | None,
        context_mode: str,
        project_id,
        db,
    ) -> dict | None:
        if context_mode != "master" or project_id is None or db is None:
            return None
        user_messages = [
            str(message.get("content") or "")
            for message in messages
            if message.get("role", "user") == "user"
        ]
        if not user_messages:
            return None
        latest = user_messages[-1].strip()
        action_terms = r"(?:生成|规划|设计|制定|扩充|扩展|展开|补齐|重建|重新生成|重生成|重做|续跑|改成|变成)"
        if re.search(rf"(?:不要|别|不用|无需).{{0,12}}{action_terms}", latest):
            return None
        if not re.search(action_terms, latest):
            return None
        strong_directive = bool(re.search(
            rf"(?:请|帮我|给我|我要你|我需要你|直接|现在|开始|继续|确认|对的|按照|按).{{0,40}}{action_terms}",
            latest,
        ))
        explicit_command = strong_directive or bool(
            re.search(rf"{action_terms}.{{0,30}}(?:大纲|章节|章|回)", latest)
        )
        capability_question = bool(re.search(
            r"(?:如何|怎么|为什么|能不能|能否|是否|可不可以|行不行|(?:可以|能够|会).{0,20}吗|[吗么]\s*[？?]?$)",
            latest,
        ))
        if capability_question and not strong_directive:
            return None
        if not explicit_command and not re.search(r"(?:删除|清空).{0,25}(?:重新生成|重建|扩充|扩展)", latest):
            return None

        target = self._explicit_target_from_text(latest)
        refers_to_previous_target = bool(re.search(
            r"(?:原定|之前|前面|刚才|这个章数|目标章数|按原计划|继续|续跑|重新生成|重生成|重建|重做)",
            latest,
        ))
        if target is None and refers_to_previous_target:
            for content in reversed(user_messages[:-1]):
                target = self._explicit_target_from_text(content)
                if target is not None:
                    break
        if target is None and refers_to_previous_target:
            target = self._declared_outline_target(existing_outline)
        if target is None:
            return None
        # One chapter is an ordinary chapter edit.  Two or more chapters share
        # one server-owned planning workflow; the service decides whether that
        # requires one model batch or many.
        if target <= 1:
            return None
        force_restart = bool(re.search(r"删除|清空|重新生成|重生成|重建|重做", latest))
        return {
            "target_chapters": target,
            "batch_size": 25,
            "replace_existing": True,
            "force_restart": force_restart,
        }

    @staticmethod
    def _chapter_planning_response(result: dict) -> str:
        if result.get("error"):
            return f"章节规划没有启动：{result['error']}。正式大纲没有改变。"
        target = int(result.get("target_chapters") or 0)
        completed = int(result.get("completed_chapters") or 0)
        status = str(result.get("status") or "")
        if status in {"already_complete", "completed"}:
            return f"服务器核实：当前正式大纲已物化 {completed}/{target} 章，规划任务已完成。"
        return (
            f"已启动 {target} 章规划任务，当前已完成 {completed}/{target} 章。"
            "系统会根据目标规模自动选择单批或多批，并校验连续编号；全部完成后才一次性替换正式大纲；"
            "当前原有正式章节不会被提前删除，任务失败可从最后成功批次继续。"
        )

    async def execute(self, context: dict) -> dict:
        messages = context.get("messages", [])
        project_info = context.get("project_info", {})
        existing_outline = context.get("existing_outline")
        project_id = context.get("project_id")
        db = context.get("db")
        context_mode = context.get("context_mode", "master")
        selected_chapter_number = context.get("selected_chapter_number")

        planning_request = self._detect_multi_chapter_planning_request(
            messages, existing_outline, context_mode, project_id, db
        )
        if planning_request:
            result = await self._execute_tool(
                "start_outline_expansion",
                planning_request,
                project_id,
                db,
                context_mode,
                selected_chapter_number,
            )
            return {
                "response": self._chapter_planning_response(result),
                "tool_result": result,
            }

        config = await self.get_agent_config()
        system_prompt = await self._build_system_prompt(
            project_info, existing_outline, project_id, db, config.model,
            context_mode, selected_chapter_number,
        )

        llm = await self.get_llm_client()

        has_tools = project_id is not None and db is not None
        logger.info(
            f"[OutlineArchitect] has_tools={has_tools}, project_id={project_id}"
        )

        if has_tools:
            return await self._agent_loop(
                llm, system_prompt, messages, project_id, db,
                context_mode, selected_chapter_number,
            )
        else:
            user_prompt = self._merge_messages(messages)
            response = await llm.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.7,
                task_type=LLMTaskType.OUTLINE_GENERATION,
            )
            return {"response": response}

    async def _build_system_prompt(
        self, project_info, existing_outline, project_id, db,
        model_name: str | None = None, context_mode: str = "master",
        selected_chapter_number: int | None = None,
    ) -> str:
        prompt = ""

        if model_name:
            prompt += (
                f"当前运行模型：{model_name}\n"
                "如果用户询问你正在使用的模型，请准确返回上述模型名，不要自行猜测或改写。\n\n"
            )

        prompt += STATIC_RULES

        if project_info:
            prompt += f"\n当前项目信息：\n"
            prompt += f"- 名称：{project_info.get('name', '未命名')}\n"
            prompt += f"- 简介：{project_info.get('description', '暂无')}\n"
            prompt += f"- 题材：{project_info.get('genre', '未指定')}\n"
            prompt += f"- 目标字数：{project_info.get('word_count_target', '未指定')}\n"

        if project_id and db:
            from app.services.worldview_digest import WorldviewDigestService

            digest_service = WorldviewDigestService()
            worldview_digest = await digest_service.get_digest(project_id, db)
            if worldview_digest:
                prompt += f"\n{worldview_digest}\n"

            from app.services.outline_index_service import OutlineIndexService

            index_service = OutlineIndexService()

            constitution_summary = await index_service.get_constitution_summary(
                project_id, db
            )
            if constitution_summary:
                prompt += f"\n{constitution_summary}\n"

            if context_mode == "chapter" and selected_chapter_number is not None:
                prompt += await self._build_chapter_context(
                    project_id, selected_chapter_number, db
                )
            else:
                thread_plan_summary = await index_service.get_thread_plan_summary(
                    project_id, db
                )
                if thread_plan_summary:
                    prompt += f"\n{thread_plan_summary}\n"

                index_md = await self._get_index_with_budget(
                    index_service, project_id, db, prompt
                )
                if index_md:
                    prompt += f"\n## 当前大纲结构\n{index_md}\n\n"
                elif existing_outline and existing_outline.get("chapters"):
                    chapters = existing_outline.get("chapters", [])
                    prompt += f"\n当前已有大纲（共 {len(chapters)} 章，可在此基础上修改）：\n"
                    prompt += json.dumps(existing_outline, ensure_ascii=False, indent=2)
                    prompt += "\n"

            from app.services.outline_memory_service import OutlineMemoryService

            memory_service = OutlineMemoryService()
            pending = await memory_service.get_pending_issues(project_id, db)
            if pending:
                remaining_budget = (
                    SYSTEM_PROMPT_BUDGET - _estimate_tokens(prompt)
                )
                max_pending_tokens = int(SYSTEM_PROMPT_BUDGET * LAYER3_BUDGET_RATIO)
                if _estimate_tokens(pending) > max_pending_tokens:
                    pending = pending[:max_pending_tokens]
                if remaining_budget > 200:
                    prompt += f"\n{pending}\n"

        elif existing_outline and existing_outline.get("chapters"):
            chapters = existing_outline.get("chapters", [])
            prompt += f"\n当前已有大纲（共 {len(chapters)} 章，可在此基础上修改）：\n"
            prompt += json.dumps(existing_outline, ensure_ascii=False, indent=2)
            prompt += "\n"

        if project_id and db:
            try:
                from app.services.coordination_read_model_service import CoordinationReadModelService
                reader = CoordinationReadModelService()
                coordination_prompt = await reader.build_coordination_prompt(
                    db, str(project_id), target_system="outline",
                    relevant_chapter=selected_chapter_number,
                    session_id=None,
                    token_budget=300,
                )
                if coordination_prompt:
                    prompt += "\n\n" + coordination_prompt
            except Exception as e:
                import logging as _logging
                _logging.getLogger(__name__).warning(f"[outline] 加载协调信息失败: {e}")

        return prompt

    async def _build_chapter_context(self, project_id, chapter_number: int, db) -> str:
        from app.services.scene_brief_service import SceneBriefService
        from app.services.story_plan_service import StoryPlanService
        from app.services.thread_plan_service import ThreadPlanService

        plan = await StoryPlanService().get_story_plan(project_id, db)
        spine = plan.get("chapter_spine", [])
        current = next(
            (item for item in spine if item.get("chapter_number") == chapter_number),
            {},
        )
        scene_brief = await SceneBriefService().get_scene_brief(
            project_id, chapter_number, db
        )
        threads = await ThreadPlanService().get_threads_for_chapter(
            project_id, chapter_number, db
        )
        adjacent = [
            {
                "chapter_number": item.get("chapter_number"),
                "title": item.get("title", ""),
                "conflict_text": item.get("conflict_text", ""),
                "hook": item.get("hook", ""),
            }
            for item in spine
            if item.get("chapter_number") in {chapter_number - 1, chapter_number + 1}
        ]

        current_payload = json.dumps(
            {
                "chapter_spine": current,
                "scene_brief": scene_brief,
                "related_threads": threads,
            },
            ensure_ascii=False,
            indent=2,
        )
        adjacent_payload = json.dumps(adjacent, ensure_ascii=False, indent=2)
        current_payload = _truncate_to_tokens(
            current_payload, int(SYSTEM_PROMPT_BUDGET * CHAPTER_CONTEXT_BUDGET_RATIO)
        )
        adjacent_payload = _truncate_to_tokens(
            adjacent_payload, int(SYSTEM_PROMPT_BUDGET * ADJACENT_CONTEXT_BUDGET_RATIO)
        )
        return (
            f"\n## 当前工作模式：第 {chapter_number} 章工作台\n"
            f"你正在协助用户精修第 {chapter_number} 章。只能修改本章章节脊柱和本章场景简报。"
            "如果用户要求跨章重构、修改全局线索计划或覆盖整本大纲，请提示返回“大纲总文件”。\n"
            "禁止调用 apply_story_plan。调用 apply_chapter_change 或 apply_scene_brief 时，"
            f"chapter_number 必须为 {chapter_number}。\n"
            f"\n### 当前章节上下文\n{current_payload}\n"
            f"\n### 相邻章节精简摘要\n{adjacent_payload}\n"
        )

    async def _get_index_with_budget(
        self, index_service, project_id, db, current_prompt
    ) -> str | None:
        remaining_budget = SYSTEM_PROMPT_BUDGET - _estimate_tokens(current_prompt)
        max_index_tokens = int(SYSTEM_PROMPT_BUDGET * LAYER1_BUDGET_RATIO)

        if remaining_budget < 300:
            return None

        full_index = await index_service.get_index(project_id, db)
        if not full_index:
            return None

        if _estimate_tokens(full_index) <= max_index_tokens:
            return full_index

        first_level = await index_service.get_first_level_index(project_id, db)
        if first_level and _estimate_tokens(first_level) <= max_index_tokens:
            return first_level

        if first_level:
            truncated = first_level
            while _estimate_tokens(truncated) > max_index_tokens and "\n" in truncated:
                truncated = "\n".join(truncated.split("\n")[:-1])
            return truncated

        return None

    async def _agent_loop(
        self, llm, system_prompt, messages, project_id, db,
        context_mode: str = "master", selected_chapter_number: int | None = None,
    ) -> dict:
        full_messages = [{"role": "system", "content": system_prompt}]
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                full_messages.append({"role": role, "content": content})

        called_tools = set()

        for i in range(self.MAX_TOOL_ITERATIONS):
            logger.info(
                f"[AgentLoop] iteration={i}, messages_count={len(full_messages)}"
            )
            response = await llm.generate_with_tools(
                messages=full_messages,
                tools=OUTLINE_TOOLS,
                temperature=0.7,
                task_type=LLMTaskType.OUTLINE_GENERATION,
            )

            if not response.get("has_tool_calls"):
                logger.info(
                    "[AgentLoop] No tool calls, returning text response"
                )
                return {"response": response.get("content", "")}

            assistant_msg = response.get("assistant_message", {})
            full_messages.append(assistant_msg)

            tool_calls = response.get("tool_calls", [])
            logger.info(f"[AgentLoop] Got {len(tool_calls)} tool calls")
            for tc in tool_calls:
                fn_name = tc.get("function", {}).get("name", "")
                fn_args_str = tc.get("function", {}).get("arguments", "{}")
                try:
                    fn_args = json.loads(fn_args_str)
                except json.JSONDecodeError:
                    fn_args = {}

                tool_key = f"{fn_name}:{fn_args_str}"
                if tool_key in called_tools:
                    logger.info(
                        f"[AgentLoop] Duplicate tool call blocked: {fn_name}"
                    )
                    tool_result = {
                        "error": "该工具已调用过，请直接基于已有信息回复。"
                    }
                else:
                    called_tools.add(tool_key)
                    logger.info(
                        f"[AgentLoop] Executing tool: {fn_name} with args: {fn_args}"
                    )
                    tool_result = await self._execute_tool(
                        fn_name, fn_args, project_id, db,
                        context_mode, selected_chapter_number,
                    )

                full_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": json.dumps(
                            tool_result, ensure_ascii=False, default=str
                        ),
                    }
                )

        return {"response": "工具调用次数已达上限，请简化请求。"}

    async def _execute_tool(
        self, name, args, project_id, db,
        context_mode: str = "master", selected_chapter_number: int | None = None,
    ):
        if context_mode == "chapter":
            if name in {
                "apply_story_plan",
                "start_outline_expansion",
                "create_chapter_outline",
                "delete_chapter_outline",
            }:
                return {"error": "章级模式下禁止覆盖整本大纲，请返回“大纲总文件”处理。"}
            if name in {"apply_chapter_change", "apply_scene_brief"}:
                if args.get("chapter_number") != selected_chapter_number:
                    return {"error": f"章级模式下只能修改第{selected_chapter_number}章。"}
            if name == "weave_propose" and args.get("target_domain") == "thread_plan":
                return {"error": "章级模式下禁止修改全局线索计划，请返回“大纲总文件”处理。"}

        if name == "apply_story_plan":
            from app.services.story_plan_service import StoryPlanService
            sp_service = StoryPlanService()
            plan_json = args.get("plan_json", {})
            expected = args.get("expected_chapter_count")
            mode = str(args.get("mode") or "replace")
            if mode not in {"replace", "patch"}:
                return {"error": "mode 只能是 replace 或 patch。"}
            try:
                expected = int(expected)
            except (TypeError, ValueError):
                return {"error": "保存 Story Plan 必须提供 expected_chapter_count，并与实际逐章数量一致。"}
            if expected < 1:
                return {"error": "expected_chapter_count 必须是正整数。"}
            if expected > self.MAX_DIRECT_CHAPTERS:
                return {
                    "error": (
                        f"单次 Story Plan 最多直接保存 {self.MAX_DIRECT_CHAPTERS} 章；"
                        f"{expected} 章必须启动分批扩展任务。"
                    ),
                    "code": "outline_expansion_required",
                    "expected_chapter_count": expected,
                    "actual_chapter_count": self._chapter_count(plan_json),
                }
            try:
                result = await sp_service.save_outline_data(
                    project_id,
                    plan_json,
                    db,
                    mode=mode,
                    expected_chapter_count=expected,
                )
                if result.get("error"):
                    await db.rollback()
                else:
                    await db.commit()
            except Exception as e:
                await db.rollback()
                logger.error("apply_story_plan commit failed: %s", e)
                raise
            if result.get("status") == "saved":
                from app.services.outline_index_service import OutlineIndexService
                index_service = OutlineIndexService()
                await index_service.generate_and_save_index(project_id, db)
                return {
                    "status": "applied",
                    "version": result.get("version", 1),
                    "actual_chapter_count": result.get("actual_chapter_count", 0),
                    "expected_chapter_count": expected,
                    "complete": result.get("actual_chapter_count") == expected,
                    "_hint": (
                        f"Story Plan 已成功写入 {result.get('actual_chapter_count', 0)}/{expected} 章。"
                        "只能根据此服务器回执告知用户实际保存数量。"
                    ),
                }
            return {
                "error": result.get("error", "应用失败"),
                "code": result.get("code"),
                "actual_chapter_count": result.get("actual_chapter_count", self._chapter_count(plan_json)),
                "expected_chapter_count": expected,
                "_hint": "Story Plan 写入失败，正式大纲没有变化。",
            }

        elif name == "start_outline_expansion":
            from app.services.outline_expansion_service import OutlineExpansionService

            try:
                target = int(args.get("target_chapters"))
                batch_size = int(args.get("batch_size") or 25)
            except (TypeError, ValueError):
                return {"error": "target_chapters 和 batch_size 必须是整数。"}
            result = await OutlineExpansionService().start(
                project_id,
                target,
                db,
                batch_size=batch_size,
                replace_existing=bool(args.get("replace_existing", True)),
                force_restart=bool(args.get("force_restart", False)),
            )
            if result.get("error"):
                return result
            result["_hint"] = (
                f"多章节规划任务已登记：当前 {result.get('completed_chapters', 0)}/"
                f"{result.get('target_chapters', target)} 章。不要声称任务已经完成。"
            )
            return result

        elif name == "apply_chapter_change":
            from app.services.story_plan_service import StoryPlanService
            sp_service = StoryPlanService()
            chapter_number = args.get("chapter_number", 0)
            updates = args.get("updates", {})
            try:
                result = await sp_service.update_chapter_spine_item(
                    project_id, chapter_number, updates, db
                )
                if "error" in result:
                    await db.rollback()
                else:
                    await db.commit()
            except Exception as e:
                await db.rollback()
                logger.error("apply_chapter_change commit failed: %s", e)
                raise
            if "error" not in result:
                return {
                    "status": "applied",
                    "chapter_number": chapter_number,
                    "actual_chapter_count": result.get("actual_chapter_count"),
                    "_hint": f"第{chapter_number}章已更新。告知用户变更已保存。",
                }
            return {
                "error": result.get("error", "更新失败"),
                "code": result.get("code"),
                "_hint": "章节更新失败。",
            }

        elif name == "create_chapter_outline":
            from app.services.story_plan_service import StoryPlanService

            service = StoryPlanService()
            chapter_data = args.get("chapter_data") or {}
            try:
                if args.get("chapter_number") is None:
                    plan = await service.get_story_plan(project_id, db)
                    spine = plan.get("chapter_spine", []) if not plan.get("error") else []
                    next_number = len(spine) + 1 if isinstance(spine, list) else 1
                    result = await service.insert_chapter_spine_item(
                        project_id, next_number, chapter_data, db
                    )
                else:
                    result = await service.insert_chapter_spine_item(
                        project_id, int(args.get("chapter_number")), chapter_data, db
                    )
                if result.get("error"):
                    await db.rollback()
                else:
                    await db.commit()
            except Exception as exc:
                await db.rollback()
                logger.error("create_chapter_outline commit failed: %s", exc)
                raise
            if result.get("error"):
                return {
                    "error": result.get("error"),
                    "code": result.get("code"),
                    "first_affected_written_chapter": result.get("first_affected_written_chapter"),
                    "_hint": "章节新增失败，正式大纲没有变化。",
                }
            from app.services.outline_index_service import OutlineIndexService

            await OutlineIndexService().generate_and_save_index(project_id, db)
            number = int(result.get("chapter_number") or 0)
            return {
                "status": "created",
                "chapter_number": number,
                "actual_chapter_count": result.get("actual_chapter_count"),
                "shifted_chapters": result.get("shifted_chapters", 0),
                "_hint": f"第{number}章已新增并保存。",
            }

        elif name == "delete_chapter_outline":
            from app.services.story_plan_service import StoryPlanService

            try:
                chapter_number = int(args.get("chapter_number"))
            except (TypeError, ValueError):
                return {"error": "chapter_number 必须是正整数。"}
            try:
                result = await StoryPlanService().delete_chapter_spine_item(
                    project_id, chapter_number, db
                )
                if result.get("error"):
                    await db.rollback()
                else:
                    await db.commit()
            except Exception as exc:
                await db.rollback()
                logger.error("delete_chapter_outline commit failed: %s", exc)
                raise
            if result.get("error"):
                return {
                    "error": result.get("error"),
                    "code": result.get("code"),
                    "first_affected_written_chapter": result.get("first_affected_written_chapter"),
                    "_hint": "章节删除失败，正式大纲没有变化。",
                }
            from app.services.outline_index_service import OutlineIndexService

            await OutlineIndexService().generate_and_save_index(project_id, db)
            return {
                "status": "deleted",
                "chapter_number": chapter_number,
                "actual_chapter_count": result.get("actual_chapter_count"),
                "shifted_chapters": result.get("shifted_chapters", 0),
                "_hint": f"原第{chapter_number}章已删除，后续规划已连续重排。",
            }

        elif name == "apply_scene_brief":
            from app.services.scene_brief_service import SceneBriefService
            scene_service = SceneBriefService()
            chapter_number = args.get("chapter_number", 0)
            scenes = args.get("scenes", [])
            if not isinstance(scenes, list):
                return {"error": "scenes 必须是完整场景数组。"}
            try:
                result = await scene_service.save_scene_brief(
                    project_id,
                    chapter_number,
                    {"scenes": scenes, "source": "user_edited"},
                    db,
                )
                await db.commit()
            except Exception as e:
                await db.rollback()
                logger.error("apply_scene_brief commit failed: %s", e)
                raise
            if "error" not in result:
                return {
                    "status": "applied",
                    "chapter_number": chapter_number,
                    "scene_count": len(scenes),
                    "_hint": f"第{chapter_number}章场景简报已保存。告知用户变更已保存。",
                }
            return {"error": result.get("error", "场景简报保存失败。")}

        if name.startswith("weave_"):
            from app.agents.weave_coordinator import WeaveCoordinatorAgent
            coordinator = WeaveCoordinatorAgent()
            return await coordinator.handle_tool(name, args, project_id, db, caller="outline")

        from app.services.outline_index_service import OutlineIndexService

        index_service = OutlineIndexService()

        if name == "get_chapter_outline":
            chapter = await index_service.get_chapter_detail(
                project_id, args.get("chapter_number", 0), db
            )
            if not chapter:
                from app.services.story_plan_service import StoryPlanService

                blueprint = await StoryPlanService().get_chapter_blueprint(
                    project_id, args.get("chapter_number", 0), db
                )
                chapter = blueprint.get("chapter") if not blueprint.get("error") else None
            if chapter:
                return {
                    "chapter": chapter,
                    "_hint": "已获取章节详情。现在可以基于此信息给出修改建议或回答用户问题。",
                }
            return {"error": f"未找到第 {args.get('chapter_number')} 章"}

        elif name == "get_node_outline":
            return await index_service.get_node_detail(
                project_id, args.get("node_id", ""), db
            )

        elif name == "get_act_index":
            return await index_service.get_act_index(
                project_id, args.get("act_number", 1), db
            )

        elif name == "search_outline":
            results = await index_service.search_chapters(
                project_id, args.get("query", ""), db
            )
            return {
                "results": results,
                "count": len(results),
                "_hint": "已返回搜索结果。基于这些信息回答用户问题。",
            }

        elif name == "get_foreshadowing_status":
            return await self._tool_get_foreshadowing_status(
                project_id, args.get("name", ""), db
            )

        elif name == "get_story_constitution":
            constitution = await index_service.get_constitution_summary(
                project_id, db
            )
            if constitution:
                return {
                    "constitution": constitution,
                    "_hint": "已获取故事宪法摘要。基于此信息回答用户问题或给出调整建议。",
                }
            return {"info": "当前项目尚未建立故事宪法"}

        elif name == "get_thread_plan":
            from app.services.thread_plan_service import ThreadPlanService

            thread_plan_service = ThreadPlanService()
            thread_id = args.get("thread_id", "")
            if thread_id:
                thread = await thread_plan_service.get_thread(
                    project_id, thread_id, db
                )
                if thread:
                    return {
                        "thread": thread,
                        "_hint": f"已获取线索「{thread.get('name', thread_id)}」详情。基于此信息回答用户问题。",
                    }
                return {"error": f"未找到线索: {thread_id}"}
            else:
                tp = await thread_plan_service.get_thread_plan(project_id, db)
                return {
                    "thread_plan": tp,
                    "thread_count": len(tp.get("threads", [])),
                    "_hint": "已获取全部线索概览。基于此信息回答用户问题。",
                }

        elif name == "get_chapter_spine":
            spine_detail = await index_service.get_chapter_spine_detail(
                project_id, args.get("chapter_number", 0), db
            )
            if spine_detail:
                return {
                    "chapter_spine": spine_detail,
                    "_hint": "已获取章节脊柱详情。基于此信息回答用户问题或给出修改建议。",
                }
            return {"error": f"未找到第 {args.get('chapter_number')} 章的脊柱数据"}

        return {"error": f"未知工具: {name}"}

    async def _tool_get_foreshadowing_status(self, project_id, name: str, db) -> dict:
        from app.services.foreshadowing_service import ForeshadowingService
        from app.services.foreshadowing_clue_service import ForeshadowingClueService

        fs_service = ForeshadowingService()
        clue_service = ForeshadowingClueService()

        try:
            line = await fs_service.get_foreshadowing_by_name(project_id, name, db)
        except Exception as e:
            return {"error": f"获取伏笔状态失败: {str(e)}"}

        if not line:
            return {"error": f"未找到伏笔线: {name}"}

        result = {
            "name": line["name"],
            "status": line["status"],
            "priority": line["priority"],
            "truth_type": line.get("secret_truth_type", ""),
            "impact_level": line.get("secret_impact_level", ""),
            "bury_window": [line.get("bury_window_start"), line.get("bury_window_end")],
            "reveal_window": [line.get("reveal_window_start"), line.get("reveal_window_end")],
            "clues_placed": line.get("clues_placed", 0),
            "total_clues_planned": line.get("total_clues_planned", 0),
            "bury_rhythm": line.get("bury_rhythm", ""),
            "reader_fairness_level": line.get("reader_fairness_level", ""),
            "salience": line.get("salience", ""),
        }

        try:
            pool = await clue_service.get_evidence_pool(line["id"], db)
            result["evidence_pool"] = {
                "supportive": pool.get("supportive", 0),
                "distractive": pool.get("distractive", 0),
                "contradictory": pool.get("contradictory", 0),
                "missing": pool.get("missing", 0),
            }
        except Exception as exc:
            logger.warning("[OutlineArchitect] 加载证据池失败: %s", exc)

        try:
            cognitive_states = await fs_service.list_cognitive_states_for_foreshadowing(
                project_id, line["id"], db,
            )
            result["character_cognitive_map"] = {}
            for s in cognitive_states:
                char_name = s["character_name"]
                result["character_cognitive_map"][char_name] = {
                    "cognitive_level": s.get("cognitive_level", "unknown"),
                    "cognitive_status": s.get("cognitive_status", "blind"),
                    "is_misled": s.get("is_intentionally_misled", False),
                }
        except Exception as exc:
            logger.warning("[OutlineArchitect] 加载认知状态失败: %s", exc)

        try:
            from app.engines.reveal_readiness import calculate_reveal_readiness
            readiness = await calculate_reveal_readiness(line["id"], db)
            result["reveal_readiness"] = readiness.get("readiness", 0)
            result["reveal_ready"] = readiness.get("ready", False)
            result["readiness_components"] = readiness.get("components", {})
        except Exception as exc:
            logger.warning("[OutlineArchitect] 加载揭示准备度失败: %s", exc)

        try:
            from app.services.foreshadowing_graph_service import ForeshadowingGraphService
            graph_service = ForeshadowingGraphService()
            affected = await graph_service.get_affected_foreshadowing(
                project_id, line["id"], db, radius=2,
            )
            if affected:
                result["affected_foreshadowing"] = affected
        except Exception as exc:
            logger.warning("[OutlineArchitect] 加载因果影响失败: %s", exc)

        result["_hint"] = f"伏笔「{name}」完整状态已加载，包含认知地图、证据池、揭示准备度和因果影响。"
        return result

    async def execute_stream(self, context: dict) -> AsyncGenerator[dict, None]:
        messages = context.get("messages", [])
        project_info = context.get("project_info", {})
        existing_outline = context.get("existing_outline")
        project_id = context.get("project_id")
        db = context.get("db")
        context_mode = context.get("context_mode", "master")
        selected_chapter_number = context.get("selected_chapter_number")

        planning_request = self._detect_multi_chapter_planning_request(
            messages, existing_outline, context_mode, project_id, db
        )
        if planning_request:
            yield {
                "type": "tool_call",
                "data": {
                    "tool": "start_outline_expansion",
                    "args": planning_request,
                    "display": self._tool_display_name("start_outline_expansion", planning_request),
                },
            }
            try:
                result = await self._execute_tool(
                    "start_outline_expansion",
                    planning_request,
                    project_id,
                    db,
                    context_mode,
                    selected_chapter_number,
                )
            except Exception as exc:
                logger.exception("[OutlineArchitect Stream] 启动章节扩展失败")
                result = {"error": f"启动章节扩展失败：{exc}"}
            yield {
                "type": "tool_result",
                "data": {
                    "tool": "start_outline_expansion",
                    "summary": self._tool_result_summary("start_outline_expansion", result),
                    "changed": self._tool_result_changed("start_outline_expansion", result),
                    "status": result.get("status"),
                    "completed_chapters": result.get("completed_chapters", 0),
                    "target_chapters": result.get("target_chapters", planning_request["target_chapters"]),
                },
            }
            content = self._chapter_planning_response(result)
            yield {"type": "text_delta", "data": {"content": content}}
            yield {"type": "done", "data": {"response": content}}
            return

        try:
            config = await self.get_agent_config()
            system_prompt = await self._build_system_prompt(
                project_info, existing_outline, project_id, db, config.model,
                context_mode, selected_chapter_number,
            )
        except Exception as e:
            logger.error(f"[OutlineArchitect Stream] 构建系统提示失败: {e}")
            yield {"type": "error", "data": {"message": f"构建系统提示失败：{str(e)}"}}
            yield {"type": "done", "data": {}}
            return

        try:
            llm = await self.get_llm_client()
        except Exception as e:
            logger.error(f"[OutlineArchitect Stream] 获取 LLM 客户端失败: {e}")
            yield {"type": "error", "data": {"message": f"获取 LLM 配置失败：{str(e)}"}}
            yield {"type": "done", "data": {}}
            return

        has_tools = project_id is not None and db is not None
        if not has_tools:
            user_prompt = self._merge_messages(messages)
            try:
                async for chunk in llm.generate_stream(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=0.7,
                    max_tokens=get_profile(LLMTaskType.OUTLINE_GENERATION).max_tokens,
                ):
                    yield {"type": "text_delta", "data": {"content": chunk}}
            except Exception as e:
                logger.error(f"[OutlineArchitect Stream] LLM 流式调用失败: {e}")
                yield {"type": "error", "data": {"message": f"LLM 调用失败：{str(e)}"}}
            yield {"type": "done", "data": {}}
            return

        full_messages = [{"role": "system", "content": system_prompt}]
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                full_messages.append({"role": role, "content": content})

        called_tools = set()

        for i in range(self.MAX_TOOL_ITERATIONS):
            try:
                response = await llm.generate_with_tools(
                    messages=full_messages,
                    tools=OUTLINE_TOOLS,
                    temperature=0.7,
                    task_type=LLMTaskType.OUTLINE_GENERATION,
                )
            except Exception as e:
                logger.error(f"[OutlineArchitect Stream] LLM 调用失败: {e}")
                yield {"type": "error", "data": {"message": f"LLM 调用失败：{str(e)}"}}
                yield {"type": "done", "data": {}}
                return

            if not response.get("has_tool_calls"):
                content = response.get("content", "")
                yield {"type": "text_delta", "data": {"content": content}}
                yield {"type": "done", "data": {"response": content}}
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
                    tool_result = {"error": "该工具已调用过，请直接基于已有信息回复。"}
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
                            fn_name, fn_args, project_id, db,
                            context_mode, selected_chapter_number,
                        )
                    except Exception as e:
                        logger.error(f"[OutlineArchitect Stream] 工具执行失败 {fn_name}: {e}")
                        tool_result = {"error": f"工具执行失败：{str(e)}"}
                    yield {
                        "type": "tool_result",
                        "data": {
                            "tool": fn_name,
                            "summary": self._tool_result_summary(fn_name, tool_result),
                            "changed": self._tool_result_changed(fn_name, tool_result),
                        },
                    }

                full_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": json.dumps(
                            tool_result, ensure_ascii=False, default=str
                        ),
                    }
                )

        yield {"type": "done", "data": {"response": "工具调用次数已达上限，请简化请求。"}}

    def _tool_display_name(self, name: str, args: dict) -> str:
        if name == "apply_story_plan":
            return "正在将 Story Plan 写入大纲系统..."
        elif name == "start_outline_expansion":
            return f"正在启动 {args.get('target_chapters', '?')} 章规划任务..."
        elif name == "apply_chapter_change":
            return f"正在更新第 {args.get('chapter_number', '?')} 章..."
        elif name == "create_chapter_outline":
            if args.get("chapter_number") is None:
                return "正在末尾新增大纲章节..."
            return f"正在插入新的第 {args.get('chapter_number')} 章并重排后续规划..."
        elif name == "delete_chapter_outline":
            return f"正在删除第 {args.get('chapter_number', '?')} 章并重排后续规划..."
        elif name == "apply_scene_brief":
            return f"正在保存第 {args.get('chapter_number', '?')} 章场景简报..."
        elif name == "get_chapter_outline":
            return f"正在查看第 {args.get('chapter_number', '?')} 章大纲..."
        elif name == "get_node_outline":
            return f"正在获取叙事节点 {args.get('node_id', '?')}..."
        elif name == "get_act_index":
            return f"正在查看第 {args.get('act_number', '?')} 幕索引..."
        elif name == "search_outline":
            return f"正在搜索「{args.get('query', '?')}」..."
        elif name == "get_foreshadowing_status":
            return f"正在查看伏笔线「{args.get('name', '?')}」..."
        elif name == "get_story_constitution":
            return "正在获取故事宪法摘要..."
        elif name == "get_thread_plan":
            thread_id = args.get("thread_id", "")
            if thread_id:
                return f"正在获取线索「{thread_id}」详情..."
            return "正在获取全部线索概览..."
        elif name == "get_chapter_spine":
            return f"正在获取第 {args.get('chapter_number', '?')} 章脊柱详情..."
        return f"正在执行 {name}..."

    def _tool_result_summary(self, name: str, result: dict) -> str:
        if name == "apply_story_plan":
            if result.get("status") == "applied":
                return (
                    f"Story Plan 已写入 {result.get('actual_chapter_count', 0)}/"
                    f"{result.get('expected_chapter_count', '?')} 章（版本 {result.get('version', '?')}）"
                )
            return f"写入失败：{result.get('error', '未知错误')}"
        elif name == "start_outline_expansion":
            if result.get("error"):
                return f"规划任务启动失败：{result.get('error')}"
            return (
                f"已启动章节规划：{result.get('completed_chapters', 0)}/"
                f"{result.get('target_chapters', '?')} 章"
            )
        elif name == "apply_chapter_change":
            if result.get("status") == "applied":
                return f"第 {result.get('chapter_number', '?')} 章已更新"
            return f"更新失败：{result.get('error', '未知错误')}"
        elif name == "create_chapter_outline":
            if result.get("status") == "created":
                return (
                    f"第 {result.get('chapter_number', '?')} 章已新增；"
                    f"当前共 {result.get('actual_chapter_count', '?')} 章，"
                    f"后移 {result.get('shifted_chapters', 0)} 章"
                )
            return f"新增失败：{result.get('error', '未知错误')}"
        elif name == "delete_chapter_outline":
            if result.get("status") == "deleted":
                return (
                    f"原第 {result.get('chapter_number', '?')} 章已删除；"
                    f"当前共 {result.get('actual_chapter_count', '?')} 章，"
                    f"前移 {result.get('shifted_chapters', 0)} 章"
                )
            return f"删除失败：{result.get('error', '未知错误')}"
        elif name == "apply_scene_brief":
            if result.get("status") == "applied":
                return f"第 {result.get('chapter_number', '?')} 章场景简报已保存"
            return f"保存失败：{result.get('error', '未知错误')}"
        elif name == "get_chapter_outline":
            ch = result.get("chapter", {})
            return f"已获取第 {ch.get('chapter_number', '?')} 章：{ch.get('title', '')}"
        elif name == "get_node_outline":
            return f"已获取节点：{result.get('node_name', '')}（{result.get('chapter_count', 0)}章）"
        elif name == "get_act_index":
            return f"已获取第 {result.get('act_number', '?')} 幕：{result.get('act_name', '')}"
        elif name == "search_outline":
            return f"找到 {result.get('count', 0)} 条结果"
        elif name == "get_foreshadowing_status":
            return f"伏笔线：{result.get('name', '')}（待收 {result.get('pending', 0)}）"
        elif name == "get_story_constitution":
            if result.get("constitution"):
                return "已获取故事宪法摘要"
            return "暂无故事宪法"
        elif name == "get_thread_plan":
            if result.get("thread"):
                return f"已获取线索：{result['thread'].get('name', '')}"
            return f"已获取全部线索概览（{result.get('thread_count', 0)}条）"
        elif name == "get_chapter_spine":
            spine = result.get("chapter_spine", {})
            return f"已获取第 {spine.get('chapter_number', '?')} 章脊柱详情"
        return "已完成"

    def _tool_result_changed(self, name: str, result: dict) -> bool:
        if result.get("error"):
            return False
        if name in {"apply_story_plan", "apply_chapter_change", "apply_scene_brief"}:
            return result.get("status") == "applied"
        if name == "create_chapter_outline":
            return result.get("status") == "created"
        if name == "delete_chapter_outline":
            return result.get("status") == "deleted"
        if name == "start_outline_expansion":
            return result.get("status") in {"started", "resumed", "already_running"}
        if name == "weave_propose":
            return result.get("status") == "executed"
        return False

    def _merge_messages(self, messages):
        parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if content:
                if role == "user":
                    parts.append(f"用户：{content}")
                elif role == "assistant":
                    parts.append(f"架构师：{content}")
        if parts:
            return "\n".join(parts)
        return "请开始工作。"
