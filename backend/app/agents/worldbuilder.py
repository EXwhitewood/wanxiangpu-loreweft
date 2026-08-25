import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.agents.base import BaseAgent
from app.services.llm_task_profiles import LLMTaskType

logger = logging.getLogger(__name__)

WORLDBUILDER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_world_rules",
            "description": "获取当前项目的所有世界规则，可按分类筛选。当需要了解已有规则、检查规则冲突时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "按分类筛选：magic/technology/society/combat/history/general。不传则返回全部。",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_character",
            "description": "获取指定人物的完整信息，包括外貌、性格、欲望、深层需求、人物弧光和关系网络。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "人物姓名",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_characters",
            "description": "获取所有人物的概要列表。当需要了解全局人物分布、查找人物时调用。",
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
            "name": "get_location",
            "description": "获取指定地点的完整信息，包括描述、氛围和从属关系。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "地点名称",
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_locations",
            "description": "获取所有地点的概要列表。",
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
            "name": "get_foreshadowing",
            "description": "获取伏笔信息。可按名称查询单条伏笔，或获取全部伏笔列表。",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "伏笔名称。不传则返回全部伏笔列表。",
                    },
                    "status": {
                        "type": "string",
                        "description": "按状态筛选：planned/active/dormant/revealing/resolved/aborted。不传则不筛选。",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_consistency",
            "description": "检查新增设定与已有世界观的一致性。在创建新规则、新人物或新地点前，应调用此工具检测潜在冲突。",
            "parameters": {
                "type": "object",
                "properties": {
                    "dimension": {
                        "type": "string",
                        "description": "检查维度：rule/character/location/foreshadowing",
                    },
                    "data": {
                        "type": "object",
                        "description": "待检查的设定数据",
                    }
                },
                "required": ["dimension", "data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_outline_summary",
            "description": "获取当前项目的大纲摘要。当需要了解故事走向、章节结构以辅助世界观设计时调用。",
            "parameters": {
                "type": "object",
                "properties": {
                    "chapter_number": {
                        "type": "integer",
                        "description": "指定章节号获取该章大纲。不传则返回大纲整体摘要。",
                    }
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_worldview_completeness",
            "description": "检查世界观的六大维度覆盖度，评估推导链完整性。当用户想了解世界观构建进度、发现设定空白时调用。",
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
]

STATIC_RULES = """你是「万象谱」世界观构建师，基于"冰山推导法"帮助作者构建逻辑自洽、为故事服务的世界观。

## 核心方法论：冰山推导法

世界观构建遵循"核心设定→推导链→表面呈现"的三层结构：

### 第一层：锚定底层根基（不可动摇的公理）
- 世界本质：世界的基础形态是什么？（现实物理/奇幻玄幻/科幻未来）
- 核心矛盾：世界诞生故事的根源冲突
- 基础铁则：不可违背的硬性规则（控制在3条以内，越少越容易自洽）

### 第二层：推演六大核心维度（严格因果推导）
每个维度必须回答：为何形成当前形态？是否契合底层铁则？

1. **地理与环境**（空间基底）
   - 山脉阻隔→差异化文明；资源富集→势力强弱；危险地带→禁忌法则
   - 地理决定资源分布，资源分布决定势力格局

2. **核心规则与能力机制**（规则核心，最易出逻辑漏洞）
   - 根据题材构建闭环：资源来源/权力来源/技术条件/能力边界→使用成本→限制代价→禁忌红线
   - 核心原则：任何能改变冲突结果的机制都需要边界；例外必须有代价或条件
   - 若涉及魔法、超能力或硬科幻技术，需明确限制程度，避免用未铺垫机制解决冲突

3. **种族与物种**
   - 环境塑造外貌特征；核心规则影响寿命、能力与社会分工；资源分配决定群体关系

4. **历史与时间线**（完整因果链）
   - 起源时代→上古变局→文明兴衰→当代格局→未来伏笔
   - 当下一切现状，都是历史事件的必然结果

5. **社会与文明**
   - 资源分布+历史走向+核心规则 → 共同决定社会形态
   - 包含：政体、阶层、宗教、律法、货币、民俗

6. **文化与禁忌**
   - 所有禁忌源于真实灾难、法则惩罚或历史教训，而非单纯迷信

### 第三层：表面呈现（冰山一角）
- 只展现与故事直接相关的设定
- 通过人物对话、环境描写、剧情事件自然展现，绝不写设定说明文
- 未展现的底层设定为故事提供逻辑支撑和拓展空间

## 构建原则

1. **够用就好**：世界观为故事服务，只构建故事需要的部分
2. **因果推导**：每个设定必须有"为什么"，拒绝凭空堆砌
3. **逻辑自洽**：前后一致，不可自相矛盾；特殊例外必须有合理代价
4. **渐进展开**：随故事推进逐步补充，不要一开始就穷尽所有设定
5. **贴近现实**：即使是奇幻世界，也要有现实的影子（吃饭、赚钱、生存）

## 推导链检查规则

创建新设定时，必须验证推导链完整性：
- 新规则 → 是否与底层铁则兼容？能否推导出社会影响？
- 新人物 → 是否符合核心规则约束？社会地位是否有历史依据？
- 新地点 → 地理位置是否影响资源/势力/文化？氛围是否与历史事件呼应？
- 新伏笔 → 埋设时机是否有逻辑依据？揭示后是否改变读者对世界观的认知？

## 一致性原则
- 新增规则必须与已有规则兼容，特别是宪法级规则不可违背
- 人物设定必须符合核心规则约束（如职业、技术、资源、法律或能力边界）
- 地点设定必须符合地理逻辑和资源分布
- 伏笔的埋设和揭示必须有时间线上的合理性
- 当检测到潜在冲突时，主动提醒用户并给出调整建议
- 如果新设定与底层铁则冲突，必须明确指出并建议修改方向

## 各维度设计规范

### 世界规则
- 规则名称：简洁有力的命名
- 规则描述：详细说明核心内容，包含推导依据
- 约束条件：从规则推导出的具体限制（必须包含代价/限制）
- 分类：magic/technology/society/combat/history/general
- 优先级：critical（宪法级，不可违背）/high/normal/low
- 推导影响：该规则对社会结构、人物行为、经济体系的影响

### 人物核心
- 姓名 + 别名
- 外貌特征（受种族/环境影响）
- 性格特点（受文化/经历塑造）
- 欲望（外在目标）vs 深层需求（内在渴望）
- 人物弧光（从A到B的转变）
- 规则机制位置（职业权限/能力范围/资源约束/限制代价）
- 社会地位（受阶层/势力/历史影响）
- 人物关系网络

### 地点网络
- 名称 + 描述
- 地理特征（地形/气候/资源）
- 氛围（受历史事件/文化影响）
- 从属关系（层级结构）
- 势力分布（谁控制这里？为什么？）

### 伏笔设计
- 伏笔名称 + 描述
- 埋设章节/场景（为什么在这个时机埋？）
- 计划揭示章节/场景（揭示后如何改变认知？）
- 关联人物和规则
- 状态：planned/active/dormant/revealing/resolved/aborted

## 输出规范
- 你是一位有灵性的世界观构建伙伴，用自然、有温度的语言与作者对话，像一位经验丰富的世界观顾问
- **对话风格**：
  - 不要机械地列出选项让用户选择，而是像朋友一样引导："我觉得我们可以先……你觉得呢？"
  - 用故事化的语言描述设定，而非干巴巴的定义。例如："关键资源守恒——这座城的净水配额是有限的，权力本质上来自分配权。谁能决定水流向哪里，谁就决定了街区的秩序。"
  - 给出建议后等待用户回应，而非一次性倾倒所有内容
  - 当需要用户去某个Tab操作时，用自然的引导语："现在去【世界规则】Tab，建立那3条铁则吧。建好后回来告诉我，我们接着推导核心规则——它会决定人物选择、冲突边界和社会结构。"
  - 主动追问、质疑、激发思考，而不是被动等待指令
- **设定生成**：当你和用户讨论达成共识后，在回复中用自然文字呈现设定内容，同时在末尾用 JSON 格式输出供系统自动填充表单：
  - 规则：```json\n{"fill": {"name": "...", "description": "...", "constraints": [...], "category": "...", "priority": "...", "locked": false}}\n```
  - 人物（方案 3 A7 补全字段）：```json\n{"fill": {"name": "...", "aliases": [], "appearance": "...", "personality": "...", "desire": "...", "deep_need": "...", "arc": "...", "role": "主角/配角/反派/导师", "faction": "所属阵营", "status": "active"}}\n```
  - 地点：```json\n{"fill": {"name": "...", "description": "...", "atmosphere": "...", "parent_location": "..."}}\n```
  - 伏笔：```json\n{"fill": {"name": "...", "description": "...", "bury_window_start": 1, "reveal_window_start": 10, "related_characters": [], "notes": "..."}}\n```
  - 物品（方案 3 B1 新增）：```json\n{"fill": {"name": "...", "aliases": [], "appearance": "...", "function": "...", "origin": "...", "owner": "...", "status": "intact", "importance": "major/moderate/minor", "first_appear_chapter": 1, "related_foreshadowing": ""}}\n```
- 可以在一次回复中输出多个 fill 块
- **重要**：JSON fill 块是给系统用的，不要在正文中重复展示 JSON 内容。正文用排版好的自然文字呈现设定
"""

TAB_ROLE_PROMPTS = {
    "overview": """
当前为概览模式。你的角色是「世界观导师」，职责是：
1. 评估当前世界观的整体健康度（覆盖度、推导链完整性）
2. 指出最紧迫的空白维度，给出构建优先级
3. 帮助用户规划世界观构建路线图
4. 回答"我接下来该做什么"类问题
不要直接生成具体设定，而是引导用户到对应Tab去构建。
优先调用 check_worldview_completeness 了解当前覆盖度。
""",
    "rules": """
当前为规则模式。你的角色是「规则架构师」，职责是：
1. 引导用户按 L0底层铁则→L1核心规则/能力机制→L2社会/历史 的顺序构建规则
2. 每条规则必须包含：推导依据（为什么需要这条规则）、约束条件（具体限制）、代价/后果（违反会怎样）
3. 检测规则间的推导链：新规则是否从已有规则推导而来？是否有冲突？
4. 主动提示缺失的推导环节（如：有了"关键资源有限"铁则，是否需要推导出配给、黑市或阶层固化等社会规则？）
创建新规则前，优先调用 check_consistency 检查与已有规则的兼容性。
""",
    "characters": """
当前为人物模式。你的角色是「人物铸造师」，职责是：
1. 每个人物必须回答：ta在核心规则或社会机制中处于什么位置？ta的社会地位由什么决定？ta的欲望如何被历史/文化塑造？
2. 人物关系网络必须反映世界观结构（师徒关系→行业传承；血缘关系→家族资源；雇佣关系→权力依附）
3. 人物弧光必须与世界观的底层矛盾呼应
4. 主动检测：人物的能力、权限或资源是否超出核心规则约束？社会地位是否有历史依据？
创建新人物前，优先调用 get_world_rules 获取核心规则约束，确保人物不违反规则。
""",
    "locations": """
当前为地点模式。你的角色是「地理编织师」，职责是：
1. 每个地点必须回答：这里的地理特征如何影响资源分布？资源如何决定势力格局？历史事件如何塑造氛围？
2. 地点层级关系必须合理（城市→区域→大陆）
3. 地点间的关系必须反映世界观（敌对势力的领地、贸易路线的枢纽）
4. 主动检测：地点的氛围是否与历史事件呼应？资源是否与地理匹配？
创建新地点前，优先调用 list_characters 获取可关联人物，调用 get_world_rules 获取地理相关规则。
""",
    "foreshadowing": """
当前为伏笔模式。你的角色是「伏笔织网师」，职责是：
1. 每条伏笔必须回答：为什么在这个时机埋设？揭示后如何改变读者对世界观的认知？与哪些规则/人物相关？
2. 伏笔网络必须形成"认知差"：读者在揭示前后的理解必须不同
3. 伏笔的埋设和揭示必须有世界观层面的逻辑依据（不是随意安排）
4. 主动检测：是否有已过reveal_window_start但未揭示的伏笔？是否有孤立伏笔（无关联人物/规则）？
创建新伏笔前，优先调用 get_outline_summary 获取大纲章节信息，确保伏笔时机合理。
""",
    "promotions": """
当前为设定变更模式。你的角色是「设定变更审阅师」，负责把正文观察和设定晋升放在同一条审阅链上：
1. 先审查章节正文反向投影出的观察，区分新增、补充、提及、揭示和冲突；根据正文证据、章节位置和置信度判断观察是否可信。
2. 再审查自动提取的设定种子，判断是否值得晋升为正式世界观，并评估它是否填补推导链空白、是否与已有设定冲突。
3. 对确认后的内容建议晋升目标类型（人物/规则/地点）和具体数据；对证据不足、冲突或低质量内容建议暂缓、拒绝或先补充上下文。
4. 明确区分“正文里实际发生了什么”和“哪些内容进入正式世界观”，不能把待确认观察直接当成正式设定。

鉴定标准：
- 引用次数≥3：高价值，建议晋升
- 引用次数1-2：观察中，建议暂缓
- 与已有设定冲突：标记冲突，建议修改后晋升
- 无推导依据：建议补充后晋升
""",
    "discoveries": """
当前为正文发现模式。你的角色是「正文发现审阅师」，职责是：
1. 解释正文反向投影出的设定观察，区分新增、补充、提及、揭示和冲突；
2. 根据正文证据、章节位置和置信度判断观察是否可信；
3. 检查观察是否与正式世界观、人物、地点和伏笔冲突；
4. 引导用户确认、拒绝或先补充上下文，不把正文观察默认当成正式设定；
5. 需要纳入正式世界观时，给出明确的目标实体和影响范围。

正文发现是待确认观察，不是普通聊天记录，也不是无证据的新设定。
""",
    "style": """
当前为文笔模式。你的角色是「风格调律师」，职责是：
1. 根据世界观氛围推荐合适的文笔风格（神话/史诗气质→典雅凝练；都市现实→现代简洁；末世/废土→冷硬克制）
2. 分析已上传书籍的风格是否与世界观匹配
3. 建议风格调整方向：词汇偏好、句式特征、语气基调
4. 不直接生成文笔画像，而是给出风格建议和参考方向

风格与世界观对应关系：
- 神话/高幻想世界观 → 典雅、意象丰富、节奏庄重
- 科幻/未来世界观 → 简洁、技术感、理性克制
- 末世/废土世界观 → 冷硬、粗粝、生存本能
- 都市/现实世界观 → 现代、生活化、情感细腻
""",
}

TAB_ROLE_NAMES = {
    "overview": "世界观导师",
    "rules": "规则架构师",
    "characters": "人物铸造师",
    "locations": "地理编织师",
    "foreshadowing": "伏笔织网师",
    "promotions": "设定变更审阅师",
    "discoveries": "正文发现审阅师",
    "style": "风格调律师",
}

TAB_TOOL_MAPPING: dict[str, list[str]] = {
    "overview": [
        "check_worldview_completeness", "get_world_rules",
        "list_characters", "list_locations", "get_foreshadowing",
    ],
    "rules": [
        "get_world_rules", "check_consistency", "get_outline_summary",
    ],
    "characters": [
        "get_character", "list_characters", "get_world_rules",
        "check_consistency",
    ],
    "locations": [
        "get_location", "list_locations", "list_characters",
        "get_world_rules",
    ],
    "foreshadowing": [
        "get_foreshadowing", "get_outline_summary",
        "list_characters", "get_world_rules",
    ],
    "promotions": [
        "get_foreshadowing", "check_consistency",
        "list_characters", "list_locations", "get_world_rules",
    ],
    "discoveries": [
        "get_world_rules", "get_foreshadowing",
        "list_characters", "list_locations", "check_consistency",
    ],
    "style": [
        "get_world_rules", "list_locations",
    ],
}

TOOL_MAP = {t["function"]["name"]: t for t in WORLDBUILDER_TOOLS}


@dataclass
class SubAgentConfig:
    name: str
    display_name: str
    tab: str
    role_prompt: str
    tool_names: list[str]
    persona: str = ""
    default_skills: list[str] = field(default_factory=list)

    @property
    def tools(self) -> list[dict]:
        return [TOOL_MAP[n] for n in self.tool_names if n in TOOL_MAP]


@dataclass
class SubAgentState:
    iteration: int = 0
    tool_calls_count: int = 0
    last_tool: str | None = None
    derivation_chain_broken: bool = False
    consistency_issues: list = field(default_factory=list)
    suggested_items: list = field(default_factory=list)
    called_tools: set = field(default_factory=set)
    phase: str = "gather"
    tab_context: str | None = None
    project_snapshot: dict | None = None
    best_result: dict | None = None
    consecutive_no_tool: int = 0
    error_count: int = 0
    last_response_text: str = ""

    def transition_to(self, new_phase: str):
        valid_transitions = {
            "gather": ["act"],
            "act": ["verify", "gather"],
            "verify": ["act", "done"],
            "done": [],
        }
        if new_phase in valid_transitions.get(self.phase, []):
            self.phase = new_phase

    def record_tool_call(self, tool_name: str, result: dict):
        self.tool_calls_count += 1
        self.last_tool = tool_name
        self.consecutive_no_tool = 0
        if tool_name == "check_consistency":
            conflicts = result.get("conflicts", [])
            if conflicts:
                self.consistency_issues.extend(conflicts)
                self.derivation_chain_broken = True
        if tool_name == "check_worldview_completeness":
            issues = result.get("derivation_issues", [])
            if issues:
                self.consistency_issues.extend(issues)
        if self.phase == "gather":
            self.transition_to("act")

    def record_text_response(self, text: str):
        self.consecutive_no_tool += 1
        self.last_response_text = text

    def get_no_progress_hint(self) -> str | None:
        if self.iteration >= 3 and self.tool_calls_count >= 3 and not self.suggested_items:
            return "请总结当前讨论并给出明确建议，避免反复调用工具。"
        if self.consecutive_no_tool >= 2 and self.phase == "act":
            return "请基于已收集的信息给出明确建议或生成设定，避免反复讨论。"
        return None

    def get_phase_instruction(self) -> str | None:
        if self.phase == "gather" and self.iteration == 0:
            return "当前处于信息收集阶段，请先调用工具了解已有世界观，再给出建议。"
        if self.phase == "act" and self.derivation_chain_broken:
            issues_text = "\n".join(f"- {iss}" for iss in self.consistency_issues[:3])
            return f"⚠️ 检测到推导链断裂：\n{issues_text}\n请优先修复上述问题。"
        return None


@dataclass
class IndividualMemory:
    last_topics: list = field(default_factory=list)
    unfinished_tasks: list = field(default_factory=list)
    key_decisions: list = field(default_factory=list)
    user_preferences: dict = field(default_factory=dict)
    last_active_at: str = ""

    def to_dict(self) -> dict:
        return {
            "last_topics": self.last_topics[-10:],
            "unfinished_tasks": self.unfinished_tasks[-10:],
            "key_decisions": self.key_decisions[-10:],
            "user_preferences": self.user_preferences,
            "last_active_at": self.last_active_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "IndividualMemory":
        return cls(
            last_topics=data.get("last_topics", []),
            unfinished_tasks=data.get("unfinished_tasks", []),
            key_decisions=data.get("key_decisions", []),
            user_preferences=data.get("user_preferences", {}),
            last_active_at=data.get("last_active_at", ""),
        )


SUB_AGENT_DEFAULT_SKILLS: dict[str, list[str]] = {
    "overview": ["worldview_completeness_check"],
    "rules": ["consistency_check", "derivation_chain_validation"],
    "characters": ["worldbuilding_assist", "consistency_check"],
    "locations": ["worldbuilding_assist"],
    "foreshadowing": ["worldbuilding_assist"],
    "promotions": ["consistency_check"],
    "discoveries": ["consistency_check"],
    "style": [],
}

SUB_AGENT_CONFIGS: dict[str, SubAgentConfig] = {}
for _tab, _prompt in TAB_ROLE_PROMPTS.items():
    SUB_AGENT_CONFIGS[_tab] = SubAgentConfig(
        name=f"wb_{_tab}",
        display_name=TAB_ROLE_NAMES[_tab],
        tab=_tab,
        role_prompt=_prompt,
        tool_names=TAB_TOOL_MAPPING.get(_tab, []),
        persona=_prompt.strip(),
        default_skills=SUB_AGENT_DEFAULT_SKILLS.get(_tab, []),
    )


class WorldbuilderSubAgent:
    def __init__(self, config: SubAgentConfig):
        self.config = config
        self.state = SubAgentState()

    async def _build_project_snapshot(self, project_id, db) -> dict:
        if not project_id or not db:
            return {}
        try:
            from app.services.memory_core import CoreMemoryService

            core_service = CoreMemoryService()
            pid = str(project_id)
            rules = await core_service.list_world_rules(pid)
            chars = await core_service.list_characters(pid)
            locs = await core_service.list_locations(pid)
            fs = await core_service.list_foreshadowing(pid)
            return {
                "rules_count": len(rules),
                "characters_count": len(chars),
                "locations_count": len(locs),
                "foreshadowing_count": len(fs),
                "critical_rules": [r.get("name", "") for r in rules if r.get("priority") == "critical"],
                "rule_categories": list({r.get("category", "") for r in rules}),
                "character_names": [c.get("name", "") if isinstance(c, dict) else getattr(c, "name", "") for c in chars[:20]],
                "location_names": [loc.get("name", "") for loc in locs[:20]],
            }
        except Exception:
            return {}

    async def _verify_suggestions(
        self, suggestions: list[dict], project_id, db, llm, full_messages: list
    ) -> dict | None:
        if not suggestions or not project_id or not db:
            return None

        all_conflicts = []
        for s in suggestions:
            dimension = self._guess_suggestion_dimension(s)
            if not dimension:
                continue
            try:
                consistency_result = await _execute_tool(
                    "check_consistency",
                    {"dimension": dimension, "data": s},
                    project_id,
                    db,
                )
                conflicts = consistency_result.get("conflicts", [])
                all_conflicts.extend(conflicts)
            except Exception:
                continue

        if not all_conflicts:
            self.state.transition_to("done")
            return None

        conflict_msgs = [c.get("message", "") for c in all_conflicts[:3]]
        verification_prompt = (
            f"⚠️ 验证发现以下一致性问题：\n"
            + "\n".join(f"- {m}" for m in conflict_msgs)
            + "\n\n请修正上述设定，确保与已有世界观一致。修正后重新输出完整的 fill 块。"
        )
        full_messages.append({"role": "user", "content": verification_prompt})

        self.state.transition_to("act")

        try:
            fix_response = await llm.generate_with_tools(
                messages=full_messages,
                tools=self.config.tools,
                temperature=0.5,
                task_type=LLMTaskType.WORLDBUILDER_WITH_TOOLS,
            )
        except Exception:
            return None

        fix_text = fix_response.get("content", "")
        if not fix_text and fix_response.get("has_tool_calls"):
            return None

        fixed_suggestions = _extract_suggestions(fix_text)
        if not fixed_suggestions and "fill" in fix_text:
            fixed_suggestions = _extract_suggestions_fuzzy(fix_text)

        if fixed_suggestions:
            self.state.transition_to("done")
            return {"response": fix_text, "suggestions": fixed_suggestions}

        return None

    @staticmethod
    def _guess_suggestion_dimension(suggestions: dict) -> str | None:
        if "category" in suggestions or "constraints" in suggestions or "priority" in suggestions:
            return "rule"
        if "appearance" in suggestions or "personality" in suggestions or "desire" in suggestions or "arc" in suggestions:
            return "character"
        if "atmosphere" in suggestions or "parent_location" in suggestions:
            return "location"
        if "bury_window_start" in suggestions or "reveal_window_start" in suggestions or "related_characters" in suggestions:
            return "foreshadowing"
        return None

    async def execute(
        self,
        system_prompt: str,
        messages: list[dict],
        project_id,
        db,
        llm,
    ) -> dict:
        self.state = SubAgentState()
        self.state.tab_context = self.config.tab
        self.state.project_snapshot = await self._build_project_snapshot(project_id, db)

        if self.state.project_snapshot:
            snapshot = self.state.project_snapshot
            system_prompt += f"\n## 项目快照（缓存）\n"
            system_prompt += f"- 规则：{snapshot.get('rules_count', 0)}条"
            if snapshot.get("critical_rules"):
                system_prompt += f"（宪法级：{', '.join(snapshot['critical_rules'][:5])}）"
            system_prompt += f"\n- 人物：{snapshot.get('characters_count', 0)}人"
            if snapshot.get("character_names"):
                system_prompt += f"（{', '.join(snapshot['character_names'][:5])}）"
            system_prompt += f"\n- 地点：{snapshot.get('locations_count', 0)}处\n"

        full_messages = [{"role": "system", "content": system_prompt}]
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role in ("user", "assistant") and content:
                full_messages.append({"role": role, "content": content})

        max_iterations = 50
        for i in range(max_iterations):
            self.state.iteration = i
            logger.info(
                f"[SubAgent:{self.config.name}] iteration={i}, "
                f"phase={self.state.phase}, "
                f"messages_count={len(full_messages)}"
            )

            if i > 0 and i % 5 == 0:
                full_messages = _compress_messages(full_messages)

            phase_instruction = self.state.get_phase_instruction()
            if phase_instruction:
                full_messages.append({"role": "user", "content": phase_instruction})

            no_progress_hint = self.state.get_no_progress_hint()
            if no_progress_hint:
                full_messages.append({"role": "user", "content": no_progress_hint})

            try:
                response = await llm.generate_with_tools(
                    messages=full_messages,
                    tools=self.config.tools,
                    temperature=0.7,
                    task_type=LLMTaskType.WORLDBUILDER_WITH_TOOLS,
                )
            except Exception as e:
                logger.error(f"[SubAgent:{self.config.name}] LLM error: {e}")
                self.state.error_count += 1
                if self.state.error_count <= 2 and full_messages:
                    full_messages.append({
                        "role": "user",
                        "content": "生成过程中出现临时错误，请重新尝试。"
                    })
                    continue
                if self.state.best_result:
                    return self.state.best_result
                if self.state.consistency_issues:
                    return {
                        "response": "生成过程中出现错误，但已发现以下一致性问题：\n"
                        + "\n".join(f"- {iss}" for iss in self.state.consistency_issues[:5]),
                        "suggestions": [],
                    }
                return {"response": "生成过程中出现错误，请重试。", "suggestions": []}

            finish_reason = response.get("finish_reason", "")
            if finish_reason == "length":
                partial_text = response.get("content", "")
                if partial_text:
                    full_messages.append({"role": "assistant", "content": partial_text})
                    full_messages.append({
                        "role": "user",
                        "content": "你的回复被截断了，请从断点继续输出。"
                    })
                    logger.info(f"[SubAgent:{self.config.name}] Truncated, requesting continuation")
                    continue

            if not response.get("has_tool_calls"):
                logger.info(
                    f"[SubAgent:{self.config.name}] No tool calls, returning text"
                )
                text = response.get("content", "")
                suggestions = _extract_suggestions(text)
                if not suggestions and "fill" in text:
                    suggestions = _extract_suggestions_fuzzy(text)
                self.state.suggested_items = suggestions
                self.state.record_text_response(text)

                mentions = _extract_mentions(text)
                navigates = _extract_navigates(text)
                display_text = _clean_inline_tags(text)

                result = {
                    "response": display_text,
                    "suggestions": suggestions,
                    "mentions": mentions,
                    "navigates": navigates,
                }

                if suggestions:
                    self.state.best_result = result
                    if self.state.phase == "act":
                        self.state.transition_to("verify")
                        verified = await self._verify_suggestions(
                            suggestions, project_id, db, llm, full_messages
                        )
                        if verified:
                            verified["mentions"] = mentions
                            verified["navigates"] = navigates
                            return verified
                else:
                    self.state.best_result = self.state.best_result or result

                return result

            assistant_msg = response.get("assistant_message", {})
            full_messages.append(assistant_msg)

            tool_calls = response.get("tool_calls", [])
            logger.info(
                f"[SubAgent:{self.config.name}] Got {len(tool_calls)} tool calls"
            )
            for tc in tool_calls:
                fn_name = tc.get("function", {}).get("name", "")
                fn_args_str = tc.get("function", {}).get("arguments", "{}")
                try:
                    fn_args = json.loads(fn_args_str)
                except json.JSONDecodeError:
                    fn_args = _try_fix_tool_args(fn_args_str)

                if isinstance(fn_args.get("data"), str):
                    try:
                        fn_args["data"] = json.loads(fn_args["data"])
                    except (json.JSONDecodeError, TypeError):
                        pass

                if fn_name not in TOOL_MAP:
                    tool_result = {"error": f"未知工具：{fn_name}，请使用可用工具列表中的工具。"}
                    full_messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": json.dumps(tool_result, ensure_ascii=False),
                    })
                    continue

                tool_key = f"{fn_name}:{fn_args_str}"
                if tool_key in self.state.called_tools:
                    logger.info(
                        f"[SubAgent:{self.config.name}] Duplicate tool call blocked: {fn_name}"
                    )
                    tool_result = {"error": "该工具已调用过，请直接基于已有信息回复。"}
                else:
                    self.state.called_tools.add(tool_key)
                    logger.info(
                        f"[SubAgent:{self.config.name}] Executing tool: {fn_name}"
                    )
                    try:
                        tool_result = await _execute_tool(
                            fn_name, fn_args, project_id, db
                        )
                    except Exception as e:
                        logger.error(
                            f"[SubAgent:{self.config.name}] Tool error: {e}"
                        )
                        self.state.error_count += 1
                        if self.state.error_count <= 2:
                            tool_result = {"error": f"工具执行失败，请调整参数重试。"}
                        else:
                            tool_result = {
                                "error": f"工具执行失败：{str(e)[:100]}，请尝试其他方式。"
                            }
                    self.state.record_tool_call(fn_name, tool_result)

                    if fn_name == "check_consistency" and tool_result.get("conflicts"):
                        conflict_msgs = [
                            c.get("message", "") for c in tool_result.get("conflicts", [])[:3]
                        ]
                        full_messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": json.dumps(tool_result, ensure_ascii=False, default=str),
                        })
                        full_messages.append({
                            "role": "user",
                            "content": f"一致性检查发现冲突：\n"
                            + "\n".join(f"- {m}" for m in conflict_msgs)
                            + "\n请修正设定以解决上述冲突。"
                        })
                        self.state.transition_to("act")
                        continue

                full_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": json.dumps(
                            _compress_tool_result(fn_name, tool_result) if isinstance(tool_result, dict) and "error" not in tool_result else tool_result,
                            ensure_ascii=False, default=str
                        ),
                    }
                )

        if self.state.best_result:
            return self.state.best_result
        return {"response": "工具调用次数已达上限，请简化请求。", "suggestions": []}


async def _execute_tool(name, args, project_id, db):
    if name.startswith("weave_"):
        from app.agents.weave_coordinator import WeaveCoordinatorAgent
        coordinator = WeaveCoordinatorAgent()
        return await coordinator.handle_tool(name, args, project_id, db, caller="worldbuilder")

    from app.services.memory_core import CoreMemoryService

    core_service = CoreMemoryService()
    pid = str(project_id)

    if name == "get_world_rules":
        rules = await core_service.list_world_rules(pid)
        category = args.get("category")
        if category:
            rules = [r for r in rules if r.get("category") == category]
        return {
            "rules": rules,
            "count": len(rules),
            "_hint": "已返回世界规则列表。基于这些信息回答用户问题或检查一致性。",
        }

    elif name == "get_character":
        char_name = args.get("name", "")
        characters = await core_service.list_characters(pid)
        for c in characters:
            c_data = c.model_dump() if hasattr(c, "model_dump") else c
            if c_data.get("name") == char_name:
                return {
                    "character": c_data,
                    "_hint": "已获取人物详情。基于此信息回答用户问题。",
                }
        return {"error": f"未找到人物：{char_name}"}

    elif name == "list_characters":
        characters = await core_service.list_characters(pid)
        char_list = []
        for c in characters:
            c_data = c.model_dump() if hasattr(c, "model_dump") else c
            char_list.append({
                "name": c_data.get("name", ""),
                "personality": c_data.get("personality", "")[:60],
                "desire": c_data.get("desire", "")[:60],
            })
        return {
            "characters": char_list,
            "count": len(char_list),
            "_hint": "已返回人物概要列表。如需详情，使用 get_character 工具。",
        }

    elif name == "get_location":
        loc_name = args.get("name", "")
        locations = await core_service.list_locations(pid)
        for loc in locations:
            if loc.get("name") == loc_name:
                return {
                    "location": loc,
                    "_hint": "已获取地点详情。基于此信息回答用户问题。",
                }
        return {"error": f"未找到地点：{loc_name}"}

    elif name == "list_locations":
        locations = await core_service.list_locations(pid)
        loc_list = []
        for loc in locations:
            loc_list.append({
                "name": loc.get("name", ""),
                "atmosphere": loc.get("atmosphere", ""),
                "parent_location": loc.get("parent_location", ""),
            })
        return {
            "locations": loc_list,
            "count": len(loc_list),
            "_hint": "已返回地点概要列表。如需详情，使用 get_location 工具。",
        }

    elif name == "get_foreshadowing":
        foreshadowing = await core_service.list_foreshadowing(pid)
        fs_name = args.get("name")
        fs_status = args.get("status")
        if fs_status:
            foreshadowing = [f for f in foreshadowing if f.get("status") == fs_status]
        if fs_name:
            for f in foreshadowing:
                if f.get("name") == fs_name:
                    return {
                        "foreshadowing": f,
                        "_hint": "已获取伏笔详情。基于此信息回答用户问题。",
                    }
            return {"error": f"未找到伏笔：{fs_name}"}
        return {
            "foreshadowing_list": foreshadowing,
            "count": len(foreshadowing),
            "_hint": "已返回伏笔列表。基于这些信息回答用户问题。",
        }

    elif name == "check_consistency":
        dimension = args.get("dimension", "")
        data = args.get("data", {})
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                data = {}
        if not isinstance(data, dict):
            data = {}
        conflicts = []

        if dimension == "rule":
            rules = await core_service.list_world_rules(pid)
            new_name = data.get("name", "")
            new_desc = data.get("description", "")
            new_category = data.get("category", "")
            new_constraints = data.get("constraints", [])

            for rule in rules:
                if rule.get("name") == new_name:
                    conflicts.append({
                        "type": "duplicate_name",
                        "message": f"已存在同名规则：{new_name}",
                        "existing_rule": rule.get("description", "")[:100],
                    })

                if rule.get("category") == new_category and rule.get("priority") == "critical":
                    rule_constraints = rule.get("constraints", [])
                    for nc in new_constraints:
                        for rc in rule_constraints:
                            if _is_constraint_conflict(nc, rc):
                                conflicts.append({
                                    "type": "constraint_conflict",
                                    "message": f"新规则约束「{nc}」可能与宪法级规则「{rule.get('name')}」的约束「{rc}」冲突",
                                    "critical_rule": rule.get("name"),
                                })

            new_priority = data.get("priority", "")
            if new_priority == "critical":
                for rule in rules:
                    if rule.get("priority") == "critical" and rule.get("category") != new_category:
                        conflicts.append({
                            "type": "cross_category_critical",
                            "message": f"新宪法级规则可能与「{rule.get('name')}」（{rule.get('category')}分类）产生跨域冲突，建议检查兼容性",
                            "existing_rule": rule.get("name"),
                        })

        elif dimension == "character":
            characters = await core_service.list_characters(pid)
            new_name = data.get("name", "")
            for c in characters:
                c_data = c.model_dump() if hasattr(c, "model_dump") else c
                if c_data.get("name") == new_name:
                    conflicts.append({
                        "type": "duplicate_name",
                        "message": f"已存在同名人物：{new_name}",
                    })
                aliases = c_data.get("aliases", [])
                new_aliases = data.get("aliases", [])
                for na in new_aliases:
                    if na in aliases:
                        conflicts.append({
                            "type": "duplicate_alias",
                            "message": f"别名「{na}」已被人物「{c_data.get('name')}」使用",
                        })

        elif dimension == "location":
            locations = await core_service.list_locations(pid)
            new_name = data.get("name", "")
            for loc in locations:
                if loc.get("name") == new_name:
                    conflicts.append({
                        "type": "duplicate_name",
                        "message": f"已存在同名地点：{new_name}",
                    })

        elif dimension == "foreshadowing":
            foreshadowing = await core_service.list_foreshadowing(pid)
            new_name = data.get("name", "")
            for f in foreshadowing:
                if f.get("name") == new_name:
                    conflicts.append({
                        "type": "duplicate_name",
                        "message": f"已存在同名伏笔：{new_name}",
                    })

        return {
            "dimension": dimension,
            "conflicts": conflicts,
            "conflict_count": len(conflicts),
            "is_consistent": len(conflicts) == 0,
            "_hint": "一致性检查完成。"
            + (
                "未发现冲突，可以安全创建。"
                if not conflicts
                else f"发现 {len(conflicts)} 个潜在冲突，请在回复中提醒用户。"
            ),
        }

    elif name == "check_worldview_completeness":
        rules = await core_service.list_world_rules(pid)
        characters = await core_service.list_characters(pid)
        locations = await core_service.list_locations(pid)
        foreshadowing = await core_service.list_foreshadowing(pid)

        dimensions = {
            "geography": {
                "label": "地理与环境",
                "covered": len(locations) > 0,
                "detail": f"已设定{len(locations)}处地点" if locations else "未设定任何地点",
            },
            "power_system": {
                "label": "核心规则/能力机制",
                "covered": any(
                    r.get("category") in ("magic", "combat", "technology")
                    for r in rules
                ),
                "detail": (
                    f"已设定{len([r for r in rules if r.get('category') in ('magic', 'combat', 'technology')])}条核心机制规则"
                    if any(r.get("category") in ("magic", "combat", "technology") for r in rules)
                    else "未设定核心规则/能力机制"
                ),
            },
            "race_species": {
                "label": "种族与物种",
                "covered": len(characters) > 0,
                "detail": f"已设定{len(characters)}个人物" if characters else "未设定任何人物",
            },
            "history": {
                "label": "历史与时间线",
                "covered": any(r.get("category") == "history" for r in rules),
                "detail": (
                    f"已设定{len([r for r in rules if r.get('category') == 'history'])}条历史规则"
                    if any(r.get("category") == "history" for r in rules)
                    else "未设定历史法则"
                ),
            },
            "society": {
                "label": "社会与文明",
                "covered": any(r.get("category") == "society" for r in rules),
                "detail": (
                    f"已设定{len([r for r in rules if r.get('category') == 'society'])}条社会规则"
                    if any(r.get("category") == "society" for r in rules)
                    else "未设定社会结构规则"
                ),
            },
            "culture": {
                "label": "文化与禁忌",
                "covered": any(
                    r.get("category") == "general"
                    and r.get("priority") in ("critical", "high")
                    for r in rules
                ),
                "detail": (
                    "已设定文化禁忌规则"
                    if any(
                        r.get("category") == "general"
                        and r.get("priority") in ("critical", "high")
                        for r in rules
                    )
                    else "未设定文化禁忌"
                ),
            },
        }

        covered_count = sum(1 for d in dimensions.values() if d["covered"])
        total_count = len(dimensions)
        critical_rules = [r for r in rules if r.get("priority") == "critical"]

        derivation_issues = []
        if critical_rules:
            for r in critical_rules:
                has_social_impact = any(
                    sr.get("category") == "society"
                    and r.get("name", "") in str(sr.get("description", ""))
                    for sr in rules
                )
                if not has_social_impact:
                    derivation_issues.append(
                        f"宪法级规则「{r.get('name', '')}」尚未推导出社会结构影响"
                    )

        if any(r.get("category") in ("magic", "combat", "technology") for r in rules):
            has_cost = any(
                "代价" in str(r.get("constraints", []))
                or "消耗" in str(r.get("constraints", []))
                for r in rules
                if r.get("category") in ("magic", "combat", "technology")
            )
            if not has_cost:
                derivation_issues.append("核心机制缺少使用代价/消耗约束，可能导致逻辑崩塌")

        return {
            "dimensions": dimensions,
            "coverage": f"{covered_count}/{total_count}",
            "coverage_percentage": round(covered_count / total_count * 100),
            "critical_rules_count": len(critical_rules),
            "derivation_issues": derivation_issues,
            "suggestion": (
                "建议优先补全未覆盖维度"
                if covered_count < total_count
                else "六大维度已基本覆盖，建议深化推导链"
            ),
            "_hint": f"世界观覆盖度检查完成：{covered_count}/{total_count}维度已覆盖。"
            + ("存在推导链问题，请提醒用户。" if derivation_issues else ""),
        }

    elif name == "get_outline_summary":
        from app.services.outline_index_service import OutlineIndexService

        index_service = OutlineIndexService()
        chapter_number = args.get("chapter_number")

        if chapter_number:
            chapter = await index_service.get_chapter_detail(pid, chapter_number, db)
            if chapter:
                return {
                    "chapter": chapter,
                    "_hint": "已获取章节大纲详情。基于此信息辅助世界观设计。",
                }
            return {"error": f"未找到第 {chapter_number} 章"}

        index_md = await index_service.get_index(pid, db)
        if index_md:
            return {
                "outline_index": index_md,
                "_hint": "已获取大纲索引。如需某章详情，使用 chapter_number 参数再次调用。",
            }

        from app.db.db_models import Project

        project = await db.get(Project, project_id)
        if project and project.outline_data:
            chapters = project.outline_data.get("chapters", [])
            summary_parts = []
            for ch in chapters[:10]:
                summary_parts.append(
                    f"第{ch.get('chapter_number','?')}章 {ch.get('title','')}: "
                    f"{ch.get('main_conflict','')[:50]}"
                )
            return {
                "outline_summary": "\n".join(summary_parts),
                "total_chapters": len(chapters),
                "_hint": "已返回大纲摘要（前10章）。基于此信息辅助世界观设计。",
            }
        return {"info": "当前项目暂无大纲数据"}

    return {"error": f"未知工具: {name}"}


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


def _extract_mentions(text: str) -> list[dict]:
    import re
    mentions = []
    pattern = r'\{"mention":\s*\{"type":\s*"([^"]+)",\s*"name":\s*"([^"]+)"\}\}'
    for m in re.finditer(pattern, text):
        mentions.append({"type": m.group(1), "name": m.group(2)})
    return mentions


def _extract_navigates(text: str) -> list[dict]:
    import re
    navigates = []
    pattern = r'\{"navigate":\s*\{"tab":\s*"([^"]+)"(?:,\s*"reason":\s*"([^"]*)")?\}\}'
    for m in re.finditer(pattern, text):
        navigates.append({"tab": m.group(1), "reason": m.group(2) or ""})
    return navigates


def _clean_inline_tags(text: str) -> str:
    import re
    text = re.sub(r'\{"mention":\s*\{[^}]+\}\}', '', text)
    text = re.sub(r'\{"navigate":\s*\{[^}]+\}\}', '', text)
    return text.strip()


def _extract_suggestions(response: str) -> list[dict]:
    try:
        import re

        matches = re.findall(r"```json\s*(\{.*?\})\s*```", response, re.DOTALL)
        if not matches:
            return []
        all_fills = []
        for match_str in matches:
            try:
                data = json.loads(match_str)
                if "fill" in data:
                    all_fills.append(data["fill"])
            except (json.JSONDecodeError, Exception):
                continue
        return all_fills
    except Exception:
        return []


def _extract_suggestions_fuzzy(text: str) -> list[dict]:
    import re
    try:
        patterns = [
            r'\{\s*"fill"\s*:\s*\{[^}]*\}\s*\}',
            r'"fill"\s*:\s*\{[^}]*\}',
        ]
        all_fills = []
        seen = set()
        for pattern in patterns:
            matches = re.findall(pattern, text, re.DOTALL)
            for match_str in matches:
                try:
                    if not match_str.strip().startswith("{"):
                        match_str = "{" + match_str
                    if not match_str.strip().endswith("}"):
                        match_str = match_str + "}"
                    data = json.loads(match_str)
                    if "fill" in data:
                        fill_data = data["fill"]
                        key = json.dumps(fill_data, sort_keys=True, ensure_ascii=False)
                        if key not in seen:
                            seen.add(key)
                            all_fills.append(fill_data)
                except (json.JSONDecodeError, Exception):
                    continue
        return all_fills
    except Exception:
        return []


def _try_fix_tool_args(args_str: str) -> dict:
    if not args_str or not isinstance(args_str, str):
        return {}
    try:
        return json.loads(args_str)
    except json.JSONDecodeError:
        pass
    import re
    fixed = args_str.strip()
    if not fixed.startswith("{"):
        brace_start = fixed.find("{")
        if brace_start >= 0:
            fixed = fixed[brace_start:]
    if not fixed.endswith("}"):
        brace_end = fixed.rfind("}")
        if brace_end >= 0:
            fixed = fixed[:brace_end + 1]
    try:
        return json.loads(fixed)
    except json.JSONDecodeError:
        pass
    try:
        fixed = re.sub(r',\s*}', '}', fixed)
        fixed = re.sub(r',\s*]', ']', fixed)
        return json.loads(fixed)
    except json.JSONDecodeError:
        logger.warning(f"[Worldbuilder] Could not fix tool args: {args_str[:100]}")
        return {}


def _compress_tool_result(tool_name: str, result: dict) -> dict:
    if not isinstance(result, dict):
        return result
    compressed = dict(result)
    compressed.pop("_hint", None)

    if tool_name == "check_consistency":
        return {
            "dimension": compressed.get("dimension"),
            "conflicts": compressed.get("conflicts", [])[:5],
            "conflict_count": compressed.get("conflict_count", 0),
            "is_consistent": compressed.get("is_consistent", True),
        }

    if tool_name == "check_worldview_completeness":
        return {
            "coverage": compressed.get("coverage"),
            "coverage_percentage": compressed.get("coverage_percentage"),
            "critical_rules_count": compressed.get("critical_rules_count", 0),
            "derivation_issues": compressed.get("derivation_issues", [])[:5],
            "suggestion": compressed.get("suggestion"),
        }

    if tool_name == "list_characters":
        chars = compressed.get("characters", [])
        slim_chars = [{"name": c.get("name", "")} for c in chars[:15]]
        return {"characters": slim_chars, "count": compressed.get("count", 0)}

    if tool_name == "list_locations":
        locs = compressed.get("locations", [])
        slim_locs = [{"name": l.get("name", ""), "atmosphere": l.get("atmosphere", "")} for l in locs[:15]]
        return {"locations": slim_locs, "count": compressed.get("count", 0)}

    if tool_name == "get_world_rules":
        rules = compressed.get("rules", [])
        slim_rules = [
            {"name": r.get("name", ""), "category": r.get("category", ""), "priority": r.get("priority", ""), "description": r.get("description", "")[:80]}
            for r in rules[:15]
        ]
        return {"rules": slim_rules, "count": compressed.get("count", 0)}

    if tool_name == "get_foreshadowing":
        fs_list = compressed.get("foreshadowing_list", [])
        if fs_list:
            slim_fs = [
                {"name": f.get("name", ""), "status": f.get("status", "")}
                for f in fs_list[:15]
            ]
            return {"foreshadowing_list": slim_fs, "count": compressed.get("count", 0)}
        return compressed

    return compressed


def _compress_messages(messages: list[dict], max_rounds: int = 20, keep_recent: int = 10) -> list[dict]:
    non_system = [m for m in messages if m.get("role") != "system"]
    system_msgs = [m for m in messages if m.get("role") == "system"]

    if len(non_system) <= max_rounds * 2:
        return messages

    recent = non_system[-(keep_recent * 2):]
    older = non_system[:-(keep_recent * 2)]

    key_summaries = []
    for i, m in enumerate(older):
        content = m.get("content", "")
        if m.get("role") == "assistant" and "fill" in content:
            key_summaries.append(f"[关键决策] {content[:100]}")

    summary_text = ""
    if key_summaries:
        summary_text = "\n".join(key_summaries[-5:])
    else:
        user_msgs = [m.get("content", "")[:50] for m in older if m.get("role") == "user"]
        if user_msgs:
            summary_text = "早期讨论：" + "；".join(user_msgs[-5:])

    compressed = list(system_msgs)
    if summary_text:
        compressed.append({"role": "user", "content": f"[历史摘要] {summary_text}"})
    compressed.extend(recent)

    return compressed


async def _load_individual_memory(tab: str, project_id, db) -> IndividualMemory:
    if not project_id or not db:
        return IndividualMemory()
    try:
        from app.db.db_models import Project

        project = await db.get(Project, project_id)
        if project and project.core_data:
            mem = (project.core_data or {}).get("worldview_agent_memory", {})
            tab_mem = mem.get(tab, {})
            return IndividualMemory.from_dict(tab_mem)
    except Exception:
        pass
    return IndividualMemory()


async def _save_individual_memory(
    tab: str, memory: IndividualMemory, project_id, db
):
    if not project_id or not db:
        return
    try:
        from app.db.db_models import Project
        from sqlalchemy import select

        project = await db.get(Project, project_id)
        if not project:
            return

        core_data = dict(project.core_data or {})
        mem = core_data.get("worldview_agent_memory", {})
        memory.last_active_at = datetime.now(timezone.utc).isoformat()
        mem[tab] = memory.to_dict()
        core_data["worldview_agent_memory"] = mem
        project.core_data = core_data
        await db.commit()
    except Exception as e:
        logger.error(f"[Worldbuilder] Failed to save individual memory: {e}")


async def _load_notifications(tab: str, project_id, db) -> list[dict]:
    if not project_id or not db:
        return []
    try:
        from app.db.db_models import Project

        project = await db.get(Project, project_id)
        if project and project.core_data:
            state = (project.core_data or {}).get("worldview_shared_state", {})
            notifications = state.get("pending_notifications", {}).get(tab, [])
            if notifications:
                core_data = dict(project.core_data or {})
                shared = core_data.get("worldview_shared_state", {})
                pn = shared.get("pending_notifications", {})
                pn.pop(tab, None)
                shared["pending_notifications"] = pn
                core_data["worldview_shared_state"] = shared
                project.core_data = core_data
                await db.commit()
            return notifications
    except Exception:
        pass
    return []


async def _send_notification(
    source_tab: str, target_tabs: list[str], message: str, project_id, db
):
    if not project_id or not db:
        return
    try:
        from app.db.db_models import Project

        project = await db.get(Project, project_id)
        if not project:
            return

        core_data = dict(project.core_data or {})
        shared = core_data.get("worldview_shared_state", {})
        pn = shared.get("pending_notifications", {})

        notification = {
            "from": TAB_ROLE_NAMES.get(source_tab, source_tab),
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        for target in target_tabs:
            if target != source_tab:
                pn.setdefault(target, []).append(notification)

        shared["pending_notifications"] = pn
        core_data["worldview_shared_state"] = shared
        project.core_data = core_data
        await db.commit()
    except Exception as e:
        logger.error(f"[Worldbuilder] Failed to send notification: {e}")


TAB_LABELS = {
    "overview": "概览",
    "rules": "世界规则",
    "characters": "人物",
    "locations": "地点",
    "foreshadowing": "伏笔",
    "promotions": "晋升与发现",
    "discoveries": "正文发现",
    "style": "文笔",
}

NOTIFICATION_TARGETS = {
    "rules": ["characters", "locations", "foreshadowing"],
    "characters": ["foreshadowing", "locations"],
    "locations": ["characters", "foreshadowing"],
    "foreshadowing": ["characters"],
}


class WorldbuilderAgent(BaseAgent):
    name = "worldbuilder"
    MAX_TOOL_ITERATIONS = 50

    def __init__(self):
        self.sub_agents: dict[str, WorldbuilderSubAgent] = {}
        for tab, config in SUB_AGENT_CONFIGS.items():
            self.sub_agents[tab] = WorldbuilderSubAgent(config)

    async def execute(self, context: dict) -> dict:
        messages = context.get("messages", [])
        project_context = context.get("project_context", {})
        active_tab = context.get("active_tab", "rules")
        project_id = context.get("project_id")
        db = context.get("db")

        system_prompt = await self._build_system_prompt(
            project_context, active_tab, project_id, db
        )

        individual_memory = await _load_individual_memory(active_tab, project_id, db)
        if individual_memory.last_topics or individual_memory.unfinished_tasks:
            system_prompt += "\n## 个体记忆（你之前的工作上下文）\n"
            if individual_memory.last_topics:
                system_prompt += f"- 最近讨论：{', '.join(individual_memory.last_topics[-5:])}\n"
            if individual_memory.unfinished_tasks:
                system_prompt += f"- 未完成：{', '.join(individual_memory.unfinished_tasks[-5:])}\n"
            if individual_memory.key_decisions:
                system_prompt += f"- 关键决策：{', '.join(individual_memory.key_decisions[-5:])}\n"

        notifications = await _load_notifications(active_tab, project_id, db)
        if notifications:
            system_prompt += "\n## 跨角色通知\n"
            for n in notifications[-5:]:
                system_prompt += f"- 来自「{n.get('from', '')}」：{n.get('message', '')}\n"
            system_prompt += "请在回复中适当回应这些通知。\n"

        existing_rules = []
        existing_chars = []
        existing_locs = []
        existing_fs = []

        if project_id and db:
            try:
                from app.services.memory_core import CoreMemoryService

                core_service = CoreMemoryService()
                existing_rules = await core_service.list_world_rules(str(project_id))
                existing_chars = await core_service.list_characters(str(project_id))
                existing_locs = await core_service.list_locations(str(project_id))
                existing_fs = await core_service.list_foreshadowing(str(project_id))
            except Exception:
                pass

        if not messages:
            welcome = self._generate_welcome(
                active_tab, existing_rules, existing_chars, existing_locs, existing_fs
            )
            if welcome:
                return {"response": welcome, "suggestions": []}

        llm = await self.get_llm_client()

        has_tools = project_id is not None and db is not None
        logger.info(
            f"[Worldbuilder] has_tools={has_tools}, project_id={project_id}, "
            f"active_tab={active_tab}, sub_agent={TAB_ROLE_NAMES.get(active_tab, active_tab)}"
        )

        if has_tools:
            sub_agent = self.sub_agents.get(active_tab)
            if sub_agent is None:
                sub_agent = self.sub_agents.get("rules")

            result = await sub_agent.execute(
                system_prompt, messages, project_id, db, llm
            )

            await self._update_individual_memory(
                active_tab, result, project_id, db
            )

            if result.get("suggestions"):
                targets = NOTIFICATION_TARGETS.get(active_tab, [])
                if targets:
                    summary = self._summarize_changes(result)
                    if summary:
                        await _send_notification(
                            active_tab, targets, summary, project_id, db
                        )

            return result
        else:
            user_prompt = self._build_user_prompt(messages, active_tab)
            response = await llm.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.7,
                task_type=LLMTaskType.SHORT_EXTRACTION,
            )
            suggestions = _extract_suggestions(response)
            return {"response": response, "suggestions": suggestions}

    async def _build_system_prompt(
        self, project_context, active_tab, project_id, db
    ) -> str:
        system_prompt = STATIC_RULES

        if project_context:
            system_prompt += f"\n当前项目信息：\n"
            system_prompt += f"- 名称：{project_context.get('name', '未命名')}\n"
            system_prompt += f"- 类型：{project_context.get('genre', '未设定')}\n"

        tab_label = TAB_LABELS.get(active_tab, "世界观")
        system_prompt += f"\n当前对话焦点：{tab_label}\n"
        system_prompt += f"当前角色：{TAB_ROLE_NAMES.get(active_tab, '世界观构建师')}\n"

        tab_role = TAB_ROLE_PROMPTS.get(active_tab)
        if tab_role:
            system_prompt += tab_role

        if project_id and db:
            try:
                from app.db.db_models import Project
                from app.services.memory_core import CoreMemoryService

                project_obj = await db.get(Project, project_id)
                core_service = CoreMemoryService()
                pid = str(project_id)

                existing_rules = await core_service.list_world_rules(pid)
                existing_chars = await core_service.list_characters(pid)
                existing_locs = await core_service.list_locations(pid)
                existing_fs = await core_service.list_foreshadowing(pid)

                if project_obj:
                    current_chapter = project_obj.current_chapter or 1
                    outline_data = project_obj.outline_data or {}
                    total_chapters = len(outline_data.get("chapters", []))
                    system_prompt += f"\n## 当前项目状态\n"
                    system_prompt += f"- 写作进度：已生成至第{current_chapter}章，共{total_chapters}章大纲\n"

                    outline_conflicts = []
                    outline_foreshadowing_needs = []
                    outline_character_needs = []
                    for ch in outline_data.get("chapters", []):
                        ch_num = ch.get("chapter_number", 0)
                        if ch.get("main_conflict"):
                            outline_conflicts.append(
                                f"第{ch_num}章：{ch.get('main_conflict', '')[:60]}"
                            )
                        for fs in ch.get("thread_ops", []):
                            outline_foreshadowing_needs.append(
                                f"第{ch_num}章：{fs.get('thread_id', '')}({fs.get('op', '')})"
                            )
                        if ch.get("pov_character"):
                            outline_character_needs.append(ch.get("pov_character"))

                    if outline_conflicts:
                        system_prompt += f"\n### 大纲冲突需求（世界观需支撑这些冲突）\n"
                        for c in outline_conflicts[:8]:
                            system_prompt += f"- {c}\n"

                    if outline_foreshadowing_needs:
                        system_prompt += f"\n### 大纲伏笔需求（世界观需提供伏笔依据）\n"
                        for f in outline_foreshadowing_needs[:8]:
                            system_prompt += f"- {f}\n"

                    if outline_character_needs:
                        unique_chars = list(dict.fromkeys(outline_character_needs))
                        system_prompt += f"\n### 大纲涉及人物（需确保人物设定完整）\n"
                        system_prompt += f"- {', '.join(unique_chars[:10])}\n"

                system_prompt += self._build_worldview_index(
                    existing_rules, existing_chars, existing_locs, existing_fs
                )

                if (
                    not existing_rules
                    and not existing_chars
                    and not existing_locs
                ):
                    system_prompt += "\n⚠️ 当前项目尚无任何世界观设定，建议从锚定底层铁则开始构建。\n"

                system_prompt += self._build_relevant_details(
                    active_tab, existing_rules, existing_chars, existing_locs, existing_fs
                )

                if active_tab == "promotions":
                    from app.services.memory_shell import ShellMemoryService

                    shell_service = ShellMemoryService()
                    seeds = await shell_service.get_seeds_by_tier(pid)
                    auto_seeds = [
                        s
                        for s in seeds
                        if s.get("metadata", {}).get("auto_extracted")
                    ]
                    if auto_seeds:
                        system_prompt += (
                            f"\n### 待鉴定种子（{len(auto_seeds)}条）\n"
                        )
                        for s in auto_seeds[:10]:
                            system_prompt += f"- [{s.get('tier', 'T3')}] {s.get('content', '')[:80]}\n"

                if active_tab == "style":
                    atmosphere_keywords = set()
                    for loc in existing_locs:
                        if loc.get("atmosphere"):
                            atmosphere_keywords.add(loc["atmosphere"])
                    for r in existing_rules:
                        if r.get("category") in ("magic", "society"):
                            atmosphere_keywords.add(r.get("name", ""))
                    if atmosphere_keywords:
                        system_prompt += f"\n### 世界观氛围关键词\n{', '.join(list(atmosphere_keywords)[:10])}\n"

                from app.services.memory_shell import ShellMemoryService

                shell_service = ShellMemoryService()
                seeds = await shell_service.get_seeds_by_tier(pid)
                auto_seeds = [
                    s for s in seeds if s.get("metadata", {}).get("auto_extracted")
                ]
                if auto_seeds:
                    system_prompt += f"\n### 章节产出提取（待确认的设定种子）\n"
                    for s in auto_seeds[:5]:
                        system_prompt += f"- {s.get('content', '')[:80]}\n"
            except Exception:
                pass

        if project_id and db:
            try:
                from app.services.coordination_read_model_service import CoordinationReadModelService
                reader = CoordinationReadModelService()
                coordination_prompt = await reader.build_coordination_prompt(
                    db, str(project_id), target_system="worldbuilder",
                    relevant_chapter=None,
                    session_id=None,
                    token_budget=300,
                )
                if coordination_prompt:
                    system_prompt += "\n\n" + coordination_prompt
            except Exception as e:
                logger.warning(f"[worldbuilder] 加载协调信息失败: {e}")

        # --- Agent Skill Runtime injection ---
        try:
            skill_packet = await self.prepare_skill_packet({
                "project_id": str(project_id) if project_id else None,
                "db": db,
                "active_tab": active_tab,
                "scene_context_package": {"project_context": project_context or {}},
            })
            system_prompt += skill_packet.render_system()
        except Exception:
            logger.warning("Skill runtime injection failed for worldbuilder", exc_info=True)

        return system_prompt

    @staticmethod
    def _build_worldview_index(rules, chars, locs, fs_list) -> str:
        index = "\n## 世界观索引（轻量概览）\n"
        index += f"- 规则：{len(rules)}条"
        critical = [r.get("name", "") for r in rules if r.get("priority") == "critical"]
        if critical:
            index += f"（宪法级：{', '.join(critical[:5])}）"
        index += "\n"

        if rules:
            by_category = {}
            for r in rules:
                cat = r.get("category", "general")
                by_category.setdefault(cat, []).append(r.get("name", ""))
            for cat, names in by_category.items():
                index += f"  - {cat}：{', '.join(names[:8])}\n"

        char_names = []
        for c in chars:
            if isinstance(c, dict):
                char_names.append(c.get("name", ""))
            else:
                char_names.append(getattr(c, "name", ""))
        index += f"- 人物：{len(chars)}人"
        if char_names:
            index += f"（{', '.join(char_names[:10])}）"
        index += "\n"

        loc_names = [loc.get("name", "") for loc in locs]
        loc_atmospheres = [loc.get("atmosphere", "") for loc in locs if loc.get("atmosphere")]
        index += f"- 地点：{len(locs)}处"
        if loc_names:
            index += f"（{', '.join(loc_names[:10])}）"
        if loc_atmospheres:
            index += f"  氛围：{', '.join(loc_atmospheres[:5])}"
        index += "\n"

        index += f"- 伏笔：{len(fs_list)}条"
        active_fs = [f.get("name", "") for f in fs_list if f.get("status") == "active"]
        resolved_fs = [f.get("name", "") for f in fs_list if f.get("status") in ("resolved", "revealing")]
        if active_fs:
            index += f"（已埋：{len(active_fs)}，已揭示：{len(resolved_fs)}）"
        index += "\n"

        return index

    @staticmethod
    def _build_relevant_details(active_tab, rules, chars, locs, fs_list) -> str:
        details = ""

        if active_tab in ("rules", "overview"):
            if rules:
                details += "\n### 规则详情\n"
                for r in rules[:15]:
                    priority = r.get("priority", "normal")
                    cat = r.get("category", "general")
                    name = r.get("name", "")
                    desc = r.get("description", "")[:80]
                    constraints = r.get("constraints", [])
                    details += f"- [{priority}][{cat}] {name}：{desc}"
                    if constraints:
                        details += f" | 约束：{'; '.join(str(c)[:40] for c in constraints[:2])}"
                    details += "\n"

        if active_tab in ("characters", "rules", "overview"):
            if chars:
                details += "\n### 人物概要\n"
                for c in chars[:10]:
                    if isinstance(c, dict):
                        name = c.get("name", "")
                        personality = c.get("personality", "")[:50]
                        desire = c.get("desire", "")[:50]
                    else:
                        name = getattr(c, "name", "")
                        personality = getattr(c, "personality", "")[:50]
                        desire = getattr(c, "desire", "")[:50]
                    details += f"- {name}：{personality}"
                    if desire:
                        details += f" | 欲望：{desire}"
                    details += "\n"

        if active_tab in ("locations", "characters", "overview"):
            if locs:
                details += "\n### 地点概要\n"
                for loc in locs[:10]:
                    name = loc.get("name", "")
                    atmosphere = loc.get("atmosphere", "")
                    parent = loc.get("parent_location", "")
                    details += f"- {name}"
                    if atmosphere:
                        details += f"（{atmosphere}）"
                    if parent:
                        details += f" → {parent}"
                    details += "\n"

        if active_tab in ("foreshadowing", "overview"):
            if fs_list:
                details += "\n### 伏笔概要\n"
                for f in fs_list[:10]:
                    name = f.get("name", "")
                    status = f.get("status", "active")
                    plant = f.get("bury_window_start", "?")
                    reveal = f.get("reveal_window_start", "?")
                    details += f"- {name}[{status}]：第{plant}章埋→第{reveal}章揭\n"

        return details

    def _generate_welcome(self, active_tab, rules, chars, locs, fs) -> str:
        critical_rules = [r for r in rules if r.get("priority") == "critical"]
        power_rules = [
            r
            for r in rules
            if r.get("category") in ("magic", "combat", "technology")
        ]

        tab_greetings = {
            "overview": "欢迎来到世界观总览！",
            "rules": "欢迎来到规则构建！",
            "characters": "欢迎来到人物铸造！",
            "locations": "欢迎来到地理编织！",
            "foreshadowing": "欢迎来到伏笔织网！",
            "promotions": "欢迎来到种子鉴定！",
            "style": "欢迎来到风格调律！",
        }
        greeting = tab_greetings.get(active_tab, "你好！")

        if not rules and not chars and not locs:
            return f"{greeting}你的世界尚在混沌之中。让我们从锚定底层铁则开始——这个世界最不可动摇的3条规则是什么？"
        elif critical_rules and not power_rules:
            return f"{greeting}已有{len(critical_rules)}条底层铁则，接下来需要构建核心规则闭环。你的铁则如何影响这个世界的资源、权力、技术或能力运作？"
        elif power_rules and not chars:
            return f"{greeting}核心规则已初步建立，现在需要创造在这个规则网络中生存的人物。谁最能体现这个世界的核心矛盾？"
        elif chars and not locs:
            return f"{greeting}人物已就位，但世界还没有舞台。让我们构建他们生存的空间——从最核心的场景开始？"
        else:
            return f"{greeting}世界观已初具规模（{len(rules)}规则/{len(chars)}人物/{len(locs)}地点/{len(fs)}伏笔）。想深化哪个维度？"

    async def _update_individual_memory(self, tab, result, project_id, db):
        if not project_id or not db:
            return
        try:
            memory = await _load_individual_memory(tab, project_id, db)

            response_text = result.get("response", "")
            if response_text:
                topics = memory.last_topics
                new_topic = response_text[:50].replace("\n", " ").strip()
                if new_topic and (not topics or topics[-1] != new_topic):
                    topics.append(new_topic)
                memory.last_topics = topics[-10:]

            suggestions = result.get("suggestions", [])
            if suggestions:
                for s in suggestions:
                    name = s.get("name", "")
                    if name:
                        decision = f"创建了{s.get('_type', '设定')}：{name}"
                        memory.key_decisions.append(decision)
                        memory.key_decisions = memory.key_decisions[-10:]

                        for task in list(memory.unfinished_tasks):
                            if name in task:
                                memory.unfinished_tasks.remove(task)

            await _save_individual_memory(tab, memory, project_id, db)
        except Exception as e:
            logger.error(f"[Worldbuilder] Failed to update individual memory: {e}")

    def _summarize_changes(self, result: dict) -> str | None:
        suggestions = result.get("suggestions", [])
        if not suggestions:
            return None

        names = []
        for s in suggestions:
            name = s.get("name", "")
            if name:
                names.append(name)
        if not names:
            return None

        if len(names) == 1:
            s = suggestions[0]
            parts = []
            if "description" in s:
                parts.append(f"描述：{s['description'][:60]}")
            if "category" in s:
                parts.append(f"分类：{s['category']}")
            if "priority" in s:
                parts.append(f"优先级：{s['priority']}")
            detail = "，".join(parts) if parts else ""
            return f"新设定已创建：「{names[0]}」{detail}。请检查与你的角色领域是否兼容。"

        return f"新设定已批量创建：{', '.join(f'「{n}」' for n in names)}（共{len(names)}项）。请检查与你的角色领域是否兼容。"

    def _build_user_prompt(self, messages: list[dict], active_tab: str) -> str:
        if not messages:
            return f"请帮我构思一些{TAB_LABELS.get(active_tab, '世界观')}设定。"

        user_msgs = [m["content"] for m in messages if m.get("role") == "user"]
        return "\n".join(user_msgs) if user_msgs else "你好"

    def get_sub_agent_info(self) -> list[dict]:
        return [
            {
                "name": config.name,
                "display_name": config.display_name,
                "tab": config.tab,
                "tools": config.tool_names,
                "persona": config.persona,
                "default_skills": config.default_skills,
            }
            for config in SUB_AGENT_CONFIGS.values()
        ]

    @staticmethod
    async def get_sub_agent_custom_skills(tab: str, project_id, db) -> list[dict]:
        if not project_id or not db:
            return []
        try:
            from app.db.db_models import Project

            project = await db.get(Project, project_id)
            if not project or not project.core_data:
                return []
            mem = (project.core_data or {}).get("worldview_agent_memory", {})
            tab_mem = mem.get(tab, {})
            return tab_mem.get("custom_skills", [])
        except Exception:
            return []

    @staticmethod
    async def add_sub_agent_custom_skill(
        tab: str, project_id, db, skill_data: dict
    ) -> dict:
        if not project_id or not db:
            return {"error": "缺少项目信息"}
        try:
            from app.db.db_models import Project

            project = await db.get(Project, project_id)
            if not project:
                return {"error": "项目不存在"}

            core_data = dict(project.core_data or {})
            mem = core_data.get("worldview_agent_memory", {})
            tab_mem = mem.get(tab, {})
            custom_skills = tab_mem.get("custom_skills", [])

            skill_name = skill_data.get("name", "")
            if not skill_name:
                return {"error": "技能名称不能为空"}
            if any(s.get("name") == skill_name for s in custom_skills):
                return {"error": f"已存在同名自定义技能：{skill_name}"}

            config = SUB_AGENT_CONFIGS.get(tab)
            if config and skill_name in config.default_skills:
                return {"error": f"「{skill_name}」是默认技能，不可覆盖"}

            new_skill = {
                "name": skill_name,
                "display_name": skill_data.get("display_name", skill_name),
                "description": skill_data.get("description", ""),
                "category": "agent",
                "agent": f"wb_{tab}",
            }
            custom_skills.append(new_skill)
            tab_mem["custom_skills"] = custom_skills
            mem[tab] = tab_mem
            core_data["worldview_agent_memory"] = mem
            project.core_data = core_data
            await db.commit()
            return new_skill
        except Exception as e:
            logger.error(f"[Worldbuilder] Failed to add custom skill: {e}")
            return {"error": str(e)}

    @staticmethod
    async def delete_sub_agent_custom_skill(
        tab: str, project_id, db, skill_name: str
    ) -> dict:
        if not project_id or not db:
            return {"error": "缺少项目信息"}
        try:
            from app.db.db_models import Project

            project = await db.get(Project, project_id)
            if not project:
                return {"error": "项目不存在"}

            config = SUB_AGENT_CONFIGS.get(tab)
            if config and skill_name in config.default_skills:
                return {"error": "默认技能不可删除"}

            core_data = dict(project.core_data or {})
            mem = core_data.get("worldview_agent_memory", {})
            tab_mem = mem.get(tab, {})
            custom_skills = tab_mem.get("custom_skills", [])

            original_len = len(custom_skills)
            custom_skills = [s for s in custom_skills if s.get("name") != skill_name]
            if len(custom_skills) == original_len:
                return {"error": f"未找到自定义技能：{skill_name}"}

            tab_mem["custom_skills"] = custom_skills
            mem[tab] = tab_mem
            core_data["worldview_agent_memory"] = mem
            project.core_data = core_data
            await db.commit()
            return {"message": f"已删除技能：{skill_name}"}
        except Exception as e:
            logger.error(f"[Worldbuilder] Failed to delete custom skill: {e}")
            return {"error": str(e)}
