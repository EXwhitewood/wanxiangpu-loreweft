---
name: anti-ai-prose
description: >-
  中文小说核心去 AI 味能力：约束模板化承接、意义通胀、促销腔、模糊归因、抽象名词堆积、
  过度解释、章末说教、破折号滥用、均质句式和无角色声纹的平滑叙事。它输出的是可修复的
  风格质量信号，不是作者身份判断。
version: "3.0.0"
tags:
  - writing
  - style
  - chinese-fiction
  - anti-ai
x-loreweft:
  id: anti_ai_prose
  display_name: "去 AI 味写作"
  format_version: open_skill_v2
  source_format: x-loreweft
  kind: prompt
  domain: writing
  category: agent
  agents:
    - core_generation
    - dialogue_generation
    - description_generation
    - editor_in_chief
  priority: 95
  enabled_by_default: true

  compatibility:
    portable_layer: "通用 SKILL.md 元数据与 Markdown 正文，其他 IDE/Agent 可直接读取。"
    loreweft_layer: "x-loreweft 承载万象谱的结构化约束、validator、repair 路由和 trace。"
    executable_runtime: false
    import_policy: "外部导入 skill 的可执行字段默认禁用，只有显式信任后才可启用。"

  ownership:
    role: "style_quality_contract"
    not_roles:
      - author_identity_detector
      - final_commit_gate
      - prose_repair_executor
      - autonomous_agent

  capability:
    summary: "让小说正文从模板化、解释化、过度平滑的 AI 腔，转向有角色声纹、场景证据、局部摩擦和节奏变化的叙事。"
    mount_policy: required_when_writer_generates_prose
    enforcement_policy: advisory_except_dash_hard_block
    agent_method:
      - "生成前先确定一个局部声纹来源：POV 偏见、当场压力、身体代价、物件历史或关系立场。"
      - "把抽象评价改成可观察的动作、后果、物件状态、感官痕迹或角色判断。"
      - "用视角和处境制造不完美，不用错别字、廉价口语和无意义混乱假装真人。"
      - "优先使用精确名词和主动动词，减少宏大解释词。"
      - "生成后必须复查模板词、破折号密度、句长变化和无证据情绪解释。"

  domain_scope:
    applies_to:
      - 中文小说正文
      - 对白间动作
      - 场景描写
      - 风格修复稿
      - 章节收束
    does_not_apply_to:
      - 元数据摘要
      - UI 文案
      - 事实诊断报告

  activation:
    mount:
      default: required
      when:
        - "任意 Writer 路径生成中文小说正文。"
        - "QualityGate、Review 或用户反馈出现任意 ai_* 问题。"
        - "任意 repair hook 重写正文后，可能重新引入平滑模板腔。"
    skip_allowed_only_if:
      - "输出不是小说正文。"
      - "上层 workflow 明确记录 degraded override。"

  references:
    - references/chinese-pattern-taxonomy.md
    - references/fiction-patterns.md
    - references/voice-fingerprint.md
    - references/research-evidence.md
  resource_packs:
    - resources/chinese_lexicon.yaml
    - resources/discourse_patterns.yaml
    - resources/voice_defaults.yaml

  triggers:
    scene_roles:
      - all
    issue_types:
      - ai_template_phrase
      - ai_emotion_label
      - ai_inflated_significance
      - ai_promotional_language
      - ai_filler_phrase
      - ai_negative_parallel
      - ai_false_range
      - ai_vague_attribution
      - ai_abstract_noun_abuse
      - ai_dash_abuse
      - ai_three_part_escalation
      - ai_uniform_rhythm
      - ai_structure_word_abuse
      - ai_visual_verb_abuse
      - ai_sterile_voiceless
      - ai_simile_overuse
      - ai_template_carryover
      - ai_synesthesia_formula
      - ai_false_agency
      - ai_rhythm_uniformity

  constraints:
    # enforcement 分级：默认 advisory，破折号例外为 hard_blocking
    # hard_blocking 指标修复耗尽后进入 waiting_review
    # advisory 指标最多修两轮后无条件放行（advisory_exhausted）
    enforcement:
      hard_blocking:
        - dash_per_1000
        - consecutive_dash_max
        - ai_punctuation_artifact
      advisory:
        - tier1_hit_count
        - tier2_cluster_count
        - tier3_density_max
        - three_part_escalation_count
        - negative_parallel_count
        - false_range_count
        - vague_attribution_count
        - filler_phrase_count
        - promotional_language_count
        - inflated_significance_count
        - structure_word_cluster_count
        - chapter_end_moral_count
        - overexplain_count
        - sentence_length_cv_min
        - simile_density_max
        - template_carryover_count_max
        - synesthesia_formula_count_max
        - false_agency_count_max
    ai_flavor:
      tier1_hit_count: 0
      tier2_cluster_count: 0
      tier3_density_max: 0.03
      three_part_escalation_count: 0
      negative_parallel_count: 0
      false_range_count: 0
      vague_attribution_count: 0
      filler_phrase_count: 0
      promotional_language_count: 0
      inflated_significance_count: 0
      structure_word_cluster_count: 0
      chapter_end_moral_count: 0
      overexplain_count: 0
      dash_per_1000_max: 0.5
      consecutive_dash_max: 2
      sentence_length_cv_min: 0.32
      simile_density_max: 1.0
      template_carryover_count_max: 2
      synesthesia_formula_count_max: 2
      false_agency_count_max: 1
      max_high_advisories: 3
      max_medium_advisories: 8
      retry_policy:
        max_retries: 1
        action: prose_repair
    ai_discourse:
      sentence_shell_count_max: 1
      paragraph_shape_repeat_count_max: 1
      semantic_restatement_count_max: 0
      action_then_explanation_count_max: 0
      mirrored_paragraph_opening_count_max: 1
      double_conclusion_count_max: 0
      moral_overclarity_count_max: 1
      fake_interaction_count_max: 0
      retry_policy:
        max_retries: 1
        action: paragraph_reconstruction
    voice_fingerprint:
      profile_required: false
      minimum_score: 60
      generic_voice_density_max: 3.0
      avoided_word_hits_max: 0
      retry_policy:
        max_retries: 1
        action: voice_reconstruction

  runtime:
    kind: prompt
    execution_phase: pre_generation
    timeout_seconds: 30
    output_char_budget: 3600
    required_context:
      - scene_contract
      - style_profile
    optional_context:
      - generated_prose_or_draft
      - quality_memory
      - pov_character_card
      - writing_mode_profile
      - narrative_config
      - voice_fingerprint
      - character_cards

  validators:
    - ai_flavor
    - ai_discourse
    - voice_fingerprint
  validation_contracts:
    ai_flavor:
      tier1_hit_count: 0
      tier2_cluster_count: 0
      tier3_density_max: 0.03
      three_part_escalation_count: 0
      negative_parallel_count: 0
      false_range_count: 0
      vague_attribution_count: 0
      filler_phrase_count: 0
      promotional_language_count: 0
      inflated_significance_count: 0
      structure_word_cluster_count: 0
      chapter_end_moral_count: 0
      overexplain_count: 0
      dash_per_1000_max: 0.5
      consecutive_dash_max: 2
      sentence_length_cv_min: 0.32
      simile_density_max: 1.0
      template_carryover_count_max: 2
      synesthesia_formula_count_max: 2
      false_agency_count_max: 1
      max_high_advisories: 3
      max_medium_advisories: 8
      retry_policy:
        max_retries: 1
        action: prose_repair
    ai_discourse:
      sentence_shell_count_max: 1
      paragraph_shape_repeat_count_max: 1
      semantic_restatement_count_max: 0
      action_then_explanation_count_max: 0
      mirrored_paragraph_opening_count_max: 1
      double_conclusion_count_max: 0
      moral_overclarity_count_max: 1
      fake_interaction_count_max: 0
      retry_policy:
        max_retries: 1
        action: paragraph_reconstruction
    voice_fingerprint:
      profile_required: false
      minimum_score: 60
      generic_voice_density_max: 3.0
      avoided_word_hits_max: 0
      retry_policy:
        max_retries: 1
        action: voice_reconstruction

  repair_hooks:
    - prose_repair
    - paragraph_reconstruction
    - voice_reconstruction
  repair_strategies:
    - tier1_replace
    - tier2_decluster
    - tier3_thin
    - break_three_part
    - break_negative_parallel
    - break_false_range
    - replace_vague_attribution
    - remove_filler
    - remove_promotional
    - tone_down_inflated
    - reduce_dash
    - vary_sentence_length
    - remove_chapter_end_moral
    - remove_overexplain
    - inject_personality_opinion
    - inject_imperfection
    - remove_sentence_shell
    - remove_semantic_restatement
    - remove_action_explanation
    - break_paragraph_isomorphism
    - preserve_emotional_ambiguity
    - restore_voice_fingerprint

  failure_policy:
    validator_failed: "词句级失败路由给 prose_repair，语篇级失败路由给 paragraph_reconstruction，声纹失败路由给 voice_reconstruction；修复后重新运行三个 validator 和保护审计。"
    unimplemented_validator: "degraded_nonpass"
    repair_failed: "返回 failed trace 给 workflow quality gate，不静默放行。"

  protection_policy:
    preserve:
      - scene markers
      - fact contract
      - numbers
      - assertion polarity
      - causal relations
      - responsibility attribution
      - character intent
      - information knowledge state
      - required ambiguity
      - POV boundary
      - chronology
      - foreshadowing obligations
    forbid:
      - "为了显得像真人而改动事实。"
      - "为了显得不机器而制造错别字、廉价口语、无意义断裂。"

  trace_policy:
    required_fields:
      - status
      - metrics
      - failed_contracts
      - repair_hooks
      - override_reason
---
# 去 AI 味写作

## 1. 定位

`anti_ai_prose` 是万象谱中文小说正文的核心风格质量合同。它不判断作者是不是 AI，也不追求骗过某个检测器；它只处理正文里可读、可定位、可修复的“机器化写法信号”。

这个 skill 的目标是让文本从“正确但无生命的标准答案”，变成“由一个具体视角在具体场景里说出的叙事”。它必须同时服务三个目标：

- 去掉模板腔：删除报告体、营销腔、总结腔、意义通胀。
- 增加现场性：用行动、代价、物件、感官、角色判断承载意义。
- 保持可控：不破坏事实、POV、章节 marker、伏笔和信息释放顺序。

## 2. 何时必须使用

以下场景必须挂载本 skill：

- Writer 生成中文小说正文、对白间动作、描写段、章节收束。
- 用户要求“去 AI 味”“像真人写”“减少模板腔”“降低机器感”。
- 质量系统出现 `ai_*`、`emotion_label`、`abstract_without_evidence`、`uniform_rhythm`、`dash_abuse` 等问题。
- `prose_repair`、`style_polish`、`voice_repair` 等修复后，需要防止模板腔回流。

以下场景可以跳过：

- 生成的是结构化 JSON、诊断报告、前端说明、日志摘要。
- workflow 明确记录该轮是 degraded 输出，并说明为什么不能执行本 skill。

## 3. 职责边界

本 skill 负责风格质量信号，不负责最终修改文本。Agent 或 Validator 发现问题；Repair 系统生成修复候选；保护审计和提交门决定是否生效。

边界如下：

| 相邻能力 | 本 skill 的边界 |
|---|---|
| `show_dont_tell` | 情绪标签必须转为场景证据时，由 `show_dont_tell` 主责；本 skill 只检查“标签化/解释化”是否形成 AI 腔。 |
| `narrative_writing` | POV、时间锚点、叙事距离由叙事 skill 主责；本 skill 不为去 AI 味而切换视角。 |
| `scene_crafting` | 目标、冲突、价值变化、钩子由场景 skill 主责；本 skill 不凭空增加剧情冲突。 |
| `style_polish` | 语感润色可以改善句子，但不得把本 skill 删除的模板词和意义通胀加回来。 |

## 4. 三层识别协议

### 4.1 Tier 1：命中即修

以下模式一旦出现在小说正文中，默认视为强风险：

| 类型 | 常见信号 | 处理方式 |
|---|---|---|
| 模板承接 | `值得注意的是`、`不难发现`、`换句话说`、`综上所述` | 删除；必要信息改成角色行动或场景后果。 |
| 意义通胀 | `标志着`、`见证了`、`至关重要`、`里程碑`、`深远意义` | 改成具体变化：谁失去什么、获得什么、误解什么。 |
| 促销腔 | `令人叹为观止`、`美轮美奂`、`无与伦比`、`博大精深` | 改成可感知细节或角色评价。 |
| 模糊归因 | `有人说`、`专家认为`、`研究表明` | 小说场景内无来源则删除；有来源则写清说话者和代价。 |
| 章末说教 | `这只是一个开始`、`未来的路还很长` | 改成物件变化、危险逼近、选择落地或信息缺口。 |

### 4.2 Tier 2：集群触发

单个词未必有问题，但同段密集出现会形成模板感：

- 视觉/氛围动词集群：`勾勒`、`渲染`、`交织`、`流淌`、`弥漫`、`晕染`、`烙印`、`蛰伏`。
- 抽象名词集群：`命运`、`宿命`、`羁绊`、`执念`、`救赎`、`灵魂`、`意义`、`价值`、`本质`。
- 结构词集群：`首先`、`其次`、`再次`、`最后`、`由此可见`。

处理原则：保留最准确的一个，其余转为物件、动作、身体反应、关系变化或局部后果。

### 4.3 Tier 3：密度与节奏触发

以下指标需要后验验证：

- 破折号对 `——` 每两千字不超过 1 处，即每千字不超过 0.5，连续不超过 2。
- 句长变化系数不能过低；连续同长度句式会形成“模型均质节奏”。
- 情绪解释不能连续覆盖行动本身。
- 章末不能用总结性判断替代场景后果。

## 5. 写作转换方法

### 5.1 抽象转现场

不要写：

> 这次相遇对她有着深远意义。

要写：

> 她把那枚铜扣攥进掌心，直到边缘硌出一圈白印。出门前，她没有再看供桌上的名字。

转换公式：

```text
抽象判断 -> 谁在什么压力下做了什么 -> 产生什么可见后果
```

### 5.2 解释转代价

不要解释“他很后悔”“她终于明白”。让代价落地：

- 他删掉写了一夜的信，却把信封上的地址撕下来塞进袖口。
- 她没再争辩，只把钥匙推回桌边，齿痕朝下。

### 5.3 平滑转声纹

“真人感”不是粗糙，而是视角有偏差、有取舍、有局部判断。可用来源：

- POV 角色的职业词汇；
- 角色的误解、偏见、恐惧、执念；
- 当下身体状态；
- 和某个物件或地点的私人历史；
- 对另一个角色的关系立场。

禁止用错别字、网络口头禅、廉价脏话、随机断句来伪装自然。

### 5.4 章末去说教

章末优先落在以下实体上：

- 一个新危险；
- 一个未兑现承诺；
- 一个物件的变化；
- 一个角色做出的选择；
- 一个信息缺口；
- 一个关系位置的改变。

不要用“而这一切才刚刚开始”收尾。

## 6. 反例与改法

| 反例 | 问题 | 改法 |
|---|---|---|
| `这标志着两人关系进入新阶段。` | 意义通胀、旁白结论 | 写两人从称呼、距离、物件交还方式发生变化。 |
| `不难发现，局势正在变得复杂。` | 报告体模板 | 写谁发现了哪条线索、哪个选择因此变坏。 |
| `命运的齿轮开始转动。` | 抽象陈词 | 写门闩落下、信被烧掉、名单多出一个名字。 |
| `她很悲伤地说。` | 情绪标签 | 让对白内容、停顿、动作和物件承载悲伤。 |
| `灯光渲染出压抑的氛围。` | 氛围动词泛化 | 写灯管嗡鸣、墙皮潮湿、杯底水印、角色避开的视线。 |

## 7. 验收合同

运行时必须执行：

- `ai_flavor`
- `ai_discourse`
- `voice_fingerprint`

核心通过标准：

| 指标 | 通过标准 | 失败动作 |
|---|---|---|
| Tier 1 命中 | `tier1_hit_count == 0` | `tier1_replace` |
| Tier 2 集群 | `tier2_cluster_count == 0` | `tier2_decluster` |
| 三段式递进 | `three_part_escalation_count == 0` | `break_three_part` |
| 否定排比 | `negative_parallel_count == 0` | `break_negative_parallel` |
| 虚假范围 | `false_range_count == 0` | `break_false_range` |
| 破折号密度 | `dash_per_1000 <= 0.5` | `reduce_dash` |
| 章末说教 | `chapter_end_moral_count == 0` | `remove_chapter_end_moral` |
| 过度解释 | `overexplain_count == 0` | `remove_overexplain` |
| 中文句壳 | `sentence_shell_count <= 1` | `paragraph_reconstruction` |
| 段落同构 | `paragraph_shape_repeat_count <= 1` | `paragraph_reconstruction` |
| 语义复述 | `semantic_restatement_count == 0` | `paragraph_reconstruction` |
| 动作后解释 | `action_then_explanation_count == 0` | `paragraph_reconstruction` |
| 声纹分数 | 有声纹时 `voice_fingerprint_score >= 60` | `voice_reconstruction` |
| 通用声纹密度 | `generic_voice_density <= 3.0` | `voice_reconstruction` |

## 8. 失败路由

本 skill 不直接修正文。失败后由 runtime 按指标路由：

```text
ai_flavor failed -> prose_repair
ai_discourse failed -> paragraph_reconstruction
voice_fingerprint failed -> voice_reconstruction
repair completed -> rerun validators -> rerun marker/protection audit
```

若修复后仍失败，应记录 failed trace，不允许仅因 medium advisory 可放行而吞掉本 skill 的硬合同失败。

## 9. 保护策略

任何去 AI 味修复都不得：

- 删除或改写 `[[SCENE:...]]`、章节 marker、硬合同字段。
- 改变事实、数字、肯定/否定极性、因果关系、责任归属、角色意图。
- 改变信息可知状态、必须模糊项、时间线、人物关系、伏笔义务。
- 为减少模板感而新增未授权设定。
- 为制造“人味”而添加错别字、口癖、废话或无意义噪声。

## 10. Trace 要求

运行时至少记录：

```json
{
  "skill_id": "anti_ai_prose",
  "status": "passed | repaired | failed | degraded",
  "metrics": {
    "tier1_hit_count": 0,
    "tier2_cluster_count": 0,
    "dash_per_1000": 0.0,
    "sentence_length_cv": 0.0,
    "chapter_end_moral_count": 0,
    "overexplain_count": 0,
    "sentence_shell_count": 0,
    "paragraph_shape_repeat_count": 0,
    "semantic_restatement_count": 0,
    "voice_fingerprint_score": 78,
    "generic_voice_density": 1.2
  },
  "failed_contracts": [],
  "repair_hooks": ["prose_repair"],
  "override_reason": null
}
```

## 11. 资源使用

- 词句级模式按需读取 `resources/chinese_lexicon.yaml`。
- 语篇级模式按需读取 `resources/discourse_patterns.yaml`。
- 声纹字段和缺失策略按需读取 `resources/voice_defaults.yaml`。
- 分类说明、小说模式、声纹方法和研究边界位于 `references/`。
- references 不自动注入 Writer prompt；resource packs 由 validator 结构化加载。

## 12. 参考来源

- Wikipedia 对 AI-like writing signs 的人工编辑模式整理：https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing
- GLTR 关于高可预测文本与生成文本特征的研究：https://arxiv.org/abs/1906.04043
- DetectGPT 对模型生成文本检测边界的研究：https://arxiv.org/abs/2301.11305
- blader/humanizer（GitHub 22000+ 星）24 条 AI 写作特征列表：https://github.com/blader/humanizer
- op7418/Humanizer-zh 中文版 24 条特征：https://github.com/op7418/Humanizer-zh
- hardikpandya/stop-slop（GitHub 8500+ 星）更严格的 Skill：https://github.com/hardikpandya/stop-slop

---

## 13. AI 写作特征完整清单（生成时引导）

> 本节基于 GitHub 开源项目 blader/humanizer、op7418/Humanizer-zh、hardikpandya/stop-slop 综合，对中文小说正文（玄幻/都市/言情/历史/科幻等所有题材）通用。Writer 生成时必须遵循以下规则。

### 13.1 五条核心原则

1. **删除填充短语**：不增加信息的铺垫词、过渡句、解释性从句，直接进入实质内容。
2. **打破公式结构**：否定式排比、三段式法则、虚假范围、"从 X 到 Y"结构，用直接陈述替代。
3. **变化节奏**：长短句交替，1-3 字短句与 5-8 字长句穿插，句长 CV ≥ 0.40。
4. **信任读者**：不解释每个动作的动机，不总结每段的含义，让读者从动作和物件中推断。
5. **删除金句**：章末不升华、不总结、不点题，停在角色行动或未解问题上。

### 13.2 24 条通用 AI 写作特征

#### 内容模式（6 条）

1. **夸大意义、遗产和更广泛趋势**：避免"标志着"、"见证了"、"不可磨灭的印记"、"深深植根于"。小说正文典型表现："这一刻标志着她命运的转折" → 直接描写发生了什么变化。
2. **过度强调知名度**：避免"名震江湖"、"享誉天下"、"世人皆知"。小说正文典型表现：用具体人物的反应替代抽象知名度。
3. **以 -ing 结尾的肤浅分析**（中文化变体）：避免"……着"、"……地"结尾的从句堆砌。小说正文典型表现："她握着剑，紧绷着，警惕着四周的动静" → "她握剑，盯着四周"。
4. **宣传和广告式语言**：避免"令人叹为观止"、"无与伦比"、"充满活力"。小说正文典型表现："令人叹为观止的剑法" → "剑法快得看不清"。
5. **模糊归因和含糊措辞**：避免"据说"、"传闻"、"人们都说"、"江湖传言"。小说正文典型表现：用具体人物的话语替代模糊归因。
6. **提纲式的"挑战与未来展望"**：避免"尽管前方还有重重困难"、"未来还很长"、"这只是一个开始"。小说正文典型表现：用具体行动或悬念替代抽象展望。

#### 语言和语法模式（6 条）

7. **过度使用的 AI 词汇**：避免"格局"、"至关重要"、"不可或缺"、"深度融合"、"织锦"。小说正文典型表现：用具体名词替代抽象词。
8. **避免使用"是"（系动词回避）**：避免"作为"、"充当"、"扮演着"、"拥有着"、"设有"。小说正文典型表现："作为修仙界的顶尖宗门" → "修仙界顶尖宗门"。
9. **否定式排比**：避免"不是 X，是 Y"、"不是 X，而是 Y"、"不仅仅是 X，更是 Y"、"不是 A，不是 B，而是 C"。小说正文典型表现："不是烧焦，是药性被激活的状态" → "叶片发黑，药性被激活了"。每章 ≤ 3 处。
10. **三段式法则过度使用**：避免"从 A 到 B，从 B 到 C，从 C 到 D"、"既 A 又 B 还 C"。小说正文典型表现：用两项或单点替代三段。
11. **刻意换词（同义词循环）**：避免同一段落内"剑/兵刃/利器/锋芒"指代同一把剑。小说正文典型表现：同一实体用同一个词。
12. **虚假范围**：避免"从 X 到 Y"但 X 和 Y 不在有意义的尺度上。小说正文典型表现："从丹田到中脘，从中脘到膻中" → "从丹田一路上行到膻中"。

#### 风格模式（7 条）

13. **破折号过度使用**：每千字 ≤ 0.5 对（8266 字 ≤ 4 对）。小说正文典型表现："是四师兄——" → "是四师兄。"。stop-slop 将此设为硬约束。
14. **粗体过度使用**：小说正文不使用粗体。
15. **内联标题垂直列表**：小说正文不使用列表格式。
16. **标题中的标题大写**：中文不适用。
17. **表情符号**：小说正文不使用表情符号。
18. **弯引号**：中文中表现为英文引号的使用，小说正文使用中文引号。
19. **被动语态和无主语片段**：避免"被击中了"、"被打飞出去"、"被一股力量牵引"。小说正文典型表现："被击中了" → "剑砍中了他的肩膀"。

#### 交流模式（5 条）

20. **协作交流痕迹**：小说正文不应出现"希望这对您有帮助"、"当然！"等聊天机器人语言。
21. **知识截止日期免责声明**：小说正文不应出现"截至 [日期]"等免责声明。
22. **谄媚/卑躬屈膝的语气**：小说正文不应出现过于积极、讨好的语言。
23. **填充短语**：避免"为了实现这一目标"、"在这一过程中"、"在某种意义上"。小说正文典型表现："为了实现这一目标" → "为了实现这一点"。
24. **过度限定和通用积极结论**：避免"也许"、"或许"、"似乎"、"应该"、"可能"、"未来可期"、"光明就在前方"。小说正文典型表现：用确定性陈述替代过度限定。

### 13.3 stop-slop 补充规则

- **虚假代理（False Agency）**：无生命物体不能执行人类动作。"决定出现了"、"变化发生了"、"想法涌上来了" → "她做了决定"、"情况变了"、"她想到"。
- **远距离叙事（Narrator-from-a-distance）**：不要用"人们……"这种远距离叙事，要用具体角色视角。
- **懒散极端词**：避免"每个"、"总是"、"从不"做模糊工作。

### 13.4 中文小说特有 AI 痕迹（跨题材通用）

以下痕迹来自对多题材 AI 生成文本的观察，是现有 humanizer/stop-slop 都没有覆盖的中文小说特有模式。

1. **明喻过度（"像...一样"）**：每千字 ≤ 1 处。模式：`["像...一样", "仿佛...", "如同...", "宛如...", "犹如...", "恰似..."]`。
   - 跨题材示例：玄幻"像一匹受惊的马" / 都市"像被雨水泡过的松针" / 言情"像一根针扎在心上" / 历史"像一面墙挡在身前" / 科幻"像星辰坠入深海"
   - 修复策略：直接描写动作或感官，不用明喻。"像一匹受惊的马冲破了栅栏" → "灵气冲破了经脉"。

2. **通感公式（"X中带着一丝Y"）**：每章 ≤ 2 处。模式：`["X中带着Y", "X中透着Y", "X里掺着Y"]`。
   - 跨题材示例：玄幻"清苦中带着一丝甜" / 都市"苦涩中带着回甘" / 言情"疼痛中带着一丝甜蜜" / 历史"肃杀中带着悲凉" / 科幻"冰冷中带着一丝光亮"
   - 修复策略：用具体感官细节替代通感公式。"清苦中带着一丝甜" → "先是苦，然后舌根泛甜"。

3. **记忆回溯模板**：每章 ≤ 2 处。模式：`["前世记忆告诉她", "记忆里有一句话", "她记得", "脑海中浮现", "记忆深处", "前世的记忆涌上心头"]`。
   - 跨题材示例：玄幻"前世记忆告诉她——这株草有毒" / 都市"记忆里有一句话浮上来" / 言情"她记得他曾经说过" / 历史"记忆深处，父亲的声音响起" / 科幻"脑海中浮现出训练手册的内容"
   - 修复策略：用动作或对话转场，不用记忆模板。"前世记忆告诉她这株草有毒" → "她下意识缩回手。这草有毒。"

4. **感官描写堆砌**：单段内不要同时出现视觉+听觉+触觉（或嗅觉）描写，每段只用 1-2 种感官。
   - 跨题材示例：玄幻"树叶被风吹动的角度、溪水冲刷鹅卵石的声音、地面下三尺处虫类爬行的震动" / 都市"霓虹灯的闪烁、车流的轰鸣、柏油路面的热度"
   - 修复策略：根据场景需要选择最合适的 1-2 种感官。

5. **"以...为..."句式**：避免"以 X 为 Y"的古风化机械化模式。
   - 跨题材示例：玄幻"以神为火，以经脉为丹炉之壁" / 都市"以咖啡代早餐" / 言情"以沉默作回答" / 历史"以退为进" / 科幻"以光为盾"
   - 修复策略：直接陈述，不用"以...为..."句式。"以神为火" → "神识当火"。

6. **段落节奏均匀**：全篇段落长度 CV ≥ 0.35，句长 CV ≥ 0.40。
   - 跨题材示例：所有题材都存在此问题。
   - 修复策略：强制插入 1-3 字短句（"够了。""她动了。""不走。"），交替使用 1 句段和 5 句段。

7. **抽象情感标签堆砌**：避免"愤怒"、"悲伤"、"恐惧"、"震惊"、"绝望"、"百感交集"、"五味杂陈"。
   - 跨题材示例：玄幻"她感到一阵恐惧" / 都市"他心中涌起一股愤怒" / 言情"她百感交集" / 历史"他心中五味杂陈" / 科幻"她感到莫名的恐惧"
   - 修复策略：用动作、对白、感官细节外化情绪。"她感到一阵恐惧" → "她后退一步，手按上剑柄。"

8. **视觉动词泛化**：避免"渲染"、"勾勒"、"描摹"、"交织"、"流淌"、"弥漫"、"晕染"、"烙印"、"映照"、"笼罩"。
   - 跨题材示例：玄幻"灵气弥漫在空气中" / 都市"霓虹灯渲染着夜色" / 言情"月光勾勒出她的轮廓" / 历史"战火笼罩着城池" / 科幻"星光照耀着飞船"
   - 修复策略：用具体物件承载氛围。"灵气弥漫在空气中" → "空气里有股淡淡的甜味，吸进去时鼻腔发凉。"

### 13.5 质量评分维度（参考 humanizer 5 维评分）

Writer 生成时应追求以下 5 维高分（每维 10 分，总分 50 分，低于 35 分需修订）：

1. **直接性**：是否用具体动作、物件、感官替代抽象评价？
2. **节奏**：是否长短句交替？句长 CV 是否 ≥ 0.40？
3. **信任度**：是否信任读者从动作和物件中推断，而非解释每个动机？
4. **真实性**：是否有角色声纹、POV 偏见、身体代价？是否避免了"完美叙事"？
5. **精炼度**：是否删除了所有填充短语、过渡句、解释性从句？
- Microsoft Copilot 关于通过具体性、声纹和修订降低 AI 味的建议：https://www.microsoft.com/en-us/microsoft-copilot/copilot-101/humanize-ai-text
