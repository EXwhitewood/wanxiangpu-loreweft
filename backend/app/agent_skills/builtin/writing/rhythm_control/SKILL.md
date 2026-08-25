---
name: rhythm-control
description: >-
  小说节奏控制协议：管理句长变化、段落呼吸、快慢切换、关键瞬间减速、章节入口和出口动能、
  张力蓄放，防止正文变得均质、拖沓、赶场或情绪平线。
version: "2.2.1"
tags:
  - writing
  - rhythm
  - pacing
  - fiction
x-loreweft:
  id: rhythm_control
  display_name: "节奏控制"
  format_version: open_skill_v2
  source_format: x-loreweft
  kind: prompt
  domain: writing
  category: agent
  agents:
    - core_generation
    - editor_in_chief
    - dialogue_generation
    - description_generation
  priority: 70
  enabled_by_default: false

  compatibility:
    portable_layer: "Markdown 正文可作为通用 pacing/rhythm skill 使用。"
    loreweft_layer: "x-loreweft 声明 rhythm_metrics 与 narrative_experience 合同。"
    executable_runtime: false

  ownership:
    role: "micro_pacing_contract"
    not_roles:
      - outline_pacing_planner
      - scene_structure_planner
      - style_detector

  capability:
    summary: "把场景压力映射为句子形状、段落密度、转场顺滑度和出口动能。"
    mount_policy: on_demand_or_issue_triggered
    enforcement_policy: repair_when_rhythm_metrics_fail
    agent_method:
      - "根据 scene_role 和 pressure 选择节奏模式。"
      - "用短句、短段、动词和留白提速。"
      - "用长句、感官、停顿、物件和沉默降速。"
      - "关键瞬间可以减速，但慢段必须改变信息、关系、压力或情绪。"
      - "快慢切换必须有过渡信号。"

  domain_scope:
    applies_to:
      - 动作场景
      - 高潮
      - 情绪余波
      - 对白交锋
      - 章节入口和出口
    does_not_apply_to:
      - 大纲宏观节奏
      - 孤立错字润色
      - 非正文诊断

  triggers:
    scene_roles:
      - climax
      - turning_point
      - calm_before_storm
      - buildup
      - aftermath
      - action
      - dialogue
      - reflection
    issue_types:
      - rhythm_uniformity
      - pacing_too_slow
      - pacing_too_fast
      - rhythm_abrupt_shift
      - no_breathing_paragraph
      - chapter_no_hook
      - tension_flatline

  constraints:
    sentence_level:
      variance_min: 8
      uniform_sentence_length_forbidden: true
      short_sentence_max_words: 10
      long_sentence_min_words: 30
      fragment_allowed: true
      fragment_max_consecutive: 3
      retry_policy:
        max_retries: 1
        action: pacing_repair
    paragraph_level:
      breathing_ratio: 0.2
      dense_paragraph_max_consecutive: 5
      sparse_paragraph_min_per_chapter: 2
      white_space_as_pace_signal: true
      one_sentence_paragraph_allowed: true
      action_scene_thought_limit: minimal
      scene_break_required: true
      retry_policy:
        max_retries: 1
        action: pacing_repair
    scene_level:
      rhythm_curve_required: true
      abrupt_shift_forbidden: true
      transition_paragraph_required: true
      retry_policy:
        max_retries: 1
        action: pacing_repair
    chapter_level:
      intro_hook_words: 300
      chapter_body_words: 1500
      exit_hook_required: true
      exit_hook_position: last_200_words
      micro_payoff_required: true
      unresolved_question_required: true
      retry_policy:
        max_retries: 1
        action: pacing_repair
    arc_level:
      tension_curve_required: true
      flatline_forbidden: true
      retry_policy:
        max_retries: 1
        action: pacing_repair

  runtime:
    kind: prompt
    execution_phase: pre_generation
    timeout_seconds: 30
    output_char_budget: 4000
    required_context:
      - scene_contract
      - chapter_outline
    optional_context:
      - pacing_profile
      - previous_scene_rhythm
      - quality_memory
      - tension_curve
      - genre_profile

  validators:
    - rhythm_metrics
    - narrative_experience
  validation_contracts:
    rhythm_metrics:
      sentence_length_variance_min: 8
      uniform_sentence_streak_max: 3
      breathing_paragraph_ratio_min: 0.15
      dense_paragraph_streak_max: 5
      transition_paragraph_present: true
      abrupt_shift_count: 0
      retry_policy:
        max_retries: 1
        action: pacing_repair
    narrative_experience:
      chapter_hook_present: true
      tension_flatline: false
      reader_momentum_at_exit: required
      retry_policy:
        max_retries: 1
        action: pacing_repair

  repair_hooks:
    - pacing_repair
  repair_strategies:
    - vary_sentence_length
    - inject_breathing_paragraph
    - break_dense_streak
    - inject_transition
    - fix_abrupt_shift
    - add_chapter_hook
    - restructure_tension_curve
    - speed_up
    - slow_down

  failure_policy:
    validator_failed: "rhythm_metrics 失败时路由给 pacing_repair；修复后重跑 rhythm_metrics 和保护审计。"
    repair_failed: "返回 degraded trace；不得为节奏改动事实。"

  protection_policy:
    preserve:
      - facts
      - scene goal
      - outcome
      - markers
      - POV
    forbid:
      - "添加不改变任何信息的呼吸段。"
      - "为提速删掉必需事实。"

  trace_policy:
    required_fields:
      - scene_rhythm_mode
      - sentence_length_variance
      - breathing_ratio
      - dense_streak_max
      - transition_present
      - exit_momentum
---
# 节奏控制

## 1. 定位

`rhythm_control` 是微观节奏合同。它不规划大纲层面的起承转合，而是控制正文层面的阅读速度、呼吸、停顿、张力和出口动能。

节奏不是“越快越好”，也不是“越细越高级”。快要推进压力，慢要增加重量。任何慢段如果不改变信息、关系、压力、情绪或线索，就是拖沓。

## 2. 何时使用

触发场景：

- climax、turning_point、action、dialogue、aftermath、reflection、calm_before_storm。
- 质量系统报告节奏单一、过慢、过快、转场生硬、无呼吸段、章节无钩子、张力平线。
- 用户要求“更爽”“更紧”“更有呼吸感”“不要流水账”“节奏太平”。

默认非强制，但一旦触发，修复必须保留事实、目标、结局和 marker。

## 3. 职责边界

| 相邻 skill | 边界 |
|---|---|
| `pacing_structure` | 它管大纲和章节宏观节奏；本 skill 管句/段/场景层面的阅读速度。 |
| `scene_crafting` | 它管场景是否有目标和冲突；本 skill 管冲突如何被读者读到。 |
| `anti_ai_prose` | 它可检测句长均质导致的 AI 味；本 skill 负责节奏层面的句段调整。 |
| `pacing_repair` | 本 skill 声明失败指标；repair 系统执行具体修复。 |

## 4. 节奏模式

### 4.1 快节奏

适用：追逐、打斗、争吵、危机、偷袭、紧急逃离。

特征：

- 短句多，中长句少。
- 段落短，留白多。
- 动词优先，解释和形容词减少。
- 对白短促，冲突直接。
- 行动后必须有后果，不只制造热闹。

示例结构：

```text
动作 -> 阻碍 -> 更短反应 -> 后果落地
```

### 4.2 慢节奏

适用：余波、亲密、悬疑铺垫、情绪消化、重要线索、冷静前。

特征：

- 中长句增加，但不能堆废话。
- 感官和物件承载情绪。
- 沉默、停顿、重复动作可以拉长时间。
- 每 300 到 500 字至少给一个小答案、新疑问、关系变化或代价。

慢不等于停。慢段必须服务一个读者问题。

### 4.3 混合节奏

适用：多数正文场景。基本公式：

```text
压力推进 -> 呼吸消化 -> 新压力 -> 小兑现 -> 更大问题
```

连续 3 到 5 个密集段后，应有一个呼吸段。呼吸段不只是风景，它要承载：

- 情绪余波；
- 线索整理；
- 关系微调；
- 身体代价；
- 下一个危险的前兆。

### 4.4 慢镜头高潮

关键瞬间可以减速：

- 拆解动作；
- 调动感官；
- 拉长心理判断；
- 定格环境异常；
- 让一个物件或声音承担重量。

禁止把高潮写成空泛抒情。慢镜头必须围绕具体动作和代价。

## 5. 句级节奏

目标不是机械配比，而是打破均质：

| 场景模式 | 句子倾向 |
|---|---|
| 快节奏 | 1-15 字短句较多，可用少量碎片句。 |
| 慢节奏 | 20-35 字中长句较多，夹入短句做落点。 |
| 高压选择 | 前段蓄长，决策点切短。 |
| 情绪余波 | 长句铺开，短句收束。 |

禁止：

- 连续 3 个以上近似长度句。
- 全段都像同一个模板吐出来。
- 动作场景中过多解释心理。
- 情绪场景中一直短句导致重量不足。

## 6. 段落节奏

段落外观会影响阅读速度：

- 短段和独句段会提速。
- 长段会让读者停留。
- 留白是节奏信号。
- 对白密集会加速，但无信息对白会注水。

密集段落连续过多时，要插入呼吸段；但呼吸段必须有功能。

## 7. 提速方法

用于拖沓、解释过多、事件推进慢：

- 删除无效铺垫。
- 压缩重复心理。
- 缩短路程、寒暄、重复说明。
- 用动作替代解释。
- 让对话带冲突或新信息。
- 把背景信息拆进动作后果。

判断拖沓：

| 类型 | 信号 | 处理 |
|---|---|---|
| 无效铺垫 | 200 字无剧情推进 | 压缩到 50 字内或删除。 |
| 情绪内耗 | 同一情绪重复 2 次以上 | 用一次动作或选择替代。 |
| 对白注水 | 3 轮对白无新信息 | 删除或合并。 |
| 冲突后停步 | 冲突发生后 100 字内不推进 | 立即接反应、后果或新钩子。 |

## 8. 降速方法

用于节奏过快、情绪无重量、高潮草率：

- 放慢关键动作。
- 增加一到两个有功能的感官细节。
- 展示身体代价。
- 让角色迟疑或误判。
- 延迟答案，但给小兑现。
- 用物件变化承载情绪。

不要在无关处加长；只在关键选择、伤害、揭示、失去、亲密、背叛处降速。

## 9. 章节入口与出口

入口：

- 前 300 字内应出现事件、压力、悬念或异常。
- 避免大段背景、设定、日常流水。

出口：

- 最后 200 字内必须保留读者问题、危险、代价、决定、信息缺口或关系变化。
- 不用“故事才刚刚开始”式空钩子。
- 钩子要在后文兑现、反转或升级。

## 10. 验收合同

必须运行：

- `rhythm_metrics`
- `narrative_experience`

| 指标 | 通过标准 | 失败动作 |
|---|---|---|
| 句长方差 | `sentence_length_variance >= 8` | `vary_sentence_length` |
| 均质句长 | `uniform_sentence_streak <= 3` | `vary_sentence_length` |
| 呼吸段比例 | `breathing_ratio >= 0.15` | `inject_breathing_paragraph` |
| 密集段连续 | `dense_streak <= 5` | `break_dense_streak` |
| 转场段 | 需要切换时 `transition_present == true` | `inject_transition` |
| 骤变切换 | `abrupt_shift_count == 0` | `fix_abrupt_shift` |
| 章节钩子 | `chapter_hook_present == true` | `add_chapter_hook` |
| 张力平线 | `tension_flatline == false` | `restructure_tension_curve` |

## 11. 失败路由与保护

```text
rhythm_metrics failed -> pacing_repair
narrative_experience failed -> pacing_repair
repair completed -> rerun rhythm_metrics -> rerun protected audit
```

修复不得：

- 删除必需事实。
- 改变场景目标和结局。
- 为呼吸段添加无功能风景。
- 为提速牺牲关键情绪或伏笔。

## 12. Trace 要求

```json
{
  "skill_id": "rhythm_control",
  "status": "passed | repaired | failed | degraded",
  "scene_rhythm_mode": "fast | slow | mixed | slow_motion_climax",
  "sentence_length_variance": 12,
  "breathing_ratio": 0.2,
  "dense_streak_max": 4,
  "transition_present": true,
  "exit_momentum": "unresolved_question"
}
```

## 13. 参考来源

- Janice Hardy 关于 line-level pacing 与句段节奏影响：https://blog.janicehardy.com/2019/05/pacing-line-by-line.html
- Writer's Digest 关于张力与释放的 pacing 原则：https://www.writersdigest.com/pacing-for-emotional-impact-in-fiction-building-tension-and-release-in-a-novel
- SJSU Writing Center 关于句式变化和 rhythm：https://www.sjsu.edu/writingcenter/docs/handouts/Sentence%20Variety%20and%20Rhythm.pdf
- Authors AI 关于 pacing 与 reader momentum：https://authors.ai/importance-of-pacing-in-fiction-writing/
- Jami Gold 关于 prose rhythm 与读者体验：https://jamigold.com/2013/11/does-your-writing-have-rhythm/
