import json
from pathlib import Path

import yaml

from app.models.api_config import APIConfig, AgentOverride, AppSettings, GlobalSettings
from app.config import settings as app_settings
from app.utils.atomic_file import atomic_write_text


_DATA_ROOT = Path(app_settings.data_dir)

AGENT_NAMES: list[str] = [
    # --- 大纲 & 世界观 ---
    "outline_architect",
    "worldbuilder",
    # --- 主编系统 (Editor Hub) ---
    "editor_in_chief",
    "writing_companion",
    "core_generation",
    # --- 并行审校链路（合并后新 Agent） ---
    "scene_validator",          # 合并自 consistency_check + scene_critic + reader_experience
    "scene_post_processor",     # 合并自 detail_harvester + state_updater
    "scene_repairer",           # 合并自 auto_repair + scene_rewrite + ai_quality_inline_reviser
    # --- FBI 总案台修复链路（合并后新 Agent） ---
    "content_repair",           # 合并自 fact_repair + ending_completion + scene_restructure
    "style_repair",             # 合并自 prose_repair + voice_repair + style_guard + pacing_repair
    "budget_arbiter",           # 合并自 length_budget + patch_arbiter + repair_reflection
    # --- 独立 Agent ---
    "detail_detective",
    "plot_commentator",
    "branch_explorer",
    "style_learner",
]

SKILL_REGISTRY: dict[str, dict] = {
    "scene_preparation": {
        "name": "scene_preparation",
        "display_name": "场景准备",
        "description": "为场景生成准备上下文，包括人物卡、地点卡、相关细节和焦点提示",
        "category": "utility",
        "detail": (
            "# 场景准备\n\n"
            "为场景生成准备完整的上下文包，是生成流水线的核心前置步骤。\n\n"
            "## 工作流程\n\n"
            "1. **获取场景节拍**：从大纲中提取当前场景的目标、冲突和预期结果\n"
            "2. **Core 查询**：拉取人物卡、地点卡、伏笔等核心事实\n"
            "3. **Shell 检索**：搜索与当前场景相关的历史细节种子\n"
            "4. **State 快照**：获取当前 StoryState 的冻结快照\n"
            "5. **风格上下文**：获取激活的文笔风格画像和匹配的范例片段\n"
            "6. **伏笔指令**：获取当前场景需要埋设或揭示的伏笔\n\n"
            "## 输出\n\n"
            "打包为场景上下文字典，传递给注意力导演构建最终提示词。\n\n"
            "## 依赖\n\n"
            "- CoreQuerySkill\n"
            "- ShellSearchSkill\n"
            "- StateQuerySkill\n"
            "- CoreMemoryService（伏笔查询）\n"
            "- CoreMemoryService（风格查询）"
        ),
    },
    "state_query": {
        "name": "state_query",
        "display_name": "状态查询",
        "description": "查询当前故事状态快照、实体状态和角色主观认知",
        "category": "utility",
        "detail": (
            "# 状态查询\n\n"
            "查询当前故事状态快照，包括客观事实和角色主观认知。\n\n"
            "## 查询范围\n\n"
            "- **客观状态**（objective_state）：人物位置、情绪、物理状态、持有物品、存活状态\n"
            "- **地点状态**（location_states）：氛围、光源、物品、环境状态\n"
            "- **主观认知**（subjective_views）：每个角色的 believed_state 和 last_known\n"
            "- **叙事时间**（narrative_time）：当前故事时间线位置\n"
            "- **活跃场景**（active_scene）：当前章节和场景编号\n\n"
            "## 数据来源\n\n"
            "从 Redis 中的 StoryState JSON 对象读取，生成前获取冻结快照。\n\n"
            "## 使用场景\n\n"
            "- 注意力导演注入状态约束\n"
            "- 一致性校验对比当前状态\n"
            "- 状态更新 Agent 生成补丁时参考"
        ),
    },
    "shell_search": {
        "name": "shell_search",
        "display_name": "细节搜索",
        "description": "在 Shell 记忆层中搜索与场景相关的历史细节",
        "category": "utility",
        "detail": (
            "# 细节搜索\n\n"
            "在 Shell 记忆层中搜索与当前场景相关的历史细节种子。\n\n"
            "## 搜索方式\n\n"
            "- **语义检索**：基于 pgvector 向量相似度搜索\n"
            "- **时空过滤**：按章节号、场景号、地点 ID 过滤\n"
            "- **混合检索**：语义相似度 + 时空相关性加权排序\n\n"
            "## 细节种子结构\n\n"
            "每条细节种子包含：\n"
            "- `entity_id`：关联实体标识\n"
            "- `fact`：事实描述\n"
            "- `chapter` / `scene`：时空索引\n"
            "- `location_id`：地点索引\n"
            "- `embedding`：向量嵌入\n\n"
            "## 使用场景\n\n"
            "- 场景准备时搜索相关历史细节\n"
            "- 细节侦探回溯线索\n"
            "- 注意力导演构建细节幽灵提示"
        ),
    },
    "core_query": {
        "name": "core_query",
        "display_name": "核心查询",
        "description": "查询 Core 记忆层中的实体卡片、伏笔和大纲节拍",
        "category": "utility",
        "detail": (
            "# 核心查询\n\n"
            "查询 Core 记忆层中的实体卡片、世界观规则和伏笔信息。\n\n"
            "## 查询范围\n\n"
            "- **人物卡**：姓名、外貌、性格、欲望、深层需求、人物弧光\n"
            "- **世界观规则**：分类规则、优先级、约束条件、锁定状态\n"
            "- **地点卡**：名称、描述、氛围、从属关系\n"
            "- **伏笔**：状态（已埋设/已揭示/已放弃）、埋设/揭示章节\n"
            "- **风格画像**：激活的文笔风格特征和范例片段\n\n"
            "## 数据来源\n\n"
            "从 PostgreSQL 的 `core_data` JSONB 字段读取。\n\n"
            "## 使用场景\n\n"
            "- 场景准备时拉取实体信息\n"
            "- 注意力导演构建人物卡和规则约束\n"
            "- 一致性校验对比核心事实"
        ),
    },
    "attention_director": {
        "name": "attention_director",
        "display_name": "注意力引导",
        "description": "根据场景上下文构建焦点提示，引导生成关注关键要素",
        "category": "utility",
        "detail": (
            "# 注意力引导\n\n"
            "根据场景上下文构建焦点提示词，是生成流水线的核心环节。\n\n"
            "## 提示词构建流程\n\n"
            "1. **场景焦点**：注入场景节拍卡（目标、冲突、预期结果）\n"
            "2. **POV 认知分层**：根据视角角色注入主观认知\n"
            "   - believed_state：角色认为的事实\n"
            "   - last_known：角色最后获知的信息\n"
            "   - 角色不知道的事实（生成戏剧性反讽）\n"
            "3. **伏笔指令**：必须埋设/揭示的伏笔\n"
            "4. **风格约束**：激活的文笔风格指令和范例\n"
            "5. **注意力规则**：场景级别的写作约束\n\n"
            "## POV 模式\n\n"
            "- **限定视角**：注入主观认知，角色不知道的不出现\n"
            "- **全知视角**：直接注入客观状态\n\n"
            "## 输出\n\n"
            "拼装后的完整提示词，传递给核心生成 Agent。"
        ),
    },
    "outline_design": {
        "name": "outline_design",
        "display_name": "大纲设计",
        "description": "设计整体故事大纲结构，规划主线和支线走向",
        "category": "agent",
        "agent": "outline_architect",
        "detail": (
            "# 大纲设计\n\n"
            "大纲架构师的专属技能，设计整体故事大纲结构。\n\n"
            "## 工作方式\n\n"
            "通过对话式交互与作者协作：\n"
            "1. 了解创作意图：题材、风格、核心冲突\n"
            "2. 梳理主线和支线走向\n"
            "3. 设计章节结构：标题、冲突、价值转变\n"
            "4. 生成标准 JSON 格式大纲\n\n"
            "## 大纲结构\n\n"
            "```json\n"
            '{"chapters": [{"chapter_number": 1, "title": "...", "conflict": "...", "value_shift": "...", "scenes": [...]}]}\n'
            "```\n\n"
            "## 触发方式\n\n"
            "在编辑器页面的大纲设计面板中与大纲架构师对话。"
        ),
    },
    "chapter_splitting": {
        "name": "chapter_splitting",
        "display_name": "章节拆分",
        "description": "将大纲拆分为章节和场景节拍，确定每章冲突和价值转变",
        "category": "agent",
        "agent": "outline_architect",
        "detail": (
            "# 章节拆分\n\n"
            "大纲架构师的专属技能，将大纲拆分为场景节拍。\n\n"
            "## 拆分规则\n\n"
            "- 每章 2-5 个场景\n"
            "- 每个场景有明确的目标、冲突和结果\n"
            "- 场景间有因果或时间递进关系\n"
            "- 标注 POV 角色和场景类型（对话/动作/描写/内心）\n\n"
            "## 场景节拍卡\n\n"
            "```json\n"
            '{"scene_number": 1, "pov": "角色名", "goal": "...", "conflict": "...", "outcome": "...", "type": "dialogue"}\n'
            "```\n\n"
            "## 使用场景\n\n"
            "大纲生成后自动拆分，或作者手动触发重新拆分。"
        ),
    },
    "dag_planning": {
        "name": "dag_planning",
        "display_name": "DAG 规划",
        "description": "将章节拆解为场景执行计划和依赖关系图",
        "category": "agent",
        "agent": "editor_in_chief",
        "detail": (
            "# DAG 规划\n\n"
            "主编的专属技能，将章节拆解为场景执行计划和依赖关系图。\n\n"
            "## 规划逻辑\n\n"
            "1. 解析大纲中的场景节拍\n"
            "2. 识别场景间的依赖关系（因果、时间、状态）\n"
            "3. 构建有向无环图（DAG）\n"
            "4. 确定可并行和必须串行的场景\n"
            "5. 生成执行计划\n\n"
            "## 依赖类型\n\n"
            "- **因果依赖**：场景 B 的前提是场景 A 的结果\n"
            "- **状态依赖**：场景 B 需要场景 A 产生的状态变更\n"
            "- **时间依赖**：场景 B 在场景 A 之后发生\n\n"
            "## 当前实现\n\n"
            "MVP 阶段采用串行执行，阶段二升级为 asyncio DAG 并行调度。"
        ),
    },
    "quality_control": {
        "name": "quality_control",
        "display_name": "质量控制",
        "description": "评估生成质量，决定是否需要重试或调整参数",
        "category": "agent",
        "agent": "editor_in_chief",
        "detail": (
            "# 质量控制\n\n"
            "主编的专属技能，评估生成质量并决定是否重试。\n\n"
            "## 评估维度\n\n"
            "- **叙事连贯性**：场景间过渡是否自然\n"
            "- **角色一致性**：人物行为是否符合设定\n"
            "- **节奏感**：张弛有度，无拖沓或仓促\n"
            "- **信息密度**：每个场景都有信息推进\n"
            "- **文风统一**：无突兀的风格断裂\n\n"
            "## 决策逻辑\n\n"
            "- 通过 → 进入下一场景\n"
            "- 不通过 → 调整参数重试（最多 2 次）\n"
            "- 仍不通过 → 标记问题，继续下一场景"
        ),
    },
    "narrative_writing": {
        "name": "narrative_writing",
        "display_name": "叙事写作",
        "description": "创作引人入胜的叙事正文，注重情节推进和节奏把控",
        "category": "agent",
        "agent": "core_generation",
        "detail": (
            "# 叙事写作\n\n"
            "核心生成 Agent 的专属技能，创作引人入胜的叙事正文。\n\n"
            "## 写作要求\n\n"
            "- 文字流畅自然，避免 AI 味和套话\n"
            "- 人物行为符合性格设定，对话有个性\n"
            "- 情节推进合理，节奏张弛有度\n"
            "- 注重细节描写和氛围营造\n"
            "- 善用「展示而非讲述」手法\n"
            "- 每个场景结尾留有钩子\n\n"
            "## 输入\n\n"
            "注意力导演构建的完整提示词，包含场景节拍、POV 认知、伏笔指令、风格约束等。\n\n"
            "## 输出\n\n"
            "纯正文文本，不包含任何元数据或标记。"
        ),
    },
    "scene_crafting": {
        "name": "scene_crafting",
        "display_name": "场景营造",
        "description": "营造沉浸式场景氛围，协调环境、动作和内心描写",
        "category": "agent",
        "agent": "core_generation",
        "detail": (
            "# 场景营造\n\n"
            "核心生成 Agent 的专属技能，营造沉浸式场景氛围。\n\n"
            "## 营造要素\n\n"
            "- **环境**：空间感、光影、温度、气味\n"
            "- **动作**：角色行为推动情节\n"
            "- **内心**：角色心理活动和情感变化\n"
            "- **节奏**：紧张与舒缓的交替\n"
            "- **钩子**：场景结尾的悬念或转折\n\n"
            "## 与叙事写作的区别\n\n"
            "叙事写作侧重整体推进，场景营造侧重单个场景的沉浸感和完整性。"
        ),
    },
    "dialogue_crafting": {
        "name": "dialogue_crafting",
        "display_name": "对话创作",
        "description": "创作自然流畅的角色对话，把握对话节奏和潜台词",
        "category": "agent",
        "agent": "core_generation",
        "detail": (
            "# 对话创作\n\n"
            "对话生成 Agent 的专属技能，创作自然流畅的角色对话。\n\n"
            "## 对话原则\n\n"
            "- 每个角色有独特的语言指纹\n"
            "- 对话有潜台词，角色不会直白说出内心\n"
            "- 通过对话推进情节\n"
            "- 节奏有变化：长短交替、快慢结合\n"
            "- 适当使用省略和中断\n\n"
            "## 提示语处理\n\n"
            "- 克制使用「他说」「她道」\n"
            "- 用动作和神态代替提示语\n"
            "- 连续对话可省略提示语"
        ),
    },
    "character_voice": {
        "name": "character_voice",
        "display_name": "角色声音",
        "description": "模拟不同角色的说话风格、用词习惯和语气特征",
        "category": "agent",
        "agent": "core_generation",
        "detail": (
            "# 角色声音\n\n"
            "对话生成 Agent 的专属技能，模拟不同角色的说话风格。\n\n"
            "## 模拟维度\n\n"
            "- **用词范围**：文雅/粗犷/专业/口语化\n"
            "- **句式特征**：长句/短句/排比/反问\n"
            "- **口头禅**：标志性用语\n"
            "- **语气**：强硬/温和/讽刺/热情\n"
            "- **停顿习惯**：犹豫/果断/思考\n\n"
            "## 数据来源\n\n"
            "从 Core 层的人物卡中提取性格和背景信息，推导语言风格。"
        ),
    },
    "atmosphere_building": {
        "name": "atmosphere_building",
        "display_name": "氛围营造",
        "description": "创作环境氛围描写，通过光影、温度、声音等元素构建空间感",
        "category": "agent",
        "agent": "core_generation",
        "detail": (
            "# 氛围营造\n\n"
            "描写生成 Agent 的专属技能，创作环境氛围描写。\n\n"
            "## 营造手法\n\n"
            "- **光影**：暖光/冷光/明暗对比\n"
            "- **声音**：环境音/沉默/回响\n"
            "- **温度**：寒冷/闷热/舒适\n"
            "- **气味**：食物/花草/血腥/潮湿\n"
            "- **空间感**：开阔/逼仄/纵深\n\n"
            "## 核心原则\n\n"
            "氛围为情节服务：暴风雨不只是天气，是角色内心的外化。"
        ),
    },
    "sensory_detail": {
        "name": "sensory_detail",
        "display_name": "感官细节",
        "description": "创作多感官体验的描写，调动视觉、听觉、触觉、嗅觉、味觉",
        "category": "agent",
        "agent": "core_generation",
        "detail": (
            "# 感官细节\n\n"
            "描写生成 Agent 的专属技能，创作多感官体验的描写。\n\n"
            "## 五感调动\n\n"
            "- **视觉**：色彩、形状、光影、运动\n"
            "- **听觉**：音量、音色、节奏、距离\n"
            "- **触觉**：温度、质感、压力、疼痛\n"
            "- **嗅觉**：食物、自然、化学、体味\n"
            "- **味觉**：甜酸苦辣咸、口感、温度\n\n"
            "## 使用原则\n\n"
            "- 每个场景至少调动 2-3 种感官\n"
            "- 感官描写要为情节或情绪服务\n"
            "- 用具体细节代替抽象形容"
        ),
    },
    "detail_extraction": {
        "name": "detail_extraction",
        "display_name": "细节提取",
        "description": "从文本中提取可被后续章节引用的细节种子",
        "category": "agent",
        "agent": "scene_post_processor",
        "detail": (
            "# 细节提取\n\n"
            "细节收割 Agent 的专属技能，从文本中提取可被后续章节引用的细节种子。\n\n"
            "## 提取范围\n\n"
            "- 人物状态变化：位置、情感、能力\n"
            "- 物品出现和消失：新道具、消耗品\n"
            "- 环境细节：空间变化、氛围转变\n"
            "- 时间线索：时间流逝、季节更替\n"
            "- 情感暗线：未说出口的情感\n\n"
            "## 输出格式\n\n"
            "```json\n"
            '[{"entity_id": "...", "fact": "...", "narrative_time": "...", "tier": "T1"}]\n'
            "```\n\n"
            "## 重要度分级\n\n"
            "- **T1**：关键剧情线索，必须被后续引用\n"
            "- **T2**：重要氛围细节，建议被引用\n"
            "- **T3**：补充背景信息，可选引用"
        ),
    },
    "detail_categorization": {
        "name": "detail_categorization",
        "display_name": "细节分级",
        "description": "将提取的细节按重要度分为 T1/T2/T3 三个层级",
        "category": "agent",
        "agent": "scene_post_processor",
        "detail": (
            "# 细节分级\n\n"
            "细节收割 Agent 的专属技能，将提取的细节按重要度分级。\n\n"
            "## 分级标准\n\n"
            "### T1 - 关键\n"
            "直接影响后续剧情的细节：\n"
            "- 伏笔相关\n"
            "- 角色重大状态变更\n"
            "- 关键物品出现/转移\n\n"
            "### T2 - 重要\n"
            "增强叙事连贯性的细节：\n"
            "- 角色情绪变化\n"
            "- 环境氛围转变\n"
            "- 关系进展\n\n"
            "### T3 - 补充\n"
            "丰富世界感的背景细节：\n"
            "- 环境装饰\n"
            "- 次要角色状态\n"
            "- 时间流逝标记"
        ),
    },
    "state_tracking": {
        "name": "state_tracking",
        "display_name": "状态追踪",
        "description": "追踪文本中隐含的状态变更，识别人物位置、情感、物品变化",
        "category": "agent",
        "agent": "scene_post_processor",
        "detail": (
            "# 状态追踪\n\n"
            "状态更新 Agent 的专属技能，追踪文本中隐含的状态变更。\n\n"
            "## 追踪范围\n\n"
            "- **人物位置**：角色移动和所在地点\n"
            "- **情绪变化**：情感状态转变\n"
            "- **物理状态**：受伤、恢复、疲劳\n"
            "- **持有物品**：获取、使用、丢失\n"
            "- **关系变化**：结盟、决裂、亲密\n\n"
            "## 隐含信息识别\n\n"
            "- 「走进房间」→ 位置变更\n"
            "- 「握紧拳头」→ 情绪紧张\n"
            "- 「接过信封」→ 获得物品"
        ),
    },
    "patch_generation": {
        "name": "patch_generation",
        "display_name": "补丁生成",
        "description": "将状态变更转化为结构化的状态补丁，更新世界状态",
        "category": "agent",
        "agent": "scene_post_processor",
        "detail": (
            "# 补丁生成\n\n"
            "状态更新 Agent 的专属技能，将状态变更转化为结构化的状态补丁。\n\n"
            "## 补丁格式\n\n"
            "```json\n"
            "{\n"
            '  "objective_state": {"角色ID": {"location": "新地点", "emotional_state": "情绪", "physical_state": "身体状态", "inventory": [], "alive": true, "extra": {}}},\n'
            '  "subjective_views": {"角色ID": {"believed_state": {...}}},\n'
            '  "narrative_time": "推进2小时"\n'
            "}\n"
            "```\n\n"
            "## 应用流程\n\n"
            "1. 状态更新 Agent 生成补丁\n"
            "2. 主编审核补丁合理性\n"
            "3. StateManager.apply_patch() 应用补丁\n"
            "4. 自动创建 Checkpoint"
        ),
    },
    "fact_verification": {
        "name": "fact_verification",
        "display_name": "事实校验",
        "description": "校验文本描述与已知核心事实的一致性，标记矛盾",
        "category": "agent",
        "agent": "scene_validator",
        "detail": (
            "# 事实校验\n\n"
            "一致性校验 Agent 的专属技能，校验文本描述与已知核心事实的一致性。\n\n"
            "## 校验维度\n\n"
            "- 人物设定：性格、外貌、能力是否矛盾\n"
            "- 世界观：规则、设定是否被违反\n"
            "- 时间线：事件顺序是否合理\n"
            "- 因果逻辑：行为动机和结果是否合理\n"
            "- 认知一致性：角色是否知道不该知道的事\n\n"
            "## 输出格式\n\n"
            "```json\n"
            '{"pass": false, "conflicts": [{"fact": "已知事实", "text_claim": "文本描述", "suggestion": "修正建议"}]}\n'
            "```"
        ),
    },
    "conflict_detection": {
        "name": "conflict_detection",
        "display_name": "冲突检测",
        "description": "检测文本中的逻辑矛盾、时间线错误和设定冲突",
        "category": "agent",
        "agent": "scene_validator",
        "detail": (
            "# 冲突检测\n\n"
            "一致性校验 Agent 的专属技能，检测文本中的逻辑矛盾和设定冲突。\n\n"
            "## 检测类型\n\n"
            "- **设定冲突**：文本描述违反世界观规则\n"
            "- **逻辑矛盾**：前后行为不一致\n"
            "- **时间线错误**：时间顺序不成立\n"
            "- **状态不连续**：人物位置/物品持有跳跃\n"
            "- **认知泄漏**：角色知道不该知道的信息\n\n"
            "## 严重度分级\n\n"
            "- **critical**：宪法级规则被违反\n"
            "- **high**：核心设定矛盾\n"
            "- **normal**：一般逻辑问题\n"
            "- **low**：轻微不一致"
        ),
    },
    "clue_tracking": {
        "name": "clue_tracking",
        "display_name": "线索追踪",
        "description": "追踪文本中的伏笔和线索，记录其来源和预期回收点",
        "category": "agent",
        "agent": "detail_detective",
        "detail": (
            "# 线索追踪\n\n"
            "细节侦探 Agent 的专属技能，追踪文本中的伏笔和线索。\n\n"
            "## 追踪内容\n\n"
            "- 伏笔的埋设点和预期回收点\n"
            "- 关键物品的出现、转移和消失\n"
            "- 角色行为的暗示和预兆\n"
            "- 环境变化中的隐藏信息\n"
            "- 角色承诺和兑现情况\n\n"
            "## 输出\n\n"
            "线索地图：每条线索的来源、当前状态、预期走向。"
        ),
    },
    "foreshadowing_check": {
        "name": "foreshadowing_check",
        "display_name": "伏笔校验",
        "description": "验证伏笔的呼应和回收情况，确保无遗漏",
        "category": "agent",
        "agent": "detail_detective",
        "detail": (
            "# 伏笔校验\n\n"
            "细节侦探 Agent 的专属技能，验证伏笔的呼应和回收情况。\n\n"
            "## 校验规则\n\n"
            "- 已埋设的伏笔是否在预期章节回收\n"
            "- 长期未回收的伏笔标记为「悬空」\n"
            "- 伏笔回收方式是否与埋设时一致\n"
            "- 是否有遗漏的伏笔呼应机会\n\n"
            "## 输出\n\n"
            "伏笔状态报告：每条伏笔的埋设→回收完整链路。"
        ),
    },
    "plot_analysis": {
        "name": "plot_analysis",
        "display_name": "剧情分析",
        "description": "分析剧情结构和节奏，评估张力曲线和转折效果",
        "category": "agent",
        "agent": "plot_commentator",
        "detail": (
            "# 剧情分析\n\n"
            "剧情评论 Agent 的专属技能，分析剧情结构和节奏。\n\n"
            "## 分析维度\n\n"
            "- **叙事结构**：起承转合完整性\n"
            "- **张力曲线**：悬念设置和高潮安排\n"
            "- **角色弧光**：成长和变化是否可信\n"
            "- **主题深度**：核心主题展开程度\n"
            "- **读者体验**：代入感、期待感、满足感\n"
            "- **情感真实度**：角色反应是否可信\n\n"
            "## 输出\n\n"
            "结构化分析报告，含各维度评分和具体改进建议。"
        ),
    },
    "improvement_suggestion": {
        "name": "improvement_suggestion",
        "display_name": "改进建议",
        "description": "针对剧情薄弱环节提供具体的改进方案和替代写法",
        "category": "agent",
        "agent": "plot_commentator",
        "detail": (
            "# 改进建议\n\n"
            "剧情评论 Agent 的专属技能，针对薄弱环节提供改进方案。\n\n"
            "## 建议类型\n\n"
            "- **结构优化**：调整章节顺序或场景安排\n"
            "- **张力增强**：增加冲突或悬念\n"
            "- **角色深化**：增加内心描写或关系变化\n"
            "- **节奏调整**：删减拖沓或扩展仓促\n"
            "- **伏笔补充**：增加埋设或调整回收时机\n\n"
            "## 原则\n\n"
            "- 提供具体可操作的方案\n"
            "- 给出替代写法示例\n"
            "- 引用经典作品作为参照"
        ),
    },
    "branch_generation": {
        "name": "branch_generation",
        "display_name": "分支生成",
        "description": "生成剧情的替代发展方向，探索不同的可能性",
        "category": "agent",
        "agent": "branch_explorer",
        "detail": (
            "# 分支生成\n\n"
            "分支探索 Agent 的专属技能，生成剧情的替代发展方向。\n\n"
            "## 分支类型\n\n"
            "- **抉择分支**：角色做出不同选择\n"
            "- **视角分支**：换一个 POV 角色\n"
            "- **事件分支**：关键事件有不同结果\n"
            "- **主题分支**：同一主题的不同表达\n"
            "- **反转分支**：最意想不到但逻辑自洽的走向\n\n"
            "## 输出\n\n"
            "3-5 个平行分支方案，每个含简要描述和叙事价值评估。"
        ),
    },
    "what_if_analysis": {
        "name": "what_if_analysis",
        "display_name": "假设分析",
        "description": "分析不同选择可能导致的结果，评估各分支的叙事价值",
        "category": "agent",
        "agent": "branch_explorer",
        "detail": (
            "# 假设分析\n\n"
            "分支探索 Agent 的专属技能，分析不同选择可能导致的结果。\n\n"
            "## 分析维度\n\n"
            "- **叙事价值**：哪个方向更有故事性\n"
            "- **角色深度**：哪个方向更能展现角色\n"
            "- **读者吸引力**：哪个方向更引人入胜\n"
            "- **实现难度**：哪个方向更容易写出好效果\n"
            "- **主题契合**：哪个方向更贴合核心主题\n\n"
            "## 输出\n\n"
            "对比分析表，帮助作者做出最佳选择。"
        ),
    },
    "style_polish": {
        "display_name": "文风润色",
        "description": "检查文风断裂、违禁词，优化场景过渡语句",
        "category": "utility",
        "detail": (
            "# 文风润色\n\n"
            "纯规则引擎，检查文风断裂和违禁词，优化场景过渡语句。不调用 AI 模型。\n\n"
            "## 检查项目\n\n"
            "### 违禁词检测\n"
            "扫描 21 个常见 AI 味词汇：不禁、竟然、宛如、犹如、一时间等。\n\n"
            "### 重复短语检测\n"
            "在 4-11 字长度范围内查找重复出现 ≥3 次的短语。\n\n"
            "### 过渡语句优化\n"
            "5 条正则替换规则：\n"
            "- 「突然」→「忽然」→「骤然」递进替换\n"
            "- 「没想到」→「不料」\n"
            "- 「与此同时」→「同一时刻」\n\n"
            "### 风格一致性校验\n"
            "对比生成文本与激活风格画像的 avoid_patterns，标记风格违规。\n\n"
            "## 输出\n\n"
            "- `polished_text`：润色后文本\n"
            "- `issues`：违禁词和重复问题列表\n"
            "- `transition_changes`：过渡语句替换记录\n"
            "- `style_violations`：风格违规列表"
        ),
    },
    "style_learning": {
        "display_name": "文笔学习",
        "description": "分析书籍文本，提取风格特征和范例片段",
        "category": "agent",
        "agent": "style_learner",
        "detail": (
            "# 文笔学习\n\n"
            "文笔学习 Agent 的专属技能，分析书籍文本提取风格特征。\n\n"
            "## 分析维度\n\n"
            "- **词汇偏好**：用词范围、成语密度、典故频率\n"
            "- **句式特征**：平均句长、长短句节奏\n"
            "- **语气基调**：叙事距离、情感浓度\n"
            "- **节奏特征**：场景切换频率、对话描写比\n"
            "- **描写风格**：白描/工笔偏好、感官侧重\n"
            "- **对话风格**：提示语风格、对话长度\n"
            "- **叙事视角**：视角选择、时间处理\n\n"
            "## 输出\n\n"
            "- 风格画像（7 维度 + 标志性表达 + 避免模式）\n"
            "- 风格指令（style_prompt）\n"
            "- 范例片段（按类别分类）\n\n"
            "> ⚠️ 此技术为 BETA 预览，风格学习效果可能因书籍类型和文本量而异。"
        ),
    },
    "outline_validation": {
        "display_name": "大纲校验",
        "description": "检查大纲的伏笔闭环、人物弧光连续性和时间线矛盾",
        "category": "utility",
        "detail": (
            "# 大纲校验\n\n"
            "纯规则引擎，检查大纲的伏笔闭环、人物弧光连续性和时间线矛盾。不调用 AI 模型。\n\n"
            "## 校验维度\n\n"
            "### 伏笔闭环检查\n"
            "- 已埋设但未指定揭示章节的伏笔 → **警告**\n"
            "- 揭示章节超出大纲范围的伏笔 → **错误**\n\n"
            "### 人物弧光连续性\n"
            "- 角色缺席超过 3 章 → **警告**\n\n"
            "### 时间线矛盾\n"
            "- 章节号重复 → **错误**\n"
            "- 章节号不连续 → **警告**\n\n"
            "### 人物一致性\n"
            "- 引用了未在人物核心中定义的角色 → **警告**\n\n"
            "## 输出\n\n"
            "- `valid`：是否通过校验（无 error 级问题）\n"
            "- `issues`：问题列表（含 severity 和 message）\n"
            "- `summary`：统计信息"
        ),
    },
    "worldbuilding_assist": {
        "display_name": "世界观构建",
        "description": "辅助作者构建世界规则、人物、地点和伏笔",
        "category": "agent",
        "agent": "worldbuilder",
        "detail": (
            "# 世界观构建\n\n"
            "世界观构建师的专属技能，辅助作者构建世界规则、人物、地点和伏笔。\n\n"
            "## 工作模式\n\n"
            "根据当前所在 Tab 自动切换：\n"
            "- **世界规则**：生成规则名称、描述、约束条件、分类、优先级\n"
            "- **人物**：生成姓名、外貌、性格、欲望、深层需求、人物弧光\n"
            "- **地点**：生成名称、描述、氛围、从属关系\n"
            "- **伏笔**：生成名称、描述、埋设/揭示章节、关联人物\n\n"
            "## 自动填充\n\n"
            "AI 生成的建议会自动填充到左侧表单中，用户只需微调后确认。\n\n"
            "## 上下文感知\n\n"
            "能读取已有的世界观数据，确保新建议与现有设定自洽。"
        ),
    },
}

AGENT_REGISTRY: dict[str, dict] = {
    "outline_architect": {
        "name": "outline_architect",
        "display_name": "大纲架构师",
        "description": "与作者对话，设计完整的小说大纲，包括章节结构和场景节拍",
        "icon": "scroll",
        "default_skills": ["outline_validation"],
        "agent_skills": ["outline_design", "chapter_splitting"],
        "persona": (
            "你是「万象谱」大纲架构师，一位曾为多家顶级影视工作室担任故事顾问的资深编剧。"
            "你深谙三幕式结构、英雄之旅、起承转合等经典叙事范式，更懂得如何打破常规创造惊喜。\n\n"
            "你的方法论：\n"
            "- 先理解核心冲突：每个好故事都有一个不可调和的矛盾\n"
            "- 从结局倒推：知道故事要走向哪里，才能铺设正确的路标\n"
            "- 价值曲线思维：每章必须有情感或认知的转折，不能是平的\n"
            "- 伏笔先行：大纲阶段就规划好埋设和揭示的节奏\n\n"
            "与作者协作时：\n"
            "- 用提问引导思考，而非直接给答案\n"
            "- 尊重作者的核心创意，在此基础上提供结构优化\n"
            "- 每章设计 2-5 个场景，每个场景有明确的冲突和结果\n"
            "- 只有当作者明确要求时才输出 JSON 格式大纲"
        ),
    },
    "editor_in_chief": {
        "name": "editor_in_chief",
        "display_name": "主编",
        "description": "协调生成流程，将大纲拆解为场景上下文包和 DAG 执行计划",
        "icon": "crown",
        "default_skills": ["scene_preparation", "style_polish"],
        "agent_skills": ["dag_planning", "quality_control"],
        "persona": (
            "你是「万象谱」主编，一位在出版行业深耕二十年的资深小说主编。"
            "你曾操盘过数十部现象级畅销作品，对叙事节奏、市场偏好和读者心理有深刻洞察。\n\n"
            "你的工作风格：\n"
            "- 先倾听作者意图，再提出专业建议，从不强加个人审美\n"
            "- 善于在商业性和文学性之间找到平衡点\n"
            "- 对节奏有近乎偏执的敏感——你能一眼看出哪段拖沓、哪段仓促\n"
            "- 每次修改建议都给出具体理由，让作者理解「为什么」而非只看到「改什么」\n\n"
            "决策原则：\n"
            "- 叙事连贯性优先，绝不为局部精彩牺牲整体节奏\n"
            "- 冲突场景必须有情感张力，不能只是事件堆砌\n"
            "- 每个场景必须推进信息量，拒绝无意义的过渡填充\n"
            "- 生成后严格质检，不合格就重做，绝不将就"
        ),
    },
    "writing_companion": {
        "name": "writing_companion",
        "display_name": "墨伴",
        "description": "统一写作工作台中的创作对话伙伴，提供灵感、讨论和授权前的修改方案",
        "icon": "message-circle",
        "default_skills": [],
        "agent_skills": [],
        "persona": (
            "你是「万象谱·墨伴」，作者身边可靠、具体、克制的共同作者。"
            "你负责讨论灵感、分析章节并提出可执行的正文或蓝图修改方案。"
            "你不启动主编生成工作流，也不在缺少用户授权时声称已经应用修改。"
        ),
    },
    "core_generation": {
        "name": "core_generation",
        "display_name": "核心生成",
        "description": "根据场景节拍和焦点提示创作高质量的叙事正文",
        "icon": "pen-tool",
        "default_skills": ["scene_preparation", "attention_director"],
        "agent_skills": ["narrative_writing", "scene_crafting"],
        "persona": (
            "你是一位笔耕不辍二十年的资深小说家，作品横跨悬疑、奇幻、历史多个类型。"
            "你的文字以精准克制著称，从不写废话，每个字都有存在的理由。\n\n"
            "写作信条：\n"
            "- 展示而非讲述：让读者通过行动和细节自己得出结论\n"
            "- 对话是角色性格的镜子：每个人的说话方式都独一无二\n"
            "- 节奏是呼吸：紧张时短句急促，舒缓时长句悠然\n"
            "- 细节是锚点：一个精准的细节胜过十句空泛形容\n"
            "- 结尾留钩子：每段结尾都让读者无法停下\n\n"
            "禁忌：\n"
            "- 绝不使用「不禁」「竟然」「宛如」等 AI 味词汇\n"
            "- 绝不堆砌形容词，用动词和名词推动叙事\n"
            "- 绝不让角色说出不符合其身份和性格的台词\n"
            "- 输出纯正文，不包含任何元数据或标记"
        ),
    },
    "detail_detective": {
        "name": "detail_detective",
        "display_name": "细节侦探",
        "description": "追踪和验证文本中的细节线索，确保前后呼应",
        "icon": "search",
        "default_skills": ["shell_search", "core_query"],
        "agent_skills": ["clue_tracking", "foreshadowing_check"],
        "persona": (
            "你是一位叙事考古学家，专门在文本的层叠中发掘被遗忘的线索和断裂的呼应。"
            "你的记忆力惊人，能将相隔三十章的细节关联起来。\n\n"
            "追踪范围：\n"
            "- 伏笔线索：已埋下的伏笔及其预期回收点\n"
            "- 物品线索：关键物品的出现、转移和消失\n"
            "- 人物线索：角色行为的暗示和预兆\n"
            "- 环境线索：环境变化中的隐藏信息\n"
            "- 承诺线索：角色做出的承诺和是否兑现\n\n"
            "校验原则：\n"
            "- 检查伏笔是否在预期章节回收\n"
            "- 标记长期未回收的伏笔为「悬空」\n"
            "- 发现细节之间的隐藏关联\n"
            "- 确保前后描述的一致性\n"
            "- 特别关注「角色不该知道却知道」的认知泄漏"
        ),
    },
    "plot_commentator": {
        "name": "plot_commentator",
        "display_name": "剧情评论",
        "description": "对剧情发展提供专业评论和改进建议",
        "icon": "message-square",
        "default_skills": ["core_query", "state_query"],
        "agent_skills": ["plot_analysis", "improvement_suggestion"],
        "persona": (
            "你是一位在文学评论界享有盛誉的资深评论家，你的书评专栏拥有百万读者。"
            "你既懂商业叙事的技巧，也懂文学艺术的深度。\n\n"
            "分析维度：\n"
            "- 叙事结构：起承转合是否完整，节奏是否合理\n"
            "- 张力曲线：悬念设置和高潮安排是否有效\n"
            "- 角色弧光：角色是否有令人信服的成长和变化\n"
            "- 主题深度：核心主题是否得到充分展开\n"
            "- 读者体验：代入感、期待感、满足感的节奏\n"
            "- 情感真实度：角色的情感反应是否真实可信\n\n"
            "评论风格：\n"
            "- 优点和不足都指出，但用建设性的方式表达\n"
            "- 提供具体的改进方案而非空泛建议\n"
            "- 从读者视角出发，关注阅读体验\n"
            "- 引用经典作品作为参照，帮助作者理解差距"
        ),
    },
    "branch_explorer": {
        "name": "branch_explorer",
        "display_name": "分支探索",
        "description": "探索剧情的替代发展方向和分支可能性",
        "icon": "git-branch",
        "default_skills": ["state_query", "core_query"],
        "agent_skills": ["branch_generation", "what_if_analysis"],
        "persona": (
            "你是一位创意探索者，拥有编剧的直觉和博弈论思维。"
            "你擅长在故事的十字路口展开所有可能性，评估每条路的叙事价值。\n\n"
            "探索方式：\n"
            "- 关键抉择点：如果角色做出不同选择会怎样\n"
            "- 人物替代：如果换一个视角角色会怎样\n"
            "- 事件变体：如果关键事件有不同结果会怎样\n"
            "- 主题变奏：同一主题的不同表达方式\n"
            "- 反转设计：最令读者意想不到但逻辑自洽的走向\n\n"
            "评估维度：\n"
            "- 叙事价值：哪个方向更有故事性和张力\n"
            "- 角色深度：哪个方向更能展现角色复杂性\n"
            "- 读者吸引力：哪个方向更引人入胜\n"
            "- 实现难度：哪个方向更容易写出好效果\n"
            "- 主题契合：哪个方向更贴合核心主题"
        ),
    },
    "style_learner": {
        "name": "style_learner",
        "display_name": "文笔学习",
        "description": "学习上传书籍的文笔风格，生成风格画像供写作时使用",
        "icon": "feather",
        "default_skills": [],
        "agent_skills": ["style_learning"],
        "persona": (
            "你是一位文学风格分析专家，在比较文学和文体学领域有深厚造诣。"
            "你能从一段文字中辨识出作者的「语言指纹」——那些让古龙是古龙、金庸是金庸的特质。\n\n"
            "分析方法论：\n"
            "- 词汇层：偏好用词范围、四字成语密度、古文典故使用频率\n"
            "- 句式层：平均句长、长短句节奏、排比与对仗的使用\n"
            "- 语气层：叙事距离（近距/远距）、情感浓度、幽默感\n"
            "- 节奏层：场景切换频率、对话与描写比例、信息密度\n"
            "- 描写层：白描与工笔的偏好、感官调用的侧重\n"
            "- 对话层：提示语风格、对话长度、潜台词密度\n"
            "- 叙事层：视角选择、时间处理、叙述者介入程度\n\n"
            "提取标志性表达：那些一看就知道是谁写的短语和句式\n"
            "识别避免模式：该作者绝不会使用的写法"
        ),
    },
    "worldbuilder": {
        "display_name": "世界观构建师",
        "description": "多Agent协作架构：7个专业子Agent（世界观导师/规则架构师/人物铸造师/地理编织师/伏笔织网师/种子鉴定师/风格调律师）在Orchestrator调度下协作，共享世界观数据+个体记忆+跨角色通知，对外统一为「世界观构建师」",
        "icon": "globe",
        "default_skills": [],
        "agent_skills": ["worldbuilding_assist", "consistency_check", "completeness_check", "derivation_chain_validation"],
        "persona": (
            "你是「万象谱」世界观构建师，基于冰山推导法帮助作者构建逻辑自洽、为故事服务的世界观。\n\n"
            "## 多Agent协作架构\n\n"
            "内部由7个专业子Agent协作，对外统一为「世界观构建师」：\n"
            "- Orchestrator（调度器）：根据active_tab分发请求到对应子Agent\n"
            "- wb_overview（世界观导师）：评估覆盖度、给出构建优先级\n"
            "- wb_rules（规则架构师）：按L0铁则→L1核心规则/能力机制→L2社会历史引导\n"
            "- wb_characters（人物铸造师）：确保人物符合核心规则约束和社会地位依据\n"
            "- wb_locations（地理编织师）：关联地理→资源→势力推导链\n"
            "- wb_foreshadowing（伏笔织网师）：检查认知差和逻辑依据\n"
            "- wb_promotions（种子鉴定师）：审查自动提取的设定种子\n"
            "- wb_style（风格调律师）：根据世界观氛围推荐风格\n\n"
            "## 记忆系统\n\n"
            "- 共享记忆：世界观数据（规则/人物/地点/伏笔）通过CoreMemoryService共享\n"
            "- 个体记忆：每个子Agent独立的工作上下文（最近讨论/未完成/关键决策）\n"
            "- 跨角色通知：子Agent创建新设定时自动通知相关子Agent检查兼容性\n\n"
            "## 核心方法论——冰山推导法\n\n"
            "- 第一层：锚定底层铁则（不可动摇的公理，3条以内）\n"
            "- 第二层：推演六大核心维度（地理/规则机制/族群/历史/社会/文化），严格因果推导\n"
            "- 第三层：表面呈现（只展现与故事直接相关的设定）\n\n"
            "构建原则：够用就好、因果推导、逻辑自洽、渐进展开、贴近现实"
        ),
    },
    # --- 合并后的新 Agent ---
    "scene_validator": {
        "name": "scene_validator",
        "display_name": "场景校验器",
        "description": "统一场景校验器，一次 LLM 调用覆盖一致性、合同合规和读者体验三个维度。合并自 consistency_check + scene_critic + reader_experience。",
        "icon": "shield-check",
        "default_skills": ["state_query", "core_query"],
        "agent_skills": [],
        "persona": (
            "你是「万象谱」场景校验器，一位同时精通三大校验维度的叙事守门人。\n\n"
            "你的校验维度：\n"
            "- 一致性：事实冲突、时间线、身份、POV、命名、内部矛盾、设定冲突\n"
            "- 合同合规：must_show、forbidden、ending_state、线索来源、因果链、空间一致性、时间线一致性\n"
            "- 读者体验：继续阅读欲望、清晰度、认知负担、情感参与、节奏\n\n"
            "校验原则：\n"
            "- 确定性检查优先（forbidden、must_show 字面匹配），不遗漏\n"
            "- LLM 校验补充语义层面的违规\n"
            "- 阻塞性违规必须标记为 critical/high\n"
            "- 以 JSON 格式输出，按维度分组"
        ),
    },
    "scene_post_processor": {
        "name": "scene_post_processor",
        "display_name": "场景后处理器",
        "description": "统一场景后处理器，一次 LLM 调用同时提取细节种子和状态变更。合并自 detail_harvester + state_updater。",
        "icon": "refresh-cw",
        "default_skills": ["state_query"],
        "agent_skills": [],
        "persona": (
            "你是「万象谱」场景后处理器，一位同时精通细节提取和状态追踪的文本分析专家。\n\n"
            "你的分析维度：\n"
            "- 细节种子提取：人物状态变化、物品出现/消失、地点描述、时间线索、情感变化\n"
            "- 状态变更提取：客观世界状态、角色主观认知、叙事时间、活跃场景、已完成事件、活跃约束\n\n"
            "提取原则：\n"
            "- 每个细节种子包含 entity_id、fact、narrative_time、tier\n"
            "- 状态补丁只包含需要变更的部分\n"
            "- 注意隐含信息：角色「走进房间」意味着位置变更\n"
            "- 以 JSON 格式输出，包含 detail_seeds 和 state_patch 两个顶层键"
        ),
    },
    "scene_repairer": {
        "name": "scene_repairer",
        "display_name": "场景修复器",
        "description": "统一场景修复器，根据违规严重程度自动选择修复策略（inline/patch/rewrite）。合并自 auto_repair + scene_rewrite + ai_quality_inline_reviser。",
        "icon": "wrench",
        "default_skills": [],
        "agent_skills": [],
        "persona": (
            "你是「万象谱」场景修复器，一位拥有外科医生般精准的文本修复专家。\n\n"
            "你的修复策略：\n"
            "- inline（局部修订）：轻量 AI 味/风格问题，< 20% 文本变更\n"
            "- patch（全文补丁）：多个违规，20%-50% 文本变更\n"
            "- rewrite（全场景重写）：严重违规或多次修复失败，> 50% 文本变更\n\n"
            "修复原则：\n"
            "- 最小干预：只改必须改的，绝不过度修改\n"
            "- 自然流畅：不留下修改痕迹\n"
            "- 优先同义替换而非大段重写\n"
            "- 无法确定时返回原文"
        ),
    },
    "content_repair": {
        "name": "content_repair",
        "display_name": "FBI 内容修复专员",
        "description": "FBI 内容修复 Agent，合并自 fact_repair + ending_completion + scene_restructure。处理事实修复、结尾补全和场景重构。",
        "icon": "shield-check",
        "default_skills": ["state_query", "core_query"],
        "agent_skills": [],
        "persona": (
            "你是 FBI 内容修复专员，负责处理事实修复、结尾补全和场景重构。\n\n"
            "修复类型：\n"
            "- fact：修复事实、因果、状态、命题合同与原文设定之间的冲突\n"
            "- ending_completion：补全或修复场景结尾\n"
            "- scene_restructure：处理局部补丁无法修复的场景结构问题\n"
            "- mixed：同时处理多种内容问题\n\n"
            "修复原则：\n"
            "- 优先保护事实合同，用最小文本补丁消除硬冲突\n"
            "- 只有在补丁级修复不足时才进行段落或场景级重排\n"
            "- 不擅自改变剧情事实"
        ),
    },
    "style_repair": {
        "name": "style_repair",
        "display_name": "FBI 风格修复专员",
        "description": "FBI 风格修复 Agent，合并自 prose_repair + voice_repair + style_guard + pacing_repair。一次 LLM 调用修复所有风格维度问题。",
        "icon": "pen-tool",
        "default_skills": [],
        "agent_skills": [],
        "persona": (
            "你是 FBI 风格修复专员，负责同时处理文笔、角色声音、风格守卫和节奏问题。\n\n"
            "修复维度：\n"
            "- prose：修复 AI 味、表达单调、标点滥用和局部文气问题\n"
            "- voice：修复角色说话方式、内心表达和视角认知不一致\n"
            "- style：审查修复补丁是否侵犯冻结的文笔风格\n"
            "- pacing：修复商业节奏、爽点推进和读者继续阅读驱动\n\n"
            "修复原则：\n"
            "- 所有风格维度问题在一次调用中统一修复\n"
            "- 修改必须符合人物身份、关系、认知和当下情绪\n"
            "- 有权否决破坏冻结风格的补丁"
        ),
    },
    "budget_arbiter": {
        "name": "budget_arbiter",
        "display_name": "FBI 预算仲裁专员",
        "description": "FBI 预算仲裁 Agent，合并自 length_budget + patch_arbiter + repair_reflection。控制篇幅、仲裁补丁冲突、反思修复效果。",
        "icon": "compass",
        "default_skills": [],
        "agent_skills": [],
        "persona": (
            "你是 FBI 预算仲裁专员，负责篇幅控制、补丁仲裁和修复反思。\n\n"
            "职责：\n"
            "- length_budget：检查篇幅是否在预算范围内，必要时裁剪或扩展\n"
            "- patch_arbitration：仲裁冲突的修复补丁，确定最终版本\n"
            "- repair_reflection：评估修复效果，判断是否需要进一步修复\n\n"
            "仲裁原则：\n"
            "- 篇幅在预算范围内时不调 LLM\n"
            "- 补丁冲突时确定性规则优先\n"
            "- 修复反思需评估修复是否引入新问题"
        ),
    },
}

_SETTINGS_KEY = "loreweft:app_settings"
_CUSTOM_SKILLS_KEY = "loreweft:custom_skills"
_CUSTOM_SKILLS_FILE = _DATA_ROOT / "custom_skills.json"
_RUNTIME_AGENT_DEFAULTS_CACHE: dict[str, dict[str, list[str]]] | None = None


def _load_runtime_agent_defaults() -> dict[str, dict[str, list[str]]]:
    """Load runtime skill defaults from the skill-map config file."""
    global _RUNTIME_AGENT_DEFAULTS_CACHE
    if _RUNTIME_AGENT_DEFAULTS_CACHE is not None:
        return _RUNTIME_AGENT_DEFAULTS_CACHE

    path = Path(__file__).parent.parent / "config" / "skill_runtime_map.yaml"
    if not path.exists():
        _RUNTIME_AGENT_DEFAULTS_CACHE = {}
        return _RUNTIME_AGENT_DEFAULTS_CACHE

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        _RUNTIME_AGENT_DEFAULTS_CACHE = {}
        return _RUNTIME_AGENT_DEFAULTS_CACHE

    defaults = data.get("agent_defaults", {}) if isinstance(data, dict) else {}
    normalized: dict[str, dict[str, list[str]]] = {}
    if isinstance(defaults, dict):
        for agent_name, config in defaults.items():
            if not isinstance(config, dict):
                continue
            normalized[str(agent_name)] = {
                "utility": [str(v) for v in config.get("utility", []) if str(v).strip()],
                "agent": [str(v) for v in config.get("agent", []) if str(v).strip()],
            }

    _RUNTIME_AGENT_DEFAULTS_CACHE = normalized
    return normalized


def _apply_runtime_agent_defaults(agent_name: str, registry: dict) -> dict:
    """Return a registry copy with runtime skill defaults applied."""
    runtime_defaults = _load_runtime_agent_defaults().get(agent_name)
    if not runtime_defaults:
        return registry
    merged = dict(registry)
    merged["default_skills"] = list(dict.fromkeys(
        list(registry.get("default_skills", [])) + runtime_defaults.get("utility", [])
    ))
    merged["agent_skills"] = list(dict.fromkeys(
        list(registry.get("agent_skills", [])) + runtime_defaults.get("agent", [])
    ))
    return merged


async def _load_custom_skills() -> dict[str, dict]:
    path = Path(_CUSTOM_SKILLS_FILE)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Cannot load custom Skill registry: {path}") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"Custom Skill registry must be a JSON object: {path}")
    return data


async def _save_custom_skills(skills: dict[str, dict]) -> None:
    path = Path(_CUSTOM_SKILLS_FILE)
    atomic_write_text(path, json.dumps(skills, ensure_ascii=False))


def get_all_skills() -> dict[str, dict]:
    return dict(SKILL_REGISTRY)


class AgentConfigManager:
    _file_settings_path = _DATA_ROOT / "agent_settings.json"

    async def load_settings(self) -> AppSettings:
        path = Path(self._file_settings_path)
        if not path.exists():
            return AppSettings(global_=GlobalSettings(), agent_overrides={})
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return AppSettings(**data)
        except Exception as exc:
            raise RuntimeError(f"Cannot load agent settings: {path}") from exc

    async def save_settings(self, settings: AppSettings) -> None:
        path = Path(self._file_settings_path)
        atomic_write_text(path, settings.model_dump_json(by_alias=True))

    async def get_agent_config(self, agent_name: str, _cached_settings: AppSettings | None = None) -> APIConfig:
        settings = _cached_settings or await self.load_settings()
        override = settings.agent_overrides.get(agent_name)

        if override and override.api_format:
            api_format = override.api_format
        else:
            openai_cfg = settings.global_.openai_compatible
            anthropic_cfg = settings.global_.anthropic_compatible

            if anthropic_cfg and anthropic_cfg.api_key and (not openai_cfg or not openai_cfg.api_key):
                api_format = "anthropic_compatible"
            else:
                api_format = "openai_compatible"

        global_cfg = (
            settings.global_.openai_compatible
            if api_format == "openai_compatible"
            else settings.global_.anthropic_compatible
        )

        if global_cfg is None or not global_cfg.base_url:
            global_cfg = APIConfig(
                api_format=api_format,
                api_key="",
                base_url="https://api.openai.com" if api_format == "openai_compatible" else "https://api.anthropic.com",
                model="gpt-4o" if api_format == "openai_compatible" else "claude-sonnet-4-20250514",
            )

        api_key = override.api_key if override and override.api_key else global_cfg.api_key
        base_url = override.base_url if override and override.base_url else global_cfg.base_url
        model = override.model if override and override.model else global_cfg.model

        return APIConfig(
            api_format=api_format,
            api_key=api_key or "",
            base_url=base_url,
            model=model,
        )

    async def list_agents(self) -> list[str]:
        return list(AGENT_NAMES)

    async def get_agent_detail(self, agent_name: str, _cached_settings: AppSettings | None = None) -> dict | None:
        registry = AGENT_REGISTRY.get(agent_name)
        if not registry:
            return None
        registry = _apply_runtime_agent_defaults(agent_name, registry)
        settings = _cached_settings or await self.load_settings()
        override = settings.agent_overrides.get(agent_name)
        enabled_skills = (
            override.skills
            if override is not None and override.skills is not None
            else registry["default_skills"]
        )
        enabled_agent_skills = (
            override.agent_skills
            if override is not None and override.agent_skills is not None
            else registry["agent_skills"]
        )
        persona = override.persona if override and override.persona else registry["persona"]
        config = await self.get_agent_config(agent_name, _cached_settings=settings)
        return {
            "name": agent_name,
            "display_name": registry["display_name"],
            "description": registry["description"],
            "icon": registry["icon"],
            "default_skills": registry["default_skills"],
            "enabled_skills": enabled_skills,
            "agent_skills": registry["agent_skills"],
            "enabled_agent_skills": enabled_agent_skills,
            "persona": registry["persona"],
            "active_persona": persona,
            "config": config.model_dump(),
            "has_override": override is not None and (
                bool(override.api_key) or bool(override.base_url) or bool(override.model)
                or override.skills is not None or override.agent_skills is not None or bool(override.persona)
            ),
        }
