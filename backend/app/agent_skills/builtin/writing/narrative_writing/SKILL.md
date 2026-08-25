---
name: narrative-writing
description: >-
  小说叙事控制协议：管理 POV 边界、叙事距离、自由间接引语、中文时间锚点、转场信号和信息过滤。
  它确保读者始终知道“谁在看、离事件多近、何时发生、哪些信息可以被知道”。
version: "2.2.1"
tags:
  - writing
  - narrative
  - prose
  - pov
x-loreweft:
  id: narrative_writing
  display_name: "叙事写作"
  format_version: open_skill_v2
  source_format: x-loreweft
  kind: prompt
  domain: writing
  category: agent
  agents:
    - core_generation
    - dialogue_generation
    - description_generation
  priority: 82
  enabled_by_default: true

  compatibility:
    portable_layer: "通用叙事写作说明保留在 Markdown 正文中。"
    loreweft_layer: "POV、时间与叙事体验合同由 x-loreweft 执行。"
    executable_runtime: false

  ownership:
    role: "narrative_viewpoint_contract"
    not_roles:
      - scene_structure_planner
      - style_polisher
      - fact_repair_executor

  capability:
    summary: "保持生成正文中的视角、时间、叙事距离和信息可知边界稳定。"
    mount_policy: required_when_writer_generates_scene_text
    enforcement_policy: blocking_for_pov_or_temporal_boundary_failures
    agent_method:
      - "生成前锁定本场景 POV。"
      - "每句话都经过 POV 可见、可闻、可推断、可回忆、可误解的过滤。"
      - "叙事距离按功能移动：远距定位，中距行动，近距承压和决策。"
      - "任何时间、地点、POV、叙事距离变化都必须先给读者信号。"
      - "自由间接引语只能贴合当前 POV，不得变成作者评论。"

  domain_scope:
    applies_to:
      - 场景叙事
      - 内心活动
      - 转场段
      - 闪回
      - 章节连续性
    does_not_apply_to:
      - 大纲摘要
      - 诊断报告
      - 世界观笔记

  triggers:
    scene_roles:
      - setup
      - emotional_turn
      - reveal
      - resolution
      - buildup
      - aftermath
    issue_types:
      - pov_leakage
      - tense_drift
      - temporal_anchor_drift
      - unclear_transition
      - head_hopping
      - narrative_distance_flat
      - narrator_omniscient_breach
      - free_indirect_discourse_abuse

  constraints:
    pov_consistency:
      single_pov_per_scene: required
      head_hopping_count: 0
      knowledge_boundary_breach: 0
      narrator_commentary_max: 0
      retry_policy:
        max_retries: 1
        action: fix_pov_leak
    narrative_experience:
      distance_level_count_min: 2
      transition_signal_present: required
      overexplain_block_count: 0
      max_high_advisories: 0
      retry_policy:
        max_retries: 1
        action: adjust_narrative_distance
    tense_consistency:
      temporal_anchor_required: true
      tense_drift_count: 0
      flashback_tense_correct: required
      time_jump_without_signal_count: 0
      retry_policy:
        max_retries: 1
        action: correct_tense
    pov:
      default_type: third_person_limited
      pov_shift_requires_break: true
      omniscient_requires_explicit_contract: true
    narrative_distance:
      min_levels_per_scene: 2
      emotional_peak_distance: close
      transition_distance: far
      abrupt_jump_forbidden: true
    temporal_coherence:
      language_model: chinese_temporal_aspect
      base_temporal_anchor: main_scene_now
      flashback_requires_entry_and_return_signal: true
      time_jump_marker: required
    exposition:
      narrator_explanation_max_words: 100
      overexplain_forbidden: true

  runtime:
    kind: prompt
    execution_phase: pre_generation
    timeout_seconds: 30
    output_char_budget: 4000
    required_context:
      - scene_contract
      - pov_character_card
      - narrative_config
      - previous_scene_summary
      - style_profile
      - world_rules_relevant
    optional_context:
      - scene_map
      - chapter_outline
      - narrative_experience_contract
      - writing_mode_profile
      - quality_memory

  validators:
    - pov_consistency
    - narrative_experience
    - tense_consistency
  validation_contracts:
    pov_consistency:
      single_pov_per_scene: required
      head_hopping_count: 0
      knowledge_boundary_breach: 0
      narrator_commentary_max: 0
      retry_policy:
        max_retries: 1
        action: fix_pov_leak
    narrative_experience:
      distance_level_count_min: 2
      transition_signal_present: required
      overexplain_block_count: 0
      max_high_advisories: 0
      retry_policy:
        max_retries: 1
        action: adjust_narrative_distance
    tense_consistency:
      temporal_anchor_required: true
      tense_drift_count: 0
      flashback_tense_correct: required
      time_jump_without_signal_count: 0
      retry_policy:
        max_retries: 1
        action: correct_tense
  repair_hooks:
    - prose_repair
    - voice_repair
  repair_strategies:
    - fix_pov_leak
    - correct_tense
    - inject_transition_signal
    - trim_overexplain
    - adjust_narrative_distance

  failure_policy:
    validator_failed: "POV 或时间失败路由给 voice_repair/prose_repair intent；修复后重跑 POV、时间、marker 和保护审计。"
    repair_failed: "返回 failed trace；POV 泄漏或时间漂移文本不得提交。"

  protection_policy:
    preserve:
      - scene facts
      - information release
      - world rules
      - markers
    forbid:
      - "未分隔地进入非 POV 角色内心。"
      - "透露 POV 不可能知道的未来、秘密或动机。"

  trace_policy:
    required_fields:
      - pov_character
      - pov_type
      - temporal_anchor
      - distance_plan
      - transition_points
      - failed_metric
---
# 叙事写作

## 1. 定位

`narrative_writing` 是正文的视角和叙事流合同。它回答四个问题：

```text
谁在看？
读者离事件多近？
现在是什么时间位置？
哪些信息可以被这个视角知道？
```

如果这四个问题失控，文本会出现 head-hopping、全知旁白越界、闪回不回收、转场突兀、角色提前知道秘密等问题。它们不是单纯文风问题，而是事实合同和读者体验问题。

## 2. 何时使用

必须使用：

- 生成场景正文、内心活动、行动段、描写段、转场段。
- 场景含闪回、倒叙、时间跳转、地点跳转、POV 切换。
- 上一轮报告出现 POV 泄漏、head-hopping、时间线混乱、转场不清、旁白说教、叙事距离单一。

可以弱化：

- 大纲、设定说明、结构化诊断、非正文报告。

## 3. 职责边界

| 相邻 skill | 边界 |
|---|---|
| `scene_crafting` | 它管目标、冲突、价值变化；本 skill 管谁在体验这些事件。 |
| `show_dont_tell` | 它管情绪证据；本 skill 管这些证据能否被当前 POV 观察。 |
| `anti_ai_prose` | 它管模板腔；本 skill 管视角、时间和信息边界。 |
| `style_polish` | 它可润色语感，但不得改坏 POV、时间锚点和事实边界。 |

## 4. POV 锁定协议

生成前必须内部锁定：

```json
{
  "pov_character": "当前视角角色",
  "pov_type": "third_person_limited | first_person | omniscient",
  "knowledge_boundary": {
    "allowed": ["当前能看到/听到/推断/回忆的信息"],
    "forbidden": ["当前不能知道的秘密、未来、他人动机"]
  }
}
```

默认规则：

- 单个场景只允许一个主 POV。
- 未经场景分隔，不得进入另一个角色内心。
- 非 POV 角色的动机只能通过动作、语气、沉默、物件使用、他人反应呈现。
- 如果必须切换 POV，必须使用明确场景边界、空行、章节边界或合同许可。

## 5. 信息过滤协议

每句话都经过三问：

1. 当前 POV 能看到、听到、触到、闻到或感到吗？
2. 当前 POV 能合理推断吗，还是作者偷告诉读者？
3. 当前 POV 会用这个词描述它吗？

不确定时，写成观察或误判，不写成真相。

反例：

> 她不知道，门后的男人其实是她失散多年的兄长。

改法：

> 门后的人没有立刻答话。她只听见一声很轻的吸气，像有人把名字咽了回去。

## 6. 叙事距离协议

叙事距离不是越近越好。它按功能移动：

| 场景位置 | 推荐距离 | 功能 |
|---|---|---|
| 开场定位 | 远到中 | 建立时间、地点、行动目标。 |
| 冲突推进 | 中 | 保持动作清楚。 |
| 情绪峰值 | 近 | 进入感官、判断、自由间接引语。 |
| 信息揭示 | 中到近 | 让读者通过 POV 理解，而不是旁白解释。 |
| 收束/转场 | 中到远 | 拉出后果，准备下一场。 |

距离变化必须有过渡。不要从宏观概述突然跳到极近内心，也不要在角色崩溃时退回报告体旁白。

## 7. 自由间接引语规则

自由间接引语适用于第三人称有限视角。它让叙述者声音和角色意识短暂融合，减少“他想/她觉得/他意识到”的标签。

可用：

> 荒唐。那些人竟真敢把钥匙交给他。

不可用：

> 荒唐。历史总会惩罚这样的愚蠢。

后者像作者评论，除非叙事合同明确允许全知讽刺旁白。

限制：

- 只能贴近当前 POV 的意识。
- 不连续大段使用，避免节奏粘滞。
- 不能借角色意识提前泄露未授权事实。
- 动作密集段优先保持外部动作清楚。

## 8. 中文时间锚点协议

中文小说没有英语那种形态化 tense 系统，所以本 skill 检查的是时间锚点、体貌标记和时间线承接。

硬规则：

- 主线场景必须有可读的当前时间位置。
- 闪回必须有进入信号，如“那年”“三年前”“她想起”。
- 闪回必须有返回信号，如“声音把她拉回眼前”“此刻”“门外又响了一声”。
- 大时间跳跃必须有明确标记，如“三日后”“片刻后”“与此同时”。
- 同一段内不要让“现在”“刚才”“后来”“当年”互相冲突。

## 9. 转场信号协议

以下变化必须给信号：

| 变化 | 必要信号 |
|---|---|
| 时间变化 | 时间短语、动作承接、光线/声音变化。 |
| 地点变化 | 新地点物理细节、角色移动、门/路/交通动作。 |
| POV 变化 | 场景分隔、章节边界、显式标记。 |
| 闪回进入 | 触发物、时间词、感官钩子。 |
| 闪回返回 | 声音、疼痛、对白、眼前物。 |
| 叙事距离变化 | 从外部动作逐步过渡到感官和判断。 |

## 10. 禁止事项

- 未分隔进入另一个角色内心。
- 说出 POV 不知道的身份、动机、秘密或未来结果。
- 在有限视角中使用无身份全知旁白。
- 闪回进入后不返回主时间线。
- 时间、地点、POV、叙事距离无信号改变。
- 用解释句替代可观察戏剧动作。
- 把自由间接引语写成作者观点。

## 11. 验收合同

必须运行：

- `pov_consistency`
- `narrative_experience`
- `tense_consistency`

| validator | 核心通过标准 | 失败动作 |
|---|---|---|
| `pov_consistency` | 单场景单 POV、无 head-hopping、无认知越界、无旁白评论 | `fix_pov_leak` |
| `narrative_experience` | 至少两层叙事距离、有转场信号、无过度解释 | `adjust_narrative_distance` |
| `tense_consistency` | 有时间锚点、闪回闭合、无无信号跳时 | `correct_tense` |

## 12. 失败路由与保护

```text
pov_consistency failed -> voice_repair / fix_pov_leak
tense_consistency failed -> correct_tense
narrative_experience failed -> adjust_narrative_distance
repair completed -> rerun validators -> rerun marker/protection audit
```

修复不得为了解决 POV 问题而改动事实结果、删除伏笔、提前揭露秘密或改变场景目标。

## 13. Trace 要求

```json
{
  "skill_id": "narrative_writing",
  "status": "passed | repaired | failed | degraded",
  "pov_character": "角色名",
  "pov_type": "third_person_limited",
  "temporal_anchor": "主时间位置",
  "distance_plan": ["far", "medium", "close", "medium"],
  "transition_points": ["time", "location", "distance"],
  "failed_metric": null
}
```

## 14. 参考来源

- Emma Darwin 关于 psychic distance 的解释：https://emmadarwin.substack.com/p/psychic-distance-what-it-is-and-how
- Jane Friedman 关于 head-hopping 风险与 POV 控制：https://janefriedman.com/how-big-of-a-problem-is-head-hopping/
- September C. Fawkes 关于 head-hopping 对读者稳定感的影响：https://www.septembercfawkes.com/2024/08/what-is-head-hopping-and-why-is-it-bad.html
- Writing StackExchange 关于第三人称有限视角与自由间接引语的讨论：https://writing.stackexchange.com/questions/33219/what-is-the-difference-between-limited-third-person-narrative-and-free-indirect
